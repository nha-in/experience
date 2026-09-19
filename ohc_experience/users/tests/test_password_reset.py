"""Resetting a password with the emailed one-time password."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from ohc_experience.integrations.local import LocalNotificationGateway
from ohc_experience.integrations.notification.templates import PASSWORD_RESET_CODE
from ohc_experience.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from django.test import Client

    from ohc_experience.users.models import User

pytestmark = pytest.mark.django_db

EMAIL = "arun@sunrise.in"
OLD_PASSWORD = "sandbox-Kerala-2026"  # noqa: S105
NEW_PASSWORD = "sandbox-Wayanad-2027"  # noqa: S105
REQUEST_PAGE = reverse("account_reset_password")
CODE_PAGE = reverse("account_confirm_password_reset_code")
PASSWORD_PAGE = reverse("account_complete_password_reset")
DONE_PAGE = reverse("account_password_reset_completed")


@pytest.fixture
def account() -> User:
    user = UserFactory.create(email=EMAIL, password=OLD_PASSWORD)
    EmailAddress.objects.create(user=user, email=EMAIL, primary=True, verified=True)
    return user


def emailed_code() -> str:
    [emailed] = LocalNotificationGateway().sent()
    return emailed["values"][0]


def request_code(client: Client) -> str:
    response = client.post(REQUEST_PAGE, {"email": EMAIL})
    assert response["Location"] == CODE_PAGE
    return emailed_code()


def test_the_request_emails_an_otp_rather_than_a_link(client: Client, account: User):
    code = request_code(client)

    [emailed] = LocalNotificationGateway().sent()
    assert emailed["channel"] == "email"
    assert emailed["receiver"] == EMAIL
    assert emailed["template_id"] == PASSWORD_RESET_CODE.id
    assert code.isdigit()
    assert len(code) == 6  # noqa: PLR2004


def test_the_code_sets_a_new_password(client: Client, account: User):
    code = request_code(client)

    confirmed = client.post(CODE_PAGE, {"code": code})
    assert confirmed["Location"] == PASSWORD_PAGE
    saved = client.post(
        PASSWORD_PAGE,
        {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )
    assert saved["Location"] == DONE_PAGE

    account.refresh_from_db()
    assert account.check_password(NEW_PASSWORD)


def test_a_wrong_code_keeps_the_old_password(client: Client, account: User):
    request_code(client)

    response = client.post(CODE_PAGE, {"code": "000000"})
    assert response.status_code == HTTPStatus.OK
    assert client.get(PASSWORD_PAGE)["Location"] == CODE_PAGE

    account.refresh_from_db()
    assert account.check_password(OLD_PASSWORD)


def test_the_password_page_needs_a_confirmed_code(client: Client, account: User):
    assert client.get(PASSWORD_PAGE)["Location"] == REQUEST_PAGE
    assert client.get(CODE_PAGE)["Location"] == reverse("account_login")


def test_an_unknown_address_sends_no_code(client: Client):
    response = client.post(REQUEST_PAGE, {"email": "nobody@example.org"})

    assert response["Location"] == CODE_PAGE
    assert LocalNotificationGateway().sent() == []
