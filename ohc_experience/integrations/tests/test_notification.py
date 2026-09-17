"""ABDM notification gateway: approved templates, filled in and sent."""

from __future__ import annotations

import re
import uuid

import pytest
from django.core.cache import cache

from ohc_experience.integrations.http import reset_breakers
from ohc_experience.integrations.notification.adapter import REQUEST_ID_HEADER
from ohc_experience.integrations.notification.adapter import TEMPLATES_CACHE_KEY
from ohc_experience.integrations.notification.adapter import TIMESTAMP_HEADER
from ohc_experience.integrations.notification.adapter import AbdmNotificationGateway
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import NotificationChannel
from ohc_experience.integrations.ports import NotificationContentType
from ohc_experience.integrations.ports import NotificationMessage
from ohc_experience.integrations.tests.notification_stub import EMAIL_OTP_TEMPLATE_ID
from ohc_experience.integrations.tests.notification_stub import MESSAGE_PATH
from ohc_experience.integrations.tests.notification_stub import REACTIVATION_TEMPLATE_ID
from ohc_experience.integrations.tests.notification_stub import SMS_OTP_TEMPLATE_ID
from ohc_experience.integrations.tests.notification_stub import TEMPLATE_PATH
from ohc_experience.integrations.tests.notification_stub import TEMPLATES_PATH
from ohc_experience.integrations.tests.notification_stub import (
    NotificationStubTransport,
)
from ohc_experience.integrations.tests.notification_stub import template

APP_URL = "https://notification-app.test"
DB_URL = "https://notification-db.test"
UNAVAILABLE = 503
#: The template list, then the send.
FIRST_SEND_CALLS = 2
TWO_SENDS = 2

SMS_OTP = NotificationMessage(
    channel=NotificationChannel.SMS,
    receiver="9999999999",
    template_id=SMS_OTP_TEMPLATE_ID,
    subject="Mobile verification",
    values=("123456",),
    content_type=NotificationContentType.OTP,
)
EMAIL_OTP = NotificationMessage(
    channel=NotificationChannel.EMAIL,
    receiver="user@example.com",
    template_id=EMAIL_OTP_TEMPLATE_ID,
    subject="Email verification",
    values=("654321",),
    content_type=NotificationContentType.OTP,
)


@pytest.fixture(autouse=True)
def _isolated(settings):
    settings.NOTIFICATION_APP_BASE_URL = APP_URL
    settings.NOTIFICATION_DB_BASE_URL = f"{DB_URL}/"
    reset_breakers()
    cache.delete(TEMPLATES_CACHE_KEY)
    yield
    reset_breakers()
    cache.delete(TEMPLATES_CACHE_KEY)


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
                {"key": "subject", "value": "Mobile verification"},
                {"key": "content", "value": "Your sandbox OTP is 123456."},
            ],
        },
    ]


def test_an_email_goes_to_the_email_id_receiver(gateway, transport):
    gateway.send(EMAIL_OTP)

    [body] = transport.sent()
    assert body["type"] == ["email"]
    assert body["receiver"] == [{"key": "emailId", "value": "user@example.com"}]
    assert body["notification"][2] == {
        "key": "content",
        "value": "Use 654321 to verify your email.",
    }


def test_every_call_carries_a_request_id_and_timestamp(gateway, transport):
    gateway.send(SMS_OTP)

    assert len(transport.calls) == FIRST_SEND_CALLS
    for request in transport.calls:
        uuid.UUID(request.headers[REQUEST_ID_HEADER])
        assert re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z",
            request.headers[TIMESTAMP_HEADER],
        )


def test_templates_come_from_notification_db_once(gateway, transport):
    gateway.send(SMS_OTP)
    gateway.send(EMAIL_OTP)

    [listing] = transport.requests("GET", TEMPLATES_PATH)
    assert str(listing.url) == f"{DB_URL}{TEMPLATES_PATH}"
    assert len(transport.requests("POST", MESSAGE_PATH)) == TWO_SENDS


def test_a_template_outside_the_sandbox_list_is_fetched_by_id(gateway, transport):
    transport.other_templates[REACTIVATION_TEMPLATE_ID] = template(
        REACTIVATION_TEMPLATE_ID,
        "Hello {0}, your account is active again.",
    )
    reactivation = NotificationMessage(
        channel=NotificationChannel.SMS,
        receiver="9999999999",
        template_id=REACTIVATION_TEMPLATE_ID,
        subject="Account reactivated",
        values=("Meera",),
    )

    gateway.send(reactivation)
    gateway.send(reactivation)

    assert len(transport.requests("GET", TEMPLATE_PATH)) == 1
    assert transport.sent()[0]["notification"][2]["value"] == (
        "Hello Meera, your account is active again."
    )
    assert transport.sent()[0]["contentType"] == "info"


def test_an_unknown_template_sends_nothing(gateway, transport):
    unknown = NotificationMessage(
        channel=NotificationChannel.SMS,
        receiver="9999999999",
        template_id="1",
        subject="Unknown",
        values=("x",),
    )

    with pytest.raises(AdapterError) as excinfo:
        gateway.send(unknown)

    assert excinfo.value.code == "HTTP_404"
    assert transport.sent() == []


@pytest.mark.parametrize("values", [(), ("123456", "extra")])
def test_values_must_fill_the_template_exactly(gateway, transport, values):
    with pytest.raises(AdapterError) as excinfo:
        gateway.send(
            NotificationMessage(
                channel=NotificationChannel.SMS,
                receiver="9999999999",
                template_id=SMS_OTP_TEMPLATE_ID,
                subject="Mobile verification",
                values=values,
            ),
        )

    assert excinfo.value.code == "TEMPLATE_MISMATCH"
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


def test_a_malformed_template_list_is_reported(gateway, transport):
    transport.sandbox_templates = [{"id": SMS_OTP_TEMPLATE_ID}]

    with pytest.raises(AdapterError) as excinfo:
        gateway.send(SMS_OTP)

    assert excinfo.value.code == "MALFORMED_RESPONSE"
