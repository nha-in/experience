from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from ohc_experience.experiences import legacy
from ohc_experience.experiences.definitions import readable_list
from ohc_experience.experiences.fields import MultipleFileField
from ohc_experience.experiences.forms import ReviewForm
from ohc_experience.experiences.models import CertificationAgency
from ohc_experience.experiences.uploads import validate_evidence_pdf
from ohc_experience.experiences.uploads import validate_evidence_spreadsheet
from ohc_experience.experiences.uploads import validate_pdf
from ohc_experience.organisations import states
from ohc_experience.organisations.lgd import LGDLookupError
from ohc_experience.organisations.lgd import lookup_pincode
from ohc_experience.organisations.models import SOLE_PROPRIETOR
from ohc_experience.organisations.widgets import PincodeInput
from ohc_experience.organisations.widgets import WebsiteInput

from .catalog import EXCLUSIVE_TRACKS
from .catalog import MILESTONE_CHOICES
from .catalog import MILESTONES
from .catalog import NHCX_ROLE_TRACKS
from .catalog import NHCX_ROLES
from .catalog import OPTIONAL_MILESTONES
from .catalog import REQUIRED_MILESTONES
from .catalog import TRACKS
from .catalog import canonical_keys
from .catalog import excluded_track
from .catalog import fixed_selections
from .catalog import milestone_predecessors
from .catalog import other_nhcx_role
from .docs import docs_page
from .wasa import WASA_FIELDS
from .wasa import WASA_VALIDITY_YEARS
from .wasa import approved_wasa_submission
from .wasa import as_date
from .wasa import certificate_context
from .wasa import current_wasa
from .wasa import validity_limit
from .widgets import ListRadioSelect
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
            (SOLE_PROPRIETOR, "Individual/sole proprietorship"),
            ("partnership", "Partnership firm"),
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
    website = forms.URLField(widget=WebsiteInput)
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
        choices=[
            ("", "Select a document type"),
            ("PAN", "PAN"),
            ("GSTIN", "GSTIN"),
            ("CIN", "CIN"),
        ],
        required=False,
    )
    verification_document_number = forms.CharField(
        label="Document number",
        max_length=32,
        required=False,
    )
    supporting_document = forms.FileField(
        label="Verification document",
        required=False,
        help_text="PDF, up to 10 MB.",
        validators=[validate_pdf],
        widget=forms.FileInput(attrs={"accept": ".pdf"}),
    )
    conditional_requirements = ("website",)

    class Media:
        js = (
            "js/organisation-form.js",
            "js/website-requirement.js",
            "js/email-domain-callout.js",
        )

    def __init__(self, *args, entity_type="", **kwargs):
        super().__init__(*args, **kwargs)
        if not isinstance(self.initial.get("logo", ""), str):
            # Logos were uploaded before they became links. An earlier upload
            # stays with its own revision and never prefills the link.
            del self.initial["logo"]
        self._lock_entity_type(entity_type)
        if self._entity_type() == SOLE_PROPRIETOR:
            # A person trading under a business name may well have no website.
            # Relax it only — a draft has already let every field go.
            self.fields["website"].required = False
        self.locations = []
        self.location_error = ""
        if self.is_bound:
            self._load_locations()
        self._location_choices()

    def _lock_entity_type(self, entity_type):
        """The type of entity is settled at sign-up and read-only from then on.

        It decides what the entity is called, which documents can verify it and
        whether a website is asked for, so changing it here would quietly
        re-file an application the reviewer read as something else. A genuine
        correction is made on the organisation in the admin. An organisation
        that arrived without a type — a social sign-in, or an account older
        than the field — still picks one here, the once.
        """
        if not entity_type:
            return
        self.initial["entity_type"] = entity_type
        field = self.fields["entity_type"]
        field.disabled = True
        field.help_text = "Chosen when the account was created."

    def _entity_type(self):
        """The type in force now: the locked one, or whatever is being picked."""
        if self.fields["entity_type"].disabled:
            return self.initial["entity_type"]
        if not self.is_bound:
            return self.initial.get("entity_type", "")
        return str(self.data.get(self.add_prefix("entity_type"), "")).strip()

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
        except LGDLookupError:
            return  # No locations: the offline names are offered instead.
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
        if self.is_bound and not self.locations:
            # LGD did not answer, so offer the offline names it would have.
            self._offline_choices()
            return
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

    def _offline_choices(self):
        """Every state, and the districts of whichever state was chosen."""
        chosen = str(self.data.get(self.add_prefix("state"), "")).strip()
        for name, values in (
            ("state", states.state_names()),
            ("district", states.district_names(chosen)),
        ):
            self.fields[name].widget.choices = [
                ("", "Select a state" if name == "state" else "Select a district"),
                *((value, value) for value in values),
            ]

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("entity_type") == SOLE_PROPRIETOR
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
        state = cleaned.get("state")
        district = cleaned.get("district")
        if not state or not district:
            return cleaned  # Both fields raise their own required error.
        matches = [
            location
            for location in self.locations
            if location["state"] == state and location["district"] == district
        ]
        if matches:
            cleaned["state_lgd_code"] = matches[0]["state_code"]
            cleaned["district_lgd_code"] = matches[0]["district_code"]
            return cleaned
        # LGD did not answer, or answered something else. The pair stands as
        # chosen so long as it is a real one; it simply earns no codes.
        pair = states.canonical(state, district)
        if pair is None:
            self.add_error("district", "Select a state and district from the list.")
            return cleaned
        cleaned["state"], cleaned["district"] = pair
        return cleaned


class ProductRegistrationForm(ReviewForm):
    full_width_fields = ("applied_milestones", "solution_type")
    conditional_fields = {"solution_type_other": ("solution_type", "other")}
    solution_type_details = {
        "hmis": (
            "A hospital system that manages clinical and administrative records.",
            docs_page("/docs/hiecm/v3/concepts/hip-hiu"),
        ),
        "clinical_hmis": (
            "A clinic information system that manages patient care and health records.",
            docs_page("/docs/hiecm/v3/concepts/hip-hiu"),
        ),
        "lmis": (
            "A laboratory system for lab operations and test results.",
            docs_page("/docs/hiecm/v3/concepts/participants/lab"),
        ),
        "pharmacy": (
            "A system that manages pharmacy dispensing and medication records.",
            docs_page("/docs/hiecm/v3/concepts/participants/pharmacy"),
        ),
        "phr": (
            "An application that helps people access and control their health records.",
            docs_page("/docs/hiecm/v3/concepts/participants/phr"),
        ),
        "health_locker": (
            "A service that stores and retrieves personal health records.",
            docs_page("/docs/hiecm/v3/milestones/p4"),
        ),
        "healthtech": (
            "A digital health product integrating with ABDM services.",
            docs_page("/docs/hiecm/v3/milestones"),
        ),
        "insurance": (
            "A payer or insurer that exchanges health insurance claims.",
            docs_page("/docs/hiecm/v3/concepts/participants/insurer"),
        ),
        "telemedicine": (
            "A service that delivers healthcare remotely through digital channels.",
            docs_page("/docs/hiecm/v3/milestones"),
        ),
        "govt_program": (
            "A government programme that integrates with ABDM services.",
            docs_page("/docs/hiecm/v3/milestones"),
        ),
        "other": (
            "A solution type not listed above. Describe it in the field that appears.",
            docs_page("/docs/hiecm/v3/milestones"),
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
    # One type is chosen but saved as a list, the shape everything else reads.
    solution_type = forms.MultipleChoiceField(
        label="Solution type applying for",
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
        widget=ListRadioSelect(
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

    def __init__(self, *args, organisation=None, product=None, **kwargs):
        self.organisation = organisation
        self.product = product
        super().__init__(*args, **kwargs)
        # A registered product keeps the solution type it was registered as,
        # unless the import had none to read and still owes us the real one.
        if product and product.solution_type:
            self.initial["solution_type"] = product.solution_type
            self.fields["solution_type"].disabled = not legacy.has_gap(
                product,
                legacy.SOLUTION_TYPE,
            )
        if self.is_bound and "other" not in (self["solution_type"].value() or []):
            self.fields["solution_type_other"].required = False
        # Locked boxes are disabled, so a fixed type may arrive with none posted.
        if self.is_bound and fixed_selections(self["solution_type"].value() or []):
            self.fields["applied_milestones"].required = False

    @property
    def holds_both_tracks(self):
        """Whether this product came from a registration that declared both.

        Legacy let one registration claim ABDM and PHR, so an imported product
        can hold both and keeps them; a new one still has to choose.
        """
        return bool(self.product) and legacy.has_gap(self.product, legacy.TRACKS)

    @property
    def milestone_tracks(self):
        solutions = self["solution_type"].value() or []
        fixed = fixed_selections(solutions)
        selected = self._apply_solution_type(
            self["applied_milestones"].value() or [],
            solutions,
            self.product.applied_milestones if self.product else (),
        )
        blocked = excluded_track({value.split(":", 1)[0] for value in selected})
        fixed_by = readable_list(
            label
            for solution, label in self.fields["solution_type"].choices
            if solution in solutions
        )
        rows = []
        for track in TRACKS:
            hard = track.prerequisites(MILESTONES)
            related = [
                key for key in track.related_milestones(MILESTONES) if key not in hard
            ]
            # The code, not the name: ABDM's name is "Milestones", which says
            # nothing in this sentence, and the heading above leads with the code.
            other = excluded_track({track.code})
            exclusion = ""
            if other:
                exclusion = f"Not in implementation scope of {other}."
            locked = fixed is not None and track.code in EXCLUSIVE_TRACKS
            owns_fixed = locked and any(
                value.startswith(f"{track.code}:") for value in fixed
            )
            rows.append(
                {
                    "definition": track,
                    "requires": self._requirement(track, hard),
                    "related": readable_list(MILESTONES[key].code for key in related),
                    "excludes": other,
                    "exclusion": exclusion,
                    "blocked": track.code == blocked,
                    "locked": locked,
                    "fixed_note": (
                        f"Set by the {fixed_by} solution type." if owns_fixed else ""
                    ),
                    "milestones": [
                        self._milestone_row(f"{track.code}:{key}", selected)
                        for key in track.keys
                    ],
                },
            )
        return rows

    def _requirement(self, track, prerequisites):
        """ "M1 or P1 ": what has to be chosen before this track's own milestones.

        UHI opens on either identity milestone, so its note reads "or"; a track
        that builds on a chain would read "and". NHCX's roles each need
        something different, so each role's row names its own.
        """
        if track.keys == NHCX_ROLES:
            return ""
        codes = [MILESTONES[key].code for key in prerequisites]
        alternatives = False
        for key in track.keys:
            milestone = MILESTONES[key]
            if len(milestone.predecessors) > 1 and not milestone.requires_all:
                alternatives = True
        return readable_list(codes, conjunction="or" if alternatives else "and")

    def _milestone_row(self, value, selected):
        key = value.split(":", 1)[1]
        definition = MILESTONES[key]
        chosen = {value.split(":", 1)[0] for value in selected} & set(EXCLUSIVE_TRACKS)
        other = other_nhcx_role(key)
        # Once ABDM or PHR is chosen, only that track's NHCX roles show.
        hidden = (
            key in NHCX_ROLE_TRACKS
            and bool(chosen)
            and NHCX_ROLE_TRACKS[key] not in chosen
        )
        required_for = [
            solution
            for solution, _ in self.fields["solution_type"].choices
            if key in REQUIRED_MILESTONES.get(solution, ())
        ]
        return {
            "definition": definition,
            "value": value,
            "selected": value in selected and not hidden,
            "requires": " ".join(milestone_predecessors(key, self.organisation)),
            "stands_alone": definition.stands_alone,
            "requires_all": definition.requires_all,
            "optional": key in OPTIONAL_MILESTONES,
            "needs": readable_list(
                MILESTONES[other].code for other in definition.predecessors
            )
            if key in NHCX_ROLES
            else "",
            "excludes": f"NHCX:{other}" if other else "",
            "excludes_code": MILESTONES[other].code if other else "",
            "role_track": NHCX_ROLE_TRACKS.get(key, ""),
            "hidden": hidden,
            "required_for": " ".join(required_for),
        }

    def _apply_solution_type(self, selections, solutions, saved=()):
        """Swap the ABDM and PHR selections for the ones the solution type fixes.

        Posted ones in `saved` stay: approved and under-review milestones arrive
        as hidden fields, and the save refuses to drop them. So does an optional
        milestone on the track the type fixes: M4 under an ABDM type.
        """
        fixed = fixed_selections(solutions)
        if fixed is None:
            return list(selections)
        fixed_tracks = {value.split(":", 1)[0] for value in fixed}

        def keeps(value):
            track, key = value.split(":", 1)
            if track not in EXCLUSIVE_TRACKS or value in saved:
                return True
            return key in OPTIONAL_MILESTONES and track in fixed_tracks

        kept = [value for value in selections if keeps(value)]
        return kept + [value for value in fixed if value not in kept]

    def clean(self):
        cleaned = super().clean()
        if "other" not in (cleaned.get("solution_type") or []):
            cleaned["solution_type_other"] = ""
        return cleaned

    def clean_applied_milestones(self):
        selections = self._apply_solution_type(
            self.cleaned_data["applied_milestones"],
            self.cleaned_data.get("solution_type") or [],
            self.product.applied_milestones if self.product else (),
        )
        chosen = {value.split(":", 1)[0] for value in selections}
        blocked = excluded_track(chosen)
        if blocked in chosen and not self.holds_both_tracks:
            names = readable_list(EXCLUSIVE_TRACKS)
            msg = f"{names} cannot be applied for together. Choose one of them."
            raise ValidationError(msg)
        keys = canonical_keys(selections)
        if len(keys & set(NHCX_ROLES)) > 1:
            msg = "Choose one NHCX role: Payer, Provider or Patient app."
            raise ValidationError(msg)
        for key in keys:
            milestone = MILESTONES[key]
            if milestone.stands_alone:
                continue
            options = milestone_predecessors(key, self.organisation)
            if not options:
                continue
            met = all if milestone.requires_all else any
            if met(option in keys for option in options):
                continue
            codes = [MILESTONES[option].code for option in options]
            needed = readable_list(
                codes,
                conjunction="and" if milestone.requires_all else "or",
            )
            msg = f"Select {needed} before {milestone.name}."
            raise ValidationError(msg)
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


EXPIRED_CERTIFICATE = "This certificate has expired. Submit a renewed WASA certificate."
OVER_VALIDITY = (
    "A WASA certificate runs for at most a year. The expiry date cannot be "
    "more than a year after the audit date."
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
            "Upload your certificate and check the audit details taken from it."
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
                # WASA certificates run for a year, so the audit date the
                # integrator gives is what the expiry beside it is read from.
                "data-autofill-target": "wasa_valid_until",
                "data-autofill-years": WASA_VALIDITY_YEARS,
                "autocomplete": "off",
            },
        ),
    )
    # The expiry is not the integrator's to set: it follows the audit date, or
    # the date the certificate itself states where its reader finds one. The
    # field still posts, so clean() validates what arrives either way.
    wasa_valid_until = forms.DateField(
        label="WASA valid until",
        help_text=(
            "Filled in to cover one year from the audit date, or the expiry "
            "printed on the certificate."
        ),
        widget=forms.DateInput(
            attrs={"type": "date", "autocomplete": "off", "readonly": True},
        ),
    )
    wasa_certificate = forms.FileField(
        label="WASA certificate",
        required=False,
        help_text="PDF, up to 5 MB.",
        validators=[validate_evidence_pdf],
        widget=WasaCertificateInput(attrs={"autocomplete": "off"}),
    )
    required_uploads = ("wasa_certificate",)

    def __init__(self, *args, product=None, **kwargs):
        self.product = product
        super().__init__(*args, **kwargs)
        today = timezone.localdate().isoformat()
        self.fields["wasa_date"].widget.attrs["max"] = today
        # The floor is what project.js measures an expired date against, so it
        # can say why beside the field the moment one lands there, derived or
        # read off the certificate, in the words clean() refuses it with. A
        # draft still keeps one: it posts without the browser's validation, and
        # records migrated with an expired certificate still open.
        expiry = self.fields["wasa_valid_until"].widget.attrs
        expiry["min"] = today
        expiry["data-expired-message"] = EXPIRED_CERTIFICATE
        # The ceiling is the other bound project.js measures against: a
        # certificate runs for a year at most from the audit it records, and a
        # date read off the certificate can outrun it. project.js moves the
        # ceiling as the audit date changes; this is the one the field is first
        # drawn with.
        expiry["data-over-validity-message"] = OVER_VALIDITY
        audited = as_date(self._audit_date())
        if audited:
            expiry["max"] = validity_limit(audited).isoformat()
        self._agency_choices()

    def _audit_date(self):
        """The audit date the form is rendering with, posted or saved."""
        if self.is_bound:
            return self.data.get(self.add_prefix("wasa_date"))
        return self.initial.get("wasa_date")

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
            self.add_error("wasa_valid_until", EXPIRED_CERTIFICATE)
        elif (
            audit_date
            and expiry
            and expiry > validity_limit(audit_date)
            and not self.draft
        ):
            # Held to submission, like the expiry itself: a migrated record can
            # carry a longer span and must still save as a draft.
            self.add_error("wasa_valid_until", OVER_VALIDITY)
        return cleaned


class ExitEvidenceForm(WasaReviewForm):
    full_width_fields = ("use_product_wasa", "wasa_certificate")
    field_info = {
        "tentative_demo_date": (
            "When you expect to demonstrate this milestone to NHA. It can't be "
            "before today or before your testing ends."
        ),
        "use_product_wasa": (
            "Uses the WASA certificate already approved for this product instead "
            "of uploading it again. An approved certificate stays available to "
            "every milestone of the product until it expires. Untick to upload a "
            "new certificate, which NHA reviews with this milestone."
        ),
    }
    section_notes = {
        "WASA audit": (
            "The certificate must cover the application and version being submitted."
        ),
    }
    sections = (
        ("Milestone dates", ("start_date", "end_date", "tentative_demo_date")),
        ("Functional testing", ("functional_certificate", "functional_report")),
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
        ("Undertaking", ("undertaking_form", "supporting_evidence")),
    )
    # The reviewer's copy of a submission and the error summary list fields in this
    # order, so it follows the sections instead of leading with the inherited WASA
    # fields.
    field_order = [key for _title, keys in sections for key in keys]
    start_date = forms.DateField(
        label="Start date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        label="End date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    tentative_demo_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    use_product_wasa = forms.BooleanField(
        label="Reuse Previously Approved Certificate",
        required=False,
    )
    wasa_source_submission = forms.IntegerField(
        label="Approved WASA submission",
        required=False,
        widget=forms.HiddenInput,
    )
    functional_certificate = forms.FileField(
        label="Functional testing certificate",
        required=False,
        help_text="PDF, up to 5 MB.",
        validators=[validate_evidence_pdf],
        widget=forms.FileInput(attrs={"accept": ".pdf"}),
    )
    functional_report = MultipleFileField(
        label="Functional testing reports",
        required=False,
        help_text="Excel workbook (.xls or .xlsx), up to 3 files, 5 MB each.",
        max_files=3,
        accept=".xls,.xlsx",
        validators=[validate_evidence_spreadsheet],
    )
    supporting_evidence = MultipleFileField(
        label="Additional evidence",
        required=False,
        help_text="PDF, up to 8 files, 5 MB each.",
        max_files=8,
        accept=".pdf",
        validators=[validate_evidence_pdf],
    )
    undertaking_form = forms.FileField(
        label="Undertaking form",
        required=False,
        help_text="PDF, up to 5 MB.",
        validators=[validate_evidence_pdf],
        widget=forms.FileInput(attrs={"accept": ".pdf"}),
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
                "The milestone start date cannot be in the future.",
            )
        if end and end > today:
            self.add_error(
                "end_date",
                "The milestone end date cannot be in the future.",
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
