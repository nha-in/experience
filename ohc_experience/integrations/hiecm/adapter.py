"""HIE-CM bridge registry — the last thing provisioning creates.

A bridge is ABDM's record that *this* integrator's system may exchange health
data, at *these* endpoints. Without it a valid token and a live subscription
still move nothing.

The bridge id is whatever it is given rather than derived, and the callback is
the integrator's own endpoint rather than a shared one.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from typing import NoReturn
from urllib.parse import quote

from django.conf import settings

from ohc_experience.integrations.http import HttpPolicy
from ohc_experience.integrations.http import IntegrationClient
from ohc_experience.integrations.http import Token
from ohc_experience.integrations.http import TokenCache
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import BridgeCreated
from ohc_experience.integrations.ports import BridgeStatus
from ohc_experience.integrations.ports import ExternalSystem

if TYPE_CHECKING:
    import httpx

    from ohc_experience.integrations.ports import BridgeSpec

NOT_FOUND = "HTTP_404"

#: ABDM gateway convention — every HIE-CM call carries all three.
REQUEST_ID_HEADER = "REQUEST-ID"
TIMESTAMP_HEADER = "TIMESTAMP"
CM_ID_HEADER = "X-CM-ID"

#: Milliseconds, UTC, trailing Z — what the gateway accepts.
_TIMESTAMP_PRECISION = "milliseconds"


class HiecmBridgeRegistry:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._api = settings.HIECM_API_PATH.rstrip("/")
        policy = HttpPolicy(
            system=ExternalSystem.HIECM,
            base_url=settings.HIECM_BASE_URL.rstrip("/"),
        )
        self._auth = IntegrationClient(policy, transport=transport)
        self._client = IntegrationClient(
            policy,
            transport=transport,
            token_cache=TokenCache(self._fetch_token),
        )

    def create_bridge(self, spec: BridgeSpec) -> BridgeCreated:
        """Registration is a PUT, so a re-run overwrites rather than duplicates."""
        self._client.request(
            "PUT",
            f"{self._api}/gateway/bridge",
            op="create_bridge",
            headers=self._gateway_headers(),
            json={
                "bridgeId": spec.bridge_id,
                "name": spec.name,
                "url": spec.url,
                "active": True,
                "blocklisted": False,
            },
        )
        return BridgeCreated(bridge_id=spec.bridge_id)

    def get_bridge_status(self, bridge_id: str) -> BridgeStatus:
        response = self._client.request(
            "GET",
            f"{self._api}/gateway/v3/bridge-services/{_segment(bridge_id)}",
            op="get_bridge_status",
            headers=self._gateway_headers(),
        )
        payload = self._json(response, "get_bridge_status")
        bridge = payload.get("bridge")
        if not isinstance(bridge, dict):
            self._malformed("get_bridge_status", "response had no bridge object")

        # Blocklisted folds into `active`: a blocklisted bridge moves no data, and
        # reporting it as active would tell the integrator they are ready.
        active = bool(bridge.get("active")) and not bool(bridge.get("blocklisted"))
        return BridgeStatus(bridge_id=bridge_id, active=active)

    def deactivate_bridge(self, bridge_id: str) -> None:
        """Idempotent: an already-inactive or absent bridge is success."""
        try:
            self._client.request(
                "PATCH",
                f"{self._api}/gateway/bridge",
                op="deactivate_bridge",
                headers=self._gateway_headers(),
                json={"id": bridge_id, "bridgeId": bridge_id, "active": False},
            )
        except AdapterError as error:
            if error.code != NOT_FOUND:
                raise

    def _fetch_token(self) -> Token:
        response = self._auth.request(
            "POST",
            f"{self._api}{settings.HIECM_SESSION_PATH}",
            op="fetch_token",
            # Retry-safe despite being a POST: a session mints nothing durable.
            idempotent=True,
            headers=self._gateway_headers(),
            json={
                "clientId": settings.HIECM_CLIENT_ID,
                "clientSecret": settings.HIECM_CLIENT_SECRET,
            },
        )
        payload = self._json(response, "fetch_token")
        return Token(
            value=self._require(payload, "accessToken", "fetch_token"),
            expires_at=time.monotonic() + float(payload.get("expiresIn", 60)),
        )

    @staticmethod
    def _gateway_headers() -> dict[str, str]:
        return {
            REQUEST_ID_HEADER: str(uuid.uuid4()),
            TIMESTAMP_HEADER: datetime.now(UTC)
            .isoformat(timespec=_TIMESTAMP_PRECISION)
            .replace("+00:00", "Z"),
            CM_ID_HEADER: settings.HIECM_CM_ID,
        }

    def _json(self, response: httpx.Response, op: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            self._malformed(op, "response body was not JSON", cause=exc)
        if not isinstance(payload, dict):
            self._malformed(op, "expected a JSON object")
        return payload

    def _require(self, payload: dict[str, Any], key: str, op: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            self._malformed(op, f"response had no usable {key!r}")
        return value

    def _malformed(
        self,
        op: str,
        detail: str,
        *,
        cause: Exception | None = None,
    ) -> NoReturn:
        raise AdapterError(
            ExternalSystem.HIECM,
            "MALFORMED_RESPONSE",
            retryable=False,
            message=f"{op}: {detail}",
        ) from cause

    def close(self) -> None:
        self._client.close()
        self._auth.close()


def _segment(value: str) -> str:
    """Nothing interpolated into a path may introduce another one."""
    return quote(value, safe="")
