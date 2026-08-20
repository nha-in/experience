"""What the OHC console actually does, once someone is through the gate.

The gate itself is pinned route by route in test_views.py; the table at the top
of this module asserts it a second time, deliberately, because it is the
security boundary of the whole feature and a screen added without
OhcTeamRequiredMixin should fail loudly in more than one place. The rest of the
file covers behaviour: the queue spans vendors and filters, a reply moves the
ticket, a status change lands in the thread, a verification decision moves a
vendor and stamps the date, publishing toggles both ways.
"""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from ohc_experience.events.models import Event
from ohc_experience.ohc.views import QUEUE_PAGE_SIZE
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.support.models import Priority
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import TicketMessage
from ohc_experience.support.models import post_reply
from ohc_experience.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpResponse
    from django.test import Client

    from ohc_experience.users.models import User

pytestmark = pytest.mark.django_db

QUEUE_URL = reverse("ohc:queue")
ORGANISATIONS_URL = reverse("ohc:organisations")
EVENTS_URL = reverse("ohc:events")
EVENT_CREATE_URL = reverse("ohc:event-create")

EVENT_DATA = {
    "title": "Certification office hours",
    "kind": Event.Kind.OFFICE_HOURS,
    "summary": "Bring your ABDM questions.",
    "description": "An open session with the certification team.",
    "starts_at": "2026-09-01T10:00",
    "ends_at": "2026-09-01T11:00",
    "location": "",
    "join_url": "https://meet.ohc.network/oh",
}

# POST-only routes. Everything else the console publishes is a GET.
POST_ONLY = {
    "ohc:ticket-reply",
    "ohc:ticket-update",
    "ohc:organisation-verification",
    "ohc:event-publish",
}


def message_texts(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


@pytest.fixture
def ohc_user(db) -> User:
    return UserFactory.create(
        name="Anand Suresh",
        email="anand@ohc.network",
        is_ohc_team=True,
    )


@pytest.fixture
def other_ohc_user(db) -> User:
    return UserFactory.create(
        name="Divya Menon",
        email="divya@ohc.network",
        is_ohc_team=True,
    )


@pytest.fixture
def vendor_user(db) -> User:
    """A signed-in vendor: authenticated, and not the OHC team."""
    return UserFactory.create(name="Meera Krishnan", email="meera@sunrise.in")


@pytest.fixture
def ticket(onboarded_organisation: Organisation, vendor_user: User) -> Ticket:
    return Ticket.objects.create(
        organisation=onboarded_organisation,
        subject="Webhook events not firing on demo facility",
        priority=Priority.HIGH,
        created_by=vendor_user,
    )


@pytest.fixture
def event(db) -> Event:
    return Event.objects.create(
        title="Upgrade webinar",
        starts_at=timezone.now() + timedelta(days=7),
    )


def route_urls(ticket: Ticket, event: Event) -> dict[str, str]:
    """Every route the console publishes, keyed by name."""
    return {
        "ohc:queue": QUEUE_URL,
        "ohc:ticket": reverse("ohc:ticket", args=[ticket.reference]),
        "ohc:ticket-reply": reverse("ohc:ticket-reply", args=[ticket.reference]),
        "ohc:ticket-update": reverse("ohc:ticket-update", args=[ticket.reference]),
        "ohc:organisations": ORGANISATIONS_URL,
        "ohc:organisation": reverse(
            "ohc:organisation",
            args=[ticket.organisation.slug],
        ),
        "ohc:organisation-verification": reverse(
            "ohc:organisation-verification",
            args=[ticket.organisation.slug],
        ),
        "ohc:events": EVENTS_URL,
        "ohc:event-create": EVENT_CREATE_URL,
        "ohc:event-update": reverse("ohc:event-update", args=[event.slug]),
        "ohc:event-publish": reverse("ohc:event-publish", args=[event.slug]),
    }


class TestEveryRouteIsGated:
    """The security boundary, route by route. Nothing here may be relaxed."""

    def test_anonymous_is_sent_to_sign_in(
        self,
        client: Client,
        ticket: Ticket,
        event: Event,
    ):
        for name, url in route_urls(ticket, event).items():
            response = client.post(url) if name in POST_ONLY else client.get(url)

            assert response.status_code == HTTPStatus.FOUND, name
            assert response["Location"].startswith(reverse("account_login")), name

    def test_a_signed_in_vendor_is_refused(
        self,
        sign_in: Callable[[User], Client],
        vendor_user: User,
        ticket: Ticket,
        event: Event,
    ):
        signed_in = sign_in(vendor_user)

        for name, url in route_urls(ticket, event).items():
            response = signed_in.post(url) if name in POST_ONLY else signed_in.get(url)

            assert response.status_code == HTTPStatus.FORBIDDEN, name

    def test_a_vendor_cannot_reach_the_console_by_owning_the_ticket(
        self,
        sign_in: Callable[[User], Client],
        ticket: Ticket,
    ):
        """Having opened the ticket is not a way in — only the team gets in."""
        response = sign_in(ticket.created_by).get(
            reverse("ohc:ticket", args=[ticket.reference]),
        )

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_django_staff_is_not_the_ohc_team(
        self,
        sign_in: Callable[[User], Client],
    ):
        """is_staff only opens the Django admin; the console asks for is_ohc_team."""
        staff = UserFactory.create(email="ops@ohc.network", is_staff=True)

        assert sign_in(staff).get(QUEUE_URL).status_code == HTTPStatus.FORBIDDEN

    def test_the_ohc_team_gets_through_on_every_route(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
        event: Event,
    ):
        signed_in = sign_in(ohc_user)
        bodies = {
            "ohc:ticket-reply": {"body": "Looking at it now."},
            "ohc:ticket-update": {
                "status": Status.AWAITING_VENDOR,
                "priority": Priority.HIGH,
                "assignee": "",
            },
            "ohc:organisation-verification": {
                "status": Organisation.VerificationStatus.VERIFIED,
            },
            "ohc:event-publish": {},
        }

        for name, url in route_urls(ticket, event).items():
            if name in POST_ONLY:
                response = signed_in.post(url, bodies[name])
                assert response.status_code == HTTPStatus.FOUND, name
            else:
                response = signed_in.get(url)
                assert response.status_code == HTTPStatus.OK, name

    def test_post_only_routes_reject_a_get(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
        event: Event,
    ):
        """A state change is never a GET link, so GET is not allowed."""
        signed_in = sign_in(ohc_user)
        urls = route_urls(ticket, event)

        for name in POST_ONLY:
            response = signed_in.get(urls[name])

            assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED, name


class TestQueue:
    def test_it_spans_every_organisation(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        other = OrganisationFactory.create(name="Arogya Systems", onboarded=True)
        Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Sandbox reset",
        )
        Ticket.objects.create(organisation=other, subject="FHIR bundle errors")

        response = sign_in(ohc_user).get(QUEUE_URL)

        assert response.status_code == HTTPStatus.OK
        organisations = {
            ticket.organisation.name for ticket in response.context["tickets"]
        }
        assert organisations == {"Sunrise Health Systems", "Arogya Systems"}

    def test_it_opens_on_the_tickets_awaiting_the_care_team(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        needs_reply = Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Needs a reply",
            status=Status.OPEN,
        )
        for status in (Status.AWAITING_VENDOR, Status.RESOLVED, Status.CLOSED):
            Ticket.objects.create(
                organisation=onboarded_organisation,
                subject=f"Not ours: {status}",
                status=status,
            )

        response = sign_in(ohc_user).get(QUEUE_URL)

        assert list(response.context["tickets"]) == [needs_reply]

    def test_an_explicit_empty_status_widens_to_every_ticket(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Open one",
            status=Status.OPEN,
        )
        Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Closed one",
            status=Status.CLOSED,
        )

        response = sign_in(ohc_user).get(QUEUE_URL, {"status": ""})

        assert {ticket.subject for ticket in response.context["tickets"]} == {
            "Open one",
            "Closed one",
        }

    def test_it_filters_by_status_and_priority(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        wanted = Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Closed and high",
            status=Status.CLOSED,
            priority=Priority.HIGH,
        )
        Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Closed and low",
            status=Status.CLOSED,
            priority=Priority.LOW,
        )

        response = sign_in(ohc_user).get(
            QUEUE_URL,
            {"status": Status.CLOSED, "priority": Priority.HIGH},
        )

        assert list(response.context["tickets"]) == [wanted]

    def test_it_filters_by_unassigned_mine_and_a_named_member(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        other_ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        unassigned = Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Nobody has this",
        )
        mine = Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Mine",
            assignee=ohc_user,
        )
        theirs = Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Divya's",
            assignee=other_ohc_user,
        )
        signed_in = sign_in(ohc_user)

        cases = {
            "unassigned": unassigned,
            "mine": mine,
            str(other_ohc_user.pk): theirs,
        }
        for value, expected in cases.items():
            response = signed_in.get(QUEUE_URL, {"assignee": value})

            assert list(response.context["tickets"]) == [expected], value

    def test_a_junk_filter_falls_back_instead_of_erroring(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        Ticket.objects.create(organisation=onboarded_organisation, subject="Open one")

        response = sign_in(ohc_user).get(
            QUEUE_URL,
            {"status": "nonsense", "priority": "nonsense", "assignee": "nonsense"},
        )

        assert response.status_code == HTTPStatus.OK
        assert len(response.context["tickets"]) == 1

    def test_it_paginates_at_twenty_five(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        total = QUEUE_PAGE_SIZE + 1
        for index in range(total):
            Ticket.objects.create(
                organisation=onboarded_organisation,
                subject=f"Ticket {index}",
            )

        response = sign_in(ohc_user).get(QUEUE_URL)

        assert len(response.context["tickets"]) == QUEUE_PAGE_SIZE
        assert response.context["page_obj"].paginator.count == total
        # The default status has to survive into page two, or the second page
        # would quietly widen to every status.
        assert "status=open" in response.context["filter_query"]

    def test_it_uses_the_care_teams_wording_for_a_status(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Waiting on them",
            status=Status.AWAITING_VENDOR,
        )

        response = sign_in(ohc_user).get(QUEUE_URL, {"status": Status.AWAITING_VENDOR})
        body = response.content.decode()

        assert "Awaiting vendor" in body
        assert "Awaiting your reply" not in body

    def test_an_htmx_filter_change_returns_only_the_results(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        Ticket.objects.create(organisation=onboarded_organisation, subject="One")

        response = sign_in(ohc_user).get(
            QUEUE_URL,
            {"status": ""},
            headers={"hx-request": "true"},
        )
        body = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert 'id="queue-results"' in body
        assert "<!DOCTYPE html>" not in body

    def test_a_boosted_navigation_still_gets_the_whole_page(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        response = sign_in(ohc_user).get(
            QUEUE_URL,
            headers={"hx-request": "true", "hx-boosted": "true"},
        )
        body = response.content.decode()

        assert 'id="main-content"' in body
        assert 'id="ohc-nav"' in body


class TestTicketDetail:
    def test_it_shows_the_vendor_and_who_opened_it(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        response = sign_in(ohc_user).get(
            reverse("ohc:ticket", args=[ticket.reference]),
        )
        body = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert ticket.organisation.name in body
        assert "Meera Krishnan" in body

    def test_the_thread_is_not_called_messages(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        """Shadowing `messages` would swallow every flash on this page."""
        post_reply(
            ticket,
            ticket.created_by,
            "It stopped on Friday.",
            from_ohc_team=False,
        )

        response = sign_in(ohc_user).get(
            reverse("ohc:ticket", args=[ticket.reference]),
        )

        assert [entry.body for entry in response.context["thread"]] == [
            "It stopped on Friday.",
        ]

    def test_an_unknown_reference_is_a_404(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        response = sign_in(ohc_user).get(reverse("ohc:ticket", args=["TKT-9999"]))

        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_the_assignee_picker_lists_only_the_ohc_team_by_name(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        vendor_user: User,
        ticket: Ticket,
    ):
        response = sign_in(ohc_user).get(
            reverse("ohc:ticket", args=[ticket.reference]),
        )
        choices = dict(response.context["control_form"].fields["assignee"].choices)

        assert "Anand Suresh" in choices.values()
        assert "Meera Krishnan" not in choices.values()


class TestReply:
    def test_a_reply_moves_the_ticket_to_awaiting_vendor(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        response = sign_in(ohc_user).post(
            reverse("ohc:ticket-reply", args=[ticket.reference]),
            {"body": "The sandbox reset rotated your signing secret."},
        )

        assert response.status_code == HTTPStatus.FOUND
        ticket.refresh_from_db()
        assert ticket.status == Status.AWAITING_VENDOR
        assert ticket.first_responded_at is not None
        message = ticket.messages.get()
        assert message.author == ohc_user
        assert message.from_ohc_team is True
        assert message.kind == TicketMessage.Kind.REPLY

    def test_an_empty_reply_changes_nothing(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        response = sign_in(ohc_user).post(
            reverse("ohc:ticket-reply", args=[ticket.reference]),
            {"body": ""},
        )

        assert response.status_code == HTTPStatus.OK
        ticket.refresh_from_db()
        assert ticket.status == Status.OPEN
        assert ticket.messages.count() == 0

    def test_an_htmx_reply_swaps_the_workspace_back(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        response = sign_in(ohc_user).post(
            reverse("ohc:ticket-reply", args=[ticket.reference]),
            {"body": "On it."},
            headers={"hx-request": "true"},
        )
        body = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert 'id="ticket-workspace"' in body
        # The flash rides along out of band, into the region outside the swap.
        assert 'hx-swap-oob="innerHTML"' in body
        assert "Awaiting vendor" in body


class TestTriage:
    def test_a_status_change_writes_an_entry_into_the_thread(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        response = sign_in(ohc_user).post(
            reverse("ohc:ticket-update", args=[ticket.reference]),
            {
                "status": Status.RESOLVED,
                "priority": ticket.priority,
                "assignee": "",
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        ticket.refresh_from_db()
        assert ticket.status == Status.RESOLVED
        assert ticket.resolved_at is not None
        entry = ticket.messages.get()
        assert entry.kind == TicketMessage.Kind.EVENT
        assert entry.author == ohc_user
        assert entry.from_ohc_team is True

    def test_it_can_close_a_ticket(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        sign_in(ohc_user).post(
            reverse("ohc:ticket-update", args=[ticket.reference]),
            {"status": Status.CLOSED, "priority": ticket.priority, "assignee": ""},
        )

        ticket.refresh_from_db()
        assert ticket.status == Status.CLOSED

    def test_it_sets_priority_and_assignee_without_touching_the_thread(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        sign_in(ohc_user).post(
            reverse("ohc:ticket-update", args=[ticket.reference]),
            {
                "status": ticket.status,
                "priority": Priority.LOW,
                "assignee": str(ohc_user.pk),
            },
        )

        ticket.refresh_from_db()
        assert ticket.priority == Priority.LOW
        assert ticket.assignee == ohc_user
        assert ticket.status == Status.OPEN
        assert ticket.messages.count() == 0

    def test_a_vendor_can_never_be_the_assignee(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        vendor_user: User,
        ticket: Ticket,
    ):
        response = sign_in(ohc_user).post(
            reverse("ohc:ticket-update", args=[ticket.reference]),
            {
                "status": ticket.status,
                "priority": ticket.priority,
                "assignee": str(vendor_user.pk),
            },
        )

        assert response.status_code == HTTPStatus.OK
        ticket.refresh_from_db()
        assert ticket.assignee is None

    def test_submitting_no_change_says_so(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        response = sign_in(ohc_user).post(
            reverse("ohc:ticket-update", args=[ticket.reference]),
            {"status": ticket.status, "priority": ticket.priority, "assignee": ""},
            follow=True,
        )

        assert ticket.messages.count() == 0
        assert "Nothing on this ticket changed." in message_texts(response)


class TestOrganisationRegister:
    def test_the_list_shows_every_vendor(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        other = OrganisationFactory.create(name="Arogya Systems", onboarded=True)

        response = sign_in(ohc_user).get(ORGANISATIONS_URL)

        listed = {org.pk for org in response.context["organisations"]}
        assert {onboarded_organisation.pk, other.pk} <= listed

    def test_vendors_awaiting_a_decision_are_listed_first(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        """The one row anyone has to act on, above an alphabet that buries it."""
        OrganisationFactory.create(
            name="Arogya Systems",
            verification_status=Organisation.VerificationStatus.VERIFIED,
        )
        waiting = OrganisationFactory.create(name="Zenith Health")

        response = sign_in(ohc_user).get(ORGANISATIONS_URL)

        first = next(iter(response.context["organisations"]))

        assert first.pk == waiting.pk

    def test_it_filters_by_verification_status(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        verified = OrganisationFactory.create(
            name="Arogya Systems",
            verification_status=Organisation.VerificationStatus.VERIFIED,
        )
        OrganisationFactory.create(name="Zenith Health")

        response = sign_in(ohc_user).get(
            ORGANISATIONS_URL,
            {"status": Organisation.VerificationStatus.VERIFIED},
        )

        assert [org.pk for org in response.context["organisations"]] == [verified.pk]

    def test_the_search_matches_the_legal_name_too(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        """The console knows a vendor by its trading name and by the entity."""
        arogya = OrganisationFactory.create(
            name="Arogya Systems",
            legal_name="Arogya Healthtech Private Limited",
        )
        OrganisationFactory.create(name="Zenith Health")

        response = sign_in(ohc_user).get(ORGANISATIONS_URL, {"q": "healthtech"})

        assert [org.pk for org in response.context["organisations"]] == [arogya.pk]

    def test_a_junk_filter_falls_back_instead_of_erroring(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        """These URLs get hand-edited and pasted around."""
        response = sign_in(ohc_user).get(ORGANISATIONS_URL, {"status": "banana"})

        assert response.status_code == HTTPStatus.OK
        assert list(response.context["organisations"]) == [onboarded_organisation]

    def test_an_empty_register_does_not_claim_a_filter_hid_anyone(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        response = sign_in(ohc_user).get(ORGANISATIONS_URL)
        body = response.content.decode()

        assert not response.context["organisations"]
        assert "No vendors have signed up yet." in body

    def test_a_filter_that_matches_nobody_says_that_instead(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        response = sign_in(ohc_user).get(ORGANISATIONS_URL, {"q": "nobody"})
        body = response.content.decode()

        assert not response.context["organisations"]
        assert "No organisations match these filters." in body

    def test_an_htmx_filter_change_returns_only_the_results(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        response = sign_in(ohc_user).get(
            ORGANISATIONS_URL,
            {"q": "sunrise"},
            headers={"HX-Request": "true"},
        )
        body = response.content.decode()

        assert 'id="organisation-results"' in body
        assert 'id="ohc-nav"' not in body

    def test_the_detail_page_shows_the_profile_and_the_roster(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        owner_membership,
    ):
        organisation = owner_membership.organisation
        organisation.legal_name = "Sunrise Healthtech Private Limited"
        organisation.technical_contact_email = "dev@sunrise.in"
        organisation.save()

        response = sign_in(ohc_user).get(
            reverse("ohc:organisation", args=[organisation.slug]),
        )
        body = response.content.decode()

        assert "Sunrise Healthtech Private Limited" in body
        assert "dev@sunrise.in" in body
        assert "Meera Krishnan" in body
        assert "Owner" in body

    def test_an_unknown_slug_is_a_404(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        response = sign_in(ohc_user).get(
            reverse("ohc:organisation", args=["no-such-vendor"]),
        )

        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_a_roster_nobody_joined_says_so(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        response = sign_in(ohc_user).get(
            reverse("ohc:organisation", args=[onboarded_organisation.slug]),
        )

        assert "Nobody has joined this organisation yet." in response.content.decode()


class TestVerification:
    def test_verifying_stamps_the_date(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        response = sign_in(ohc_user).post(
            reverse(
                "ohc:organisation-verification",
                args=[onboarded_organisation.slug],
            ),
            {"status": Organisation.VerificationStatus.VERIFIED},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response.url == reverse(
            "ohc:organisation",
            args=[onboarded_organisation.slug],
        )
        onboarded_organisation.refresh_from_db()
        assert onboarded_organisation.is_verified
        assert onboarded_organisation.verified_at is not None
        assert "Sunrise Health Systems is now a verified vendor." in message_texts(
            response,
        )

    def test_moving_a_vendor_out_of_verified_clears_the_date(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        """A stale "Verified 3 Mar" under a rejected badge is a lie."""
        onboarded_organisation.set_verification(
            Organisation.VerificationStatus.VERIFIED,
        )

        sign_in(ohc_user).post(
            reverse(
                "ohc:organisation-verification",
                args=[onboarded_organisation.slug],
            ),
            {"status": Organisation.VerificationStatus.REJECTED},
        )

        onboarded_organisation.refresh_from_db()
        assert not onboarded_organisation.is_verified
        assert onboarded_organisation.verified_at is None

    def test_a_decision_can_go_back_to_pending(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        onboarded_organisation.set_verification(
            Organisation.VerificationStatus.REJECTED,
        )

        sign_in(ohc_user).post(
            reverse(
                "ohc:organisation-verification",
                args=[onboarded_organisation.slug],
            ),
            {"status": Organisation.VerificationStatus.PENDING},
        )

        onboarded_organisation.refresh_from_db()
        assert (
            onboarded_organisation.verification_status
            == Organisation.VerificationStatus.PENDING
        )

    def test_submitting_the_state_it_is_already_in_says_so(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        response = sign_in(ohc_user).post(
            reverse(
                "ohc:organisation-verification",
                args=[onboarded_organisation.slug],
            ),
            {"status": Organisation.VerificationStatus.PENDING},
        )

        assert "That vendor was already in that state." in message_texts(response)

    def test_a_junk_status_moves_nothing(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        response = sign_in(ohc_user).post(
            reverse(
                "ohc:organisation-verification",
                args=[onboarded_organisation.slug],
            ),
            {"status": "approved-ish"},
        )

        assert response.status_code == HTTPStatus.OK
        onboarded_organisation.refresh_from_db()
        assert (
            onboarded_organisation.verification_status
            == Organisation.VerificationStatus.PENDING
        )

    def test_an_htmx_decision_swaps_the_register_back(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        response = sign_in(ohc_user).post(
            reverse(
                "ohc:organisation-verification",
                args=[onboarded_organisation.slug],
            ),
            {"status": Organisation.VerificationStatus.VERIFIED},
            headers={"HX-Request": "true"},
        )
        body = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert 'id="organisation-register"' in body
        # The badge moved in the same swap as the rail.
        assert "Verified vendor" in body

    def test_the_vendor_sees_the_decision_on_their_own_settings_page(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        owner_membership,
    ):
        """The whole point of the decision — it is the vendor's badge that moves."""
        organisation = owner_membership.organisation
        sign_in(ohc_user).post(
            reverse("ohc:organisation-verification", args=[organisation.slug]),
            {"status": Organisation.VerificationStatus.VERIFIED},
        )

        response = sign_in(owner_membership.user).get(
            reverse("organisations:detail"),
        )

        assert "Verified vendor" in response.content.decode()


class TestEvents:
    def test_the_list_shows_drafts_and_published_alike(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        draft = Event.objects.create(
            title="Draft session",
            starts_at=timezone.now() + timedelta(days=3),
        )
        live = Event.objects.create(
            title="Live webinar",
            starts_at=timezone.now() + timedelta(days=4),
            published_at=timezone.now(),
        )

        response = sign_in(ohc_user).get(EVENTS_URL)

        assert set(response.context["events"]) == {draft, live}

    def test_creating_an_event_records_the_author_and_leaves_it_a_draft(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        response = sign_in(ohc_user).post(EVENT_CREATE_URL, EVENT_DATA)

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == EVENTS_URL
        event = Event.objects.get(title="Certification office hours")
        assert event.created_by == ohc_user
        assert event.published_at is None
        assert event.slug == "certification-office-hours"

    def test_an_end_before_the_start_is_refused(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        response = sign_in(ohc_user).post(
            EVENT_CREATE_URL,
            {**EVENT_DATA, "ends_at": "2026-09-01T09:00"},
        )

        assert response.status_code == HTTPStatus.OK
        assert "ends_at" in response.context["form"].errors
        assert not Event.objects.exists()

    def test_editing_keeps_the_slug_and_the_author(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        other_ohc_user: User,
    ):
        event = Event.objects.create(
            title="Upgrade webinar",
            starts_at=timezone.now() + timedelta(days=7),
            created_by=other_ohc_user,
        )

        response = sign_in(ohc_user).post(
            reverse("ohc:event-update", args=[event.slug]),
            {**EVENT_DATA, "title": "Upgrade webinar, rescheduled"},
        )

        assert response.status_code == HTTPStatus.FOUND
        event.refresh_from_db()
        assert event.title == "Upgrade webinar, rescheduled"
        assert event.slug == "upgrade-webinar"
        assert event.created_by == other_ohc_user

    def test_the_edit_form_prefills_the_datetime_picker(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        event: Event,
    ):
        """type="datetime-local" only shows a value in its own format."""
        response = sign_in(ohc_user).get(
            reverse("ohc:event-update", args=[event.slug]),
        )
        body = response.content.decode()
        expected = timezone.localtime(event.starts_at).strftime("%Y-%m-%dT%H:%M")

        assert 'type="datetime-local"' in body
        assert f'value="{expected}"' in body

    def test_publish_toggles_published_at_both_ways(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        event: Event,
    ):
        signed_in = sign_in(ohc_user)
        url = reverse("ohc:event-publish", args=[event.slug])

        response = signed_in.post(url)

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == EVENTS_URL
        event.refresh_from_db()
        assert event.published_at is not None
        assert event in Event.objects.published()

        signed_in.post(url)

        event.refresh_from_db()
        assert event.published_at is None
        assert event not in Event.objects.published()

    def test_publishing_is_what_puts_an_event_in_front_of_vendors(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        event: Event,
    ):
        """The console is the only door: a draft is invisible to every vendor."""
        assert event not in Event.objects.upcoming()

        sign_in(ohc_user).post(reverse("ohc:event-publish", args=[event.slug]))

        assert event in Event.objects.upcoming()

    def test_an_htmx_publish_returns_the_row_and_the_card(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        event: Event,
    ):
        response = sign_in(ohc_user).post(
            reverse("ohc:event-publish", args=[event.slug]),
            headers={"hx-request": "true"},
        )
        body = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert f'id="event-{event.pk}"' in body
        assert f'id="event-card-{event.pk}"' in body
        assert "Unpublish" in body


class TestLongTokensStayReadable:
    """Free text from a vendor has to wrap, not disappear.

    An API ticket is where someone pastes a callback URL, a request id or a
    JWT, and none of those carry a space for the browser to break at. Without
    `break-words` the console's cards clip them away (.ui-card is
    overflow-x: hidden) and the ticket subject drags the whole page past the
    viewport — measured at 806px against a 375px screen before this was fixed.

    Asserting the class is a proxy for a layout property only a browser can
    measure, but it is the property that broke, and it is the same convention
    the vendor's side of the thread already follows in
    support/partials/ticket_message.html.
    """

    UNBREAKABLE = "X-HIP-ID=aHR0cHM6Ly9zYW5kYm94LmFiZG0uZ292LmluL2dhdGV3YXkvdjM"

    def test_the_ticket_subject_and_thread_wrap(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
        vendor_user: User,
    ):
        ticket = Ticket.objects.create(
            organisation=onboarded_organisation,
            subject=self.UNBREAKABLE,
            created_by=vendor_user,
            linked_facility="HFR-KL-EKM-004219800000000000000000000",
        )
        post_reply(
            ticket,
            vendor_user,
            f"callbackUrl={self.UNBREAKABLE}",
            from_ohc_team=False,
        )

        response = sign_in(ohc_user).get(
            reverse("ohc:ticket", args=[ticket.reference]),
        )
        body = response.content.decode()

        # The three places a vendor's own unbreakable text lands.
        assert 'tracking-[-0.015em] break-words text-foreground">' in body
        assert 'leading-relaxed break-words text-foreground">' in body
        assert 'class="min-w-0 text-right font-medium break-words">' in body

    def test_the_stacked_queue_card_wraps_a_subject(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        """Below md there is no scrolling table to catch it."""
        Ticket.objects.create(
            organisation=onboarded_organisation,
            subject=self.UNBREAKABLE,
        )

        response = sign_in(ohc_user).get(QUEUE_URL, {"status": ""})
        body = response.content.decode()

        assert 'class="font-semibold break-words hover:underline"' in body

    def test_the_stacked_event_card_wraps_a_title(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        Event.objects.create(
            title="ReleaseNotes_2026_08_ABDM_M1_v3_consent_artefact_notify_migration",
            starts_at=timezone.now() + timedelta(days=3),
        )

        response = sign_in(ohc_user).get(EVENTS_URL)
        body = response.content.decode()

        assert 'class="font-semibold break-words hover:underline"' in body


class TestEmptyStatesAreHonest:
    """An empty queue may only make a claim about the query that ran."""

    def test_the_default_queue_says_nothing_is_waiting(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Already answered",
            status=Status.AWAITING_VENDOR,
        )

        response = sign_in(ohc_user).get(QUEUE_URL)
        body = response.content.decode()

        assert response.context["showing_default_queue"] is True
        assert "Nothing is waiting on the Care team right now." in body

    def test_a_narrowed_filter_never_claims_nothing_is_waiting(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        """Three tickets need a reply; a Closed+High filter finds none of them.

        Saying "nothing is waiting on the Care team" here would be false, and
        it is the sentence that sends someone away from a full queue.
        """
        for index in range(3):
            Ticket.objects.create(
                organisation=onboarded_organisation,
                subject=f"Waiting on us {index}",
                status=Status.OPEN,
            )

        response = sign_in(ohc_user).get(
            QUEUE_URL,
            {"status": Status.CLOSED, "priority": Priority.HIGH},
        )
        body = response.content.decode()

        assert not response.context["tickets"]
        assert response.context["showing_default_queue"] is False
        assert "No tickets match these filters." in body
        assert "Nothing is waiting on the Care team" not in body

    def test_an_assignee_filter_alone_also_narrows_the_claim(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        other_ohc_user: User,
        onboarded_organisation: Organisation,
    ):
        Ticket.objects.create(
            organisation=onboarded_organisation,
            subject="Nobody has looked at this",
            status=Status.OPEN,
        )

        response = sign_in(ohc_user).get(
            QUEUE_URL,
            {"assignee": str(other_ohc_user.pk)},
        )
        body = response.content.decode()

        assert not response.context["tickets"]
        assert "Nothing is waiting on the Care team" not in body

    def test_an_empty_event_list_does_not_invent_one(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
    ):
        response = sign_in(ohc_user).get(EVENTS_URL)
        body = response.content.decode()

        assert not response.context["events"]
        assert "No events yet." in body
        assert "0 events" in body

    def test_a_thread_with_no_messages_says_so(
        self,
        sign_in: Callable[[User], Client],
        ohc_user: User,
        ticket: Ticket,
    ):
        response = sign_in(ohc_user).get(
            reverse("ohc:ticket", args=[ticket.reference]),
        )

        assert not ticket.messages.exists()
        assert "This ticket has no messages yet." in response.content.decode()
