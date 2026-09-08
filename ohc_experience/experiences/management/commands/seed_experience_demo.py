from __future__ import annotations

from datetime import timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ohc_experience.experiences.models import ApplicationAccess
from ohc_experience.experiences.models import ApplicationDependency
from ohc_experience.experiences.models import ApplicationEvent
from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import ApplicationQueryMessage
from ohc_experience.experiences.models import ApplicationQueryThread
from ohc_experience.experiences.models import EventKind
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import FormRecord
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.experiences.models import ProductType
from ohc_experience.experiences.models import QueryStatus
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.services import application_context
from ohc_experience.experiences.services import form_field_schema
from ohc_experience.experiences.services import issue_product_outcomes
from ohc_experience.experiences.services import materialize_application_forms
from ohc_experience.experiences.services import recalculate_progress
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role

DEFAULT_PASSWORD = "experience-demo-2026"  # noqa: S105

APPLICANT_EMAIL = "applicant@abdm-demo.in"
APPLICANT_NAME = "Dr Kavya Rao"
CONTRIBUTOR_EMAIL = "contributor@abdm-demo.in"
CONTRIBUTOR_NAME = "Arjun Menon"
ADMIN_EMAIL = "decision-maker@ohc.network"
ADMIN_NAME = "Nandita Shah"

ORGANISATION_NAME = "Arogya Digital Health Technologies"
ORGANISATION_SLUG = "arogya-digital-health-demo"
PRODUCT_NAME = "Arogya One Health Platform"
PRODUCT_SLUG = "arogya-one-health-platform"

DRAFT_REFERENCE = "ABDM-DEMO-DRAFT"
REVIEW_REFERENCE = "ABDM-DEMO-REVIEW"
APPLICATION_TYPE = "abdm_production_access"
CURRENT_CERTIFICATION_SUBMISSION_NUMBER = 2

PDF_BYTES = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)


def profile_data() -> dict:
    return {
        "legal_entity_name": ORGANISATION_NAME,
        "organisation_type": "company",
        "registration_number": "U72900KA2024PTC123456",
        "registered_address": "42 Health Stack Road, Indiranagar",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pincode": "560038",
        "website": "https://example.com/arogya-digital",
        "authorised_contact_name": APPLICANT_NAME,
        "authorised_contact_email": APPLICANT_EMAIL,
        "authorised_contact_phone": "+91 98765 43210",
    }


def application_plan_data(*, days_until_launch: int) -> dict:
    return {
        "product_version": "3.2.0",
        "intended_use": (
            "This release covers outpatient and inpatient workflows, "
            "ABHA-assisted registration, care-context linking, consented record "
            "sharing, and clinician-authorised longitudinal record access."
        ),
        "expected_monthly_transactions": 42000,
        "target_go_live_date": (
            timezone.localdate() + timedelta(days=days_until_launch)
        ).isoformat(),
    }


def integration_data(
    *,
    include_health_locker: bool = False,
    protocols: tuple[str, ...] = ("nhcx", "uhi"),
) -> dict:
    roles = ["hip", "hiu"]
    if include_health_locker:
        roles.append("health_locker")
    return {
        "abdm_roles": roles,
        "protocols": list(protocols),
        "milestones": ["m1", "m2", "m3"],
        "sandbox_client_id": "SBX-AROGYA-HMIS-032",
        "sandbox_exit_request_id": "EXIT-2026-1042",
        "hfr_facility_ids": "IN2910000123\nIN2910000456",
        "health_information_types": [
            "diagnostic_report",
            "discharge_summary",
            "prescription",
            "op_consultation",
        ],
        "integration_approach": "direct",
        "connector_name": "",
    }


def nhcx_data() -> dict:
    return {
        "participant_roles": ["provider", "technology_provider"],
        "participant_id": "NHCX-AROGYA-032",
        "protocol_version": "0.9",
        "production_callback_url": "https://claims.example.com/nhcx/callback",
        "public_key_url": "https://claims.example.com/.well-known/jwks.json",
        "claim_use_cases": [
            "coverage_eligibility",
            "preauthorization",
            "claim",
            "communication",
            "payment",
            "status",
        ],
        "claim_modes": ["cashless", "reimbursement"],
        "sandbox_test_reference_ids": (
            "nhcx-demo-eligibility-1001\nnhcx-demo-preauth-1002\nnhcx-demo-claim-1003"
        ),
        "signed_encrypted_payloads": True,
        "asynchronous_idempotency": True,
        "fhir_validation": True,
        "synthetic_data_only": True,
    }


def uhi_data() -> dict:
    return {
        "participant_role": "eua_hsp",
        "subscriber_id": "uhi.arogya.example.com",
        "protocol_version": "0.0.1",
        "production_callback_url": "https://care.example.com/uhi/callback",
        "service_categories": [
            "teleconsultation",
            "in_person_consultation",
            "appointment_booking",
        ],
        "supported_flows": [
            "discovery",
            "selection",
            "initialization",
            "confirmation",
            "status",
            "cancellation",
            "feedback",
        ],
        "hpr_hfr_registry_ids": "71-2345-6789-0123\nIN2910000123",
        "sandbox_transaction_ids": (
            "uhi-demo-search-2001\nuhi-demo-confirm-2002\nuhi-demo-status-2003"
        ),
        "catalog_current": True,
        "signed_callbacks": True,
        "consent_and_privacy": True,
        "grievance_email": "uhi-support@example.com",
    }


def complete_submission_data() -> dict[str, dict]:
    return {
        "organisation_profile": profile_data(),
        "application_plan": application_plan_data(days_until_launch=45),
        "integration_scope": integration_data(),
        "nhcx_integration": nhcx_data(),
        "uhi_integration": uhi_data(),
        "technical_readiness": {
            "production_callback_url": "https://abdm.example.com/gateway/v3",
            "health_check_url": "https://abdm.example.com/health",
            "public_key_url": "https://abdm.example.com/.well-known/jwks.json",
            "outbound_ip_addresses": "203.0.113.20\n2001:db8:100::20",
            "hosting_region": "Mumbai, India",
            "uptime_commitment": "99.95",
            "technical_contact_email": "abdm-operations@example.com",
            "incident_contact_phone": "+91 98765 40101",
            "callback_idempotency": True,
            "secrets_confirmation": True,
        },
        "security_compliance": {
            "privacy_policy_url": "https://example.com/privacy",
            "security_assessment_agency": "Example CERT-In Empanelled Auditor",
            "assessment_date": (timezone.localdate() - timedelta(days=21)).isoformat(),
            "wasa_certificate_number": "WASA-DEMO-2026-0098",
            "data_retention_policy": (
                "Retention is purpose-bound, tenant-configurable, and enforced "
                "through scheduled deletion with auditable legal holds."
            ),
            "incident_response_summary": (
                "A 24x7 on-call rotation triages security alerts, contains affected "
                "tenants, preserves evidence, and follows the notification matrix."
            ),
            "encryption_at_rest": True,
            "encryption_in_transit": True,
            "least_privilege": True,
        },
        "security_certification": {
            "certification_type": "cert_in_audit",
            "certification_name": "Annual ABDM Production Security Assessment",
            "issuing_body": "Example CERT-In Empanelled Auditor",
            "certificate_number": "CERTIN-DEMO-2026-118",
            "issued_on": (timezone.localdate() - timedelta(days=345)).isoformat(),
            "expires_on": (timezone.localdate() + timedelta(days=20)).isoformat(),
            "scope_summary": (
                "ABDM gateway callbacks, consent artefact handling, health-data "
                "exchange services, cloud controls, and the production support plane."
            ),
        },
        "conformance_evidence": {
            "demonstration_date": (
                timezone.localdate() - timedelta(days=14)
            ).isoformat(),
            "tested_milestones": ["m1", "m2", "m3"],
            "functional_testing_agency": "ABDM Integration Test Team",
            "functional_certificate_number": "FUNC-DEMO-2026-184",
            "demo_recording_url": "https://example.com/demo/abdm-production-review",
            "test_request_ids": "req-demo-1001\nreq-demo-1002\nreq-demo-1003",
            "known_limitations": "No known release-blocking limitations.",
        },
        "declaration": {
            "signatory_name": APPLICANT_NAME,
            "signatory_designation": "Chief Medical Information Officer",
            "signatory_email": APPLICANT_EMAIL,
            "declaration_date": timezone.localdate().isoformat(),
            "information_accurate": True,
            "lawful_processing": True,
            "change_notification": True,
            "authority_confirmation": True,
        },
    }


class Command(BaseCommand):
    help = "Seed an ABDM product, applications, and working demo accounts."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--password", default=DEFAULT_PASSWORD)
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Remove only this demo product and its applications before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        password = options["password"]
        if options["fresh"]:
            self._remove_demo_applications()

        applicant = self._user(
            APPLICANT_EMAIL,
            APPLICANT_NAME,
            password,
            platform=False,
        )
        contributor = self._user(
            CONTRIBUTOR_EMAIL,
            CONTRIBUTOR_NAME,
            password,
            platform=False,
        )
        admin = self._user(
            ADMIN_EMAIL,
            ADMIN_NAME,
            password,
            platform=True,
        )
        organisation = self._organisation(applicant, contributor)
        product = self._product(organisation, applicant)

        draft = self._application(
            reference=DRAFT_REFERENCE,
            product=product,
            applicant=applicant,
            admin=admin,
            status="draft",
        )
        draft_submissions = self._submissions(
            draft,
            applicant,
            {
                "organisation_profile": profile_data(),
                "application_plan": application_plan_data(days_until_launch=75),
                "integration_scope": integration_data(include_health_locker=True),
            },
        )
        ApplicationAccess.objects.update_or_create(
            application=draft,
            user=contributor,
            defaults={
                "role_key": "applicant_contributor",
                "direct_permissions": [],
                "granted_by": applicant,
            },
        )
        applicant_query, _created = ApplicationQueryThread.objects.update_or_create(
            application=draft,
            subject="Confirm health locker custodian evidence",
            defaults={
                "status": QueryStatus.AWAITING_REVIEWER,
                "opened_by": applicant,
                "assigned_to": None,
                "submission": draft_submissions["integration_scope"],
            },
        )
        ApplicationQueryMessage.objects.update_or_create(
            thread=applicant_query,
            body=(
                "Please confirm which technology-partner evidence is acceptable "
                "for the health locker custodian review."
            ),
            defaults={"author": applicant},
        )

        review = self._application(
            reference=REVIEW_REFERENCE,
            product=product,
            applicant=applicant,
            admin=admin,
            status="under_review",
        )
        review_submission_data = complete_submission_data()
        review_submission_data.pop("organisation_profile")
        submissions = self._submissions(
            review,
            applicant,
            review_submission_data,
        )
        previous_certification = self._security_certification_history(
            review,
            submissions["security_certification"],
            applicant,
        )
        self._attachment(
            submissions["nhcx_integration"],
            applicant,
            "conformance_documents",
            "demo-nhcx-conformance-report.pdf",
            multiple=True,
        )
        self._attachment(
            submissions["uhi_integration"],
            applicant,
            "conformance_documents",
            "demo-uhi-conformance-report.pdf",
            multiple=True,
        )
        self._attachment(
            submissions["security_compliance"],
            applicant,
            "security_audit_report",
            "demo-security-assessment.pdf",
        )
        self._attachment(
            submissions["conformance_evidence"],
            applicant,
            "functional_test_report",
            "demo-functional-test-report-volume-1.pdf",
            multiple=True,
        )
        self._attachment(
            submissions["conformance_evidence"],
            applicant,
            "functional_test_report",
            "demo-functional-test-report-volume-2.pdf",
            multiple=True,
        )
        self._attachment(
            submissions["conformance_evidence"],
            applicant,
            "signed_undertaking",
            "demo-signed-undertaking.pdf",
        )
        for name in (
            "demo-security-certificate.pdf",
            "demo-security-assessment-annexure.pdf",
        ):
            self._attachment(
                submissions["security_certification"],
                applicant,
                "certificate_documents",
                name,
                multiple=True,
            )
        for name in (
            "demo-remediation-closure.pdf",
            "demo-scope-confirmation.pdf",
        ):
            self._attachment(
                submissions["security_certification"],
                applicant,
                "supporting_documents",
                name,
                multiple=True,
            )
        self._attachment(
            previous_certification,
            applicant,
            "certificate_documents",
            "demo-expired-security-certificate.pdf",
            multiple=True,
        )
        review.submitted_at = timezone.now() - timedelta(days=2)
        review.metadata = {
            **review.metadata,
            "submission_count": 1,
            "review_started_by": admin.display_name,
        }
        review.save(update_fields=["submitted_at", "metadata", "updated_at"])
        ApplicationDependency.objects.update_or_create(
            application=draft,
            depends_on=review,
        )

        self.stdout.write(self.style.SUCCESS("ABDM experience demo is ready."))
        self.stdout.write("")
        self.stdout.write(f"Applicant:   {APPLICANT_EMAIL}")
        self.stdout.write(f"Contributor: {CONTRIBUTOR_EMAIL}")
        self.stdout.write(f"OHC admin:   {ADMIN_EMAIL}")
        self.stdout.write(f"Password:    {password}")
        self.stdout.write("")
        self.stdout.write(f"Product workspace:   /products/{PRODUCT_SLUG}/")
        self.stdout.write(f"Applicant workspace: /applications/{DRAFT_REFERENCE}/")
        self.stdout.write(f"Review workspace:    /ohc/applications/{REVIEW_REFERENCE}/")

    def _user(
        self,
        email: str,
        name: str,
        password: str,
        *,
        platform: bool,
    ):
        user, _created = get_user_model().objects.get_or_create(
            email=email,
            defaults={"name": name},
        )
        user.name = name
        user.is_active = True
        user.is_ohc_team = platform
        user.is_staff = platform
        user.is_superuser = False
        user.set_password(password)
        user.save()
        EmailAddress.objects.update_or_create(
            user=user,
            email=email,
            defaults={"verified": True, "primary": True},
        )
        return user

    def _organisation(self, applicant, contributor):
        organisation, _created = Organisation.objects.update_or_create(
            slug=ORGANISATION_SLUG,
            defaults={
                "name": ORGANISATION_NAME,
                "legal_name": ORGANISATION_NAME,
                "website": "https://example.com/arogya-digital",
                "city": "Bengaluru",
                "state": "Karnataka",
                "deployment_regions": "Karnataka, Kerala, Tamil Nadu",
                "technical_contact_name": APPLICANT_NAME,
                "technical_contact_email": APPLICANT_EMAIL,
                "technical_contact_phone": "+91 98765 43210",
                "verification_status": Organisation.VerificationStatus.VERIFIED,
                "verified_at": timezone.now(),
                "onboarded_at": timezone.now(),
            },
        )
        Membership.objects.update_or_create(
            organisation=organisation,
            user=applicant,
            defaults={"role": Role.OWNER},
        )
        Membership.objects.update_or_create(
            organisation=organisation,
            user=contributor,
            defaults={"role": Role.DEVELOPER},
        )
        return organisation

    def _product(self, organisation, applicant):
        product, _created = Product.objects.update_or_create(
            organisation=organisation,
            slug=PRODUCT_SLUG,
            defaults={
                "name": PRODUCT_NAME,
                "product_type": ProductType.HMIS,
                "description": (
                    "A multi-facility digital health platform for registration, "
                    "clinical workflows, claims, and consented health-data exchange."
                ),
                "website": "https://example.com/arogya-one",
                "current_facility_count": 18,
                "deployment_regions": "Karnataka\nKerala\nTamil Nadu",
                "metadata": {"seed_key": PRODUCT_SLUG},
                "created_by": applicant,
            },
        )
        return product

    def _application(
        self,
        *,
        reference,
        product,
        applicant,
        admin,
        status,
    ):
        application, _created = ApplicationInstance.objects.update_or_create(
            reference=reference,
            defaults={
                "application_type": APPLICATION_TYPE,
                "title": "ABDM production access",
                "product": product,
                "created_by": applicant,
                "status": status,
                "metadata": {
                    "seed_key": reference,
                    "progress_percent": 0,
                },
                "outcome": {},
                "decided_at": None,
                "decided_by": None,
            },
        )
        definition = registry.get(application.application_type)
        materialize_application_forms(
            application=application,
            definition=definition,
            user=applicant,
        )
        ApplicationAccess.objects.update_or_create(
            application=application,
            user=applicant,
            defaults={
                "role_key": "applicant_owner",
                "direct_permissions": [],
                "granted_by": applicant,
            },
        )
        ApplicationAccess.objects.update_or_create(
            application=application,
            user=admin,
            defaults={
                "role_key": "decision_maker",
                "direct_permissions": [],
                "granted_by": admin,
            },
        )
        if not application.events.filter(title="Demo application seeded").exists():
            ApplicationEvent.objects.create(
                application=application,
                actor=applicant,
                kind=EventKind.CREATED,
                title="Demo application seeded",
                status_after=status,
            )
        issue_product_outcomes(
            application=application,
            actor=applicant,
            outcomes=definition.initial_product_outcomes(application, applicant),
        )
        return application

    def _submissions(self, application, applicant, data_by_key):
        result = {}
        definition = registry.get(application.application_type)
        for form_key, data in data_by_key.items():
            form_definition = definition.get_form(form_key)
            form_use = application.form_uses.select_related("form").get(
                form_key=form_key,
            )
            form_record = form_use.form
            submission = FormSubmission.objects.filter(
                form=form_record,
                origin_application=application,
                metadata__seed_key=f"{application.reference}:{form_key}",
            ).first()
            current = form_record.current_submission
            if current and (submission is None or current.pk != submission.pk):
                current.is_current = False
                current.save(update_fields=["is_current"])
            submission_values = {
                "form_key": form_key,
                "data": data,
                "field_schema": form_field_schema(form_definition.form_class()),
                "status": "completed",
                "schema_version": form_definition.schema_version,
                "valid_until": (
                    data.get(form_definition.valid_until_field)
                    if form_definition.valid_until_field
                    else None
                ),
                "submitted_by": applicant,
                "is_current": True,
                "metadata": {"seed_key": f"{application.reference}:{form_key}"},
            }
            if submission is None:
                latest = form_record.submissions.order_by(
                    "-submission_number",
                    "-revision",
                ).first()
                submission = FormSubmission.objects.create(
                    form=form_record,
                    origin_application=application,
                    submission_number=latest.submission_number if latest else 1,
                    revision=latest.revision + 1 if latest else 1,
                    **submission_values,
                )
            else:
                for key, value in submission_values.items():
                    setattr(submission, key, value)
                submission.save()
            form_use.selected_submission = submission
            form_use.save(update_fields=["selected_submission", "updated_at"])
            result[form_key] = submission
        recalculate_progress(application)
        application.refresh_from_db()
        context = application_context(application, applicant)
        metadata = dict(application.metadata)
        for form_definition in definition.forms:
            data = data_by_key.get(form_definition.key)
            if data:
                metadata.update(form_definition.metadata_updates(data, context))
        application.metadata = metadata
        application.save(update_fields=["metadata", "updated_at"])
        return result

    def _attachment(
        self,
        submission,
        applicant,
        field_key,
        name,
        *,
        multiple=False,
    ) -> None:
        attachment = submission.attachments.filter(
            field_key=field_key,
            original_name=name,
            is_current=True,
        ).first()
        if attachment is None:
            attachment = FormAttachment.objects.create(
                submission=submission,
                field_key=field_key,
                file=ContentFile(PDF_BYTES, name=name),
                original_name=name,
                content_type="application/pdf",
                size=len(PDF_BYTES),
                uploaded_by=applicant,
            )
        data = dict(submission.data)
        if multiple:
            data[field_key] = [
                {
                    "attachment_id": item.pk,
                    "name": item.original_name,
                    "size": item.size,
                }
                for item in submission.attachments.filter(
                    field_key=field_key,
                    is_current=True,
                ).order_by("created_at", "pk")
            ]
        else:
            data[field_key] = {
                "attachment_id": attachment.pk,
                "name": attachment.original_name,
                "size": attachment.size,
            }
        submission.data = data
        submission.save(update_fields=["data", "updated_at"])

    def _security_certification_history(self, application, current, applicant):
        if current.submission_number != CURRENT_CERTIFICATION_SUBMISSION_NUMBER:
            current.submission_number = CURRENT_CERTIFICATION_SUBMISSION_NUMBER
            current.save(update_fields=["submission_number", "updated_at"])
        historical, _created = FormSubmission.objects.update_or_create(
            form=current.form,
            origin_application=None,
            form_key="security_certification",
            submission_number=1,
            revision=1,
            defaults={
                "is_current": False,
                "status": "completed",
                "data": {
                    "certification_type": "wasa",
                    "certification_name": "ABDM Web Application Security Assessment",
                    "issuing_body": "Example Security Assurance Labs",
                    "certificate_number": "WASA-DEMO-2025-044",
                    "issued_on": (
                        timezone.localdate() - timedelta(days=730)
                    ).isoformat(),
                    "expires_on": (
                        timezone.localdate() - timedelta(days=365)
                    ).isoformat(),
                    "scope_summary": (
                        "Gateway and consent-manager integration endpoints for the "
                        "previous production release."
                    ),
                },
                "field_schema": form_field_schema(
                    registry.get(application.application_type)
                    .get_form("security_certification")
                    .form_class(),
                ),
                "schema_version": 1,
                "revision": 1,
                "valid_until": timezone.localdate() - timedelta(days=365),
                "submitted_by": applicant,
                "metadata": {
                    "seed_key": f"{application.reference}:security-certification-old",
                },
            },
        )
        return historical

    def _remove_demo_applications(self) -> None:
        applications = ApplicationInstance.objects.filter(
            reference__in=[DRAFT_REFERENCE, REVIEW_REFERENCE],
        )
        form_record_ids = list(
            FormRecord.objects.filter(
                application_uses__application__in=applications,
            ).values_list("pk", flat=True),
        )
        for attachment in FormAttachment.objects.filter(
            submission__form_id__in=form_record_ids,
        ):
            attachment.file.delete(save=False)
        ProductOutcome.objects.filter(source_application__in=applications).delete()
        ApplicationDependency.objects.filter(
            application__in=applications,
        ).delete()
        applications.delete()
        FormRecord.objects.filter(pk__in=form_record_ids).delete()
        Product.objects.filter(
            slug=PRODUCT_SLUG,
            organisation__slug=ORGANISATION_SLUG,
        ).delete()
