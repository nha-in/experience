"""Upload plumbing shared by every form that carries a file field.

Two things live here. ``validate_upload`` and the size limits are what any
form uses to refuse the wrong file type or an oversized file. The rest gives
an ordinary ``ModelForm`` the shape ``components/file_upload_field.html``
expects: the engine's own ``ExperienceForm`` exposes ``existing_files`` and
``removed_file_ids`` for the files a submission already holds, and
``ModelFileFormMixin`` does the same for a model's saved ``FileField`` values.
Each saved file is presented as an ``ExistingFile`` whose ``pk`` is the field
name, the remove toggle posts ``remove_files__<field>=<field>``, and a removal
without a replacement clears the column on save.

Where a saved file can be downloaded from is the one thing this module cannot
know; a form provides it through the ``download_url`` hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _

MB = 1024 * 1024
PDF = frozenset({".pdf"})
IMAGES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
DOCUMENTS = PDF | IMAGES
PDF_MAX_MB = 10
IMAGE_MAX_MB = 2


def validate_upload(upload, *, extensions, max_mb: int) -> None:
    """Refuse the wrong file type or an oversized file.

    ``None`` (nothing uploaded) and ``False`` (a ModelForm clearing the field)
    both pass; there is nothing to check.
    """
    if upload is None or upload is False:
        return
    allowed = frozenset(extensions)
    extension = Path(upload.name).suffix.lower()
    if extension not in allowed:
        msg = _("Use one of these file types: %(types)s.") % {
            "types": ", ".join(sorted(allowed)),
        }
        raise ValidationError(msg)
    if upload.size > max_mb * MB:
        msg = _("The file must be smaller than %(size)s MB.") % {"size": max_mb}
        raise ValidationError(msg)


def validate_uploads(uploads, *, extensions, max_mb: int) -> None:
    """``validate_upload`` for every file a ``MultipleFileField`` returned."""
    for upload in uploads or []:
        validate_upload(upload, extensions=extensions, max_mb=max_mb)


@dataclass(frozen=True)
class ExistingFile:
    """A saved file, in the shape the upload widget draws."""

    pk: str
    original_name: str
    size: int
    download_url: str


def existing_file(
    instance,
    field_name: str,
    *,
    download_url: str,
) -> ExistingFile | None:
    value = getattr(instance, field_name, None)
    if not value:
        return None
    try:
        size = value.size
    except OSError, ValueError:
        # Object storage may be unreachable from a form render; the name is
        # what matters and the size is decoration.
        size = 0
    return ExistingFile(
        pk=field_name,
        original_name=Path(value.name).name,
        size=size,
        download_url=download_url,
    )


class ModelFileFormMixin:
    """Existing-file presentation and removal for a ModelForm's FileFields."""

    def download_url(self, field_name: str) -> str:
        """Where the saved file in ``field_name`` can be fetched from.

        Downloads never expose a storage path, so the default is no link at
        all; a form that serves its files overrides this.
        """
        return ""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.existing_files: dict[str, list[ExistingFile]] = {}
        self.removed_file_ids: dict[str, set[str]] = {}
        for name, field in self.fields.items():
            if not isinstance(field, forms.FileField):
                continue
            current = (
                existing_file(
                    self.instance,
                    name,
                    download_url=self.download_url(name),
                )
                if getattr(self.instance, "pk", None)
                else None
            )
            self.existing_files[name] = [current] if current else []
            self.removed_file_ids[name] = (
                {name} if name in self._posted_removals(name) else set()
            )

    def _posted_removals(self, name: str) -> set[str]:
        key = f"remove_files__{name}"
        if hasattr(self.data, "getlist"):
            return set(self.data.getlist(key))
        value = self.data.get(key) if self.data else None
        if value is None:
            return set()
        return set(value) if isinstance(value, (list, tuple, set)) else {value}

    def new_upload(self, name: str):
        """The file posted this time, or None: a ModelForm's FileField hands
        back the *saved* FieldFile when nothing new was uploaded."""
        value = self.cleaned_data.get(name)
        return value if isinstance(value, UploadedFile) else None

    def has_file(self, name: str) -> bool:
        """A new upload, or a saved file that was not marked for removal."""
        if self.new_upload(name) is not None:
            return True
        return bool(self.existing_files.get(name)) and not self.removed_file_ids.get(
            name,
        )

    def clean(self):
        cleaned = super().clean()
        for name, removed in self.removed_file_ids.items():
            if removed and self.new_upload(name) is None:
                # False tells FileField.save_form_data to clear the column.
                cleaned[name] = False
        return cleaned
