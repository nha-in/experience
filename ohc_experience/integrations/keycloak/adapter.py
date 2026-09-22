"""Keycloak Admin API: the client id and secret an integrator ships with.

Two Keycloak 26 behaviours are load-bearing here.

`GET /client-secret` reads and `POST /client-secret` rotates. Binding a read to
POST silently invalidates a live integrator's credential, so `_read_secret` and
`rotate_client_secret` are kept apart.

A realm role reaches a `client_credentials` token only if it is *both*
scope-mapped to the client and granted to the client's service-account user.
Scope-mapping alone filters what may appear in a token without putting anything
in it.

It signs in as the legacy portal did, as a master-realm admin with a password
grant: those are the only Keycloak credentials NHA issued.
"""

from __future__ import annotations

import secrets
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
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ClientCreated
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import SecretRotated

if TYPE_CHECKING:
    import httpx

    from ohc_experience.integrations.ports import ClientSpec

#: Random, never derived from a sequence: this id is publicly visible and doubles
#: as the bridge id, so a guessable one lets an integrator name another's.
CLIENT_ID_PREFIX = "SBX_"
CLIENT_ID_ENTROPY_BYTES = 8

NOT_FOUND = "HTTP_404"

ADMIN_TOKEN_REALM = "master"  # noqa: S105 - a realm name, not a password


class KeycloakIdpAdmin:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._realm = settings.KEYCLOAK_REALM
        policy = HttpPolicy(
            system=ExternalSystem.KEYCLOAK,
            base_url=settings.KEYCLOAK_BASE_URL.rstrip("/"),
        )
        # The token call cannot carry a bearer — it is the call that obtains one.
        self._auth = IntegrationClient(policy, transport=transport)
        self._client = IntegrationClient(
            policy,
            transport=transport,
            token_cache=TokenCache(self.fetch_admin_token),
        )

    def create_client(self, spec: ClientSpec) -> ClientCreated:
        client_id = new_client_id()
        response = self._client.request(
            "POST",
            f"{self._admin}/clients",
            op="create_client",
            json={
                "clientId": client_id,
                "name": spec.display_name,
                "description": spec.reference,
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                # With this true the token would carry every realm role.
                "fullScopeAllowed": False,
            },
        )
        external_id = self._created_uuid(response)
        self._grant_roles(external_id, spec.role_names)
        return ClientCreated(
            client_id=client_id,
            external_id=external_id,
            initial_secret=self._read_secret(external_id),
        )

    def rotate_client_secret(self, external_id: str) -> SecretRotated:
        """The only code path allowed to POST to `/client-secret`."""
        response = self._client.request(
            "POST",
            f"{self._admin}/clients/{_segment(external_id)}/client-secret",
            op="rotate_client_secret",
        )
        payload = self._json(response, "rotate_client_secret")
        return SecretRotated(
            external_id=external_id,
            secret=self._require(payload, "value", "rotate_client_secret"),
        )

    def disable_client(self, external_id: str) -> None:
        """Idempotent: a disabled or already-absent client is success."""
        try:
            self._client.request(
                "PUT",
                f"{self._admin}/clients/{_segment(external_id)}",
                op="disable_client",
                json={"enabled": False},
            )
        except AdapterError as error:
            if error.code != NOT_FOUND:
                raise

    def enable_client(self, external_id: str) -> None:
        """Undo `disable_client`. A missing client raises: nothing to bring back."""
        self._client.request(
            "PUT",
            f"{self._admin}/clients/{_segment(external_id)}",
            op="enable_client",
            json={"enabled": True},
        )

    @property
    def _admin(self) -> str:
        return f"/admin/realms/{_segment(self._realm)}"

    def fetch_admin_token(self) -> Token:
        """Legacy's admin sign-in, which HIE-CM also takes for bridge registration."""
        api_key = settings.KEYCLOAK_API_KEY
        response = self._auth.request(
            "POST",
            f"/realms/{ADMIN_TOKEN_REALM}/protocol/openid-connect/token",
            op="fetch_token",
            # Retry-safe despite being a POST: it mints nothing durable.
            idempotent=True,
            # Legacy sent the API key with this call and no other.
            headers={"apikey": api_key} if api_key else None,
            data={
                "grant_type": "password",
                "scope": "openid",
                "client_id": settings.KEYCLOAK_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
                "username": settings.KEYCLOAK_USERNAME,
                "password": settings.KEYCLOAK_PASSWORD,
            },
        )
        payload = self._json(response, "fetch_token")
        return Token(
            value=self._require(payload, "access_token", "fetch_token"),
            expires_at=time.monotonic() + float(payload.get("expires_in", 60)),
        )

    def _read_secret(self, external_id: str) -> str:
        """GET, never POST — see the module docstring."""
        response = self._client.request(
            "GET",
            f"{self._admin}/clients/{_segment(external_id)}/client-secret",
            op="read_client_secret",
        )
        payload = self._json(response, "read_client_secret")
        return self._require(payload, "value", "read_client_secret")

    def _grant_roles(self, external_id: str, role_names: tuple[str, ...]) -> None:
        if not role_names:
            return

        roles = [self._role_by_name(name) for name in role_names]
        client_path = f"{self._admin}/clients/{_segment(external_id)}"

        # Permits the roles in the client's tokens...
        self._client.request(
            "POST",
            f"{client_path}/scope-mappings/realm",
            op="scope_map_roles",
            json=roles,
        )
        # ...and this is what actually puts them there.
        response = self._client.request(
            "GET",
            f"{client_path}/service-account-user",
            op="read_service_account",
        )
        service_account = self._json(response, "read_service_account")
        user_id = self._require(service_account, "id", "read_service_account")
        self._client.request(
            "POST",
            f"{self._admin}/users/{_segment(user_id)}/role-mappings/realm",
            op="grant_service_account_roles",
            json=roles,
        )

    def _role_by_name(self, name: str) -> dict[str, str]:
        """Resolved at call time, so no realm UUID is ever stored in config."""
        response = self._client.request(
            "GET",
            f"{self._admin}/roles/{_segment(name)}",
            op="read_role",
        )
        payload = self._json(response, "read_role")
        return {
            "id": self._require(payload, "id", "read_role"),
            "name": self._require(payload, "name", "read_role"),
        }

    def _created_uuid(self, response: httpx.Response) -> str:
        location = response.headers.get("Location", "")
        uuid_ = location.rstrip("/").rpartition("/")[2]
        if not uuid_:
            self._malformed("create_client", "no client id in the Location header")
        return uuid_

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
        """Shape errors become `AdapterError` too — never KeyError from an adapter."""
        raise AdapterError(
            ExternalSystem.KEYCLOAK,
            "MALFORMED_RESPONSE",
            retryable=False,
            message=f"{op}: {detail}",
        ) from cause

    def close(self) -> None:
        self._client.close()
        self._auth.close()


def new_client_id() -> str:
    return f"{CLIENT_ID_PREFIX}{secrets.token_hex(CLIENT_ID_ENTROPY_BYTES).upper()}"


def _segment(value: str) -> str:
    """Nothing interpolated into a path may introduce another one."""
    return quote(value, safe="")
