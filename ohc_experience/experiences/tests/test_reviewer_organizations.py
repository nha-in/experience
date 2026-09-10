# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_reviewer_navigation_and_organization_detail(environment, client):
    reviewer = environment["reviewer"]
    organization = environment["org"]
    workspace = environment["workspace"]
    verification = organization.review_items.get(kind="organisation_verification")

    client.force_login(reviewer)
    index = client.get(reverse("experiences:organizations"))

    assert index.status_code == HTTPStatus.OK
    assert b'id="nav-organizations"' in index.content
    assert organization.display_name.encode() in index.content
    assert reverse(
        "experiences:organization-detail",
        args=[organization.slug],
    ).encode() in index.content

    detail = client.get(
        reverse("experiences:organization-detail", args=[organization.slug]),
    )
    assert detail.status_code == HTTPStatus.OK
    assert detail.context["organization"] == organization
    assert list(detail.context["products"]) == [workspace]
    assert verification in detail.context["review_requests"]
    assert b"DEMO-CIN-2026" in detail.content
    assert workspace.get_absolute_url().encode() in detail.content


def test_organization_pages_follow_category_review_scope(environment, client):
    visible_item = submit(environment, "locker1")
    reviewer = UserFactory(is_nha_team=True, is_staff=True)
    AccessGrant.objects.create(
        user=reviewer,
        program="abdm",
        area="review",
        category="HealthLocker",
    )
    hidden_organization = OrganisationFactory(onboarded=True)
    client.force_login(reviewer)

    index = client.get(reverse("experiences:organizations"))
    assert list(index.context["organizations"]) == [environment["org"]]
    assert hidden_organization.display_name.encode() not in index.content

    detail = client.get(
        reverse(
            "experiences:organization-detail",
            args=[environment["org"].slug],
        ),
    )
    assert list(detail.context["products"]) == [environment["workspace"]]
    assert list(detail.context["review_requests"]) == [visible_item]
    assert b"DEMO-CIN-2026" not in detail.content

    products = client.get(reverse("experiences:products"))
    assert list(products.context["workspaces"]) == [environment["workspace"]]
    assert client.get(
        reverse(
            "experiences:organization-detail",
            args=[hidden_organization.slug],
        ),
    ).status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("route", ["organizations", "organization-detail"])
def test_integrators_cannot_access_reviewer_organizations(
    environment,
    client,
    route,
):
    client.force_login(environment["applicant"])
    args = [environment["org"].slug] if route == "organization-detail" else []

    assert client.get(reverse(f"experiences:{route}", args=args)).status_code == (
        HTTPStatus.FORBIDDEN
    )
