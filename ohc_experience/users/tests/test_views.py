"""View tests for signup, the post-login hop and the user's own settings."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import InvitationFactory
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.views import INVITATION_SESSION_KEY

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from ohc_experience.organisations.models import Membership as MembershipType
    from ohc_experience.users.models import User

pytestmark = pytest.mark.django_db

SIGNUP_URL = "/accounts/signup/"
SIGNUP_DATA = {
    "name": "Arun Nair",
    "email": "arun@sunrise.in",
    "mobile_number": "+91 98765 43210",
    "password1": "sandbox-Kerala-2026",
    "password2": "sandbox-Kerala-2026",
}


class TestUserRedirectView:
    def test_requires_login(self, client: Client):
        response = client.get(reverse("users:redirect"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"].startswith(reverse("account_login"))

    def test_sends_a_new_vendor_to_onboarding(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        response = sign_in(membership.user).get(reverse("users:redirect"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("organisations:onboarding")

    def test_sends_an_onboarded_vendor_to_the_dashboard(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: MembershipType,
    ):
        response = sign_in(owner_membership.user).get(reverse("users:redirect"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("dashboard")

    def test_sends_a_user_without_an_organisation_home(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        response = sign_in(user).get(reverse("users:redirect"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("home")


class TestUserProfileView:
    def test_requires_login(self, client: Client):
        response = client.get(reverse("users:profile"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"].startswith(reverse("account_login"))

    def test_renders_for_a_signed_in_user(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: MembershipType,
    ):
        response = sign_in(owner_membership.user).get(reverse("users:profile"))

        assert response.status_code == HTTPStatus.OK
        assert response.context["settings_section"] == "profile"

    def test_saves_the_name(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: MembershipType,
    ):
        client = sign_in(owner_membership.user)

        response = client.post(
            reverse("users:profile"),
            data={"name": "Meera K Krishnan"},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("users:profile")
        owner_membership.user.refresh_from_db()
        assert owner_membership.user.name == "Meera K Krishnan"

    def test_an_htmx_save_swaps_the_saved_form_back_in(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: MembershipType,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("users:profile"),
            data={"name": "Meera K Krishnan"},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert '<form id="profile-form"' in html
        assert "<!DOCTYPE html>" not in html
        assert "Meera K Krishnan" in html
        # The flash rides along out of band into the page's #flash-messages.
        assert 'hx-swap-oob="innerHTML"' in html
        assert "Your details were updated." in html
        owner_membership.user.refresh_from_db()
        assert owner_membership.user.name == "Meera K Krishnan"

    def test_an_invalid_htmx_save_swaps_the_errors_in(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: MembershipType,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("users:profile"),
            data={"name": "M" * 300},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        # 200, not 4xx: htmx swaps the fragment and the errors become visible.
        assert response.status_code == HTTPStatus.OK
        assert '<form id="profile-form"' in html
        assert "<!DOCTYPE html>" not in html
        assert "Ensure this value has at most 255 characters" in html
        assert "Your details were updated." not in html
        owner_membership.user.refresh_from_db()
        assert owner_membership.user.name == "Meera Krishnan"

    def test_the_form_posts_on_its_own_without_javascript(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: MembershipType,
    ):
        html = (
            sign_in(owner_membership.user)
            .get(
                reverse("users:profile"),
            )
            .content.decode()
        )

        # The htmx attributes are enhancement: the form has to stay a plain
        # POST to a real URL, with one CSRF token, for a browser without them.
        assert 'method="post"' in html
        assert f'action="{reverse("users:profile")}"' in html
        assert html.count("csrfmiddlewaretoken") == 1

    def test_the_legacy_update_url_redirects_to_the_profile(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        response = sign_in(user).get(reverse("users:update"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("users:profile")


class TestUserDetailView:
    def test_redirects_to_the_profile_page(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        response = sign_in(user).get(reverse("users:detail", kwargs={"pk": user.pk}))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("users:profile")

    def test_requires_login(self, client: Client, user: User):
        response = client.get(reverse("users:detail", kwargs={"pk": user.pk}))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"].startswith(reverse("account_login"))


class TestUserSignupView:
    def test_renders_the_account_card(self, client: Client):
        response = client.get(SIGNUP_URL)

        assert response.status_code == HTTPStatus.OK
        assert "organisation" in response.context["form"].fields

    def test_an_invite_in_the_session_shapes_the_form(
        self,
        client: Client,
        organisation: Organisation,
    ):
        invitation = InvitationFactory.create(
            organisation=organisation,
            email=SIGNUP_DATA["email"],
        )
        session = client.session
        session[INVITATION_SESSION_KEY] = invitation.token
        session.save()

        response = client.get(SIGNUP_URL)

        assert response.status_code == HTTPStatus.OK
        assert "organisation" not in response.context["form"].fields
        assert response.context["invitation"] == invitation

    def test_a_stale_token_is_dropped_from_the_session(
        self,
        client: Client,
        organisation: Organisation,
    ):
        invitation = InvitationFactory.create(
            organisation=organisation,
            email=SIGNUP_DATA["email"],
            expired=True,
        )
        session = client.session
        session[INVITATION_SESSION_KEY] = invitation.token
        session.save()

        response = client.get(SIGNUP_URL)

        assert response.status_code == HTTPStatus.OK
        assert "organisation" in response.context["form"].fields
        assert INVITATION_SESSION_KEY not in client.session

    def test_signing_up_creates_the_vendor_account(self, client: Client):
        response = client.post(
            SIGNUP_URL,
            data={**SIGNUP_DATA, "organisation": "Sunrise Health Systems"},
        )

        assert response.status_code == HTTPStatus.FOUND
        membership = Membership.objects.get(user__email=SIGNUP_DATA["email"])
        assert membership.role == Role.OWNER
        assert membership.organisation.name == "Sunrise Health Systems"

    def test_signing_up_from_an_invite_joins_that_organisation(
        self,
        client: Client,
        organisation: Organisation,
    ):
        invitation = InvitationFactory.create(
            organisation=organisation,
            email=SIGNUP_DATA["email"],
            role=Role.SUPPORT,
        )
        session = client.session
        session[INVITATION_SESSION_KEY] = invitation.token
        session.save()

        response = client.post(SIGNUP_URL, data=SIGNUP_DATA)

        assert response.status_code == HTTPStatus.FOUND
        membership = Membership.objects.get(user__email=SIGNUP_DATA["email"])
        assert membership.organisation == organisation
        assert membership.role == Role.SUPPORT
        assert Organisation.objects.count() == 1
        assert INVITATION_SESSION_KEY not in client.session
