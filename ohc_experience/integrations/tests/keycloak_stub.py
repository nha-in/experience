"""A stand-in Keycloak Admin API, recording every request it is sent.

Enough of the real API's shape to drive the adapter end to end, and — because
it records — enough to assert on what was *not* called. B9 replaces this with
wire-level WireMock fixtures; the recording is what the GET-vs-POST proof needs.
"""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator

REALM = "abdm-sandbox"
ADMIN = f"/admin/realms/{REALM}"
TOKEN_PATH = f"/realms/{REALM}/protocol/openid-connect/token"

CREATED_CLIENT_UUID = "11111111-2222-3333-4444-555555555555"
SERVICE_ACCOUNT_USER_ID = "99999999-8888-7777-6666-555555555555"

CREATED = 201
NO_CONTENT = 204
NOT_FOUND = 404

ROLE_IDS = {
    "healthId": "role-healthid",
    "hip": "role-hip",
    "hiu": "role-hiu",
    "hfr": "role-hfr",
}


class KeycloakStubTransport(httpx.BaseTransport):
    """Records calls; `failures` forces a status for a given (method, path)."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.failures: dict[tuple[str, str], int] = {}
        self.secret = "initial-secret"  # noqa: S105 - stub value, not a credential
        self.scope_mapped: list[str] = []
        self.granted_to_service_account: list[str] = []
        self.disabled = False
        self.token_calls = 0

    # Assertions the tests are built from

    def paths(self, method: str) -> list[str]:
        return [c.url.path for c in self.calls if c.method == method]

    def bodies(self, method: str, path_suffix: str) -> Iterator[object]:
        for call in self.calls:
            if call.method == method and call.url.path.endswith(path_suffix):
                yield json.loads(call.content)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        method = request.method

        forced = self.failures.get((method, path))
        if forced is not None:
            return httpx.Response(forced, json={"error": "forced"})

        for handler in (self._token, self._client_crud, self._roles, self._secrets):
            response = handler(method, path, request)
            if response is not None:
                return response
        return httpx.Response(NOT_FOUND, json={"error": f"unstubbed {method} {path}"})

    def _token(
        self,
        method: str,
        path: str,
        request: httpx.Request,
    ) -> httpx.Response | None:
        if path != TOKEN_PATH:
            return None
        self.token_calls += 1
        return httpx.Response(
            200,
            json={"access_token": f"token-{self.token_calls}", "expires_in": 300},
        )

    def _client_crud(
        self,
        method: str,
        path: str,
        request: httpx.Request,
    ) -> httpx.Response | None:
        if method == "POST" and path == f"{ADMIN}/clients":
            return httpx.Response(
                CREATED,
                headers={
                    "Location": f"http://kc.test{ADMIN}/clients/{CREATED_CLIENT_UUID}",
                },
            )
        if method == "PUT" and path.startswith(f"{ADMIN}/clients/"):
            self.disabled = True
            return httpx.Response(NO_CONTENT)
        if method == "GET" and path.endswith("/service-account-user"):
            return httpx.Response(200, json={"id": SERVICE_ACCOUNT_USER_ID})
        return None

    def _roles(
        self,
        method: str,
        path: str,
        request: httpx.Request,
    ) -> httpx.Response | None:
        if method == "GET" and path.startswith(f"{ADMIN}/roles/"):
            name = path.rpartition("/")[2]
            if name not in ROLE_IDS:
                return httpx.Response(NOT_FOUND, json={"error": "unknown role"})
            return httpx.Response(200, json={"id": ROLE_IDS[name], "name": name})
        if method == "POST" and path.endswith("/scope-mappings/realm"):
            self.scope_mapped += [r["name"] for r in json.loads(request.content)]
            return httpx.Response(NO_CONTENT)
        if method == "POST" and path.endswith("/role-mappings/realm"):
            self.granted_to_service_account += [
                r["name"] for r in json.loads(request.content)
            ]
            return httpx.Response(NO_CONTENT)
        return None

    def _secrets(
        self,
        method: str,
        path: str,
        request: httpx.Request,
    ) -> httpx.Response | None:
        if not path.endswith("/client-secret"):
            return None
        if method == "POST":  # rotates, exactly as the real server does
            self.secret = f"rotated-{secrets.token_hex(4)}"
        return httpx.Response(200, json={"type": "secret", "value": self.secret})
