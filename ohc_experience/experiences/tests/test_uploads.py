from __future__ import annotations

import pytest
from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from ohc_experience.experiences.uploads import MB
from ohc_experience.experiences.uploads import PDF
from ohc_experience.experiences.uploads import ModelFileFormMixin
from ohc_experience.experiences.uploads import existing_file
from ohc_experience.experiences.uploads import validate_upload
from ohc_experience.experiences.uploads import validate_uploads
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.tests.factories import OrganisationFactory

FIELD = "verification_document"


def pdf(name: str = "report.pdf", size: int = 10) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%" * size, content_type="application/pdf")


def test_validate_upload_accepts_nothing_and_a_cleared_field():
    validate_upload(None, extensions=PDF, max_mb=1)
    validate_upload(False, extensions=PDF, max_mb=1)  # noqa: FBT003


def test_validate_upload_refuses_the_wrong_type_and_oversized_files():
    with pytest.raises(ValidationError, match="file types"):
        validate_upload(pdf("report.exe"), extensions=PDF, max_mb=1)
    with pytest.raises(ValidationError, match="smaller than 2 MB"):
        validate_upload(pdf(size=3 * MB), extensions={".pdf"}, max_mb=2)
    validate_upload(pdf(size=MB), extensions=PDF, max_mb=2)


def test_validate_uploads_checks_every_file():
    validate_uploads(None, extensions=PDF, max_mb=1)
    validate_uploads([pdf(), pdf("two.pdf")], extensions=PDF, max_mb=1)
    with pytest.raises(ValidationError, match="file types"):
        validate_uploads([pdf(), pdf("photo.png")], extensions=PDF, max_mb=1)


class DocumentForm(ModelFileFormMixin, forms.ModelForm):
    class Meta:
        model = Organisation
        fields = [FIELD]

    def download_url(self, field_name: str) -> str:
        return f"/files/{field_name}/"


@pytest.mark.django_db
def test_existing_file_describes_a_saved_file_in_the_widget_shape():
    organisation = OrganisationFactory(onboarded=True)

    saved = existing_file(organisation, FIELD, download_url="/files/x/")
    missing = existing_file(organisation, "logo", download_url="/files/y/")

    assert saved is not None
    assert saved.pk == FIELD
    assert saved.original_name == "pan.pdf"
    assert saved.download_url == "/files/x/"
    assert missing is None


@pytest.mark.django_db
def test_model_file_form_lists_saved_files_and_clears_on_removal():
    organisation = OrganisationFactory(onboarded=True)

    unbound = DocumentForm(instance=organisation)
    removal = DocumentForm(
        {f"remove_files__{FIELD}": FIELD},
        instance=organisation,
    )
    replacement = DocumentForm(
        {f"remove_files__{FIELD}": FIELD},
        {FIELD: pdf("new-pan.pdf")},
        instance=organisation,
    )

    assert [item.original_name for item in unbound.existing_files[FIELD]] == ["pan.pdf"]
    assert unbound.existing_files[FIELD][0].download_url == f"/files/{FIELD}/"
    assert unbound.removed_file_ids[FIELD] == set()
    assert removal.is_valid(), removal.errors
    assert removal.cleaned_data[FIELD] is False
    assert removal.has_file(FIELD) is False
    assert replacement.is_valid(), replacement.errors
    assert replacement.new_upload(FIELD).name == "new-pan.pdf"
    assert replacement.has_file(FIELD) is True


@pytest.mark.django_db
def test_model_file_form_without_a_saved_file_has_nothing_to_show():
    form = DocumentForm({}, instance=OrganisationFactory())

    assert form.existing_files[FIELD] == []
    assert form.is_valid(), form.errors
    assert form.has_file(FIELD) is False
