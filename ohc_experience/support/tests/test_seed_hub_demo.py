"""The demo seeder, pinned where it would hurt to be wrong.

Two things matter here. The first is that the dataset is honest: every status,
category and priority is represented, the response-time figures the console
prints are computed from timestamps that really sit in the past, and the
credentials the command prints actually sign in. The second is that re-running
it is safe — idempotent on the way in, and narrowly scoped on the way out, so
``--fresh`` can never reach past the rows this command authored.
"""

from __future__ import annotations

from http import HTTPStatus
from io import StringIO

import pytest
from allauth.account.models import EmailAddress
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from ohc_experience.events.models import Event
from ohc_experience.organisations.models import Organisation
from ohc_experience.support.management.commands.seed_hub_demo import DEFAULT_PASSWORD
from ohc_experience.support.management.commands.seed_hub_demo import (
    DEMO_ORGANISATION_SLUG,
)
from ohc_experience.support.models import Category
from ohc_experience.support.models import Priority
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import TicketMessage
from ohc_experience.users.models import User

pytestmark = pytest.mark.django_db

SEEDED_TICKETS = 8
SEEDED_EVENTS = 4
# Seven of the eight threads get an OHC reply; one is still waiting.
ANSWERED_TICKETS = 7
RESOLVED_TICKETS = 4
STATUS_CHANGE_ENTRIES = 6
UPCOMING_EVENTS = 2
PAST_EVENTS = 1
DRAFT_EVENTS = 1

OHC_EMAIL = "anand@ohc.network"
OWNER_EMAIL = "meera@arogyasystems.in"
DEVELOPER_EMAIL = "rahul@arogyasystems.in"


def seed(*args) -> str:
    out = StringIO()
    call_command("seed_hub_demo", *args, stdout=out)
    return out.getvalue()


def counts() -> tuple[int, ...]:
    return (
        Ticket.objects.count(),
        TicketMessage.objects.count(),
        Event.objects.count(),
        User.objects.count(),
        Organisation.objects.count(),
    )


class TestTheDataset:
    def test_it_covers_every_status_category_and_priority(self):
        seed()

        assert Ticket.objects.count() == SEEDED_TICKETS
        assert set(Ticket.objects.values_list("status", flat=True)) == set(
            Status.values,
        )
        assert set(Ticket.objects.values_list("category", flat=True)) == set(
            Category.values,
        )
        assert set(Ticket.objects.values_list("priority", flat=True)) == set(
            Priority.values,
        )

    def test_the_response_time_columns_are_populated(self):
        seed()

        assert (
            Ticket.objects.filter(first_responded_at__isnull=False).count()
            == ANSWERED_TICKETS
        )
        assert (
            Ticket.objects.filter(resolved_at__isnull=False).count() == RESOLVED_TICKETS
        )

    def test_the_threads_sit_in_the_past(self):
        """The whole reason the command rewrites the auto timestamps.

        If these read as "now" the console's median-first-response line would
        print zero for every ticket, which is a fabricated number dressed up as
        a real one.
        """
        seed()
        now = timezone.now()

        assert Ticket.objects.filter(created_at__gte=now).count() == 0
        for created, responded in Ticket.objects.filter(
            first_responded_at__isnull=False,
        ).values_list("created_at", "first_responded_at"):
            assert responded > created

    def test_a_resolved_ticket_was_resolved_after_it_was_opened(self):
        seed()

        for created, resolved in Ticket.objects.filter(
            resolved_at__isnull=False,
        ).values_list("created_at", "resolved_at"):
            assert resolved >= created

    def test_status_moves_are_recorded_in_the_thread(self):
        """Status is never assigned; it moves through record_status_change."""
        seed()

        events = TicketMessage.objects.filter(kind=TicketMessage.Kind.EVENT)

        assert events.count() == STATUS_CHANGE_ENTRIES
        # Every settled ticket carries the entry that settled it.
        for ticket in Ticket.objects.filter(
            status__in=[Status.RESOLVED, Status.CLOSED],
        ):
            assert ticket.messages.filter(kind=TicketMessage.Kind.EVENT).exists()

    def test_every_ticket_has_a_thread(self):
        seed()

        for ticket in Ticket.objects.all():
            assert ticket.messages.exists(), ticket.reference

    def test_the_events_split_into_upcoming_past_and_draft(self):
        seed()

        assert Event.objects.count() == SEEDED_EVENTS
        assert Event.objects.upcoming().count() == UPCOMING_EVENTS
        assert Event.objects.past().count() == PAST_EVENTS
        assert Event.objects.filter(published_at__isnull=True).count() == DRAFT_EVENTS


class TestTheCredentials:
    """The command prints a password and tells you where to use it.

    check_password() alone is not enough to know that claim is true:
    ACCOUNT_EMAIL_VERIFICATION is "mandatory", so an account with the right
    password but no verified EmailAddress is refused at the login form and
    handed a "verify your email" page instead. These tests drive the real
    allauth login view.
    """

    def test_the_printed_password_signs_in(self):
        output = seed()

        for email in (OHC_EMAIL, OWNER_EMAIL, DEVELOPER_EMAIL):
            assert email in output
            assert User.objects.get(email=email).check_password(DEFAULT_PASSWORD)

    def test_every_account_has_a_verified_address(self):
        seed()

        for email in (OHC_EMAIL, OWNER_EMAIL, DEVELOPER_EMAIL):
            address = EmailAddress.objects.get(email=email)
            assert address.verified is True
            assert address.primary is True

    @pytest.mark.parametrize(
        "email",
        [OHC_EMAIL, OWNER_EMAIL, DEVELOPER_EMAIL],
    )
    def test_each_account_can_actually_sign_in(self, client, email: str):
        seed()

        response = client.post(
            reverse("account_login"),
            {"login": email, "password": DEFAULT_PASSWORD},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response.wsgi_request.user.is_authenticated
        assert response.wsgi_request.user.email == email

    def test_a_rerun_leaves_the_account_able_to_sign_in(self, client):
        seed()

        seed()

        response = client.post(
            reverse("account_login"),
            {"login": OHC_EMAIL, "password": DEFAULT_PASSWORD},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response.wsgi_request.user.is_authenticated

    def test_the_seeded_ohc_account_reaches_the_console(self, client):
        """The end the command is actually selling: sign in, land on the queue."""
        seed()
        client.post(
            reverse("account_login"),
            {"login": OHC_EMAIL, "password": DEFAULT_PASSWORD},
        )

        response = client.get(reverse("ohc:queue"))

        assert response.status_code == HTTPStatus.OK

    def test_a_seeded_vendor_is_refused_the_console(self, client):
        seed()
        client.post(
            reverse("account_login"),
            {"login": OWNER_EMAIL, "password": DEFAULT_PASSWORD},
        )

        response = client.get(reverse("ohc:queue"))

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_the_ohc_account_can_reach_the_console_and_the_admin(self):
        seed()

        anand = User.objects.get(email=OHC_EMAIL)

        assert anand.is_ohc_team is True
        assert anand.is_staff is True
        assert anand.is_superuser is False

    def test_the_vendor_accounts_are_not_ohc_team(self):
        """A seeded vendor must not be able to open the OHC console."""
        seed()

        for email in (OWNER_EMAIL, DEVELOPER_EMAIL):
            assert User.objects.get(email=email).is_ohc_team is False
            assert User.objects.get(email=email).is_staff is False

    def test_an_override_password_is_the_one_that_works(self):
        seed("--password", "another-dev-password")

        assert User.objects.get(email=OHC_EMAIL).check_password(
            "another-dev-password",
        )
        assert not User.objects.get(email=OHC_EMAIL).check_password(DEFAULT_PASSWORD)

    def test_a_rerun_reasserts_the_printed_password(self):
        seed()
        anand = User.objects.get(email=OHC_EMAIL)
        anand.set_password("changed-by-hand")
        anand.save()

        seed()

        anand.refresh_from_db()
        assert anand.check_password(DEFAULT_PASSWORD)


class TestRerunning:
    def test_a_second_run_changes_nothing(self):
        seed()
        first = counts()

        seed()

        assert counts() == first

    def test_fresh_rebuilds_to_the_same_shape(self):
        seed()
        first = counts()

        seed("--fresh")

        assert counts() == first

    def test_fresh_on_an_empty_database_still_seeds(self):
        seed("--fresh")

        assert Ticket.objects.count() == SEEDED_TICKETS
        assert Event.objects.count() == SEEDED_EVENTS


class TestItRespectsExistingData:
    def test_an_existing_organisation_is_used_rather_than_a_new_one(self):
        existing = Organisation.objects.create(name="Sunrise Health Systems")

        seed()

        assert Organisation.objects.count() == 1
        assert existing.tickets.count() == SEEDED_TICKETS

    def test_no_vendor_accounts_are_invented_inside_an_operators_organisation(self):
        Organisation.objects.create(name="Sunrise Health Systems")

        seed()

        assert not User.objects.filter(email=OWNER_EMAIL).exists()
        assert not User.objects.filter(email=DEVELOPER_EMAIL).exists()
        assert User.objects.count() == 1

    def test_fresh_leaves_an_operators_own_organisation_standing(self):
        theirs = Organisation.objects.create(name="Sunrise Health Systems")
        seed()

        seed("--fresh")

        assert Organisation.objects.filter(pk=theirs.pk).exists()

    def test_fresh_does_not_reach_into_an_untargeted_organisation(self):
        """The blast radius of --fresh is the organisations it seeded into.

        A demo subject is distinctive but it is not reserved. An operator with
        their own ticket by that name, in an organisation this command never
        seeded into, must still have it afterwards — matching on the subject
        alone across the whole database would have taken it.
        """
        Organisation.objects.create(name="Alpha Health Systems")
        Organisation.objects.create(name="Beta Health Systems")
        # Third by created_at, so outside the earliest-two window the seeder
        # targets — this command never writes here.
        untargeted = Organisation.objects.create(name="Zenith Care Labs")
        seed()
        theirs = Ticket.objects.create(
            organisation=untargeted,
            subject="Rate limits on the sandbox FHIR endpoints",
        )

        seed("--fresh")

        assert Ticket.objects.filter(pk=theirs.pk).exists()
        assert untargeted.tickets.count() == 1

    def test_fresh_removes_the_organisation_this_command_created(self):
        seed()
        assert Organisation.objects.filter(slug=DEMO_ORGANISATION_SLUG).exists()

        seed("--fresh")

        # Rebuilt, not left behind from the previous run.
        assert Organisation.objects.filter(slug=DEMO_ORGANISATION_SLUG).count() == 1
        assert Organisation.objects.count() == 1


class TestTheReport:
    def test_it_names_the_organisations_and_the_counts(self):
        output = seed()

        assert "Arogya Systems" in output
        assert f"Tickets: {SEEDED_TICKETS} created" in output
        assert f"Events:  {SEEDED_EVENTS} created" in output
        assert DEFAULT_PASSWORD in output

    def test_a_rerun_reports_the_rows_as_already_present(self):
        seed()

        output = seed()

        assert f"Tickets: 0 created, {SEEDED_TICKETS} already present." in output
        assert f"Events:  0 created, {SEEDED_EVENTS} already present." in output
