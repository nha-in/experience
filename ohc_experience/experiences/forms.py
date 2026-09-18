from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator
from django.utils.translation import gettext_lazy as _

from ohc_experience.support.models import Category

from .fields import MultipleFileField
from .production import validate_client_id
from .production import validate_issued_on
from .registry import get_program
from .uploads import validate_pdf


class ExperienceForm(forms.Form):
    """A form that retains, removes and appends previously uploaded files."""

    def __init__(
        self,
        *args,
        existing_files=None,
        **kwargs,
    ) -> None:
        self.existing_files = existing_files or {}
        super().__init__(*args, **kwargs)
        self.removed_file_ids = {
            field_name: self._posted_removals(field_name)
            for field_name, field in self.fields.items()
            if isinstance(field, forms.FileField)
        }

    def _posted_removals(self, field_name: str) -> set[int]:
        key = f"remove_files__{field_name}"
        values = (
            self.data.getlist(key)
            if hasattr(self.data, "getlist")
            else self.data.get(key, [])
        )
        if not isinstance(values, (list, tuple)):
            values = [values]
        result = set()
        for value in values:
            try:
                result.add(int(value))
            except TypeError, ValueError:
                continue
        return result

    def retained_existing_files(self, field_name: str):
        removed_ids = self.removed_file_ids.get(field_name, set())
        return [
            attachment
            for attachment in self.existing_files.get(field_name, [])
            if attachment.pk not in removed_ids
        ]

    def clean(self):
        cleaned = super().clean()
        for field_name, field in self.fields.items():
            if not isinstance(field, MultipleFileField) or field_name in self.errors:
                continue
            existing_count = len(self.retained_existing_files(field_name))
            upload_count = len(cleaned.get(field_name) or [])
            final_count = existing_count + upload_count
            if final_count < field.min_files:
                self.add_error(
                    field_name,
                    ValidationError(
                        field.error_messages["too_few_files"],
                        code="too_few_files",
                        params={"minimum": field.min_files},
                    ),
                )
            elif field.max_files is not None and final_count > field.max_files:
                self.add_error(
                    field_name,
                    ValidationError(
                        field.error_messages["too_many_files"],
                        code="too_many_files",
                        params={"maximum": field.max_files},
                    ),
                )
        return cleaned

    def require_upload(self, field_name: str, label: str) -> None:
        has_upload = self.cleaned_data.get(field_name)
        has_upload = has_upload or self.retained_existing_files(field_name)
        if not has_upload and not self.has_error(field_name):
            self.add_error(
                field_name,
                _("Upload %(label)s before completing this form.") % {"label": label},
            )


class ReviewForm(ExperienceForm):
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


class ProductionAccessForm(forms.Form):
    client_id = forms.CharField(
        label=_("Production client ID"),
        max_length=255,
        help_text=_("As issued by the gateway team. No secret is stored here."),
    )
    issued_on = forms.DateField(
        label=_("Production issue date"),
        help_text=_("The day the gateway team issued the production credentials."),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    #: The ID the form was opened with, so a concurrent change is not overwritten.
    expected = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_client_id(self):
        return validate_client_id(self.cleaned_data["client_id"])

    def clean_issued_on(self):
        return validate_issued_on(self.cleaned_data["issued_on"])


class CredentialURLsForm(forms.Form):
    callback_url = forms.URLField(required=False)
    bridge_url = forms.URLField(required=False)

    def clean(self):
        cleaned = super().clean()
        for key, value in cleaned.items():
            if value and not value.lower().startswith("https://"):
                self.add_error(key, "Use an HTTPS URL.")
        return cleaned


#: Resolving a ticket takes a reply that says how it was resolved.
RESOLVE_COMMENT_MIN_LENGTH = 10


class SupportForm(forms.Form):
    subject = forms.CharField(max_length=255)
    category = forms.ChoiceField(
        choices=Category.choices,
        initial=Category.SANDBOX,
        required=False,
    )
    track = forms.ChoiceField(required=False)
    priority = forms.ChoiceField(
        choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")],
        initial="medium",
    )
    body = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 5}))
    attachments = MultipleFileField(
        required=False,
        max_files=5,
        validators=[validate_pdf],
        accept=".pdf",
    )

    def __init__(self, *args, program=None, workspace=None, resolving=False, **kwargs):
        super().__init__(*args, **kwargs)
        if resolving:
            body = self.fields["body"]
            body.validators.append(MinLengthValidator(RESOLVE_COMMENT_MIN_LENGTH))
            body.error_messages["required"] = body.error_messages["min_length"] = (
                f"Add a comment of at least {RESOLVE_COMMENT_MIN_LENGTH} characters "
                "to resolve this ticket."
            )
        program = program or (workspace.definition if workspace else get_program())
        applied = (
            {value.split(":", 1)[0] for value in workspace.applied_milestones}
            if workspace
            else None
        )
        self.fields["track"].choices = [
            ("", "Not track-specific"),
            *(
                (track.code, f"{track.code} · {track.name}")
                for track in program.tracks
                if applied is None or track.code in applied
            ),
        ]

    def clean_category(self):
        return self.cleaned_data["category"] or Category.SANDBOX
