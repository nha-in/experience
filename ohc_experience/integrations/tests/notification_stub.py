"""A stand-in for notification-db's templates and notification-app's sends.

Template ids are JSON numbers here, as `Long` ids are on the real service.
"""

from __future__ import annotations

import json

import httpx

from ohc_experience.integrations.notification.templates import EMAIL_VERIFICATION_CODE
from ohc_experience.integrations.notification.templates import MOBILE_VERIFICATION_CODE

SMS_PATH = "/internal/v3/notification/message"
EMAIL_PATH = "/internal/v3/notification/email/send"
TEMPLATES_PATH = "/internal/v3/notification/template/name/SANDBOX"
TEMPLATE_PATH = "/internal/v3/notification/template/id/"

SMS_OTP_TEMPLATE_ID = MOBILE_VERIFICATION_CODE.id
EMAIL_OTP_TEMPLATE_ID = EMAIL_VERIFICATION_CODE.id
REACTIVATION_TEMPLATE_ID = "1007162235771710153"

OK = 200
NOT_FOUND = 404


def template(template_id: str, message: str) -> dict:
    return {
        "id": int(template_id),
        "name": "SANDBOX",
        "message": message,
        "header": "NHASMS",
        "type": "SMS",
        "subType": "OTP",
    }


class NotificationStubTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.sandbox_templates = [
            template(SMS_OTP_TEMPLATE_ID, "Your sandbox OTP is {0}."),
            template(EMAIL_OTP_TEMPLATE_ID, "Use {0} to verify your email."),
        ]
        self.other_templates: dict[str, dict] = {}
        self.send_status_code = OK
        self.send_body: object = {"status": "SENT"}

    def requests(self, method: str, path: str) -> list[httpx.Request]:
        return [
            call
            for call in self.calls
            if call.method == method and call.url.path.startswith(path)
        ]

    def texted(self) -> list[dict]:
        return self._bodies(SMS_PATH)

    def emailed(self) -> list[dict]:
        return self._bodies(EMAIL_PATH)

    def _bodies(self, path: str) -> list[dict]:
        return [json.loads(call.content) for call in self.requests("POST", path)]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        method, path = request.method, request.url.path
        if method == "GET" and path == TEMPLATES_PATH:
            return httpx.Response(OK, json=self.sandbox_templates)
        if method == "GET" and path.startswith(TEMPLATE_PATH):
            found = self.other_templates.get(path.removeprefix(TEMPLATE_PATH))
            if found is None:
                return httpx.Response(NOT_FOUND, json={"error": "no template"})
            return httpx.Response(OK, json=found)
        if method == "POST" and path in {SMS_PATH, EMAIL_PATH}:
            return httpx.Response(self.send_status_code, json=self.send_body)
        return httpx.Response(NOT_FOUND, json={"error": f"unstubbed {method} {path}"})
