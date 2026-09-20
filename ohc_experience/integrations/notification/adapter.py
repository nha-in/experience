"""ABDM's notification service.

The approved text lives in each template's body template, and is sent through
notification-app's message endpoint, SMS and email alike. Gateway email posts to
the email endpoint on the same host, through the Global Email backend.
"""

from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from typing import NoReturn

from django.conf import settings
from django.template.loader import render_to_string

from ohc_experience.integrations.http import HttpPolicy
from ohc_experience.integrations.http import IntegrationClient
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import NotificationChannel

if TYPE_CHECKING:
    import httpx

    from ohc_experience.integrations.ports import NotificationMessage

MESSAGE_PATH = "/internal/v3/notification/message"

REQUEST_ID_HEADER = "REQUEST-ID"
TIMESTAMP_HEADER = "TIMESTAMP"

# Fixed by the notification team's contract, the same for every deployment.
ORIGIN = "abha"
SENDER = "NHASMS"
READ_TIMEOUT_SECONDS = 5.0

SENT_STATUSES = frozenset({"SENT", "SUCCESS"})


class AbdmNotificationGateway:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._app = IntegrationClient(
            _policy(settings.NOTIFICATION_APP_BASE_URL),
            transport=transport,
        )

    def send(self, message: NotificationMessage) -> None:
        template = message.template
        # Only the file's own final newline: the registered text is sent exactly.
        content = render_to_string(
            template.body,
            dict(zip(template.values, message.values, strict=True)),
        ).removesuffix("\n")
        op = f"send_{template.channel.value}"
        request_id = str(uuid.uuid4())
        response = self._app.request(
            "POST",
            MESSAGE_PATH,
            op=op,
            headers=_headers(request_id, datetime.now(UTC)),
            json=_body(message, content, request_id=request_id),
        )
        payload = _json(response, op)
        status = payload.get("status") if isinstance(payload, dict) else None
        if not isinstance(status, str) or status.upper() not in SENT_STATUSES:
            raise AdapterError(
                ExternalSystem.NOTIFICATION,
                "NOT_SENT",
                retryable=False,
                message=f"{op} returned status {status!r}",
            )

    def close(self) -> None:
        self._app.close()


def _policy(base_url: str) -> HttpPolicy:
    return HttpPolicy(
        system=ExternalSystem.NOTIFICATION,
        base_url=base_url.rstrip("/"),
        read_timeout=READ_TIMEOUT_SECONDS,
    )


def _headers(request_id: str, at: datetime) -> dict[str, str]:
    return {
        REQUEST_ID_HEADER: request_id,
        # The service binds this header to a java.sql.Timestamp; ISO-8601 is rejected.
        TIMESTAMP_HEADER: at.strftime("%Y-%m-%d %H:%M:%S.%f"),
    }


def _body(
    message: NotificationMessage,
    content: str,
    *,
    request_id: str,
) -> dict[str, Any]:
    template = message.template
    if template.channel is NotificationChannel.EMAIL:
        receiver = {"key": "emailId", "value": message.receiver}
        notification = [
            {"key": "requestId", "value": request_id},
            {"key": "templateId", "value": template.id},
            {"key": "subject", "value": template.subject},
            {"key": "content", "value": content},
        ]
    else:
        receiver = {"key": "mobile", "value": message.receiver}
        notification = [
            {"key": "templateId", "value": template.id},
            {"key": "content", "value": content},
        ]
    return {
        "origin": ORIGIN,
        "type": [template.channel.value],
        "contentType": template.content_type.value,
        "sender": SENDER,
        "receiver": [receiver],
        "notification": notification,
    }


def _json(response: httpx.Response, op: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        _malformed(op, "response body was not JSON", cause=exc)


def _malformed(op: str, detail: str, *, cause: Exception | None = None) -> NoReturn:
    raise AdapterError(
        ExternalSystem.NOTIFICATION,
        "MALFORMED_RESPONSE",
        retryable=False,
        message=f"{op}: {detail}",
    ) from cause
