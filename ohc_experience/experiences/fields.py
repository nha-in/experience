from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """A file field that validates every newly selected file and returns a list."""

    widget = MultipleFileInput
    default_error_messages = {
        **forms.FileField.default_error_messages,
        "too_few_files": _("Keep at least %(minimum)s files in this section."),
        "too_many_files": _("Keep no more than %(maximum)s files in this section."),
    }

    def __init__(
        self,
        *args,
        min_files: int = 0,
        max_files: int | None = None,
        accept: str = "",
        **kwargs,
    ) -> None:
        self.min_files = min_files
        self.max_files = max_files
        super().__init__(*args, **kwargs)
        if accept:
            self.widget.attrs["accept"] = accept

    def clean(self, data, initial=None):
        uploads = list(data) if isinstance(data, (list, tuple)) else [data]
        uploads = [upload for upload in uploads if upload]
        if not uploads:
            if self.required:
                raise ValidationError(self.error_messages["required"], code="required")
            return []
        if self.max_files is not None and len(uploads) > self.max_files:
            raise ValidationError(
                self.error_messages["too_many_files"],
                code="too_many_files",
                params={"maximum": self.max_files},
            )
        clean_one = super().clean
        return [clean_one(upload, initial) for upload in uploads]
