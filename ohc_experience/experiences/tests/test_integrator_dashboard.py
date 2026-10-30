# ruff: noqa: F811
from datetime import timedelta
from http import HTTPStatus
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.tests.test_workflow import phr_product
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.presentation import PRODUCT_STATUS_KEY
from ohc_experience.experiences.presentation import product_statuses
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def dashboard(client, user):
    client.force_login(user)
    return client.get(reverse("dashboard"))


def raise_query(environment, key="m1"):
    item = milestone(environment, key)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(
        item,
        environment["reviewer"],
        action="query",
        note="Which cases cover consent expiry?",
        field_key="functional_report",
    )
    return item


def test_an_integrator_lands_on_their_products(environment, client):
    OrganisationFactory.create()
    response = dashboard(client, environment["applicant"])

    assert response.status_code == HTTPStatus.OK
    assert "experiences/dashboard.html" in [t.name for t in response.templates]
    assert list(response.context["product_rows"]) == [environment["product"]]
    assert response.context["nav"] == "dashboard"
    assert environment["product"].get_absolute_url() in response.text
    assert environment["org"].display_name in response.text


def test_no_product_is_selected_on_the_dashboard(environment, client):
    client.force_login(environment["applicant"])
    client.get(environment["product"].get_absolute_url())

    response = client.get(reverse("dashboard"))
    sidebar = response.text[response.text.index('id="app-nav"') :]

    assert response.context["selected_product"] is None
    assert "Pick a product" in sidebar
    assert environment["org"].display_name in sidebar
    for link in ("overview", "credentials", "certification", "edit"):
        assert f'id="nav-{link}"' not in sidebar
    for link in ("dashboard", "queries", "events", "support", "register"):
        assert f'id="nav-{link}"' in sidebar


def test_pending_queries_cover_the_organisation_from_the_dashboard(environment, client):
    submit(environment, "m1")
    item = raise_query(environment)
    client.force_login(environment["applicant"])
    # Having viewed a product with no query must not hide the one that has.
    client.get(phr_product(environment).get_absolute_url())

    home = client.get(reverse("dashboard"))
    queries = client.get(reverse("experiences:pending-queries"))

    assert home.context["waiting_count"] == home.context["query_count"] == 1
    assert list(queries.context["items"]) == [item]
    assert queries.context["selected_product"] is None
    assert environment["product"].name in queries.text


def test_pending_queries_narrow_to_the_product_named(environment, client):
    submit(environment, "m1")
    item = raise_query(environment)
    other = phr_product(environment)
    client.force_login(environment["applicant"])

    def queries(product):
        url = reverse("experiences:product-queries", args=[product.reference])
        return client.get(url), url

    assert not queries(other)[0].context["items"]
    response, url = queries(environment["product"])

    assert list(response.context["items"]) == [item]
    assert response.context["selected_product"] == environment["product"]
    assert f'href="{url}"' in response.text


@pytest.mark.parametrize(
    "route",
    [
        "experiences:product-queries",
        "experiences:product-support",
        "experiences:product-events",
    ],
)
def test_a_product_of_another_organisation_is_not_found(environment, client, route):
    other = OrganisationFactory.create()
    Membership.objects.create(
        organisation=other,
        user=environment["outsider"],
        role=Role.OWNER,
    )
    client.force_login(environment["outsider"])

    response = client.get(reverse(route, args=[environment["product"].reference]))

    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    "route",
    [
        "dashboard",
        "experiences:pending-queries",
        "experiences:support",
        "experiences:events",
        "experiences:organisation",
        "experiences:product-create",
        "organisations:team",
        "users:profile",
    ],
)
def test_dashboard_level_pages_offer_pick_a_product(environment, client, route):
    client.force_login(environment["applicant"])
    client.get(environment["product"].get_absolute_url())

    response = client.get(reverse(route))

    assert response.context["selected_product"] is None
    assert "Pick a product" in response.text


def test_session_pages_follow_the_version_they_were_opened_from(environment, client):
    event = Event.objects.create(
        title="Gateway clinic",
        slug="gateway-clinic",
        starts_at=timezone.now() + timedelta(days=3),
        published_at=timezone.now(),
        created_by=environment["admin"],
    )
    product = environment["product"]
    client.force_login(environment["applicant"])

    everyone = client.get(reverse("experiences:events"))
    assert event.get_absolute_url() in everyone.text
    assert client.get(event.get_absolute_url()).context["selected_product"] is None

    inside = client.get(
        reverse("experiences:product-events", args=[product.reference]),
    )
    url = reverse("experiences:product-event", args=[product.reference, event.pk])
    assert url in inside.text
    detail = client.get(url)
    assert detail.context["selected_product"] == product
    assert (
        f'href="{reverse("experiences:product-events", args=[product.reference])}"'
        in detail.text
    )

    registered = client.post(url)
    assert registered.url == url


def raise_ticket(environment, product, subject):
    return Ticket.objects.create(
        organisation=environment["org"],
        product=product,
        subject=subject,
        created_by=environment["applicant"],
        category="phr-app-p2",
    )


def test_support_lists_the_organisations_tickets_unless_a_product_is_named(
    environment,
    client,
):
    other = phr_product(environment)
    first = raise_ticket(environment, environment["product"], "Callback rejected")
    second = raise_ticket(environment, other, "Consent stuck")
    client.force_login(environment["applicant"])
    url = reverse("experiences:support")

    everything = client.get(url, {"status": ""})
    assert set(everything.context["tickets"]) == {first, second}
    assert everything.context["selected_product"] is None
    assert other.name in everything.text

    narrowed = client.get(
        reverse("experiences:product-support", args=[other.reference]),
        {"status": ""},
    )
    assert list(narrowed.context["tickets"]) == [second]
    assert narrowed.context["selected_product"] == other


def test_a_new_ticket_asks_which_product_when_there_are_several(environment, client):
    other = phr_product(environment)
    client.force_login(environment["applicant"])
    url = reverse("experiences:support")

    picking = client.get(url, {"new": "1"})
    assert 'name="product"' in picking.text
    assert 'name="subject"' not in picking.text
    assert other.reference in picking.text
    assert environment["product"].reference in picking.text

    picked = client.get(url, {"new": "1", "product": other.reference})
    product_url = reverse("experiences:product-support", args=[other.reference])
    assert picked.url == f"{product_url}?new=1"
    writing = client.get(picked.url)
    assert 'name="subject"' in writing.text
    assert writing.context["selected_product"] == other


def test_a_ticket_form_left_open_on_the_old_address_still_sends(environment, client):
    other = phr_product(environment)
    client.force_login(environment["applicant"])

    response = client.post(
        reverse("experiences:support") + f"?new=1&product={other.reference}",
        {"subject": "Sent before the move"},
    )

    assert response.status_code == HTTPStatus.TEMPORARY_REDIRECT
    assert response.url == (
        reverse("experiences:product-support", args=[other.reference]) + "?new=1"
    )


def test_a_bad_product_in_a_link_opens_the_organisations_support(environment, client):
    client.force_login(environment["applicant"])

    response = client.get(reverse("experiences:support"), {"product": "a/b"})

    assert response.status_code == HTTPStatus.OK
    assert response.context["selected_product"] is None


def test_a_new_ticket_goes_straight_to_an_organisations_only_product(
    environment,
    client,
):
    client.force_login(environment["applicant"])

    response = client.get(reverse("experiences:support"), {"new": "1"})

    assert 'name="subject"' in response.text
    assert response.context["selected_product"] == environment["product"]


def test_a_ticket_needs_a_product_when_there_are_several(environment, client):
    phr_product(environment)
    client.force_login(environment["applicant"])

    response = client.post(
        reverse("experiences:support"),
        {
            "subject": "Which product?",
            "category": "phr-app-p2",
            "issue_type": "Consent Flow",
            "priority": "medium",
            "body": "Sent without choosing one.",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    assert not Ticket.objects.exists()


def test_a_product_page_still_selects_its_product(environment, client):
    client.force_login(environment["applicant"])

    response = client.get(environment["product"].get_absolute_url())

    assert response.context["selected_product"] == environment["product"]
    assert 'id="nav-overview"' in response.text


def test_each_product_shows_a_chip_per_milestone(environment, client):
    approve(environment, "m1")
    submit(environment, "m2")

    response = dashboard(client, environment["applicant"])

    assert [
        (chip["code"], chip["state"])
        for chip in response.context["product_rows"][0].statuses
    ] == [
        ("M1", "approved"),
        ("M2", "in_review"),
        ("M3", "draft"),
        ("M4", "draft"),
        ("UHI", "draft"),
    ]
    assert "ui-queue-chip--in_review font-mono" in response.text


def test_the_status_key_names_every_chip_a_product_can_show(environment, client):
    html = dashboard(client, environment["applicant"]).text

    for state, label in PRODUCT_STATUS_KEY:
        assert f"ui-queue-chip--{state} font-medium" in html
        assert label in html


def test_tiles_count_what_waits_on_whom(environment, client):
    submit(environment, "m1")
    response = dashboard(client, environment["applicant"])
    assert response.context["waiting_count"] == 0
    assert response.context["under_review_count"] == 1
    assert response.context["production_count"] == 0

    item = raise_query(environment)
    response = client.get(reverse("dashboard"))
    assert response.context["waiting_count"] == 1
    assert response.context["under_review_count"] == 0

    services.reply_query(item.queries.get(), environment["applicant"], "Case 3.")
    response = client.get(reverse("dashboard"))
    assert response.context["waiting_count"] == 0
    assert response.context["under_review_count"] == 1

    environment["product"].production_client_id = "PROD-1"
    environment["product"].save(update_fields=["production_client_id"])
    assert client.get(reverse("dashboard")).context["production_count"] == 1


def test_tiles_leave_out_other_organisations(environment, client):
    other = Organisation.objects.create(name="Someone else")
    Membership.objects.create(
        organisation=other,
        user=environment["outsider"],
        role=Role.OWNER,
    )
    item = services.organisation_review(other, environment["outsider"])
    services.save_review_form(
        item,
        environment["outsider"],
        data=organisation_data(),
        files={"supporting_document": pdf()},
        submit=True,
    )
    item.refresh_from_db()
    assert item.status == ReviewItem.Status.IN_REVIEW

    response = dashboard(client, environment["applicant"])

    assert response.context["under_review_count"] == 0


def test_a_rejected_registration_stands_out_and_stops_counting(environment, client):
    submit(environment, "m1")
    ReviewItem.objects.filter(
        product=environment["product"],
        kind=ReviewItem.Kind.PRODUCT,
    ).update(status=ReviewItem.Status.REJECTED)

    response = dashboard(client, environment["applicant"])

    assert "Your product registration was rejected" in response.text
    assert response.context["under_review_count"] == 0


@pytest.fixture
def unsent_verification(environment):
    Organisation.objects.filter(pk=environment["org"].pk).update(
        verification_status=Organisation.VerificationStatus.PENDING,
    )
    environment["org"].review_items.filter(
        kind=ReviewItem.Kind.ORGANISATION,
    ).update(status=ReviewItem.Status.DRAFT)


def test_an_unsent_verification_is_the_next_step(
    environment,
    unsent_verification,
    client,
):
    response = dashboard(client, environment["applicant"])

    assert response.context["next_step"]["url"] == reverse("experiences:organisation")
    assert "Complete your organisation verification" in response.text


def test_only_those_who_can_act_are_prompted(environment, unsent_verification, client):
    support = UserFactory.create()
    Membership.objects.create(
        organisation=environment["org"],
        user=support,
        role=Role.SUPPORT,
    )

    response = dashboard(client, support)

    assert response.status_code == HTTPStatus.OK
    assert "next_step" not in response.context
    assert "Register a product" not in response.text


def test_a_verified_organisation_has_no_next_step(environment, client):
    response = dashboard(client, environment["applicant"])

    assert response.context["next_step"] is None


def tile(code, label="Open", status="draft", locked_by=""):
    return {
        "definition": SimpleNamespace(key=code.lower(), code=code, name="Services"),
        "label": label,
        "status": status,
        "locked_by": locked_by,
    }


def test_a_locked_milestone_takes_the_queues_waiting_chip():
    assert product_statuses(
        [{"tiles": [tile("M2", "Locked", "draft", locked_by="M1")]}],
        {},
    ) == [
        {
            "code": "M2",
            "title": "M2 Services",
            "state": "blocked",
            "label": "Locked",
        },
    ]


def test_a_milestone_two_tracks_share_counts_once():
    shared = tile("M2")

    chips = product_statuses([{"tiles": [shared]}, {"tiles": [shared]}], {})

    assert [chip["code"] for chip in chips] == ["M2"]


def test_a_wasa_request_gets_a_chip_of_its_own():
    review = ReviewItem(status=ReviewItem.Status.QUERY)

    chips = product_statuses(
        [{"tiles": [tile("M1", "Query raised", "query_raised")]}],
        {"review": review},
    )

    assert [(chip["code"], chip["state"]) for chip in chips] == [
        ("M1", "query_raised"),
        ("WASA", "query_raised"),
    ]
