"""Building experience rows from legacy registrations."""

import secrets
import string
import uuid
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from ohc_experience.abdm.catalog import EXCLUSIVE_TRACKS
from ohc_experience.abdm.catalog import MILESTONES
from ohc_experience.abdm.catalog import OPTIONAL_MILESTONES
from ohc_experience.abdm.catalog import REQUIRED_MILESTONES
from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.abdm.forms import OrganisationForm
from ohc_experience.abdm.forms import ProductRegistrationForm
from ohc_experience.abdm.forms import UhiParticipationForm
from ohc_experience.abdm.forms import WasaReviewForm
from ohc_experience.experiences import legacy
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import ApplicationDependency
from ohc_experience.experiences.models import ApplicationFormUse
from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import CertificationAgency
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import FormRecord
from ohc_experience.experiences.models import FormReuseScope
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Milestone
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.secrets import cipher
from ohc_experience.experiences.services import form_field_schema
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisionedSystem
from ohc_experience.integrations.models import ProvisioningRun
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role

from . import clean
from . import files
from .organisations import read_integrators
from .timestamps import HistoricalTimestamps

#: `ApplicationStatusFilter` in legacy's enums, under the names this import uses:
#: legacy's SANDBOX APPROVED, EXIT REJECTED and PRODUCTION APPROVED are a
#: registration approved, an exit sent back and an exit approved.
APPROVED, REJECTED, PENDING = 1, 2, 0
EXIT_PENDING, EXIT_SENT_BACK, EXIT_APPROVED = 3, 4, 5
NHCX_PENDING, NHCX_APPROVED = 9, 10
#: Kept as `metadata["legacy_note"]` where a WASA record lacks what the portal's
#: own would carry.
WASA_UNDATED = (
    "Legacy dated WASA once per account, not per exit, so this certificate has no "
    "audit or expiry date."
)
WASA_NONE = "Legacy held no certificate for these dates."
WASA_LAPSED = (
    "Legacy recorded no dates for this certificate. A WASA runs a year, so the expiry "
    "is a year after the upload: the latest it could have been."
)
#: What the import could not read from legacy, kept on the product for the portal.
LEGACY_GAPS = legacy.LEGACY_GAPS
GAP_SOLUTION_TYPE = legacy.SOLUTION_TYPE
GAP_NHCX_ROLE = legacy.NHCX_ROLE
GAP_TRACKS = legacy.TRACKS
#: What a WASA approval shows on the product page.
WASA_FIELDS = [
    {"key": "wasa_agency", "label": "WASA audit agency"},
    {"key": "wasa_date", "label": "WASA audit date"},
    {"key": "wasa_valid_until", "label": "Valid until"},
]
#: One `sd_status` column per HTC member who left a remark.
HTC_COMMENTS = ("htc1_comment", "htc2_comment", "htc3_comment", "htc4_comment")
#: `Status.ACTIVE` in legacy's enums, beside inactive.
ACTIVE_LOGIN = "1"
#: How often a long run says where it is.
PROGRESS_EVERY = 250
#: Legacy's roles are `mst_role` rows, named there rather than fixed in its
#: code, and a Super Admin could add more, so the import reads the name.
INTEGRATOR_ROLE = "user"
SUPER_ADMIN_ROLE = "super admin"
REVIEWER_ROLE = "htc"
VIEW_ONLY_ROLE = "view"
#: The length of a bcrypt hash, which is how a legacy password is recognised.
BCRYPT_LENGTH = 60
#: Shown on the Credentials page, so it is the portal's words, not legacy's.
PRODUCTION_HANDOFF = (
    "Issued by the NHA gateway team. The production client ID appears on the "
    "Credentials page once it is recorded."
)
UNRECORDED_SOLUTION_TYPE = legacy.UNRECORDED_SOLUTION_TYPE
EVERY_OPTION_SOLUTION_TYPE = legacy.EVERY_OPTION_SOLUTION_TYPE
REFERENCE_ALPHABET = string.ascii_uppercase + string.digits


def filed_in_order(exits, fallback):
    """Exits oldest first, on the date legacy filed each."""
    return sorted(
        exits,
        key=lambda row: (
            row["created_date"] or row["created_at"] or fallback,
            row["id"],
        ),
    )


#: How far a reviewer has to look: a GSTIN or a login names the company outright,
#: what only it holds comes next, and everything else is worth reading.
def solution_by_milestones():
    """NHA's intent-for-request matrix backwards, where it names one type only.

    M1 to M3 is what seven types require, so it names none; the PHR sets and the
    payer's M1 and M3 each belong to one.
    """
    found = defaultdict(list)
    for solution, keys in REQUIRED_MILESTONES.items():
        found[frozenset(keys)].append(solution)
    return {keys: names[0] for keys, names in found.items() if len(names) == 1}


SOLUTION_BY_MILESTONES = solution_by_milestones()


def legacy_metadata(account):
    """What a product keeps of its legacy registration, gaps included."""
    metadata = {}
    if account.application_id:
        metadata["legacy_application_id"] = account.application_id
    if account.gaps:
        metadata[LEGACY_GAPS] = sorted(set(account.gaps))
    return metadata


def newest_brief(exits):
    """The most recent exit's summary of the organisation."""
    return next(
        (
            clean.paragraph(row["brief_on_organisation"])
            for row in reversed(exits)
            if clean.present(row["brief_on_organisation"])
        ),
        "",
    )


def uhi_answers(request):
    """A UHI request in the words the portal's form offers for legacy's."""
    roles = [
        clean.UHI_ROLES[token.lower()]
        for token in clean.tokens(request["intent_for_request"])
        if token.lower() in clean.UHI_ROLES
    ]
    services = [
        clean.UHI_SERVICES[token.lower()]
        for token in clean.tokens(request["type_of_service"])
        if token.lower() in clean.UHI_SERVICES
    ]
    return {
        "uhi_role": list(dict.fromkeys(roles)),
        "uhi_services": list(dict.fromkeys(services)),
        "uhi_tell_us_about": clean.paragraph(request["tell_us_about"]),
        "uhi_extra_details": clean.paragraph(request["extra_details"]),
    }


def newest_callback(exits):
    """The callback the newest exit saved.

    Legacy's `bridge_url` is the callback URL under another name: its own UHI
    service returned that column as `callbackUrl`.
    """
    return next(
        (
            clean.https_url(row["bridge_url"])
            for row in reversed(exits)
            if clean.https_url(row["bridge_url"])
        ),
        "",
    )


def newest_logo(exits):
    """The most recent exit's logo address."""
    return next(
        (
            clean.https_url(row["company_logo_url"])
            for row in reversed(exits)
            if clean.https_url(row["company_logo_url"])
        ),
        "",
    )


def stated_business(rows):
    """What a registration called its business, where no exit describes it."""
    return next(
        (
            clean.paragraph(row["business_type"])
            for row in rows
            if clean.present(row["business_type"])
        ),
        "",
    )


def distinct_words(rows, column):
    return sorted(
        {clean.text(row[column]) for row in rows if clean.present(row[column])},
    )


def legacy_words(rows):
    """Legacy's own words for what the portal's form never asks."""
    words = {
        "category": distinct_words(rows, "category"),
        "ownership": distinct_words(rows, "type_of_application"),
        "entity_type": distinct_words(rows, "entity_type"),
    }
    return {column: said for column, said in words.items() if said}


def staff_grants(role_name):
    """What a legacy staff role may review, read from its name.

    None where the name says nothing the portal can place, which the run reports
    rather than guessing at.
    """
    words = set(role_name.replace("_", " ").split())
    if role_name == SUPER_ADMIN_ROLE:
        return []
    if role_name == REVIEWER_ROLE:
        return [("*", True, True, True)]
    tracks = [code for code in TRACK_MAP if code.lower() in words]
    if tracks:
        return [(code, True, False, False) for code in tracks]
    if VIEW_ONLY_ROLE in words:
        return [("*", True, False, False)]
    return None


#: The portal's own words for the state legacy left a request in.
REVIEW_STATUS = {
    "approved": ReviewItem.Status.APPROVED,
    "rejected": ReviewItem.Status.REJECTED,
    "in_review": ReviewItem.Status.IN_REVIEW,
    "draft": ReviewItem.Status.DRAFT,
}
APPLICATION_STATUS = {
    "approved": "approved",
    "rejected": "draft",
    "in_review": "under_review",
    "draft": "draft",
}


@dataclass(frozen=True)
class Decision:
    """What legacy decided about one milestone request, and on which submission."""

    submission: object = None
    status: str = "draft"
    decided: object = None
    note: str = ""
    action: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Registration:
    """One legacy account, read once for the one or two products it becomes."""

    row: dict
    organisation: object
    user: object
    created: object
    decided: object
    status: dict
    approved: bool
    rejected: bool
    solutions: list
    other: str
    name: str
    description: str
    application_id: str
    production_id: str
    exits: list
    exit_keys: dict
    gaps: list


class Report:
    def __init__(self):
        self.counts = Counter()
        self.skipped = []
        self.review = defaultdict(list)
        self.mapping = []

    def skip(self, table, key, reason):
        self.counts[f"skipped {table}: {reason}"] += 1
        self.skipped.append((table, key, reason))

    def link(self, table, key, obj):
        self.mapping.append((table, key, obj._meta.label, obj.pk))  # noqa: SLF001

    def snapshot(self):
        return (
            self.counts.copy(),
            len(self.skipped),
            len(self.mapping),
            {name: len(rows) for name, rows in self.review.items()},
        )

    def restore(self, snapshot):
        counts, skipped, mapping, review = snapshot
        self.counts = counts
        del self.skipped[skipped:]
        del self.mapping[mapping:]
        for name, rows in self.review.items():
            del rows[review.get(name, 0) :]


class Importer:
    def __init__(self, legacy, *, store_files, stdout, password=""):
        self.legacy = legacy
        # Hashed once: 15,000 accounts through the hasher would cost minutes.
        self.shared_password = make_password(password) if password else ""
        self.files = files.FileStore(legacy, enabled=store_files)
        self.stdout = stdout
        self.report = Report()
        self.stamps = HistoricalTimestamps()
        self.now = timezone.now()
        self.references = set()
        self.production_holders = {}
        self.client_ids = set()
        self.staff_emails = set()
        self.roles = {}

    # ---------------------------------------------------------------- loading

    def newest_claims(self, rows):
        """Which registration keeps each production client id: the newest to claim it.

        Legacy let staff type one id against two registrations and the portal
        holds it once, so the older claims are listed for review instead.
        """
        claimed = defaultdict(list)
        for row in rows:
            client_id = clean.text(row["production_client_id"]).lower()
            if client_id:
                claimed[client_id].append(row)
        return {
            client_id: max(
                rows,
                key=lambda row: (clean.when(row["created_at"]), row["sd_id"]),
            )["sd_id"]
            for client_id, rows in claimed.items()
        }

    def load(self):
        self.logins = self.legacy.logins()
        self.production_holders = self.newest_claims(self.logins)
        self.roles = self.legacy.roles()
        self.statuses = self.legacy.statuses()
        self.addresses = self.legacy.addresses()
        self.exits = self.legacy.exits()
        self.declarations_by_id, self.declarations_by_login = self.legacy.declarations()
        self.documents = self.legacy.documents()
        self.nhcx = self.legacy.nhcx_decisions()
        self.wasa = self.legacy.wasa()
        self.uhi = self.legacy.uhi()
        self.enrolments = self.legacy.nhcx_enrolments()
        self.agencies = {
            name.lower(): name
            for name in CertificationAgency.objects.filter(program="abdm").values_list(
                "name",
                flat=True,
            )
        }
        self.schemas = {
            "organisation": form_field_schema(OrganisationForm()),
            "registration": form_field_schema(ProductRegistrationForm(initial={})),
            "exit": form_field_schema(
                ExitEvidenceForm(product=None, prefer_product_wasa=False),
            ),
            "wasa": form_field_schema(WasaReviewForm(product=None)),
            "uhi": form_field_schema(UhiParticipationForm()),
        }

    def role_name(self, row):
        return clean.text(self.roles.get(row["role_id"])).lower()

    def select_valid_integrator_users(self):
        registrations = []
        for row in self.logins:
            if self.role_name(row) != INTEGRATOR_ROLE:
                continue
            key = row["sd_id"]
            address = clean.email(row["email"])
            if "@" not in address:
                self.report.skip("sd_login", key, "no usable email")
                continue
            if address in self.staff_emails:
                self.report.skip("sd_login", key, "email belongs to a staff login")
                continue
            if clean.is_test_account(row["name"], address, row["organization"]):
                self.report.skip("sd_login", key, "test account")
                self.report.review["test accounts"].append({"sd_id": key})
                continue
            registrations.append(row)
        return registrations

    # ---------------------------------------------------------------- helpers

    def reference(self, prefix, when, length=6):
        while True:
            suffix = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(length))
            value = (
                f"{prefix}-{when:%y}-{suffix}" if prefix != "FORM" else f"FORM-{suffix}"
            )
            if value not in self.references:
                self.references.add(value)
                return value

    def created(self, row):
        return row["created_at"] or self.now

    def decision_time(self, row):
        """When the super admin settled this registration, or when it was filed."""
        status = self.statuses.get(row["sd_id"])
        if status and status["date"]:
            return clean.aware(status["date"])
        return self.created(row)

    def audit(  # noqa: PLR0913
        self,
        when,
        *,
        organisation,
        action,
        product=None,
        item=None,
        detail=None,
    ):
        return self.stamps.create(
            AuditEvent,
            when,
            organisation=organisation,
            product=product,
            item=item,
            actor=None,
            action=action,
            detail=detail or {},
        )

    # ---------------------------------------------------------------- people

    def password_for(self, row):
        if self.shared_password:
            self.report.counts["passwords set to the shared one"] += 1
            return self.shared_password
        value = clean.text(row["password"])
        if value.startswith(("$2a$", "$2b$", "$2y$")) and len(value) == BCRYPT_LENGTH:
            return f"bcrypt${value}"
        self.report.counts["passwords set unusable"] += 1
        return make_password(None)

    def create_user(self, rows, **flags):
        newest = max(
            rows,
            key=lambda row: (clean.when(row["created_at"]), row["sd_id"]),
        )
        joined = min(
            (row["created_at"] for row in rows if row["created_at"]),
            default=self.now,
        )
        user_model = get_user_model()
        number = clean.mobile(newest["mobile"])
        user = user_model(
            email=clean.email(newest["email"]),
            name=clean.text(newest["name"])[:255],
            phone_number=number,
            # Legacy signed the account up on this number as it did on the email
            phone_verified=bool(number),
            password=self.password_for(newest),
            is_active=clean.text(newest["status"]) == ACTIVE_LOGIN,
            date_joined=joined,
            **flags,
        )
        user.save()
        EmailAddress.objects.create(
            user=user,
            email=user.email,
            verified=True,
            primary=True,
        )
        for row in rows:
            self.report.link("sd_login", row["sd_id"], user)
        return user

    def import_staff(self):
        by_email = defaultdict(list)
        for row in self.logins:
            name = self.role_name(row)
            if name == INTEGRATOR_ROLE:
                continue
            if staff_grants(name) is None:
                self.report.skip(
                    "sd_login",
                    row["sd_id"],
                    f"role {name or row['role_id']} not imported",
                )
                continue
            address = clean.email(row["email"])
            if "@" not in address:
                self.report.skip(
                    "sd_login",
                    row["sd_id"],
                    "staff login is not an email",
                )
                continue
            by_email[address].append(row)
        for rows in by_email.values():
            newest = max(
                rows,
                key=lambda row: (clean.when(row["created_at"]), row["sd_id"]),
            )
            name = self.role_name(newest)
            with transaction.atomic():
                user = self.create_user(
                    rows,
                    is_nha_team=True,
                    is_superuser=name == SUPER_ADMIN_ROLE,
                    is_staff=name == SUPER_ADMIN_ROLE,
                )
                for category, read, write, approve in staff_grants(name) or []:
                    AccessGrant.objects.create(
                        user=user,
                        program="abdm",
                        area=AccessGrant.Area.REVIEW,
                        category=category,
                        can_read=read,
                        can_write=write,
                        can_approve=approve,
                    )
            self.report.counts["staff users"] += 1
        self.staff_emails = set(by_email)

    def import_users(self, registrations):
        by_email = defaultdict(list)
        for row in registrations:
            by_email[clean.email(row["email"])].append(row)
        users = {}
        for address, rows in by_email.items():
            with transaction.atomic():
                users[address] = self.create_user(rows)
        self.report.counts["integrator users"] += len(users)
        return users

    # ---------------------------------------------------------------- organisations

    def verification_status(self, integrator, verified_at):
        """Verified once legacy approved a registration, rejected when it never did."""
        if verified_at:
            return Organisation.VerificationStatus.VERIFIED
        rejected = all(
            (self.statuses.get(row["sd_id"]) or {}).get("final_status") == REJECTED
            for row in integrator.rows
        )
        if rejected:
            return Organisation.VerificationStatus.REJECTED
        return Organisation.VerificationStatus.PENDING

    def entity_slug(self, integrator, lead, enrolment):
        """The form choice legacy's words name. Blank when none of them fit."""
        if integrator.is_person:
            return "sole_proprietor"
        raw = clean.text(lead["entity_type"]) or clean.text(
            enrolment.get("entity_type"),
        )
        entity = clean.entity_type(raw)
        if entity:
            return entity
        if clean.present(raw):
            self.report.review["entity types with no form choice"].append(
                {"sd_id": lead["sd_id"], "entity_type": raw},
            )
        else:
            self.report.review["organisations with no entity type"].append(
                {"sd_id": lead["sd_id"]},
            )
        return ""

    def import_all(self, registrations, users, limit=None):
        integrators = read_integrators(registrations)
        integrators.sort(
            key=lambda integrator: (
                clean.when(integrator.rows[0]["created_at"]),
                integrator.rows[0]["sd_id"],
            ),
        )
        if limit:
            integrators = integrators[:limit]
        with self.stamps.enabled():
            for index, integrator in enumerate(integrators, start=1):
                self.import_isolated(integrator, users)
                if index % PROGRESS_EVERY == 0:
                    self.stdout.write(f"  {index} of {len(integrators)} organisations")

    def import_isolated(self, integrator, users):
        """Roll back and report an organisation that fails, then carry on."""
        before = self.report.snapshot()
        sd_ids = " ".join(str(row["sd_id"]) for row in integrator.rows)
        try:
            with transaction.atomic():
                self.import_integrator(integrator, users)
        except Exception as error:  # noqa: BLE001 - the command fails after the report.
            self.report.restore(before)
            self.report.counts["organisations failed"] += 1
            self.report.review["failed organisations"].append(
                {"sd_ids": sd_ids, "error": f"{type(error).__name__}: {error}"},
            )
        else:
            self.report.counts["organisations"] += 1

    def import_integrator(self, integrator, users):
        rows = integrator.rows
        approved = [
            row
            for row in rows
            if self.statuses.get(row["sd_id"], {}).get("final_status") == APPROVED
        ]
        lead = (approved or rows)[-1]
        first = rows[0]
        founded = self.created(first)
        if integrator.is_person:
            name = clean.text(lead["name"]) or clean.email(lead["email"]).split("@")[0]
        else:
            name = clean.text(lead["organization"])
        verified_at = min((self.decision_time(row) for row in approved), default=None)
        location = self.addresses.get(lead["sd_id"]) or {}
        enrolment = self.enrolments.get(lead["sd_id"]) or {}
        organisation = self.stamps.create(
            Organisation,
            founded,
            name=name[:255],
            legal_name=name[:255],
            entity_type=self.entity_slug(integrator, lead, enrolment),
            website=clean.website(lead["website"], enrolment.get("website"))
            if not integrator.is_person
            else "",
            city=clean.present(
                location.get("district_name") or enrolment.get("district_name"),
            )[:120],
            state=clean.state_name(
                location.get("state_name") or enrolment.get("state_name"),
            )[:120],
            verification_status=self.verification_status(integrator, verified_at),
            verified_at=verified_at,
            onboarded_at=founded,
        )
        owner = clean.email(first["email"])
        for address in dict.fromkeys(clean.email(row["email"]) for row in rows):
            self.stamps.create(
                Membership,
                founded,
                organisation=organisation,
                user=users[address],
                role=Role.OWNER if address == owner else Role.ADMIN,
            )
        integrator_exits = [
            exit_row for row in rows for exit_row in self.exits.get(row["sd_id"], [])
        ]
        self.import_verification(
            organisation,
            integrator,
            lead,
            users[owner],
            verified_at,
            integrator_exits,
        )
        for row in rows:
            self.import_product(organisation, row, users[clean.email(row["email"])])

    def import_verification(  # noqa: PLR0913, PLR0917
        self,
        organisation,
        integrator,
        lead,
        owner,
        verified_at,
        integrator_exits,
    ):
        founded = organisation.created_at
        record = self.stamps.create(
            FormRecord,
            founded,
            reference=self.reference("FORM", founded, 16),
            form_key="sandbox_organisation",
            name="Organisation verification",
            reuse_scope=FormReuseScope.ORGANISATION,
            organisation=organisation,
            product=None,
            metadata={"program": "abdm", "legacy": legacy_words(integrator.rows)},
            created_by=owner,
        )
        data = self.verification_answers(
            organisation,
            integrator,
            lead,
            integrator_exits,
        )
        decision, decided, note = self.verification_decision(
            organisation,
            integrator,
            verified_at,
        )
        filed = decision != "draft"
        submitted = verified_at or founded
        submission = self.stamps.create(
            FormSubmission,
            submitted,
            form=record,
            origin_application=None,
            form_key=record.form_key,
            status="completed" if filed else "needs_changes",
            data=data,
            field_schema=self.schemas["organisation"],
            metadata={"complete": filed, "imported": True},
            schema_version=1,
            revision=1,
            submission_number=1,
            is_current=True,
            submitted_by=owner,
        )
        self.attach_verification_document(
            submission,
            integrator,
            integrator_exits,
            owner,
            gst=data["verification_document_number"],
        )
        item = self.stamps.create(
            ReviewItem,
            founded,
            kind=ReviewItem.Kind.ORGANISATION,
            organisation=organisation,
            product=None,
            application=None,
            form=record,
            selected_submission=submission,
            status=REVIEW_STATUS[decision],
            submitted_at=submitted if filed else None,
            decided_at=decided,
            decided_by=None,
            decision_note=note,
        )
        if decided:
            self.audit(
                decided,
                organisation=organisation,
                item=item,
                action="Approved" if decision == "approved" else "Rejected",
                detail={"note": note, "submission_id": submission.pk},
            )
        self.report.counts[f"organisations {organisation.verification_status}"] += 1

    def verification_decision(self, organisation, integrator, verified_at):
        """What legacy decided about this organisation, in the portal's words.

        Legacy decided one application, which the portal keeps as a product
        registration and an organisation verification, so one rejection settles
        both. A registration nobody decided is still waiting; one that holds no
        status row at all was never put in front of anyone.
        """
        states = Organisation.VerificationStatus
        if organisation.verification_status == states.VERIFIED:
            return "approved", verified_at, ""
        if organisation.verification_status == states.REJECTED:
            newest = integrator.rows[-1]
            status = self.statuses.get(newest["sd_id"]) or {}
            return (
                "rejected",
                self.decision_time(newest),
                clean.paragraph(status.get("admin_comment")),
            )
        waiting = any(row["sd_id"] in self.statuses for row in integrator.rows)
        return ("in_review" if waiting else "draft"), None, ""

    def verification_answers(self, organisation, integrator, lead, integrator_exits):
        """The organisation form, as the registration and its exits answer it."""
        location = self.addresses.get(lead["sd_id"]) or {}
        enrolment = self.enrolments.get(lead["sd_id"]) or {}
        oldest_first = sorted(
            integrator_exits,
            key=lambda row: row["created_date"] or row["created_at"] or self.now,
        )
        abroad = (
            clean.text(lead["register_india_status"])
            or clean.text(enrolment.get("registered_in_india_status"))
        ).lower() == "no"
        gst = clean.gstin(
            lead["gst_no"],
            enrolment.get("gst_no"),
            *(row["gstn_id"] for row in integrator_exits),
        )
        return {
            "name": organisation.name,
            "description": newest_brief(oldest_first)
            or clean.paragraph(lead["ecosystem"])
            or stated_business(integrator.rows)
            or organisation.name,
            "entity_type": organisation.entity_type,
            "category": "foreign" if abroad else "india",
            "website": organisation.website,
            "logo": newest_logo(oldest_first),
            "registered_address": clean.paragraph(
                location.get("complete_address")
                or lead["address"]
                or enrolment.get("registered_address"),
            ),
            "pincode": clean.pincode(location.get("pin_code")),
            "state": organisation.state,
            "district": organisation.city,
            "state_lgd_code": "",
            "district_lgd_code": "",
            "verification_document_type": "GSTIN" if gst else "",
            "verification_document_number": gst,
        }

    def attach_verification_document(
        self,
        submission,
        integrator,
        integrator_exits,
        owner,
        *,
        gst,
    ):
        """The GSTIN certificate an exit carried, else the registration's own."""
        document = files.verification_document(
            integrator_exits,
            self.documents,
            "GSTIN" if gst else "",
            [
                files.registration_certificate(row)
                for row in integrator.rows
                if row["has_certificate"]
            ],
        )
        if not document:
            return
        kept = self.attach(
            submission,
            "supporting_document",
            document,
            owner,
            single=True,
        )
        if kept and document.table == "sd_login":
            self.report.counts["registration certificates as the document"] += 1

    # ---------------------------------------------------------------- files

    def attach(self, submission, field_key, source, user, *, single):
        stored = self.files.store(source) if self.files.enabled else None
        if stored is None:
            return None
        attachment = self.stamps.create(
            FormAttachment,
            source.created_at or submission.submitted_at,
            submission=submission,
            field_key=field_key,
            file=stored.name,
            original_name=stored.original_name,
            content_type=stored.content_type,
            size=stored.size,
            is_current=True,
            uploaded_by=user,
        )
        entry = {
            "attachment_id": attachment.pk,
            "name": attachment.original_name,
            "size": attachment.size,
        }
        if single:
            submission.data[field_key] = entry
        else:
            submission.data.setdefault(field_key, []).append(entry)
        FormSubmission.objects.filter(pk=submission.pk).update(data=submission.data)
        self.report.counts["attachments"] += 1
        return attachment

    # ---------------------------------------------------------------- products

    def solution_choice(self, row, enrolment, keys):
        """The types to record, and the words that go in the Other box."""
        dumped = clean.every_option(
            row["solution_type"],
            enrolment.get("solution_type"),
        )
        if dumped:
            self.report.counts["solution type was the whole option list"] += 1
            solutions, unlisted = [], []
        else:
            solutions, unlisted = clean.solution_types(
                row["solution_type"],
                enrolment.get("solution_type"),
            )
        for name in unlisted:
            self.report.counts[f"solution type moved to Other: {name}"] += 1
        other = clean.other_solution_type(unlisted, row["solution_type_others"])
        if other and "other" not in solutions:
            solutions.append("other")
        solutions = solutions or ["other"]
        if solutions == ["other"] and not other:
            derived = self.solution_from_milestones(keys)
            if derived:
                self.report.counts["solution type read from the milestones"] += 1
                return [derived], ""
            other = EVERY_OPTION_SOLUTION_TYPE if dumped else UNRECORDED_SOLUTION_TYPE
        return solutions, other

    def import_product(self, organisation, row, user):
        sd_id = row["sd_id"]
        status = self.statuses.get(sd_id) or {}
        approved = status.get("final_status") == APPROVED
        rejected = status.get("final_status") == REJECTED
        created = self.created(row)
        decided = self.decision_time(row)
        enrolment = self.enrolments.get(sd_id) or {}
        exits = filed_in_order(self.exits.get(sd_id, []), created)
        exit_keys = self.resolve_exit_milestones(row, exits)
        keys, gaps = self.milestones_and_gaps(row, exits, exit_keys)
        solutions, other = self.solution_choice(row, enrolment, keys)
        if other in (EVERY_OPTION_SOLUTION_TYPE, UNRECORDED_SOLUTION_TYPE):
            gaps.append(GAP_SOLUTION_TYPE)
        newest_exit = exits[-1] if exits else {}
        name = (
            clean.present(row["product_name"])
            or clean.present(enrolment.get("product_name"))
            or clean.present(newest_exit.get("product_name"))
            or organisation.name
        )
        description = (
            clean.paragraph(row["ecosystem"])
            or clean.paragraph(newest_exit.get("brief_on_organisation"))
            or "Imported from the legacy ABDM sandbox."
        )
        account = Registration(
            row=row,
            organisation=organisation,
            user=user,
            created=created,
            decided=decided,
            status=status,
            approved=approved,
            rejected=rejected,
            solutions=solutions,
            other=other,
            name=name,
            description=description,
            application_id=clean.text(row["application_id"]),
            production_id=self.production_client_id(row),
            exits=exits,
            exit_keys=exit_keys,
            gaps=gaps,
        )
        self.build_product(account, clean.ordered_milestones(keys))

    def production_client_id(self, row):
        """Legacy's production client id, which only its newest claim keeps."""
        client_id = clean.text(row["production_client_id"])
        if not client_id:
            return ""
        if self.production_holders.get(client_id.lower()) != row["sd_id"]:
            self.report.review["duplicate production client ids"].append(
                {"sd_id": row["sd_id"]},
            )
            return ""
        return client_id

    def milestones_and_gaps(self, row, exits, exit_keys):
        """The product's milestones, and what the import had to read into them.

        NHCX is a choice of role legacy never recorded, and a product holding both
        exclusive tracks loses one of them, so each is kept as a gap the portal
        can show.
        """
        keys = self.milestones_of(row, exits, exit_keys)
        gaps = []
        roles = keys & set(clean.NHCX_ROLES)
        if roles:
            role = self.nhcx_role(row, keys - roles)
            if not role:
                self.report.counts["NHCX role not named, read as provider"] += 1
                gaps.append(GAP_NHCX_ROLE)
                role = clean.NHCX_DEFAULT_ROLE
            keys = (keys - roles) | {role}
            self.file_nhcx_exits_under(exit_keys, role)
        tracks = {clean.MILESTONE_TRACK[key] for key in keys} & set(EXCLUSIVE_TRACKS)
        if len(tracks) == len(EXCLUSIVE_TRACKS):
            self.report.counts["products holding both exclusive tracks"] += 1
            gaps.append(GAP_TRACKS)
        return keys, gaps

    def file_nhcx_exits_under(self, exit_keys, role):
        """Move the NHCX exits onto the role the product holds.

        An exit says NHCX and never which role, so it reads as a provider. A
        payer would then have its approval looking for an exit nobody filed.
        """
        for exit_id, keys in exit_keys.items():
            if any(key in clean.NHCX_ROLES for key in keys):
                exit_keys[exit_id] = list(
                    dict.fromkeys(
                        role if key in clean.NHCX_ROLES else key for key in keys
                    ),
                )

    def milestones_of(self, row, exits, exit_keys):
        """Every milestone the account declared, filed an exit for, or applied to."""
        sd_id = row["sd_id"]
        keys = {
            key
            for declaration in self.declarations_by_login.get(sd_id, [])
            for key in clean.milestone_keys(declaration["complete_mil"])
        } or set(self.registration_tracks(row))
        for exit_row in exits:
            keys.update(exit_keys.get(exit_row["id"], []))
        if sd_id in self.uhi:
            keys.add("uhi1")
        if sd_id in self.nhcx or sd_id in self.enrolments:
            keys.add(clean.NHCX_DEFAULT_ROLE)
        return keys

    def nhcx_role(self, row, keys):
        """The NHCX role legacy's own words name, or blank when none do.

        A payer category or the Payers track names a payer, the Providers track a
        provider, and PHR milestones alone a patient app.
        """
        enrolment = self.enrolments.get(row["sd_id"]) or {}
        if clean.names_a_payer(enrolment.get("payer_category")):
            return "nhcx_payer"
        words = set()
        for value in (row["field_detail"], row["solution_type"]):
            said = frozenset(token.lower() for token in clean.tokens(value))
            if (
                said not in clean.WHOLE_TRACK_LISTS
                and said not in clean.LEGACY_OPTION_SETS
            ):
                words |= said
        if "payers" in words:
            return "nhcx_payer"
        if "providers" in words:
            return "nhcx_provider"
        if clean.present(row["category"]).lower() == "insurance":
            return "nhcx_payer"
        if keys & {"p1", "p2", "p3", "p4"} and not keys & {"m1", "m2", "m3", "m4"}:
            return "nhcx_patient_app"
        return ""

    def solution_from_milestones(self, keys):
        """The type whose required milestones are exactly the ones claimed."""
        claimed = frozenset(
            key
            for key in keys
            if clean.MILESTONE_TRACK.get(key) in EXCLUSIVE_TRACKS
            and key not in OPTIONAL_MILESTONES
        )
        return SOLUTION_BY_MILESTONES.get(claimed, "")

    def build_product(self, account, keys):
        """The one product this registration becomes, with every milestone it holds."""
        row = account.row
        created, decided = account.created, account.decided
        production_id = account.production_id
        name = account.name
        applied = [f"{clean.MILESTONE_TRACK[key]}:{key}" for key in keys]
        product = self.stamps.create(
            Product,
            created,
            organisation=account.organisation,
            name=name[:255],
            description=account.description,
            metadata=legacy_metadata(account),
            created_by=account.user,
            experience_type="abdm",
            solution_type=account.solutions,
            applied_milestones=applied,
            registered_at=decided,
            production_client_id=production_id[:255],
            production_issued_on=clean.ist_date(row["upload_time"])
            if production_id
            else None,
            production_recorded_at=row["upload_time"] if production_id else None,
            updated_at=row["updated_at"] or created,
        )
        self.report.link("sd_login", row["sd_id"], product)
        registration = self.import_registration(
            product,
            account,
            applied,
            self.registration_note(account.status),
        )
        if account.approved:
            self.import_credentials(product, registration, account)
        elif account.rejected:
            self.report.counts["products left without a client, rejected"] += 1
        else:
            self.report.counts["products awaiting provisioning"] += 1
        if production_id:
            self.audit(
                row["upload_time"] or decided,
                organisation=account.organisation,
                product=product,
                action="Production client ID recorded",
                detail={"client_id": production_id},
            )
        self.import_milestones(
            product,
            account.user,
            row,
            keys,
            account.exits,
            account.exit_keys,
        )
        if row["sd_id"] in self.wasa:
            self.import_wasa(
                product,
                account.user,
                self.wasa[row["sd_id"]],
                account.exits,
            )
        self.report.counts["products"] += 1

    def registration_tracks(self, row):
        """Before the self-declaration, the registration form asked for the tracks.

        Never for NHCX: legacy opened that form only once M1 was approved, and no
        registration names it. Payers and Providers are solution types, and
        reading them as NHCX roles would apply on the integrator's behalf.
        """
        picked = clean.tokens(row["field_detail"])
        for token in picked:
            if token.lower() not in clean.REGISTRATION_TRACKS:
                self.report.counts[f"registration track not read: {token}"] += 1
        if frozenset(token.lower() for token in picked) in clean.WHOLE_TRACK_LISTS:
            self.report.counts["registration tracks that were the whole list"] += 1
            return []
        tracks = [
            key
            for key in clean.registration_tracks(row["field_detail"])
            if key not in clean.NHCX_ROLES
        ]
        if tracks:
            self.report.counts["products given their registration tracks"] += 1
        return tracks

    def registration_note(self, status):
        """The reviewer's remark, then each HTC member's, as they wrote them."""
        remarks = [clean.paragraph(status.get("admin_comment"))]
        remarks += [
            f"HTC: {note}"
            for column in HTC_COMMENTS
            if (note := clean.paragraph(status.get(column)))
        ]
        return "\n".join(dict.fromkeys(remark for remark in remarks if remark))

    def import_registration(self, product, account, applied, note):
        """The registration the portal keeps, under the decision legacy made on it."""
        created, decided, user = account.created, account.decided, account.user
        decision = "rejected" if account.rejected else "approved"
        application = self.stamps.create(
            ApplicationInstance,
            created,
            reference=self.reference("REG", created),
            application_type="abdm_sandbox_product",
            title="ABDM product registration",
            product=product,
            created_by=user,
            status=APPLICATION_STATUS[decision],
            metadata={},
            submitted_at=created,
            decided_at=decided,
            decided_by=None,
            updated_at=decided,
        )
        record = self.stamps.create(
            FormRecord,
            created,
            reference=self.reference("FORM", created, 8),
            form_key="sandbox_product_registration",
            name="Product registration",
            reuse_scope=FormReuseScope.PRODUCT,
            organisation=product.organisation,
            product=product,
            metadata={"schema_version": 1},
            created_by=user,
        )
        submission = self.stamps.create(
            FormSubmission,
            created,
            form=record,
            origin_application=application,
            form_key=record.form_key,
            status="completed",
            data={
                "name": product.name,
                "description": product.description,
                "solution_type": account.solutions,
                "solution_type_other": account.other,
                "applied_milestones": applied,
            },
            field_schema=self.schemas["registration"],
            metadata={"complete": True, "imported": True},
            schema_version=1,
            revision=1,
            submission_number=1,
            is_current=True,
            submitted_by=user,
        )
        self.stamps.create(
            ApplicationFormUse,
            created,
            application=application,
            form=record,
            form_key=record.form_key,
            selected_submission=submission,
        )
        item = self.stamps.create(
            ReviewItem,
            created,
            kind=ReviewItem.Kind.PRODUCT,
            organisation=product.organisation,
            product=product,
            application=application,
            form=record,
            selected_submission=submission,
            status=REVIEW_STATUS[decision],
            submitted_at=created,
            decided_at=decided,
            decided_by=None,
            decision_note=note,
        )
        self.audit(
            decided,
            organisation=product.organisation,
            product=product,
            item=item,
            action="Rejected" if account.rejected else "Recorded",
            detail={
                "submission_id": submission.pk,
                "note": note or "Imported from the legacy ABDM sandbox.",
            },
        )
        self.report.counts[f"registrations {decision}"] += 1
        if note:
            self.report.counts["registration notes"] += 1
        return application

    def import_credentials(self, product, registration, account):
        """The client legacy issued, as the portal's own provisioned rows."""
        row, status, decided, exits = (
            account.row,
            account.status,
            account.decided,
            account.exits,
        )
        client_id = clean.text(status.get("client_id"))
        if not client_id or client_id.upper() in {"NA", "N/A"}:
            self.report.review["approved without a client id"].append(
                {"sd_id": row["sd_id"]},
            )
            return
        if client_id.lower() in self.client_ids:
            self.report.review["duplicate sandbox client ids"].append(
                {"sd_id": row["sd_id"]},
            )
            return
        secret = clean.text(status.get("gen_securate"))
        if not secret:
            # Resources without a credential would read as provisioned and the
            # cutover sweep would pass them by, leaving nobody to issue one.
            self.report.review["approved without a stored secret"].append(
                {"sd_id": row["sd_id"]},
            )
            return
        self.client_ids.add(client_id.lower())
        # Legacy named both the Keycloak client and the WSO2 application after the
        # client id. No bridge: a bridge row means the integrator saved a callback.
        for system in (ProvisionedSystem.KEYCLOAK, ProvisionedSystem.WSO2):
            self.stamps.create(
                ProvisionedResource,
                decided,
                product=product,
                system=system,
                external_ref=client_id,
                public_ref=client_id,
                secret_ref="",
            )
        self.stamps.create(
            ProvisioningRun,
            decided,
            product=product,
            status=ProvisioningRun.Status.READY,
            correlation_id=f"legacy-{uuid.uuid4().hex[:12]}",
            started_by=None,
            finished_at=decided,
        )
        callback = newest_callback(exits)
        credential = ProductCredential.objects.create(
            product=product,
            client_id=client_id[:100],
            encrypted_secret=cipher().encrypt(secret.encode()).decode(),
            status="active",
            gateway_url=ABDM.sandbox_credentials.gateway_url(),
            callback_url=callback,
            issued_at=decided,
        )
        self.stamps.create(
            ProductOutcome,
            decided,
            product=product,
            outcome_type="sandbox_credentials",
            name="Sandbox credentials",
            status="active",
            data={
                "client_id": credential.client_id,
                "gateway_url": credential.gateway_url,
                "credential_id": credential.pk,
                "demo": False,
            },
            field_schema=[],
            metadata={"secret_access": "audited_reveal_endpoint"},
            source_application=registration,
            issued_by=None,
            valid_until=None,
            issued_at=decided,
        )
        self.report.counts["credentials"] += 1

    # ---------------------------------------------------------------- milestones

    def resolve_exit_milestones(self, row, exits):
        claimed = {
            exit_row["id"]: clean.milestone_keys(exit_row["integration_detail"])
            for exit_row in exits
        }
        covered = [key for keys in claimed.values() for key in keys]
        for exit_row in exits:
            if claimed[exit_row["id"]]:
                continue
            if exit_row["final_status"] not in (
                EXIT_PENDING,
                EXIT_SENT_BACK,
                EXIT_APPROVED,
            ):
                self.report.counts["draft exits naming no milestone"] += 1
                continue
            inferred = clean.milestone_keys(
                row["integration_level"],
            ) or clean.ordered_milestones(set(covered))
            if inferred:
                claimed[exit_row["id"]] = inferred
                self.report.counts["exits given the account's milestones"] += 1
            else:
                self.report.skip(
                    exit_row["source"],
                    exit_row["id"],
                    "submitted exit names no milestone",
                )
        return claimed

    def declaration_for(self, exit_row, sd_id):
        declaration = self.declarations_by_id.get(exit_row["self_declaration_id"])
        if declaration and declaration["sd_id"] == sd_id:
            return declaration
        filed = exit_row["created_date"] or exit_row["created_at"]
        earlier = [
            candidate
            for candidate in self.declarations_by_login.get(sd_id, [])
            if candidate["created_at"] and filed and candidate["created_at"] <= filed
        ]
        return (
            max(earlier, key=lambda candidate: candidate["created_at"])
            if earlier
            else None
        )

    def exit_data(self, exit_row, key, sd_id):
        # An exit carried its WASA agency and certificate but no dates: legacy
        # kept one WASA date pair per account, which only the WASA review holds.
        declaration = self.declaration_for(exit_row, sd_id) or {}
        prefix = clean.DECLARATION_DATES.get(key)
        agency = clean.present(exit_row["organization_evaluate"])
        return {
            "wasa_agency": self.agencies.get(agency.lower(), agency),
            "start_date": clean.iso(
                clean.ist_date(declaration.get(f"{prefix}_start_date")),
            ),
            "end_date": clean.iso(
                clean.ist_date(declaration.get(f"{prefix}_end_date")),
            ),
            "tentative_demo_date": clean.iso(clean.ist_date(exit_row["sare_date"])),
            "use_product_wasa": False,
            "wasa_source_submission": None,
        }

    def import_milestones(self, product, user, row, keys, exits, exit_keys):  # noqa: PLR0913, PLR0917
        if not keys:
            return
        applications = self.open_requests(product, user, keys)
        record = (
            self.exit_form(product, user)
            if any(key != "uhi1" for key in keys)
            else None
        )
        filings = self.record_exits(
            product,
            user,
            row,
            applications,
            record,
            exits,
            exit_keys,
        )
        certificates = set()
        for key, application in applications.items():
            if key == "uhi1" and row["sd_id"] in self.uhi:
                self.import_uhi(product, user, application, self.uhi[row["sd_id"]])
                continue
            self.settle_request(
                product,
                application,
                self.uhi_record(product, user) if key == "uhi1" else record,
                key,
                filings,
                self.nhcx.get(row["sd_id"]),
                certificates,
            )

    def open_requests(self, product, user, keys):
        """One request per milestone, each depending on the ones that open it."""
        created = product.created_at
        applications = {}
        for key in keys:
            definition = MILESTONES[key]
            application = self.stamps.create(
                ApplicationInstance,
                created,
                reference=self.reference("UHI" if key == "uhi1" else "EXIT", created),
                application_type=ABDM.application_for(key).key,
                title=f"{definition.code} - {definition.name}",
                product=product,
                created_by=user,
                status="draft",
                metadata={"milestone": key},
            )
            for predecessor in ABDM.milestone_predecessors(key, product.organisation):
                if predecessor in applications:
                    self.stamps.create(
                        ApplicationDependency,
                        created,
                        application=application,
                        depends_on=applications[predecessor],
                    )
            Milestone.objects.create(
                product=product,
                key=key,
                application=application,
                enabled=True,
            )
            applications[key] = application
        return applications

    def exit_form(self, product, user):
        """The one exit evidence form every milestone of a product submits on."""
        created = product.created_at
        return self.stamps.create(
            FormRecord,
            created,
            reference=self.reference("FORM", created, 8),
            form_key="sandbox_exit_evidence",
            name="Sandbox exit evidence",
            reuse_scope=FormReuseScope.PRODUCT,
            organisation=product.organisation,
            product=product,
            metadata={"schema_version": 1},
            created_by=user,
        )

    def record_exits(  # noqa: PLR0913, PLR0917
        self,
        product,
        user,
        row,
        applications,
        record,
        exits,
        exit_keys,
    ):
        """Every legacy exit, as one submission under each milestone it claimed."""
        created = product.created_at
        filings = defaultdict(list)
        number = 0
        newest = None
        for exit_row in exits:
            claimed = [
                key for key in exit_keys.get(exit_row["id"], []) if key in applications
            ]
            if not claimed:
                continue
            if exit_row["source"] == "sd_exit_live":
                self.report.counts["exits from sd_exit_live"] += 1
            for key in claimed:
                number += 1
                submission = self.record_exit(
                    exit_row,
                    key,
                    user=user,
                    sd_id=row["sd_id"],
                    application=applications[key],
                    record=record,
                    number=number,
                    filed=exit_row["created_date"] or exit_row["created_at"] or created,
                )
                filings[key].append((exit_row, submission))
                newest = submission
                self.report.counts["exit submissions"] += 1
        if newest:
            FormSubmission.objects.filter(pk=newest.pk).update(is_current=True)
        return filings

    def record_exit(  # noqa: PLR0913
        self,
        exit_row,
        key,
        *,
        user,
        sd_id,
        application,
        record,
        number,
        filed,
    ):
        """One exit read as one milestone's submission, with its evidence attached."""
        submitted = exit_row["final_status"] in (
            EXIT_PENDING,
            EXIT_SENT_BACK,
            EXIT_APPROVED,
        )
        evidence = (
            files.exit_evidence(exit_row, self.documents.get(exit_row["id"], []), key)
            if exit_row["source"]
            else {}
        )
        metadata = {"complete": submitted, "imported": True}
        if "wasa_certificate" in evidence:
            metadata["legacy_note"] = WASA_UNDATED
        submission = self.stamps.create(
            FormSubmission,
            filed,
            form=record,
            origin_application=application,
            form_key=record.form_key,
            status="completed" if submitted else "needs_changes",
            data=self.exit_data(exit_row, key, sd_id),
            field_schema=self.schemas["exit"],
            metadata=metadata,
            schema_version=1,
            revision=1,
            submission_number=number,
            is_current=False,
            valid_until=None,
            submitted_by=user,
        )
        for field_key, sources in evidence.items():
            for source in sources:
                self.attach(
                    submission,
                    field_key,
                    source,
                    user,
                    single=field_key != "supporting_evidence",
                )
        if submitted and exit_row["final_status"] == EXIT_APPROVED and not evidence:
            self.report.review["approved exits without files"].append(
                {"exit": exit_row["id"], "milestone": key},
            )
        return submission

    def uhi_record(self, product, user):
        """An empty UHI form for a registration that picked UHI but never asked."""
        created = product.created_at
        return self.stamps.create(
            FormRecord,
            created,
            reference=self.reference("FORM", created, 8),
            form_key="sandbox_uhi_participation",
            name="UHI participation",
            reuse_scope=FormReuseScope.PRODUCT,
            organisation=product.organisation,
            product=product,
            metadata={"schema_version": 1},
            created_by=user,
        )

    def newest_filing(self, filings):
        """The last exit this registration filed, whichever milestone it claimed."""
        every = [pair for pairs in filings.values() for pair in pairs]
        return max(every, key=lambda pair: pair[1].submission_number, default=None)

    def decision_for(self, key, filings, nhcx):
        """What legacy decided about this request, read from the exits it filed.

        An NHCX approval can name no exit of its own. Legacy raised it when an
        exit was filed under a declaration naming NHCX, and replaces a
        declaration rather than editing it, so the words can be gone while the
        approval stands. The exit it was decided against is the newest one.
        """
        mine = filings.get(key, [])
        approved = [
            (exit_row, submission)
            for exit_row, submission in mine
            if exit_row["final_status"] == EXIT_APPROVED
        ]
        if approved:
            exit_row, submission = approved[0]
            return Decision(
                submission=submission,
                status="approved",
                decided=self.exit_decided(exit_row, submission),
                note=clean.paragraph(exit_row["admin_coment"]),
                action="Approved",
            )
        if (
            key in clean.NHCX_ROLES
            and nhcx
            and nhcx["nhcx_final_status"] == NHCX_APPROVED
        ):
            filed = mine[-1] if mine else self.newest_filing(filings)
            if filed:
                submission = filed[1]
                return Decision(
                    submission=submission,
                    status="approved",
                    decided=nhcx["nhcx_admin_status_updated_at"]
                    or submission.submitted_at,
                    note=clean.paragraph(nhcx["nhcx_admin_comment"]),
                    action="Approved",
                )
        if not mine:
            return Decision()
        exit_row, submission = mine[-1]
        if exit_row["final_status"] == EXIT_SENT_BACK:
            reason = clean.exit_reject_reason(exit_row["admin_coment"])
            self.report.counts[
                f"send-back reason read as: {reason}"
                if reason
                else "send-back reason left to the note"
            ] += 1
            return Decision(
                submission=submission,
                status="rejected",
                decided=self.exit_decided(exit_row, submission),
                note=clean.reject_note(exit_row["admin_coment"], reason),
                action="Rejected",
                reason=reason,
            )
        if exit_row["final_status"] == EXIT_PENDING:
            return Decision(submission=submission, status="in_review")
        return Decision(submission=submission)

    def exit_decided(self, exit_row, submission):
        """When legacy settled this exit, falling back on when it was filed."""
        return (
            exit_row["final_status_date"]
            or exit_row["admin_status_date"]
            or submission.submitted_at
        )

    def settle_request(  # noqa: PLR0913, PLR0917
        self,
        product,
        application,
        record,
        key,
        filings,
        nhcx,
        certificates,
    ):
        mine = filings.get(key, [])
        decision = self.decision_for(key, filings, nhcx)
        if (
            key in clean.NHCX_ROLES
            and nhcx
            and decision.status != "approved"
            and not mine
        ):
            self.report.review["NHCX decisions with no NHCX exit"].append(
                {"product": product.pk},
            )
        filed = (decision.submission and decision.submission.submitted_at) or None
        open_at_least_once = [
            exit_row
            for exit_row, _ in mine
            if exit_row["final_status"] in (EXIT_PENDING, EXIT_SENT_BACK, EXIT_APPROVED)
        ]
        created = application.created_at
        if record is not None:
            self.stamps.create(
                ApplicationFormUse,
                created,
                application=application,
                form=record,
                form_key=record.form_key,
                selected_submission=decision.submission,
            )
        item = self.stamps.create(
            ReviewItem,
            created,
            kind=ReviewItem.Kind.APPLICATION,
            organisation=product.organisation,
            product=product,
            application=application,
            form=record,
            selected_submission=decision.submission,
            status=REVIEW_STATUS[decision.status],
            submitted_at=filed if decision.status != "draft" else None,
            resubmission_count=max(len(open_at_least_once) - 1, 0),
            decided_at=decision.decided if decision.action else None,
            decided_by=None,
            decision_note=decision.note if decision.action else "",
            decision_reason=decision.reason,
        )
        ApplicationInstance.objects.filter(pk=application.pk).update(
            status=APPLICATION_STATUS[decision.status],
            submitted_at=filed if decision.status != "draft" else None,
            decided_at=decision.decided if decision.action else None,
            updated_at=decision.decided or filed or created,
        )
        if decision.action:
            self.audit(
                decision.decided,
                organisation=product.organisation,
                product=product,
                item=item,
                action=decision.action,
                detail={"note": decision.note, "submission_id": decision.submission.pk},
            )
        if decision.status == "approved":
            self.record_milestone_approval(product, application, key, decision)
            self.milestone_wasa(
                product,
                application,
                decision.submission,
                decision.decided,
                certificates,
            )
        self.report.counts[f"milestone requests {decision.status}"] += 1

    def record_milestone_approval(self, product, application, key, decision):
        """The outcome the portal issues when it approves a milestone."""
        self.stamps.create(
            ProductOutcome,
            decision.decided,
            product=product,
            outcome_type="milestone_approval",
            name=f"{application.title} approved",
            status="active",
            data={
                "milestone": key,
                "approved_on": decision.decided.isoformat(),
                "decision_note": decision.note,
                "production_handoff": PRODUCTION_HANDOFF,
            },
            field_schema=[
                {"key": "decision_note", "label": "Decision"},
                {"key": "production_handoff", "label": "Production credentials"},
            ],
            metadata={},
            source_application=application,
            issued_by=None,
            valid_until=None,
            issued_at=decision.decided,
        )

    def milestone_wasa(self, product, application, submission, decided, certificates):
        """Approving a milestone approves the certificate it carried, undated here."""
        entry = submission.data.get("wasa_certificate")
        if not entry:
            return
        attachment = FormAttachment.objects.get(pk=entry["attachment_id"])
        if attachment.file.name in certificates:
            return
        certificates.add(attachment.file.name)
        expiry = clean.lapsed_wasa_expiry(
            clean.ist_date(attachment.created_at),
            clean.ist_date(self.now),
        )
        self.stamps.create(
            ProductOutcome,
            decided,
            product=product,
            outcome_type="wasa_approval",
            name="WASA certification approved",
            status="active",
            data={
                "submission_id": submission.pk,
                "wasa_agency": submission.data.get("wasa_agency", ""),
                "wasa_date": None,
                "wasa_valid_until": clean.iso(expiry),
            },
            field_schema=WASA_FIELDS,
            metadata={"legacy_note": WASA_LAPSED if expiry else WASA_UNDATED},
            source_application=application,
            issued_by=None,
            valid_until=expiry,
            issued_at=decided,
        )
        self.report.counts[
            "WASA approvals from milestone certificates, expired"
            if expiry
            else "WASA approvals from milestone certificates, undated"
        ] += 1

    def import_uhi(self, product, user, application, request):
        if request is None:
            return
        recorded = request["created_at"] or product.created_at
        record = self.stamps.create(
            FormRecord,
            recorded,
            reference=self.reference("FORM", recorded, 8),
            form_key="sandbox_uhi_participation",
            name="UHI participation",
            reuse_scope=FormReuseScope.PRODUCT,
            organisation=product.organisation,
            product=product,
            metadata={"schema_version": 1},
            created_by=user,
        )
        submission = self.stamps.create(
            FormSubmission,
            recorded,
            form=record,
            origin_application=application,
            form_key=record.form_key,
            status="completed",
            data=uhi_answers(request),
            field_schema=self.schemas["uhi"],
            metadata={"complete": True, "imported": True},
            schema_version=1,
            revision=1,
            submission_number=1,
            is_current=True,
            submitted_by=user,
        )
        self.stamps.create(
            ApplicationFormUse,
            recorded,
            application=application,
            form=record,
            form_key=record.form_key,
            selected_submission=submission,
        )
        item = self.stamps.create(
            ReviewItem,
            recorded,
            kind=ReviewItem.Kind.APPLICATION,
            organisation=product.organisation,
            product=product,
            application=application,
            form=record,
            selected_submission=submission,
            status=ReviewItem.Status.APPROVED,
            submitted_at=recorded,
            decided_at=recorded,
            decided_by=None,
        )
        ApplicationInstance.objects.filter(pk=application.pk).update(
            status="approved",
            submitted_at=recorded,
            decided_at=recorded,
            updated_at=recorded,
        )
        self.audit(
            recorded,
            organisation=product.organisation,
            product=product,
            item=item,
            action="Recorded",
            detail={"submission_id": submission.pk},
        )
        self.report.counts["UHI participation records"] += 1

    def import_wasa(self, product, user, wasa, exits):
        issued = clean.ist_date(wasa["wasa_issue_date"])
        expires = clean.ist_date(wasa["wasa_expiry_date"])
        if not issued or not expires:
            self.report.skip(
                "wasa_dhis_initiation_details",
                product.pk,
                "missing WASA dates",
            )
            return
        recorded = wasa["updated_at"] or wasa["wasa_issue_date"]
        application = self.wasa_review(product, user, recorded)
        record = self.wasa_form(product, user, recorded)
        # Legacy approved this record once, so its dates stand whatever date the
        # certificate happens to carry: the newest one on the account is shown.
        certificate = next(iter(files.wasa_certificates(exits, self.documents)), None)
        metadata = {"complete": True, "imported": True}
        if certificate is None:
            metadata["legacy_note"] = WASA_NONE
        submission = self.stamps.create(
            FormSubmission,
            recorded,
            form=record,
            origin_application=application,
            form_key=record.form_key,
            status="completed",
            data={
                "wasa_agency": self.audit_agency(exits),
                "wasa_date": issued.isoformat(),
                "wasa_valid_until": expires.isoformat(),
            },
            field_schema=self.schemas["wasa"],
            metadata=metadata,
            schema_version=1,
            revision=1,
            submission_number=1,
            is_current=True,
            valid_until=expires,
            submitted_by=user,
        )
        attached = (
            self.attach(submission, "wasa_certificate", certificate, user, single=True)
            if certificate
            else None
        )
        self.stamps.create(
            ApplicationFormUse,
            recorded,
            application=application,
            form=record,
            form_key=record.form_key,
            selected_submission=submission,
        )
        item = self.stamps.create(
            ReviewItem,
            recorded,
            kind=ReviewItem.Kind.APPLICATION,
            organisation=product.organisation,
            product=product,
            application=application,
            form=record,
            selected_submission=submission,
            status=ReviewItem.Status.APPROVED,
            submitted_at=recorded,
            decided_at=recorded,
            decided_by=None,
        )
        self.audit(
            recorded,
            organisation=product.organisation,
            product=product,
            item=item,
            action="Approved",
            detail={"note": "", "submission_id": submission.pk},
        )
        if attached is None:
            self.report.review["WASA rows without a certificate"].append(
                {"product": product.pk},
            )
        self.record_wasa_approval(
            product,
            application,
            submission,
            recorded=recorded,
            expires=expires,
            note="" if attached is not None else WASA_NONE,
        )

    def wasa_review(self, product, user, recorded):
        """The review the portal holds a WASA under, approved as legacy left it."""
        return self.stamps.create(
            ApplicationInstance,
            recorded,
            reference=self.reference("WASA", recorded),
            application_type="abdm_wasa_review",
            title="WASA certification review",
            product=product,
            created_by=user,
            status="approved",
            metadata={},
            submitted_at=recorded,
            decided_at=recorded,
            decided_by=None,
        )

    def wasa_form(self, product, user, recorded):
        """The WASA form the product's certificate is submitted on."""
        return self.stamps.create(
            FormRecord,
            recorded,
            reference=self.reference("FORM", recorded, 8),
            form_key="abdm_wasa",
            name="WASA certification",
            reuse_scope=FormReuseScope.PRODUCT,
            organisation=product.organisation,
            product=product,
            metadata={"schema_version": 1},
            created_by=user,
        )

    def audit_agency(self, exits):
        """The agency the newest exit named, under the portal's spelling of it."""
        named = next(
            (
                clean.present(row["organization_evaluate"])
                for row in reversed(exits)
                if clean.present(row["organization_evaluate"])
            ),
            "",
        )
        return self.agencies.get(named.lower(), named)

    def record_wasa_approval(  # noqa: PLR0913
        self,
        product,
        application,
        submission,
        *,
        recorded,
        expires,
        note="",
    ):
        """The outcome that puts a WASA on the product page, with its dates."""
        self.stamps.create(
            ProductOutcome,
            recorded,
            product=product,
            outcome_type="wasa_approval",
            name="WASA certification approved",
            status="active",
            data={
                "submission_id": submission.pk,
                "wasa_agency": submission.data["wasa_agency"],
                "wasa_date": submission.data["wasa_date"],
                "wasa_valid_until": submission.data["wasa_valid_until"],
            },
            field_schema=WASA_FIELDS,
            metadata={"legacy_note": note} if note else {},
            source_application=application,
            issued_by=None,
            valid_until=expires,
            issued_at=recorded,
        )
        self.report.counts["WASA approvals"] += 1
