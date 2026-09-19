"""ABDM notification gateway: approved templates, filled in and sent."""

from __future__ import annotations

import re
import uuid

import pytest
from django.template.loader import render_to_string

from ohc_experience.integrations.http import reset_breakers
from ohc_experience.integrations.notification import templates as registry
from ohc_experience.integrations.notification.adapter import REQUEST_ID_HEADER
from ohc_experience.integrations.notification.adapter import TIMESTAMP_HEADER
from ohc_experience.integrations.notification.adapter import AbdmNotificationGateway
from ohc_experience.integrations.notification.templates import EMAIL_VERIFICATION_CODE
from ohc_experience.integrations.notification.templates import MOBILE_VERIFICATION_CODE
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import NotificationMessage
from ohc_experience.integrations.ports import NotificationTemplate
from ohc_experience.integrations.tests.notification_stub import EMAIL_OTP_TEMPLATE_ID
from ohc_experience.integrations.tests.notification_stub import MESSAGE_PATH
from ohc_experience.integrations.tests.notification_stub import SMS_OTP_TEMPLATE_ID
from ohc_experience.integrations.tests.notification_stub import (
    NotificationStubTransport,
)

APP_URL = "https://notification-app.test"
UNAVAILABLE = 503

#: What notification-db holds for template 100001, filled in.
EMAIL_TEXT = (
    "Dear User, \n"
    "To complete the verification of your email address please use the "
    "following One-Time Password (OTP) 654321. Please enter this OTP on the "
    "verification page to confirm your email address. This OTP is valid for "
    "10 minutes only.\n"
    "\n"
    "Thank you for your cooperation.\n"
    "\n"
    "ABDM, NHA"
)
#: What notification-db holds for template 1007172534306341299, filled in.
SMS_TEXT = (
    "OTP for sandbox application to verify mobile number is 123456. "
    "This OTP is valid for 10 minutes and can be used only once.\n"
    "\n"
    "ABDM, National Health Authority"
)

SMS_OTP = NotificationMessage(
    template=MOBILE_VERIFICATION_CODE,
    receiver="9999999999",
    values=("123456",),
)
EMAIL_OTP = NotificationMessage(
    template=EMAIL_VERIFICATION_CODE,
    receiver="user@example.com",
    values=("654321",),
)


@pytest.fixture(autouse=True)
def _isolated(settings):
    settings.NOTIFICATION_APP_BASE_URL = APP_URL
    reset_breakers()
    yield
    reset_breakers()


@pytest.fixture
def transport():
    return NotificationStubTransport()


@pytest.fixture
def gateway(transport):
    built = AbdmNotificationGateway(transport=transport)
    yield built
    built.close()


def test_an_sms_otp_is_the_documented_message(gateway, transport):
    gateway.send(SMS_OTP)

    [request] = transport.requests("POST", MESSAGE_PATH)
    assert str(request.url) == f"{APP_URL}{MESSAGE_PATH}"
    assert transport.sent() == [
        {
            "origin": "abha",
            "type": ["sms"],
            "contentType": "otp",
            "sender": "NHASMS",
            "receiver": [{"key": "mobile", "value": "9999999999"}],
            "notification": [
                {"key": "templateId", "value": SMS_OTP_TEMPLATE_ID},
                {"key": "content", "value": SMS_TEXT},
            ],
        },
    ]


def test_an_email_goes_to_the_same_endpoint_as_a_message(gateway, transport):
    gateway.send(EMAIL_OTP)

    [request] = transport.requests("POST", MESSAGE_PATH)
    assert transport.sent() == [
        {
            "origin": "abha",
            "type": ["email"],
            "contentType": "otp",
            "sender": "NHASMS",
            "receiver": [{"key": "emailId", "value": "user@example.com"}],
            "notification": [
                {"key": "requestId", "value": request.headers[REQUEST_ID_HEADER]},
                {"key": "templateId", "value": EMAIL_OTP_TEMPLATE_ID},
                {"key": "subject", "value": "Email verification"},
                {"key": "content", "value": EMAIL_TEXT},
            ],
        },
    ]


def test_every_call_carries_a_request_id_and_timestamp(gateway, transport):
    gateway.send(SMS_OTP)

    [request] = transport.calls
    uuid.UUID(request.headers[REQUEST_ID_HEADER])
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}",
        request.headers[TIMESTAMP_HEADER],
    )


def test_every_notification_renders_its_approved_text():
    listed = [
        value
        for value in vars(registry).values()
        if isinstance(value, NotificationTemplate)
    ]

    assert listed
    for template in listed:
        content = render_to_string(
            template.body,
            {name: f"__{name}__" for name in template.values},
        ).strip()
        assert content
        assert all(f"__{name}__" in content for name in template.values)


@pytest.mark.parametrize("values", [(), ("123456", "extra")])
def test_values_must_fill_the_template_exactly(gateway, transport, values):
    with pytest.raises(ValueError, match="zip"):
        gateway.send(
            NotificationMessage(
                template=MOBILE_VERIFICATION_CODE,
                receiver="9999999999",
                values=values,
            ),
        )

    assert transport.sent() == []


@pytest.mark.parametrize("status", ["SENT", "success"])
def test_a_sent_status_is_accepted(gateway, transport, status):
    transport.send_body = {"status": status}

    gateway.send(SMS_OTP)


@pytest.mark.parametrize("body", [{"status": "FAILED"}, {}, ["SENT"]])
def test_anything_but_a_sent_status_is_a_failure(gateway, transport, body):
    transport.send_body = body

    with pytest.raises(AdapterError) as excinfo:
        gateway.send(SMS_OTP)

    assert excinfo.value.code == "NOT_SENT"
    assert "123456" not in str(excinfo.value)


def test_a_failed_send_is_not_retried(gateway, transport):
    """A resent POST is a second SMS."""
    transport.send_status_code = UNAVAILABLE

    with pytest.raises(AdapterError) as excinfo:
        gateway.send(SMS_OTP)

    assert excinfo.value.code == f"HTTP_{UNAVAILABLE}"
    assert len(transport.requests("POST", MESSAGE_PATH)) == 1
