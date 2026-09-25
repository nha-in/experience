"""Type and size limits on the milestone evidence documents."""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.datastructures import MultiValueDict

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.tests.test_workflow import xlsx
from ohc_experience.experiences import uploads

pytestmark = pytest.mark.django_db

PDF_FIELDS = ("wasa_certificate", "functional_certificate", "undertaking_form")


def xls(name="report.xls"):
    return SimpleUploadedFile(
        name,
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1workbook",
        content_type="application/vnd.ms-excel",
    )


def submitted(**replacements):
    return ExitEvidenceForm(
        data=evidence_data(),
        files=MultiValueDict({**files(), **replacements}),
    )


@pytest.mark.parametrize("field", PDF_FIELDS)
def test_an_evidence_pdf_over_five_megabytes_is_refused(field):
    document = pdf(f"{field}.pdf")
    document.size = uploads.MAX_EVIDENCE_BYTES + 1
    form = submitted(**{field: [document]})

    assert not form.is_valid()
    assert form.errors[field] == ["Files must be 5 MB or smaller."]


@pytest.mark.parametrize("field", PDF_FIELDS)
def test_an_evidence_pdf_at_five_megabytes_is_accepted(field):
    document = pdf(f"{field}.pdf")
    document.size = uploads.MAX_EVIDENCE_BYTES
    form = submitted(**{field: [document]})

    assert form.is_valid(), form.errors


def test_the_undertaking_no_longer_takes_a_word_document():
    """It was the one evidence field with no server-side check at all."""
    document = SimpleUploadedFile(
        "undertaking.docx",
        b"PK\x03\x04undertaking",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    form = submitted(undertaking_form=[document])

    assert not form.is_valid()
    assert form.errors["undertaking_form"] == ["Upload a PDF document."]


@pytest.mark.parametrize("workbook", [xlsx, xls])
def test_a_functional_report_takes_either_excel_format(workbook):
    form = submitted(functional_report=[workbook()])

    assert form.is_valid(), form.errors


def test_a_functional_report_refuses_a_pdf():
    form = submitted(functional_report=[pdf("report.pdf")])

    assert not form.is_valid()
    assert form.errors["functional_report"] == [
        "Upload an Excel workbook (.xls or .xlsx).",
    ]


def test_three_functional_reports_are_accepted():
    form = submitted(
        functional_report=[xlsx(f"report-{index}.xlsx") for index in range(3)],
    )

    assert form.is_valid(), form.errors


def test_a_fourth_functional_report_is_refused():
    form = submitted(
        functional_report=[xlsx(f"report-{index}.xlsx") for index in range(4)],
    )

    assert not form.is_valid()
    assert "no more than 3" in str(form.errors["functional_report"])


def test_a_functional_report_over_five_megabytes_is_refused():
    workbook = xlsx()
    workbook.size = uploads.MAX_EVIDENCE_BYTES + 1
    form = submitted(functional_report=[workbook])

    assert not form.is_valid()
    assert form.errors["functional_report"] == ["Files must be 5 MB or smaller."]


def test_uploads_at_large_keep_the_ten_megabyte_limit():
    """Only the evidence documents were tightened."""
    document = pdf()
    document.size = uploads.MAX_UPLOAD_BYTES
    uploads.validate_pdf(document)
    document.size = uploads.MAX_UPLOAD_BYTES + 1
    with pytest.raises(ValidationError, match="10 MB"):
        uploads.validate_pdf(document)


@pytest.mark.parametrize(
    ("field", "accept", "help_text"),
    [
        ("wasa_certificate", ".pdf", "PDF, up to 5 MB."),
        ("functional_certificate", ".pdf", "PDF, up to 5 MB."),
        (
            "functional_report",
            ".xls,.xlsx",
            "Excel workbook (.xls or .xlsx), up to 3 files, 5 MB each.",
        ),
        ("undertaking_form", ".pdf", "PDF, up to 5 MB."),
    ],
)
def test_each_evidence_field_states_what_it_takes(field, accept, help_text):
    """The picker and the hint say the same thing the validators enforce."""
    bound = ExitEvidenceForm().fields[field]

    assert bound.widget.attrs["accept"] == accept
    assert bound.help_text == help_text
