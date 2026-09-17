"""A stand-in WSO2 DevPortal that keeps state, so re-runs can be asserted on.

Unlike the Keycloak stub this one has to *remember* applications and
subscriptions: create-or-lookup and idempotent unsubscribe are only meaningful
against a server that already holds something. B9 replaces it with WireMock.
"""

from __future__ import annotations

import json
import uuid

import httpx

DEVPORTAL = "/api/am/devportal/v3"
TOKEN_PATH = "/oauth2/token"  # noqa: S105 - a URL path, not a token

CREATED = 201
OK = 200
NOT_FOUND = 404
CONFLICT = 409

#: Published APIs, by name. Ids are opaque, as they are on a real gateway.
API_IDS = {
    "abha-v3": "api-0001-abha",
    "hip-v3": "api-0002-hip",
    "hiu-v3": "api-0003-hiu",
}


class Wso2StubTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.failures: dict[tuple[str, str], int] = {}
        self.raw_bodies: dict[tuple[str, str], str] = {}
        self.applications: dict[str, str] = {}  # name -> applicationId
        self.subscriptions: dict[str, dict[str, str]] = {}  # appId -> api -> subId
        self.mapped_keys: dict[str, dict[str, str]] = {}
        self.token_calls = 0
        self.created_applications = 0

    def paths(self, method: str) -> list[str]:
        return [c.url.path for c in self.calls if c.method == method]

    def publish_application(self, name: str) -> str:
        application_id = f"app-{uuid.uuid4().hex[:8]}"
        self.applications[name] = application_id
        self.subscriptions.setdefault(application_id, {})
        return application_id

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        method, path = request.method, request.url.path

        forced = self.failures.get((method, path))
        if forced is not None:
            return httpx.Response(forced, json={"error": "forced"})

        raw = self.raw_bodies.get((method, path))
        if raw is not None:
            return httpx.Response(OK, text=raw)

        for handler in (self._token, self._applications, self._apis, self._subs):
            response = handler(method, path, request)
            if response is not None:
                return response
        return httpx.Response(NOT_FOUND, json={"error": f"unstubbed {method} {path}"})

    def _token(self, method, path, request):
        if path != TOKEN_PATH:
            return None
        self.token_calls += 1
        return httpx.Response(
            OK,
            json={"access_token": f"wso2-token-{self.token_calls}", "expires_in": 300},
        )

    def _applications(self, method, path, request):
        if path == f"{DEVPORTAL}/applications" and method == "GET":
            query = request.url.params.get("query", "")
            found = [
                {"applicationId": app_id, "name": name}
                for name, app_id in self.applications.items()
                if query in name
            ]
            return httpx.Response(OK, json={"list": found})

        if path == f"{DEVPORTAL}/applications" and method == "POST":
            name = json.loads(request.content)["name"]
            if name in self.applications:
                return httpx.Response(CONFLICT, json={"error": "name already exists"})
            self.created_applications += 1
            return httpx.Response(
                CREATED,
                json={"applicationId": self.publish_application(name), "name": name},
            )

        if path.endswith("/map-keys") and method == "POST":
            application_id = path.split("/")[-2]
            self.mapped_keys[application_id] = json.loads(request.content)
            return httpx.Response(OK, json={"keyMappingId": "km-1"})

        return None

    def _apis(self, method, path, request):
        if path != f"{DEVPORTAL}/apis" or method != "GET":
            return None
        wanted = request.url.params.get("query", "").removeprefix("name:")
        found = [
            {"id": api_id, "name": name}
            for name, api_id in API_IDS.items()
            if name == wanted
        ]
        return httpx.Response(OK, json={"list": found})

    def _subs(self, method, path, request):
        if path == f"{DEVPORTAL}/subscriptions" and method == "GET":
            application_id = request.url.params.get("applicationId", "")
            held = self.subscriptions.get(application_id, {})
            return httpx.Response(
                OK,
                json={
                    "list": [
                        {"subscriptionId": sub_id, "apiInfo": {"name": name}}
                        for name, sub_id in held.items()
                    ],
                },
            )

        if path == f"{DEVPORTAL}/subscriptions/multiple" and method == "POST":
            for entry in json.loads(request.content):
                name = next(
                    key for key, value in API_IDS.items() if value == entry["apiId"]
                )
                held = self.subscriptions.setdefault(entry["applicationId"], {})
                held[name] = f"sub-{uuid.uuid4().hex[:8]}"
            return httpx.Response(OK, json={"list": []})

        if path.startswith(f"{DEVPORTAL}/subscriptions/") and method == "DELETE":
            subscription_id = path.rpartition("/")[2]
            for held in self.subscriptions.values():
                for name, existing in list(held.items()):
                    if existing == subscription_id:
                        del held[name]
                        return httpx.Response(OK)
            return httpx.Response(NOT_FOUND, json={"error": "no such subscription"})

        return None
