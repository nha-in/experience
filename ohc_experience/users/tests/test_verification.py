"""Email and mobile verification codes, sent through the notification gateway."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.urls import reverse

from ohc_experience.integrations import local
from ohc_experience.integrations.local import LocalNotificationGateway
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.users.models import User
from ohc_experience.users.stages import PENDING_MOBILE_NUMBER_SESSION_KEY
from ohc_experience.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

pytestmark = pytest.mark.django_db

EMAIL = "arun@sunrise.in"
PASSWORD = "sandbox-Kerala-2026"  # noqa: S105
MOBILE_NUMBER = "+919876543210"
SIGNUP_DATA = {
    "name": "Arun Nair",
    "email": EMAIL,
    "mobile_number": "98765 43210",
    "organisation": "Sunrise Health Systems",
    "organisation_type": "private_company",
    "password1": PASSWORD,
    "password2": PASSWORD,
}
CODE_PAGE = reverse("account_email_verification_sent")
SETTINGS_SHELL = 'id="settings-nav-profile"'
ENTRANCE_SHELL = "marketing-auth-panel"
PHONE_CODE_PAGE = reverse("account_verify_phone")


def sent(channel: str) -> list[dict]:
    return [m for m in LocalNotificationGateway().sent() if m["channel"] == channel]


def last_code(channel: str) -> str:
    return sent(channel)[-1]["values"][0]


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def sign_up(client: Client, **data) -> None:
    response = client.post(reverse("account_signup"), data=SIGNUP_DATA | data)
    assert response["Location"] == CODE_PAGE


def confirm_email(client: Client):
    return client.post(CODE_PAGE, {"code": last_code("email")})


def signed_in_user(client: Client) -> User | None:
    user_id = client.session.get("_auth_user_id")
    return User.objects.get(pk=user_id) if user_id else None


class TestEmailCode:
    def test_signup_emails_a_six_digit_code(self, client: Client, settings):
        sign_up(client)

        [message] = sent("email")
        [code] = message.pop("values")
        assert re.fullmatch(r"\d{6}", code)
        assert message == {
            "channel": "email",
            "receiver": EMAIL,
            "template_id": settings.NOTIFICATION_EMAIL_OTP_TEMPLATE_ID,
            "subject": "Email verification",
            "content_type": "otp",
        }
        assert mail.outbox == []

    def test_the_code_verifies_the_address_then_asks_for_the_mobile_code(
        self,
        client: Client,
    ):
        sign_up(client)

        response = confirm_email(client)

        assert response["Location"] == PHONE_CODE_PAGE
        assert EmailAddress.objects.get(email=EMAIL).verified
        assert signed_in_user(client) is None

    def test_a_wrong_code_is_refused(self, client: Client):
        sign_up(client)

        response = client.post(CODE_PAGE, {"code": wrong(last_code("email"))})

        assert response.status_code == HTTPStatus.OK
        assert response.context["verify_form"].errors
        assert not EmailAddress.objects.get(email=EMAIL).verified
        assert signed_in_user(client) is None

    def test_an_undelivered_code_says_so_and_can_be_sent_again(self, client: Client):
        local.always_fail(ExternalSystem.NOTIFICATION)
        sign_up(client)

        page = client.get(CODE_PAGE).content.decode()

        assert "We could not send your code" in page
        assert "Confirmation email sent" not in page
        local.clear_failures(ExternalSystem.NOTIFICATION)
        client.post(CODE_PAGE, {"action": "resend"})
        assert len(sent("email")) == 1


class TestEmailFromSettings:
    def test_a_new_address_is_confirmed_in_the_app_shell(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        client = sign_in(user)
        client.post(
            reverse("account_email"),
            {"email": "second@sunrise.in", "action_add": ""},
        )

        page = client.get(CODE_PAGE).content.decode()

        assert sent("email")[-1]["receiver"] == "second@sunrise.in"
        assert SETTINGS_SHELL in page
        assert ENTRANCE_SHELL not in page


class TestMobileNumberAtSignup:
    def test_the_number_is_texted_a_code_after_the_email_is_confirmed(
        self,
        client: Client,
        settings,
    ):
        sign_up(client, mobile_number="98765 43210")
        assert sent("sms") == []

        response = confirm_email(client)

        assert response["Location"] == PHONE_CODE_PAGE
        [message] = sent("sms")
        assert message["receiver"] == "9876543210"
        assert message["template_id"] == settings.NOTIFICATION_SMS_OTP_TEMPLATE_ID
        assert message["content_type"] == "otp"
        assert User.objects.get(email=EMAIL).phone_number == ""

    def test_the_texted_code_saves_the_number_and_signs_in(self, client: Client):
        sign_up(client, mobile_number="98765 43210")
        confirm_email(client)

        response = client.post(PHONE_CODE_PAGE, {"code": last_code("sms")})

        assert response.status_code == HTTPStatus.FOUND
        user = signed_in_user(client)
        assert user.phone_number == MOBILE_NUMBER
        assert user.phone_verified

    def test_skipping_signs_in_without_the_number(self, client: Client):
        sign_up(client, mobile_number="98765 43210")
        confirm_email(client)
        skip = reverse("account_skip_phone_verification")
        assert skip in client.get(PHONE_CODE_PAGE).content.decode()

        response = client.post(skip)

        assert response.status_code == HTTPStatus.FOUND
        user = signed_in_user(client)
        assert user.phone_number == ""
        assert not user.phone_verified

    def test_the_code_screen_keeps_the_signed_out_shell(self, client: Client):
        sign_up(client, mobile_number="98765 43210")
        confirm_email(client)

        page = client.get(PHONE_CODE_PAGE).content.decode()

        assert ENTRANCE_SHELL in page
        assert SETTINGS_SHELL not in page

    def test_a_number_verified_by_another_account_is_refused(self, client: Client):
        UserFactory(phone_number=MOBILE_NUMBER, phone_verified=True)
        sign_up(client, mobile_number=MOBILE_NUMBER)
        confirm_email(client)

        response = client.post(PHONE_CODE_PAGE, {"code": last_code("sms")})

        assert response.status_code == HTTPStatus.OK
        assert response.context["verify_form"].errors
        assert User.objects.get(email=EMAIL).phone_number == ""

    def test_a_pending_number_never_reaches_another_account(self, client: Client):
        user = UserFactory(password=PASSWORD)
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=True,
        )
        session = client.session
        session[PENDING_MOBILE_NUMBER_SESSION_KEY] = {
            "user_id": UserFactory().pk,
            "phone": MOBILE_NUMBER,
        }
        session.save()

        client.post(
            reverse("account_login"),
            {"login": user.email, "password": PASSWORD},
        )

        assert signed_in_user(client) == user
        assert sent("sms") == []
        assert PENDING_MOBILE_NUMBER_SESSION_KEY not in client.session


class TestMobileNumberFromProfile:
    def test_a_new_number_is_saved_only_once_confirmed(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        client = sign_in(user)

        response = client.post(
            reverse("account_change_phone"),
            {"phone": "98765 43210"},
        )

        assert response["Location"] == PHONE_CODE_PAGE
        user.refresh_from_db()
        assert user.phone_number == ""
        client.post(PHONE_CODE_PAGE, {"code": last_code("sms")})
        user.refresh_from_db()
        assert user.phone_number == MOBILE_NUMBER
        assert user.phone_verified

    def test_an_earlier_unverified_number_can_be_confirmed(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        user.phone_number = "+91 98765 43210"
        user.save(update_fields=["phone_number"])
        client = sign_in(user)
        page = client.get(reverse("account_change_phone")).content.decode()
        assert "Confirm this number" in page

        client.post(reverse("account_change_phone"), {"action": "verify"})

        assert sent("sms")[-1]["receiver"] == "9876543210"
        client.post(PHONE_CODE_PAGE, {"code": last_code("sms")})
        user.refresh_from_db()
        assert user.phone_number == MOBILE_NUMBER
        assert user.phone_verified

    def test_the_code_screen_stays_in_the_app_shell(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        client = sign_in(user)
        client.post(reverse("account_change_phone"), {"phone": "98765 43210"})

        page = client.get(PHONE_CODE_PAGE).content.decode()

        assert SETTINGS_SHELL in page
        assert ENTRANCE_SHELL not in page

    def test_the_profile_shows_whether_the_number_is_verified(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        user.phone_number = MOBILE_NUMBER
        user.phone_verified = True
        user.save(update_fields=["phone_number", "phone_verified"])

        page = sign_in(user).get(reverse("users:profile")).content.decode()

        assert MOBILE_NUMBER in page
        assert 'ui-badge--success ui-badge--size-sm">Verified' in page
        assert reverse("account_change_phone") in page
