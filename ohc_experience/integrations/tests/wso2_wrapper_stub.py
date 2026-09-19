"""A stand-in for NHA's WSO2 wrapper: one endpoint, and a record of what it was sent.

It keeps no state — the wrapper exposes nothing to read back, which is exactly
why the adapter above it can neither look up nor remove anything.
"""

from __future__ import annotations

import json

import httpx

ADD_SUBSCRIPTIONS = "/add-subscriptions"

OK = 200
NOT_FOUND = 404


class Wso2WrapperStubTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.forced_status: int | None = None

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path != ADD_SUBSCRIPTIONS:
            return httpx.Response(
                NOT_FOUND,
                json={"error": f"unstubbed {request.method} {request.url.path}"},
            )

        self.bodies.append(json.loads(request.content))
        if self.forced_status is not None:
            return httpx.Response(self.forced_status, json={"error": "forced"})
        # Legacy's controller answers with a bare string, not JSON.
        return httpx.Response(OK, text="Success")
