"""WSO2 DevPortal: the gateway side of an integrator's credentials.

A Keycloak client alone calls nothing. It also needs a WSO2 application, keyed
to that client and subscribed to the sandbox APIs, before a token opens any
door. This adapter owns that half, including the removals that let a revoked
integrator actually lose gateway access.
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import NoReturn
from urllib.parse import quote

from django.conf import settings

from ohc_experience.integrations.http import HttpPolicy
from ohc_experience.integrations.http import IntegrationClient
from ohc_experience.integrations.http import Token
from ohc_experience.integrations.http import TokenCache
from ohc_experience.integrations.naming import APP_NAME_TEMPLATE
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import GatewayAppCreated
from ohc_experience.integrations.secret_ref import resolve_secret

if TYPE_CHECKING:
    import httpx

    from ohc_experience.integrations.ports import GatewayAppSpec

NOT_FOUND = "HTTP_404"
CONFLICT = "HTTP_409"


class Wso2ApiGateway:
    """Implements `ApiGateway` over the WSO2 DevPortal REST API."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._devportal = settings.WSO2_DEVPORTAL_PATH.rstrip("/")
        policy = HttpPolicy(
            system=ExternalSystem.WSO2,
            base_url=settings.WSO2_BASE_URL.rstrip("/"),
            # The admin APIs are slower than the others we call (06-integrations).
            read_timeout=settings.WSO2_READ_TIMEOUT_SECONDS,
        )
        self._auth = IntegrationClient(policy, transport=transport)
        self._client = IntegrationClient(
            policy,
            transport=transport,
            token_cache=TokenCache(self._fetch_token),
        )

    def create_application(self, spec: GatewayAppSpec) -> GatewayAppCreated:
        """Create-or-lookup: a re-run of the chain must not make twins."""
        name = APP_NAME_TEMPLATE.format(reference=spec.reference)

        existing = self._find_application(name)
        if existing is not None:
            return GatewayAppCreated(external_id=existing, name=name)

        try:
            response = self._client.request(
                "POST",
                f"{self._devportal}/applications",
                op="create_application",
                json={
                    "name": name,
                    "description": spec.name,
                    "throttlingPolicy": settings.WSO2_THROTTLING_POLICY,
                    "tokenType": settings.WSO2_TOKEN_TYPE,
                },
            )
        except AdapterError as error:
            # Lost a race with a concurrent chain run; the winner's app is ours too.
            if error.code != CONFLICT:
                raise
            found = self._find_application(name)
            if found is None:
                raise
            return GatewayAppCreated(external_id=found, name=name)

        payload = self._json(response, "create_application")
        return GatewayAppCreated(
            external_id=self._require(payload, "applicationId", "create_application"),
            name=name,
        )

    def subscribe(self, external_id: str, api_names: tuple[str, ...]) -> None:
        if not api_names:
            return

        already = self._subscriptions(external_id)
        wanted = [name for name in api_names if name not in already]
        if not wanted:
            return

        self._client.request(
            "POST",
            f"{self._devportal}/subscriptions/multiple",
            op="subscribe",
            json=[
                {
                    "apiId": self._api_id(name),
                    "applicationId": external_id,
                    "throttlingPolicy": settings.WSO2_THROTTLING_POLICY,
                }
                for name in wanted
            ],
        )

    def map_keys(self, external_id: str, consumer_key: str, secret_ref: str) -> None:
        """The one place a secret_ref is dereferenced — transiently, never stored."""
        self._client.request(
            "POST",
            f"{self._devportal}/applications/{_segment(external_id)}/map-keys",
            op="map_keys",
            json={
                "keyManager": settings.WSO2_KEY_MANAGER,
                "keyType": settings.WSO2_KEY_TYPE,
                "consumerKey": consumer_key,
                "consumerSecret": resolve_secret(secret_ref, ExternalSystem.WSO2),
            },
        )

    def unsubscribe(self, external_id: str, api_names: tuple[str, ...]) -> None:
        """Idempotent: what was never subscribed, or already gone, is success."""
        subscriptions = self._subscriptions(external_id)
        for name in api_names:
            subscription_id = subscriptions.get(name)
            if subscription_id is None:
                continue
            try:
                self._client.request(
                    "DELETE",
                    f"{self._devportal}/subscriptions/{_segment(subscription_id)}",
                    op="unsubscribe",
                )
            except AdapterError as error:
                if error.code != NOT_FOUND:
                    raise

    def _fetch_token(self) -> Token:
        credentials = base64.b64encode(
            f"{settings.WSO2_CLIENT_ID}:{settings.WSO2_CLIENT_SECRET}".encode(),
        ).decode()
        response = self._auth.request(
            "POST",
            settings.WSO2_TOKEN_PATH,
            op="fetch_token",
            # Retry-safe despite being a POST: it mints nothing durable.
            idempotent=True,
            headers={"Authorization": f"Basic {credentials}"},
            data={
                "grant_type": settings.WSO2_GRANT_TYPE,
                "username": settings.WSO2_USERNAME,
                "password": settings.WSO2_PASSWORD,
                "scope": " ".join(settings.WSO2_SCOPES),
            },
        )
        payload = self._json(response, "fetch_token")
        return Token(
            value=self._require(payload, "access_token", "fetch_token"),
            expires_at=time.monotonic() + float(payload.get("expires_in", 60)),
        )

    def _find_application(self, name: str) -> str | None:
        response = self._client.request(
            "GET",
            f"{self._devportal}/applications",
            op="find_application",
            params={"query": name},
        )
        payload = self._json(response, "find_application")
        for entry in payload.get("list") or []:
            if isinstance(entry, dict) and entry.get("name") == name:
                return self._require(entry, "applicationId", "find_application")
        return None

    def _api_id(self, name: str) -> str:
        """Resolved by name at call time, so no instance UUID sits in config."""
        response = self._client.request(
            "GET",
            f"{self._devportal}/apis",
            op="find_api",
            params={"query": f"name:{name}"},
        )
        payload = self._json(response, "find_api")
        for entry in payload.get("list") or []:
            if isinstance(entry, dict) and entry.get("name") == name:
                return self._require(entry, "id", "find_api")
        raise AdapterError(
            ExternalSystem.WSO2,
            NOT_FOUND,
            retryable=False,
            message=f"no API published under the name {name!r}",
        )

    def _subscriptions(self, external_id: str) -> dict[str, str]:
        """API name → subscription id for one application."""
        response = self._client.request(
            "GET",
            f"{self._devportal}/subscriptions",
            op="list_subscriptions",
            params={"applicationId": external_id},
        )
        payload = self._json(response, "list_subscriptions")
        found: dict[str, str] = {}
        for entry in payload.get("list") or []:
            if not isinstance(entry, dict):
                continue
            info = entry.get("apiInfo") or {}
            name = info.get("name") if isinstance(info, dict) else None
            subscription_id = entry.get("subscriptionId")
            if isinstance(name, str) and isinstance(subscription_id, str):
                found[name] = subscription_id
        return found

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
            ExternalSystem.WSO2,
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
