# ruff: noqa: E501
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.management.color import no_style
from django.db import connection
from django.db import transaction
from django.utils import timezone
from django.utils.datastructures import MultiValueDict
from PIL import Image
from PIL import ImageDraw

from ohc_experience.abdm import forms
from ohc_experience.events.models import Event
from ohc_experience.experiences import production
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import CertificationAgency
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.experiences.models import TicketAttachment
from ohc_experience.integrations.services import provision_inline
from ohc_experience.organisations.lgd import LGDLookupError
from ohc_experience.organisations.lgd import lookup_pincode
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import post_reply

DEMO_PASSWORD = "experience-demo-2026"  # noqa: S105


def demo_pdf(name="functional-testing.pdf", title="Functional testing certificate"):
    page = Image.new("RGB", (1240, 1754), "white")
    drawing = ImageDraw.Draw(page)
    drawing.text((90, 100), "ABDM DEVELOPER SANDBOX", fill="#096447", font_size=38)
    drawing.text((90, 190), title, fill="#222724", font_size=28)
    drawing.text(
        (90, 265),
        "Synthetic demonstration document. Not a real certification.",
        fill="#646d68",
        font_size=20,
    )
    drawing.text(
        (90, 335),
        "Product: Medibase HMIS\nScope: identity and health information exchange\nEnvironment: sandbox\nResult: demonstration evidence only",
        fill="#222724",
        font_size=23,
        spacing=18,
    )
    output = BytesIO()
    page.save(output, format="PDF", resolution=150)
    return SimpleUploadedFile(name, output.getvalue(), content_type="application/pdf")


def organisation_data(name="Medibase Technologies Private Limited"):
    return {
        "name": name,
        "description": "Digital health systems for connected care.",
        "entity_type": "private_company",
        "category": "india",
        "website": "https://example.org",
        "registered_address": "42 Technology Park, Bengaluru",
        "pincode": "560001",
        "state": "Karnataka",
        "district": "Bengaluru Urban",
        "verification_document_type": "CIN",
        "verification_document_number": "L12345KA2020PLC123456",
    }


def product_data(name="Medibase HMIS 4.2"):
    return {
        "name": name,
        "description": "Hospital information management, patient records and connected health services.",
        "solution_type": ["clinical_hmis"],
        "applied_milestones": [
            "HIE-CM:m1",
            "HIE-CM:m2",
            "HIE-CM:m3",
            "HIE-CM:m4",
            "PHR:phr1",
            "HealthLocker:locker1",
            "UHI:uhi1",
        ],
    }


def uhi_data():
    return {
        "uhi_role": ["eua"],
        "uhi_services": ["teleconsultation", "physical_consultation"],
        "uhi_tell_us_about": "Discovery and teleconsultation for our clinic network.",
        "uhi_extra_details": "",
    }


def evidence_data():
    today = timezone.localdate()
    return {
        "start_date": (today - timedelta(days=45)).isoformat(),
        "end_date": (today - timedelta(days=5)).isoformat(),
        "tentative_demo_date": (today + timedelta(days=10)).isoformat(),
        "wasa_agency": _demo_agency(),
        "wasa_date": (today - timedelta(days=12)).isoformat(),
        "wasa_valid_until": (today + timedelta(days=180)).isoformat(),
    }


def _demo_agency():
    agency = (
        CertificationAgency.objects.filter(program="abdm", is_active=True)
        .order_by("sort_order", "name", "pk")
        .values_list("name", flat=True)
        .first()
    )
    if agency is None:
        msg = (
            "No active ABDM certification agency is available. Add or activate an "
            "agency in Django admin before seeding the demo. No data has been changed."
        )
        raise CommandError(msg)
    return agency


@transaction.atomic
def _reset_demo_database():
    agencies = list(CertificationAgency.objects.values())
    call_command("flush", interactive=False)
    CertificationAgency.objects.bulk_create(
        [CertificationAgency(**agency) for agency in agencies],
    )
    # Insertion updates automatic timestamps; restore the administrator's originals.
    CertificationAgency.objects.bulk_update(
        [CertificationAgency(**agency) for agency in agencies],
        ["created_at", "updated_at"],
    )
    with connection.cursor() as cursor:
        for sql in connection.ops.sequence_reset_sql(no_style(), [CertificationAgency]):
            cursor.execute(sql)


def evidence_files():
    return MultiValueDict(
        {
            "wasa_certificate": [
                demo_pdf("wasa-certificate.pdf", "WASA certificate"),
            ],
            "functional_certificate": [demo_pdf()],
            "functional_report": [
                demo_pdf("functional-report.pdf", "Functional testing report"),
            ],
            "undertaking_form": [
                demo_pdf("undertaking-form.pdf", "Undertaking form"),
            ],
            "supporting_evidence": [
                demo_pdf("audit-annexure.pdf", "Audit annexure"),
                demo_pdf("test-cases.pdf", "Test cases"),
            ],
        },
    )


def _verify_demo_location():
    try:
        demo_locations = lookup_pincode("560001")
    except LGDLookupError as exc:
        msg = (
            "LGD lookup is unavailable. Configure LGD_API_KEY and verify API "
            "access before seeding the demo. No data has been changed."
        )
        raise CommandError(msg) from exc
    if not demo_locations:
        msg = (
            "LGD returned no location for the demo PIN code. No data has been changed."
        )
        raise CommandError(msg)


def _demo_lookup_pincode(pincode):
    """LGD's answer for the demo PIN code, for seeding without LGD access."""
    if pincode != "560001":
        return []
    return [
        {
            "state": "KARNATAKA",
            "state_code": "29",
            "district": "BENGALURU URBAN",
            "district_code": "525",
        },
    ]


class DemoBuilder:
    def __init__(self, stdout, style):
        self.stdout = stdout
        self.style = style

    def handle(self, *args, **options):
        if not settings.DEBUG:
            msg = "Demo seeding is only available with DEBUG enabled."
            raise CommandError(msg)
        if options.get("permissions_only"):
            self.permission_accounts(options["password"])
            return
        if options.get("skip_lgd"):
            # The organisation form looks the PIN code up as well.
            with patch.object(forms, "lookup_pincode", _demo_lookup_pincode):
                self.seed(options)
        else:
            _verify_demo_location()
            self.seed(options)

    def seed(self, options):  # noqa: PLR0915
        _demo_agency()
        if options["reset"]:
            files = set(FormAttachment.objects.values_list("file", flat=True)) | set(
                TicketAttachment.objects.values_list("file", flat=True),
            )
            _reset_demo_database()
            for name in files:
                if name:
                    default_storage.delete(name)
        elif ProductWorkspace.objects.exists():
            msg = "Sandbox data already exists. Pass --reset for a fresh local demo."
            raise CommandError(msg)
        password = options["password"]
        applicant = self.user("applicant@abdm-demo.in", "Dr Kavya Rao", password)
        contributor = self.user("contributor@abdm-demo.in", "Arjun Menon", password)
        reviewer = self.user(
            "reviewer@abdm-demo.in",
            "Priya Sharma",
            password,
            reviewer=True,
        )
        for area in AccessGrant.Area.values:
            AccessGrant.objects.update_or_create(
                user=reviewer,
                program="abdm",
                area=area,
                category="*",
                defaults={"can_read": True, "can_write": True, "can_approve": True},
            )
        admin = self.user(
            "admin@abdm-demo.in",
            "NHA Administrator",
            password,
            admin=True,
        )
        self.user(
            "decision-maker@ohc.network",
            "NHA Demo Administrator",
            password,
            admin=True,
        )
        org = Organisation.objects.create(name="Medibase Technologies Private Limited")
        Membership.objects.create(organisation=org, user=applicant, role="owner")
        Membership.objects.create(organisation=org, user=contributor, role="developer")
        org_review = services.organisation_review(org, applicant)
        org_review, form, saved = services.save_review_form(
            org_review,
            applicant,
            data=organisation_data(),
            files={
                "supporting_document": demo_pdf(
                    "organisation-verification.pdf",
                    "Organisation verification",
                ),
            },
            submit=True,
        )
        if not saved:
            raise CommandError(str(form.errors))
        services.assign_review(org_review, admin, reviewer)
        services.decide(
            org_review,
            reviewer,
            action="approve",
            note="Organisation identity verified for sandbox participation.",
        )
        org.refresh_from_db()
        workspace, form = self.register_product(org, applicant, data=product_data())
        if not workspace:
            raise CommandError(str(form.errors))
        self.exit(workspace, "m1", applicant, admin, reviewer, "approved")
        # A visibly fake ID: the demo never reaches the NHA production gateway.
        production.record(
            workspace.product,
            reviewer,
            client_id=f"DEMO_PROD_{workspace.reference.replace('-', '_')}",
            issued_on=timezone.localdate() - timedelta(days=9),
            expected="",
        )
        self.exit(workspace, "m2", applicant, admin, reviewer, "query")
        # M3 builds on M1, which is approved, so this review is ready to decide.
        self.exit(workspace, "m3", applicant, admin, reviewer, "review")
        self.exit(workspace, "m4", applicant, admin, reviewer, "review")
        self.exit(workspace, "phr1", applicant, admin, reviewer, "review")
        self.exit(workspace, "locker1", applicant, admin, reviewer, "sent_back")
        uhi = workspace.product.milestones.get(key="uhi1").application.review_item
        services.save_review_form(uhi, applicant, data=uhi_data(), submit=True)
        self.register_product(
            org,
            applicant,
            data={
                **product_data("Medibase Health Locker"),
                "solution_type": ["health_locker"],
                "applied_milestones": ["HealthLocker:locker1"],
            },
        )
        pending_user = self.user("new-integrator@abdm-demo.in", "Nisha Patel", password)
        pending_org = Organisation.objects.create(name="HealthBridge Digital")
        Membership.objects.create(
            organisation=pending_org,
            user=pending_user,
            role="owner",
        )
        pending_item = services.organisation_review(pending_org, pending_user)
        services.save_review_form(
            pending_item,
            pending_user,
            data=organisation_data(pending_org.name),
            files={
                "supporting_document": demo_pdf(
                    "identity-document.pdf",
                    "Entity identity document",
                ),
            },
            submit=True,
        )
        self.events(admin)
        ticket = Ticket.objects.create(
            organisation=org,
            product=workspace.product,
            track="HIE-CM",
            subject="Clarification on consent callback acknowledgement",
            priority="medium",
            created_by=applicant,
            assignee=reviewer,
        )
        post_reply(
            ticket,
            applicant,
            "Should our M2 callback acknowledge receipt before processing the encrypted payload?",
            from_nha_team=False,
        )
        post_reply(
            ticket,
            reviewer,
            "Please share the synthetic callback trace and correlate it with your test case reference. Do not include real patient data.",
            from_nha_team=True,
        )
        self.stdout.write(self.style.SUCCESS("Fresh ABDM sandbox demo ready."))
        self.stdout.write(
            f"Product: http://localhost:8000{workspace.get_absolute_url()}",
        )
        self.stdout.write(
            "Applicant: applicant@abdm-demo.in\nReviewer: reviewer@abdm-demo.in\nAdministrator: admin@abdm-demo.in",
        )
        self.stdout.write(f"Password: {password}")
        self.permission_accounts(password)

    def permission_accounts(self, password):
        accounts = [
            ("reviewer", "Priya Sharma", "*", AccessGrant.Area.values),
            ("nhcx-reviewer", "NHCX Reviewer", "NHCX", ["review"]),
            ("uhi-reviewer", "UHI Reviewer", "UHI", ["review"]),
            ("hiecm-reviewer", "HIE-CM Reviewer", "HIE-CM", ["review"]),
            ("nhcx-support", "NHCX Support", "NHCX", ["support"]),
            ("uhi-events", "UHI Event Manager", "UHI", ["events"]),
        ]
        for username, name, category, areas in accounts:
            user = self.user(f"{username}@abdm-demo.in", name, password, reviewer=True)
            for area in areas:
                AccessGrant.objects.get_or_create(
                    user=user,
                    program="abdm",
                    area=area,
                    category=category,
                    defaults={"can_read": True, "can_write": True, "can_approve": True},
                )
            self.stdout.write(f"{user.email}: {category}, {', '.join(areas)}")
        self.stdout.write(
            self.style.SUCCESS(
                "Demo permission accounts ready; application data unchanged.",
            ),
        )

    def register_product(self, org, applicant, *, data):
        """Registers, then runs the chain inline — a seed waits for no worker."""
        workspace, form = services.register_product(org, applicant, data=data)
        if workspace:
            provision_inline(workspace.product)
        return workspace, form

    def user(self, email, name, password, *, reviewer=False, admin=False):
        user, _ = get_user_model().objects.get_or_create(email=email)
        user.name, user.is_active = name, True
        user.is_staff, user.is_superuser, user.is_nha_team = (
            reviewer or admin,
            admin,
            reviewer or admin,
        )
        user.set_password(password)
        user.save()
        EmailAddress.objects.update_or_create(
            user=user,
            email=email,
            defaults={"primary": True, "verified": True},
        )
        return user

    def exit(self, workspace, key, applicant, admin, reviewer, state):  # noqa: PLR0913, PLR0917
        item = workspace.product.milestones.get(key=key).application.review_item
        item, form, saved = services.save_review_form(
            item,
            applicant,
            data=evidence_data(),
            files=evidence_files(),
            submit=True,
        )
        if not saved:
            raise CommandError(str(form.errors))
        services.assign_review(item, admin, reviewer)
        if state == "approved":
            services.decide(
                item,
                reviewer,
                action="approve",
                note="M1 approved. This approval also satisfies PHR M1.",
            )
        elif state == "query":
            services.decide(
                item,
                reviewer,
                action="query",
                field_key="functional_report",
                note="Please identify the test cases covering consent expiry and revocation in the functional report.",
            )
        elif state == "sent_back":
            services.decide(
                item,
                reviewer,
                action="send_back",
                reason="Incomplete documentation",
                note="Include the data-retention and document-retrieval scenarios, then resubmit the functional report.",
            )

    def events(self, admin):
        for index, (title, kind, days) in enumerate(
            [
                ("HIE-CM integration office hours", "office_hours", 3),
                ("PHR application flows: developer workshop", "workshop", 7),
                ("UHI participant integration clinic", "webinar", 14),
                ("ABHA identity integration walkthrough", "webinar", -7),
            ],
        ):
            starts = timezone.now() + timedelta(days=days)
            Event.objects.create(
                title=title,
                slug=f"sandbox-event-{index}",
                kind=kind,
                summary="A working session with the ABDM integration team.",
                description="Bring your sandbox questions and synthetic test evidence.\nReference material: https://nha-in.github.io/docs/",
                starts_at=starts,
                ends_at=starts + timedelta(hours=1),
                published_at=timezone.now(),
                created_by=admin,
            )
