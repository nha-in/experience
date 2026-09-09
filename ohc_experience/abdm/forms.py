from django import forms
from django.core.exceptions import ValidationError

from ohc_experience.experiences.fields import MultipleFileField
from ohc_experience.experiences.forms import ReviewForm
from ohc_experience.experiences.uploads import validate_pdf
from ohc_experience.experiences.uploads import validate_upload_size

from .catalog import MILESTONE_CHOICES
from .catalog import MILESTONES
from .catalog import TRACKS
from .catalog import canonical_keys


class OrganisationForm(ReviewForm):
    sections = (
        (
            "Identity",
            ("name", "description", "entity_type", "category", "website", "logo"),
        ),
        ("Registered address", ("registered_address", "pincode", "state", "district")),
        (
            "Verification document",
            (
                "verification_document_type",
                "verification_document_number",
                "supporting_document",
            ),
        ),
    )
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


class ProductRegistrationForm(ReviewForm):
    full_width_fields = ("applied_milestones",)
    section_notes = {
        "Tracks and milestones": (
            "M1 approval is shared by HI-CM and PHR. "
            "Select each preceding milestone in the same track."
        ),
    }
    section_badges = {"Tracks and milestones": (("NHCX", "No milestones published"),)}
    sections = (
        ("Product details", ("name", "description", "category", "solution_type")),
        ("Tracks and milestones", ("applied_milestones",)),
    )
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

    def __init__(self, *args, **kwargs):
        # Defaults belong to new registrations, never a saved or bound form.
        if not args and kwargs.get("data") is None and kwargs.get("initial") is None:
            kwargs["initial"] = {
                "category": "hmis",
                "solution_type": "clinical_hmis",
                "applied_milestones": ["HI-CM:m1"],
            }
        super().__init__(*args, **kwargs)

    @property
    def milestone_tracks(self):
        selected = self["applied_milestones"].value() or []
        return [
            {
                "definition": track,
                "milestones": [
                    {
                        "definition": MILESTONES[key],
                        "value": f"{track.code}:{key}",
                        "selected": f"{track.code}:{key}" in selected,
                        "shared": track.code == "PHR" and key == "m1",
                    }
                    for key in track.keys
                ],
            }
            for track in TRACKS
        ]

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


class ExitEvidenceForm(ReviewForm):
    sections = (
        ("Sandbox testing", ("start_date", "end_date", "tentative_demo_date")),
        ("WASA audit", ("wasa_agency", "wasa_date")),
        (
            "Functional testing",
            ("functional_certificate", "functional_report", "supporting_evidence"),
        ),
    )
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
