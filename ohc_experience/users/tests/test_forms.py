"""Form tests for account creation and the user's own details."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.translation import gettext_lazy as _

from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import InvitationFactory
from ohc_experience.users.forms import UserAdminCreationForm
from ohc_experience.users.forms import UserProfileForm
from ohc_experience.users.forms import UserSignupForm

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.test import RequestFactory

    from ohc_experience.users.models import User

SIGNUP_DATA = {
    "name": "Meera Krishnan",
    "email": "meera@sunrise.in",
    "mobile_number": "+91 98765 43210",
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
        settings.TURNSTILE_SITE_KEY = ""
        settings.DEBUG = True
        request = signup_request(rf)
        UserSignupForm(request=request)
        first, second = request.session["signup_challenge"][:2]
        data = {**SIGNUP_DATA, "email": user.email}

        guessed = UserSignupForm(data={**data, "captcha": -1}, request=request)
        assert not guessed.is_valid()
        assert not guessed.has_error("email")

        solved = UserSignupForm(
            data={**data, "captcha": first + second},
            request=request,
        )
        assert not solved.is_valid()
        assert solved.account_exists

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
class TestUserProfileForm:
    def test_saves_the_name(self, user: User):
        form = UserProfileForm({"name": "Meera Krishnan"}, instance=user)

        assert form.is_valid(), form.errors
        assert form.save().name == "Meera Krishnan"


class TestSignupContactDetails:
    """The mockup's signup card asks for a mobile number; it must reach the user."""

    @pytest.mark.django_db
    def test_stores_the_mobile_number(self, rf: RequestFactory):
        form = UserSignupForm(data=SIGNUP_DATA)

        assert form.is_valid(), form.errors
        user = form.save(signup_request(rf))

        assert user.phone_number == SIGNUP_DATA["mobile_number"]

    @pytest.mark.django_db
    def test_mobile_number_is_optional_for_sandbox_signup(self):
        payload = {k: v for k, v in SIGNUP_DATA.items() if k != "mobile_number"}

        form = UserSignupForm(data=payload)

        assert form.is_valid(), form.errors
        assert form.cleaned_data["mobile_number"] == ""
