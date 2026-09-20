"""Email and mobile verification codes, sent through the notification gateway."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.internal.flows.email_verification_by_code import (
    EMAIL_VERIFICATION_CODE_SESSION_KEY,
)
from allauth.account.models import EmailAddress
from django.core import mail
from django.test import Client
from django.urls import reverse

from ohc_experience.integrations import local
from ohc_experience.integrations.local import LocalNotificationGateway
from ohc_experience.integrations.notification.templates import EMAIL_VERIFICATION_CODE
from ohc_experience.integrations.notification.templates import MOBILE_VERIFICATION_CODE
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.users.models import User
from ohc_experience.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.django_db

EMAIL = "arun@sunrise.in"
PASSWORD = "sandbox-Kerala-2026"  # noqa: S105
MOBILE_NUMBER = "+919876543210"
SIGNUP_DATA = {
    "name": "Arun Nair",
    "email": EMAIL,
    "mobile_number": "9876543210",
    "organisation": "Sunrise Health Systems",
    "organisation_type": "private_company",
    "website": "https://sunrise.in",
    "password1": PASSWORD,
    "password2": PASSWORD,
}
VERIFY_PAGE = reverse("account_verification")
CODE_PAGE = reverse("account_email_verification_sent")
PHONE_CODE_PAGE = reverse("account_verify_phone")
SETTINGS_SHELL = 'id="settings-nav-profile"'
ENTRANCE_SHELL = "marketing-auth-panel"
EMAIL_BOX = 'name="email-code"'
PHONE_BOX = 'name="phone-code"'
VERIFIED_BADGE = "ui-badge--success"


def sent(channel: str) -> list[dict]:
    return [m for m in LocalNotificationGateway().sent() if m["channel"] == channel]


def last_code(channel: str) -> str:
    return sent(channel)[-1]["values"][0]


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def sign_up(client: Client) -> None:
    response = client.post(reverse("account_signup"), data=SIGNUP_DATA)
    assert response["Location"] == VERIFY_PAGE


def confirm(client: Client, channel: str, code: str | None = None):
    field = "email" if channel == "email" else "phone"
    return client.post(
        VERIFY_PAGE,
        {
            "action": f"verify_{field}",
            f"{field}-code": code or last_code(channel),
        },
    )


def screen(client: Client) -> str:
    return client.get(VERIFY_PAGE).content.decode()


def signed_in_user(client: Client) -> User | None:
    user_id = client.session.get("_auth_user_id")
    return User.objects.get(pk=user_id) if user_id else None


class TestSignupVerification:
    """Signup confirms the address and the number on one screen."""

    def test_signup_sends_a_code_to_each(self, client: Client):
        sign_up(client)

        [emailed] = sent("email")
        [texted] = sent("sms")
        [code] = emailed.pop("values")
        assert re.fullmatch(r"\d{6}", code)
        assert emailed == {
            "channel": "email",
            "receiver": EMAIL,
            "template_id": EMAIL_VERIFICATION_CODE.id,
            "subject": "Email verification",
            "content_type": "otp",
        }
        assert texted["receiver"] == "9876543210"
        assert texted["template_id"] == MOBILE_VERIFICATION_CODE.id
        assert texted["content_type"] == "otp"
        assert mail.outbox == []

    def test_the_screen_asks_for_both_codes(self, client: Client):
        sign_up(client)

        page = screen(client)

        assert EMAIL in page
        assert MOBILE_NUMBER in page
        assert EMAIL_BOX in page
        assert PHONE_BOX in page
        assert ENTRANCE_SHELL in page

    def test_one_code_confirms_only_its_own_channel(self, client: Client):
        sign_up(client)

        response = confirm(client, "email")

        assert response["Location"] == VERIFY_PAGE
        assert EmailAddress.objects.get(email=EMAIL).verified
        assert not User.objects.get(email=EMAIL).phone_verified
        assert signed_in_user(client) is None
        page = screen(client)
        assert VERIFIED_BADGE in page
        assert EMAIL_BOX not in page
        assert PHONE_BOX in page

    def test_both_codes_save_the_number_and_sign_in(self, client: Client):
        sign_up(client)
        confirm(client, "email")

        response = confirm(client, "sms")

        assert response.status_code == HTTPStatus.FOUND
        user = signed_in_user(client)
        assert user.phone_number == MOBILE_NUMBER
        assert user.phone_verified

    def test_the_codes_can_be_confirmed_in_either_order(self, client: Client):
        sign_up(client)

        confirm(client, "sms")
        assert signed_in_user(client) is None
        assert User.objects.get(email=EMAIL).phone_verified
        confirm(client, "email")

        assert signed_in_user(client) is not None

    @pytest.mark.parametrize("channel", ["email", "sms"])
    def test_a_wrong_code_is_refused(self, client: Client, channel: str):
        sign_up(client)

        response = confirm(client, channel, wrong(last_code(channel)))

        assert response.status_code == HTTPStatus.OK
        assert not EmailAddress.objects.get(email=EMAIL).verified
        assert not User.objects.get(email=EMAIL).phone_verified
        assert signed_in_user(client) is None

    @pytest.mark.parametrize(
        ("action", "channel"),
        [("email", "email"), ("phone", "sms")],
    )
    def test_a_new_code_can_be_sent_once_the_wait_is_over(
        self,
        client: Client,
        settings,
        action: str,
        channel: str,
    ):
        sign_up(client)
        settings.VERIFICATION_RESEND_AFTER_SECONDS = 0

        client.post(VERIFY_PAGE, {"action": f"resend_{action}"})

        codes = [message["values"][0] for message in sent(channel)]
        assert len(codes) == len({*codes}) == 2  # noqa: PLR2004

    @pytest.mark.parametrize("action", ["email", "phone"])
    def test_a_new_code_cannot_be_asked_for_straight_away(
        self,
        client: Client,
        action: str,
    ):
        sign_up(client)
        page = screen(client)
        assert "You can ask for a new code in" in page
        assert "disabled" in page

        client.post(VERIFY_PAGE, {"action": f"resend_{action}"})

        assert len(sent("email")) == len(sent("sms")) == 1

    def test_the_address_can_be_corrected(self, client: Client):
        sign_up(client)
        user = User.objects.get(email=EMAIL)
        corrected = "arun@clinic.in"
        assert 'name="email_change-email"' in screen(client)

        client.post(
            VERIFY_PAGE,
            {"action": "change_email", "email_change-email": corrected},
        )

        assert sent("email")[-1]["receiver"] == corrected
        confirm(client, "email")
        user.refresh_from_db()
        assert user.email == corrected
        assert EmailAddress.objects.get(email=corrected).verified

    def test_the_number_can_be_corrected(self, client: Client):
        sign_up(client)
        assert 'name="phone_change-phone"' in screen(client)

        client.post(
            VERIFY_PAGE,
            {"action": "change_phone", "phone_change-phone": "9812345678"},
        )

        assert sent("sms")[-1]["receiver"] == "9812345678"
        confirm(client, "sms")
        assert User.objects.get(email=EMAIL).phone_number == "+919812345678"

    def test_an_address_on_another_account_is_refused(self, client: Client):
        taken = UserFactory(email="arun@clinic.in")
        EmailAddress.objects.create(user=taken, email=taken.email, primary=True)
        sign_up(client)
        code = last_code("email")

        response = client.post(
            VERIFY_PAGE,
            {"action": "change_email", "email_change-email": taken.email},
        )

        assert response.status_code == HTTPStatus.OK
        assert "already registered with this email address" in response.content.decode()
        assert len(sent("email")) == 1
        assert mail.outbox == []
        confirm(client, "email", code)
        assert EmailAddress.objects.get(email=EMAIL).verified

    def test_a_number_on_another_account_is_refused(self, client: Client):
        UserFactory(phone_number="+919812345678", phone_verified=True)
        sign_up(client)
        code = last_code("sms")

        response = client.post(
            VERIFY_PAGE,
            {"action": "change_phone", "phone_change-phone": "9812345678"},
        )

        assert response.status_code == HTTPStatus.OK
        assert "already registered with this phone number" in response.content.decode()
        assert len(sent("sms")) == 1
        confirm(client, "sms", code)
        assert User.objects.get(email=EMAIL).phone_number == MOBILE_NUMBER

    def test_a_confirmed_channel_stays_confirmed_while_the_other_is_corrected(
        self,
        client: Client,
    ):
        sign_up(client)
        confirm(client, "email")

        client.post(
            VERIFY_PAGE,
            {"action": "change_phone", "phone_change-phone": "9812345678"},
        )

        page = screen(client)
        assert VERIFIED_BADGE in page
        assert EMAIL_BOX not in page
        assert PHONE_BOX in page
        assert "9812345678" in page

    def test_an_expired_mobile_code_is_replaced_rather_than_waived(
        self,
        client: Client,
        settings,
    ):
        sign_up(client)
        settings.ACCOUNT_PHONE_VERIFICATION_TIMEOUT = 0

        page = screen(client)

        assert "That code has expired." in page
        assert PHONE_BOX not in page
        client.post(VERIFY_PAGE, {"action": "resend_phone"})
        settings.ACCOUNT_PHONE_VERIFICATION_TIMEOUT = 15 * 60
        assert len(sent("sms")) == 2  # noqa: PLR2004
        confirm(client, "sms")
        assert User.objects.get(email=EMAIL).phone_verified

    def test_an_undelivered_code_says_so_and_can_be_sent_again(
        self,
        client: Client,
        settings,
    ):
        local.always_fail(ExternalSystem.NOTIFICATION)
        sign_up(client)

        page = screen(client)

        assert "We could not send your code" in page
        assert "Confirmation email sent" not in page
        local.clear_failures(ExternalSystem.NOTIFICATION)
        settings.VERIFICATION_RESEND_AFTER_SECONDS = 0
        client.post(VERIFY_PAGE, {"action": "resend_email"})
        assert len(sent("email")) == 1

    def test_a_number_claimed_while_the_code_was_out_is_refused(self, client: Client):
        sign_up(client)
        UserFactory(phone_number=MOBILE_NUMBER, phone_verified=True)

        response = confirm(client, "sms")

        assert response.status_code == HTTPStatus.OK
        assert not User.objects.get(email=EMAIL).phone_verified

    def test_signing_up_with_someone_elses_number_is_refused(self, client: Client):
        UserFactory(phone_number=MOBILE_NUMBER, phone_verified=True)

        response = client.post(reverse("account_signup"), data=SIGNUP_DATA)

        assert response.status_code == HTTPStatus.OK
        assert "already registered with this phone number" in response.content.decode()
        assert sent("sms") == []
        assert not User.objects.filter(email=EMAIL).exists()

    def test_an_expired_email_code_is_replaced_rather_than_dropped(
        self,
        client: Client,
        settings,
    ):
        sign_up(client)
        settings.ACCOUNT_EMAIL_VERIFICATION_BY_CODE_TIMEOUT = 0

        page = screen(client)

        assert "That code has expired." in page
        assert EMAIL_BOX not in page
        client.post(VERIFY_PAGE, {"action": "resend_email"})
        settings.ACCOUNT_EMAIL_VERIFICATION_BY_CODE_TIMEOUT = 15 * 60
        assert len(sent("email")) == 2  # noqa: PLR2004
        confirm(client, "email")
        assert EmailAddress.objects.get(email=EMAIL).verified

    def test_the_screen_counts_down_to_the_next_code(self, client: Client):
        sign_up(client)

        page = screen(client)

        assert "data-resend-countdown=" in page
        assert "autofocus" in page

    def test_a_new_code_starts_the_wait_again(self, client: Client, settings):
        sign_up(client)
        settings.VERIFICATION_RESEND_AFTER_SECONDS = 0
        first = client.session[EMAIL_VERIFICATION_CODE_SESSION_KEY]["at"]

        client.post(VERIFY_PAGE, {"action": "resend_email"})

        assert client.session[EMAIL_VERIFICATION_CODE_SESSION_KEY]["at"] > first

    def test_create_account_and_sign_in_stay_reachable_while_verifying(
        self,
        client: Client,
    ):
        sign_up(client)

        assert client.get(reverse("account_signup")).status_code == HTTPStatus.OK
        assert client.get(reverse("account_login")).status_code == HTTPStatus.OK
        assert client.get(VERIFY_PAGE).status_code == HTTPStatus.OK

    def test_the_screen_needs_a_signup_in_progress(self, client: Client):
        response = client.get(VERIFY_PAGE)

        assert response["Location"] == reverse("account_login")

    def test_leaving_and_signing_in_asks_for_the_number_again(self, client: Client):
        sign_up(client)
        confirm(client, "email")

        later = Client()
        response = later.post(
            reverse("account_login"),
            {"login": EMAIL, "password": PASSWORD},
        )

        assert response["Location"] == VERIFY_PAGE
        assert len(sent("sms")) == 2  # noqa: PLR2004
        page = later.get(VERIFY_PAGE).content.decode()
        assert PHONE_BOX in page
        assert EMAIL_BOX not in page
        confirm(later, "sms")
        assert signed_in_user(later).phone_verified

    def test_a_team_account_is_not_asked_to_confirm_its_number(self, client: Client):
        staff = UserFactory(
            password=PASSWORD,
            is_nha_team=True,
            phone_number=MOBILE_NUMBER,
        )
        EmailAddress.objects.create(
            user=staff,
            email=staff.email,
            primary=True,
            verified=True,
        )

        client.post(
            reverse("account_login"),
            {"login": staff.email, "password": PASSWORD},
        )

        assert signed_in_user(client) == staff
        assert sent("sms") == []

    def test_an_unverified_address_at_login_asks_only_for_that_code(
        self,
        client: Client,
    ):
        user = UserFactory(password=PASSWORD)
        EmailAddress.objects.create(user=user, email=user.email, primary=True)

        client.post(
            reverse("account_login"),
            {"login": user.email, "password": PASSWORD},
        )
        page = screen(client)

        assert EMAIL_BOX in page
        assert PHONE_BOX not in page
        assert sent("sms") == []


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

    def test_a_number_on_another_account_is_never_texted(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        UserFactory(phone_number=MOBILE_NUMBER, phone_verified=True)
        client = sign_in(user)

        response = client.post(
            reverse("account_change_phone"),
            {"phone": "98765 43210"},
        )

        assert response.status_code == HTTPStatus.OK
        assert "already registered with this phone number" in response.content.decode()
        assert sent("sms") == []

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
