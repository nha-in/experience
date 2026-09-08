from pathlib import Path

from django.core.exceptions import ValidationError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def validate_upload_size(upload):
    if upload.size > MAX_UPLOAD_BYTES:
        msg = "Files must be 10 MB or smaller."
        raise ValidationError(msg)


def validate_pdf(upload):
    validate_upload_size(upload)
    signature = upload.read(5)
    upload.seek(0)
    if Path(upload.name).suffix.lower() != ".pdf" or signature != b"%PDF-":
        msg = "Upload a PDF document."
        raise ValidationError(msg)
