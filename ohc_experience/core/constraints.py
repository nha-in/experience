from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError

if TYPE_CHECKING:
    from django.db import models


def validate_constraint_on_field(instance: models.Model, name: str, field: str) -> None:
    """Check the model's constraint `name` and report a violation on `field`.

    Django lists a broken check constraint above the form, since it can't tell
    which field is wrong. Called from the model's clean(), this shows the
    constraint's own message under `field` instead, and Django then skips the
    constraint in its own pass so the message isn't repeated. Every form for
    the model must include `field`.
    """
    constraint = {c.name: c for c in instance._meta.constraints}[name]  # noqa: SLF001
    try:
        constraint.validate(type(instance), instance)
    except ValidationError as error:
        raise ValidationError({field: error}) from None
