"""Every behaviour the shared HTTP policy promises (B1 acceptance criteria)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import pytest

from ohc_experience.integrations.http import IDEMPOTENT_METHODS
from ohc_experience.integrations.http import HttpPolicy
from ohc_experience.integrations.http import IntegrationClient
from ohc_experience.integrations.http import Token
from ohc_experience.integrations.http import TokenCache
from ohc_experience.integrations.http import breaker_for
from ohc_experience.integrations.http import get_correlation_id
from ohc_experience.integrations.http import reset_breakers
from ohc_experience.integrations.http import set_correlation_id
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ExternalSystem

MAX_ATTEMPTS = 3
BREAKER_FAIL_MAX = 5
CORRELATION_ID_LENGTH = 32
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 10.0
OK = 200
NOT_FOUND = 404
SERVER_ERROR = 500
UNAVAILABLE = 503
NOISE_CALLS = 10


@pytest.fixture(autouse=True)
def _clean_breakers():
    reset_breakers()
    yield
    reset_breakers()


def policy(**overrides: Any) -> HttpPolicy:
    defaults: dict[str, Any] = {
        "system": ExternalSystem.KEYCLOAK,
        "base_url": "https://keycloak.test",
        "backoff_initial": 0.0,
        "backoff_max": 0.0,
    }
    return HttpPolicy(**(defaults | overrides))


class CountingTransport(httpx.BaseTransport):
    """Records every attempt so tests can assert on retry counts."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self.handler(request)


def always(status: int):
    return lambda _request: httpx.Response(status, json={})


def client(handler, **policy_overrides) -> tuple[IntegrationClient, CountingTransport]:
    transport = CountingTransport(handler)
    return IntegrationClient(policy(**policy_overrides), transport=transport), transport


# Timeouts


def test_timeout_becomes_a_retryable_adapter_error():
    def handler(request):
        msg = "read timed out"
        raise httpx.ReadTimeout(msg, request=request)

    api, transport = client(handler)

    with pytest.raises(AdapterError) as excinfo:
        api.request("GET", "/clients", op="list_clients")

    assert excinfo.value.code == "TIMEOUT"
    assert excinfo.value.retryable is True
    assert excinfo.value.system is ExternalSystem.KEYCLOAK
    assert len(transport.calls) == MAX_ATTEMPTS  # retried, because GET is idempotent


def test_timeout_values_are_bounded_on_the_underlying_client():
    api, _ = client(
        always(OK),
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=READ_TIMEOUT,
    )

    timeout = api._client.timeout  # noqa: SLF001 - the bound is the thing under test

    assert timeout.connect == CONNECT_TIMEOUT
    assert timeout.read == READ_TIMEOUT


# Retries


def test_idempotent_operation_retries_up_to_max_attempts():
    api, transport = client(always(UNAVAILABLE))

    with pytest.raises(AdapterError):
        api.request("GET", "/clients", op="list_clients")

    assert len(transport.calls) == MAX_ATTEMPTS


def test_non_idempotent_operation_is_never_retried():
    api, transport = client(always(UNAVAILABLE))

    with pytest.raises(AdapterError):
        api.request("POST", "/clients", op="create_client")

    assert len(transport.calls) == 1


def test_post_can_opt_into_retries_explicitly():
    api, transport = client(always(UNAVAILABLE))

    with pytest.raises(AdapterError):
        api.request("POST", "/clients", op="create_client", idempotent=True)

    assert len(transport.calls) == MAX_ATTEMPTS


def test_client_errors_are_not_retried():
    api, transport = client(always(NOT_FOUND))

    with pytest.raises(AdapterError) as excinfo:
        api.request("GET", "/clients/missing", op="get_client")

    assert excinfo.value.code == "HTTP_404"
    assert excinfo.value.retryable is False
    assert len(transport.calls) == 1


def test_retry_succeeds_when_a_later_attempt_works():
    responses = [httpx.Response(SERVER_ERROR), httpx.Response(OK, json={"ok": True})]
    api, transport = client(lambda _request: responses.pop(0))

    response = api.request("GET", "/clients", op="list_clients")

    assert response.status_code == OK
    assert len(transport.calls) == 2  # noqa: PLR2004 - failed once, then succeeded


def test_put_and_delete_count_as_idempotent():
    assert {"PUT", "DELETE", "GET", "HEAD", "OPTIONS"} == set(IDEMPOTENT_METHODS)
    assert "POST" not in IDEMPOTENT_METHODS
    assert "PATCH" not in IDEMPOTENT_METHODS


# Circuit breaker


def test_breaker_opens_after_five_consecutive_failures():
    api, transport = client(always(UNAVAILABLE))

    for _ in range(BREAKER_FAIL_MAX):
        with pytest.raises(AdapterError):
            api.request("POST", "/clients", op="create_client")

    assert len(transport.calls) == BREAKER_FAIL_MAX

    with pytest.raises(AdapterError) as excinfo:
        api.request("POST", "/clients", op="create_client")

    assert excinfo.value.code == "CIRCUIT_OPEN"
    assert excinfo.value.retryable is True
    assert (
        len(transport.calls) == BREAKER_FAIL_MAX
    )  # the open circuit did not reach the transport


def test_breaker_half_opens_after_the_reset_timeout():
    outcomes = [httpx.Response(UNAVAILABLE)] * BREAKER_FAIL_MAX + [
        httpx.Response(OK, json={}),
    ]
    api, _ = client(lambda _request: outcomes.pop(0), breaker_reset_timeout=0.05)

    for _ in range(BREAKER_FAIL_MAX):
        with pytest.raises(AdapterError):
            api.request("POST", "/clients", op="create_client")

    time.sleep(0.06)

    assert api.request("POST", "/clients", op="create_client").status_code == OK


def test_client_errors_do_not_trip_the_breaker():
    api, transport = client(always(NOT_FOUND))

    for _ in range(NOISE_CALLS):
        with pytest.raises(AdapterError) as excinfo:
            api.request("GET", "/clients/missing", op="get_client")
        assert excinfo.value.code == "HTTP_404"

    assert len(transport.calls) == NOISE_CALLS  # never short-circuited


def test_breaker_is_shared_per_system():
    first = policy(base_url="https://a.test")
    second = policy(base_url="https://b.test")

    assert breaker_for(first) is breaker_for(second)
    assert breaker_for(policy(system=ExternalSystem.WSO2)) is not breaker_for(first)


# Auth and tracing


def test_bearer_token_is_attached_and_cached():
    fetches = []

    def fetch() -> Token:
        fetches.append(1)
        return Token(expires_at=time.monotonic() + 3600, value="token-abc")

    transport = CountingTransport(always(OK))
    api = IntegrationClient(
        policy(),
        transport=transport,
        token_cache=TokenCache(fetch),
    )

    api.request("GET", "/a", op="a")
    api.request("GET", "/b", op="b")

    assert len(fetches) == 1
    assert transport.calls[0].headers["Authorization"] == "Bearer token-abc"


def test_token_is_refreshed_early_rather_than_at_expiry():
    tokens = [
        Token(expires_at=time.monotonic() + 10, value="about-to-expire"),
        Token(expires_at=time.monotonic() + 3600, value="fresh"),
    ]
    cache = TokenCache(lambda: tokens.pop(0), leeway=30.0)

    first = cache.get()
    second = cache.get()

    assert first == "about-to-expire"  # fetched, then immediately considered stale
    assert second == "fresh"


def test_token_value_is_kept_out_of_repr():
    assert "s3cret" not in repr(Token(expires_at=0.0, value="s3cret"))


def test_invalidating_the_cache_forces_a_refetch():
    """An adapter that sees a 401 needs a way to discard a token the server rejected."""
    issued = iter(["first", "second"])

    def fetch() -> Token:
        return Token(expires_at=time.monotonic() + 3600, value=next(issued))

    cache = TokenCache(fetch)

    assert cache.get() == "first"
    cache.invalidate()
    assert cache.get() == "second"


def test_every_call_carries_correlation_and_traceparent_headers():
    set_correlation_id("0af7651916cd43dd8448eb211c80319c")
    transport = CountingTransport(always(OK))
    api = IntegrationClient(policy(), transport=transport)

    api.request("GET", "/clients", op="list_clients")

    sent = transport.calls[0].headers
    assert sent["X-Correlation-Id"] == "0af7651916cd43dd8448eb211c80319c"
    assert sent["traceparent"].startswith("00-0af7651916cd43dd8448eb211c80319c-")
    assert sent["traceparent"].endswith("-01")


def test_correlation_id_is_generated_when_absent():
    set_correlation_id("")

    assert len(get_correlation_id()) == CORRELATION_ID_LENGTH


def test_one_structured_log_line_per_call_without_secrets(caplog):
    transport = CountingTransport(always(OK))
    api = IntegrationClient(
        policy(),
        transport=transport,
        token_cache=TokenCache(
            lambda: Token(expires_at=time.monotonic() + 3600, value="s3cret"),
        ),
    )

    with caplog.at_level(logging.INFO, logger="ohc_experience.integrations.http"):
        api.request(
            "GET",
            "/clients",
            op="list_clients",
            headers={"X-Api-Key": "leak-me"},
        )

    logger_name = "ohc_experience.integrations.http"
    records = [r for r in caplog.records if r.name == logger_name]
    assert len(records) == 1
    record = records[0]
    assert record.system == "KEYCLOAK"
    assert record.op == "list_clients"
    assert record.outcome == "ok"
    assert record.status_code == OK
    assert record.duration_ms >= 0
    assert "s3cret" not in caplog.text
    assert "leak-me" not in caplog.text


# Error mapping


def test_transport_errors_are_mapped_not_propagated():
    def handler(request):
        msg = "name resolution failed"
        raise httpx.ConnectError(msg, request=request)

    api, _ = client(handler)

    with pytest.raises(AdapterError) as excinfo:
        api.request("GET", "/clients", op="list_clients")

    assert excinfo.value.code == "TRANSPORT_ERROR"
    assert excinfo.value.retryable is True


def test_adapter_error_carries_system_code_and_retryable():
    error = AdapterError(
        ExternalSystem.WSO2,
        "HTTP_500",
        retryable=True,
        message="boom",
    )

    assert error.system is ExternalSystem.WSO2
    assert error.code == "HTTP_500"
    assert error.retryable is True
    assert str(error) == "[WSO2] HTTP_500: boom"


def test_adapter_error_renders_without_a_message():
    assert (
        str(AdapterError(ExternalSystem.HIECM, "CIRCUIT_OPEN", retryable=True))
        == "[HIECM] CIRCUIT_OPEN"
    )


def test_client_closes_via_context_manager():
    with IntegrationClient(policy(), transport=CountingTransport(always(OK))) as api:
        assert api.request("GET", "/clients", op="list_clients").status_code == OK

    assert api._client.is_closed  # noqa: SLF001 - closing is the behaviour under test
