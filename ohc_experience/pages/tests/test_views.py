"""Landing page and dashboard."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.core.cache import cache
from django.urls import reverse

from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import Milestone
from ohc_experience.experiences.models import Product
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.pages.abdm_dashboard import CACHE_KEY
from ohc_experience.pages.views import indian_grouping
from ohc_experience.pages.views import resolve_post_login_destination
from ohc_experience.pages.views import successful_integrator_count
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

    def test_a_local_server_puts_its_port_first_in_the_title(
        self,
        client: Client,
        settings,
    ):
        settings.DEBUG = True
        settings.INTERNAL_IPS = ["127.0.0.1"]

        html = client.get(reverse("home"), SERVER_PORT="8002").content.decode()
        title = html.split("<title>", 1)[1].split("</title>", 1)[0]

        assert title.split()[:2] == [":8002", "·"]

    def test_a_deployed_server_leaves_the_port_out_of_the_title(
        self,
        client: Client,
    ):
        html = client.get(reverse("home"), SERVER_PORT="8002").content.decode()

        assert ":8002" not in html.split("</title>", 1)[0]

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


def milestone_for(organisation, key="m1", *, status="approved", enabled=True):
    owner = UserFactory.create()
    product, _ = Product.objects.get_or_create(
        organisation=organisation,
        slug="product",
        defaults={
            "name": "Product",
            "description": "A product",
            "experience_type": "abdm",
            "created_by": owner,
        },
    )
    application = ApplicationInstance.objects.create(
        reference=f"{organisation.slug}-{key}",
        application_type="abdm_sandbox_exit",
        title=key.upper(),
        product=product,
        created_by=owner,
        status=status,
    )
    return Milestone.objects.create(
        product=product,
        key=key,
        application=application,
        enabled=enabled,
    )


class TestLandingFigures:
    @pytest.fixture(autouse=True)
    def empty_cache(self):
        cache.clear()
        yield
        cache.clear()

    def test_shows_the_cached_abdm_figures_grouped_as_in_india(self, client):
        cache.set(
            CACHE_KEY,
            {
                "records_linked": 1224714978,
                "professionals_registered": 1220798,
                "facilities_registered": 585761,
            },
        )

        response = client.get(reverse("home"))
        html = response.content.decode()

        assert response.context["abdm_figures"] == {
            "records_linked": "1,22,47,14,978",
            "professionals_registered": "12,20,798",
            "facilities_registered": "5,85,761",
        }
        assert '<span class="sr-only">1,22,47,14,978</span>' in html
        assert "ABHA Linked Health Record Created" in html

    def test_leaves_the_abdm_figures_out_until_they_are_fetched(self, client):
        # The test settings carry no credentials, so the first fetch fails.
        response = client.get(reverse("home"))
        html = response.content.decode()

        assert response.context["abdm_figures"] == {}
        assert "ABHA Linked Health Record Created" not in html
        assert "Successful Integrators" in html

    def test_counts_successful_integrators_from_the_database(self, client):
        milestone_for(OrganisationFactory.create(), "m1")

        response = client.get(reverse("home"))

        assert response.context["successful_integrators"] == "1"
        assert '<span class="sr-only">1</span>' in response.content.decode()


class TestSuccessfulIntegratorCount:
    def test_counts_each_organisation_with_an_approved_milestone_once(self):
        twice = OrganisationFactory.create()
        milestone_for(twice, "m1")
        milestone_for(twice, "m2")
        milestone_for(OrganisationFactory.create(), "uhi1")

        assert successful_integrator_count() == 2  # noqa: PLR2004

    @pytest.mark.parametrize(
        ("status", "enabled"),
        [
            ("draft", True),
            ("under_review", True),
            ("query_raised", True),
            ("approved", False),
        ],
    )
    def test_leaves_out_an_organisation_without_one(self, status, enabled):
        organisation = OrganisationFactory.create()
        milestone_for(organisation, "m1", status=status, enabled=enabled)
        OrganisationFactory.create()

        assert successful_integrator_count() == 0


@pytest.mark.parametrize(
    ("number", "grouped"),
    [
        (0, "0"),
        (604, "604"),
        (1234, "1,234"),
        (123456, "1,23,456"),
        (1220798, "12,20,798"),
        (1224714978, "1,22,47,14,978"),
    ],
)
def test_indian_grouping(number, grouped):
    assert indian_grouping(number) == grouped


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
