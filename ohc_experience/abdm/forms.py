"""Forms for the integrator and reviewer screens. Writes happen in services."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from ohc_experience.experiences.uploads import PDF
from ohc_experience.experiences.uploads import PDF_MAX_MB
from ohc_experience.experiences.uploads import validate_upload

from .uploads import PortalFileFormMixin


class QueryReplyForm(forms.Form):
    reply = forms.CharField(
        label=_("Your reply"),
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": _("Answer the reviewer's question")},
        ),
        error_messages={"required": _("Write a reply before sending it.")},
    )


class ProductForm(forms.ModelForm):
    """Register or edit a product: identity plus the milestones applied for.

    Milestones are checkboxes grouped by track (see the milestone_checkboxes
    partial). A new product starts with HI-CM M1 ticked
    (tracks.DEFAULT_MILESTONE_KEY); an existing one starts with its own.
    In edit mode a milestone whose exit request is approved or under review is
    locked: its checkbox is disabled and a hidden input keeps it in the
    submission, and the service refuses to drop it either way.
    """

    milestones = forms.MultipleChoiceField(
        label=_("Tracks and milestones"),
        required=True,
        error_messages={"required": _("Choose at least one milestone to apply for.")},
    )

    class Meta:
        from .models import Product  # noqa: PLC0415 - keeps the module import-light

        model = Product
        fields = ["name", "description", "category", "solution_type"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}
        help_texts = {
            "description": _(
                "What the product does, who uses it and how it will use ABDM.",
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        from . import tracks  # noqa: PLC0415 - keeps the module import-light

        self.fields["milestones"].choices = [
            (milestone.key, milestone.label)
            for track in tracks.TRACKS
            for milestone in track.milestones
        ]
        self.fields["name"].widget.attrs.setdefault("placeholder", _("Arogya HMIS"))
        self.locked_keys: set[str] = set()
        self.locked_reasons: dict[str, str] = {}
        if self.instance.pk:
            for record in self.instance.compliance_records.all():
                if record.is_approved:
                    self.locked_keys.add(record.key)
                    self.locked_reasons[record.key] = str(_("Approved"))
                elif record.is_under_review:
                    self.locked_keys.add(record.key)
                    self.locked_reasons[record.key] = str(_("Under review"))
            if not self.is_bound:
                self.initial.setdefault("milestones", self.instance_keys())
        elif not self.is_bound:
            self.initial.setdefault("milestones", [tracks.DEFAULT_MILESTONE_KEY])

    def instance_keys(self) -> list[str]:
        """Checkbox keys for the product's records, alias tiles included."""
        from . import tracks  # noqa: PLC0415

        canonical = set(self.instance.applied_milestones or [])
        applied = set(self.instance.applied_tracks or [])
        return [
            milestone.key
            for track in tracks.TRACKS
            for milestone in track.milestones
            if milestone.canonical_key in canonical
            and (not milestone.is_alias or track.code in applied)
        ]

    @property
    def selected_keys(self) -> set[str]:
        if self.is_bound:
            values = (
                self.data.getlist("milestones")
                if hasattr(self.data, "getlist")
                else self.data.get("milestones", [])
            )
            return set(values if isinstance(values, (list, tuple)) else [values])
        return set(self.initial.get("milestones") or [])

    def clean_milestones(self) -> list[str]:
        from . import tracks  # noqa: PLC0415

        keys = set(self.cleaned_data["milestones"])
        canonical, _applied = tracks.validate_selection(keys)
        for key in self.locked_keys:
            if key not in canonical:
                track_code, code = tracks.parse_key(key)
                msg = _(
                    "%(milestone)s on %(track)s cannot be removed: it is %(why)s.",
                ) % {
                    "milestone": code,
                    "track": track_code,
                    "why": self.locked_reasons[key].lower(),
                }
                raise forms.ValidationError(msg)
        return sorted(keys)

    def product_data(self) -> dict:
        """What services.register_product / update_product take."""
        return {
            **{name: self.cleaned_data[name] for name in self.Meta.fields},
            "milestones": self.cleaned_data["milestones"],
        }


class CallbackUrlsForm(forms.Form):
    """The two URLs the integrator owns: the callback and the bridge."""

    callback_url = forms.URLField(
        label=_("Callback URL"),
        required=False,
        assume_scheme="https",
        widget=forms.URLInput(
            attrs={"placeholder": "https://sandbox.example.in/abdm/callbacks"},
        ),
        help_text=_(
            "Where the sandbox gateway delivers callbacks. Checked every 15 minutes.",
        ),
    )
    bridge_url = forms.URLField(
        label=_("Bridge URL"),
        required=False,
        assume_scheme="https",
        widget=forms.URLInput(
            attrs={"placeholder": "https://sandbox.example.in/abdm/bridge"},
        ),
        help_text=_("Your bridge service's base URL, as registered with the gateway."),
    )

    def _https_only(self, name: str) -> str:
        value = self.cleaned_data.get(name, "")
        if value and not value.lower().startswith("https://"):
            msg = _("Use an HTTPS URL.")
            raise forms.ValidationError(msg)
        return value

    def clean_callback_url(self) -> str:
        return self._https_only("callback_url")

    def clean_bridge_url(self) -> str:
        return self._https_only("bridge_url")


class ExitRequestForm(PortalFileFormMixin, forms.ModelForm):
    """The exit request form (design doc 5.6): dates, WASA, the two PDFs.

    Every field is optional so a draft can be saved half-done; "Request for
    exit" is what insists on completeness (ComplianceRecord.missing_fields).
    """

    download_kind = "compliance"

    class Meta:
        from .models import ComplianceRecord  # noqa: PLC0415

        model = ComplianceRecord
        fields = [
            "start_date",
            "end_date",
            "demo_date",
            "wasa_agency",
            "wasa_date",
            "functional_certificate",
            "functional_report",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "demo_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "wasa_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        labels = {
            "start_date": _("Start date"),
            "end_date": _("End date"),
            "demo_date": _("Tentative demo date"),
            "wasa_agency": _("WASA audit agency"),
            "wasa_date": _("WASA date"),
            "functional_certificate": _("Functional testing certificate"),
            "functional_report": _("Functional testing report"),
        }
        help_texts = {
            "functional_certificate": _("PDF, up to 10 MB."),
            "functional_report": _("PDF, up to 10 MB."),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        for name in ("functional_certificate", "functional_report"):
            self.fields[name].widget.attrs["accept"] = ".pdf"
        self.fields["wasa_agency"].widget.attrs.setdefault(
            "placeholder",
            _("CERT-In empanelled auditor"),
        )

    def _pdf(self, name: str):
        upload = self.cleaned_data.get(name)
        validate_upload(upload, extensions=PDF, max_mb=PDF_MAX_MB)
        return upload

    def clean_functional_certificate(self):
        return self._pdf("functional_certificate")

    def clean_functional_report(self):
        return self._pdf("functional_report")

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", _("The end date has to follow the start date."))
        return cleaned


# ── reviewer side ──────────────────────────────────────────────────────────


class QueueFilterForm(forms.Form):
    """The queue's chips, read from the query string. Unknown values mean 'All'."""

    CHIPS = [
        ("", _("All")),
        ("exit_request", _("Exit requests")),
        ("organisation_verification", _("Organisations")),
        ("product_registration", _("Products")),
        ("mine", _("Mine")),
    ]
    SCOPES = [
        ("open", _("Open")),
        ("decided", _("Decided")),
        ("all", _("Everything")),
    ]

    chip = forms.ChoiceField(choices=CHIPS, required=False)
    scope = forms.ChoiceField(choices=SCOPES, required=False)

    def chosen(self, name: str) -> str:
        self.is_valid()
        return self.cleaned_data.get(name) or ""


class ApproveForm(forms.Form):
    approved_on = forms.DateField(
        label=_("Approved on"),
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    approved_by = forms.CharField(
        label=_("Approved by"),
        max_length=255,
        disabled=True,
        required=False,
    )
    note = forms.CharField(
        label=_("Decision note"),
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": _(
                    "What was checked, and anything the integrator should know next.",
                ),
            },
        ),
    )

    def clean_approved_on(self):
        value = self.cleaned_data["approved_on"]
        from django.utils import timezone  # noqa: PLC0415

        if value > timezone.localdate():
            msg = _("The approval date cannot be in the future.")
            raise forms.ValidationError(msg)
        return value


class SendBackForm(forms.Form):
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": _("What has to change before it can be approved."),
            },
        ),
        error_messages={"required": _("Give the integrator a reason.")},
    )


class RaiseQueryForm(forms.Form):
    field_key = forms.CharField(
        label=_("Against"),
        required=False,
        widget=forms.HiddenInput,
    )
    field_label = forms.CharField(required=False, widget=forms.HiddenInput)
    question = forms.CharField(
        label=_("Question"),
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": _(
                    "Ask for the clarification or the corrected evidence.",
                ),
            },
        ),
        error_messages={"required": _("Write the question before sending it.")},
    )

    def clean_field_key(self) -> str:
        return self.cleaned_data.get("field_key") or "form"


class AssignForm(forms.Form):
    assignee = forms.ModelChoiceField(
        label=_("Assignee"),
        queryset=None,
        required=False,
        empty_label=_("Unassigned"),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model  # noqa: PLC0415

        self.fields["assignee"].queryset = (
            get_user_model()
            .objects.filter(is_ohc_team=True, is_active=True)
            .order_by("name", "email")
        )
        self.fields["assignee"].label_from_instance = lambda user: user.display_name
