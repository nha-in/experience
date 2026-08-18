"""Landing page and dashboard."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse

from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import InvitationFactory
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.pages.views import resolve_post_login_destination

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from ohc_experience.organisations.models import Membership
    from ohc_experience.organisations.models import Organisation
    from ohc_experience.users.models import User

pytestmark = pytest.mark.django_db


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
        assert response["Location"] == reverse("dashboard")

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
        assert response["Location"] == reverse("organisations:onboarding")

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
    def test_requires_login(self, client: Client):
        response = client.get(reverse("dashboard"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"].startswith(reverse("account_login"))

    def test_redirects_until_the_company_profile_is_done(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        response = sign_in(membership.user).get(reverse("dashboard"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("organisations:onboarding")

    def test_renders_once_the_organisation_is_onboarded(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).get(reverse("dashboard"))

        assert response.status_code == HTTPStatus.OK
        assert response.context["nav_section"] == "dashboard"
        assert response.context["organisation"] == owner_membership.organisation
        assert response.context["team_size"] == 1
        assert response.context["pending_invites"] == 0

    def test_the_setup_checklist_reports_honest_progress(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).get(reverse("dashboard"))

        steps = response.context["setup_steps"]
        assert [step["done"] for step in steps] == [True, False, False, False]
        progress = (
            response.context["setup_done"],
            response.context["setup_total"],
            response.context["setup_percent"],
        )
        assert progress == (1, 4, 25)

    def test_inviting_a_teammate_ticks_the_team_step(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        InvitationFactory.create(organisation=owner_membership.organisation)

        response = sign_in(owner_membership.user).get(reverse("dashboard"))

        progress = (
            response.context["pending_invites"],
            response.context["setup_done"],
            response.context["setup_percent"],
        )
        assert progress == (1, 2, 50)

    def test_a_user_without_an_organisation_is_refused(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        response = sign_in(user).get(reverse("dashboard"))

        assert response.status_code == HTTPStatus.FORBIDDEN


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
            == "organisations:onboarding"
        )

    def test_a_finished_organisation_lands_on_the_dashboard(
        self,
        owner_membership: Membership,
    ):
        assert resolve_post_login_destination(owner_membership.user) == "dashboard"
