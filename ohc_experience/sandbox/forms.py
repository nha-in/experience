from django import forms
from django.core.exceptions import ValidationError

from ohc_experience.experiences.fields import MultipleFileField
from ohc_experience.experiences.forms import ExperienceForm

from .catalog import MILESTONE_CHOICES
from .catalog import MILESTONES
from .catalog import canonical_keys
from .uploads import validate_pdf
from .uploads import validate_upload_size


class SandboxForm(ExperienceForm):
    required_uploads = ()
    schema_version = 1

    def __init__(self, *args, draft=False, **kwargs):
        self.draft = draft
        super().__init__(*args, **kwargs)
        if draft:
            for field in self.fields.values():
                field.required = False

    def clean(self):
        cleaned = super().clean()
        if not self.draft:
            for key in self.required_uploads:
                self.require_upload(key, self.fields[key].label or key)
        return cleaned


class OrganisationForm(SandboxForm):
    name = forms.CharField(label="Name of the entity", max_length=255)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    entity_type = forms.ChoiceField(
        label="Type of entity",
        choices=[
            ("private_company", "Private company"),
            ("government", "Government body"),
            ("sole_proprietor", "Sole proprietorship"),
            ("trust", "Trust or society"),
            ("section8", "Section 8 company"),
            ("llp", "LLP"),
        ],
    )
    category = forms.ChoiceField(
        choices=[
            ("india", "Entity in India"),
            ("foreign", "Foreign entity with Indian subsidiary"),
            ("research", "Academic or research institution"),
        ],
    )
    website = forms.URLField()
    logo = forms.ImageField(
        required=False,
        validators=[validate_upload_size],
        widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
    )
    registered_address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    pincode = forms.RegexField(
        regex=r"^[1-9][0-9]{5}$",
        label="PIN code",
        error_messages={"invalid": "Enter a six-digit Indian PIN code."},
    )
    state = forms.CharField(max_length=120)
    district = forms.CharField(max_length=120)
    verification_document_type = forms.ChoiceField(
        label="Document type",
        choices=[("PAN", "PAN"), ("GSTIN", "GSTIN"), ("CIN", "CIN")],
    )
    verification_document_number = forms.CharField(
        label="Document number",
        max_length=32,
    )
    supporting_document = forms.FileField(
        label="Verification document",
        required=False,
        validators=[validate_pdf],
        widget=forms.FileInput(attrs={"accept": ".pdf"}),
    )
    required_uploads = ("supporting_document",)


class ProductRegistrationForm(SandboxForm):
    name = forms.CharField(label="Product name", max_length=255)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    category = forms.ChoiceField(
        choices=[
            ("hmis", "HMIS"),
            ("lmis", "LMIS"),
            ("phr_locker", "PHR application"),
            ("health_locker", "Health locker"),
            ("claims_platform", "Payer or TPA system"),
            ("other", "Other"),
        ],
    )
    solution_type = forms.ChoiceField(
        label="Solution type applying for",
        choices=[
            ("clinical_hmis", "Clinical HMIS"),
            ("eua", "EUA"),
            ("health_locker", "Health Locker"),
        ],
    )
    applied_milestones = forms.MultipleChoiceField(
        label="Tracks and milestones",
        choices=MILESTONE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    def clean_applied_milestones(self):
        selections = self.cleaned_data["applied_milestones"]
        keys = canonical_keys(selections)
        for key in keys:
            predecessor = MILESTONES[key].predecessor
            if predecessor and predecessor not in keys:
                msg = (
                    f"Select {MILESTONES[predecessor].code} "
                    f"before {MILESTONES[key].name}."
                )
                raise ValidationError(
                    msg,
                )
        return selections


class ExitEvidenceForm(SandboxForm):
    start_date = forms.DateField(
        label="Sandbox testing start date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        label="Sandbox testing end date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    tentative_demo_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    wasa_agency = forms.CharField(label="WASA audit agency name", max_length=255)
    wasa_date = forms.DateField(
        label="WASA audit date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    functional_certificate = forms.FileField(
        label="Functional testing certificate",
        required=False,
        validators=[validate_pdf],
        widget=forms.FileInput(attrs={"accept": ".pdf"}),
    )
    functional_report = forms.FileField(
        label="Functional testing report",
        required=False,
        validators=[validate_pdf],
        widget=forms.FileInput(attrs={"accept": ".pdf"}),
    )
    supporting_evidence = MultipleFileField(
        label="Additional evidence",
        required=False,
        max_files=8,
        accept=".pdf",
        validators=[validate_pdf],
    )
    required_uploads = ("functional_certificate", "functional_report")

    def clean(self):
        cleaned = super().clean()
        start, end, demo = (
            cleaned.get(key)
            for key in ("start_date", "end_date", "tentative_demo_date")
        )
        if start and end and end < start:
            self.add_error("end_date", "Testing must end on or after its start date.")
        if end and demo and demo < end:
            self.add_error(
                "tentative_demo_date",
                "The demo must be on or after the testing end date.",
            )
        return cleaned


class CredentialURLsForm(forms.Form):
    callback_url = forms.URLField(required=False)
    bridge_url = forms.URLField(required=False)

    def clean(self):
        cleaned = super().clean()
        for key, value in cleaned.items():
            if value and not value.lower().startswith("https://"):
                self.add_error(key, "Use an HTTPS URL.")
        return cleaned


class SupportForm(forms.Form):
    subject = forms.CharField(max_length=255)
    track = forms.ChoiceField(
        choices=[
            ("", "General"),
            ("HI-CM", "HI-CM"),
            ("UHI", "UHI"),
            ("NHCX", "NHCX"),
            ("PHR", "PHR"),
            ("HealthLocker", "HealthLocker"),
        ],
        required=False,
    )
    priority = forms.ChoiceField(
        choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")],
    )
    body = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 5}))
    attachments = MultipleFileField(
        required=False,
        max_files=5,
        validators=[validate_pdf],
        accept=".pdf",
    )
