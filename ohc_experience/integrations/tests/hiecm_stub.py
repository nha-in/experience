"""A stand-in HIE-CM gateway that keeps bridge state.

Registration is a PUT on the real gateway, so the stub upserts too \u2014 that is the
property `create_bridge` re-run safety rests on, and a stub that appended would
quietly make the test meaningless.
"""

from __future__ import annotations

import json

import httpx

API = "/api/v3"
SESSION_PATH = f"{API}/sessions"
BRIDGE_PATH = f"{API}/gateway/bridge"
BRIDGE_SERVICES = f"{API}/gateway/v3/bridge-services"

OK = 200
NO_CONTENT = 204
NOT_FOUND = 404


class HiecmStubTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.failures: dict[tuple[str, str], int] = {}
        self.bridges: dict[str, dict] = {}
        self.token_calls = 0

    def paths(self, method: str) -> list[str]:
        return [c.url.path for c in self.calls if c.method == method]

    def publish_bridge(self, bridge_id: str, **overrides) -> None:
        self.bridges[bridge_id] = {
            "id": bridge_id,
            "name": "existing",
            "url": "https://existing.test",
            "active": True,
            "blocklisted": False,
        } | overrides

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        method, path = request.method, request.url.path

        forced = self.failures.get((method, path))
        if forced is not None:
            return httpx.Response(forced, json={"error": "forced"})

        for handler in (self._session, self._bridge, self._status):
            response = handler(method, path, request)
            if response is not None:
                return response
        return httpx.Response(NOT_FOUND, json={"error": f"unstubbed {method} {path}"})

    def _session(self, method, path, request):
        if path != SESSION_PATH or method != "POST":
            return None
        self.token_calls += 1
        return httpx.Response(
            OK,
            json={"accessToken": f"hiecm-token-{self.token_calls}", "expiresIn": 300},
        )

    def _bridge(self, method, path, request):
        if path != BRIDGE_PATH:
            return None

        body = json.loads(request.content)
        if method == "PUT":
            # Upsert, exactly as a PUT should.
            self.bridges[body["bridgeId"]] = {
                "id": body["bridgeId"],
                "name": body["name"],
                "url": body["url"],
                "active": body["active"],
                "blocklisted": body["blocklisted"],
            }
            return httpx.Response(OK, json={})

        if method == "PATCH":
            record = self.bridges.get(body["bridgeId"])
            if record is None:
                return httpx.Response(NOT_FOUND, json={"error": "no such bridge"})
            record["active"] = body["active"]
            return httpx.Response(NO_CONTENT)

        return None

    def _status(self, method, path, request):
        if not path.startswith(f"{BRIDGE_SERVICES}/") or method != "GET":
            return None
        record = self.bridges.get(path.rpartition("/")[2])
        if record is None:
            return httpx.Response(NOT_FOUND, json={"error": "no such bridge"})
        return httpx.Response(OK, json={"bridge": record, "services": []})
