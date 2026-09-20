from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.demo import product_data
from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences import workflows
from ohc_experience.experiences.definitions import SupportCategoryDefinition
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
        ("Zeta Locker", ["HealthLocker:locker1", "HIE-CM:m1"]),
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


def test_ticket_defaults_and_applied_category_choices(portal_workspaces):
    """A product on HIE-CM is offered that track's categories, and the catch-all."""
    form = SupportForm(workspace=portal_workspaces[0])
    assert form["priority"].value() == "medium"
    assert [value for value, _label in form.fields["category"].choices] == [
        "abdm-m1",
        "abdm-m2",
        "abdm-m3",
        "abdm-m4",
        "abdm-review",
        "abdm-scan-share",
        "",
    ]
    # "Others" sits last but is still what a ticket defaults to.
    assert form.fields["category"].choices[-1][1] == "Others"
    assert form["category"].value() == ""


@pytest.mark.parametrize("category", ["phr-app", "nhcx-auth", "HIE-CM", "unknown"])
def test_ticket_refuses_a_category_this_product_cannot_file_under(
    portal_workspaces,
    category,
):
    """Another track's categories, a retired track code, and nonsense alike."""
    form = SupportForm(
        workspace=portal_workspaces[0],
        data={
            "subject": "Help",
            "priority": "medium",
            "body": "Details",
            "category": category,
        },
    )
    assert not form.is_valid()
    assert "category" in form.errors


def test_a_category_takes_an_issue_type_from_its_own_sub_menu(portal_workspaces):
    def submit(**extra):
        return SupportForm(
            workspace=portal_workspaces[0],
            data={
                "subject": "Help",
                "priority": "medium",
                "body": "Details",
                **extra,
            },
        )

    assert not submit(category="abdm-m2").is_valid()
    # An issue type from another category is no better than none at all.
    assert "issue_type" in submit(category="abdm-m2", issue_type="ABHA Creation").errors
    form = submit(category="abdm-m2", issue_type="Data Transfer")
    assert form.is_valid(), form.errors
    assert form.cleaned_data["issue_type"] == "Data Transfer"


def test_a_category_with_no_sub_menu_records_no_issue_type():
    """A category that asks nothing further drops an issue type posted anyway.

    Every ABDM category has a sub-menu now, so this uses a program that keeps a
    bare one: the rule belongs to the form, not to one program's menu.
    """

    class BareMenu:
        @staticmethod
        def support_category_map():
            return {
                "bare": SupportCategoryDefinition("bare", "Bare"),
                "full": SupportCategoryDefinition(
                    "full",
                    "Full",
                    issue_types=("Data Transfer",),
                ),
            }

    form = SupportForm(
        program=BareMenu,
        data={
            "subject": "Help",
            "priority": "medium",
            "body": "Details",
            "category": "bare",
            "issue_type": "Data Transfer",
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["issue_type"] == ""


def test_others_asks_which_kind_of_general_question_it_is(portal_workspaces):
    """The catch-all carries a sub-menu of its own, so it is answered like one."""

    def submit(**extra):
        return SupportForm(
            workspace=portal_workspaces[0],
            data={
                "subject": "Help",
                "priority": "medium",
                "body": "Details",
                "category": "",
                **extra,
            },
        )

    assert not submit().is_valid()
    assert "issue_type" in submit(issue_type="Data Transfer").errors
    form = submit(issue_type="Access / General Inquiry / Concerns")
    assert form.is_valid(), form.errors
    assert form.cleaned_data["issue_type"] == "Access / General Inquiry / Concerns"


def test_issue_type_menu_is_flat_and_tags_each_option_with_its_category(
    portal_workspaces,
):
    """The sub-menu lists issue types only: no category heading, but each option
    still carries the category it belongs to so the script can narrow it."""
    html = str(SupportForm(workspace=portal_workspaces[0])["issue_type"])
    assert "<optgroup" not in html
    # Category names all read "ABDM - …"; no issue type does, so none leaked in.
    assert "ABDM - " not in html
    assert 'value="Data Transfer" data-category="abdm-m2"' in html


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
            "category": "abdm-m2",
            "issue_type": "Bridge Service",
            "priority": "medium",
            "body": "The callback returns an unexpected status.",
        },
    )
    ticket = Ticket.objects.get()
    assert response.status_code == HTTPStatus.FOUND
    assert ticket.category == "abdm-m2"
    assert ticket.issue_type == "Bridge Service"
    assert ticket.category_label == "ABDM - Milestone 2"
    assert ticket.product == portal_workspaces[1].product
    assert ticket.messages.get().body == "The callback returns an unexpected status."


def test_ticket_list_shows_category_and_sub_category_columns(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_workspaces[1].product,
        subject="Callback rejects the request",
        created_by=owner_membership.user,
        category="abdm-m2",
        issue_type="Bridge Service",
    )
    response = portal_client.get(reverse("experiences:support"))
    assert response.status_code == HTTPStatus.OK
    body = response.content.decode()
    assert ">Category</th>" in body
    assert ">Sub-category</th>" in body
    assert "ABDM - Milestone 2" in body
    assert "Bridge Service" in body


def test_ticket_creation_error_keeps_form_and_does_not_write(portal_client):
    response = portal_client.post(
        reverse("experiences:support"),
        {
            "subject": "Help",
            "category": "bad",
            "priority": "medium",
            "body": "Details",
        },
    )
    assert response.status_code == HTTPStatus.OK
    assert "category" in response.context["form"].errors
    assert b"New support ticket" in response.content
    assert not Ticket.objects.exists()


@pytest.mark.parametrize(
    ("actor", "message"),
    [
        ("reviewer", "Only an organisation's integrators can open a ticket."),
        ("no_product", "Register a product before opening a ticket."),
    ],
)
def test_support_hides_the_new_ticket_form_from_those_who_cannot_use_it(
    client,
    owner_membership,
    portal_workspaces,
    actor,
    message,
):
    """A reviewer, and an integrator with no product yet, have nothing to raise
    a ticket against. They must not be offered the form, and posting one must
    explain itself rather than fail the request."""
    if actor == "reviewer":
        client.force_login(ReviewerFactory(is_nha_team=True))
    else:
        membership = MembershipFactory.create(role="owner")
        client.force_login(membership.user)

    creating = client.get(reverse("experiences:support"), {"new": "1"})

    assert creating.status_code == HTTPStatus.OK
    assert not creating.context["creating"]
    assert b"New support ticket" not in creating.content

    response = client.post(
        reverse("experiences:support"),
        {
            "subject": "Callback rejects the request",
            "priority": "medium",
            "body": "The callback returns an unexpected status.",
        },
        follow=True,
    )

    assert response.status_code == HTTPStatus.OK
    assert not Ticket.objects.exists()
    assert message in [str(item) for item in response.context["messages"]]


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
        category="abdm-m1",
        issue_type="ABHA Creation",
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
    assert ticket.category == "abdm-m1"
    assert ticket.issue_type == "ABHA Creation"


def test_support_search_keeps_workspace_and_status(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    for workspace, subject, status in (
        (portal_workspaces[0], "Callback on another product", "open"),
        (portal_workspaces[1], "Callback investigation", "open"),
        (portal_workspaces[1], "Callback fixed", "closed"),
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
        (1, "Callback investigation", "abdm-m2", "high", "open"),
        (1, "Callback fixed", "abdm-m2", "high", "closed"),
        (1, "Unrelated issue", "abdm-m2", "high", "open"),
        (1, "Callback medium priority", "abdm-m2", "medium", "open"),
        (1, "Callback sandbox issue", "abdm-m1", "high", "open"),
        (0, "Callback on another product", "abdm-m2", "high", "open"),
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
        {"q": "callback", "category": "abdm-m2", "priority": "high", "status": "open"},
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
        "awaiting_integrator": 0,
        "closed": 1,
    }
    assert response.context["has_ticket_filters"]
    assert b">Clear filters</a>" in response.content
    cleared = portal_client.get(
        reverse("experiences:support"),
        {"product": portal_workspaces[1].reference},
    )
    assert not cleared.context["has_ticket_filters"]
    assert cleared.context["ticket_filters"]["status"] == "open"
    open_workspace_tickets = 4
    assert len(cleared.context["tickets"]) == open_workspace_tickets
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


@pytest.mark.parametrize(
    ("as_reviewer", "labels"),
    [
        (False, ["All tickets", "With NHA team", "Awaiting your reply", "Resolved"]),
        (True, ["All tickets", "Needs a reply", "Awaiting integrator", "Resolved"]),
    ],
)
def test_status_tabs_say_whose_turn_it_is(portal_client, as_reviewer, labels):
    if as_reviewer:
        portal_client.force_login(ReviewerFactory(is_nha_team=True))
    response = portal_client.get(reverse("experiences:support"))
    assert response.status_code == HTTPStatus.OK
    assert [tab["label"] for tab in response.context["status_tabs"]] == labels


@pytest.mark.parametrize(
    ("as_reviewer", "message"),
    [
        (False, b"None of your tickets are with the NHA team right now."),
        (True, b"No tickets need your reply right now."),
    ],
)
def test_an_empty_first_tab_does_not_claim_there_are_no_tickets(
    portal_client,
    portal_workspaces,
    owner_membership,
    as_reviewer,
    message,
):
    Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_workspaces[0].product,
        subject="Waiting on the integrator",
        status="awaiting_integrator",
    )
    if as_reviewer:
        portal_client.force_login(ReviewerFactory(is_nha_team=True))
    response = portal_client.get(
        reverse("experiences:support"),
        {"product": portal_workspaces[0].reference},
    )
    assert response.status_code == HTTPStatus.OK
    assert b"No tickets yet" not in response.content
    assert message in response.content


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
    response = portal_client.get(event.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert b"Joining details have not been added yet." in response.content
    assert b"Cancel registration" in response.content
    assert b"Join event" not in response.content


def test_support_pages_keep_filters_in_links(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    url = reverse("experiences:support")
    per_page = portal_client.get(url).context["tickets"].paginator.per_page
    for number in range(per_page + 1):
        Ticket.objects.create(
            organisation=owner_membership.organisation,
            product=portal_workspaces[1].product,
            subject=f"Callback {number}",
            status="open",
        )
    params = {"q": "callback", "status": "open"}
    first = portal_client.get(url, params)
    assert len(first.context["tickets"]) == per_page
    assert b'aria-label="Tickets pagination"' in first.content
    assert b'href="?q=callback&amp;status=open&amp;page=2"' in first.content
    last = portal_client.get(url, {**params, "page": 2})
    assert len(last.context["tickets"]) == 1


def test_events_paginate_in_date_order(portal_client):
    url = reverse("experiences:events")
    per_page = portal_client.get(url).context["events"].paginator.per_page
    for day in range(1, per_page + 2):
        Event.objects.create(
            title=f"Office hours {day}",
            starts_at=timezone.now() + timedelta(days=day),
            published_at=timezone.now(),
        )
    first = portal_client.get(url, {"period": "upcoming"})
    assert b'href="?period=upcoming&amp;page=2"' in first.content
    last = portal_client.get(url, {"period": "upcoming", "page": 2})
    assert [event.title for event in last.context["events"]] == [
        f"Office hours {per_page + 1}",
    ]


def test_event_register_and_cancel_on_the_event_page(
    portal_client,
    portal_workspaces,
):
    event = Event.objects.create(
        title="Integration office hours",
        kind="event",
        starts_at=timezone.now() + timedelta(days=2),
        published_at=timezone.now(),
        join_url="https://example.org/event",
        description="Bring your integration questions.",
    )
    url = event.get_absolute_url()
    listing = (
        f"{reverse('experiences:events')}?product={portal_workspaces[1].reference}"
        "&kind=event&period=upcoming"
    )
    # The listing no longer registers anyone; it sends them to the event instead.
    assert url.encode() in portal_client.get(listing).content
    response = portal_client.post(url, {"intent": "register"}, follow=True)
    assert response.status_code == HTTPStatus.OK
    # The product the integrator came from stays selected on the event's page.
    assert response.context["workspace"] == portal_workspaces[1]
    assert response.redirect_chain == [(url, HTTPStatus.FOUND)]
    assert [str(message) for message in response.context["messages"]] == [
        f"You are registered for {event.title}.",
    ]
    assert EventRegistration.objects.filter(event=event).exists()
    assert Notification.objects.filter(subject__contains=event.title).count() == 1
    assert b"Cancel registration" in response.content
    assert b"Join event" in response.content
    filtered = portal_client.get(listing, {"kind": "webinar"})
    assert event not in filtered.context["events"]
    response = portal_client.post(url, {"intent": "cancel"}, follow=True)
    assert response.status_code == HTTPStatus.OK
    assert not EventRegistration.objects.filter(event=event).exists()
    assert response.redirect_chain == [(url, HTTPStatus.FOUND)]
    assert [str(message) for message in response.context["messages"]] == [
        f"Registration cancelled for {event.title}.",
    ]
    # Registering is the event page's job now; the listing only reads.
    assert (
        portal_client.post(reverse("experiences:events")).status_code
        == HTTPStatus.METHOD_NOT_ALLOWED
    )


def test_an_event_that_has_ended_takes_no_registration(portal_client):
    event = Event.objects.create(
        title="Last month's workshop",
        starts_at=timezone.now() - timedelta(days=30),
        published_at=timezone.now() - timedelta(days=40),
    )
    url = event.get_absolute_url()
    page = portal_client.get(url)
    assert b"This event has ended." in page.content
    assert b'value="register"' not in page.content
    assert (
        portal_client.post(url, {"intent": "register"}).status_code
        == HTTPStatus.FORBIDDEN
    )
    assert not EventRegistration.objects.exists()


def test_reviewer_can_reply_and_resolve(
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
    assert b"Mark as resolved" in response.content
    response = portal_client.post(
        ticket.get_absolute_url(),
        {"intent": "close", "body": "Callback acknowledged on our side."},
        follow=True,
    )
    assert response.status_code == HTTPStatus.OK
    ticket.refresh_from_db()
    assert ticket.status == "closed"


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


def test_integrator_can_resolve_their_own_ticket(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_workspaces[0].product,
        subject="Sorted on our side",
    )
    url = ticket.get_absolute_url()
    assert b"Mark as resolved" in portal_client.get(url).content

    response = portal_client.post(url, {"intent": "close", "body": "Fixed now."})

    assert response.status_code == HTTPStatus.FOUND
    ticket.refresh_from_db()
    assert ticket.status == "closed"
    assert list(ticket.messages.values_list("kind", "body")) == [
        ("reply", "Fixed now."),
        ("event", "Resolved"),
    ]
    assert b"Mark as resolved" not in portal_client.get(url).content


def test_resolving_takes_a_comment_of_at_least_ten_characters(
    portal_client,
    portal_workspaces,
    owner_membership,
):
    ticket = Ticket.objects.create(
        organisation=owner_membership.organisation,
        product=portal_workspaces[0].product,
        subject="Sorted on our side",
    )
    url = ticket.get_absolute_url()
    for body in ("", "Fixed now", "   Fixed now   "):
        response = portal_client.post(url, {"intent": "close", "body": body})
        assert response.status_code == HTTPStatus.OK
        assert response.context["form"].errors["body"] == [
            "Add a comment of at least 10 characters to resolve this ticket.",
        ]
        assert b"This ticket has not been resolved." in response.content
    ticket.refresh_from_db()
    assert ticket.status == "open"
    assert not ticket.messages.exists()
    # A plain reply has no minimum length.
    response = portal_client.post(url, {"body": "Fixed now"})
    assert response.status_code == HTTPStatus.FOUND
