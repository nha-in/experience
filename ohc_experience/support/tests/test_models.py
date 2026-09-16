"""Ticket rules the inbox, the thread and the OHC console all lean on.

These live at the model layer on purpose: post_reply() is the only sanctioned
way to move a ticket, so it is pinned here independently of any view that
calls it.
"""

from __future__ import annotations

import pytest

from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.support.models import Category
from ohc_experience.support.models import Priority
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import TicketMessage
from ohc_experience.support.models import post_reply
from ohc_experience.support.tests.factories import product_for
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def integrator():
    return UserFactory.create(name="Meera Krishnan", email="meera@sunrise.in")


@pytest.fixture
def nha_member():
    return UserFactory.create(
        name="Anand S",
        email="anand@ohc.network",
        is_nha_team=True,
    )


@pytest.fixture
def ticket(organisation, integrator) -> Ticket:
    return Ticket.objects.create(
        organisation=organisation,
        product=product_for(organisation),
        subject="Sandbox reset wiped our seeded patient records",
        category=Category.SANDBOX,
        priority=Priority.HIGH,
        created_by=integrator,
    )


def open_ticket(organisation, subject: str = "Another problem") -> Ticket:
    return Ticket.objects.create(
        organisation=organisation,
        product=product_for(organisation),
        subject=subject,
    )


class TestReference:
    def test_references_are_handed_out_in_order(self, organisation):
        first = open_ticket(organisation, "First")
        second = open_ticket(organisation, "Second")

        assert [first.reference, second.reference] == ["TKT-2001", "TKT-2002"]

    def test_a_deleted_ticket_does_not_hand_its_reference_on(self, organisation):
        open_ticket(organisation, "First")
        second = open_ticket(organisation, "Second")
        open_ticket(organisation, "Third")

        second.delete()
        fourth = open_ticket(organisation, "Fourth")

        assert fourth.reference == "TKT-2004"
        assert set(Ticket.objects.values_list("reference", flat=True)) == {
            "TKT-2001",
            "TKT-2003",
            "TKT-2004",
        }

    def test_a_reference_survives_an_edit(self, ticket: Ticket):
        original = ticket.reference

        ticket.subject = "Sandbox reset — follow up"
        ticket.save()
        ticket.refresh_from_db()

        assert ticket.reference == original

    def test_the_reference_reads_in_the_string_form(self, ticket: Ticket):
        assert str(ticket).startswith(f"{ticket.reference} — ")


class TestPostReply:
    def test_an_ohc_reply_puts_the_ticket_back_on_the_integrator(
        self,
        ticket: Ticket,
        nha_member,
    ):
        message = post_reply(
            ticket,
            nha_member,
            "The sandbox was refreshed on Monday.",
            from_nha_team=True,
        )
        ticket.refresh_from_db()

        assert ticket.status == Status.AWAITING_INTEGRATOR
        assert ticket.first_responded_at is not None
        assert message.kind == TicketMessage.Kind.REPLY
        assert message.from_nha_team is True

    def test_the_first_response_is_stamped_once_and_only_once(
        self,
        ticket: Ticket,
        nha_member,
        integrator,
    ):
        post_reply(ticket, nha_member, "Looking into it.", from_nha_team=True)
        ticket.refresh_from_db()
        stamped_at = ticket.first_responded_at

        post_reply(ticket, integrator, "Thanks.", from_nha_team=False)
        post_reply(ticket, nha_member, "Fixed on our side.", from_nha_team=True)
        ticket.refresh_from_db()

        assert ticket.first_responded_at == stamped_at

    def test_an_integrator_reply_reopens_the_ticket(
        self,
        ticket: Ticket,
        nha_member,
        integrator,
    ):
        post_reply(ticket, nha_member, "Any request ids?", from_nha_team=True)

        message = post_reply(
            ticket,
            integrator,
            "Request id 8f2c1a94.",
            from_nha_team=False,
        )
        ticket.refresh_from_db()

        assert ticket.status == Status.OPEN
        assert message.from_nha_team is False

    def test_an_integrator_reply_is_not_a_first_response(
        self,
        ticket: Ticket,
        integrator,
    ):
        post_reply(ticket, integrator, "Adding more detail.", from_nha_team=False)
        ticket.refresh_from_db()

        assert ticket.first_responded_at is None

    def test_the_thread_reads_oldest_first(
        self,
        ticket: Ticket,
        nha_member,
        integrator,
    ):
        post_reply(ticket, integrator, "First", from_nha_team=False)
        post_reply(ticket, nha_member, "Second", from_nha_team=True)
        post_reply(ticket, integrator, "Third", from_nha_team=False)

        assert list(ticket.messages.values_list("body", flat=True)) == [
            "First",
            "Second",
            "Third",
        ]


class TestResolving:
    def test_the_reply_is_followed_by_a_resolved_entry(
        self,
        ticket: Ticket,
        nha_member,
    ):
        message = post_reply(
            ticket,
            nha_member,
            "Restored from the nightly backup.",
            from_nha_team=True,
            resolve=True,
        )
        ticket.refresh_from_db()

        assert ticket.status == Status.CLOSED
        assert ticket.resolved_at is not None
        assert ticket.first_responded_at is not None
        assert message.kind == TicketMessage.Kind.REPLY
        assert list(ticket.messages.values_list("kind", "body", "from_nha_team")) == [
            (TicketMessage.Kind.REPLY, "Restored from the nightly backup.", True),
            (TicketMessage.Kind.EVENT, str(Status.CLOSED.label), True),
        ]

    def test_resolving_again_keeps_the_original_stamp(
        self,
        ticket: Ticket,
        nha_member,
        integrator,
    ):
        post_reply(
            ticket,
            nha_member,
            "Restored from backup.",
            from_nha_team=True,
            resolve=True,
        )
        ticket.refresh_from_db()
        resolved_at = ticket.resolved_at

        post_reply(ticket, integrator, "One is still missing.", from_nha_team=False)
        post_reply(
            ticket,
            nha_member,
            "Restored that one too.",
            from_nha_team=True,
            resolve=True,
        )
        ticket.refresh_from_db()

        assert ticket.resolved_at == resolved_at

    def test_a_plain_reply_does_not_count_as_resolving(
        self,
        ticket: Ticket,
        nha_member,
    ):
        post_reply(ticket, nha_member, "Looking into it.", from_nha_team=True)
        ticket.refresh_from_db()

        assert ticket.resolved_at is None
        assert not ticket.messages.filter(kind=TicketMessage.Kind.EVENT).exists()

    def test_an_integrator_can_resolve_their_own_ticket(
        self,
        ticket: Ticket,
        integrator,
    ):
        post_reply(
            ticket,
            integrator,
            "Our payload was malformed.",
            from_nha_team=False,
            resolve=True,
        )
        ticket.refresh_from_db()

        assert ticket.status == Status.CLOSED
        assert ticket.first_responded_at is None
        assert ticket.messages.get(kind=TicketMessage.Kind.EVENT).from_nha_team is False


class TestBadgeVariants:
    @pytest.mark.parametrize(
        ("status", "variant"),
        [
            (Status.OPEN, "info"),
            (Status.AWAITING_INTEGRATOR, "warning"),
            (Status.CLOSED, "neutral"),
        ],
    )
    def test_status_variant(self, status: str, variant: str):
        assert Ticket(status=status).status_variant == variant

    @pytest.mark.parametrize(
        ("status", "integrator_label", "queue_label"),
        [
            (Status.OPEN, "With NHA team", "Needs a reply"),
            (Status.AWAITING_INTEGRATOR, "Awaiting your reply", "Awaiting integrator"),
            (Status.CLOSED, "Resolved", "Resolved"),
        ],
    )
    def test_status_labels_say_whose_turn_it_is(
        self,
        status: str,
        integrator_label: str,
        queue_label: str,
    ):
        ticket = Ticket(status=status)

        assert ticket.get_status_display() == integrator_label
        assert ticket.queue_status_label == queue_label

    @pytest.mark.parametrize(
        ("priority", "variant"),
        [
            (Priority.HIGH, "destructive"),
            (Priority.MEDIUM, "warning"),
            (Priority.LOW, "neutral"),
        ],
    )
    def test_priority_variant(self, priority: str, variant: str):
        assert Ticket(priority=priority).priority_variant == variant

    def test_every_status_and_priority_has_a_variant(self):
        assert all(Ticket(status=value).status_variant for value in Status.values)
        assert all(Ticket(priority=value).priority_variant for value in Priority.values)

    @pytest.mark.parametrize(
        ("status", "is_open"),
        [
            (Status.OPEN, True),
            (Status.AWAITING_INTEGRATOR, True),
            (Status.CLOSED, False),
        ],
    )
    def test_is_open_covers_the_two_live_states(
        self,
        status: str,
        is_open: bool,  # noqa: FBT001
    ):
        assert Ticket(status=status).is_open is is_open


class TestTicketQuerySet:
    def test_for_organisation_keeps_one_integrator_in_view(self, organisation):
        mine = open_ticket(organisation, "Mine")
        other = OrganisationFactory.create(name="Arogya Systems")
        open_ticket(other, "Theirs")

        assert list(Ticket.objects.for_organisation(organisation)) == [mine]

    def test_open_only_drops_closed(self, organisation, nha_member):
        live = open_ticket(organisation, "Still going")
        closed = open_ticket(organisation, "Filed away")
        post_reply(
            closed,
            nha_member,
            "Sorted on the sandbox.",
            from_nha_team=True,
            resolve=True,
        )

        assert list(Ticket.objects.open_only()) == [live]

    def test_open_filter_lists_tickets_awaiting_reviewers(
        self,
        organisation,
        nha_member,
        integrator,
    ):
        waiting_on_us = open_ticket(organisation, "Integrator spoke last")
        post_reply(waiting_on_us, integrator, "Any news?", from_nha_team=False)
        waiting_on_them = open_ticket(organisation, "We spoke last")
        post_reply(waiting_on_them, nha_member, "Over to you.", from_nha_team=True)

        assert list(Ticket.objects.filter(status=Status.OPEN)) == [waiting_on_us]
