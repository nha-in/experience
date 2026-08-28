from __future__ import annotations

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ohc_experience.experiences import permission_keys
from ohc_experience.experiences.definitions import COMMON_PERMISSIONS
from ohc_experience.experiences.definitions import ActionResult
from ohc_experience.experiences.definitions import ApplicationAction
from ohc_experience.experiences.definitions import ApplicationDefinition
from ohc_experience.experiences.definitions import ApplicationFormAction
from ohc_experience.experiences.definitions import ApplicationFormDefinition
from ohc_experience.experiences.definitions import FormActionResult
from ohc_experience.experiences.definitions import QueryRequest
from ohc_experience.experiences.definitions import RoleDefinition
from ohc_experience.experiences.definitions import StatusDefinition
from ohc_experience.experiences.models import QueryStatus
from ohc_experience.experiences.registry import registry

from .forms import ApplicantQueryForm
from .forms import ApprovalForm
from .forms import ConformanceEvidenceForm
from .forms import DeclarationForm
from .forms import HealthLockerOperationsForm
from .forms import IntegrationScopeForm
from .forms import OrganisationProfileForm
from .forms import ProductUseCaseForm
from .forms import RaiseQueryForm
from .forms import RejectionForm
from .forms import SecurityComplianceForm
from .forms import TechnicalReadinessForm


class OrganisationProfile(ApplicationFormDefinition):
    key = "organisation_profile"
    name = _("Organisation profile")
    description = _("Legal identity and the authorised production-access contact.")
    form_class = OrganisationProfileForm
    allow_updates = True

    @classmethod
    def get_initial(cls, context):
        initial = super().get_initial(context)
        organisation = context.application.organisation
        user = context.user
        defaults = {
            "legal_entity_name": organisation.display_name,
            "registered_address": "",
            "city": organisation.city,
            "state": organisation.state,
            "website": organisation.website,
            "authorised_contact_name": organisation.technical_contact_name
            or user.display_name,
            "authorised_contact_email": organisation.technical_contact_email
            or user.email,
            "authorised_contact_phone": organisation.technical_contact_phone
            or user.phone_number,
        }
        return {**defaults, **initial}

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {
            "legal_entity_name": cleaned_data["legal_entity_name"],
            "contact_email": cleaned_data["authorised_contact_email"],
        }


class ProductUseCase(ApplicationFormDefinition):
    key = "product_use_case"
    name = _("Product and use case")
    description = _("The product, deployment footprint, users, and planned launch.")
    form_class = ProductUseCaseForm
    dependencies = (OrganisationProfile.key,)
    allow_updates = True

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {
            "product_name": cleaned_data["product_name"],
            "product_version": cleaned_data["product_version"],
            "target_go_live_date": cleaned_data["target_go_live_date"],
        }


class IntegrationScope(ApplicationFormDefinition):
    key = "integration_scope"
    name = _("ABDM integration scope")
    description = _("HIP, HIU, health-locker roles and completed sandbox milestones.")
    form_class = IntegrationScopeForm
    dependencies = (ProductUseCase.key,)
    allow_updates = True

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {
            "abdm_roles": cleaned_data["abdm_roles"],
            "milestones": cleaned_data["milestones"],
            "sandbox_client_id": cleaned_data["sandbox_client_id"],
        }


class HealthLockerOperations(ApplicationFormDefinition):
    key = "health_locker_operations"
    name = _("Health locker operations")
    description = _(
        "Operational controls required only when health locker access is requested.",
    )
    form_class = HealthLockerOperationsForm
    dependencies = (IntegrationScope.key,)
    allow_updates = True

    @classmethod
    def is_applicable(cls, context):
        integration_scope = context.form_data(IntegrationScope.key)
        return "health_locker" in integration_scope.get("abdm_roles", [])

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {
            "health_locker_name": cleaned_data["locker_name"],
            "health_locker_custodian_model": cleaned_data["custodian_model"],
        }


class TechnicalReadiness(ApplicationFormDefinition):
    key = "technical_readiness"
    name = _("Technical readiness")
    description = _(
        "Production endpoints, network controls, reliability, and escalation.",
    )
    form_class = TechnicalReadinessForm
    dependencies = (IntegrationScope.key,)
    allow_updates = True


class VerifySecurityEvidence(ApplicationFormAction):
    key = "verify_evidence"
    name = _("Verify security evidence")
    description = _(
        "Record that the current security submission and assessment report "
        "were reviewed.",
    )
    permission = permission_keys.REVIEW_APPLICATION
    allowed_statuses = frozenset({"under_review", "revision_submitted"})

    @classmethod
    def extra_availability(cls, context, submission):
        if submission.metadata.get("verified_revision") == submission.revision:
            return False, _("The current security evidence is already verified.")
        return True, ""

    @classmethod
    def outcome_label(cls, context, submission):
        if (
            submission
            and submission.metadata.get("verified_revision") == submission.revision
        ):
            return str(_("Evidence verified"))
        return ""

    @classmethod
    def perform(cls, context, submission, cleaned_data):
        return FormActionResult(
            message=_("Security evidence verified"),
            metadata_updates={
                "evidence_verified_at": timezone.now(),
                "evidence_verified_by": context.user.pk,
                "evidence_verified_by_name": context.user.display_name,
                "verified_revision": submission.revision,
            },
        )


class SecurityCompliance(ApplicationFormDefinition):
    key = "security_compliance"
    name = _("Security and privacy")
    description = _("Assessment evidence and operational data-protection controls.")
    form_class = SecurityComplianceForm
    dependencies = (TechnicalReadiness.key,)
    allow_updates = True
    actions = (VerifySecurityEvidence,)

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {
            "security_assessment_date": cleaned_data["assessment_date"],
            "security_assessment_agency": cleaned_data["security_assessment_agency"],
        }


class ConformanceEvidence(ApplicationFormDefinition):
    key = "conformance_evidence"
    name = _("Conformance evidence")
    description = _("Functional testing report, undertaking, demo, and request traces.")
    form_class = ConformanceEvidenceForm
    dependencies = (SecurityCompliance.key,)
    allow_updates = True

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {
            "tested_milestones": cleaned_data["tested_milestones"],
            "functional_certificate_number": cleaned_data[
                "functional_certificate_number"
            ],
        }


class Declaration(ApplicationFormDefinition):
    key = "declaration"
    name = _("Authorised declaration")
    description = _("Final declarations and accountable signatory details.")
    form_class = DeclarationForm
    dependencies = (ConformanceEvidence.key,)
    allow_updates = False

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {
            "signatory_name": cleaned_data["signatory_name"],
            "declaration_date": cleaned_data["declaration_date"],
        }


class SubmitApplication(ApplicationAction):
    key = "submit"
    name = _("Submit for review")
    description = _("Lock the completed application into the OHC review queue.")
    permission = permission_keys.SUBMIT_APPLICATION
    allowed_statuses = frozenset({"draft", "changes_requested"})

    @classmethod
    def extra_availability(cls, context):
        definition = registry.get(context.application.application_type)
        if not definition.required_forms_complete(context):
            return False, _("Complete every required form before submitting.")
        has_unanswered_query = context.application.query_threads.filter(
            status=QueryStatus.AWAITING_APPLICANT,
        ).exists()
        if context.application.status == "changes_requested" and has_unanswered_query:
            return False, _("Respond to every open query before resubmitting.")
        return True, ""

    @classmethod
    def perform(cls, context, cleaned_data):
        is_revision = context.application.status == "changes_requested"
        count = int(context.application.metadata.get("submission_count", 0)) + 1
        return ActionResult(
            message=_("Application resubmitted")
            if is_revision
            else _("Application submitted"),
            new_status="revision_submitted" if is_revision else "submitted",
            metadata_updates={
                "submission_count": count,
                "last_submitted_by": context.user.display_name,
            },
        )


class AskReviewTeam(ApplicationAction):
    key = "ask_review_team"
    name = _("Ask review team")
    description = _(
        "Open a question about this application or request support from the "
        "review team.",
    )
    permission = permission_keys.OPEN_QUERY
    allowed_statuses = frozenset(
        {
            "draft",
            "submitted",
            "under_review",
            "changes_requested",
            "revision_submitted",
            "approved",
            "rejected",
        },
    )
    form_class = ApplicantQueryForm

    @classmethod
    def perform(cls, context, cleaned_data):
        return ActionResult(
            message=_("Question sent to the review team"),
            query=QueryRequest(
                subject=cleaned_data["subject"],
                message=cleaned_data["message"],
                form_key=cleaned_data.get("related_form", ""),
                initial_status=QueryStatus.AWAITING_REVIEWER,
            ),
        )


class StartReview(ApplicationAction):
    key = "start_review"
    name = _("Start review")
    description = _("Move the submitted application into active review.")
    permission = permission_keys.REVIEW_APPLICATION
    allowed_statuses = frozenset({"submitted", "revision_submitted"})

    @classmethod
    def perform(cls, context, cleaned_data):
        return ActionResult(
            message=_("Review started"),
            new_status="under_review",
            metadata_updates={"review_started_by": context.user.display_name},
        )


class RaiseQuery(ApplicationAction):
    key = "raise_query"
    name = _("Raise query")
    description = _("Request clarification, corrected data, or replacement evidence.")
    permission = permission_keys.RAISE_QUERY
    allowed_statuses = frozenset(
        {"submitted", "under_review", "changes_requested", "revision_submitted"},
    )
    form_class = RaiseQueryForm

    @classmethod
    def perform(cls, context, cleaned_data):
        return ActionResult(
            message=_("Query raised"),
            new_status="changes_requested",
            query=QueryRequest(
                subject=cleaned_data["subject"],
                message=cleaned_data["message"],
                form_key=cleaned_data.get("related_form", ""),
                due_at=cleaned_data.get("due_at"),
            ),
        )


class ApproveApplication(ApplicationAction):
    key = "approve"
    name = _("Approve production access")
    description = _("Record approved milestones and production credentials reference.")
    permission = permission_keys.APPROVE_APPLICATION
    allowed_statuses = frozenset({"under_review", "revision_submitted"})
    form_class = ApprovalForm

    @classmethod
    def extra_availability(cls, context):
        if context.application.query_threads.exclude(
            status=QueryStatus.RESOLVED,
        ).exists():
            return False, _("Resolve every application query before approval.")
        return True, ""

    @classmethod
    def perform(cls, context, cleaned_data):
        return ActionResult(
            message=_("Production access approved"),
            new_status="approved",
            outcome_updates={
                "decision": "approved",
                "production_client_id": cleaned_data["production_client_id"],
                "approved_milestones": cleaned_data["approved_milestones"],
                "effective_date": cleaned_data["effective_date"],
                "certificate_reference": cleaned_data["certificate_reference"],
                "decision_note": cleaned_data.get("note", ""),
                "decided_by": context.user.display_name,
            },
        )


class RejectApplication(ApplicationAction):
    key = "reject"
    name = _("Reject application")
    description = _("Record a final rejection with clear reasons and next steps.")
    permission = permission_keys.REJECT_APPLICATION
    allowed_statuses = frozenset(
        {"submitted", "under_review", "changes_requested", "revision_submitted"},
    )
    form_class = RejectionForm
    style = "destructive"

    @classmethod
    def perform(cls, context, cleaned_data):
        return ActionResult(
            message=_("Application rejected"),
            new_status="rejected",
            outcome_updates={
                "decision": "rejected",
                "reason": cleaned_data["reason"],
                "details": cleaned_data["details"],
                "decision_note": cleaned_data.get("note", ""),
                "decided_by": context.user.display_name,
            },
        )


@registry.register
class ABDMProductionAccess(ApplicationDefinition):
    key = "abdm_production_access"
    name = _("ABDM production access")
    description = _(
        "A full sandbox-exit and production-access review for an ABDM-integrated "
        "digital health product.",
    )
    reference_prefix = "ABDM"
    statuses = (
        StatusDefinition("draft", _("Draft"), _("Forms are being completed."), "muted"),
        StatusDefinition(
            "submitted",
            _("Submitted"),
            _("Waiting for a reviewer to begin."),
            "info",
        ),
        StatusDefinition(
            "under_review",
            _("Under review"),
            _("The OHC team is reviewing the evidence."),
            "warning",
        ),
        StatusDefinition(
            "changes_requested",
            _("Changes requested"),
            _("One or more reviewer queries need a response."),
            "destructive",
        ),
        StatusDefinition(
            "revision_submitted",
            _("Revision submitted"),
            _("Updated evidence is ready for review."),
            "info",
        ),
        StatusDefinition(
            "approved",
            _("Approved"),
            _("Production access has been approved."),
            "success",
            terminal=True,
        ),
        StatusDefinition(
            "rejected",
            _("Rejected"),
            _("A final rejection has been recorded."),
            "destructive",
            terminal=True,
        ),
        StatusDefinition(
            "withdrawn",
            _("Withdrawn"),
            _("The applicant withdrew this application."),
            "muted",
            terminal=True,
        ),
    )
    permissions = COMMON_PERMISSIONS
    roles = (
        RoleDefinition(
            "applicant_owner",
            _("Application owner"),
            _("Completes, submits, and manages applicant access."),
            "organisation",
            frozenset(
                {
                    permission_keys.VIEW_APPLICATION,
                    permission_keys.EDIT_FORMS,
                    permission_keys.SUBMIT_APPLICATION,
                    permission_keys.VIEW_QUERIES,
                    permission_keys.OPEN_QUERY,
                    permission_keys.RESPOND_QUERIES,
                    permission_keys.MANAGE_APPLICANT_ACCESS,
                },
            ),
        ),
        RoleDefinition(
            "applicant_submitter",
            _("Applicant submitter"),
            _("Completes forms, answers queries, and can submit."),
            "organisation",
            frozenset(
                {
                    permission_keys.VIEW_APPLICATION,
                    permission_keys.EDIT_FORMS,
                    permission_keys.SUBMIT_APPLICATION,
                    permission_keys.VIEW_QUERIES,
                    permission_keys.OPEN_QUERY,
                    permission_keys.RESPOND_QUERIES,
                },
            ),
        ),
        RoleDefinition(
            "applicant_contributor",
            _("Applicant contributor"),
            _("Completes forms and answers queries without submitting."),
            "organisation",
            frozenset(
                {
                    permission_keys.VIEW_APPLICATION,
                    permission_keys.EDIT_FORMS,
                    permission_keys.VIEW_QUERIES,
                    permission_keys.OPEN_QUERY,
                    permission_keys.RESPOND_QUERIES,
                },
            ),
        ),
        RoleDefinition(
            "applicant_viewer",
            _("Applicant viewer"),
            _("Read-only access to application data and queries."),
            "organisation",
            frozenset(
                {
                    permission_keys.VIEW_APPLICATION,
                    permission_keys.VIEW_QUERIES,
                },
            ),
        ),
        RoleDefinition(
            "reviewer",
            _("Reviewer"),
            _("Reviews forms and can raise or resolve queries."),
            "platform",
            frozenset(
                {
                    permission_keys.VIEW_APPLICATION,
                    permission_keys.VIEW_QUERIES,
                    permission_keys.REVIEW_APPLICATION,
                    permission_keys.RAISE_QUERY,
                    permission_keys.RESOLVE_QUERY,
                },
            ),
        ),
        RoleDefinition(
            "decision_maker",
            _("Decision maker"),
            _("Reviews, manages reviewers, and records final decisions."),
            "platform",
            frozenset(
                {
                    permission_keys.VIEW_APPLICATION,
                    permission_keys.VIEW_QUERIES,
                    permission_keys.REVIEW_APPLICATION,
                    permission_keys.RAISE_QUERY,
                    permission_keys.RESOLVE_QUERY,
                    permission_keys.APPROVE_APPLICATION,
                    permission_keys.REJECT_APPLICATION,
                    permission_keys.MANAGE_REVIEW_ACCESS,
                },
            ),
        ),
        RoleDefinition(
            "review_observer",
            _("Review observer"),
            _("Read-only access for assurance and oversight."),
            "platform",
            frozenset(
                {
                    permission_keys.VIEW_APPLICATION,
                    permission_keys.VIEW_QUERIES,
                },
            ),
        ),
    )
    forms = (
        OrganisationProfile,
        ProductUseCase,
        IntegrationScope,
        HealthLockerOperations,
        TechnicalReadiness,
        SecurityCompliance,
        ConformanceEvidence,
        Declaration,
    )
    actions = (
        SubmitApplication,
        AskReviewTeam,
        StartReview,
        RaiseQuery,
        ApproveApplication,
        RejectApplication,
    )
