from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from ohc_experience.experiences.definitions import readable_list
from ohc_experience.experiences.fields import MultipleFileField
from ohc_experience.experiences.forms import ReviewForm
from ohc_experience.experiences.models import CertificationAgency
from ohc_experience.experiences.uploads import validate_pdf
from ohc_experience.organisations.lgd import LGDLookupError
from ohc_experience.organisations.lgd import lookup_pincode
from ohc_experience.organisations.widgets import PincodeInput

from .catalog import MILESTONE_CHOICES
from .catalog import MILESTONES
from .catalog import REQUIRED_MILESTONES
from .catalog import TRACKS
from .catalog import canonical_keys
from .wasa import WASA_FIELDS
from .wasa import WASA_VALIDITY_YEARS
from .wasa import approved_wasa_submission
from .wasa import certificate_context
from .wasa import current_wasa
from .widgets import WasaCertificateInput


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
            ("sole_proprietor", "Individual/sole proprietorship"),
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
    logo = forms.URLField(label="Logo URL", required=False)
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

    class Media:
        js = ("js/organisation-form.js", "js/email-domain-callout.js")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not isinstance(self.initial.get("logo", ""), str):
            # Logos were uploaded before they became links. An earlier upload
            # stays with its own revision and never prefills the link.
            del self.initial["logo"]
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
        if (
            cleaned.get("entity_type") == "sole_proprietor"
            and cleaned.get("verification_document_type") == "CIN"
        ):
            self.add_error(
                "verification_document_type",
                "An individual or sole proprietorship has no CIN. Choose PAN or GSTIN.",
            )
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


def required_warning(labels):
    """Why an unchecked milestone is required. The picker script says the same."""
    if not labels:
        return ""
    noun = "solution types" if len(labels) > 1 else "solution type"
    return f"Required for the {readable_list(labels)} {noun}."


class ProductRegistrationForm(ReviewForm):
    full_width_fields = ("applied_milestones", "solution_type")
    conditional_fields = {"solution_type_other": ("solution_type", "other")}
    solution_type_details = {
        "hmis": (
            "A hospital system that manages clinical and administrative records.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/concepts/hip-hiu",
        ),
        "clinical_hmis": (
            "A clinic information system that manages patient care and health records.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/concepts/hip-hiu",
        ),
        "lmis": (
            "A laboratory system for lab operations and test results.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/getting-started/glossary?#lmis",
        ),
        "pharmacy": (
            "A system that manages pharmacy dispensing and medication records.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/concepts/participants/pharmacy",
        ),
        "phr": (
            "An application that helps people access and control their health records.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/concepts/phr",
        ),
        "health_locker": (
            "A service that stores and retrieves personal health records.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/concepts/phr#where-the-citizen-is-the-hip",
        ),
        "healthtech": (
            "A digital health product integrating with ABDM services.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones",
        ),
        "insurance": (
            "A payer or insurer that exchanges health insurance claims.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/concepts/participants/insurer",
        ),
        "telemedicine": (
            "A service that delivers healthcare remotely through digital channels.",
            "https://abdm-docs.dev.eka.care/docs/uhi/v1/getting-started/onboarding",
        ),
        "govt_program": (
            "A government programme that integrates with ABDM services.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones",
        ),
        "other": (
            "A solution type not listed above. Describe it in the field that appears.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones",
        ),
    }

    sections = (
        (
            "Product details",
            ("name", "description", "solution_type", "solution_type_other"),
        ),
        ("Tracks and milestones", ("applied_milestones",)),
    )
    name = forms.CharField(label="Product name", max_length=255)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    solution_type = forms.MultipleChoiceField(
        label="Solution types applying for",
        choices=[
            ("hmis", "HMIS"),
            ("clinical_hmis", "Clinic HMIS"),
            ("lmis", "LMIS"),
            ("pharmacy", "Pharmacy"),
            ("phr", "PHR"),
            ("health_locker", "Health Locker"),
            ("healthtech", "HealthTech"),
            ("insurance", "Insurance"),
            ("telemedicine", "Telemedicine"),
            ("govt_program", "Government Programme"),
            ("other", "Other"),
        ],
        widget=forms.CheckboxSelectMultiple(
            attrs={"class": "ui-checkbox shrink-0", "data-solution-type": ""},
        ),
    )
    solution_type_other = forms.CharField(
        label="Other solution type",
        max_length=255,
        error_messages={"required": "Describe the other solution type."},
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
                "solution_type": ["clinical_hmis"],
                "applied_milestones": [
                    f"HIE-CM:{key}" for key in REQUIRED_MILESTONES["clinical_hmis"]
                ],
            }
        super().__init__(*args, **kwargs)
        if self.is_bound and "other" not in (self["solution_type"].value() or []):
            self.fields["solution_type_other"].required = False

    @property
    def milestone_tracks(self):
        selected = self["applied_milestones"].value() or []
        solutions = self["solution_type"].value() or []
        return [
            {
                "definition": track,
                "requires": readable_list(
                    MILESTONES[key].code for key in track.prerequisites(MILESTONES)
                ),
                "milestones": [
                    self._milestone_row(f"{track.code}:{key}", selected, solutions)
                    for key in track.keys
                ],
            }
            for track in TRACKS
        ]

    def _milestone_row(self, value, selected, solutions):
        key = value.split(":", 1)[1]
        required_for = [
            (solution, label)
            for solution, label in self.fields["solution_type"].choices
            if key in REQUIRED_MILESTONES.get(solution, ())
        ]
        missing_for = (
            []
            if value in selected
            else [label for solution, label in required_for if solution in solutions]
        )
        return {
            "definition": MILESTONES[key],
            "value": value,
            "selected": value in selected,
            "required_for": " ".join(solution for solution, _ in required_for),
            "warning": required_warning(missing_for),
        }

    def clean(self):
        cleaned = super().clean()
        if "other" not in (cleaned.get("solution_type") or []):
            cleaned["solution_type_other"] = ""
        return cleaned

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


class UhiParticipationForm(ReviewForm):
    """What legacy collected on its UHI application, and nothing more."""

    full_width_fields = ("uhi_role", "uhi_services")
    sections = (
        (
            "UHI participation",
            ("uhi_role", "uhi_services", "uhi_tell_us_about", "uhi_extra_details"),
        ),
    )
    uhi_role = forms.MultipleChoiceField(
        label="Role",
        choices=[
            ("eua", "End User Applications (EUA)"),
            ("hspa", "Health Service Provider Application (HSPA)"),
        ],
        widget=forms.CheckboxSelectMultiple(attrs={"class": "ui-checkbox shrink-0"}),
    )
    uhi_services = forms.MultipleChoiceField(
        label="Services",
        choices=[
            ("blood_bank_discovery", "Blood Bank Discovery"),
            ("physical_consultation", "Physical Consultation"),
            ("teleconsultation", "Teleconsultation"),
            ("pmjay_hem_find_hospital", "PMJAY HEM Find Hospital"),
        ],
        widget=forms.CheckboxSelectMultiple(attrs={"class": "ui-checkbox shrink-0"}),
    )
    uhi_tell_us_about = forms.CharField(
        label="Tell us about your UHI integration",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    uhi_extra_details = forms.CharField(
        label="Any extra details you would like to share",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class WasaReviewForm(ReviewForm):
    # The certificate leads: the audit fields below are read from it.
    sections = (
        (
            "WASA audit",
            ("wasa_certificate", "wasa_agency", "wasa_date", "wasa_valid_until"),
        ),
    )
    full_width_fields = ("wasa_certificate",)
    section_notes = {
        "WASA audit": (
            "Upload your certificate and enter the expiry date stated on it."
        ),
    }
    wasa_agency = forms.ChoiceField(
        label="WASA audit agency name",
        choices=[("", "Select an audit agency")],
        error_messages={"invalid_choice": "Select an audit agency from the list."},
        widget=forms.Select(attrs={"autocomplete": "off"}),
    )
    wasa_date = forms.DateField(
        label="WASA audit date",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                # WASA certificates run for a year, so the expiry is offered as
                # a starting point the integrator can correct.
                "data-autofill-target": "wasa_valid_until",
                "data-autofill-years": WASA_VALIDITY_YEARS,
                "autocomplete": "off",
            },
        ),
    )
    wasa_valid_until = forms.DateField(
        label="WASA valid until",
        help_text=(
            "Filled in to cover one year from the audit date. Change it if the "
            "certificate states a different expiry date."
        ),
        widget=forms.DateInput(attrs={"type": "date", "autocomplete": "off"}),
    )
    wasa_certificate = forms.FileField(
        label="WASA certificate",
        required=False,
        validators=[validate_pdf],
        widget=WasaCertificateInput(attrs={"autocomplete": "off"}),
    )
    required_uploads = ("wasa_certificate",)

    def __init__(self, *args, product=None, **kwargs):
        self.product = product
        super().__init__(*args, **kwargs)
        self._agency_choices()

    def _agency_choices(self):
        # Earlier submissions accepted free text. Only their own saved value is
        # retained; a posted value cannot extend the administrator's agency list.
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
        audit_date = cleaned.get("wasa_date")
        expiry = cleaned.get("wasa_valid_until")
        if audit_date and audit_date > timezone.localdate():
            self.add_error("wasa_date", "The audit date cannot be in the future.")
        if audit_date and expiry and expiry < audit_date:
            self.add_error(
                "wasa_valid_until",
                "The expiry date must be on or after the audit date.",
            )
        elif expiry and expiry < timezone.localdate() and not self.draft:
            self.add_error(
                "wasa_valid_until",
                "This certificate has expired. Submit a renewed WASA certificate.",
            )
        return cleaned


class ExitEvidenceForm(WasaReviewForm):
    full_width_fields = ("use_product_wasa", "wasa_certificate")
    section_notes = {
        "WASA audit": (
            "The certificate must cover the application and version being submitted."
        ),
    }
    sections = (
        ("Sandbox testing", ("start_date", "end_date", "tentative_demo_date")),
        (
            "WASA audit",
            (
                "use_product_wasa",
                "wasa_source_submission",
                "wasa_certificate",
                "wasa_agency",
                "wasa_date",
                "wasa_valid_until",
            ),
        ),
        (
            "Functional testing",
            (
                "functional_certificate",
                "functional_report",
                "undertaking_form",
                "supporting_evidence",
            ),
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
    use_product_wasa = forms.BooleanField(
        label="Use an approved certificate",
        required=False,
        help_text="Uncheck to upload a new certificate for review.",
    )
    wasa_source_submission = forms.IntegerField(
        label="Approved WASA submission",
        required=False,
        widget=forms.HiddenInput,
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
    undertaking_form = forms.FileField(
        label="Undertaking form",
        required=False,
        widget=forms.FileInput(attrs={"accept": ".doc,.docx,.pdf"}),
    )
    required_uploads = (
        "wasa_certificate",
        "functional_certificate",
        "functional_report",
        "undertaking_form",
    )

    def __init__(
        self,
        *args,
        wasa_source_submission=None,
        prefer_product_wasa=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        today = timezone.localdate().isoformat()
        self.fields["start_date"].widget.attrs["max"] = today
        self.fields["end_date"].widget.attrs["max"] = today
        self.fields["tentative_demo_date"].widget.attrs["min"] = today
        saved_reuse = bool(self.initial.get("use_product_wasa"))
        source = wasa_source_submission
        if self.is_bound:
            reuse = self.fields["use_product_wasa"].widget.value_from_datadict(
                self.data,
                self.files,
                self.add_prefix("use_product_wasa"),
            )
            if reuse:
                source = approved_wasa_submission(
                    self.product,
                    self.data.get(self.add_prefix("wasa_source_submission")),
                )
            elif source:
                self.data = self.data.copy()
                self.data[self.add_prefix("wasa_source_submission")] = source.pk
        else:
            # Never change an existing submission's choice implicitly.
            if prefer_product_wasa is None:
                prefer_product_wasa = not self.initial
            reuse = bool(source) if prefer_product_wasa else saved_reuse
            self.initial["use_product_wasa"] = reuse
            if source:
                self.initial["wasa_source_submission"] = source.pk
        self.wasa_source = source if reuse else None
        display_source = source or wasa_source_submission
        self.product_wasa = certificate_context(display_source)
        valid_source = bool(
            display_source
            and approved_wasa_submission(self.product, display_source.pk),
        )
        self.product_wasa.update(
            valid=valid_source,
            status_label=(
                "Approved"
                if valid_source
                else "Expired"
                if display_source and display_source.is_expired
                else "Unavailable"
            ),
        )
        current = current_wasa(self.product) if self.product else None
        latest = approved_wasa_submission(
            self.product,
            current.data.get("submission_id") if current else None,
        )
        self.latest_product_wasa = (
            certificate_context(latest)
            if latest and display_source and latest.pk != display_source.pk
            else None
        )
        self.wasa_reuse_available = bool(display_source)
        if reuse:
            self._use_approved_wasa(source)
        elif saved_reuse:
            # Changing away from reuse requires its own newly uploaded evidence.
            self.existing_files.pop("wasa_certificate", None)

    def _use_approved_wasa(self, source):
        for key in WASA_FIELDS:
            self.fields[key].disabled = True
            self.fields[key].required = False
            self.initial[key] = source.data.get(key) if source else None
        self.fields["wasa_certificate"].disabled = True
        self.existing_files["wasa_certificate"] = (
            list(
                source.attachments.filter(
                    field_key="wasa_certificate",
                    is_current=True,
                ),
            )
            if source
            else []
        )
        self.removed_file_ids["wasa_certificate"] = set()
        self._agency_choices()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("use_product_wasa"):
            if not self.wasa_source:
                self.add_error(
                    "use_product_wasa",
                    "Select a valid, approved WASA certificate for this product "
                    "or submit a new certificate.",
                )
            else:
                cleaned["wasa_source_submission"] = self.wasa_source.pk
        else:
            cleaned["wasa_source_submission"] = None
        start, end, demo = (
            cleaned.get(key)
            for key in ("start_date", "end_date", "tentative_demo_date")
        )
        today = timezone.localdate()
        if start and start > today:
            self.add_error(
                "start_date",
                "The sandbox testing start date cannot be in the future.",
            )
        if end and end > today:
            self.add_error(
                "end_date",
                "The sandbox testing end date cannot be in the future.",
            )
        if demo and demo < today:
            self.add_error(
                "tentative_demo_date",
                "The tentative demo date cannot be in the past.",
            )
        if start and end and end < start:
            self.add_error("end_date", "Testing must end on or after its start date.")
        if end and demo and demo < end:
            self.add_error(
                "tentative_demo_date",
                "The demo must be on or after the testing end date.",
            )
        return cleaned
