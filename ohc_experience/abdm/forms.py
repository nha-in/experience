from django import forms
from django.core.exceptions import ValidationError

from ohc_experience.experiences.fields import MultipleFileField
from ohc_experience.experiences.forms import ReviewForm
from ohc_experience.experiences.models import CertificationAgency
from ohc_experience.experiences.uploads import validate_pdf
from ohc_experience.experiences.uploads import validate_upload_size
from ohc_experience.organisations.lgd import LGDLookupError
from ohc_experience.organisations.lgd import lookup_pincode
from ohc_experience.organisations.widgets import PincodeInput

from .catalog import MILESTONE_CHOICES
from .catalog import MILESTONES
from .catalog import TRACK_GATES
from .catalog import TRACK_MAP
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
        widget=PincodeInput,
    )
    state = forms.CharField(
        max_length=120,
        widget=forms.Select(attrs={"data-lgd-state": ""}),
    )
    district = forms.CharField(
        max_length=120,
        widget=forms.Select(attrs={"data-lgd-district": ""}),
    )
    state_lgd_code = forms.CharField(
        label="State LGD code",
        required=False,
        disabled=True,
        widget=forms.HiddenInput,
    )
    district_lgd_code = forms.CharField(
        label="District LGD code",
        required=False,
        disabled=True,
        widget=forms.HiddenInput,
    )
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.locations = []
        self.location_error = ""
        if self.is_bound:
            self._load_locations()
        self._location_choices()

    def _load_locations(self):
        try:
            pincode = self.fields["pincode"].clean(
                self.data.get(self.add_prefix("pincode")),
            )
        except ValidationError:
            return  # The field reports its own format/required error.
        if not pincode:
            return
        try:
            self.locations = lookup_pincode(pincode)
        except LGDLookupError as error:
            self.location_error = str(error)
            return
        if not self.locations:
            self.location_error = "No state or district was found for this PIN code."
            return

        # Fill unique matches on the server as well, including without JavaScript.
        # Accept the casing of older saved addresses, but store LGD's names.
        self.data = self.data.copy()
        candidates = self.locations
        for name in ("state", "district"):
            key = self.add_prefix(name)
            posted = str(self.data.get(key, "")).strip()
            options = {location[name] for location in candidates}
            canonical = next(
                (value for value in options if value.casefold() == posted.casefold()),
                "",
            )
            if canonical:
                self.data[key] = canonical
            elif not posted and len(options) == 1:
                self.data[key] = next(iter(options))
            candidates = [
                location
                for location in candidates
                if location[name] == self.data.get(key)
            ]

    def _location_choices(self):
        for name in ("state", "district"):
            values = sorted({location[name] for location in self.locations})
            if not self.is_bound:
                initial = self.initial.get(name)
                if initial:
                    values = [initial]
            self.fields[name].widget.choices = [
                ("", "Select a state" if name == "state" else "Select a district"),
                *((value, value) for value in values),
            ]

    def clean(self):
        cleaned = super().clean()
        # Codes are always derived from this PIN's response, never from POST or
        # from an earlier submission whose PIN may have changed.
        cleaned["state_lgd_code"] = cleaned["district_lgd_code"] = ""
        if self.location_error:
            self.add_error("pincode", self.location_error)
            return cleaned
        if not cleaned.get("pincode") or not self.locations:
            return cleaned
        state = cleaned.get("state")
        district = cleaned.get("district")
        matches = [
            location
            for location in self.locations
            if location["state"] == state and location["district"] == district
        ]
        if len(matches) == 1:
            cleaned["state_lgd_code"] = matches[0]["state_code"]
            cleaned["district_lgd_code"] = matches[0]["district_code"]
        elif state and district:
            self.add_error(
                "district",
                "Select a state and district returned for this PIN code.",
            )
        return cleaned


class ProductRegistrationForm(ReviewForm):
    full_width_fields = ("applied_milestones", "solution_type")
    conditional_fields = {"payer_category": ("solution_type", "payers")}
    section_notes = {
        "Tracks and milestones": (
            "M1 approval is shared by HI-CM and PHR. "
            "Select each preceding milestone in the same track."
        ),
    }
    sections = (
        (
            "Product details",
            ("name", "description", "category", "solution_type", "payer_category"),
        ),
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
    solution_type = forms.MultipleChoiceField(
        label="Solution types applying for",
        choices=[
            ("clinical_hmis", "Clinical HMIS"),
            ("hmis", "HMIS"),
            ("govt_hmis", "Government HMIS"),
            ("lmis", "LMIS"),
            ("phr", "PHR"),
            ("govt_phr", "Government PHR"),
            ("health_locker", "Health Locker"),
            ("eua", "End user application (EUA)"),
            ("govt_program", "Government programme"),
            ("healthtech", "Healthtech"),
            ("insurance", "Insurance"),
            ("payers", "Payers"),
            ("providers", "Providers"),
            ("pharmacy", "Pharmacy"),
            ("telemedicine", "Telemedicine"),
            ("other", "Other"),
        ],
        widget=forms.CheckboxSelectMultiple(attrs={"class": "ui-checkbox shrink-0"}),
    )
    payer_category = forms.MultipleChoiceField(
        label="Payer categories",
        required=False,
        choices=[("tpa", "TPA"), ("insurance_company", "Insurance company")],
        widget=forms.CheckboxSelectMultiple(attrs={"class": "ui-checkbox shrink-0"}),
        help_text="Applies when Payers is one of the solution types.",
    )
    applied_milestones = forms.MultipleChoiceField(
        label="Tracks and milestones",
        choices=MILESTONE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, approved_milestones=(), **kwargs):
        self.approved_milestones = set(approved_milestones)
        # Defaults belong to new registrations, never a saved or bound form.
        if not args and kwargs.get("data") is None and kwargs.get("initial") is None:
            kwargs["initial"] = {
                "category": "hmis",
                "solution_type": ["clinical_hmis"],
                "applied_milestones": ["HI-CM:m1"],
            }
        super().__init__(*args, **kwargs)

    def track_lock_reason(self, track):
        """Why a gated track cannot be picked yet, or "" once it is open."""
        gate = TRACK_GATES.get(track.code)
        if not gate or gate in self.approved_milestones:
            return ""
        return (
            f"Opens once {MILESTONES[gate].code} is approved. "
            f"{MILESTONES[gate].code} is shared by the HI-CM and PHR tracks, so "
            f"an approved PHR track satisfies this too."
        )

    @property
    def milestone_tracks(self):
        selected = self["applied_milestones"].value() or []
        return [
            {
                "definition": track,
                "locked": bool(self.track_lock_reason(track)),
                "lock_reason": self.track_lock_reason(track),
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

    def clean(self):
        cleaned = super().clean()
        solutions = cleaned.get("solution_type") or []
        if "payers" in solutions:
            if not cleaned.get("payer_category") and not self.draft:
                self.add_error("payer_category", "Select at least one payer category.")
        elif cleaned.get("payer_category"):
            cleaned["payer_category"] = []
        return cleaned

    def clean_applied_milestones(self):
        selections = self.cleaned_data["applied_milestones"]
        for code in {value.split(":", 1)[0] for value in selections}:
            reason = self.track_lock_reason(TRACK_MAP[code])
            if reason:
                msg = f"{code} cannot be selected yet. {reason}"
                raise ValidationError(msg)
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
        ("WASA audit", ("wasa_agency", "wasa_date", "wasa_certificate")),
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
    wasa_agency = forms.ChoiceField(
        label="WASA audit agency name",
        choices=[("", "Select an audit agency")],
        error_messages={"invalid_choice": "Select an audit agency from the list."},
    )
    wasa_date = forms.DateField(
        label="WASA audit date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    wasa_certificate = forms.FileField(
        label="WASA certificate",
        required=False,
        validators=[validate_pdf],
        widget=forms.FileInput(attrs={"accept": ".pdf"}),
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
    required_uploads = (
        "wasa_certificate",
        "functional_certificate",
        "functional_report",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Earlier submissions accepted free text. Keep that saved answer available
        # on its own form; posted values must never extend the agency list.
        previous_agency = self.initial.get("wasa_agency")
        field = self.fields["wasa_agency"]
        field.choices = [
            ("", "Select an audit agency"),
            *CertificationAgency.objects.filter(
                program="abdm",
                is_active=True,
            ).values_list("name", "name"),
        ]
        if previous_agency and not field.valid_value(previous_agency):
            field.choices = [
                *field.choices,
                (previous_agency, f"{previous_agency} (previously saved)"),
            ]

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
