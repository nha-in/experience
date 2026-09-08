from __future__ import annotations

import secrets
import string
from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from django import forms
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .fields import MultipleFileField
from .models import ApplicationFormUse
from .models import ApplicationInstance
from .models import FormAttachment
from .models import FormRecord
from .models import FormReuseScope
from .models import ProductOutcome
from .registry import registry

REFERENCE_ALPHABET = string.ascii_uppercase + string.digits


def _make_reference(prefix: str) -> str:
    for _attempt in range(10):
        suffix = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))
        reference = f"{prefix}-{timezone.now():%y}-{suffix}"
        if not ApplicationInstance.objects.filter(reference=reference).exists():
            return reference
    msg = _("Could not allocate a unique application reference. Try again.")
    raise ValidationError(msg)


def _make_form_reference() -> str:
    for _attempt in range(10):
        suffix = "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(8))
        reference = f"FORM-{suffix}"
        if not FormRecord.objects.filter(reference=reference).exists():
            return reference
    msg = _("Could not allocate a unique form reference. Try again.")
    raise ValidationError(msg)


def _resolve_form_record(*, application, form_definition, user) -> FormRecord:
    defaults = {
        "reference": _make_form_reference(),
        "name": form_definition.name,
        "created_by": user,
        "metadata": {"schema_version": form_definition.schema_version},
    }
    if form_definition.reuse_scope == FormReuseScope.ORGANISATION:
        form_record, _created = FormRecord.objects.get_or_create(
            organisation=application.organisation,
            product=None,
            form_key=form_definition.key,
            reuse_scope=FormReuseScope.ORGANISATION,
            defaults=defaults,
        )
    elif form_definition.reuse_scope == FormReuseScope.PRODUCT:
        form_record, _created = FormRecord.objects.get_or_create(
            organisation=application.organisation,
            product=application.product,
            form_key=form_definition.key,
            reuse_scope=FormReuseScope.PRODUCT,
            defaults=defaults,
        )
    else:
        form_record = FormRecord.objects.create(
            organisation=application.organisation,
            product=application.product,
            form_key=form_definition.key,
            reuse_scope=FormReuseScope.APPLICATION,
            **defaults,
        )
    if form_record.name != str(form_definition.name):
        form_record.name = form_definition.name
        form_record.save(update_fields=["name", "updated_at"])
    return form_record


def materialize_application_forms(*, application, definition, user) -> None:
    for form_definition in definition.forms:
        existing_use = application.form_uses.filter(
            form_key=form_definition.key,
        ).first()
        if existing_use is not None:
            continue
        form_record = _resolve_form_record(
            application=application,
            form_definition=form_definition,
            user=user,
        )
        form_use = ApplicationFormUse(
            application=application,
            form=form_record,
            form_key=form_definition.key,
            selected_submission=form_record.current_submission,
        )
        form_use.full_clean()
        form_use.save()


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


@transaction.atomic
def create_application(*, application_type: str, product, user):
    """Create a registered application and link its independent form records."""
    definition = registry.get(application_type)
    if not product.organisation.memberships.filter(user=user).exists():
        msg = _("Only an organisation member can start its application.")
        raise PermissionDenied(msg)
    application = ApplicationInstance.objects.create(
        reference=_make_reference(definition.reference_prefix),
        application_type=definition.key,
        title=definition.name,
        product=product,
        created_by=user,
        status=definition.initial_status,
    )
    materialize_application_forms(
        application=application,
        definition=definition,
        user=user,
    )
    for outcome in definition.on_start(application, user):
        issue_outcome(application=application, actor=user, outcome=outcome)
    return application


def issue_outcome(*, application, actor, outcome):
    result, _ = ProductOutcome.objects.update_or_create(
        product=application.product,
        source_application=application,
        outcome_type=outcome.key,
        defaults={
            "name": outcome.name,
            "status": outcome.status,
            "data": _json_value(outcome.data),
            "metadata": outcome.metadata,
            "field_schema": outcome.field_schema,
            "valid_until": outcome.valid_until,
            "issued_by": actor,
        },
    )
    return result


def submission_payload(form, initial_data):
    stored_data = dict(initial_data)
    uploads: dict[str, list[UploadedFile]] = {}
    multiple_upload_fields: set[str] = set()
    for field_name, value in form.cleaned_data.items():
        form_field = form.fields[field_name]
        if not isinstance(form_field, forms.FileField):
            stored_data[field_name] = _json_value(value)
            continue
        if isinstance(value, UploadedFile):
            uploads[field_name] = [value]
        elif isinstance(value, (list, tuple)):
            uploads[field_name] = [
                upload for upload in value if isinstance(upload, UploadedFile)
            ]
        if isinstance(form_field, MultipleFileField):
            multiple_upload_fields.add(field_name)
        if not uploads.get(field_name):
            uploads.pop(field_name, None)
    return stored_data, uploads, multiple_upload_fields


def _choice_schema(choices) -> list[dict[str, Any]]:
    result = []
    for value, label in choices:
        if isinstance(label, (list, tuple)):
            result.extend(_choice_schema(label))
        else:
            result.append({"value": _json_value(value), "label": str(label)})
    return result


def form_field_schema(form) -> list[dict[str, Any]]:
    return [
        {
            "key": name,
            "label": str(field.label or name.replace("_", " ").title()),
            "type": field.__class__.__name__,
            "required": field.required,
            "choices": _choice_schema(field.choices)
            if getattr(field, "choices", None)
            else [],
        }
        for name, field in form.fields.items()
    ]


def clone_current_attachments(*, source, destination, form) -> None:
    if source is None:
        return
    file_fields = {
        name
        for name, field in form.fields.items()
        if isinstance(field, forms.FileField)
    }
    for attachment in source.attachments.filter(
        field_key__in=file_fields,
        is_current=True,
    ):
        form_field = form.fields[attachment.field_key]
        removed_ids = getattr(form, "removed_file_ids", {}).get(
            attachment.field_key,
            set(),
        )
        is_multiple_file = isinstance(form_field, MultipleFileField)
        replacing_single_file = not is_multiple_file and form.cleaned_data.get(
            attachment.field_key,
        )
        if attachment.pk in removed_ids or replacing_single_file:
            continue
        FormAttachment.objects.create(
            submission=destination,
            field_key=attachment.field_key,
            file=attachment.file.name,
            original_name=attachment.original_name,
            content_type=attachment.content_type,
            size=attachment.size,
            uploaded_by=attachment.uploaded_by,
        )


def store_uploads(
    *,
    submission,
    uploads,
    multiple_upload_fields,
    stored_data,
    user,
) -> None:
    for field_name, field_uploads in uploads.items():
        if field_name not in multiple_upload_fields:
            submission.attachments.filter(
                field_key=field_name,
                is_current=True,
            ).update(is_current=False)
        attachment_data = []
        for upload in field_uploads:
            attachment = FormAttachment.objects.create(
                submission=submission,
                field_key=field_name,
                file=upload,
                original_name=upload.name,
                content_type=getattr(upload, "content_type", ""),
                size=upload.size,
                uploaded_by=user,
            )
            attachment_data.append(
                {
                    "attachment_id": attachment.pk,
                    "name": attachment.original_name,
                    "size": attachment.size,
                },
            )
        stored_data[field_name] = (
            attachment_data
            if field_name in multiple_upload_fields
            else attachment_data[0]
        )


def synchronize_attachment_data(*, submission, form, stored_data) -> bool:
    has_file_fields = False
    for field_name, form_field in form.fields.items():
        if not isinstance(form_field, forms.FileField):
            continue
        has_file_fields = True
        attachments = list(
            submission.attachments.filter(
                field_key=field_name,
                is_current=True,
            ).order_by("created_at", "pk"),
        )
        attachment_data = [
            {
                "attachment_id": attachment.pk,
                "name": attachment.original_name,
                "size": attachment.size,
            }
            for attachment in attachments
        ]
        if isinstance(form_field, MultipleFileField):
            if attachment_data:
                stored_data[field_name] = attachment_data
            else:
                stored_data.pop(field_name, None)
        elif attachment_data:
            stored_data[field_name] = attachment_data[0]
        else:
            stored_data.pop(field_name, None)
    return has_file_fields
