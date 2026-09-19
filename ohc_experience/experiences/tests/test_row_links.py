"""A row on these screens opens its page from anywhere in the row.

static/js/row-link.js follows the link marked data-row-link in the row a click
lands in, so each screen marks one link per row: the one to the row's page.
"""

# ruff: noqa: F811
from datetime import timedelta
from html.parser import HTMLParser
from http import HTTPStatus
from urllib.parse import quote

import pytest
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.tests.test_wasa_lifecycle import certificate_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences import production
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def row_links(response):
    """Where each row on the page opens, in page order."""

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.hrefs = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "a" and "data-row-link" in attributes:
                self.hrefs.append(attributes["href"])

    parser = Links()
    parser.feed(response.content.decode())
    return parser.hrefs


def open_ticket(environment):
    return Ticket.objects.create(
        organisation=environment["org"],
        product=environment["workspace"].product,
        subject="Callback fails",
        status="open",
    )


def test_production_rows_open_the_product(environment, client):
    approve(environment)
    client.force_login(environment["reviewer"])
    url = reverse("experiences:production-list")
    detail = reverse(
        "experiences:production-detail",
        args=[environment["workspace"].reference],
    )
    # An approved exit puts the product in Pending, waiting on its client ID...
    assert row_links(client.get(url, {"tab": "pending"})) == [detail]
    production.record(
        environment["workspace"].product,
        environment["reviewer"],
        client_id="PROD-ROW-LINK",
        expected="",
    )
    # ...and adding the ID moves the same row to Approved.
    assert row_links(client.get(url, {"tab": "approved"})) == [detail]


def test_queue_rows_open_the_product_review(environment, client):
    submit(environment, "m1")
    client.force_login(environment["reviewer"])
    response = client.get(reverse("experiences:queue"))
    reference = environment["workspace"].reference
    assert row_links(response) == [
        reverse("experiences:product-detail", args=[reference]),
    ]


def test_ticket_rows_open_the_ticket(environment, client):
    url = reverse("experiences:ticket", args=[open_ticket(environment).reference])
    client.force_login(environment["applicant"])
    response = client.get(
        reverse("experiences:support"),
        {"product": environment["workspace"].reference},
    )
    # The table row, and the card that replaces it on a narrow screen.
    assert row_links(response) == [url, url]


def test_staff_rows_open_the_editor_except_a_superadmins(environment, client):
    admin = environment["admin"]
    client.force_login(admin)
    response = client.get(reverse("experiences:staff-list"))
    # A superadmin is listed but cannot be edited here, so that row opens nothing.
    assert admin.email in response.content.decode()
    url = reverse("experiences:staff-edit", args=[environment["reviewer"].pk])
    assert row_links(response) == [url, url]


def test_dashboard_track_rows_open_the_queue_for_their_track(environment, client):
    submit(environment, "m1")
    client.force_login(environment["reviewer"])
    response = client.get(reverse("experiences:assess-dashboard"))
    queue = reverse("experiences:queue")
    tracks = response.context["by_track"]
    assert tracks
    assert row_links(response) == [
        f"{queue}?scope=ready&item={quote(row['code'])}" for row in tracks
    ]


def test_event_rows_open_their_participants(client):
    staff = UserFactory(is_nha_team=True)
    AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="events",
        category="UHI",
        can_write=True,
    )
    starts_at = timezone.now() + timedelta(days=7)
    draft = Event.objects.create(title="Draft", category="UHI", starts_at=starts_at)
    published = Event.objects.create(
        title="Launch webinar",
        category="UHI",
        starts_at=starts_at,
        published_at=timezone.now(),
    )
    client.force_login(staff)
    response = client.get(reverse("experiences:event-manage"))
    # Editing a published event is a superadmin's call, but its registrations
    # are readable, so every row opens the one page it always has.
    assert row_links(response) == [
        reverse("experiences:event-participants", args=[event.pk])
        for event in (published, draft)
    ]


def test_review_rows_open_approvals_prerequisites_and_tickets(environment, client):
    m1 = submit(environment, "m1")
    m3 = submit(environment, "m3")
    ticket = reverse("experiences:ticket", args=[open_ticket(environment).reference])
    client.force_login(environment["reviewer"])
    response = client.get(reverse("experiences:review", args=[m3.pk]))
    approvals = [
        approval.get_absolute_url() for approval in response.context["prior_approvals"]
    ]
    assert approvals
    # M3 builds on M1, so its page lists M1 among what it waits on...
    assert sorted(row_links(response)) == sorted(
        [*approvals, ticket, m1.get_absolute_url()],
    )
    # ...and M1's page lists M3 among the requests waiting on it.
    response = client.get(reverse("experiences:review", args=[m1.pk]))
    assert m3.get_absolute_url() in row_links(response)


def test_production_exit_rows_open_their_review(environment, client):
    approve(environment)
    client.force_login(environment["reviewer"])
    response = client.get(
        reverse(
            "experiences:production-detail",
            args=[environment["workspace"].reference],
        ),
    )
    assert row_links(response) == [milestone(environment, "m1").get_absolute_url()]


def test_product_page_ticket_rows_open_the_ticket(environment, client):
    ticket = reverse("experiences:ticket", args=[open_ticket(environment).reference])
    reference = environment["workspace"].reference
    client.force_login(environment["reviewer"])
    response = client.get(reverse("experiences:product-detail", args=[reference]))
    assert row_links(response) == [ticket]


def test_wasa_history_rows_open_the_submission(environment, client):
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )
    client.force_login(environment["applicant"])
    page = client.get(url)
    response = client.post(
        url,
        {
            **certificate_data(),
            "intent": "submit",
            "certification_revision": page.context["certification_revision"],
            "revision": "",
            "wasa_certificate": pdf("product-wasa.pdf"),
        },
    )
    assert response.status_code == HTTPStatus.FOUND
    response = client.get(url)
    item = response.context["item"]
    assert row_links(response) == [
        reverse("experiences:submission", args=[item.pk, item.selected_submission_id]),
    ]


def test_upcoming_event_rows_open_the_events_page(environment, client):
    Event.objects.create(
        title="Launch webinar",
        starts_at=timezone.now() + timedelta(days=3),
        published_at=timezone.now(),
    )
    client.force_login(environment["applicant"])
    response = client.get(
        reverse("experiences:overview", args=[environment["workspace"].reference]),
    )
    assert "Launch webinar" in response.content.decode()
    assert row_links(response) == [reverse("experiences:events")]
