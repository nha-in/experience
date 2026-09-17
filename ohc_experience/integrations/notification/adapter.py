"""ABDM's notification service.

Template text comes from notification-db and is sent through notification-app.
Only a template's `{0}`, `{1}`… placeholders may vary: SMS carriers reject any
text that differs from the registered template.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any
from typing import NoReturn
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache

from ohc_experience.integrations.http import HttpPolicy
from ohc_experience.integrations.http import IntegrationClient
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import NotificationChannel

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from ohc_experience.integrations.ports import NotificationMessage

MESSAGE_PATH = "/internal/v3/notification/message"
TEMPLATES_PATH = "/internal/v3/notification/template/name/SANDBOX"
TEMPLATE_PATH = "/internal/v3/notification/template/id/{template_id}"

REQUEST_ID_HEADER = "REQUEST-ID"
TIMESTAMP_HEADER = "TIMESTAMP"

TEMPLATES_CACHE_KEY = "notification:templates"
TEMPLATES_CACHE_SECONDS = 60 * 60

RECEIVER_KEYS = {
    NotificationChannel.EMAIL: "emailId",
    NotificationChannel.SMS: "mobile",
}
SENT_STATUSES = frozenset({"SENT", "SUCCESS"})
PLACEHOLDER = re.compile(r"\{(\d+)\}")


class AbdmNotificationGateway:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._app = IntegrationClient(
            _policy(settings.NOTIFICATION_APP_BASE_URL),
            transport=transport,
        )
        self._db = IntegrationClient(
            _policy(settings.NOTIFICATION_DB_BASE_URL),
            transport=transport,
        )

    def send(self, message: NotificationMessage) -> None:
        content = _fill(self._template(message.template_id), message.values)
        response = self._app.request(
            "POST",
            MESSAGE_PATH,
            op="send_message",
            headers=_headers(),
            json={
                "origin": settings.NOTIFICATION_ORIGIN,
                "type": [message.channel.value],
                "contentType": message.content_type.value,
                "sender": settings.NOTIFICATION_SENDER,
                "receiver": [
                    {"key": RECEIVER_KEYS[message.channel], "value": message.receiver},
                ],
                "notification": [
                    {"key": "templateId", "value": message.template_id},
                    {"key": "subject", "value": message.subject},
                    {"key": "content", "value": content},
                ],
            },
        )
        payload = _json(response, "send_message")
        status = payload.get("status") if isinstance(payload, dict) else None
        if not isinstance(status, str) or status.upper() not in SENT_STATUSES:
            raise AdapterError(
                ExternalSystem.NOTIFICATION,
                "NOT_SENT",
                retryable=False,
                message=f"send_message returned status {status!r}",
            )

    def close(self) -> None:
        self._app.close()
        self._db.close()

    def _template(self, template_id: str) -> str:
        templates = cache.get(TEMPLATES_CACHE_KEY)
        if templates is None:
            templates = self._fetch(TEMPLATES_PATH, "list_templates")
            cache.set(TEMPLATES_CACHE_KEY, templates, TEMPLATES_CACHE_SECONDS)
        if template_id not in templates:
            path = TEMPLATE_PATH.format(template_id=quote(template_id, safe=""))
            templates |= self._fetch(path, "get_template")
            cache.set(TEMPLATES_CACHE_KEY, templates, TEMPLATES_CACHE_SECONDS)
        if template_id not in templates:
            raise AdapterError(
                ExternalSystem.NOTIFICATION,
                "UNKNOWN_TEMPLATE",
                retryable=False,
                message=f"no template {template_id}",
            )
        return templates[template_id]

    def _fetch(self, path: str, op: str) -> dict[str, str]:
        payload = _json(self._db.request("GET", path, op=op, headers=_headers()), op)
        records = payload if isinstance(payload, list) else [payload]
        templates = {}
        for record in records:
            if (
                not isinstance(record, dict)
                or record.get("id") is None
                or not isinstance(record.get("message"), str)
            ):
                _malformed(op, "expected templates with an id and a message")
            templates[str(record["id"])] = record["message"]
        return templates


def _policy(base_url: str) -> HttpPolicy:
    return HttpPolicy(
        system=ExternalSystem.NOTIFICATION,
        base_url=base_url.rstrip("/"),
        read_timeout=settings.NOTIFICATION_READ_TIMEOUT_SECONDS,
    )


def _headers() -> dict[str, str]:
    return {
        REQUEST_ID_HEADER: str(uuid.uuid4()),
        TIMESTAMP_HEADER: datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    }


def _fill(template: str, values: Sequence[str]) -> str:
    placeholders = {int(index) for index in PLACEHOLDER.findall(template)}
    if placeholders != set(range(len(values))):
        raise AdapterError(
            ExternalSystem.NOTIFICATION,
            "TEMPLATE_MISMATCH",
            retryable=False,
            message=(
                f"template placeholders {sorted(placeholders)} "
                f"do not take {len(values)} values"
            ),
        )
    return PLACEHOLDER.sub(lambda match: values[int(match.group(1))], template)


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
