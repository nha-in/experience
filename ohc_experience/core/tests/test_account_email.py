"""Account flows render their real allauth templates into the gateway outbox."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from anymail.exceptions import AnymailConfigurationError
from django.urls import reverse

from ohc_experience.core.mail import QUEUED_GLOBAL_EMAIL_BACKEND
from ohc_experience.experiences.models import Notification
from ohc_experience.users.models import User

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
        "account/email/email_confirmation_signup": "74010",
        "account/email/password_reset_key": "74011",
    }
    network = Mock(side_effect=AssertionError("Unexpected network request"))
    monkeypatch.setattr("requests.Session.request", network)
    return network


def test_signup_renders_verification_email_into_outbox(client, gateway, signup_data):
    response = client.post(
        reverse("account_signup"),
        data=signup_data,
    )
    assert response.status_code == 302  # noqa: PLR2004
    notification = Notification.objects.get()
    assert notification.recipient == signup_data["email"]
    assert notification.template_id == "74010"
    assert "/accounts/confirm-email/" in notification.body
    assert notification.sent_at is None
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


def test_unconfigured_signup_template_rolls_back_account_and_email(
    client,
    settings,
    signup_data,
):
    settings.GLOBAL_EMAIL_TEMPLATE_IDS = {"notification": "74012"}
    with pytest.raises(AnymailConfigurationError):
        client.post(
            reverse("account_signup"),
            data=signup_data,
        )
    assert not User.objects.filter(email=signup_data["email"]).exists()
    # Django may separately email ADMINS about the 500 after the request rolls
    # back. The failed account's verification email must never be queued.
    assert not Notification.objects.filter(recipient=signup_data["email"]).exists()
