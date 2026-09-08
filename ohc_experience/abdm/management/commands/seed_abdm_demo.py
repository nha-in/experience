"""Seed a clickable demo of the ABDM sandbox portal, both personas at once.

Development helper. It plays the portal the way its users do — every row is
written through ``abdm.services`` — so the history, the audit trail and the
notifications are exactly what the real flows produce. What it builds:

* the NHA certification desk: two reviewers and one portal admin;
* Medibase Health Systems, verified, with Medibase HMIS 4.2 registered on
  HI-CM and PHR — M1 approved, M2 under review, M3 in progress, M4 locked,
  PHR1 not started — with sandbox credentials, callback URLs and a fresh
  reachability check;
* six more organisations whose items fill the review queue: an exit request
  sent back, one with an open query, one brand new, one in review, a product
  registration and two organisation verifications;
* seven events (two already run, with recordings) and three support tickets.

Every row is keyed on a natural key — an email, an organisation slug, a
(organisation, product name) pair, an event slug, a (organisation, subject)
pair — so running the command twice leaves the database exactly as the first
run left it. Nothing is ever deleted unless ``--fresh`` is passed, and even
then only the rows this command knows it authored.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test.utils import override_settings
from django.utils import timezone

from ohc_experience.abdm import services
from ohc_experience.abdm.models import ComplianceRecord
from ohc_experience.abdm.models import Credential
from ohc_experience.abdm.models import Product
from ohc_experience.abdm.models import ReviewHistory
from ohc_experience.abdm.models import ReviewItem
from ohc_experience.events.models import Event
from ohc_experience.events.models import EventRegistration
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
DEFAULT_PASSWORD = "abdm-demo-pass-2026"  # noqa: S105

ADMIN_EMAIL = "admin@nha.gov.in"
ADMIN_NAME = "NHA portal admin"
REVIEWERS = (
    ("vaidya@nha.gov.in", "Anita Vaidya"),
    ("menon@nha.gov.in", "Rohan Menon"),
)
VAIDYA, MENON = (email for email, _name in REVIEWERS)

# A one-page PDF stub: enough for the upload validators and a download link.
PDF_STUB = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)

INTEGRATOR_DOMAIN = "medibase.in"
MEDIBASE_SLUG = "medibase-health-systems"
MEDIBASE_PRODUCT = "Medibase HMIS 4.2"
OWNER_EMAIL = f"rhea.nambiar@{INTEGRATOR_DOMAIN}"
OWNER_NAME = "Rhea Nambiar"
DEVELOPER_EMAIL = f"arjun.mehta@{INTEGRATOR_DOMAIN}"
DEVELOPER_NAME = "Arjun Mehta"


@dataclass(frozen=True)
class OrgSpec:
    """An organisation and its owner; ``verified_days_ago`` None = pending."""

    slug: str
    name: str
    owner_email: str
    owner_name: str
    description: str
    entity_type: str
    category: str
    website: str
    address: str
    pincode: str
    state: str
    district: str
    document_type: str
    document_number: str
    submitted_days_ago: int
    verified_days_ago: int | None = None
    extra_members: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class ExitSpec:
    """One milestone's exit request and how far the review got.

    outcome: draft | new | review | query | sent_back | approved.
    """

    track: str
    milestone: str
    outcome: str
    days_ago: int
    assignee: str = ""
    note: str = ""
    field_key: str = "functional_certificate"
    field_label: str = "Functional testing certificate"
    reply: str = ""


@dataclass(frozen=True)
class ProductSpec:
    org_slug: str
    name: str
    description: str
    category: str
    solution_type: str
    milestones: tuple[str, ...]
    registered_days_ago: int | None  # None → registration still in the queue
    exits: tuple[ExitSpec, ...] = ()
    callback_url: str = ""
    bridge_url: str = ""


@dataclass(frozen=True)
class EventSpec:
    slug: str
    title: str
    kind: str
    summary: str
    description: str
    starts_in_days: float
    duration_hours: float
    location: str = ""
    join_url: str = ""
    materials_url: str = ""
    recording_url: str = ""
    register_owner: bool = False


DESK = "desk"
INTEGRATOR = "integrator"


@dataclass(frozen=True)
class Turn:
    """One entry in a seeded thread, placed N hours after the ticket opened."""

    actor: str
    hours: float
    body: str = ""
    status: str = ""

    @property
    def from_desk(self) -> bool:
        return self.actor == DESK


@dataclass(frozen=True)
class TicketSpec:
    subject: str
    category: str
    priority: str
    opened_days_ago: int
    track: str
    turns: tuple[Turn, ...]


ORG_SPECS: tuple[OrgSpec, ...] = (
    OrgSpec(
        slug=MEDIBASE_SLUG,
        name="Medibase Health Systems Pvt Ltd",
        owner_email=OWNER_EMAIL,
        owner_name=OWNER_NAME,
        description=(
            "Hospital information system vendor serving 140 secondary-care "
            "hospitals across Karnataka and Tamil Nadu."
        ),
        entity_type=Organisation.EntityType.PRIVATE_COMPANY,
        category=Organisation.Category.INDIA_ENTITY,
        website="https://medibase.in",
        address=(
            "4th Floor, Prestige Tech Park, Marathahalli Outer Ring Road, Bengaluru"
        ),
        pincode="560103",
        state="Karnataka",
        district="Bengaluru Urban",
        document_type=Organisation.VerificationDocumentType.CIN,
        document_number="U72200KA2016PTC094321",
        submitted_days_ago=56,
        verified_days_ago=54,
        extra_members=((DEVELOPER_EMAIL, DEVELOPER_NAME, Role.DEVELOPER),),
    ),
    OrgSpec(
        slug="nidaan-labs",
        name="Nidaan Labs Pvt Ltd",
        owner_email="kiran.desai@nidaanlabs.in",
        owner_name="Kiran Desai",
        description="Laboratory information system for diagnostic chains.",
        entity_type=Organisation.EntityType.PRIVATE_COMPANY,
        category=Organisation.Category.INDIA_ENTITY,
        website="https://nidaanlabs.in",
        address="Plot 12, Baner Road, Pune",
        pincode="411045",
        state="Maharashtra",
        district="Pune",
        document_type=Organisation.VerificationDocumentType.PAN,
        document_number="AAACN4321L",
        submitted_days_ago=42,
        verified_days_ago=40,
    ),
    OrgSpec(
        slug="arogya-digital-health",
        name="Arogya Digital Health Pvt Ltd",
        owner_email="meera.pillai@arogyadigital.in",
        owner_name="Meera Pillai",
        description="Consumer PHR application with ABHA-linked record access.",
        entity_type=Organisation.EntityType.PRIVATE_COMPANY,
        category=Organisation.Category.INDIA_ENTITY,
        website="https://arogyadigital.in",
        address="Infopark Phase 2, Kakkanad, Kochi",
        pincode="682042",
        state="Kerala",
        district="Ernakulam",
        document_type=Organisation.VerificationDocumentType.CIN,
        document_number="U72900KL2019PTC058812",
        submitted_days_ago=70,
        verified_days_ago=68,
    ),
    OrgSpec(
        slug="swasthkart-technologies",
        name="SwasthKart Technologies",
        owner_email="aman.gupta@swasthkart.in",
        owner_name="Aman Gupta",
        description="End-user application for booking diagnostics and consultations.",
        entity_type=Organisation.EntityType.LLP,
        category=Organisation.Category.INDIA_ENTITY,
        website="https://swasthkart.in",
        address="Sector 62, Noida",
        pincode="201309",
        state="Uttar Pradesh",
        district="Gautam Buddha Nagar",
        document_type=Organisation.VerificationDocumentType.GSTIN,
        document_number="09AAECS1234F1Z5",
        submitted_days_ago=50,
        verified_days_ago=48,
    ),
    OrgSpec(
        slug="medledger-systems",
        name="MedLedger Systems",
        owner_email="farah.khan@medledger.in",
        owner_name="Farah Khan",
        description="Cloud HMIS for multi-speciality hospitals.",
        entity_type=Organisation.EntityType.PRIVATE_COMPANY,
        category=Organisation.Category.INDIA_ENTITY,
        website="https://medledger.in",
        address="HITEC City, Hyderabad",
        pincode="500081",
        state="Telangana",
        district="Rangareddy",
        document_type=Organisation.VerificationDocumentType.CIN,
        document_number="U72200TG2017PTC118844",
        submitted_days_ago=90,
        verified_days_ago=88,
    ),
    OrgSpec(
        slug="carebridge-health-tech",
        name="CareBridge Health Tech",
        owner_email="nithya.raman@carebridge.in",
        owner_name="Nithya Raman",
        description="Consumer health locker for lab reports and discharge summaries.",
        entity_type=Organisation.EntityType.PRIVATE_COMPANY,
        category=Organisation.Category.INDIA_ENTITY,
        website="https://carebridge.in",
        address="Anna Salai, Chennai",
        pincode="600002",
        state="Tamil Nadu",
        district="Chennai",
        document_type=Organisation.VerificationDocumentType.CIN,
        document_number="U72200TN2020PTC134455",
        submitted_days_ago=21,
        verified_days_ago=19,
    ),
    OrgSpec(
        slug="sunrise-diagnostics",
        name="Sunrise Diagnostics LLP",
        owner_email="anil.thomas@sunrisediagnostics.in",
        owner_name="Anil Thomas",
        description=(
            "Chain of 32 pathology laboratories across Kerala with a "
            "hospital-facing LIS."
        ),
        entity_type=Organisation.EntityType.LLP,
        category=Organisation.Category.INDIA_ENTITY,
        website="https://sunrisediagnostics.in",
        address="12 MG Road, Ravipuram, Kochi",
        pincode="682016",
        state="Kerala",
        district="Ernakulam",
        document_type=Organisation.VerificationDocumentType.GSTIN,
        document_number="32AAECS1234F1Z5",
        submitted_days_ago=2,
    ),
    OrgSpec(
        slug="zenith-health-tech",
        name="Zenith Health Tech Pvt Ltd",
        owner_email="priya.rao@zenithhealth.in",
        owner_name="Priya Rao",
        description=(
            "Telemedicine platform for tier-2 cities with 400 empanelled doctors."
        ),
        entity_type=Organisation.EntityType.PRIVATE_COMPANY,
        category=Organisation.Category.INDIA_ENTITY,
        website="https://zenithhealth.in",
        address="Plot 44, HITEC City, Hyderabad",
        pincode="500081",
        state="Telangana",
        district="Rangareddy",
        document_type=Organisation.VerificationDocumentType.CIN,
        document_number="U85100TG2019PTC132277",
        submitted_days_ago=1,
    ),
)

PRODUCT_SPECS: tuple[ProductSpec, ...] = (
    ProductSpec(
        org_slug=MEDIBASE_SLUG,
        name=MEDIBASE_PRODUCT,
        description=(
            "Clinical HMIS covering OPD, IPD, laboratory and pharmacy workflows, "
            "deployed on-premise and as SaaS."
        ),
        category=Product.Category.HMIS,
        solution_type=Product.SolutionType.CLINICAL_HMIS,
        milestones=(
            "HI-CM:M1",
            "HI-CM:M2",
            "HI-CM:M3",
            "HI-CM:M4",
            "PHR:M1",
            "PHR:PHR1",
        ),
        registered_days_ago=53,
        callback_url="https://hip.medibase.in/abdm/v3/callback",
        bridge_url="https://hip.medibase.in/abdm/v3",
        exits=(
            ExitSpec(
                "HI-CM",
                "M1",
                "approved",
                days_ago=35,
                assignee=VAIDYA,
                note=(
                    "All M1 flows demonstrated. WASA certificate and functional "
                    "testing report accepted without remarks. Production access "
                    "for M1 flows is issued outside the portal."
                ),
            ),
            ExitSpec("HI-CM", "M2", "review", days_ago=11, assignee=VAIDYA),
            ExitSpec("HI-CM", "M3", "draft", days_ago=3),
        ),
    ),
    ProductSpec(
        org_slug="nidaan-labs",
        name="Nidaan LIMS",
        description="Laboratory information management with ABHA-linked reports.",
        category=Product.Category.LMIS,
        solution_type=Product.SolutionType.CLINICAL_HMIS,
        milestones=("HI-CM:M1", "HI-CM:M2"),
        registered_days_ago=38,
        exits=(
            ExitSpec(
                "HI-CM",
                "M1",
                "sent_back",
                days_ago=14,
                assignee=VAIDYA,
                note=(
                    "WASA date precedes the sandbox testing window. The audit must "
                    "cover the build that was tested; resubmit with a certificate "
                    "dated on or after the testing start."
                ),
            ),
        ),
    ),
    ProductSpec(
        org_slug="arogya-digital-health",
        name="Arogya PHR",
        description="PHR application: ABHA login, consent listing, record viewing.",
        category=Product.Category.PHR_APP,
        solution_type=Product.SolutionType.EUA,
        milestones=("PHR:M1", "PHR:PHR1"),
        registered_days_ago=66,
        exits=(
            ExitSpec(
                "HI-CM",
                "M1",
                "approved",
                days_ago=21,
                assignee=MENON,
                note="Approved without remarks.",
            ),
            ExitSpec(
                "PHR",
                "PHR1",
                "query",
                days_ago=7,
                assignee=MENON,
                note=(
                    "The certificate lists ABHA login and consent listing only. "
                    "Record viewing and download are not named; confirm they were "
                    "part of the functional test run or attach the full certificate."
                ),
            ),
        ),
    ),
    ProductSpec(
        org_slug="swasthkart-technologies",
        name="SwasthKart EUA",
        description="End-user application on the UHI network.",
        category=Product.Category.OTHER,
        solution_type=Product.SolutionType.EUA,
        milestones=("UHI:UHI1",),
        registered_days_ago=45,
        exits=(ExitSpec("UHI", "UHI1", "new", days_ago=6),),
    ),
    ProductSpec(
        org_slug="medledger-systems",
        name="MedLedger HMIS",
        description="Cloud HMIS with HIP and HIU services.",
        category=Product.Category.HMIS,
        solution_type=Product.SolutionType.CLINICAL_HMIS,
        milestones=("HI-CM:M1", "HI-CM:M2", "HI-CM:M3"),
        registered_days_ago=85,
        exits=(
            ExitSpec(
                "HI-CM",
                "M1",
                "approved",
                days_ago=60,
                assignee=MENON,
                note="Approved without remarks.",
            ),
            ExitSpec(
                "HI-CM",
                "M2",
                "approved",
                days_ago=34,
                assignee=MENON,
                note="HIP flows demonstrated; approved.",
            ),
            ExitSpec("HI-CM", "M3", "review", days_ago=5, assignee=MENON),
        ),
    ),
    ProductSpec(
        org_slug="carebridge-health-tech",
        name="CareBridge Locker",
        description=(
            "Consumer health locker storing lab reports and discharge summaries "
            "on behalf of ABHA holders."
        ),
        category=Product.Category.HEALTH_LOCKER,
        solution_type=Product.SolutionType.HEALTH_LOCKER,
        milestones=("HealthLocker:PHR1", "PHR:M1"),
        registered_days_ago=None,
    ),
)

EVENT_SPECS: tuple[EventSpec, ...] = (
    EventSpec(
        slug="fhir-r4-profiles-workshop",
        title="FHIR R4 profiles workshop",
        kind=Event.Kind.WORKSHOP,
        summary="NRCES profiles for discharge summaries, prescriptions and reports.",
        description=(
            "A full day on the NRCES FHIR R4 profiles the sandbox validates "
            "against: bundle structure, the mandatory elements, and the "
            "mistakes that fail validation most often."
        ),
        starts_in_days=3,
        duration_hours=7,
        location="NHA office, New Delhi and online",
        join_url="https://meet.abdm.gov.in/fhir-workshop",
    ),
    EventSpec(
        slug="gateway-changes-pack-2026-09",
        title="Gateway changes in pack 2026.09",
        kind=Event.Kind.WEBINAR,
        summary="Callback header changes and the deprecation schedule.",
        description=(
            "The platform team walks through the September gateway pack: the "
            "new callback headers, the deprecated v2 endpoints and the dates."
        ),
        starts_in_days=10,
        duration_hours=1,
        join_url="https://meet.abdm.gov.in/gateway-2026-09",
    ),
    EventSpec(
        slug="sandbox-exit-clinic-wasa",
        title="Sandbox exit clinic: WASA and functional testing",
        kind=Event.Kind.OFFICE_HOURS,
        summary="For products preparing an M2 or M3 exit request.",
        description=(
            "The certification desk answers questions on the WASA certificate, "
            "the functional testing report and what the demo has to show."
        ),
        starts_in_days=16,
        duration_hours=1,
        join_url="https://meet.abdm.gov.in/exit-clinic",
        register_owner=True,
    ),
    EventSpec(
        slug="integration-clinic-care-context-linking",
        title="Integration clinic: care-context linking",
        kind=Event.Kind.OFFICE_HOURS,
        summary="For HIP integrators in M2; bring your own callback logs.",
        description=(
            "Hands-on debugging of care-context linking flows with the NHA and "
            "OHC Foundation engineers."
        ),
        starts_in_days=24,
        duration_hours=2,
        join_url="https://meet.abdm.gov.in/linking-clinic",
    ),
    EventSpec(
        slug="nhcx-claims-hackathon",
        title="NHCX claims hackathon",
        kind=Event.Kind.WORKSHOP,
        summary="Three days on claims exchange with NHA and IRDAI.",
        description=(
            "Build claims exchange flows against the NHCX sandbox with the NHA "
            "and IRDAI teams in the room. Applications close two weeks before."
        ),
        starts_in_days=31,
        duration_hours=54,
        location="Bengaluru",
    ),
    EventSpec(
        slug="m1-identity-deep-dive",
        title="M1 identity deep dive",
        kind=Event.Kind.WEBINAR,
        summary="ABHA creation, verification and profile flows end to end.",
        description="A recorded walkthrough of every M1 flow on the sandbox.",
        starts_in_days=-40,
        duration_hours=1.5,
        materials_url="https://sandbox.abdm.gov.in/docs/events/m1-deep-dive.pdf",
        recording_url="https://video.abdm.gov.in/m1-deep-dive",
    ),
    EventSpec(
        slug="sandbox-onboarding-session",
        title="Sandbox onboarding session",
        kind=Event.Kind.WEBINAR,
        summary="From sign-up to the first exit request.",
        description="How the portal works, for teams new to the sandbox.",
        starts_in_days=-12,
        duration_hours=1,
        recording_url="https://video.abdm.gov.in/onboarding-session",
    ),
)

TICKET_SPECS: tuple[TicketSpec, ...] = (
    TicketSpec(
        subject="Discharge summary bundle rejected with profile error 1002",
        category=Category.API,
        priority=Priority.HIGH,
        opened_days_ago=0,
        track="HI-CM",
        turns=(
            Turn(
                INTEGRATOR,
                0,
                "Our discharge summary bundle passes the public HAPI validator, "
                "but the sandbox gateway rejects it with error 1002 against "
                "DocumentReference.content.attachment. The message names the "
                "profile but not the constraint that failed, so we cannot tell "
                "whether the problem is the content type, the title or the size. "
                "Bundle and validator output are attached, both with synthetic data.",
            ),
            Turn(
                DESK,
                0.75,
                "The NRCES profile for discharge summaries constrains the "
                "attachment: contentType must be application/pdf and title is "
                "mandatory. Your bundle sends application/octet-stream with no "
                "title, which the public validator does not check because the "
                "ABDM profile is not loaded there. Validate against the ABDM "
                "profile pack before the next attempt.",
            ),
        ),
    ),
    TicketSpec(
        subject="Callback signature header rejected",
        category=Category.SANDBOX,
        priority=Priority.MEDIUM,
        opened_days_ago=20,
        track="HI-CM",
        turns=(
            Turn(
                INTEGRATOR,
                0,
                "Every callback from the gateway is rejected by our endpoint "
                "because the signature header does not match the documented name.",
            ),
            Turn(
                DESK,
                6,
                "The header was renamed in the 2026.08 pack; the docs lagged. "
                "The new name is in the callback reference now.",
            ),
            Turn(INTEGRATOR, 20, "Fixed on our side, thank you."),
            Turn(DESK, 22, status=Status.RESOLVED),
        ),
    ),
    TicketSpec(
        subject="Consent artefact expiry misread",
        category=Category.API,
        priority=Priority.LOW,
        opened_days_ago=34,
        track="HI-CM",
        turns=(
            Turn(
                INTEGRATOR,
                0,
                "Our HIU treats the consent artefact's dataEraseAt as the expiry "
                "and fetches records after the permission window has closed.",
            ),
            Turn(
                DESK,
                9,
                "permission.dateRange.to is the window that governs fetches; "
                "dataEraseAt is when you must delete what you fetched.",
            ),
            Turn(DESK, 30, status=Status.RESOLVED),
        ),
    ),
)

DEMO_ORG_SLUGS = [spec.slug for spec in ORG_SPECS]
DEMO_ACCOUNT_EMAILS = [
    ADMIN_EMAIL,
    *(email for email, _name in REVIEWERS),
    *(spec.owner_email for spec in ORG_SPECS),
    *(email for spec in ORG_SPECS for email, _n, _r in spec.extra_members),
]
DEMO_EVENT_SLUGS = [spec.slug for spec in EVENT_SPECS]
DEMO_TICKET_SUBJECTS = [spec.subject for spec in TICKET_SPECS]


@dataclass
class Report:
    accounts: list[tuple[str, str, str]] = field(default_factory=list)
    organisations_created: int = 0
    products_created: int = 0
    items_created: int = 0
    events_created: int = 0
    tickets_created: int = 0


class Command(BaseCommand):
    help = (
        "Seed a demo dataset for the ABDM sandbox portal: the NHA certification "
        "desk, a verified integrator with a product mid-way through HI-CM, a "
        "review queue with every kind of item, events and tickets. Safe to "
        "re-run — rows are matched on natural keys and never duplicated."
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
                "Delete the rows this command seeds (its accounts, organisations, "
                "products, review items, events and tickets) before seeding them "
                "again. Nothing else is touched."
            ),
        )

    def handle(self, *args, **options) -> None:
        # The services mail integrators and reviewers as they go; a seed run
        # should not, so the whole run uses a backend that drops mail.
        with override_settings(
            EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend",
        ):
            self._seed(options["password"], fresh=options["fresh"])

    @transaction.atomic
    def _seed(self, password: str, *, fresh: bool) -> None:
        report = Report()
        if fresh:
            self._delete_demo_data()
        admin, reviewers = self._ensure_desk(password, report)
        organisations = {
            spec.slug: self._ensure_organisation(spec, password, reviewers, report)
            for spec in ORG_SPECS
        }
        for spec in PRODUCT_SPECS:
            self._ensure_product(
                spec,
                organisations[spec.org_slug],
                admin=admin,
                reviewers=reviewers,
                report=report,
            )
        medibase = organisations[MEDIBASE_SLUG]
        self._ensure_events(reviewers[VAIDYA], medibase, report)
        self._ensure_tickets(medibase, reviewers[MENON], report)
        self._report(report, password)

    # -- destructive path, only ever reached via --fresh ------------------

    def _delete_demo_data(self) -> None:
        self.stdout.write(self.style.WARNING("--fresh: removing seeded rows."))
        organisations = Organisation.objects.filter(slug__in=DEMO_ORG_SLUGS)
        tickets, _ = Ticket.objects.filter(
            organisation__in=organisations,
            subject__in=DEMO_TICKET_SUBJECTS,
        ).delete()
        events, _ = Event.objects.filter(slug__in=DEMO_EVENT_SLUGS).delete()
        items, _ = ReviewItem.objects.filter(organisation__in=organisations).delete()
        products, _ = Product.objects.filter(organisation__in=organisations).delete()
        deleted_orgs, _ = organisations.delete()
        accounts, _ = User.objects.filter(email__in=DEMO_ACCOUNT_EMAILS).delete()
        self.stdout.write(
            f"  deleted {tickets} ticket row(s), {events} event row(s), "
            f"{items} review row(s), {products} product row(s), "
            f"{deleted_orgs} organisation row(s), {accounts} account row(s).",
        )

    # -- accounts -----------------------------------------------------------

    @staticmethod
    def _mark_email_verified(user) -> None:
        """ACCOUNT_EMAIL_VERIFICATION is mandatory: an unverified demo account
        could never sign in with the password this command prints."""
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )

    def _ensure_user(self, email: str, name: str, password: str, **flags):
        user, _created = User.objects.get_or_create(
            email=email,
            defaults={"name": name},
        )
        user.name = user.name or name
        for flag, value in flags.items():
            setattr(user, flag, value)
        user.set_password(password)
        user.save()
        self._mark_email_verified(user)
        return user

    def _ensure_desk(self, password: str, report: Report):
        admin = self._ensure_user(
            ADMIN_EMAIL,
            ADMIN_NAME,
            password,
            is_ohc_team=True,
            is_staff=True,
            is_superuser=True,
        )
        report.accounts.append((admin.email, admin.name, "NHA portal admin"))
        reviewers = {}
        for email, name in REVIEWERS:
            reviewer = self._ensure_user(
                email,
                name,
                password,
                is_ohc_team=True,
                is_staff=True,
            )
            reviewers[email] = reviewer
            report.accounts.append((reviewer.email, reviewer.name, "NHA reviewer"))
        return admin, reviewers

    # -- organisations -----------------------------------------------------

    def _ensure_organisation(self, spec: OrgSpec, password: str, reviewers, report):
        organisation = Organisation.objects.filter(slug=spec.slug).first()
        owner = self._ensure_user(spec.owner_email, spec.owner_name, password)
        report.accounts.append((owner.email, owner.name, f"{spec.name} — owner"))
        for email, name, role in spec.extra_members:
            member = self._ensure_user(email, name, password)
            role_label = Role(role).label.lower()
            report.accounts.append(
                (member.email, member.name, f"{spec.name} — {role_label}"),
            )
        if organisation is not None:
            return organisation

        organisation = Organisation.objects.create(
            slug=spec.slug,
            name=spec.name,
            description=spec.description,
            entity_type=spec.entity_type,
            category=spec.category,
            website=spec.website,
            registered_address=spec.address,
            pincode=spec.pincode,
            state=spec.state,
            district=spec.district,
            verification_document_type=spec.document_type,
            verification_document_number=spec.document_number,
            technical_contact_name=spec.owner_name,
            technical_contact_email=spec.owner_email,
            onboarded_at=timezone.now(),
        )
        organisation.verification_document.save(
            f"{spec.slug}-verification.pdf",
            ContentFile(PDF_STUB),
            save=True,
        )
        Membership.objects.create(
            organisation=organisation,
            user=owner,
            role=Role.OWNER,
        )
        for email, _name, role in spec.extra_members:
            Membership.objects.create(
                organisation=organisation,
                user=User.objects.get(email=email),
                role=role,
            )
        report.organisations_created += 1

        item = services.submit_organisation_for_verification(
            organisation=organisation,
            user=owner,
        )
        report.items_created += 1
        if spec.verified_days_ago is not None:
            reviewer = reviewers[VAIDYA]
            services.start_review(item=item, user=reviewer)
            services.approve(
                item=item,
                user=reviewer,
                approved_on=self._day(spec.verified_days_ago),
                note="Registry check passed; legal name and registered office match.",
            )
            Organisation.objects.filter(pk=organisation.pk).update(
                verified_at=self._when(spec.verified_days_ago),
            )
        self._backdate(item, spec.submitted_days_ago)
        Organisation.objects.filter(pk=organisation.pk).update(
            verification_submitted_at=self._when(spec.submitted_days_ago),
            created_at=self._when(spec.submitted_days_ago + 1),
        )
        organisation.refresh_from_db()
        return organisation

    # -- products and exit requests -----------------------------------------

    def _ensure_product(
        self,
        spec: ProductSpec,
        organisation,
        *,
        admin,
        reviewers,
        report: Report,
    ) -> None:
        if Product.objects.filter(organisation=organisation, name=spec.name).exists():
            return
        owner = organisation.owner
        product = services.register_product(
            organisation=organisation,
            user=owner,
            data={
                "name": spec.name,
                "description": spec.description,
                "category": spec.category,
                "solution_type": spec.solution_type,
                "milestones": list(spec.milestones),
            },
        )
        report.products_created += 1
        registration = product.review_items.get(
            item_type=ReviewItem.Type.PRODUCT_REGISTRATION,
        )
        report.items_created += 1
        if spec.registered_days_ago is not None:
            reviewer = reviewers[VAIDYA]
            services.start_review(item=registration, user=reviewer)
            services.approve(
                item=registration,
                user=reviewer,
                approved_on=self._day(spec.registered_days_ago),
                note="Tracks opened as applied for.",
            )
            self._backdate(registration, spec.registered_days_ago + 1)
            Product.objects.filter(pk=product.pk).update(
                created_at=self._when(spec.registered_days_ago + 1),
            )
        else:
            self._backdate(registration, 3)
        if spec.callback_url:
            self._set_callback(product, owner, spec)
        for exit_spec in spec.exits:
            self._play_exit(
                product,
                exit_spec,
                owner=owner,
                admin=admin,
                reviewers=reviewers,
                report=report,
            )

    def _set_callback(self, product, owner, spec: ProductSpec) -> None:
        credential = getattr(product, "credential", None)
        if credential is None:
            return
        services.update_callback_urls(
            product=product,
            user=owner,
            callback_url=spec.callback_url,
            bridge_url=spec.bridge_url,
        )
        # A fresh, healthy probe result without opening a socket.
        Credential.objects.filter(pk=credential.pk).update(
            callback_status_code=200,
            callback_latency_ms=340,
            callback_checked_at=timezone.now() - timedelta(minutes=2),
            callback_error="",
            callback_failure_streak=0,
        )

    def _play_exit(  # noqa: PLR0913
        self,
        product,
        spec: ExitSpec,
        *,
        owner,
        admin,
        reviewers,
        report: Report,
    ) -> None:
        record = ComplianceRecord.objects.get(
            product=product,
            track_code=spec.track,
            milestone_code=spec.milestone,
        )
        start = self._day(spec.days_ago + 24)
        record.start_date = start
        record.end_date = start + timedelta(days=22)
        record.demo_date = start + timedelta(days=21)
        if spec.outcome == "draft":
            record.status = ComplianceRecord.Status.IN_PROGRESS
            record.save()
            return
        record.wasa_agency = "Arcline Security Assurance"
        record.wasa_date = start + timedelta(days=20)
        record.functional_certificate.save(
            f"ft-certificate-{spec.milestone.lower()}.pdf",
            ContentFile(PDF_STUB),
            save=False,
        )
        record.functional_report.save(
            f"ft-report-{spec.milestone.lower()}.pdf",
            ContentFile(PDF_STUB),
            save=False,
        )
        record.save()
        item = services.request_exit(record=record, user=owner)
        report.items_created += 1
        reviewer = reviewers.get(spec.assignee)
        if reviewer is not None:
            services.assign_reviewer(item=item, actor=admin, assignee=reviewer)
            services.start_review(item=item, user=reviewer)
        if spec.outcome == "approved":
            services.approve(
                item=item,
                user=reviewer,
                approved_on=self._day(spec.days_ago - 4),
                note=spec.note,
            )
        elif spec.outcome == "sent_back":
            services.send_back(item=item, user=reviewer, reason=spec.note)
        elif spec.outcome == "query":
            services.raise_query(
                item=item,
                user=reviewer,
                field_key=spec.field_key,
                field_label=spec.field_label,
                question=spec.note,
            )
        self._backdate(item, spec.days_ago)

    # -- events and tickets ------------------------------------------------

    def _ensure_events(self, author, organisation, report: Report) -> None:
        owner = organisation.owner
        for spec in EVENT_SPECS:
            if Event.objects.filter(slug=spec.slug).exists():
                continue
            starts_at = timezone.now() + timedelta(days=spec.starts_in_days)
            event = Event.objects.create(
                slug=spec.slug,
                title=spec.title,
                kind=spec.kind,
                summary=spec.summary,
                description=spec.description,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=spec.duration_hours),
                location=spec.location,
                join_url=spec.join_url,
                materials_url=spec.materials_url,
                recording_url=spec.recording_url,
                published_at=timezone.now() - timedelta(days=45),
                created_by=author,
            )
            if spec.register_owner and owner is not None:
                EventRegistration.objects.get_or_create(event=event, user=owner)
            report.events_created += 1

    def _ensure_tickets(self, organisation, desk, report: Report) -> None:
        owner = organisation.owner
        product = Product.objects.filter(organisation=organisation).first()
        for spec in TICKET_SPECS:
            if Ticket.objects.filter(
                organisation=organisation,
                subject=spec.subject,
            ).exists():
                continue
            opened_at = timezone.now() - timedelta(days=spec.opened_days_ago, hours=3)
            ticket = Ticket.objects.create(
                organisation=organisation,
                subject=spec.subject,
                category=spec.category,
                priority=spec.priority,
                created_by=owner,
                assignee=desk,
                product=product,
                track=spec.track,
            )
            Ticket.objects.filter(pk=ticket.pk).update(created_at=opened_at)
            for turn in spec.turns:
                if turn.status:
                    message = record_status_change(ticket, desk, turn.status)
                else:
                    message = post_reply(
                        ticket,
                        desk if turn.from_desk else owner,
                        turn.body,
                        from_ohc_team=turn.from_desk,
                        notify=False,
                    )
                TicketMessage.objects.filter(pk=message.pk).update(
                    created_at=opened_at + timedelta(hours=turn.hours),
                )
            if ticket.first_responded_at is not None:
                Ticket.objects.filter(pk=ticket.pk).update(
                    first_responded_at=opened_at + timedelta(hours=spec.turns[1].hours),
                )
            report.tickets_created += 1

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _when(days_ago: float):
        return timezone.now() - timedelta(days=days_ago)

    @staticmethod
    def _day(days_ago: int) -> date:
        return timezone.localdate() - timedelta(days=days_ago)

    @staticmethod
    def _backdate(item: ReviewItem, days: int) -> None:
        """Move an item and its history into the past by ``days`` days.

        Everything the services wrote is stamped "now"; the demo needs a queue
        with real ages in it. Only the timestamps move — nothing else.
        """
        delta = timedelta(days=days)
        item.refresh_from_db()
        ReviewItem.objects.filter(pk=item.pk).update(
            submitted_on=item.submitted_on - delta,
            updated_at=item.updated_at - delta,
        )
        for history in ReviewHistory.objects.filter(item=item):
            ReviewHistory.objects.filter(pk=history.pk).update(
                created_at=history.created_at - delta,
            )
        for query in item.queries.all():
            item.queries.filter(pk=query.pk).update(raised_at=query.raised_at - delta)
        if item.compliance_id:
            record = item.compliance
            updates = {}
            if record.submitted_on:
                updates["submitted_on"] = record.submitted_on - delta
            if record.sent_back_on:
                updates["sent_back_on"] = record.sent_back_on - delta
            if updates:
                ComplianceRecord.objects.filter(pk=record.pk).update(**updates)

    def _report(self, report: Report, password: str) -> None:
        write = self.stdout.write
        write(self.style.SUCCESS("ABDM sandbox demo seeded."))
        write(
            f"  {report.organisations_created} organisation(s), "
            f"{report.products_created} product(s), {report.items_created} review "
            f"item(s), {report.events_created} event(s), "
            f"{report.tickets_created} ticket(s) created; existing rows kept.",
        )
        write(f"  Password for every account: {password}")
        seen = set()
        for email, name, role in report.accounts:
            if email in seen:
                continue
            seen.add(email)
            write(f"  {email:40} {name:22} {role}")
        write("  Integrator: /  ·  Certification desk: /assess/dashboard")
