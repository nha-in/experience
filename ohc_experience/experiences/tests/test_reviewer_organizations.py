# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.tests.test_workflow import phr_workspace
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

VERIFICATION_DOCUMENT_NUMBER = organisation_data()["verification_document_number"]


def register_locker(organization, owner, name):
    workspace, form = services.register_product(
        organization,
        owner,
        data={
            **product_data(name),
            "solution_type": ["health_locker"],
            "applied_milestones": ["PHR:p1", "PHR:p2", "PHR:p3", "PHR:p4"],
        },
    )
    assert workspace, form.errors
    return workspace


def test_reviewer_navigation_and_organization_detail(environment, client):
    reviewer = environment["reviewer"]
    organization = environment["org"]
    workspace = environment["workspace"]
    verification = organization.review_items.get(kind="organisation_verification")

    client.force_login(reviewer)
    index = client.get(reverse("experiences:organizations"))

    assert index.status_code == HTTPStatus.OK
    assert b'id="nav-organizations"' in index.content
    assert b'id="nav-products"' not in index.content
    assert organization.display_name.encode() in index.content
    assert (
        reverse(
            "experiences:organization-detail",
            args=[organization.slug],
        ).encode()
        in index.content
    )
    assert f'href="{reverse("experiences:products")}"'.encode() in index.content

    detail = client.get(
        reverse("experiences:organization-detail", args=[organization.slug]),
    )
    assert detail.status_code == HTTPStatus.OK
    assert detail.context["organization"] == organization
    assert list(detail.context["products"]) == [workspace]
    assert verification in detail.context["review_requests"]
    assert VERIFICATION_DOCUMENT_NUMBER.encode() in detail.content
    staff_url = reverse("experiences:product-detail", args=[workspace.reference])
    assert f'href="{staff_url}"'.encode() in detail.content
    assert f'href="{workspace.get_absolute_url()}"'.encode() not in detail.content


def test_organization_pages_follow_category_review_scope(environment, client):
    visible_item = submit(environment, "p1")
    # P1 sits on the organisation's PHR product, not on its ABDM one.
    workspace = phr_workspace(environment)
    reviewer = UserFactory(is_nha_team=True, is_staff=True)
    AccessGrant.objects.create(
        user=reviewer,
        program="abdm",
        area="review",
        category="PHR",
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
    assert list(detail.context["products"]) == [workspace]
    assert list(detail.context["review_requests"]) == [visible_item]
    assert VERIFICATION_DOCUMENT_NUMBER.encode() not in detail.content

    products = client.get(reverse("experiences:products"))
    assert list(products.context["workspaces"]) == [workspace]
    assert list(products.context["products"]) == [workspace]
    assert (
        client.get(
            reverse(
                "experiences:organization-detail",
                args=[hidden_organization.slug],
            ),
        ).status_code
        == HTTPStatus.NOT_FOUND
    )


def test_staff_see_products_as_a_tab_of_organizations(environment, client):
    workspace = environment["workspace"]
    client.force_login(environment["admin"])

    response = client.get(reverse("experiences:products"))
    assert response.status_code == HTTPStatus.OK
    assert "experiences/reviewer_products.html" in [t.name for t in response.templates]
    assert response.context["nav"] == "organizations"
    assert b'id="nav-products"' not in response.content
    assert f'href="{reverse("experiences:organizations")}"'.encode() in response.content
    staff_url = reverse("experiences:product-detail", args=[workspace.reference])
    assert f'href="{staff_url}"'.encode() in response.content

    client.force_login(environment["applicant"])
    response = client.get(reverse("experiences:products"))
    assert response.status_code == HTTPStatus.OK
    assert "experiences/products.html" in [t.name for t in response.templates]
    assert list(response.context["products"]) == [workspace]


def test_open_request_counts_leave_out_drafts(environment, client):
    submitted = submit(environment, "m1")
    organization = environment["org"]
    workspace = environment["workspace"]
    quiet = register_locker(organization, environment["applicant"], "Aarogya Locker")
    assert organization.review_items.filter(status="draft").exists()
    client.force_login(environment["admin"])

    index = client.get(reverse("experiences:organizations"))
    [row] = index.context["organizations"]
    assert (row.visible_product_count, row.open_count) == (2, 1)

    products = client.get(reverse("experiences:products")).context["products"]
    assert [(product, product.open_count) for product in products] == [
        (quiet, 0),
        (workspace, 1),
    ]

    detail = client.get(
        reverse("experiences:organization-detail", args=[organization.slug]),
    )
    # The organization's own page leads with the products that need a decision.
    assert [
        (product, product.open_count) for product in detail.context["products"]
    ] == [
        (workspace, 1),
        (quiet, 0),
    ]
    requests = list(detail.context["review_requests"])
    assert submitted in requests
    assert "draft" not in {item.status for item in requests}


@pytest.fixture
def catalogue(environment):
    """An HMIS and a locker from the test organization, a locker from another one,
    and a UHI reviewer who can see only the HMIS."""
    other = OrganisationFactory(
        onboarded=True,
        verification_status="rejected",
        entity_type="sole_proprietor",
        state="JAMMU AND KASHMIR",
    )
    owner = UserFactory()
    Membership.objects.create(organisation=other, user=owner, role="owner")
    uhi_reviewer = UserFactory(is_nha_team=True, is_staff=True)
    AccessGrant.objects.create(
        user=uhi_reviewer,
        program="abdm",
        area="review",
        category="UHI",
    )
    return {
        "admin": environment["admin"],
        "organization": environment["org"],
        "hmis": environment["workspace"],
        "locker": register_locker(
            environment["org"],
            environment["applicant"],
            "Medibase Health Locker",
        ),
        "other": other,
        "elsewhere": register_locker(other, owner, "Clinic Locker"),
        "uhi_reviewer": uhi_reviewer,
    }


def listed(client, user, route="products", **params):
    client.force_login(user)
    response = client.get(reverse(f"experiences:{route}"), params)
    return set(response.context[route]), response.context


def test_organizations_tab_filters_within_scope(catalogue, client):
    admin, organization, other = (
        catalogue["admin"],
        catalogue["organization"],
        catalogue["other"],
    )

    def organizations(user, **params):
        return listed(client, user, "organizations", **params)

    assert organizations(admin)[0] == {organization, other}
    assert organizations(admin, status="verified")[0] == {organization}
    assert organizations(admin, status="rejected")[0] == {other}
    assert organizations(admin, entity_type="sole_proprietor")[0] == {other}
    assert organizations(admin, state="JAMMU AND KASHMIR")[0] == {other}
    assert (
        organizations(admin, status="verified", state="JAMMU AND KASHMIR")[0] == set()
    )

    rows, context = organizations(admin, status="unknown")
    assert rows == {organization, other}
    assert context["selected_status"] == ""
    assert not context["filtered"]
    # Only values some organization holds, with LGD's capitals made readable.
    assert context["entity_type_choices"] == [
        ("private_company", "Private company"),
        ("sole_proprietor", "Individual/sole proprietorship"),
    ]
    assert ("JAMMU AND KASHMIR", "Jammu and Kashmir") in context["state_choices"]

    # Values held only by organizations outside the reviewer's scope are not offered.
    rows, context = organizations(catalogue["uhi_reviewer"], state="JAMMU AND KASHMIR")
    assert rows == {organization}
    assert context["selected_state"] == ""
    assert context["entity_type_choices"] == [("private_company", "Private company")]
    assert "JAMMU AND KASHMIR" not in dict(context["state_choices"])


def test_products_tab_filters_by_organization_within_scope(catalogue, client):
    admin, organization, other = (
        catalogue["admin"],
        catalogue["organization"],
        catalogue["other"],
    )
    hmis, locker, elsewhere = (
        catalogue["hmis"],
        catalogue["locker"],
        catalogue["elsewhere"],
    )
    assert listed(client, admin)[0] == {hmis, locker, elsewhere}
    assert listed(client, admin, organization=organization.slug)[0] == {hmis, locker}
    assert listed(client, admin, organization=other.slug)[0] == {elsewhere}
    assert listed(client, admin, organization=other.slug, q="hmis")[0] == set()
    assert listed(client, admin, q=locker.reference.lower())[0] == {locker}

    # An organization outside the reviewer's scope is not a filter they can apply.
    uhi_reviewer = catalogue["uhi_reviewer"]
    products, context = listed(client, uhi_reviewer, organization=other.slug)
    assert products == {hmis}
    assert context["selected_organization"] == ""
    assert list(context["organization_choices"]) == [organization]
    assert listed(client, uhi_reviewer, q="locker")[0] == set()


def test_products_tab_filters_by_solution_type_within_scope(catalogue, client):
    admin, other = catalogue["admin"], catalogue["other"]
    hmis, locker, elsewhere = (
        catalogue["hmis"],
        catalogue["locker"],
        catalogue["elsewhere"],
    )

    products, context = listed(client, admin, solution_type="health_locker")
    assert products == {locker, elsewhere}
    assert context["selected_solution_type"] == "health_locker"
    # Only the types some product applied for, in the catalogue's order.
    assert context["solution_type_choices"] == [
        ("clinical_hmis", "Clinic HMIS"),
        ("health_locker", "Health Locker"),
    ]
    assert listed(client, admin, solution_type="clinical_hmis")[0] == {hmis}
    page = client.get(reverse("experiences:products"))
    # Each row names the solution types the filter matches on.
    assert f"{hmis.reference}</span> · Clinic HMIS ·".encode() in page.content
    assert listed(
        client,
        admin,
        solution_type="health_locker",
        organization=other.slug,
    )[0] == {elsewhere}
    # A type no visible product applied for is not a filter at all.
    products, context = listed(client, admin, solution_type="pharmacy")
    assert products == {hmis, locker, elsewhere}
    assert context["selected_solution_type"] == ""

    products, context = listed(
        client,
        catalogue["uhi_reviewer"],
        solution_type="health_locker",
    )
    assert products == {hmis}
    assert context["selected_solution_type"] == ""
    assert context["solution_type_choices"] == [("clinical_hmis", "Clinic HMIS")]


def test_an_unsent_verification_does_not_list_its_organization(environment, client):
    """Opening the organisation form starts a draft, the integrator's unsent work.

    A withdrawn verification is a draft again too, but it was sent, and stays.
    """

    def open_organisation_form(organization):
        owner = UserFactory()
        Membership.objects.create(organisation=organization, user=owner, role="owner")
        return services.organisation_review(organization, owner), owner

    unsent = OrganisationFactory(name="Unsent Trust", entity_type="trust")
    withdrawn = OrganisationFactory(name="Withdrawn Labs")
    open_organisation_form(unsent)
    verification, owner = open_organisation_form(withdrawn)
    verification, form, saved = services.save_review_form(
        verification,
        owner,
        data=organisation_data("Withdrawn Labs"),
        files={"supporting_document": pdf()},
        submit=True,
    )
    assert saved, form.errors
    services.withdraw(verification, owner)
    withdrawn.refresh_from_db()
    assert withdrawn.verification_status == "withdrawn"

    rows, context = listed(client, environment["admin"], "organizations")
    assert withdrawn in rows
    assert unsent not in rows
    assert "trust" not in dict(context["entity_type_choices"])
    for organization, status in (
        (unsent, HTTPStatus.NOT_FOUND),
        (withdrawn, HTTPStatus.OK),
    ):
        response = client.get(
            reverse("experiences:organization-detail", args=[organization.slug]),
        )
        assert response.status_code == status


def test_pages_never_repeat_or_skip_rows_that_share_a_name(environment, client):
    for _index in range(23):
        organization = OrganisationFactory(name="Care Plus", onboarded=True)
        owner = UserFactory()
        Membership.objects.create(organisation=organization, user=owner, role="owner")
        register_locker(organization, owner, "Twin Locker")
    client.force_login(environment["admin"])
    for route in ("organizations", "products"):
        url = reverse(f"experiences:{route}")
        first = client.get(url).context[route]
        rows = [
            row.pk
            for number in first.paginator.page_range
            for row in client.get(url, {"page": number}).context[route]
        ]
        assert len(rows) == len(set(rows)) == first.paginator.count


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
