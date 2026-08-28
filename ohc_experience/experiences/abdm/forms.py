from __future__ import annotations

from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

ORGANISATION_TYPES = [
    ("company", _("Company")),
    ("llp", _("Limited liability partnership")),
    ("partnership", _("Partnership firm")),
    ("proprietorship", _("Proprietorship")),
    ("trust_society", _("Trust or society")),
    ("government", _("Government or statutory institution")),
]

PRODUCT_TYPES = [
    ("hmis", _("Hospital management information system (HMIS)")),
    ("lmis", _("Laboratory management information system (LMIS)")),
    ("emr", _("Electronic medical record (EMR)")),
    ("phr_locker", _("Personal health record / health locker")),
    ("telemedicine", _("Telemedicine platform")),
    ("pharmacy", _("Pharmacy system")),
    ("connector", _("ABDM connector or middleware")),
    ("other", _("Other digital health solution")),
]

ABDM_ROLES = [
    ("hip", _("Health Information Provider (HIP)")),
    ("hiu", _("Health Information User (HIU)")),
    ("health_locker", _("Health locker")),
]

MILESTONES = [
    ("m1", _("M1 - ABHA creation, capture and verification")),
    ("m2", _("M2 - HIP and consented health-record sharing")),
    ("m3", _("M3 - HIU and consented health-record access")),
]


class ExperienceForm(forms.Form):
    """Lets a static form receive workflow context without coupling Django to it."""

    def __init__(
        self,
        *args,
        experience_context=None,
        existing_files=None,
        **kwargs,
    ) -> None:
        self.experience_context = experience_context
        self.existing_files = existing_files or {}
        super().__init__(*args, **kwargs)

    def require_upload(self, field_name: str, label: str) -> None:
        has_upload = (
            self.cleaned_data.get(field_name) or field_name in self.existing_files
        )
        if not has_upload:
            self.add_error(
                field_name,
                _("Upload %(label)s before completing this form.") % {"label": label},
            )


def validate_upload(upload, *, extensions: set[str], max_mb: int) -> None:
    if upload is None:
        return
    extension = Path(upload.name).suffix.lower()
    if extension not in extensions:
        allowed = ", ".join(sorted(extensions))
        raise ValidationError(
            _("Use one of these file types: %(types)s.") % {"types": allowed},
        )
    if upload.size > max_mb * 1024 * 1024:
        raise ValidationError(
            _("The file must be smaller than %(size)s MB.") % {"size": max_mb},
        )


def validate_https(value: str) -> str:
    if value and not value.lower().startswith("https://"):
        raise ValidationError(_("Use an HTTPS URL for production endpoints."))
    return value


class OrganisationProfileForm(ExperienceForm):
    legal_entity_name = forms.CharField(label=_("Legal entity name"), max_length=255)
    organisation_type = forms.ChoiceField(
        label=_("Organisation type"),
        choices=ORGANISATION_TYPES,
    )
    registration_number = forms.CharField(
        label=_("CIN / LLPIN / registration number"),
        max_length=80,
    )
    registered_address = forms.CharField(
        label=_("Registered address"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    city = forms.CharField(label=_("City"), max_length=120)
    state = forms.CharField(label=_("State or union territory"), max_length=120)
    pincode = forms.RegexField(
        label=_("PIN code"),
        regex=r"^[1-9][0-9]{5}$",
        error_messages={"invalid": _("Enter a valid six-digit Indian PIN code.")},
    )
    website = forms.URLField(label=_("Organisation website"))
    authorised_contact_name = forms.CharField(
        label=_("Authorised contact"),
        max_length=255,
    )
    authorised_contact_email = forms.EmailField(label=_("Contact email"))
    authorised_contact_phone = forms.CharField(
        label=_("Contact phone"),
        max_length=32,
    )


class ProductUseCaseForm(ExperienceForm):
    product_name = forms.CharField(
        label=_("Digital health product name"),
        max_length=255,
    )
    product_version = forms.CharField(
        label=_("Version seeking production access"),
        max_length=80,
    )
    product_type = forms.ChoiceField(label=_("Product type"), choices=PRODUCT_TYPES)
    product_description = forms.CharField(
        label=_("Product and intended use"),
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=_(
            "Describe users, care settings, and the ABDM-enabled patient journey.",
        ),
    )
    current_facility_count = forms.IntegerField(
        label=_("Facilities currently using the product"),
        min_value=0,
    )
    expected_monthly_transactions = forms.IntegerField(
        label=_("Expected monthly ABDM transactions"),
        min_value=0,
    )
    deployment_states = forms.CharField(
        label=_("Planned deployment states / UTs"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Enter one or more states or union territories."),
    )
    target_go_live_date = forms.DateField(
        label=_("Target production go-live"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean_target_go_live_date(self):
        value = self.cleaned_data["target_go_live_date"]
        if value < timezone.localdate():
            raise ValidationError(_("Choose today or a future date."))
        return value


class IntegrationScopeForm(ExperienceForm):
    abdm_roles = forms.MultipleChoiceField(
        label=_("ABDM roles"),
        choices=ABDM_ROLES,
        widget=forms.CheckboxSelectMultiple,
    )
    milestones = forms.MultipleChoiceField(
        label=_("Completed sandbox milestones"),
        choices=MILESTONES,
        widget=forms.CheckboxSelectMultiple,
    )
    sandbox_client_id = forms.CharField(label=_("Sandbox client ID"), max_length=150)
    sandbox_exit_request_id = forms.CharField(
        label=_("Sandbox exit request ID"),
        max_length=150,
        required=False,
    )
    hfr_facility_ids = forms.CharField(
        label=_("HFR facility IDs"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_("One facility ID per line. Do not include facility credentials."),
    )
    health_information_types = forms.MultipleChoiceField(
        label=_("Health information types"),
        choices=[
            ("diagnostic_report", _("Diagnostic report")),
            ("discharge_summary", _("Discharge summary")),
            ("prescription", _("Prescription")),
            ("immunization", _("Immunization record")),
            ("op_consultation", _("Outpatient consultation")),
            ("wellness", _("Wellness record")),
        ],
        widget=forms.CheckboxSelectMultiple,
    )
    integration_approach = forms.ChoiceField(
        label=_("Gateway integration approach"),
        choices=[
            ("direct", _("Direct integration")),
            ("connector", _("Through an ABDM connector / middleware")),
        ],
    )
    connector_name = forms.CharField(
        label=_("Connector name"),
        max_length=255,
        required=False,
    )

    def clean(self):
        cleaned = super().clean()
        roles = cleaned.get("abdm_roles") or []
        milestones = cleaned.get("milestones") or []
        if "hip" in roles and "m2" not in milestones:
            self.add_error(
                "milestones",
                _("HIP production access requires M2 evidence."),
            )
        if "hiu" in roles and "m3" not in milestones:
            self.add_error(
                "milestones",
                _("HIU production access requires M3 evidence."),
            )
        if cleaned.get("integration_approach") == "connector" and not cleaned.get(
            "connector_name",
        ):
            self.add_error(
                "connector_name",
                _("Name the connector used by the product."),
            )
        return cleaned


class HealthLockerOperationsForm(ExperienceForm):
    locker_name = forms.CharField(label=_("Health locker name"), max_length=255)
    locker_endpoint = forms.URLField(
        label=_("Production health locker endpoint"),
        validators=[validate_https],
    )
    custodian_model = forms.ChoiceField(
        label=_("Health-record custodian model"),
        choices=[
            ("first_party", _("Operated directly by the applicant")),
            ("partner", _("Operated with a disclosed technology partner")),
        ],
    )
    custodian_partner = forms.CharField(
        label=_("Custodian technology partner"),
        max_length=255,
        required=False,
    )
    account_deletion_sla_hours = forms.IntegerField(
        label=_("Account deletion SLA (hours)"),
        min_value=1,
        max_value=720,
    )
    portability_supported = forms.BooleanField(
        label=_("Users can export or transfer their locker records"),
    )
    grievance_email = forms.EmailField(label=_("Locker grievance contact"))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("custodian_model") == "partner" and not cleaned.get(
            "custodian_partner",
        ):
            self.add_error(
                "custodian_partner",
                _("Name the partner operating the health locker."),
            )
        return cleaned


class TechnicalReadinessForm(ExperienceForm):
    production_callback_url = forms.URLField(
        label=_("Production gateway callback base URL"),
        validators=[validate_https],
    )
    health_check_url = forms.URLField(
        label=_("Health-check URL"),
        validators=[validate_https],
    )
    public_key_url = forms.URLField(
        label=_("Public encryption / signing key URL"),
        validators=[validate_https],
    )
    outbound_ip_addresses = forms.CharField(
        label=_("Outbound production IP addresses"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("One IPv4 or IPv6 address per line."),
    )
    hosting_region = forms.CharField(label=_("Hosting region"), max_length=150)
    uptime_commitment = forms.DecimalField(
        label=_("Monthly uptime commitment (%)"),
        min_value=95,
        max_value=100,
        decimal_places=2,
    )
    technical_contact_email = forms.EmailField(label=_("24x7 technical contact email"))
    incident_contact_phone = forms.CharField(
        label=_("Incident escalation phone"),
        max_length=32,
    )
    callback_idempotency = forms.BooleanField(
        label=_("Callbacks are idempotent and safe to retry"),
    )
    secrets_confirmation = forms.BooleanField(
        label=_("No private keys, client secrets, or patient data are included here"),
    )


class SecurityComplianceForm(ExperienceForm):
    privacy_policy_url = forms.URLField(label=_("Published privacy policy URL"))
    security_assessment_agency = forms.CharField(
        label=_("Security assessment agency"),
        max_length=255,
    )
    assessment_date = forms.DateField(
        label=_("Assessment completion date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    wasa_certificate_number = forms.CharField(
        label=_("WASA certificate number"),
        max_length=150,
        required=False,
    )
    security_audit_report = forms.FileField(
        label=_("Security assessment report"),
        required=False,
        help_text=_("PDF, up to 10 MB. A replacement creates a new retained revision."),
    )
    data_retention_policy = forms.CharField(
        label=_("Health-data retention and deletion policy"),
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    incident_response_summary = forms.CharField(
        label=_("Security incident response process"),
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    encryption_at_rest = forms.BooleanField(
        label=_("Sensitive data is encrypted at rest"),
    )
    encryption_in_transit = forms.BooleanField(
        label=_(
            "All external and internal health-data transport uses TLS",
        ),
    )
    least_privilege = forms.BooleanField(
        label=_(
            "Production access follows least-privilege controls",
        ),
    )

    def clean_security_audit_report(self):
        upload = self.cleaned_data.get("security_audit_report")
        validate_upload(upload, extensions={".pdf"}, max_mb=10)
        return upload

    def clean(self):
        cleaned = super().clean()
        self.require_upload("security_audit_report", _("security assessment report"))
        return cleaned


class ConformanceEvidenceForm(ExperienceForm):
    demonstration_date = forms.DateField(
        label=_("ABDM functionality demonstration date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    tested_milestones = forms.MultipleChoiceField(
        label=_("Milestones covered by functional testing"),
        choices=MILESTONES,
        widget=forms.CheckboxSelectMultiple,
    )
    functional_testing_agency = forms.CharField(
        label=_("Functional testing agency / team"),
        max_length=255,
    )
    functional_certificate_number = forms.CharField(
        label=_("Functional certification reference"),
        max_length=150,
    )
    functional_test_report = forms.FileField(
        label=_("Functional testing report"),
        required=False,
        help_text=_("PDF, up to 15 MB."),
    )
    signed_undertaking = forms.FileField(
        label=_("Signed production undertaking"),
        required=False,
        help_text=_("Signed PDF, up to 10 MB."),
    )
    demo_recording_url = forms.URLField(
        label=_("Demonstration recording URL"),
        required=False,
    )
    test_request_ids = forms.CharField(
        label=_("Representative sandbox request IDs"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Use test traffic only. One request ID per line."),
    )
    known_limitations = forms.CharField(
        label=_("Known limitations and mitigations"),
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
    )

    def clean_functional_test_report(self):
        upload = self.cleaned_data.get("functional_test_report")
        validate_upload(upload, extensions={".pdf"}, max_mb=15)
        return upload

    def clean_signed_undertaking(self):
        upload = self.cleaned_data.get("signed_undertaking")
        validate_upload(upload, extensions={".pdf"}, max_mb=10)
        return upload

    def clean(self):
        cleaned = super().clean()
        self.require_upload("functional_test_report", _("functional testing report"))
        self.require_upload("signed_undertaking", _("signed undertaking"))
        return cleaned


class DeclarationForm(ExperienceForm):
    signatory_name = forms.CharField(label=_("Authorised signatory"), max_length=255)
    signatory_designation = forms.CharField(label=_("Designation"), max_length=150)
    signatory_email = forms.EmailField(label=_("Official email"))
    declaration_date = forms.DateField(
        label=_("Declaration date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    information_accurate = forms.BooleanField(
        label=_("I confirm that the information and evidence are accurate"),
    )
    lawful_processing = forms.BooleanField(
        label=_(
            "The organisation maintains lawful privacy and data-protection controls",
        ),
    )
    change_notification = forms.BooleanField(
        label=_(
            "Material changes will be reported before or after production access",
        ),
    )
    authority_confirmation = forms.BooleanField(
        label=_("I am authorised to submit this application for the organisation"),
    )

    def clean_declaration_date(self):
        value = self.cleaned_data["declaration_date"]
        if value > timezone.localdate():
            raise ValidationError(_("The declaration date cannot be in the future."))
        return value


class ActionForm(ExperienceForm):
    pass


class RaiseQueryForm(ActionForm):
    subject = forms.CharField(label=_("Query subject"), max_length=255)
    related_form = forms.ChoiceField(
        label=_("Related form"),
        required=False,
    )
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
        definition = self.experience_context and self.experience_context.application
        if definition:
            from ohc_experience.experiences.registry import registry  # noqa: PLC0415

            application_definition = registry.get(definition.application_type)
            self.fields["related_form"].choices = [
                ("", _("Whole application")),
                *[(item.key, item.name) for item in application_definition.forms],
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
    production_client_id = forms.CharField(
        label=_("Production client ID"),
        max_length=150,
    )
    approved_milestones = forms.MultipleChoiceField(
        label=_("Approved milestones"),
        choices=MILESTONES,
        widget=forms.CheckboxSelectMultiple,
    )
    effective_date = forms.DateField(
        label=_("Access effective date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    certificate_reference = forms.CharField(
        label=_("Certification / approval reference"),
        max_length=150,
    )
    note = forms.CharField(
        label=_("Decision note"),
        widget=forms.Textarea(attrs={"rows": 4}),
        required=False,
    )


class RejectionForm(ActionForm):
    reason = forms.ChoiceField(
        label=_("Primary reason"),
        choices=[
            ("functional", _("Functional testing incomplete")),
            ("security", _("Security assessment incomplete or failed")),
            ("documentation", _("Required documentation missing")),
            ("eligibility", _("Entity or use case is not eligible")),
            ("other", _("Other")),
        ],
    )
    details = forms.CharField(
        label=_("Reason and next steps"),
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    note = forms.CharField(
        label=_("Internal decision note"),
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
    )
