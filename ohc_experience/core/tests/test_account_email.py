"""Account mail renders real allauth templates into the gateway outbox, except
verification and password reset codes, which go straight to the notification
gateway, and the unknown-address notice, which is not sent at all."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.urls import reverse

from ohc_experience.core.mail import QUEUED_GLOBAL_EMAIL_BACKEND
from ohc_experience.experiences.models import Notification
from ohc_experience.integrations.local import LocalNotificationGateway
from ohc_experience.integrations.notification.templates import PASSWORD_RESET_CODE

pytestmark = pytest.mark.django_db


@pytest.fixture
def signup_data():
    # Unique addresses keep allauth's cache-backed send limits independent.
    return {
        "name": "Applicant Example",
        "email": f"{uuid4().hex}@example.org",
        "mobile_number": "9876543210",
        "organisation": "Example Health",
        "organisation_type": "private_company",
        "password1": "portal-test-password-2026",
        "password2": "portal-test-password-2026",
    }


@pytest.fixture(autouse=True)
def gateway(settings, monkeypatch):
    settings.EMAIL_BACKEND = QUEUED_GLOBAL_EMAIL_BACKEND
    settings.ANYMAIL = {
        "GLOBAL_EMAIL_API_URL": "https://gateway.invalid/email/send",
    }
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {}
    network = Mock(side_effect=AssertionError("Unexpected network request"))
    monkeypatch.setattr("requests.Session.request", network)
    return network


def test_signup_sends_its_codes_through_the_notification_gateway(
    client,
    gateway,
    signup_data,
):
    response = client.post(
        reverse("account_signup"),
        data=signup_data,
    )
    assert response.status_code == 302  # noqa: PLR2004
    sent = LocalNotificationGateway().sent()
    assert {message["channel"] for message in sent} == {"email", "sms"}
    [emailed] = [message for message in sent if message["channel"] == "email"]
    assert emailed["receiver"] == signup_data["email"]
    assert not Notification.objects.exists()
    gateway.assert_not_called()


def test_password_reset_sends_its_code_through_the_notification_gateway(
    client,
    gateway,
    user,
):
    response = client.post(reverse("account_reset_password"), {"email": user.email})
    assert response.status_code == 302  # noqa: PLR2004
    [emailed] = LocalNotificationGateway().sent()
    assert emailed["channel"] == "email"
    assert emailed["receiver"] == user.email
    assert emailed["template_id"] == PASSWORD_RESET_CODE.id
    assert emailed["content_type"] == "otp"
    assert not Notification.objects.exists()
    gateway.assert_not_called()


def test_an_unknown_address_is_told_nothing(client, gateway):
    """The form answers every address alike, so the reply is the tell, not a
    mail. No template was registered for one, and sending none cannot fail."""
    response = client.post(
        reverse("account_reset_password"),
        {"email": "nobody@example.org"},
    )
    assert response.status_code == 302  # noqa: PLR2004
    assert not Notification.objects.exists()
    assert LocalNotificationGateway().sent() == []
    gateway.assert_not_called()
