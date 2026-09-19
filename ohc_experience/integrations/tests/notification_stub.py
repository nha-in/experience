"""A stand-in for notification-app's sends."""

from __future__ import annotations

import json

import httpx

from ohc_experience.integrations.notification.templates import EMAIL_VERIFICATION_CODE
from ohc_experience.integrations.notification.templates import MOBILE_VERIFICATION_CODE

MESSAGE_PATH = "/internal/v3/notification/message"

SMS_OTP_TEMPLATE_ID = MOBILE_VERIFICATION_CODE.id
EMAIL_OTP_TEMPLATE_ID = EMAIL_VERIFICATION_CODE.id

OK = 200
NOT_FOUND = 404


class NotificationStubTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.send_status_code = OK
        self.send_body: object = {"status": "SENT"}

    def requests(self, method: str, path: str) -> list[httpx.Request]:
        return [
            call
            for call in self.calls
            if call.method == method and call.url.path.startswith(path)
        ]

    def sent(self) -> list[dict]:
        return [
            json.loads(call.content)
            for call in self.requests("POST", MESSAGE_PATH)
        ]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        method, path = request.method, request.url.path
        if method == "POST" and path == MESSAGE_PATH:
            return httpx.Response(self.send_status_code, json=self.send_body)
        return httpx.Response(NOT_FOUND, json={"error": f"unstubbed {method} {path}"})
