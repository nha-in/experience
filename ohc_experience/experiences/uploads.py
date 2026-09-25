from pathlib import Path

from django.core.exceptions import ValidationError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Milestone evidence is held to a tighter cap than uploads at large.
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
# An .xls is an OLE2 compound file; an .xlsx is a zip container.
SPREADSHEET_SIGNATURES = {
    ".xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    ".xlsx": b"PK\x03\x04",
}


def validate_upload_size(upload, limit=MAX_UPLOAD_BYTES):
    if upload.size > limit:
        msg = f"Files must be {limit // (1024 * 1024)} MB or smaller."
        raise ValidationError(msg)


def _validate_pdf_content(upload):
    signature = upload.read(5)
    upload.seek(0)
    if Path(upload.name).suffix.lower() != ".pdf" or signature != b"%PDF-":
        msg = "Upload a PDF document."
        raise ValidationError(msg)


def validate_pdf(upload):
    validate_upload_size(upload)
    _validate_pdf_content(upload)


def validate_evidence_pdf(upload):
    """A milestone evidence PDF: the same document, under the tighter cap."""
    validate_upload_size(upload, MAX_EVIDENCE_BYTES)
    _validate_pdf_content(upload)


def validate_evidence_spreadsheet(upload):
    """A milestone evidence workbook: Excel only, under the tighter cap."""
    validate_upload_size(upload, MAX_EVIDENCE_BYTES)
    expected = SPREADSHEET_SIGNATURES.get(Path(upload.name).suffix.lower())
    signature = upload.read(8)
    upload.seek(0)
    if not expected or not signature.startswith(expected):
        msg = "Upload an Excel workbook (.xls or .xlsx)."
        raise ValidationError(msg)
