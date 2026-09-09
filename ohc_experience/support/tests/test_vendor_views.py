"""The vendor's support inbox and ticket thread (screens 1g and 1h).

The scoping tests are the important ones: a vendor must never read or move
another organisation's ticket, and a reference that belongs elsewhere has to
look exactly like a reference that does not exist.

Every action is checked twice — once as htmx sends it and once as a browser
with scripting off sends it — because the second path is the one the app is
required to work on.
"""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.context_processors import SESSION_PRODUCT_KEY
from ohc_experience.abdm.tests.factories import ProductFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.support.models import Category
from ohc_experience.support.models import Priority
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import TicketMessage
from ohc_experience.support.views import TICKETS_PER_PAGE
from ohc_experience.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpResponse
    from django.test import Client

    from ohc_experience.organisations.models import Membership
    from ohc_experience.organisations.models import Organisation
    from ohc_experience.users.models import User

pytestmark = pytest.mark.django_db

LIST_URL = reverse("support:list")
CREATE_URL = reverse("support:create")
HTMX_HEADERS = {"HX-Request": "true"}

# How far past one page the pagination test fills the inbox.
OVERFLOW_TICKETS = 2
# One reply and one recorded status change.
THREAD_ENTRIES = 2
# The htmx reply and the plain one that follows it.
EXPECTED_REPLIES = 2


def make_ticket(organisation: Organisation, **overrides) -> Ticket:
    fields = {
        "subject": "Webhook events not firing on demo facility",
        "category": Category.API,
        "priority": Priority.HIGH,
        "status": Status.OPEN,
    }
    fields.update(overrides)
    return Ticket.objects.create(organisation=organisation, **fields)


def backdate(ticket: Ticket, *, opened_ago: timedelta, answered_after: timedelta):
    """Rewrite a ticket's clock, which auto_now_add will not let us set."""
    created = timezone.now() - opened_ago
    Ticket.objects.filter(pk=ticket.pk).update(
        created_at=created,
        first_responded_at=created + answered_after,
    )


def message_texts(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def fragment_of(response: HttpResponse) -> str:
    """The response body with its whitespace squashed to single spaces.

    Lets a test read the markup an htmx response carries without caring how the
    template happened to be wrapped.
    """
    return " ".join(response.content.decode().split())


def form_at(html: str, action: str) -> str:
    """The whole form posting to that action, from its `<form` to `</form>`.

    Anchored on the opening tag rather than on the `action=` attribute, because
    `method=` is rendered ahead of it and a test that starts mid-tag would read
    a form with no method as a form with one.
    """
    marker = html.index(f'action="{action}"')
    start = html.rindex("<form", 0, marker)
    return html[start : html.index("</form>", start)]


def detail_url(ticket: Ticket) -> str:
    return reverse("support:detail", kwargs={"reference": ticket.reference})


def reply_url(ticket: Ticket) -> str:
    return reverse("support:reply", kwargs={"reference": ticket.reference})


def status_url(ticket: Ticket) -> str:
    return reverse("support:status", kwargs={"reference": ticket.reference})


@pytest.fixture
def vendor(owner_membership: Membership) -> User:
    return owner_membership.user


@pytest.fixture
def vendor_client(sign_in: Callable[[User], Client], vendor: User) -> Client:
    return sign_in(vendor)


@pytest.fixture
def other_organisation() -> Organisation:
    return OrganisationFactory.create(name="Rival Health Systems", onboarded=True)


# ── The inbox ──────────────────────────────────────────────────────────────


def test_inbox_lists_only_this_organisations_tickets(
    vendor_client: Client,
    onboarded_organisation: Organisation,
    other_organisation: Organisation,
):
    ours = make_ticket(onboarded_organisation, subject="Ours to read")
    make_ticket(other_organisation, subject="Theirs to read")

    response = vendor_client.get(LIST_URL)

    assert response.status_code == HTTPStatus.OK
    assert list(response.context["tickets"]) == [ours]
    body = response.content.decode()
    assert "Ours to read" in body
    assert "Theirs to read" not in body


def test_inbox_shows_an_empty_state_when_nothing_was_ever_raised(
    vendor_client: Client,
):
    response = vendor_client.get(LIST_URL)

    assert response.status_code == HTTPStatus.OK
    assert list(response.context["tickets"]) == []
    assert "No tickets yet" in response.content.decode()


def test_filters_narrow_the_queryset(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    api_ticket = make_ticket(
        onboarded_organisation,
        subject="FHIR bundle validation errors",
        category=Category.API,
        priority=Priority.HIGH,
        status=Status.OPEN,
    )
    billing_ticket = make_ticket(
        onboarded_organisation,
        subject="GST invoice for the Advanced cohort",
        category=Category.BILLING,
        priority=Priority.LOW,
        status=Status.RESOLVED,
    )

    by_category = vendor_client.get(LIST_URL, {"category": Category.BILLING})
    assert list(by_category.context["tickets"]) == [billing_ticket]

    by_status_and_priority = vendor_client.get(
        LIST_URL,
        {"status": Status.OPEN, "priority": Priority.HIGH},
    )
    assert list(by_status_and_priority.context["tickets"]) == [api_ticket]

    unfiltered = vendor_client.get(LIST_URL)
    assert set(unfiltered.context["tickets"]) == {api_ticket, billing_ticket}


def test_a_filter_matching_nothing_says_so_rather_than_looking_empty(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    make_ticket(onboarded_organisation, priority=Priority.HIGH)

    response = vendor_client.get(LIST_URL, {"priority": Priority.LOW})

    body = response.content.decode()
    assert "No tickets match these filters" in body
    assert "No tickets yet" not in body


def test_an_unknown_filter_value_is_ignored_rather_than_erroring(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation)

    response = vendor_client.get(LIST_URL, {"status": "not-a-status"})

    assert response.status_code == HTTPStatus.OK
    assert list(response.context["tickets"]) == [ticket]


def test_the_inbox_paginates_and_the_pager_keeps_the_filters(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    for index in range(TICKETS_PER_PAGE + OVERFLOW_TICKETS):
        make_ticket(onboarded_organisation, subject=f"Ticket {index}")

    first_page = vendor_client.get(LIST_URL, {"category": Category.API})

    assert len(first_page.context["tickets"]) == TICKETS_PER_PAGE
    assert first_page.context["page_obj"].has_next()
    assert "page=2&amp;category=api" in first_page.content.decode()

    second_page = vendor_client.get(LIST_URL, {"category": Category.API, "page": 2})
    assert len(second_page.context["tickets"]) == OVERFLOW_TICKETS


def test_the_median_first_response_waits_for_enough_data(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    answered = make_ticket(onboarded_organisation)
    backdate(answered, opened_ago=timedelta(days=2), answered_after=timedelta(hours=2))

    too_few = vendor_client.get(LIST_URL)
    assert "Too few answered tickets" in too_few.content.decode()

    for minutes in (60, 134, 300):
        ticket = make_ticket(onboarded_organisation)
        backdate(
            ticket,
            opened_ago=timedelta(days=2),
            answered_after=timedelta(minutes=minutes),
        )

    response = vendor_client.get(LIST_URL)
    body = response.content.decode()
    assert "Median first response over the last 30 days" in body
    # 120, 60, 134 and 300 minutes — median 127 minutes.
    assert "2 h 7 m" in body


def test_a_reply_older_than_the_window_is_not_counted(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    for _index in range(3):
        ticket = make_ticket(onboarded_organisation)
        backdate(
            ticket,
            opened_ago=timedelta(days=90),
            answered_after=timedelta(minutes=30),
        )

    response = vendor_client.get(LIST_URL)

    assert "Too few answered tickets" in response.content.decode()


def test_htmx_filtering_returns_the_results_fragment(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    make_ticket(onboarded_organisation, subject="Only ticket")

    response = vendor_client.get(LIST_URL, headers=HTMX_HEADERS)

    body = fragment_of(response)
    assert response.status_code == HTTPStatus.OK
    assert "<!DOCTYPE" not in body
    assert 'id="ticket-results"' in body
    # The count sits outside the swapped region, so it comes back out of band.
    assert 'id="ticket-count"' in body
    assert "hx-swap-oob" in body


def test_a_boosted_navigation_still_gets_the_whole_page(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    """A nav click is an htmx request too, and it must not get the fragment.

    The shell boosts its navigation: htmx fetches the very URL the href points
    at, keeps #main-content out of the whole-page response and picks #app-nav
    out of the same response out of band. Answer that with the results card on
    its own and there is nothing for either selector to find — the shell's
    guard then cancels the swap and falls back to a full reload, so the click
    costs two requests and flashes. Only the filter form and the pager ask for
    the results alone.
    """
    make_ticket(onboarded_organisation, subject="Boosted in")

    response = vendor_client.get(
        LIST_URL,
        headers={"HX-Request": "true", "HX-Boosted": "true"},
    )
    body = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert 'id="main-content"' in body
    assert 'id="app-nav"' in body
    assert "Boosted in" in body


# ── Scoping ────────────────────────────────────────────────────────────────


def test_another_organisations_reference_is_a_404_everywhere(
    vendor_client: Client,
    other_organisation: Organisation,
):
    theirs = make_ticket(other_organisation)

    assert vendor_client.get(detail_url(theirs)).status_code == HTTPStatus.NOT_FOUND
    reply = vendor_client.post(reply_url(theirs), {"body": "Prying."})
    assert reply.status_code == HTTPStatus.NOT_FOUND
    status = vendor_client.post(status_url(theirs), {"status": Status.RESOLVED})
    assert status.status_code == HTTPStatus.NOT_FOUND

    theirs.refresh_from_db()
    assert theirs.status == Status.OPEN
    assert not theirs.messages.exists()


def test_an_unknown_reference_is_a_404(vendor_client: Client):
    url = reverse("support:detail", kwargs={"reference": "TKT-9999"})

    assert vendor_client.get(url).status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("url", [LIST_URL, CREATE_URL])
def test_an_anonymous_visitor_is_redirected_to_sign_in(client: Client, url: str):
    response = client.get(url)

    assert response.status_code == HTTPStatus.FOUND
    assert reverse("account_login") in response["Location"]


def test_an_anonymous_reply_is_redirected_and_writes_nothing(
    client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation)

    response = client.post(reply_url(ticket), {"body": "Not signed in."})

    assert response.status_code == HTTPStatus.FOUND
    assert reverse("account_login") in response["Location"]
    assert not ticket.messages.exists()


def test_an_anonymous_status_change_is_redirected_and_writes_nothing(
    client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation, status=Status.OPEN)

    response = client.post(status_url(ticket), {"status": Status.RESOLVED})

    assert response.status_code == HTTPStatus.FOUND
    assert reverse("account_login") in response["Location"]
    ticket.refresh_from_db()
    assert ticket.status == Status.OPEN
    assert not ticket.messages.exists()


def test_a_signed_in_user_with_no_organisation_is_refused_everywhere(
    sign_in: Callable[[User], Client],
    onboarded_organisation: Organisation,
):
    """No membership, no vendor scope — and 403 rather than somebody's inbox.

    The scoping above all starts from `self.organisation`, so a user the mixin
    cannot resolve one for is the case that must never fall through to a
    queryset.
    """
    ticket = make_ticket(onboarded_organisation)
    stranger = sign_in(UserFactory.create())

    assert stranger.get(LIST_URL).status_code == HTTPStatus.FORBIDDEN
    assert stranger.get(CREATE_URL).status_code == HTTPStatus.FORBIDDEN
    assert stranger.get(detail_url(ticket)).status_code == HTTPStatus.FORBIDDEN
    reply = stranger.post(reply_url(ticket), {"body": "Prying."})
    assert reply.status_code == HTTPStatus.FORBIDDEN
    status = stranger.post(status_url(ticket), {"status": Status.RESOLVED})
    assert status.status_code == HTTPStatus.FORBIDDEN

    ticket.refresh_from_db()
    assert ticket.status == Status.OPEN
    assert not ticket.messages.exists()


def test_a_vendor_is_refused_at_every_ohc_console_route(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    """The other side of the boundary this app sits on.

    /ohc/ is one queue across every vendor, so a signed-in vendor who guesses a
    URL there is the worst case in the whole feature. 403 rather than a
    redirect, and — the part that matters — the console's two mutations write
    nothing when a vendor posts to them.
    """
    ticket = make_ticket(onboarded_organisation, status=Status.OPEN)

    reads = [
        reverse("ohc:queue"),
        reverse("ohc:ticket", kwargs={"reference": ticket.reference}),
        reverse("ohc:events"),
        reverse("ohc:event-create"),
    ]
    for url in reads:
        assert vendor_client.get(url).status_code == HTTPStatus.FORBIDDEN, url

    writes = [
        (
            reverse("ohc:ticket-reply", kwargs={"reference": ticket.reference}),
            {"body": "Answering my own ticket as the Care team."},
        ),
        (
            reverse("ohc:ticket-update", kwargs={"reference": ticket.reference}),
            {"status": Status.CLOSED},
        ),
    ]
    for url, data in writes:
        assert vendor_client.post(url, data).status_code == HTTPStatus.FORBIDDEN, url

    ticket.refresh_from_db()
    assert ticket.status == Status.OPEN
    assert not ticket.messages.exists()


# ── The thread ─────────────────────────────────────────────────────────────


def test_the_thread_renders_replies_as_cards_and_events_as_lines(
    vendor_client: Client,
    onboarded_organisation: Organisation,
    vendor: User,
):
    ticket = make_ticket(onboarded_organisation, linked_facility="Arogya Test Hospital")
    TicketMessage.objects.create(
        ticket=ticket,
        author=vendor,
        body="Deliveries stopped after Friday evening.",
        kind=TicketMessage.Kind.REPLY,
    )
    TicketMessage.objects.create(
        ticket=ticket,
        author=vendor,
        body=str(Status.RESOLVED.label),
        kind=TicketMessage.Kind.EVENT,
    )

    response = vendor_client.get(detail_url(ticket))
    body = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert ticket.reference in body
    assert "Deliveries stopped after Friday evening." in body
    assert "Arogya Test Hospital" in body
    assert "marked this" in body
    assert len(response.context["thread"]) == THREAD_ENTRIES


def test_every_action_keeps_a_real_form_for_a_browser_without_scripting(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    """htmx is enhancement: the markup underneath it has to stand on its own.

    Every form keeps a method and a real action, and every link a real href, so
    a browser that never runs the hx- attributes still reaches the same URL by
    the same verb. The redirects those POSTs answer with are asserted by the
    reply and status tests below, each of which posts without HX-Request.
    """
    ticket = make_ticket(onboarded_organisation)

    inbox = fragment_of(vendor_client.get(LIST_URL))
    filters = form_at(inbox, LIST_URL)
    assert 'method="get"' in filters
    # Reading a list is a GET, so the filter form needs no token — but it does
    # need a button, or a picker change is the only way to apply it.
    assert "data-filter-submit" in filters
    assert f'href="{CREATE_URL}"' in inbox

    new_ticket = fragment_of(vendor_client.get(CREATE_URL))
    opening = form_at(new_ticket, CREATE_URL)
    assert 'method="post"' in opening
    assert "csrfmiddlewaretoken" in opening
    assert f'href="{LIST_URL}"' in new_ticket

    thread = fragment_of(vendor_client.get(detail_url(ticket)))
    for action in (reply_url(ticket), status_url(ticket)):
        form = form_at(thread, action)
        assert 'method="post"' in form, action
        assert "csrfmiddlewaretoken" in form, action
    assert f'href="{LIST_URL}"' in thread


def test_a_reply_creates_a_message_and_moves_the_ticket_to_open(
    vendor_client: Client,
    onboarded_organisation: Organisation,
    vendor: User,
):
    ticket = make_ticket(onboarded_organisation, status=Status.AWAITING_VENDOR)

    response = vendor_client.post(
        reply_url(ticket),
        {"body": "Secret rotated — deliveries resumed."},
    )

    assert response.status_code == HTTPStatus.FOUND
    assert response["Location"] == detail_url(ticket)
    ticket.refresh_from_db()
    assert ticket.status == Status.OPEN
    message = ticket.messages.get()
    assert message.body == "Secret rotated — deliveries resumed."
    assert message.author == vendor
    assert message.from_ohc_team is False
    assert message.kind == TicketMessage.Kind.REPLY


def test_an_empty_reply_is_refused_without_touching_the_ticket(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation, status=Status.AWAITING_VENDOR)

    response = vendor_client.post(reply_url(ticket), {"body": "   "})

    assert response.status_code == HTTPStatus.FOUND
    assert not ticket.messages.exists()
    ticket.refresh_from_db()
    assert ticket.status == Status.AWAITING_VENDOR
    assert any("Write something" in text for text in message_texts(response))


def test_the_htmx_reply_returns_a_fragment_while_the_plain_reply_redirects(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation, status=Status.AWAITING_VENDOR)

    swapped = vendor_client.post(
        reply_url(ticket),
        {"body": "Confirming the replay landed."},
        headers=HTMX_HEADERS,
    )
    fragment = fragment_of(swapped)

    assert swapped.status_code == HTTPStatus.OK
    assert "<!DOCTYPE" not in fragment
    assert "Confirming the replay landed." in fragment
    # The badges, the details rail and the emptied reply box ride along.
    assert 'id="ticket-badges" hx-swap-oob="outerHTML"' in fragment
    assert 'id="ticket-details" hx-swap-oob="outerHTML"' in fragment
    assert 'id="reply-panel" hx-swap-oob="outerHTML"' in fragment

    plain = vendor_client.post(reply_url(ticket), {"body": "And again, no script."})
    assert plain.status_code == HTTPStatus.FOUND
    assert plain["Location"] == detail_url(ticket)
    assert ticket.messages.count() == EXPECTED_REPLIES


def test_an_empty_htmx_reply_swaps_the_errored_box_back_rather_than_the_thread(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation)

    response = vendor_client.post(reply_url(ticket), {"body": ""}, headers=HTMX_HEADERS)
    fragment = fragment_of(response)

    assert response.status_code == HTTPStatus.OK
    assert not ticket.messages.exists()
    # Only the reply panel comes back, and it is out of band — so htmx has
    # nothing left to append to the conversation.
    assert 'id="reply-panel" hx-swap-oob="outerHTML"' in fragment
    assert 'id="ticket-badges"' not in fragment


def test_resolving_sets_resolved_and_writes_an_event_entry(
    vendor_client: Client,
    onboarded_organisation: Organisation,
    vendor: User,
):
    ticket = make_ticket(onboarded_organisation, status=Status.OPEN)

    response = vendor_client.post(status_url(ticket), {"status": Status.RESOLVED})

    assert response.status_code == HTTPStatus.FOUND
    assert response["Location"] == detail_url(ticket)
    ticket.refresh_from_db()
    assert ticket.status == Status.RESOLVED
    assert ticket.resolved_at is not None
    entry = ticket.messages.get()
    assert entry.is_event
    assert entry.body == str(Status.RESOLVED.label)
    assert entry.author == vendor
    assert entry.from_ohc_team is False


def test_reopening_moves_a_resolved_ticket_back_to_open(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation, status=Status.RESOLVED)

    response = vendor_client.post(status_url(ticket), {"status": Status.OPEN})

    assert response.status_code == HTTPStatus.FOUND
    ticket.refresh_from_db()
    assert ticket.status == Status.OPEN
    assert ticket.messages.get().is_event


def test_a_vendor_cannot_close_a_ticket(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation, status=Status.OPEN)

    response = vendor_client.post(status_url(ticket), {"status": Status.CLOSED})

    assert response.status_code == HTTPStatus.FORBIDDEN
    ticket.refresh_from_db()
    assert ticket.status == Status.OPEN
    assert not ticket.messages.exists()


def test_the_htmx_status_change_swaps_the_badges(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation, status=Status.OPEN)

    response = vendor_client.post(
        status_url(ticket),
        {"status": Status.RESOLVED},
        headers=HTMX_HEADERS,
    )
    fragment = fragment_of(response)

    assert response.status_code == HTTPStatus.OK
    assert "<!DOCTYPE" not in fragment
    assert 'id="ticket-badges" hx-swap-oob="outerHTML"' in fragment
    assert "ui-badge--success" in fragment
    # A resolved ticket offers the other move.
    assert "Reopen ticket" in fragment


def test_the_status_endpoint_refuses_a_get(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    ticket = make_ticket(onboarded_organisation)

    response = vendor_client.get(status_url(ticket))

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED


# ── Opening a ticket ───────────────────────────────────────────────────────


def test_opening_a_ticket_scopes_it_and_records_the_first_message(
    vendor_client: Client,
    onboarded_organisation: Organisation,
    vendor: User,
):
    response = vendor_client.post(
        CREATE_URL,
        {
            "subject": "Bulk patient import — CSV column mapping",
            "category": Category.SANDBOX,
            "priority": Priority.MEDIUM,
            "linked_facility": "Arogya Test Hospital",
            "body": "The importer rejects our date column.",
        },
    )

    ticket = Ticket.objects.get()
    assert response.status_code == HTTPStatus.FOUND
    assert response["Location"] == detail_url(ticket)
    assert ticket.organisation == onboarded_organisation
    assert ticket.created_by == vendor
    assert ticket.status == Status.OPEN
    assert ticket.messages.get().body == "The importer rejects our date column."


def test_the_new_ticket_form_ignores_the_fields_a_vendor_does_not_own(
    vendor_client: Client,
    onboarded_organisation: Organisation,
):
    """Posting extra keys must not let a vendor place a ticket elsewhere.

    The organisation and the author are set from the session after the form has
    had its say, and the fields nobody types — reference, status, assignee —
    are not on the form at all. This posts all of them anyway.
    """
    other = OrganisationFactory.create(name="Rival Health Systems", onboarded=True)
    ohc_person = UserFactory.create(is_ohc_team=True)

    vendor_client.post(
        CREATE_URL,
        {
            "subject": "Sandbox tokens expire after an hour",
            "category": Category.SANDBOX,
            "priority": Priority.MEDIUM,
            "linked_facility": "",
            "body": "They used to last a day.",
            "organisation": other.pk,
            "status": Status.CLOSED,
            "assignee": ohc_person.pk,
            "reference": "TKT-1",
        },
    )

    ticket = Ticket.objects.get()
    assert ticket.organisation == onboarded_organisation
    assert ticket.status == Status.OPEN
    assert ticket.assignee is None
    assert ticket.reference != "TKT-1"
    assert not other.tickets.exists()


def test_an_invalid_new_ticket_redraws_the_form(
    vendor_client: Client,
):
    response = vendor_client.post(CREATE_URL, {"subject": "", "body": ""})

    assert response.status_code == HTTPStatus.OK
    assert not Ticket.objects.exists()
    assert response.context["form"].errors


def test_a_mail_outage_does_not_undo_a_new_ticket(
    vendor_client: Client,
    mail_outage: list[tuple],
    caplog: pytest.LogCaptureFixture,
):
    UserFactory.create(email="desk@nha.gov.in", is_ohc_team=True)

    response = vendor_client.post(
        CREATE_URL,
        {
            "subject": "Callback never received after a consent request",
            "category": Category.API,
            "priority": Priority.HIGH,
            "body": "The gateway accepted the request and nothing came back.",
        },
    )

    ticket = Ticket.objects.get()
    assert mail_outage, "the desk copy was never attempted"
    assert response.status_code == HTTPStatus.FOUND
    assert response["Location"] == detail_url(ticket)
    assert ticket.messages.count() == 1
    assert "Could not send" in caplog.text


def test_the_new_ticket_form_defaults_to_the_current_product(
    vendor_client: Client,
    owner_membership: Membership,
):
    ProductFactory.create(organisation=owner_membership.organisation)
    second = ProductFactory.create(organisation=owner_membership.organisation)
    session = vendor_client.session
    session[SESSION_PRODUCT_KEY] = second.sandbox_id
    session.save()

    response = vendor_client.get(CREATE_URL)

    assert response.context["form"]["product"].value() == second.pk


def test_the_new_ticket_form_falls_back_to_the_first_product(
    vendor_client: Client,
    owner_membership: Membership,
):
    first = ProductFactory.create(organisation=owner_membership.organisation)
    ProductFactory.create(organisation=owner_membership.organisation)

    response = vendor_client.get(CREATE_URL)

    assert response.context["form"]["product"].value() == first.pk


def test_the_new_ticket_form_stays_generic_without_products(vendor_client: Client):
    response = vendor_client.get(CREATE_URL)

    assert response.context["form"]["product"].value() is None


# ── The Track picker follows the product ───────────────────────────────────

ALL_TRACKS = ["", "HI-CM", "UHI", "NHCX", "PHR", "HealthLocker"]


def track_options(response: HttpResponse) -> list[str]:
    return [value for value, _label in response.context["form"].fields["track"].choices]


def uhi_product(organisation: Organisation):
    return ProductFactory.create(
        organisation=organisation,
        applied_tracks=["UHI"],
        applied_milestones=["UHI:UHI1"],
    )


def test_the_track_picker_offers_only_the_chosen_products_tracks(
    vendor_client: Client,
    owner_membership: Membership,
):
    ProductFactory.create(organisation=owner_membership.organisation)

    response = vendor_client.get(CREATE_URL)

    assert track_options(response) == ["", "HI-CM"]


def test_the_track_picker_offers_every_track_without_a_product(
    vendor_client: Client,
    owner_membership: Membership,
):
    ProductFactory.create(organisation=owner_membership.organisation)

    # "Not product-specific" comes back as a blank product, not a missing one.
    response = vendor_client.get(CREATE_URL, {"product": ""})

    assert track_options(response) == ALL_TRACKS
    assert not response.context["form"]["product"].value()


def test_changing_the_product_redraws_the_track_picker(
    vendor_client: Client,
    owner_membership: Membership,
):
    ProductFactory.create(organisation=owner_membership.organisation)
    uhi = uhi_product(owner_membership.organisation)

    response = vendor_client.get(
        CREATE_URL,
        {"product": uhi.pk, "track": "UHI"},
        headers=HTMX_HEADERS,
    )

    body = fragment_of(response)
    assert response.status_code == HTTPStatus.OK
    assert "<!DOCTYPE" not in body
    assert 'id="track-field"' in body
    assert track_options(response) == ["", "UHI"]
    assert response.context["form"]["track"].value() == "UHI"


def test_a_track_the_new_product_is_not_on_is_dropped_by_the_redraw(
    vendor_client: Client,
    owner_membership: Membership,
):
    uhi = uhi_product(owner_membership.organisation)

    response = vendor_client.get(
        CREATE_URL,
        {"product": uhi.pk, "track": "HI-CM"},
        headers=HTMX_HEADERS,
    )

    assert track_options(response) == ["", "UHI"]
    assert not response.context["form"]["track"].value()


def test_a_boosted_visit_to_the_new_ticket_form_gets_the_whole_page(
    vendor_client: Client,
):
    response = vendor_client.get(
        CREATE_URL,
        headers={"HX-Request": "true", "HX-Boosted": "true"},
    )
    body = response.content.decode()

    assert 'id="main-content"' in body
    assert 'id="track-field"' in body


def test_a_track_the_product_is_not_on_is_refused(
    vendor_client: Client,
    owner_membership: Membership,
):
    product = ProductFactory.create(organisation=owner_membership.organisation)

    response = vendor_client.post(
        CREATE_URL,
        {
            "subject": "UHI search returns nothing",
            "category": Category.API,
            "priority": Priority.HIGH,
            "product": product.pk,
            "track": "UHI",
            "body": "Every search comes back empty.",
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert not Ticket.objects.exists()
    assert "not on this track" in str(response.context["form"].errors["track"])
    # The form is redrawn with the product's own tracks, not with every track.
    assert track_options(response) == ["", "HI-CM"]


def test_a_track_the_product_is_on_is_recorded(
    vendor_client: Client,
    owner_membership: Membership,
):
    product = ProductFactory.create(organisation=owner_membership.organisation)

    response = vendor_client.post(
        CREATE_URL,
        {
            "subject": "Care context linking fails",
            "category": Category.API,
            "priority": Priority.HIGH,
            "product": product.pk,
            "track": "HI-CM",
            "body": "The link request is rejected with a 400.",
        },
    )

    ticket = Ticket.objects.get()
    assert response.status_code == HTTPStatus.FOUND
    assert ticket.product == product
    assert ticket.track == "HI-CM"
