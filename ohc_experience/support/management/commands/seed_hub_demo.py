"""Seed a clickable demo of the vendor hub: an OHC team member, tickets, events.

Development helper. Every row is keyed on a natural key — an email, an
organisation slug, an (organisation, subject) pair, an event slug — so running
the command twice leaves the database exactly as the first run left it. Nothing
is ever deleted unless ``--fresh`` is passed, and even then only the rows this
command knows it authored.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ohc_experience.events.models import Event
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.support.models import Category
from ohc_experience.support.models import Priority
from ohc_experience.support.models import Status
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import TicketMessage
from ohc_experience.support.models import post_reply
from ohc_experience.support.models import record_status_change

User = get_user_model()

# Documented dev password. Override with --password; never used outside a
# developer machine, where the whole point is that the accounts are shareable.
DEFAULT_PASSWORD = "ohc-demo-pass-2026"  # noqa: S105

OHC_EMAIL = "anand@ohc.network"
OHC_NAME = "Anand S"

DEMO_ORGANISATION_NAME = "Arogya Systems"
DEMO_ORGANISATION_SLUG = "arogya-systems"
DEMO_OWNER_EMAIL = "meera@arogyasystems.in"
DEMO_OWNER_NAME = "Meera Krishnan"
DEMO_DEVELOPER_EMAIL = "rahul@arogyasystems.in"
DEMO_DEVELOPER_NAME = "Rahul Nair"

# Tickets are spread over at most this many organisations, so each one keeps a
# thread dense enough to be worth clicking through.
MAX_DEMO_ORGANISATIONS = 2

VENDOR = "vendor"
OHC = "ohc"


@dataclass(frozen=True)
class Turn:
    """One entry in a seeded thread, placed N hours after the ticket opened."""

    actor: str
    hours: float
    body: str = ""
    status: str = ""


@dataclass(frozen=True)
class TicketSpec:
    subject: str
    category: str
    priority: str
    opened_days_ago: int
    turns: list[Turn]
    linked_facility: str = ""
    assigned: bool = True


@dataclass(frozen=True)
class EventSpec:
    slug: str
    title: str
    kind: str
    summary: str
    description: str
    starts_in_days: float
    duration_hours: float
    published_days_ago: int | None = None
    location: str = ""
    join_url: str = ""


# Eight threads covering every status, every category and every priority, with
# opening dates spread over the last month so "median first response" has real
# numbers to chew on (3, 4, 5, 8, 11, 19 and 26 hours — one ticket is still
# waiting for its first reply, as one always is).
TICKET_SPECS: list[TicketSpec] = [
    TicketSpec(
        subject="Sandbox reset wiped our seeded patient records",
        category=Category.SANDBOX,
        priority=Priority.HIGH,
        opened_days_ago=3,
        linked_facility="Arogya Sandbox — Kozhikode District Hospital",
        turns=[
            Turn(
                VENDOR,
                0,
                "Our nightly sync ran at 02:00 and every patient we seeded last "
                "week has gone. Was the sandbox refreshed? We have a client demo "
                "on Thursday and need to know whether to reseed.",
            ),
            Turn(
                OHC,
                5,
                "The sandbox was refreshed on Monday as part of the 25.9 rollout. "
                "Seeded data does not survive a refresh — please reseed from your "
                "fixtures and we will give you the dates ahead of the next one.",
            ),
            Turn(
                VENDOR,
                30,
                "Reseeded, thank you. Where is the refresh schedule published? "
                "We would rather not find out from an empty database again.",
            ),
        ],
    ),
    TicketSpec(
        subject="ABHA link API returns 401 after a token refresh",
        category=Category.API,
        priority=Priority.HIGH,
        opened_days_ago=9,
        turns=[
            Turn(
                VENDOR,
                0,
                "Linking works on a fresh token, but every call after our refresh "
                "cycle comes back 401 invalid_token. Request id "
                "8f2c1a94-7f3d-4d0e-9d55-2f6a1c0b77aa if you want to trace it.",
            ),
            Turn(
                OHC,
                3,
                "Traced it — the refresh call is being sent to the v1 host while "
                "the link call goes to v2, and the two do not share a token. "
                "Point both at the v2 gateway and the 401s should stop.",
            ),
            Turn(
                VENDOR,
                20,
                "Moved both to v2. Linking holds now, but we are seeing the same "
                "401 on the consent fetch about once in twenty calls.",
            ),
            Turn(
                OHC,
                26,
                "That one is a clock skew on your side: tokens issued less than a "
                "second before use fail validation. Can you send the NTP offset "
                "on the box making the consent calls?",
            ),
        ],
    ),
    TicketSpec(
        subject="Which M1 flows need evidence in the submission pack?",
        category=Category.CERTIFICATION,
        priority=Priority.MEDIUM,
        opened_days_ago=21,
        turns=[
            Turn(
                VENDOR,
                0,
                "We are assembling the Milestone 1 pack. Do we need request and "
                "response captures for every ABHA flow, or only the four in the "
                "certification checklist?",
            ),
            Turn(
                OHC,
                19,
                "All six flows, with the REQUEST-ID visible in each capture. The "
                "checklist lists the four that are usually missed, not the full "
                "set — sorry, that is a wording problem on our side.",
            ),
            Turn(
                VENDOR,
                40,
                "Captured all six. One last thing: does the demo recording have "
                "to show the same environment as the captures?",
            ),
            Turn(
                OHC,
                44,
                "Yes — same sandbox, same day if you can manage it. Your pack "
                "looks complete otherwise, so go ahead and submit.",
            ),
            Turn(OHC, 60, status=Status.RESOLVED),
        ],
    ),
    TicketSpec(
        subject="Deployment checklist for the Kozhikode district rollout",
        category=Category.DEPLOYMENT,
        priority=Priority.MEDIUM,
        opened_days_ago=26,
        linked_facility="Kozhikode District Hospital",
        turns=[
            Turn(
                VENDOR,
                0,
                "We go live across eleven facilities next month. Is there a "
                "hardened checklist for a district-wide deployment, or do we "
                "work from the single-facility one?",
            ),
            Turn(
                OHC,
                8,
                "There is a district variant — attached to the deployment guide "
                "under 'multi-facility'. The differences are the shared master "
                "data load and the staggered user import.",
            ),
            Turn(
                VENDOR,
                30,
                "Found it. The staggered import assumes an HR feed we do not "
                "have; can we bulk import in one pass for eleven facilities?",
            ),
            Turn(
                OHC,
                33,
                "One pass is fine at that size. Keep the batches under 2,000 "
                "users and run them outside clinic hours.",
            ),
            Turn(OHC, 50, status=Status.RESOLVED),
            Turn(OHC, 120, status=Status.CLOSED),
        ],
    ),
    TicketSpec(
        subject="Invoice for the Q2 sandbox tier has the wrong GSTIN",
        category=Category.BILLING,
        priority=Priority.LOW,
        opened_days_ago=14,
        turns=[
            Turn(
                VENDOR,
                0,
                "The Q2 invoice carries our old GSTIN. Our finance team cannot "
                "process it — could you reissue against the number on our "
                "company profile?",
            ),
            Turn(
                OHC,
                26,
                "Reissued this morning against the GSTIN on file. The old "
                "invoice is voided, so please discard it.",
            ),
            Turn(VENDOR, 40, "Received and processed. Thank you."),
            Turn(OHC, 48, status=Status.RESOLVED),
        ],
    ),
    TicketSpec(
        subject="Rate limits on the sandbox FHIR endpoints",
        category=Category.API,
        priority=Priority.LOW,
        opened_days_ago=6,
        turns=[
            Turn(
                VENDOR,
                0,
                "Our load test tripped a 429 at roughly 40 requests a second on "
                "/Patient. What is the sandbox limit, and does production use "
                "the same ceiling?",
            ),
            Turn(
                OHC,
                11,
                "Sandbox is 30 requests a second per client, production is "
                "negotiated per deployment. Tell us the peak you expect in "
                "Kozhikode and we will size it with you.",
            ),
        ],
    ),
    TicketSpec(
        subject="HFR facility id does not resolve in the sandbox directory",
        category=Category.DEPLOYMENT,
        priority=Priority.HIGH,
        opened_days_ago=1,
        assigned=False,
        turns=[
            Turn(
                VENDOR,
                0,
                "Facility id IN2910000123 resolves on the HFR portal but returns "
                "'not found' from the sandbox directory lookup. Is the sandbox "
                "directory a stale snapshot?",
            ),
        ],
    ),
    TicketSpec(
        subject="Webhook retries during the upgrade window",
        category=Category.SANDBOX,
        priority=Priority.MEDIUM,
        opened_days_ago=30,
        turns=[
            Turn(
                VENDOR,
                0,
                "During last week's upgrade window our webhook endpoint received "
                "the same event fourteen times. Is that expected, or did our 503s "
                "trigger a retry storm?",
            ),
            Turn(
                OHC,
                4,
                "Expected, and blunter than it should be: we retry for two hours "
                "with a fixed one-minute gap. Make the handler idempotent on the "
                "event id and the duplicates become harmless.",
            ),
            Turn(
                VENDOR,
                12,
                "Handler is idempotent now. Is exponential backoff on the roadmap?",
            ),
            Turn(
                OHC,
                15,
                "It is — scheduled for the next platform release. We will note "
                "it in the changelog when it lands.",
            ),
            Turn(OHC, 20, status=Status.RESOLVED),
            Turn(OHC, 96, status=Status.CLOSED),
        ],
    ),
]

EVENT_SPECS: list[EventSpec] = [
    EventSpec(
        slug="care-api-office-hours",
        title="Care API office hours",
        kind=Event.Kind.OFFICE_HOURS,
        summary="Bring an integration problem, leave with an answer.",
        description=(
            "An open hour with the Care platform team. No agenda — queue up "
            "with whatever is blocking your integration and we work through it "
            "together."
        ),
        starts_in_days=4,
        duration_hours=1,
        published_days_ago=12,
        join_url="https://meet.ohc.network/office-hours",
    ),
    EventSpec(
        slug="care-25-9-upgrade-webinar",
        title="Upgrade webinar: what changes in Care 25.9",
        kind=Event.Kind.WEBINAR,
        summary="Breaking changes, the migration path, and the deprecation clock.",
        description=(
            "A walkthrough of the 25.9 release for vendor engineering teams: "
            "the encounter API changes, what the migration script does to your "
            "data, and the timeline for the deprecated v1 endpoints."
        ),
        starts_in_days=11,
        duration_hours=1.5,
        published_days_ago=6,
        join_url="https://meet.ohc.network/upgrade-webinar",
    ),
    EventSpec(
        slug="abdm-m1-certification-ama",
        title="ABDM M1 certification AMA",
        kind=Event.Kind.AMA,
        summary="The questions vendors ask most often before submitting M1.",
        description=(
            "The certification team answers the questions that come up in "
            "almost every M1 submission: what counts as evidence, how the "
            "sandbox differs from the certification environment, and what to "
            "do when a flow fails on the day."
        ),
        starts_in_days=-9,
        duration_hours=1,
        published_days_ago=30,
        join_url="https://meet.ohc.network/m1-ama",
    ),
    EventSpec(
        slug="district-deployment-workshop",
        title="Workshop: deploying Care across a district",
        kind=Event.Kind.WORKSHOP,
        summary="Draft — dates and venue still being confirmed.",
        description=(
            "A hands-on day for teams taking Care live across more than one "
            "facility: master data, staged user imports, and the fortnight of "
            "hypercare that follows."
        ),
        starts_in_days=25,
        duration_hours=6,
        location="Thiruvananthapuram",
    ),
]

DEMO_ACCOUNT_EMAILS = [OHC_EMAIL, DEMO_OWNER_EMAIL, DEMO_DEVELOPER_EMAIL]
DEMO_TICKET_SUBJECTS = [spec.subject for spec in TICKET_SPECS]
DEMO_EVENT_SLUGS = [spec.slug for spec in EVENT_SPECS]


@dataclass
class Account:
    email: str
    name: str
    role: str
    created: bool = False


@dataclass
class Report:
    accounts: list[Account] = field(default_factory=list)
    organisations: list[str] = field(default_factory=list)
    tickets_created: int = 0
    tickets_kept: int = 0
    events_created: int = 0
    events_kept: int = 0


class Command(BaseCommand):
    help = (
        "Seed a demo dataset for the vendor hub: an OHC team member, support "
        "tickets with real threads, and a handful of events. Safe to re-run — "
        "rows are matched on natural keys and never duplicated."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help="Password set on every demo account. Default: %(default)s",
        )
        parser.add_argument(
            "--fresh",
            action="store_true",
            help=(
                "Delete the rows this command seeds (its demo accounts, the "
                "Arogya Systems organisation, its tickets and its events) "
                "before seeding them again. Nothing else is touched."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        password = options["password"]
        report = Report()

        if options["fresh"]:
            self._delete_demo_data()

        ohc_user = self._ensure_ohc_member(password, report)
        organisations = self._ensure_organisations(password, report)
        self._ensure_tickets(organisations, ohc_user, report)
        self._ensure_events(ohc_user, report)
        self._report(report, password)

    # -- destructive path, only ever reached via --fresh ------------------

    def _delete_demo_data(self) -> None:
        self.stdout.write(self.style.WARNING("--fresh: removing seeded rows."))

        # Seeded tickets land in whichever organisations _target_organisations
        # picks, which may be organisations the operator brought themselves, so
        # the delete has to look in exactly those and nowhere else. Matching on
        # the subject alone would reach across the entire database and take a
        # real ticket with it if anyone had ever titled one "Rate limits on the
        # sandbox FHIR endpoints".
        tickets, _ = Ticket.objects.filter(
            organisation__in=self._target_organisations(),
            subject__in=DEMO_TICKET_SUBJECTS,
        ).delete()
        events, _ = Event.objects.filter(slug__in=DEMO_EVENT_SLUGS).delete()
        organisations, _ = Organisation.objects.filter(
            slug=DEMO_ORGANISATION_SLUG,
        ).delete()
        accounts, _ = User.objects.filter(email__in=DEMO_ACCOUNT_EMAILS).delete()

        self.stdout.write(
            f"  deleted {tickets} ticket row(s), {events} event row(s), "
            f"{organisations} organisation row(s), {accounts} account row(s).",
        )

    # -- accounts and organisations ---------------------------------------

    @staticmethod
    def _mark_email_verified(user) -> None:
        """Give the account a verified address, or it cannot sign in at all.

        ACCOUNT_EMAIL_VERIFICATION is "mandatory", so allauth refuses a login
        for a user with no confirmed EmailAddress row and tries to send a
        confirmation mail instead. A demo account whose printed password is
        rejected at the login form is worse than no demo account, so the
        address this command hands out is created already verified.
        """
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )

    def _ensure_ohc_member(self, password: str, report: Report):
        user, created = User.objects.get_or_create(
            email=OHC_EMAIL,
            defaults={"name": OHC_NAME, "is_ohc_team": True, "is_staff": True},
        )
        # Re-running has to leave the printed credentials true, so the flags and
        # the password are asserted every time rather than only on creation.
        user.name = user.name or OHC_NAME
        user.is_ohc_team = True
        user.is_staff = True
        user.set_password(password)
        user.save()
        self._mark_email_verified(user)
        report.accounts.append(
            Account(user.email, user.name, "OHC team (staff)", created=created),
        )
        return user

    @staticmethod
    def _target_organisations() -> list[Organisation]:
        """The organisations this command seeds into: the earliest few that exist.

        Both the seed and ``--fresh`` ask this question, and they have to get
        the same answer — otherwise --fresh would either leave seeded rows
        behind or reach past them into an operator's own data.
        """
        return list(
            Organisation.objects.order_by("created_at", "pk")[:MAX_DEMO_ORGANISATIONS],
        )

    def _ensure_organisations(
        self,
        password: str,
        report: Report,
    ) -> list[Organisation]:
        existing = self._target_organisations()
        if existing:
            report.organisations = [
                self._organisation_line(organisation, created=False)
                for organisation in existing
            ]
            # If a previous run's demo organisation is one of them, re-assert its
            # accounts so the credentials printed below stay true. Organisations
            # the operator brought themselves are left exactly as they are.
            for organisation in existing:
                if organisation.slug == DEMO_ORGANISATION_SLUG:
                    self._ensure_demo_members(organisation, password, report)
            return existing

        organisation = Organisation.objects.create(
            name=DEMO_ORGANISATION_NAME,
            slug=DEMO_ORGANISATION_SLUG,
            legal_name="Arogya Systems Private Limited",
            website="https://arogyasystems.in",
            city="Kochi",
            state="Kerala",
            deployment_regions="Kerala, Karnataka",
            technical_contact_name=DEMO_DEVELOPER_NAME,
            technical_contact_email=DEMO_DEVELOPER_EMAIL,
            technical_contact_phone="+91 98470 11223",
            verification_status=Organisation.VerificationStatus.VERIFIED,
            verified_at=timezone.now() - timedelta(days=40),
            onboarded_at=timezone.now() - timedelta(days=45),
        )
        self._ensure_demo_members(organisation, password, report)
        report.organisations = [self._organisation_line(organisation, created=True)]
        return [organisation]

    @staticmethod
    def _organisation_line(organisation: Organisation, *, created: bool) -> str:
        state = "created" if created else "already in the database"
        owner = organisation.owner
        owned_by = f", owner {owner.email}" if owner else ", no members yet"
        return f"{organisation.name} ({state}{owned_by})"

    def _ensure_demo_members(
        self,
        organisation: Organisation,
        password: str,
        report: Report,
    ) -> None:
        for email, name, role in (
            (DEMO_OWNER_EMAIL, DEMO_OWNER_NAME, Role.OWNER),
            (DEMO_DEVELOPER_EMAIL, DEMO_DEVELOPER_NAME, Role.DEVELOPER),
        ):
            self._ensure_vendor_member(
                organisation,
                email=email,
                name=name,
                role=role,
                password=password,
                report=report,
            )

    def _ensure_vendor_member(  # noqa: PLR0913
        self,
        organisation: Organisation,
        *,
        email: str,
        name: str,
        role: str,
        password: str,
        report: Report,
    ) -> None:
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"name": name},
        )
        user.name = user.name or name
        user.set_password(password)
        user.save()
        self._mark_email_verified(user)
        Membership.objects.get_or_create(
            organisation=organisation,
            user=user,
            defaults={"role": role},
        )
        report.accounts.append(
            Account(
                user.email,
                user.name,
                f"{organisation.name} — {Role(role).label.lower()}",
                created=created,
            ),
        )

    # -- tickets -----------------------------------------------------------

    def _ensure_tickets(
        self,
        organisations: list[Organisation],
        ohc_user,
        report: Report,
    ) -> None:
        for index, spec in enumerate(TICKET_SPECS):
            organisation = organisations[index % len(organisations)]
            if Ticket.objects.filter(
                organisation=organisation,
                subject=spec.subject,
            ).exists():
                report.tickets_kept += 1
                continue
            self._build_ticket(spec, organisation, ohc_user)
            report.tickets_created += 1

    def _build_ticket(
        self,
        spec: TicketSpec,
        organisation: Organisation,
        ohc_user,
    ) -> Ticket:
        opened_at = timezone.now() - timedelta(days=spec.opened_days_ago)
        vendor_users = [
            membership.user
            for membership in organisation.memberships.select_related("user").all()
        ]
        ticket = Ticket.objects.create(
            organisation=organisation,
            subject=spec.subject,
            category=spec.category,
            priority=spec.priority,
            created_by=vendor_users[0] if vendor_users else None,
            assignee=ohc_user if spec.assigned else None,
            linked_facility=spec.linked_facility,
        )
        # created_at/updated_at are auto fields, so the only way to place this
        # ticket in the past is to write the columns directly.
        Ticket.objects.filter(pk=ticket.pk).update(created_at=opened_at)
        ticket.created_at = opened_at

        vendor_turn = 0
        for turn in spec.turns:
            if turn.actor == OHC:
                author = ohc_user
            else:
                author = (
                    vendor_users[vendor_turn % len(vendor_users)]
                    if vendor_users
                    else None
                )
                vendor_turn += 1
            message = self._play_turn(ticket, turn, author)
            TicketMessage.objects.filter(pk=message.pk).update(
                created_at=self._turn_time(opened_at, turn),
            )

        self._stamp_ticket(ticket, spec, opened_at)
        return ticket

    def _play_turn(self, ticket: Ticket, turn: Turn, author) -> TicketMessage:
        """Move the ticket the same way a view would — never by assignment."""
        if turn.status:
            return record_status_change(ticket, author, turn.status)
        return post_reply(
            ticket,
            author,
            turn.body,
            from_ohc_team=turn.actor == OHC,
            notify=False,
        )

    @staticmethod
    def _turn_time(opened_at: datetime, turn: Turn) -> datetime:
        return opened_at + timedelta(hours=turn.hours)

    def _stamp_ticket(self, ticket: Ticket, spec: TicketSpec, opened_at) -> None:
        """Backdate the derived timestamps to when they would really have happened.

        post_reply() and record_status_change() stamp "now"; the seeded thread
        happened weeks ago, so the response-time figures on the console would
        otherwise all read as zero.
        """
        first_response = next(
            (
                self._turn_time(opened_at, turn)
                for turn in spec.turns
                if turn.actor == OHC and not turn.status
            ),
            None,
        )
        resolved = next(
            (
                self._turn_time(opened_at, turn)
                for turn in spec.turns
                if turn.status == Status.RESOLVED
            ),
            None,
        )
        last_activity = (
            self._turn_time(opened_at, spec.turns[-1]) if spec.turns else opened_at
        )
        Ticket.objects.filter(pk=ticket.pk).update(
            created_at=opened_at,
            updated_at=last_activity,
            first_responded_at=first_response,
            resolved_at=resolved,
        )

    # -- events ------------------------------------------------------------

    def _ensure_events(self, ohc_user, report: Report) -> None:
        now = timezone.now()
        for spec in EVENT_SPECS:
            starts_at = now + timedelta(days=spec.starts_in_days)
            published_at = (
                None
                if spec.published_days_ago is None
                else now - timedelta(days=spec.published_days_ago)
            )
            _, created = Event.objects.get_or_create(
                slug=spec.slug,
                defaults={
                    "title": spec.title,
                    "kind": spec.kind,
                    "summary": spec.summary,
                    "description": spec.description,
                    "starts_at": starts_at,
                    "ends_at": starts_at + timedelta(hours=spec.duration_hours),
                    "location": spec.location,
                    "join_url": spec.join_url,
                    "published_at": published_at,
                    "created_by": ohc_user,
                },
            )
            if created:
                report.events_created += 1
            else:
                report.events_kept += 1

    # -- output ------------------------------------------------------------

    def _report(self, report: Report, password: str) -> None:
        self.stdout.write(self.style.SUCCESS("Vendor hub demo data is ready."))
        self.stdout.write("")
        self.stdout.write("Organisations:")
        for line in report.organisations:
            self.stdout.write(f"  {line}")
        self.stdout.write(
            f"Tickets: {report.tickets_created} created, "
            f"{report.tickets_kept} already present.",
        )
        self.stdout.write(
            f"Events:  {report.events_created} created, "
            f"{report.events_kept} already present.",
        )
        self.stdout.write("")
        self.stdout.write("Seeded accounts (all share the password below):")
        width = max(len(account.email) for account in report.accounts)
        for account in report.accounts:
            state = "created" if account.created else "existing"
            self.stdout.write(
                f"  {account.email:<{width}}  {account.role}  [{state}]",
            )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Password: {password}"))
        self.stdout.write(
            "Sign in at /accounts/login/ — the OHC account also reaches /admin/.",
        )
