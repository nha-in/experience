"""A small experience that exercises every feature of the engine.

This is the reference for adding an experience (see the app README) and the
definition the engine's own tests run against. It is registered only from the
tests' conftest; the engine ships with an empty registry.

Each form exists for one engine feature:

- ``profile`` — an updatable form whose answers feed application metadata.
- ``scope`` — a form the next one depends on; its channels decide what else
  is required.
- ``locker_operations`` — applicable only when ``scope`` chose ``locker``.
- ``readiness`` — a plain dependent form.
- ``compliance`` — a single upload plus a reviewer-side form action.
- ``certification`` — repeatable, time-bound, with two multiple-file groups.
- ``declaration`` — locked once it is complete.
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
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
from ohc_experience.experiences.fields import MultipleFileField
from ohc_experience.experiences.forms import ExperienceForm
from ohc_experience.experiences.models import QueryStatus
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.uploads import IMAGES
from ohc_experience.experiences.uploads import PDF
from ohc_experience.experiences.uploads import validate_upload
from ohc_experience.experiences.uploads import validate_uploads

CHANNELS = [
    ("web", _("Web application")),
    ("mobile", _("Mobile application")),
    ("locker", _("Health locker")),
]

REJECTION_REASONS = [
    ("evidence", _("Evidence incomplete")),
    ("eligibility", _("Not eligible")),
    ("other", _("Other")),
]

MAX_CERTIFICATE_FILES = 5
MAX_SUPPORTING_FILES = 8


# ── forms ──────────────────────────────────────────────────────────────────


class ProfileForm(ExperienceForm):
    legal_name = forms.CharField(label=_("Legal entity name"), max_length=255)
    contact_email = forms.EmailField(label=_("Contact email"))


class ScopeForm(ExperienceForm):
    channels = forms.MultipleChoiceField(
        label=_("Channels"),
        choices=CHANNELS,
        widget=forms.CheckboxSelectMultiple,
    )


class LockerForm(ExperienceForm):
    locker_name = forms.CharField(label=_("Locker name"), max_length=255)


class ReadinessForm(ExperienceForm):
    endpoint_url = forms.URLField(label=_("Production endpoint"))
    region = forms.CharField(label=_("Hosting region"), max_length=150)


class ComplianceForm(ExperienceForm):
    assessment_date = forms.DateField(
        label=_("Assessment date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    report = forms.FileField(
        label=_("Assessment report"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".pdf"}),
    )

    def clean_report(self):
        upload = self.cleaned_data.get("report")
        validate_upload(upload, extensions=PDF, max_mb=10)
        return upload

    def clean(self):
        cleaned = super().clean()
        self.require_upload("report", _("the assessment report"))
        return cleaned


class CertificationForm(ExperienceForm):
    certificate_number = forms.CharField(label=_("Certificate number"), max_length=150)
    issued_on = forms.DateField(
        label=_("Issued on"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    expires_on = forms.DateField(
        label=_("Valid until"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    certificate_documents = MultipleFileField(
        label=_("Certificate documents"),
        required=False,
        min_files=1,
        max_files=MAX_CERTIFICATE_FILES,
        accept=".pdf",
    )
    supporting_documents = MultipleFileField(
        label=_("Supporting evidence"),
        required=False,
        max_files=MAX_SUPPORTING_FILES,
        accept=".pdf,.png,.jpg,.jpeg",
    )

    def clean_certificate_documents(self):
        uploads = self.cleaned_data.get("certificate_documents", [])
        validate_uploads(uploads, extensions=PDF, max_mb=15)
        return uploads

    def clean_supporting_documents(self):
        uploads = self.cleaned_data.get("supporting_documents", [])
        validate_uploads(uploads, extensions=PDF | IMAGES, max_mb=10)
        return uploads

    def clean(self):
        cleaned = super().clean()
        issued_on = cleaned.get("issued_on")
        expires_on = cleaned.get("expires_on")
        if issued_on and expires_on and expires_on <= issued_on:
            self.add_error(
                "expires_on",
                _("The expiry date must follow the issue date."),
            )
        self.require_upload("certificate_documents", _("certification evidence"))
        return cleaned


class DeclarationForm(ExperienceForm):
    signatory_name = forms.CharField(label=_("Authorised signatory"), max_length=255)
    confirmed = forms.BooleanField(label=_("The information is accurate"))


class ActionForm(ExperienceForm):
    pass


class RaiseQueryForm(ActionForm):
    subject = forms.CharField(label=_("Query subject"), max_length=255)
    related_form = forms.ChoiceField(label=_("Related form"), required=False)
    message = forms.CharField(
        label=_("Question or requested correction"),
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    due_at = forms.DateField(
        label=_("Response due date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        application = self.experience_context and self.experience_context.application
        if application:
            definition = registry.get(application.application_type)
            self.fields["related_form"].choices = [
                ("", _("Whole application")),
                *[(item.key, item.name) for item in definition.forms],
            ]

    def clean_due_at(self):
        value = self.cleaned_data.get("due_at")
        if value and value < timezone.localdate():
            raise ValidationError(_("Choose today or a future date."))
        return value


class ApplicantQueryForm(RaiseQueryForm):
    message = forms.CharField(
        label=_("Question or support request"),
        widget=forms.Textarea(attrs={"rows": 6}),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields.pop("due_at")


class ApprovalForm(ActionForm):
    client_id = forms.CharField(label=_("Client ID"), max_length=150)
    effective_date = forms.DateField(
        label=_("Effective date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    note = forms.CharField(
        label=_("Decision note"),
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
    )


class RejectionForm(ActionForm):
    reason = forms.ChoiceField(label=_("Primary reason"), choices=REJECTION_REASONS)
    details = forms.CharField(
        label=_("Reason and next steps"),
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    note = forms.CharField(
        label=_("Internal decision note"),
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
    )


# ── form definitions ───────────────────────────────────────────────────────


class Profile(ApplicationFormDefinition):
    key = "profile"
    name = _("Organisation profile")
    description = _("Legal identity and the contact for this application.")
    form_class = ProfileForm
    allow_updates = True

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {
            "legal_name": cleaned_data["legal_name"],
            "contact_email": cleaned_data["contact_email"],
        }


class Scope(ApplicationFormDefinition):
    key = "scope"
    name = _("Integration scope")
    description = _("The channels the product integrates through.")
    form_class = ScopeForm
    dependencies = (Profile.key,)
    allow_updates = True

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {"channels": cleaned_data["channels"]}


class LockerOperations(ApplicationFormDefinition):
    key = "locker_operations"
    name = _("Locker operations")
    description = _("Required only when a health locker is in scope.")
    form_class = LockerForm
    dependencies = (Scope.key,)
    allow_updates = True

    @classmethod
    def is_applicable(cls, context):
        return "locker" in context.form_data(Scope.key).get("channels", [])


class Readiness(ApplicationFormDefinition):
    key = "readiness"
    name = _("Technical readiness")
    description = _("Production endpoint and hosting.")
    form_class = ReadinessForm
    dependencies = (Scope.key,)
    allow_updates = True


class VerifyEvidence(ApplicationFormAction):
    key = "verify_evidence"
    name = _("Verify evidence")
    description = _("Record that the current assessment report was reviewed.")
    permission = permission_keys.REVIEW_APPLICATION
    allowed_statuses = frozenset({"under_review", "revision_submitted"})

    @classmethod
    def extra_availability(cls, context, submission):
        if submission.metadata.get("verified_revision") == submission.revision:
            return False, _("The current evidence is already verified.")
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
            message=_("Evidence verified"),
            metadata_updates={
                "verified_at": timezone.now(),
                "verified_by": context.user.pk,
                "verified_revision": submission.revision,
            },
        )


class Compliance(ApplicationFormDefinition):
    key = "compliance"
    name = _("Compliance evidence")
    description = _("The assessment report and its date.")
    form_class = ComplianceForm
    dependencies = (Readiness.key,)
    allow_updates = True
    actions = (VerifyEvidence,)


class Certification(ApplicationFormDefinition):
    key = "certification"
    name = _("Security certification")
    description = _("A time-bound certification with retained renewal history.")
    form_class = CertificationForm
    dependencies = (Compliance.key,)
    allow_updates = True
    repeatable = True
    valid_until_field = "expires_on"
    renewal_window_days = 45
    editable_statuses = frozenset(
        {
            "draft",
            "submitted",
            "under_review",
            "changes_requested",
            "revision_submitted",
            "approved",
        },
    )

    @classmethod
    def metadata_updates(cls, cleaned_data, context):
        return {"certificate_number": cleaned_data["certificate_number"]}


class Declaration(ApplicationFormDefinition):
    key = "declaration"
    name = _("Declaration")
    description = _("The accountable signatory's declaration.")
    form_class = DeclarationForm
    dependencies = (Certification.key,)
    allow_updates = False


# ── actions ────────────────────────────────────────────────────────────────


class SubmitApplication(ApplicationAction):
    key = "submit"
    name = _("Submit for review")
    description = _("Lock the completed application into the review queue.")
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
            metadata_updates={"submission_count": count},
        )


class AskReviewTeam(ApplicationAction):
    key = "ask_review_team"
    name = _("Ask review team")
    description = _("Open a question about this application.")
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
        return ActionResult(message=_("Review started"), new_status="under_review")


class RaiseQuery(ApplicationAction):
    key = "raise_query"
    name = _("Raise query")
    description = _("Ask the applicant for a correction or clarification.")
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
    name = _("Approve")
    description = _("Record the approval and the client reference.")
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
            message=_("Application approved"),
            new_status="approved",
            outcome_updates={
                "decision": "approved",
                "client_id": cleaned_data["client_id"],
                "effective_date": cleaned_data["effective_date"],
                "decision_note": cleaned_data.get("note", ""),
                "decided_by": context.user.display_name,
            },
        )


class RejectApplication(ApplicationAction):
    key = "reject"
    name = _("Reject")
    description = _("Record a final rejection with reasons.")
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


# ── the definition ─────────────────────────────────────────────────────────

APPLICANT_BASE = frozenset(
    {
        permission_keys.VIEW_APPLICATION,
        permission_keys.VIEW_QUERIES,
    },
)
APPLICANT_CONTRIBUTOR = APPLICANT_BASE | {
    permission_keys.EDIT_FORMS,
    permission_keys.OPEN_QUERY,
    permission_keys.RESPOND_QUERIES,
}
APPLICANT_SUBMITTER = APPLICANT_CONTRIBUTOR | {permission_keys.SUBMIT_APPLICATION}
REVIEWER = APPLICANT_BASE | {
    permission_keys.REVIEW_APPLICATION,
    permission_keys.RAISE_QUERY,
    permission_keys.RESOLVE_QUERY,
}


class SampleExperience(ApplicationDefinition):
    key = "sample_experience"
    name = _("Sample experience")
    description = _("A small application that exercises every engine feature.")
    reference_prefix = "SMP"
    statuses = (
        StatusDefinition("draft", _("Draft"), _("Forms are being completed."), "muted"),
        StatusDefinition(
            "submitted",
            _("Submitted"),
            _("Waiting for a reviewer."),
            "info",
        ),
        StatusDefinition(
            "under_review",
            _("Under review"),
            _("The review team is reviewing the evidence."),
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
            _("The application was approved."),
            "success",
            terminal=True,
        ),
        StatusDefinition(
            "rejected",
            _("Rejected"),
            _("A final rejection was recorded."),
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
            APPLICANT_SUBMITTER | {permission_keys.MANAGE_APPLICANT_ACCESS},
        ),
        RoleDefinition(
            "applicant_submitter",
            _("Applicant submitter"),
            _("Completes forms, answers queries, and can submit."),
            "organisation",
            APPLICANT_SUBMITTER,
        ),
        RoleDefinition(
            "applicant_contributor",
            _("Applicant contributor"),
            _("Completes forms and answers queries without submitting."),
            "organisation",
            APPLICANT_CONTRIBUTOR,
        ),
        RoleDefinition(
            "applicant_viewer",
            _("Applicant viewer"),
            _("Read-only access to application data and queries."),
            "organisation",
            APPLICANT_BASE,
        ),
        RoleDefinition(
            "reviewer",
            _("Reviewer"),
            _("Reviews forms and can raise or resolve queries."),
            "platform",
            REVIEWER,
        ),
        RoleDefinition(
            "decision_maker",
            _("Decision maker"),
            _("Reviews, manages reviewers, and records final decisions."),
            "platform",
            REVIEWER
            | {
                permission_keys.APPROVE_APPLICATION,
                permission_keys.REJECT_APPLICATION,
                permission_keys.MANAGE_REVIEW_ACCESS,
            },
        ),
        RoleDefinition(
            "review_observer",
            _("Review observer"),
            _("Read-only access for oversight."),
            "platform",
            APPLICANT_BASE,
        ),
    )
    forms = (
        Profile,
        Scope,
        LockerOperations,
        Readiness,
        Compliance,
        Certification,
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


def register() -> type[SampleExperience]:
    """Register the sample once; safe to call from more than one place."""
    if SampleExperience.key not in {item.key for item in registry.all()}:
        registry.register(SampleExperience)
    return SampleExperience
