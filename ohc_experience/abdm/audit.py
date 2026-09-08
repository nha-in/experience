"""The audit trail: who changed what, when, and how it differed.

Every write on an organisation, product, compliance record or review item goes
through ``abdm.services``, and every one of those calls ``record_audit`` with
the snapshot it took before touching the row. The log is append-only.
"""

from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db import models


def _plain(value: Any) -> Any:
    if isinstance(value, models.fields.files.FieldFile):
        return value.name or ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, models.Model):
        return value.pk
    return value


def snapshot(instance: models.Model) -> dict[str, Any]:
    """The row as plain JSON-able values, one entry per concrete field."""
    result = {}
    for field in instance._meta.concrete_fields:  # noqa: SLF001
        if field.name in {"secret_encrypted", "password"}:
            continue
        result[field.name] = _plain(getattr(instance, field.attname))
    return result


def record_audit(
    *,
    actor,
    instance: models.Model,
    action: str,
    before: dict[str, Any] | None = None,
):
    from .models import AuditLog  # noqa: PLC0415 - avoid an import cycle

    after = snapshot(instance)
    if before is None:
        diff = {key: {"before": None, "after": value} for key, value in after.items()}
    else:
        diff = {
            key: {"before": before.get(key), "after": value}
            for key, value in after.items()
            if before.get(key) != value
        }
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        model_label=instance._meta.label_lower,  # noqa: SLF001
        object_pk=str(instance.pk),
        object_repr=str(instance)[:255],
        action=action,
        diff=diff,
    )
