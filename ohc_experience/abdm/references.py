"""Human-quotable identifiers: ``SBX-2026-00042`` and ``REV-2026-00017``.

Sequences restart each year and are derived from the highest existing number,
never from a row count, so a deleted row can never hand its id to a new one.
"""

from __future__ import annotations

from django.utils import timezone

SANDBOX_PREFIX = "SBX"
REVIEW_PREFIX = "REV"
SEQUENCE_WIDTH = 5


def _next(prefix: str, existing: list[str], year: int | None = None) -> str:
    year = year or timezone.localdate().year
    stem = f"{prefix}-{year}-"
    numbers = [
        int(value[len(stem) :])
        for value in existing
        if value.startswith(stem) and value[len(stem) :].isdigit()
    ]
    return f"{stem}{(max(numbers, default=0) + 1):0{SEQUENCE_WIDTH}d}"


def next_sandbox_id(year: int | None = None) -> str:
    from .models import Product  # noqa: PLC0415 - avoid an import cycle

    stem = f"{SANDBOX_PREFIX}-{year or timezone.localdate().year}-"
    latest = list(
        Product.objects.filter(sandbox_id__startswith=stem)
        .order_by("-sandbox_id")
        .values_list("sandbox_id", flat=True)[:1],
    )
    return _next(SANDBOX_PREFIX, latest, year)


def next_review_reference(year: int | None = None) -> str:
    from .models import ReviewItem  # noqa: PLC0415 - avoid an import cycle

    stem = f"{REVIEW_PREFIX}-{year or timezone.localdate().year}-"
    latest = list(
        ReviewItem.objects.filter(reference__startswith=stem)
        .order_by("-reference")
        .values_list("reference", flat=True)[:1],
    )
    return _next(REVIEW_PREFIX, latest, year)
