"""Form tests for account creation and the user's own details."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import InvitationFactory
from ohc_experience.users.fields import MobileNumberField
from ohc_experience.users.forms import UserAdminCreationForm
from ohc_experience.users.forms import UserChangePasswordForm
from ohc_experience.users.forms import UserProfileForm
from ohc_experience.users.forms import UserResetPasswordKeyForm
from ohc_experience.users.forms import UserSetPasswordForm
from ohc_experience.users.forms import UserSignupForm
from ohc_experience.users.stages import PENDING_MOBILE_NUMBER_SESSION_KEY

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.test import RequestFactory

    from ohc_experience.users.models import User

SIGNUP_DATA = {
    "name": "Meera Krishnan",
    "email": "meera@sunrise.in",
    "mobile_number": "9876543210",
    "organisation": "Sunrise Health Systems",
    "organisation_type": "private_company",
    "website": "https://sunrise.in",
    "password1": "sandbox-Kerala-2026",
    "password2": "sandbox-Kerala-2026",
}


def signup_request(rf: RequestFactory) -> HttpRequest:
    """A request complete enough for allauth's `form.save(request)`."""
    request = rf.post("/accounts/signup/")
    SessionMiddleware(lambda _request: None).process_request(request)
    request.session.save()
    MessageMiddleware(lambda _request: None).process_request(request)
    request.user = AnonymousUser()
    return request


class TestUserAdminCreationForm:
    def test_rejects_an_email_that_is_already_taken(self, user: User):
        form = UserAdminCreationForm(
            {
                "email": user.email,
                "password1": user.password,
                "password2": user.password,
            },
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert form.errors["email"][0] == _("This email has already been taken.")


@pytest.mark.django_db
class TestUserSignupForm:
    def test_the_confirmation_must_match_the_password(self):
        form = UserSignupForm(data={**SIGNUP_DATA, "password2": "sandbox-Kerala-2025"})

        assert not form.is_valid()
        assert "password2" in form.errors

    def test_a_mistyped_confirmation_is_reported_with_a_rejected_password(self):
        form = UserSignupForm(
            data={**SIGNUP_DATA, "password1": "sandbox", "password2": "sandbox-2"},
        )

        assert not form.is_valid()
        assert "password1" in form.errors
        assert form.errors["password2"] == [
            "You must type the same password each time.",
        ]

    def test_creates_the_organisation_and_an_owner_membership(
        self,
        rf: RequestFactory,
    ):
        form = UserSignupForm(data=SIGNUP_DATA)

        assert form.is_valid(), form.errors
        user = form.save(signup_request(rf))

        assert user.name == "Meera Krishnan"
        organisation = Organisation.objects.get(name="Sunrise Health Systems")
        membership = Membership.objects.get(user=user)
        assert membership.organisation == organisation
        assert membership.role == Role.OWNER
        assert organisation.website == "https://sunrise.in"

    def test_rejects_a_blank_organisation(self):
        form = UserSignupForm(data={**SIGNUP_DATA, "organisation": "   "})

        assert not form.is_valid()
        assert form.errors["organisation"] == [
            "Enter your organisation or business name.",
        ]

    def test_requires_the_type_of_entity(self):
        form = UserSignupForm(data={**SIGNUP_DATA, "organisation_type": ""})

        assert not form.is_valid()
        assert "organisation_type" in form.errors

    def test_an_existing_email_is_only_revealed_once_the_captcha_passes(
        self,
        rf: RequestFactory,
        settings,
        user: User,
    ):
        settings.SANDBOX_SIGNUP_CAPTCHA = True
        settings.TURNSTILE_SITE_KEY = "test-site"
        settings.TURNSTILE_SECRET_KEY = "test-secret"  # noqa: S105 - mocked provider key
        request = signup_request(rf)
        data = {**SIGNUP_DATA, "email": user.email, "captcha": "response-token"}

        def turnstile(*, success):
            response = {"success": success, "hostname": "testserver"}
            return patch(
                "ohc_experience.users.captcha.urlopen",
                return_value=io.BytesIO(json.dumps(response).encode()),
            )

        with turnstile(success=False):
            failed = UserSignupForm(data=data, request=request)
            assert not failed.is_valid()
            assert not failed.has_error("email")

        with turnstile(success=True):
            passed = UserSignupForm(data=data, request=request)
            assert not passed.is_valid()
            assert passed.account_exists

    def test_an_invite_drops_the_organisation_field(self, organisation: Organisation):
        invitation = InvitationFactory.create(
            organisation=organisation,
            email=SIGNUP_DATA["email"],
        )
        form = UserSignupForm(invitation=invitation)

        assert "organisation" not in form.fields

    def test_an_invite_rejects_a_mismatched_email(self, organisation: Organisation):
        invitation = InvitationFactory.create(
            organisation=organisation,
            email="someone-else@sunrise.in",
        )
        form = UserSignupForm(data=SIGNUP_DATA, invitation=invitation)

        assert not form.is_valid()
        assert form.errors["email"] == [
            "Sign up with the address the invite was sent to.",
        ]

    def test_an_invite_joins_the_inviting_organisation(
        self,
        rf: RequestFactory,
        organisation: Organisation,
    ):
        invitation = InvitationFactory.create(
            organisation=organisation,
            email=SIGNUP_DATA["email"],
            role=Role.ADMIN,
        )
        form = UserSignupForm(data=SIGNUP_DATA, invitation=invitation)

        assert form.is_valid(), form.errors
        user = form.save(signup_request(rf))

        membership = Membership.objects.get(user=user)
        assert membership.organisation == organisation
        assert membership.role == Role.ADMIN
        assert Organisation.objects.count() == 1
        invitation.refresh_from_db()
        assert invitation.accepted_at is not None

    def test_an_invite_matches_the_email_case_insensitively(
        self,
        organisation: Organisation,
    ):
        invitation = InvitationFactory.create(
            organisation=organisation,
            email=SIGNUP_DATA["email"].upper(),
        )
        form = UserSignupForm(data=SIGNUP_DATA, invitation=invitation)

        assert form.is_valid(), form.errors


@pytest.mark.django_db
@pytest.mark.parametrize(
    "form_class",
    [UserChangePasswordForm, UserSetPasswordForm, UserResetPasswordKeyForm],
)
class TestPasswordConfirmation:
    def test_a_mistyped_confirmation_is_reported_once(self, form_class, user: User):
        form = form_class(
            data={
                "password1": "sandbox-Kerala-2026",
                "password2": "sandbox-Kerala-2025",
            },
            user=user,
        )

        assert not form.is_valid()
        assert form.errors["password2"] == [
            "You must type the same password each time.",
        ]

    def test_a_mistyped_confirmation_is_reported_with_a_rejected_password(
        self,
        form_class,
        user: User,
    ):
        form = form_class(
            data={"password1": "sandbox", "password2": "sandbox-2"},
            user=user,
        )

        assert not form.is_valid()
        assert "password1" in form.errors
        assert form.errors["password2"] == [
            "You must type the same password each time.",
        ]


@pytest.mark.django_db
class TestUserProfileForm:
    def test_saves_the_name(self, user: User):
        form = UserProfileForm({"name": "Meera Krishnan"}, instance=user)

        assert form.is_valid(), form.errors
        assert form.save().name == "Meera Krishnan"


class TestSignupContactDetails:
    """The mockup's signup card asks for a mobile number; it is saved once confirmed."""

    @pytest.mark.django_db
    def test_holds_the_mobile_number_until_a_code_confirms_it(
        self,
        rf: RequestFactory,
    ):
        form = UserSignupForm(data=SIGNUP_DATA)
        request = signup_request(rf)

        assert form.is_valid(), form.errors
        user = form.save(request)

        assert user.phone_number == ""
        assert request.session[PENDING_MOBILE_NUMBER_SESSION_KEY] == {
            "user_id": user.pk,
            "phone": "+919876543210",
        }

    @pytest.mark.parametrize(
        "typed",
        ["9876543210", "+91 98765 43210", "919876543210", "098765-43210"],
    )
    def test_cleans_an_indian_mobile_number(self, typed: str):
        assert MobileNumberField().clean(typed) == "+919876543210"

    @pytest.mark.parametrize(
        "typed",
        ["12345", "5876543210", "+1 415 555 0100", "98765432101"],
    )
    def test_rejects_anything_but_an_indian_mobile_number(self, typed: str):
        with pytest.raises(ValidationError):
            MobileNumberField().clean(typed)

    @pytest.mark.django_db
    def test_requires_a_mobile_number(self):
        payload = {k: v for k, v in SIGNUP_DATA.items() if k != "mobile_number"}

        form = UserSignupForm(data=payload)

        assert not form.is_valid()
        assert form.errors["mobile_number"] == ["Enter your mobile number."]

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "mobile_number",
        ["123456789", "12345678901", "+919876543210", "98765 43210"],
    )
    def test_rejects_an_invalid_mobile_number(self, mobile_number: str):
        form = UserSignupForm(data={**SIGNUP_DATA, "mobile_number": mobile_number})

        assert not form.is_valid()
        assert form.errors["mobile_number"] == [
            "Enter a valid 10-digit phone number without the country code.",
        ]
