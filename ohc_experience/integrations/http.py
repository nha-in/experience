"""One guarded doorway for every outbound call.

Every adapter goes through `IntegrationClient`, which enforces the policy in one
place: bounded timeouts, retries on idempotent operations only, a per-system
circuit breaker, and one structured log line per call that never contains a
secret.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Self

import httpx
import pybreaker
from tenacity import RetryError
from tenacity import Retrying
from tenacity import retry_if_exception
from tenacity import stop_after_attempt
from tenacity import wait_exponential_jitter

from ohc_experience.integrations.correlation import get_correlation_id
from ohc_experience.integrations.correlation import set_correlation_id
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ExternalSystem

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

__all__ = [
    "HttpPolicy",
    "IntegrationClient",
    "Token",
    "TokenCache",
    "breaker_for",
    "get_correlation_id",
    "reset_breakers",
    "set_correlation_id",
]

# PUT/DELETE are idempotent by HTTP semantics; POST is not, and is never retried
# unless an adapter can point at an idempotency guarantee.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})

#: Enough of an error body to name the fault: HIE-CM wraps a 405 in a 400, and
#: only its body says so.
ERROR_BODY_MAX_CHARS = 300


def _error_body(response: httpx.Response) -> str:
    return " ".join(response.text.split())[:ERROR_BODY_MAX_CHARS]


@dataclass(frozen=True, slots=True)
class HttpPolicy:
    """Per-system limits. Every field has a bound — nothing may be unlimited."""

    system: ExternalSystem
    base_url: str
    connect_timeout: float = 3.0
    read_timeout: float = 10.0
    max_attempts: int = 3
    backoff_initial: float = 0.2
    backoff_max: float = 2.0
    breaker_fail_max: int = 5
    breaker_reset_timeout: float = 30.0


def _is_client_error(exc: BaseException) -> bool:
    """A 4xx is our fault, not the dependency failing — keep it off the breaker."""
    return isinstance(exc, AdapterError) and not exc.retryable


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, AdapterError) and exc.retryable


_breakers: dict[ExternalSystem, pybreaker.CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def breaker_for(policy: HttpPolicy) -> pybreaker.CircuitBreaker:
    """One breaker per system, shared by every client pointed at it."""
    with _breakers_lock:
        breaker = _breakers.get(policy.system)
        if breaker is None:
            breaker = pybreaker.CircuitBreaker(
                fail_max=policy.breaker_fail_max,
                reset_timeout=policy.breaker_reset_timeout,
                exclude=[_is_client_error],
                name=str(policy.system),
            )
            _breakers[policy.system] = breaker
        return breaker


def reset_breakers() -> None:
    with _breakers_lock:
        _breakers.clear()


@dataclass(frozen=True, slots=True)
class Token:
    expires_at: float  # time.monotonic() deadline
    value: str = field(repr=False)


class TokenCache:
    """Holds a bearer token, refreshing `leeway` seconds before it expires.

    The early refresh is the point: a token that passes the expiry check and then
    expires in flight produces a 401 the retry policy is not allowed to fix.
    """

    def __init__(self, fetch: Callable[[], Token], *, leeway: float = 30.0) -> None:
        self._fetch = fetch
        self._leeway = leeway
        self._token: Token | None = None
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            token = self._token
            if token is None or token.expires_at - self._leeway <= time.monotonic():
                token = self._fetch()
                self._token = token
            return token.value

    def invalidate(self) -> None:
        with self._lock:
            self._token = None


@dataclass(frozen=True, slots=True)
class _Call:
    method: str
    url: str
    op: str
    headers: Mapping[str, str] | None
    kwargs: dict[str, Any]


class IntegrationClient:
    def __init__(
        self,
        policy: HttpPolicy,
        *,
        transport: httpx.BaseTransport | None = None,
        token_cache: TokenCache | None = None,
    ) -> None:
        self._policy = policy
        self._token_cache = token_cache
        self._breaker = breaker_for(policy)
        self._client = httpx.Client(
            base_url=policy.base_url,
            timeout=httpx.Timeout(
                connect=policy.connect_timeout,
                read=policy.read_timeout,
                write=policy.read_timeout,
                pool=policy.connect_timeout,
            ),
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        op: str,
        idempotent: bool | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send one logical request. Raises `AdapterError`, never httpx errors.

        `op` names the operation for logs. `idempotent` overrides the default
        derived from the HTTP method — an adapter must opt POST in explicitly.
        """
        retries_allowed = (
            method.upper() in IDEMPOTENT_METHODS if idempotent is None else idempotent
        )
        call = _Call(method=method, url=url, op=op, headers=headers, kwargs=kwargs)

        try:
            return self._breaker.call(
                self._with_retries,
                call,
                retries_allowed=retries_allowed,
            )
        except pybreaker.CircuitBreakerError as exc:
            self._log(
                call,
                started=time.monotonic(),
                status_code=None,
                outcome="circuit_open",
            )
            raise AdapterError(
                self._policy.system,
                "CIRCUIT_OPEN",
                retryable=True,
                message="circuit breaker is open; not attempting the call",
            ) from exc

    def _with_retries(self, call: _Call, *, retries_allowed: bool) -> httpx.Response:
        attempts = self._policy.max_attempts if retries_allowed else 1
        retrying = Retrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential_jitter(
                initial=self._policy.backoff_initial,
                max=self._policy.backoff_max,
            ),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )
        try:
            return retrying(self._send_once, call)
        except (
            RetryError
        ) as exc:  # pragma: no cover - reraise=True makes this unreachable
            raise AdapterError(
                self._policy.system,
                "RETRIES_EXHAUSTED",
                retryable=True,
            ) from exc

    def _send_once(self, call: _Call) -> httpx.Response:
        started = time.monotonic()
        request_headers = dict(call.headers or {}) | self._trace_headers()
        if self._token_cache is not None:
            request_headers["Authorization"] = f"Bearer {self._token_cache.get()}"

        try:
            response = self._client.request(
                call.method,
                call.url,
                headers=request_headers,
                **call.kwargs,
            )
        except httpx.TimeoutException as exc:
            self._log(call, started=started, status_code=None, outcome="timeout")
            raise AdapterError(
                self._policy.system,
                "TIMEOUT",
                retryable=True,
                message=f"{call.op} exceeded the configured timeout",
            ) from exc
        except httpx.TransportError as exc:
            self._log(
                call,
                started=started,
                status_code=None,
                outcome="transport_error",
            )
            raise AdapterError(
                self._policy.system,
                "TRANSPORT_ERROR",
                retryable=True,
                message=f"{call.op} could not reach the host",
            ) from exc

        outcome = "ok" if response.is_success else "error"
        self._log(
            call,
            started=started,
            status_code=response.status_code,
            outcome=outcome,
        )
        if response.is_success:
            return response

        raise AdapterError(
            self._policy.system,
            f"HTTP_{response.status_code}",
            retryable=response.status_code in RETRYABLE_STATUS,
            message=f"{call.op} returned {response.status_code}: "
            f"{_error_body(response)}",
        )

    def _trace_headers(self) -> dict[str, str]:
        correlation_id = get_correlation_id()
        span_id = uuid.uuid4().hex[:16]
        return {
            "X-Correlation-Id": correlation_id,
            "traceparent": f"00-{correlation_id}-{span_id}-01",
        }

    def _log(
        self,
        call: _Call,
        *,
        started: float,
        status_code: int | None,
        outcome: str,
    ) -> None:
        """One line per call. No headers and no body — both carry secrets."""
        logger.info(
            "integration call",
            extra={
                "system": str(self._policy.system),
                "op": call.op,
                "http_method": call.method.upper(),
                "path": call.url,
                "status_code": status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "outcome": outcome,
                "correlation_id": get_correlation_id(),
            },
        )
