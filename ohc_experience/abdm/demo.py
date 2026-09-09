# ruff: noqa: E501
from datetime import timedelta
from io import BytesIO

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from django.utils.datastructures import MultiValueDict
from PIL import Image
from PIL import ImageDraw

from ohc_experience.events.models import Event
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.experiences.models import TicketAttachment
from ohc_experience.experiences.models import TicketContext
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
        "verification_document_number": "DEMO-CIN-2026",
    }


def product_data(name="Medibase HMIS 4.2"):
    return {
        "name": name,
        "description": "Hospital information management, patient records and connected health services.",
        "category": "hmis",
        "solution_type": "clinical_hmis",
        "applied_milestones": [
            "HI-CM:m1",
            "HI-CM:m2",
            "HI-CM:m3",
            "HI-CM:m4",
            "PHR:m1",
            "PHR:phr1",
            "HealthLocker:locker1",
            "UHI:uhi1",
        ],
    }


def evidence_data():
    today = timezone.localdate()
    return {
        "start_date": (today - timedelta(days=45)).isoformat(),
        "end_date": (today - timedelta(days=5)).isoformat(),
        "tentative_demo_date": (today + timedelta(days=10)).isoformat(),
        "wasa_agency": "SecureStack Audit Services (demo)",
        "wasa_date": (today - timedelta(days=12)).isoformat(),
    }


def evidence_files():
    return MultiValueDict(
        {
            "functional_certificate": [demo_pdf()],
            "functional_report": [
                demo_pdf("functional-report.pdf", "Functional testing report"),
            ],
            "supporting_evidence": [
                demo_pdf("audit-annexure.pdf", "Audit annexure"),
                demo_pdf("test-cases.pdf", "Test cases"),
            ],
        },
    )


class DemoBuilder:
    def __init__(self, stdout, style):
        self.stdout = stdout
        self.style = style

    def handle(self, *args, **options):  # noqa: PLR0915
        if not settings.DEBUG:
            msg = "Demo seeding is only available with DEBUG enabled."
            raise CommandError(msg)
        if options.get("permissions_only"):
            self.permission_accounts(options["password"])
            return
        if options["reset"]:
            files = set(FormAttachment.objects.values_list("file", flat=True)) | set(
                TicketAttachment.objects.values_list("file", flat=True),
            )
            call_command("flush", interactive=False)
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
        workspace, form = services.register_product(org, applicant, data=product_data())
        if not workspace:
            raise CommandError(str(form.errors))
        registration = workspace.product.review_items.get(
            kind="product_registration",
        )
        services.assign_review(registration, admin, reviewer)
        services.decide(
            registration,
            reviewer,
            action="approve",
            note="Product registered for the selected compliance tracks.",
        )
        workspace.refresh_from_db()
        self.exit(workspace, "m1", applicant, admin, reviewer, "approved")
        self.exit(workspace, "m2", applicant, admin, reviewer, "query")
        self.exit(workspace, "phr1", applicant, admin, reviewer, "review")
        self.exit(workspace, "locker1", applicant, admin, reviewer, "sent_back")
        draft = workspace.product.milestones.get(key="uhi1").application.review_item
        services.save_review_form(
            draft,
            applicant,
            data={"wasa_agency": "SecureStack Audit Services (demo)"},
            submit=False,
        )
        services.register_product(
            org,
            applicant,
            data={
                **product_data("Medibase Health Locker"),
                "category": "health_locker",
                "solution_type": "health_locker",
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
            subject="Clarification on consent callback acknowledgement",
            priority="medium",
            created_by=applicant,
            assignee=reviewer,
        )
        TicketContext.objects.create(
            ticket=ticket,
            product=workspace.product,
            track="HI-CM",
        )
        post_reply(
            ticket,
            applicant,
            "Should our M2 callback acknowledge receipt before processing the encrypted payload?",
            from_ohc_team=False,
        )
        post_reply(
            ticket,
            reviewer,
            "Please share the synthetic callback trace and correlate it with your test case reference. Do not include real patient data.",
            from_ohc_team=True,
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
            ("hiecm-reviewer", "HI-CM Reviewer", "HI-CM", ["review"]),
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

    def user(self, email, name, password, *, reviewer=False, admin=False):
        user, _ = get_user_model().objects.get_or_create(email=email)
        user.name, user.is_active = name, True
        user.is_staff, user.is_superuser, user.is_ohc_team = (
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
                note="M1 approved. This approval also satisfies PHR M1. Production handoff recorded; the gateway team will contact your technical lead separately.",
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
                note="Include the data-retention and document-retrieval scenarios, then resubmit the functional report.",
            )

    def events(self, admin):
        for index, (title, kind, days) in enumerate(
            [
                ("HI-CM integration office hours", "office_hours", 3),
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
