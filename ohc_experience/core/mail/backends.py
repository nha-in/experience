"""Anymail adapter for ABDM's internal Global Email Notification API.

The gateway receives an already-rendered plaintext subject/body and an approved
template ID. It owns the sender identity and delivery; there is no template
lookup or SMTP transport here. One optional HTML alternative is deliberately
discarded when a nonempty plaintext body is available, as used by Django/allauth.
"""

# Error constructor arguments below are stable reason codes, not message text.
# ruff: noqa: EM101

from __future__ import annotations

import math
from datetime import UTC
from datetime import datetime
from http import HTTPStatus
from urllib.parse import urlsplit
from uuid import UUID
from uuid import uuid4

import requests
from anymail.backends.base_requests import AnymailRequestsBackend
from anymail.backends.base_requests import RequestsPayload
from anymail.exceptions import AnymailConfigurationError
from anymail.exceptions import AnymailInvalidAddress
from anymail.exceptions import AnymailRequestsAPIError
from anymail.message import AnymailRecipientStatus
from anymail.utils import get_anymail_setting
from django.conf import settings

MESSAGE_PATH = "/internal/v3/notification/message"
# The gateway answers SENT on the multi-channel endpoint and SUCCESS on the SES
# one, for the same accepted message.
ACCEPTED_STATUSES = frozenset({"SUCCESS", "SENT"})


def _as_message(data):
    """The multi-channel endpoint's shape, from the same validated fields.

    `/internal/v3/notification/email/send` is not deployed on the production
    gateway, which answers 404 for it. This endpoint carries email too: it is
    the one the verification codes already go out on. It has no CC field, so
    every copied address becomes another receiver.
    """
    addresses = [data["receiver"], *(data["ccRecipients"] or [])]
    return {
        "origin": data["origin"],
        "type": ["email"],
        "contentType": data["contentType"],
        "sender": data["sender"],
        "receiver": [{"key": "emailId", "value": address} for address in addresses],
        "notification": [
            {"key": "requestId", "value": data["requestId"]},
            {"key": "templateId", "value": data["templateId"]},
            {"key": "subject", "value": data["subject"]},
            {"key": "content", "value": data["content"]},
        ],
    }


class GlobalEmailAPIError(AnymailRequestsAPIError):
    """Safe operational failure information for an outbox or monitoring system.

    Never attach the requests response, request payload or original exception:
    Anymail normally includes them in its error text and they can contain OTPs.
    Only explicitly safe failures are retryable; request IDs are correlation
    identifiers, without a documented guarantee of gateway deduplication.
    """

    reason_codes = frozenset(
        {
            "connection_timeout",
            "timeout",
            "network_error",
            "http_error",
            "invalid_response",
            "gateway_rejected",
            "response_mismatch",
        },
    )

    def __init__(self, reason_code, *, status_code=None, retryable=False):
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(
            f"Global email gateway failed ({reason_code}).",
            status_code=status_code,
            esp_name="Global Email",
        )

    def describe_response(self):
        return None

    def describe_cause(self):
        return None


def _configured_string(value, setting):
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or any(ord(character) < 32 for character in value)  # noqa: PLR2004
    ):
        message = f"Configure a valid ANYMAIL {setting}."
        raise AnymailConfigurationError(message)
    return value


class GlobalEmailBackend(AnymailRequestsBackend):
    """Send one primary recipient and optional CCs through the Global service."""

    esp_name = "Global Email"

    def __init__(self, **kwargs):
        api_url = get_anymail_setting(
            "api_url",
            esp_name=self.esp_name,
            kwargs=kwargs,
            default="",
        )
        _configured_string(api_url, "GLOBAL_EMAIL_API_URL")
        try:
            parsed = urlsplit(api_url)
            valid_url = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
            # Accessing port also validates the configured port's syntax/range.
            parsed.port  # noqa: B018
        except ValueError:
            valid_url = False
        if not valid_url:
            message = "Configure an absolute HTTP(S) ANYMAIL GLOBAL_EMAIL_API_URL."
            raise AnymailConfigurationError(message)
        self.template_id = get_anymail_setting(
            "template_id",
            esp_name=self.esp_name,
            kwargs=kwargs,
            default=None,
        )
        self.origin = _configured_string(
            get_anymail_setting(
                "origin",
                esp_name=self.esp_name,
                kwargs=kwargs,
                default="abha",
            ),
            "GLOBAL_EMAIL_ORIGIN",
        )
        self.sender = _configured_string(
            get_anymail_setting(
                "sender",
                esp_name=self.esp_name,
                kwargs=kwargs,
                default="NHASMS",
            ),
            "GLOBAL_EMAIL_SENDER",
        )
        kwargs.setdefault(
            "timeout",
            get_anymail_setting(
                "requests_timeout",
                esp_name=self.esp_name,
                kwargs=kwargs,
                default=settings.EMAIL_TIMEOUT,
            ),
        )
        super().__init__(api_url=api_url, **kwargs)
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            message = (
                "Configure a positive finite ANYMAIL GLOBAL_EMAIL_REQUESTS_TIMEOUT."
            )
            raise AnymailConfigurationError(message)
        # A global Anymail debug flag must never dump authentication email bodies.
        self.debug_api_requests = False
        # The API cannot safely drop unsupported fields, even with a global opt-in.
        self.ignore_unsupported_features = False

    @property
    def uses_message_endpoint(self):
        """Read the shape off the configured URL, so the two cannot disagree."""
        return urlsplit(self.api_url).path.rstrip("/").endswith(MESSAGE_PATH)

    def build_message_payload(self, message, defaults):
        """Validate and serialize without opening a session or making a request."""
        try:
            payload = GlobalEmailPayload(message, defaults, self)
        except AnymailInvalidAddress:
            error = "Global email message contains an invalid email address."
            raise AnymailInvalidAddress(error) from None
        payload.validate()
        return payload

    def post_to_esp(self, payload, message):
        params = payload.get_request_params(self.api_url)
        params["timeout"] = self.timeout
        params["allow_redirects"] = False
        try:
            response = self.session.request(**params)
        except requests.ConnectTimeout:
            raise GlobalEmailAPIError("connection_timeout", retryable=True) from None
        except requests.Timeout:
            raise GlobalEmailAPIError("timeout") from None
        except requests.RequestException:
            raise GlobalEmailAPIError("network_error") from None
        # The document specifies 200, but the multi-channel endpoint answers 202
        # for the same accepted message. Any 2xx means the gateway took it; a
        # redirect does not, and is never followed.
        if not HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES:
            raise GlobalEmailAPIError(
                "http_error",
                status_code=response.status_code,
                retryable=response.status_code == HTTPStatus.TOO_MANY_REQUESTS,
            )
        return response

    def parse_recipient_status(self, response, payload, message):
        if not response.content.strip():
            # An accepted request the gateway had nothing to say about. There
            # is no status to check, and legacy read the SES path the same way:
            # it returns void, and any 2xx was the whole answer.
            result = {}
        else:
            try:
                result = response.json()
            except ValueError:
                raise GlobalEmailAPIError("invalid_response") from None
            if not isinstance(result, dict) or not isinstance(
                result.get("status"),
                str,
            ):
                raise GlobalEmailAPIError("invalid_response")
            if result["status"] not in ACCEPTED_STATUSES:
                raise GlobalEmailAPIError("gateway_rejected")
            self._validate_response_echoes(result, payload.data)
        message_id = result.get("gatewayTxnid")
        if message_id is None or message_id == "":
            message_id = payload.data["requestId"]
        if (
            not isinstance(message_id, str)
            or len(message_id) > 255  # noqa: PLR2004
            or message_id != message_id.strip()
        ):
            raise GlobalEmailAPIError("invalid_response")
        return {
            recipient: AnymailRecipientStatus(message_id=message_id, status="queued")
            for recipient in payload.all_recipients
        }

    @staticmethod
    def _validate_response_echoes(result, data):
        if "receiver" in result and result["receiver"] != data["receiver"]:
            raise GlobalEmailAPIError("response_mismatch")
        if "requestId" in result:
            try:
                response_request_id = str(UUID(result["requestId"]))
            except (TypeError, ValueError, AttributeError):
                raise GlobalEmailAPIError("response_mismatch") from None
            if response_request_id != data["requestId"]:
                raise GlobalEmailAPIError("response_mismatch")
        # The document uses a string templateId in requests, but a JSON number
        # in the example response. Accept either representation of the same ID.
        if "templateId" in result:
            template_id = result["templateId"]
            if (
                type(template_id) not in (str, int)
                or str(template_id) != data["templateId"]
            ):
                raise GlobalEmailAPIError("response_mismatch")
        if (
            result.get("type", "EMAIL") != "EMAIL"
            or result.get(
                "subType",
                data["contentType"].upper(),
            )
            != data["contentType"].upper()
        ):
            raise GlobalEmailAPIError("response_mismatch")


class GlobalEmailPayload(RequestsPayload):
    """The documented JSON body, with a matching correlation ID in its headers."""

    def __init__(self, message, defaults, backend):
        self.all_recipients = []
        super().__init__(message, defaults, backend, headers={})

    def init_payload(self):
        now = datetime.now(UTC)
        self.data = {
            "requestId": str(uuid4()),
            "timestamp": int(now.timestamp()) * 1000 + now.microsecond // 1000,
            "origin": self.backend.origin,
            "contentType": "info",
            "sender": self.backend.sender,
            "receiver": None,
            "templateId": self.backend.template_id,
            "subject": "",
            "content": "",
            "ccRecipients": None,
        }
        self.headers = {
            "Content-Type": "application/json",
            # The multi-channel endpoint binds this to a java.sql.Timestamp and
            # rejects an ISO-8601 value, as the verification codes' adapter has
            # always known. The SES path's own binding is unverified: it has
            # never answered anything but 404.
            "TIMESTAMP": (
                now.strftime("%Y-%m-%d %H:%M:%S.%f")
                if self.backend.uses_message_endpoint
                else now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            ),
        }

    def validate(self):
        _configured_string(self.data["templateId"], "GLOBAL_EMAIL_TEMPLATE_ID")
        if not self.data["receiver"]:
            self.unsupported_feature("messages without exactly one primary recipient")
        if (
            not isinstance(self.data["content"], str)
            or not self.data["content"].strip()
        ):
            self.unsupported_feature("messages without a nonempty plaintext body")
        if not isinstance(self.data["subject"], str):
            self.unsupported_feature("non-text subjects")
        self.headers["REQUEST-ID"] = self.data["requestId"]

    def serialize_data(self):
        self.validate()
        if self.backend.uses_message_endpoint:
            return self.serialize_json(_as_message(self.data))
        return self.serialize_json(self.data)

    def set_from_email(self, email):
        # This service owns the actual From address. Django's from_email is not
        # the gateway sender identifier and must never replace it.
        pass

    def set_recipients(self, recipient_type, emails):
        if recipient_type == "bcc":
            if emails:
                self.unsupported_feature("BCC recipients")
            return
        if recipient_type == "to":
            if len(emails) != 1:
                self.unsupported_feature(
                    "messages without exactly one primary recipient",
                )
            self.data["receiver"] = emails[0].addr_spec
        else:
            self.data["ccRecipients"] = [email.addr_spec for email in emails] or None
        self.all_recipients.extend(email.addr_spec for email in emails)

    def set_subject(self, subject):
        self.data["subject"] = subject

    def set_reply_to(self, emails):
        if emails:
            self.unsupported_feature("reply_to")

    def process_extra_headers(self, headers):
        if headers:
            self.unsupported_feature("custom email headers")

    def set_text_body(self, body):
        self.data["content"] = body

    def set_html_body(self, body):
        self.unsupported_feature("HTML-only bodies")

    def set_alternatives(self, alternatives):
        if not alternatives:
            return
        if (
            len(alternatives) != 1
            or alternatives[0][1] != "text/html"
            or self.message.content_subtype != "plain"
            or not self.data["content"]
        ):
            self.unsupported_feature(
                "MIME alternatives other than one HTML alternative",
            )
        # The API specifies no MIME field. Preserve the rendered plain text;
        # allowing the common HTML alternative keeps Django/allauth compatible.

    def add_alternative(self, content, mimetype):
        self.unsupported_feature("unsupported MIME body types")

    def prepped_attachments(self, attachments):
        if attachments:
            self.unsupported_feature("attachments")
        return []

    def set_template_id(self, template_id):
        self.data["templateId"] = template_id

    def set_esp_extra(self, extra):
        if not isinstance(extra, dict) or set(extra) - {"request_id", "content_type"}:
            self.unsupported_feature("unsupported esp_extra fields")
        if "request_id" in extra:
            try:
                request_id = str(UUID(str(extra["request_id"])))
            except (TypeError, ValueError, AttributeError):
                self.unsupported_feature("non-UUID request_id")
            self.data["requestId"] = request_id
        if "content_type" in extra:
            content_type = extra["content_type"]
            if content_type not in ("info", "otp"):
                self.unsupported_feature("content_type other than info or otp")
            self.data["contentType"] = content_type
