"""Landing page and dashboard."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone

from ohc_experience.events.tests.factories import EventFactory
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import InvitationFactory
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


class TestDashboardUpcomingEvents:
    """The card on the dashboard that previews what is coming up."""

    def test_lists_the_next_three_published_events(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        soonest = [
            EventFactory.create(
                published=True,
                title=f"Office hours {day}",
                starts_at=timezone.now() + timedelta(days=day),
            )
            for day in (1, 2, 3)
        ]
        fourth = EventFactory.create(
            published=True,
            title="Office hours 4",
            starts_at=timezone.now() + timedelta(days=4),
        )

        response = sign_in(owner_membership.user).get(reverse("dashboard"))

        assert list(response.context["upcoming_events"]) == soonest
        assert fourth.title not in response.content.decode()

    def test_each_row_links_to_the_event_and_the_card_to_the_list(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        event = EventFactory.create(published=True, title="Care v3.3 upgrade webinar")

        response = sign_in(owner_membership.user).get(reverse("dashboard"))

        body = response.content.decode()
        assert event.get_absolute_url() in body
        assert reverse("events:list") in body
        assert "Care v3.3 upgrade webinar" in body

    def test_drafts_and_finished_events_stay_out(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        EventFactory.create(title="Draft roadmap AMA")
        EventFactory.create(published=True, past=True, title="Certification AMA")

        response = sign_in(owner_membership.user).get(reverse("dashboard"))

        assert list(response.context["upcoming_events"]) == []
        body = response.content.decode()
        assert "Draft roadmap AMA" not in body
        assert "Certification AMA" not in body

    def test_keeps_the_empty_state_when_there_is_nothing(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).get(reverse("dashboard"))

        assert list(response.context["upcoming_events"]) == []
        assert "Nothing scheduled yet" in response.content.decode()


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


class TestOhcStaffLanding:
    """An OHC member without a vendor account must never hit a bare 403."""

    @pytest.fixture
    def ohc_user(self, db):
        return UserFactory.create(is_ohc_team=True)

    def test_post_login_goes_to_the_console(self, client, ohc_user):
        client.force_login(ohc_user)

        response = client.get(reverse("users:redirect"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("ohc:queue")

    def test_the_dashboard_redirects_to_the_console(self, client, ohc_user):
        client.force_login(ohc_user)

        response = client.get(reverse("dashboard"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("ohc:queue")

    def test_a_vendor_with_no_organisation_still_gets_403(self, client, user):
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
