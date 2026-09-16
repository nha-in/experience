"""Landing page and dashboard."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.pages.views import resolve_post_login_destination
from ohc_experience.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from ohc_experience.organisations.models import Membership
    from ohc_experience.organisations.models import Organisation
    from ohc_experience.users.models import User

pytestmark = pytest.mark.django_db

# An error page shorter than this is the empty-skeleton bug, not a real page.
RENDERED_ERROR_PAGE_MIN_LENGTH = 1000


class TestLandingView:
    def test_renders_for_an_anonymous_visitor(self, client: Client):
        response = client.get(reverse("home"))

        assert response.status_code == HTTPStatus.OK
        assert "pages/home.html" in [
            template.name for template in response.templates if template.name
        ]

    def test_sends_an_onboarded_member_to_the_dashboard(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).get(reverse("home"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("experiences:home")

    def test_sends_a_half_set_up_member_to_onboarding(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        response = sign_in(membership.user).get(reverse("home"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("experiences:organisation")

    def test_a_signed_in_user_without_an_organisation_still_gets_a_page(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        # Regression: home → users:redirect → home used to loop forever.
        response = sign_in(user).get(reverse("home"), follow=True)

        assert response.status_code == HTTPStatus.OK
        assert response.redirect_chain == []


class TestDashboardView:
    def test_requires_login(self, client):
        response = client.get(reverse("dashboard"))
        assert response.status_code == HTTPStatus.FOUND
        assert response.url.startswith(reverse("account_login"))

    def test_unfinished_organisation_goes_to_current_onboarding(
        self,
        client,
        organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )
        client.force_login(membership.user)
        response = client.get(reverse("dashboard"))
        assert response.url == reverse("experiences:organisation")

    def test_onboarded_organisation_without_products_goes_to_registration(
        self,
        client,
        owner_membership,
    ):
        client.force_login(owner_membership.user)
        response = client.get(reverse("dashboard"))
        assert response.url == reverse("experiences:product-create")


class TestPostLoginDestination:
    def test_no_organisation_lands_home(self, user: User):
        assert resolve_post_login_destination(user) == "home"

    def test_an_unfinished_organisation_lands_on_onboarding(
        self,
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(organisation=organisation)

        assert (
            resolve_post_login_destination(membership.user)
            == "experiences:organisation"
        )

    def test_a_finished_organisation_lands_on_the_dashboard(
        self,
        owner_membership: Membership,
    ):
        assert (
            resolve_post_login_destination(owner_membership.user) == "experiences:home"
        )


class TestOhcStaffLanding:
    """A staff identity alone does not grant access to the reviewer console."""

    @pytest.fixture
    def ohc_user(self, db):
        return UserFactory.create(is_nha_team=True)

    def test_post_login_goes_to_the_console(self, client, ohc_user):
        client.force_login(ohc_user)

        response = client.get(reverse("users:redirect"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("experiences:home")

    def test_the_dashboard_redirects_to_the_console(self, client, ohc_user):
        client.force_login(ohc_user)

        response = client.get(reverse("dashboard"))

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_an_integrator_with_no_organisation_still_gets_403(self, client, user):
        client.force_login(user)

        assert client.get(reverse("dashboard")).status_code == HTTPStatus.FORBIDDEN


class TestErrorPages:
    """The error templates render real content, not an empty shell."""

    def test_the_403_page_says_what_happened(self, client, user, settings):
        settings.DEBUG = False
        client.force_login(user)

        response = client.get(reverse("dashboard"))
        html = response.content.decode()

        assert response.status_code == HTTPStatus.FORBIDDEN
        assert "403" in html
        assert "do not have access" in html
        # The bug this pins: the old template filled a block base.html no longer
        # defines, so the page rendered as an empty skeleton.
        assert len(html) > RENDERED_ERROR_PAGE_MIN_LENGTH
