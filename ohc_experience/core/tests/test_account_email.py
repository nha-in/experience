"""Account mail renders real allauth templates into the gateway outbox, except
verification codes, which go straight to the notification gateway."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.urls import reverse

from ohc_experience.core.mail import QUEUED_GLOBAL_EMAIL_BACKEND
from ohc_experience.experiences.models import Notification
from ohc_experience.integrations.local import LocalNotificationGateway

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
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {
        "account/email/password_reset_key": "74011",
    }
    network = Mock(side_effect=AssertionError("Unexpected network request"))
    monkeypatch.setattr("requests.Session.request", network)
    return network


def test_signup_sends_its_code_through_the_notification_gateway(
    client,
    gateway,
    signup_data,
):
    response = client.post(
        reverse("account_signup"),
        data=signup_data,
    )
    assert response.status_code == 302  # noqa: PLR2004
    [sent] = LocalNotificationGateway().sent()
    assert sent["receiver"] == signup_data["email"]
    assert not Notification.objects.exists()
    gateway.assert_not_called()


def test_password_reset_renders_reset_link_into_outbox(client, user, gateway):
    response = client.post(reverse("account_reset_password"), {"email": user.email})
    assert response.status_code == 302  # noqa: PLR2004
    notification = Notification.objects.get()
    assert notification.recipient == user.email
    assert notification.template_id == "74011"
    assert "/accounts/password/reset/key/" in notification.body
    assert notification.sent_at is None
    gateway.assert_not_called()
