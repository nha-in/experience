from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.demo import product_data
from ohc_experience.events.models import Event
from ohc_experience.experiences import workflows
from ohc_experience.experiences.forms import SupportForm
from ohc_experience.experiences.models import EventRegistration
from ohc_experience.experiences.models import Notification
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def portal_workspaces(owner_membership):
    result = []
    for name, milestones in (
        ("Alpha HMIS", ["HIE-CM:m1"]),
        ("Zeta Locker", ["HealthLocker:locker1"]),
    ):
        data = product_data(name)
        data["applied_milestones"] = milestones
        workspace, form = workflows.register_product(
            owner_membership.organisation,
            owner_membership.user,
            data=data,
        )
        assert workspace, form.errors
        workspace.refresh_from_db()
        result.append(workspace)
    return result


@pytest.fixture
def portal_client(client, owner_membership, portal_workspaces):
    client.force_login(owner_membership.user)
    session = client.session
    session["experience_product"] = portal_workspaces[1].reference
    session.save()
    return client


def test_ticket_defaults_and_applied_track_choices(portal_workspaces):
    form = SupportForm(workspace=portal_workspaces[0])
    assert form["priority"].value() == "medium"
    assert form["category"].value() == "sandbox"
    assert [value for value, _label in form.fields["track"].choices] == ["", "HIE-CM"]
    assert form.fields["track"].choices[0][1] == "Not track-specific"


@pytest.mark.parametrize("track", ["PHR", "NHCX", "HealthLocker", "unknown"])
def test_ticket_refuses_unapplied_track(portal_workspaces, track):
    form = SupportForm(
        workspace=portal_workspaces[0],
        data={
            "subject": "Help",
            "priority": "medium",
            "body": "Details",
            "track": track,
        },
    )
    assert not form.is_valid()
    assert "track" in form.errors


@pytest.mark.parametrize("route", ["experiences:support", "experiences:events"])
def test_programme_pages_keep_current_product(portal_client, portal_workspaces, route):
    response = portal_client.get(reverse(route))
    assert response.status_code == HTTPStatus.OK
    assert response.context["workspace"] == portal_workspaces[1]
    assert f"product={portal_workspaces[1].reference}".encode() in response.content
    response = portal_client.get(
        reverse(route),
        {"product": portal_workspaces[0].reference},
    )
    assert response.context["workspace"] == portal_workspaces[0]
    assert portal_client.session["experience_product"] == portal_workspaces[0].reference


def test_support_ignores_another_organisations_product(
    portal_client,
    portal_workspaces,
):
    membership = MembershipFactory()
    other, form = workflows.register_product(
        membership.organisation,
        membership.user,
        data=product_data("Private product"),
    )
    assert other, form.errors
    response = portal_client.get(
        reverse("experiences:support"),
        {"product": other.reference},
    )
    assert response.context["workspace"] == portal_workspaces[1]
    assert b"Private product" not in response.content


def test_ticket_create_saves_category_and_scopes_product(
    portal_client,
    portal_workspaces,
):
    response = portal_client.post(
        reverse("experiences:support"),
        {
            "subject": "Callback rejects the request",
            "category": "api",
            "priority": "medium",
            "track": "HealthLocker",
            "body": "The callback returns an unexpected status.",
        },
    )
    ticket = Ticket.objects.get()
    assert response.status_code == HTTPStatus.FOUND
    assert ticket.category == "api"
    assert ticket.product == portal_workspaces[1].product
    assert ticket.track == "HealthLocker"
    assert ticket.messages.get().body == "The callback returns an unexpected status."


def test_ticket_creation_error_keeps_form_and_does_not_write(portal_client):
    response = portal_client.post(
        reverse("experiences:support"),
        {
            "subject": "Help",
            "category": "bad",
            "priority": "medium",
            "track": "HIE-CM",
            "body": "Details",
        },
    )
    assert response.status_code == HTTPStatus.OK
    assert "category" in response.context["form"].errors
    assert "track" in response.context["form"].errors
    assert b"New support ticket" in response.content
    assert not Ticket.objects.exists()


def test_ticket_reply_needs_no_category_and_keeps_downloads(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_workspaces[0].product,
        subject="Existing ticket",
        created_by=owner_membership.user,
        category="api",
    )
    upload = SimpleUploadedFile(
        "diagnostic.pdf",
        b"%PDF-1.4\n%%EOF",
        content_type="application/pdf",
    )
    response = portal_client.post(
        ticket.get_absolute_url(),
        {"body": "More details", "attachments": upload},
    )
    assert response.status_code == HTTPStatus.FOUND
    message = ticket.messages.get()
    assert message.body == "More details"
    assert message.attachments.get().original_name == "diagnostic.pdf"
    response = portal_client.get(ticket.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert b"diagnostic.pdf" in response.content
    assert portal_client.session["experience_product"] == portal_workspaces[0].reference
    assert set(response.context["form"].fields) == {"body", "attachments"}
    ticket.refresh_from_db()
    assert ticket.category == "api"


def test_support_search_keeps_workspace_and_status(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    for workspace, subject, status in (
        (portal_workspaces[0], "Callback on another product", "open"),
        (portal_workspaces[1], "Callback investigation", "open"),
        (portal_workspaces[1], "Callback fixed", "resolved"),
        (portal_workspaces[1], "Other issue", "open"),
    ):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=workspace.product,
            subject=subject,
            status=status,
        )
    response = portal_client.get(
        reverse("experiences:support"),
        {"q": "callback", "status": "open"},
    )
    assert response.status_code == HTTPStatus.OK
    assert [ticket.subject for ticket in response.context["tickets"]] == [
        "Callback investigation",
    ]


def test_support_counts_keep_filters_and_workspace_before_status(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    for product_index, subject, category, priority, status in (
        (1, "Callback investigation", "api", "high", "open"),
        (1, "Callback fixed", "api", "high", "resolved"),
        (1, "Unrelated issue", "api", "high", "open"),
        (1, "Callback medium priority", "api", "medium", "open"),
        (1, "Callback sandbox issue", "sandbox", "high", "open"),
        (0, "Callback on another product", "api", "high", "open"),
    ):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=portal_workspaces[product_index].product,
            subject=subject,
            category=category,
            priority=priority,
            status=status,
        )
    response = portal_client.get(
        reverse("experiences:support"),
        {"q": "callback", "category": "api", "priority": "high", "status": "open"},
    )
    assert response.status_code == HTTPStatus.OK
    assert [ticket.subject for ticket in response.context["tickets"]] == [
        "Callback investigation",
    ]
    expected_workspace_tickets = 5
    assert response.context["ticket_total"] == expected_workspace_tickets
    assert {tab["value"]: tab["count"] for tab in response.context["status_tabs"]} == {
        "": 2,
        "open": 1,
        "awaiting_vendor": 0,
        "resolved": 1,
        "closed": 0,
    }
    assert response.context["has_ticket_filters"]
    assert b">Clear filters</a>" in response.content
    cleared = portal_client.get(
        reverse("experiences:support"),
        {"product": portal_workspaces[1].reference},
    )
    assert not cleared.context["has_ticket_filters"]
    assert len(cleared.context["tickets"]) == expected_workspace_tickets
    assert cleared.context["workspace"] == portal_workspaces[1]


def test_invalid_support_filters_do_not_create_a_false_active_state(portal_client):
    response = portal_client.get(
        reverse("experiences:support"),
        {"category": "unknown", "priority": "urgent", "status": "invalid", "q": "  "},
    )
    assert response.status_code == HTTPStatus.OK
    assert response.context["ticket_filters"] == {
        "category": "",
        "priority": "",
        "status": "",
        "q": "",
    }
    assert not response.context["has_ticket_filters"]


def test_event_counts_filter_by_kind_and_registrations_stay_private(
    portal_client,
    owner_membership,
):
    events = {}
    for title, kind, days, published in (
        ("Next webinar", "webinar", 2, True),
        ("Next workshop", "workshop", 1, True),
        ("Past webinar", "webinar", -2, True),
        ("Unpublished webinar", "webinar", 1, False),
    ):
        events[title] = Event.objects.create(
            title=title,
            kind=kind,
            starts_at=timezone.now() + timedelta(days=days),
            published_at=timezone.now() if published else None,
        )
    for title in ("Next webinar", "Past webinar", "Unpublished webinar"):
        EventRegistration.objects.create(
            event=events[title],
            user=owner_membership.user,
        )
    EventRegistration.objects.create(
        event=events["Next workshop"],
        user=UserFactory(),
    )
    response = portal_client.get(
        reverse("experiences:events"),
        {"kind": "webinar", "period": "past"},
    )
    assert response.status_code == HTTPStatus.OK
    assert list(response.context["events"]) == [events["Past webinar"]]
    assert response.context["upcoming_count"] == 1
    assert response.context["past_count"] == 1
    assert response.context["next_event"] == events["Next webinar"]
    assert response.context["registered_upcoming_count"] == 1
    assert events["Next workshop"].pk not in response.context["registered"]
    assert events["Next webinar"].title.encode() not in response.content


def test_registered_event_explains_missing_joining_details(
    portal_client,
    owner_membership,
):
    event = Event.objects.create(
        title="Office hours awaiting a link",
        starts_at=timezone.now() + timedelta(days=2),
        published_at=timezone.now(),
    )
    EventRegistration.objects.create(event=event, user=owner_membership.user)
    response = portal_client.get(reverse("experiences:events"))
    assert response.status_code == HTTPStatus.OK
    assert b"Joining details have not been added yet." in response.content
    assert b"Cancel registration" in response.content
    assert b"Join event" not in response.content


def test_event_register_cancel_and_filter_keep_product(
    portal_client,
    portal_workspaces,
):
    event = Event.objects.create(
        title="Integration office hours",
        kind="office_hours",
        starts_at=timezone.now() + timedelta(days=2),
        published_at=timezone.now(),
        join_url="https://example.org/event",
        description="Bring your integration questions.",
    )
    url = (
        f"{reverse('experiences:events')}?product={portal_workspaces[1].reference}"
        "&kind=office_hours&period=upcoming"
    )
    response = portal_client.post(
        url,
        {"event": event.pk, "intent": "register"},
        follow=True,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.context["workspace"] == portal_workspaces[1]
    assert response.redirect_chain == [(url, HTTPStatus.FOUND)]
    assert [str(message) for message in response.context["messages"]] == [
        f"You are registered for {event.title}.",
    ]
    assert EventRegistration.objects.filter(event=event).exists()
    assert Notification.objects.filter(subject__contains=event.title).count() == 1
    assert b"Cancel registration" in response.content
    assert b"Join event" in response.content
    response = portal_client.get(url, {"kind": "webinar"})
    assert event not in response.context["events"]
    response = portal_client.post(
        url,
        {"event": event.pk, "intent": "cancel"},
        follow=True,
    )
    assert response.status_code == HTTPStatus.OK
    assert not EventRegistration.objects.filter(event=event).exists()
    assert response.redirect_chain == [(url, HTTPStatus.FOUND)]
    assert [str(message) for message in response.context["messages"]] == [
        f"Registration cancelled for {event.title}.",
    ]


def test_reviewer_can_render_tickets_and_resolve(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_workspaces[0].product,
        subject="Needs review",
    )
    portal_client.force_login(ReviewerFactory(is_nha_team=True))
    response = portal_client.get(ticket.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    html = response.content.decode()
    assert html.index("Mark resolved") < html.index("Close ticket")
    response = portal_client.post(
        ticket.get_absolute_url(),
        {"intent": "resolve"},
        follow=True,
    )
    assert response.status_code == HTTPStatus.OK
    ticket.refresh_from_db()
    assert ticket.status == "resolved"


@pytest.mark.parametrize("route", ["experiences:events", "experiences:support"])
def test_reviewer_pages_do_not_select_a_product(
    portal_client,
    portal_workspaces,
    route,
):
    portal_client.force_login(ReviewerFactory(is_nha_team=True))
    session = portal_client.session
    session["experience_product"] = portal_workspaces[1].reference
    session.save()
    response = portal_client.get(reverse(route))

    assert response.status_code == HTTPStatus.OK
    assert response.context["workspace"] is None
    assert portal_client.session["experience_product"] == portal_workspaces[1].reference


def test_vendor_can_close_their_own_ticket(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_workspaces[0].product,
        subject="Sorted on our side",
    )
    page = portal_client.get(ticket.get_absolute_url())
    assert b"Close ticket" in page.content
    assert b"Mark resolved" not in page.content

    response = portal_client.post(ticket.get_absolute_url(), {"intent": "close"})

    assert response.status_code == HTTPStatus.FOUND
    ticket.refresh_from_db()
    assert ticket.status == "closed"
    assert ticket.messages.filter(kind="event", body="Closed").exists()
    assert b"Close ticket" not in portal_client.get(ticket.get_absolute_url()).content
