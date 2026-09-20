"""Contract tests that exercise requests serialization without network access."""

# ruff: noqa: PLR2004, SLF001

from __future__ import annotations

import json
import re
from datetime import datetime
from unittest.mock import Mock
from uuid import UUID

import pytest
import requests
from anymail.exceptions import AnymailConfigurationError
from anymail.exceptions import AnymailError
from anymail.exceptions import AnymailInvalidAddress
from anymail.exceptions import AnymailUnsupportedFeature
from anymail.message import AnymailMessage
from django.core.mail import EmailMessage
from django.core.mail import EmailMultiAlternatives

from ohc_experience.core.mail.backends import GlobalEmailAPIError
from ohc_experience.core.mail.backends import GlobalEmailBackend

API_URL = "http://global-notification.internal/internal/v3/notification/email/send"
RECEIVER = "applicant@example.test"
REQUEST_ID = "83dc87e5-b782-4f09-861a-ad371c1dc35a"


@pytest.fixture(autouse=True)
def gateway_settings(settings):
    settings.ANYMAIL = {
        "GLOBAL_EMAIL_API_URL": API_URL,
        "GLOBAL_EMAIL_TEMPLATE_ID": "approved-template-123",
        "GLOBAL_EMAIL_REQUESTS_TIMEOUT": 7.5,
    }


@pytest.fixture
def gateway(monkeypatch):
    """Replace the HTTP transport, retaining requests' real PreparedRequest."""
    state = {
        "status": 200,
        "body": None,
        "raw": None,
        "headers": {},
    }

    def respond(request, **kwargs):
        payload = json.loads(request.body)
        body = state["body"]
        if body is None:
            body = {
                "status": "SUCCESS",
                "requestId": payload["requestId"],
                "receiver": payload["receiver"],
                "templateId": payload["templateId"],
                "gatewayTxnid": "provider-id-123",
                "type": "EMAIL",
                "subType": payload["contentType"].upper(),
                "remark": "Request accepted",
                "retryCount": 0,
            }
        response = requests.Response()
        response.status_code = state["status"]
        response.request = request
        response.headers.update(state["headers"])
        response._content = (
            state["raw"] if state["raw"] is not None else json.dumps(body).encode()
        )
        return response

    transport = Mock(side_effect=respond)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", transport)
    return state, transport


@pytest.fixture
def message():
    return EmailMessage(
        subject="Your application update",
        body="Your application has been approved. നന്ദി",
        from_email="Portal <portal@example.test>",
        to=[f"Applicant <{RECEIVER}>"],
    )


def test_sends_documented_contract_and_reports_queued(message, gateway):
    _, transport = gateway
    backend = GlobalEmailBackend()

    assert backend.send_messages([message]) == 1

    transport.assert_called_once()
    request = transport.call_args.args[0]
    assert request.method == "POST"
    assert request.url == API_URL
    assert request.headers["Content-Type"] == "application/json"
    assert "Authorization" not in request.headers
    assert transport.call_args.kwargs["timeout"] == 7.5
    payload = json.loads(request.body)
    assert payload == {
        "requestId": request.headers["REQUEST-ID"],
        "timestamp": payload["timestamp"],
        "origin": "abha",
        "contentType": "info",
        "sender": "NHASMS",
        "receiver": RECEIVER,
        "templateId": "approved-template-123",
        "subject": message.subject,
        "content": message.body,
        "ccRecipients": None,
    }
    assert UUID(payload["requestId"]).version == 4
    timestamp = request.headers["TIMESTAMP"]
    assert timestamp.endswith("Z")
    assert len(timestamp.partition(".")[2]) == 4
    assert type(payload["timestamp"]) is int
    header_time = datetime.fromisoformat(timestamp)
    assert payload["timestamp"] == (
        int(header_time.timestamp()) * 1000 + header_time.microsecond // 1000
    )
    assert message.anymail_status.status == {"queued"}
    assert message.anymail_status.message_id == "provider-id-123"
    assert message.anymail_status.recipients[RECEIVER].status == "queued"
    assert backend.session is None


def test_cc_and_per_message_overrides(message, gateway, settings):
    _, transport = gateway
    settings.ANYMAIL["GLOBAL_EMAIL_ORIGIN"] = "sandbox"
    settings.ANYMAIL["GLOBAL_EMAIL_SENDER"] = "APPROVED_SENDER"
    message.template_id = "different-approved-template"
    message.esp_extra = {"request_id": REQUEST_ID, "content_type": "otp"}
    message.cc = ["First <first@example.test>", "second@example.test"]
    assert GlobalEmailBackend().send_messages([message]) == 1
    request = transport.call_args.args[0]
    payload = json.loads(request.body)
    assert payload["ccRecipients"] == ["first@example.test", "second@example.test"]
    assert payload["templateId"] == message.template_id
    assert payload["requestId"] == request.headers["REQUEST-ID"] == REQUEST_ID
    assert payload["contentType"] == "otp"
    assert payload["origin"] == "sandbox"
    assert payload["sender"] == "APPROVED_SENDER"
    assert set(message.anymail_status.recipients) == {
        RECEIVER,
        "first@example.test",
        "second@example.test",
    }
    assert message.anymail_status.status == {"queued"}


def test_no_gateway_message_id_falls_back_to_request_uuid(message, gateway):
    state, _ = gateway
    state["body"] = {"status": "SUCCESS"}
    message.esp_extra = {"request_id": REQUEST_ID}
    assert GlobalEmailBackend().send_messages([message]) == 1
    assert message.anymail_status.message_id == REQUEST_ID


def test_documented_numeric_template_id_in_success_response(message, gateway):
    state, _ = gateway
    message.template_id = "100001"
    message.esp_extra = {"request_id": REQUEST_ID}
    state["body"] = {
        "status": "SUCCESS",
        "requestId": REQUEST_ID,
        "receiver": RECEIVER,
        "templateId": 100001,
        "gatewayTxnid": "provider-id-123",
        "type": "EMAIL",
        "subType": "INFO",
        "remark": "Request accepted",
        "retryCount": 0,
    }
    assert GlobalEmailBackend().send_messages([message]) == 1
    assert message.anymail_status.status == {"queued"}


def test_equivalent_response_request_uuid_is_accepted(message, gateway):
    state, _ = gateway
    message.esp_extra = {"request_id": REQUEST_ID}
    state["body"] = {"status": "SUCCESS", "requestId": REQUEST_ID.upper()}
    assert GlobalEmailBackend().send_messages([message]) == 1


def test_validation_can_run_without_network(message, gateway):
    _, transport = gateway
    backend = GlobalEmailBackend()
    payload = backend.build_message_payload(message, backend.send_defaults)
    assert payload.data["receiver"] == RECEIVER
    assert payload.data["templateId"] == "approved-template-123"
    assert payload.headers["REQUEST-ID"] == payload.data["requestId"]
    assert backend.session is None
    transport.assert_not_called()


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("GLOBAL_EMAIL_API_URL", ""),
        ("GLOBAL_EMAIL_API_URL", None),
        ("GLOBAL_EMAIL_API_URL", "/relative/path"),
        ("GLOBAL_EMAIL_API_URL", "ftp://example.test/send"),
        ("GLOBAL_EMAIL_API_URL", "https://user:secret@example.test/send"),
        ("GLOBAL_EMAIL_API_URL", "https://example.test/send?secret=hidden"),
        ("GLOBAL_EMAIL_API_URL", "https://example.test/send#fragment"),
        ("GLOBAL_EMAIL_API_URL", "https://example.test:invalid/send"),
        ("GLOBAL_EMAIL_API_URL", " https://example.test/send"),
        ("GLOBAL_EMAIL_ORIGIN", ""),
        ("GLOBAL_EMAIL_SENDER", ""),
        ("GLOBAL_EMAIL_REQUESTS_TIMEOUT", None),
        ("GLOBAL_EMAIL_REQUESTS_TIMEOUT", 0),
        ("GLOBAL_EMAIL_REQUESTS_TIMEOUT", -1),
        ("GLOBAL_EMAIL_REQUESTS_TIMEOUT", float("inf")),
        ("GLOBAL_EMAIL_REQUESTS_TIMEOUT", float("nan")),
        ("GLOBAL_EMAIL_REQUESTS_TIMEOUT", True),
    ],
)
def test_configuration_fails_before_network(settings, gateway, setting, value):
    _, transport = gateway
    settings.ANYMAIL[setting] = value
    with pytest.raises(AnymailConfigurationError):
        GlobalEmailBackend()
    transport.assert_not_called()


@pytest.mark.parametrize("template_id", [None, "", " ", " bad-id ", 100001, True])
def test_requires_explicit_valid_template_id(message, gateway, settings, template_id):
    _, transport = gateway
    settings.ANYMAIL["GLOBAL_EMAIL_TEMPLATE_ID"] = template_id
    with pytest.raises(AnymailConfigurationError, match="GLOBAL_EMAIL_TEMPLATE_ID"):
        GlobalEmailBackend().send_messages([message])
    transport.assert_not_called()


def test_per_message_template_works_without_global_default(message, gateway, settings):
    settings.ANYMAIL.pop("GLOBAL_EMAIL_TEMPLATE_ID")
    message.template_id = "approved-auth-template"
    assert GlobalEmailBackend().send_messages([message]) == 1


@pytest.mark.parametrize(
    "status",
    [302, 303, 307, 308, 400, 401, 403, 404, 429, 500, 502, 503, 504],
)
def test_http_failures_never_follow_redirects_or_expose_responses(
    message,
    gateway,
    status,
):
    state, transport = gateway
    state["status"] = status
    state["headers"] = {"Location": "https://unrelated.example.test/send"}
    state["body"] = {"secret": "123456", "recipient": RECEIVER}
    with pytest.raises(GlobalEmailAPIError) as raised:
        GlobalEmailBackend().send_messages([message])
    assert raised.value.status_code == status
    assert raised.value.retryable == (status == 429)
    assert raised.value.reason_code == "http_error"
    assert "123456" not in str(raised.value)
    assert RECEIVER not in str(raised.value)
    assert raised.value.response is None
    transport.assert_called_once()


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({}, "invalid_response"),
        ([], "invalid_response"),
        ({"status": True}, "invalid_response"),
        ({"status": "FAILED", "remark": "123456"}, "gateway_rejected"),
        ({"status": "success"}, "gateway_rejected"),
        ({"status": "SUCCESS", "requestId": "wrong-id"}, "response_mismatch"),
        ({"status": "SUCCESS", "receiver": "wrong@example.test"}, "response_mismatch"),
        ({"status": "SUCCESS", "templateId": "wrong-id"}, "response_mismatch"),
        ({"status": "SUCCESS", "templateId": True}, "response_mismatch"),
        ({"status": "SUCCESS", "type": "SMS"}, "response_mismatch"),
        ({"status": "SUCCESS", "subType": "OTP"}, "response_mismatch"),
        ({"status": "SUCCESS", "requestId": None}, "response_mismatch"),
        ({"status": "SUCCESS", "gatewayTxnid": ["invalid"]}, "invalid_response"),
        ({"status": "SUCCESS", "gatewayTxnid": "x" * 256}, "invalid_response"),
    ],
)
def test_validates_gateway_result(message, gateway, body, code):
    state, transport = gateway
    state["body"] = body
    with pytest.raises(GlobalEmailAPIError) as raised:
        GlobalEmailBackend().send_messages([message])
    assert raised.value.reason_code == code
    assert not raised.value.retryable
    assert "123456" not in str(raised.value)
    assert RECEIVER not in str(raised.value)
    transport.assert_called_once()


# An empty body is no longer here: a 2xx with nothing in it is an accepted
# request, covered by test_an_accepted_request_needs_no_body.
@pytest.mark.parametrize("body", [b"not json 123456", b"null", b'"SUCCESS"'])
def test_invalid_response_is_redacted(message, gateway, body):
    state, _ = gateway
    state["raw"] = body
    with pytest.raises(GlobalEmailAPIError, match="invalid_response") as raised:
        GlobalEmailBackend().send_messages([message])
    assert not raised.value.retryable
    assert "123456" not in str(raised.value)


@pytest.mark.parametrize(
    ("error_type", "code", "retryable"),
    [
        (requests.ConnectTimeout, "connection_timeout", True),
        (requests.ReadTimeout, "timeout", False),
        (requests.Timeout, "timeout", False),
        (requests.ConnectionError, "network_error", False),
    ],
)
def test_transport_errors_are_redacted(message, gateway, error_type, code, retryable):
    _, transport = gateway
    transport.side_effect = error_type(f"OTP 123456 for {RECEIVER}")
    with pytest.raises(GlobalEmailAPIError) as raised:
        GlobalEmailBackend().send_messages([message])
    assert raised.value.reason_code == code
    assert raised.value.retryable is retryable
    assert "123456" not in str(raised.value)
    assert RECEIVER not in str(raised.value)
    assert raised.value.__cause__ is None
    transport.assert_called_once()


def test_fail_silently_returns_zero_for_operational_error(message, gateway):
    state, transport = gateway
    state["status"] = 503
    with pytest.warns(DeprecationWarning, match="fail_silently"):
        backend = GlobalEmailBackend(fail_silently=True)
    assert backend.send_messages([message]) == 0
    transport.assert_called_once()


def test_fail_silently_does_not_hide_missing_configuration(message, gateway, settings):
    _, transport = gateway
    settings.ANYMAIL.pop("GLOBAL_EMAIL_TEMPLATE_ID")
    with pytest.warns(DeprecationWarning, match="fail_silently"):
        backend = GlobalEmailBackend(fail_silently=True)
    with pytest.raises(AnymailConfigurationError):
        backend.send_messages([message])
    transport.assert_not_called()


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("to", [RECEIVER, "second@example.test"]),
        ("bcc", ["hidden@example.test"]),
        ("reply_to", ["reply@example.test"]),
        ("extra_headers", {"Reply-To": "reply@example.test"}),
        ("extra_headers", {"From": "spoofed@example.test"}),
        ("extra_headers", {"X-Custom": "secret"}),
        ("attachments", [("file.txt", "contents", "text/plain")]),
        ("content_subtype", "html"),
        ("content_subtype", "calendar"),
        ("body", ""),
        ("body", "   "),
        ("body", 123),
        ("subject", 123),
        ("envelope_sender", "different@example.test"),
        ("metadata", {"reference": "123"}),
        ("tags", ["update"]),
        ("track_clicks", True),
        ("track_opens", False),
        ("send_at", "2030-01-01T00:00:00Z"),
        ("merge_data", {RECEIVER: {"name": "User"}}),
        ("merge_global_data", {"name": "User"}),
        ("merge_headers", {RECEIVER: {"Header": "value"}}),
        ("merge_metadata", {RECEIVER: {"id": "value"}}),
        ("esp_extra", {"receiver": "injected@example.test"}),
        ("esp_extra", {"content_type": "html"}),
        ("esp_extra", {"request_id": "not-a-uuid"}),
    ],
)
def test_unsupported_features_are_never_silently_dropped(
    message,
    gateway,
    settings,
    attribute,
    value,
):
    _, transport = gateway
    settings.ANYMAIL["IGNORE_UNSUPPORTED_FEATURES"] = True
    setattr(message, attribute, value)
    with pytest.raises(AnymailUnsupportedFeature):
        GlobalEmailBackend().send_messages([message])
    transport.assert_not_called()


def test_only_cc_without_primary_recipient_is_rejected(message, gateway):
    _, transport = gateway
    message.to = []
    message.cc = ["cc@example.test"]
    with pytest.raises(AnymailUnsupportedFeature, match="primary recipient"):
        GlobalEmailBackend().send_messages([message])
    transport.assert_not_called()


def test_invalid_address_is_redacted(message, gateway):
    _, transport = gateway
    message.to = ["123456 recipient <bad@@example.test>"]
    with pytest.raises(AnymailInvalidAddress) as raised:
        GlobalEmailBackend().send_messages([message])
    assert "123456" not in str(raised.value)
    transport.assert_not_called()


def test_allauth_style_html_alternative_sends_plaintext(message, gateway):
    _, transport = gateway
    combined = EmailMultiAlternatives(
        subject=message.subject,
        body=message.body,
        from_email=message.from_email,
        to=message.to,
        alternatives=[("<p>HTML fallback</p>", "text/html")],
    )
    assert GlobalEmailBackend().send_messages([combined]) == 1
    payload = json.loads(transport.call_args.args[0].body)
    assert payload["content"] == message.body
    assert "HTML fallback" not in payload["content"]


@pytest.mark.parametrize(
    "alternatives",
    [
        [("calendar", "text/calendar")],
        [("extra text", "text/plain")],
        [("<p>One</p>", "text/html"), ("<p>Two</p>", "text/html")],
    ],
)
def test_unsupported_mime_alternatives_are_rejected(message, gateway, alternatives):
    _, transport = gateway
    message.alternatives = alternatives
    with pytest.raises(AnymailUnsupportedFeature):
        GlobalEmailBackend().send_messages([message])
    transport.assert_not_called()


def test_anymail_message_and_send_defaults(gateway, settings):
    _, transport = gateway
    settings.ANYMAIL["GLOBAL_EMAIL_SEND_DEFAULTS"] = {
        "template_id": "default-approved-template",
        "esp_extra": {"content_type": "otp"},
    }
    message = AnymailMessage("OTP", "Your OTP is 123456", to=[RECEIVER])
    assert GlobalEmailBackend().send_messages([message]) == 1
    payload = json.loads(transport.call_args.args[0].body)
    assert payload["templateId"] == "default-approved-template"
    assert payload["contentType"] == "otp"


def test_disables_anymail_debug_dump_and_automatic_retries(settings, capsys):
    settings.ANYMAIL["DEBUG_API_REQUESTS"] = True
    backend = GlobalEmailBackend()
    assert backend.open()
    try:
        assert not backend.debug_api_requests
        assert backend.session.hooks["response"] == []
        assert backend.session.get_adapter(API_URL).max_retries.total == 0
    finally:
        backend.close()
    assert capsys.readouterr().out == ""


def test_uses_django_email_timeout_without_anymail_override(settings, message, gateway):
    settings.ANYMAIL.pop("GLOBAL_EMAIL_REQUESTS_TIMEOUT")
    settings.EMAIL_TIMEOUT = 3
    _, transport = gateway
    assert GlobalEmailBackend().send_messages([message]) == 1
    assert transport.call_args.kwargs["timeout"] == settings.EMAIL_TIMEOUT


def test_empty_batch_or_no_recipients_does_not_send(message, gateway):
    _, transport = gateway
    backend = GlobalEmailBackend()
    assert backend.send_messages([]) == 0
    message.to = []
    assert backend.send_messages([message]) == 0
    transport.assert_not_called()


def test_operational_errors_are_anymail_errors():
    assert isinstance(GlobalEmailAPIError("timeout"), AnymailError)


MESSAGE_URL = "http://global-notification.internal/internal/v3/notification/message"


def test_the_message_endpoint_carries_the_same_fields(settings):
    """The SES path 404s in production; this one takes email too, without CC."""
    settings.ANYMAIL = {
        "GLOBAL_EMAIL_API_URL": MESSAGE_URL,
        "GLOBAL_EMAIL_TEMPLATE_ID": "approved-template-123",
    }
    message = EmailMessage(
        subject="Invitation",
        body="Accept the invite.",
        to=[RECEIVER],
        cc=["reviewer@example.test"],
    )
    backend = GlobalEmailBackend()
    payload = backend.build_message_payload(message, backend.send_defaults)
    body = json.loads(payload.serialize_data())

    assert body["type"] == ["email"]
    assert body["origin"] == "abha"
    assert body["sender"] == "NHASMS"
    # No CC field exists here, so a copied address becomes another receiver.
    assert body["receiver"] == [
        {"key": "emailId", "value": RECEIVER},
        {"key": "emailId", "value": "reviewer@example.test"},
    ]
    assert "ccRecipients" not in body
    assert {entry["key"]: entry["value"] for entry in body["notification"]} == {
        "requestId": payload.data["requestId"],
        "templateId": "approved-template-123",
        "subject": "Invitation",
        "content": "Accept the invite.",
    }
    # A java.sql.Timestamp, which is what this service binds; never ISO-8601.
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6}",
        payload.headers["TIMESTAMP"],
    )


def test_the_ses_path_keeps_its_own_shape():
    message = EmailMessage(subject="Invitation", body="Accept.", to=[RECEIVER])
    backend = GlobalEmailBackend()
    payload = backend.build_message_payload(message, backend.send_defaults)
    body = json.loads(payload.serialize_data())

    assert body["receiver"] == RECEIVER
    assert body["subject"] == "Invitation"
    assert payload.headers["TIMESTAMP"].endswith("Z")


@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_any_2xx_is_an_accepted_message(message, gateway, status):
    """The document specifies 200; the multi-channel endpoint answers 202."""
    state, _ = gateway
    state["status"] = status

    GlobalEmailBackend().send_messages([message])

    assert message.anymail_status.recipients[RECEIVER].status == "queued"


def test_an_accepted_request_needs_no_body(message, gateway):
    """Nothing to check means nothing to reject; the request id is the handle."""
    state, _ = gateway
    state["status"] = 202
    state["raw"] = b""

    GlobalEmailBackend().send_messages([message])

    status = message.anymail_status.recipients[RECEIVER]
    assert status.status == "queued"
    assert UUID(status.message_id)


def test_a_body_that_says_something_is_still_checked(message, gateway):
    state, _ = gateway
    state["status"] = 202
    state["body"] = {"status": "FAILED"}

    with pytest.raises(GlobalEmailAPIError) as raised:
        GlobalEmailBackend().send_messages([message])

    assert raised.value.reason_code == "gateway_rejected"
