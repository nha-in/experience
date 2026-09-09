"""Reads for the portal screens: queue rows, dashboard numbers, view context.

Nothing here writes. Views (and other apps' views) call these so the same
question is always answered the same way.
"""

from __future__ import annotations

from django.db.models import Q

from .forms import QueryReplyForm
from .models import Product
from .models import ReviewHistory
from .models import ReviewItem
from .services import WRITER_ROLES
from .tracks import TRACKS
from .uploads import document_url

ACTIVITY_LIMIT = 12


def can_write(membership) -> bool:
    """May this organisation member change things, or only read them?"""
    return membership is not None and membership.role in WRITER_ROLES


def query_thread_context(item, *, can_reply: bool) -> dict:
    """What abdm/partials/query_thread.html needs, for any item type."""
    return {
        "item": item,
        "item_queries": list(item.queries.select_related("raised_by", "replied_by")),
        "can_reply": can_reply,
        "reply_form": QueryReplyForm(),
    }


def products_for(organisation):
    return Product.objects.for_organisation(organisation).with_related()


def activity_for_product(product, limit: int = ACTIVITY_LIMIT):
    """The history feed on the overview: the product's items plus the org's."""
    return (
        ReviewHistory.objects.filter(
            Q(item__product=product)
            | Q(
                item__organisation=product.organisation,
                item__item_type=ReviewItem.Type.ORGANISATION_VERIFICATION,
            ),
        )
        .select_related("actor", "item", "item__compliance", "item__product")
        .order_by("-created_at", "-pk")[:limit]
    )


def track_summaries(product) -> list[dict]:
    """One entry per applied track: tiles, and n of m approved."""
    summaries = []
    for track in TRACKS:
        if not product.has_track(track.code):
            continue
        approved, applied = product.approved_count(track)
        summaries.append(
            {
                "track": track,
                "entries": product.records_for_track(track),
                "approved": approved,
                "applied": applied,
            },
        )
    return summaries


# ── reviewer side ──────────────────────────────────────────────────────────


def queue_items(*, chip: str = "", scope: str = "open", user=None):
    """The review queue, oldest submission first, narrowed by chip and scope."""
    items = ReviewItem.objects.with_related()
    if scope == "decided":
        items = items.decided()
    elif scope != "all":
        items = items.open()
    if chip == "mine" and user is not None:
        items = items.filter(assignee=user)
    elif chip in ReviewItem.Type.values:
        items = items.of_type(chip)
    return items.order_by("submitted_on", "pk")


def queue_chip_counts(*, scope: str = "open", user=None) -> dict[str, int]:
    """How many items each chip on the queue would show, for the chip labels."""
    from .forms import QueueFilterForm  # noqa: PLC0415

    return {
        value: queue_items(chip=value, scope=scope, user=user).count()
        for value, _label in QueueFilterForm.CHIPS
    }


def submitted_form_sections(item) -> list[dict]:
    """The submitted form as sections of (key, label, value, file url) rows.

    What the review detail shows on the left, with a Query action per field:
    the keys are what queries are raised against.
    """
    if item.is_exit_request:
        return _exit_request_sections(item.compliance)
    if item.is_product_registration:
        return _product_sections(item.product)
    return _organisation_sections(item.organisation)


def _row(key, label, value="", *, url="", mono=False, file=False) -> dict:  # noqa: PLR0913
    """One submitted field. ``file`` marks a stored upload rather than a link."""
    return {
        "key": key,
        "label": str(label),
        "value": value,
        "url": url,
        "mono": mono,
        "file": file,
    }


def _organisation_sections(organisation) -> list[dict]:
    logo_url = (
        document_url("organisation", organisation.pk, "logo")
        if organisation.logo
        else ""
    )
    verification_url = (
        document_url("organisation", organisation.pk, "verification_document")
        if organisation.verification_document
        else ""
    )
    return [
        {
            "title": "Identity",
            "rows": [
                _row("name", "Name of the entity", organisation.name),
                _row("description", "Description", organisation.description),
                _row(
                    "entity_type",
                    "Type of entity",
                    organisation.get_entity_type_display(),
                ),
                _row("category", "Category", organisation.get_category_display()),
                _row(
                    "website",
                    "Website",
                    organisation.website,
                    url=organisation.website,
                ),
                _row(
                    "logo",
                    "Logo",
                    "Download" if logo_url else "",
                    url=logo_url,
                    file=True,
                ),
            ],
        },
        {
            "title": "Registered address",
            "rows": [
                _row("registered_address", "Address", organisation.registered_address),
                _row("pincode", "Pincode", organisation.pincode),
                _row("state", "State", organisation.state),
                _row("district", "District", organisation.district),
            ],
        },
        {
            "title": "Verification document",
            "rows": [
                _row(
                    "verification_document_type",
                    "Document type",
                    organisation.verification_document_type,
                ),
                _row(
                    "verification_document_number",
                    "Document number",
                    organisation.verification_document_number,
                    mono=True,
                ),
                _row(
                    "verification_document",
                    "Supporting document",
                    "Download" if verification_url else "",
                    url=verification_url,
                    file=True,
                ),
            ],
        },
    ]


def _product_sections(product) -> list[dict]:
    from .tracks import milestone_label  # noqa: PLC0415
    from .tracks import parse_key  # noqa: PLC0415

    milestones = ", ".join(
        f"{track} {milestone_label(track, code)}"
        for track, code in (parse_key(key) for key in product.applied_milestones)
    )
    return [
        {
            "title": "Product",
            "rows": [
                _row("name", "Product name", product.name),
                _row("description", "Description", product.description),
                _row("category", "Category", product.get_category_display()),
                _row(
                    "solution_type",
                    "Solution type",
                    product.get_solution_type_display(),
                ),
                _row("sandbox_id", "Sandbox id", product.sandbox_id, mono=True),
            ],
        },
        {
            "title": "Tracks and milestones",
            "rows": [
                _row("applied_tracks", "Tracks", ", ".join(product.applied_tracks)),
                _row("applied_milestones", "Milestones", milestones),
            ],
        },
    ]


def _exit_request_sections(record) -> list[dict]:
    from django.utils.formats import date_format  # noqa: PLC0415

    def day(value):
        return date_format(value, "j M Y") if value else ""

    certificate_url = (
        document_url("compliance", record.pk, "functional_certificate")
        if record.functional_certificate
        else ""
    )
    report_url = (
        document_url("compliance", record.pk, "functional_report")
        if record.functional_report
        else ""
    )
    return [
        {
            "title": "Sandbox testing",
            "rows": [
                _row("start_date", "Start date", day(record.start_date)),
                _row("end_date", "End date", day(record.end_date)),
                _row("demo_date", "Tentative demo date", day(record.demo_date)),
            ],
        },
        {
            "title": "WASA",
            "rows": [
                _row("wasa_agency", "WASA audit agency", record.wasa_agency),
                _row("wasa_issued_on", "WASA issued on", day(record.wasa_issued_on)),
                _row(
                    "wasa_valid_until",
                    "WASA valid until",
                    day(record.wasa_valid_until),
                ),
            ],
        },
        {
            "title": "Functional testing",
            "rows": [
                _row(
                    "functional_certificate",
                    "Functional testing certificate",
                    "Download certificate" if certificate_url else "",
                    url=certificate_url,
                    file=True,
                ),
                _row(
                    "functional_report",
                    "Functional testing report",
                    "Download report" if report_url else "",
                    url=report_url,
                    file=True,
                ),
            ],
        },
    ]


def integrator_context(item) -> dict:
    """Organisation verification, prior approvals, callback health, open tickets."""
    from ohc_experience.support.models import Ticket  # noqa: PLC0415

    organisation = item.organisation
    product = item.product
    approvals = ReviewItem.objects.filter(
        organisation=organisation,
        item_type=ReviewItem.Type.EXIT_REQUEST,
        status=ReviewItem.Status.APPROVED,
    ).select_related("compliance", "product")
    credential = product.active_credential if product else None
    return {
        "context_organisation": organisation,
        "prior_approvals": list(approvals.exclude(pk=item.pk)),
        "context_credential": credential,
        "open_tickets": list(
            Ticket.objects.for_organisation(organisation).open_only()[:5],
        ),
    }


# Ageing buckets, as the prototype draws them; the last one is the attention
# threshold (settings.ABDM_REVIEW_ATTENTION_DAYS) and is drawn in red.
def ageing_buckets(attention_days: int) -> tuple[tuple[int, int | None, str], ...]:
    return (
        (0, 3, "0-3 days"),
        (4, attention_days, f"4-{attention_days} days"),
        (attention_days + 1, None, f"Over {attention_days} days"),
    )


DECISION_WEEKS = 8
DECISION_WINDOW_DAYS = 90
NEEDS_DECISION_LIMIT = 5


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def dashboard_stats(*, user=None) -> dict:
    """The numbers on the reviewer dashboard (design doc 5.9)."""
    from collections import Counter  # noqa: PLC0415
    from datetime import timedelta  # noqa: PLC0415

    from django.conf import settings  # noqa: PLC0415
    from django.utils import timezone  # noqa: PLC0415

    from .models import ReviewQuery  # noqa: PLC0415
    from .tracks import milestone_label  # noqa: PLC0415

    today = timezone.localdate()
    now = timezone.now()
    open_items = list(ReviewItem.objects.open().with_related())
    by_status = Counter(item.status for item in open_items)

    decided = ReviewItem.objects.decided().filter(
        decided_on__gte=today - timedelta(days=DECISION_WINDOW_DAYS),
    )
    durations = [
        (
            item.decided_on
            - item.submitted_on.astimezone(timezone.get_current_timezone()).date()
        ).days
        for item in decided
    ]

    month_start = today.replace(day=1)
    approved_month = Counter()
    for item in ReviewItem.objects.filter(
        status=ReviewItem.Status.APPROVED,
        decided_on__gte=month_start,
    ).select_related("compliance"):
        if item.is_exit_request and item.compliance_id:
            key = f"{item.compliance.track_code} {item.compliance.milestone_code}"
        else:
            key = str(item.get_item_type_display())
        approved_month[key] += 1

    needs_decision = sorted(open_items, key=lambda item: (item.submitted_on, item.pk))[
        :NEEDS_DECISION_LIMIT
    ]

    weeks = []
    this_monday = today - timedelta(days=today.weekday())
    for offset in range(DECISION_WEEKS - 1, -1, -1):
        start = this_monday - timedelta(weeks=offset)
        end = start + timedelta(days=7)
        window = ReviewItem.objects.filter(decided_on__gte=start, decided_on__lt=end)
        weeks.append(
            {
                "label": start.strftime("%-d %b"),
                "approved": window.filter(status=ReviewItem.Status.APPROVED).count(),
                "sent_back": window.filter(status=ReviewItem.Status.SENT_BACK).count(),
            },
        )
    peak = max((max(week["approved"], week["sent_back"]) for week in weeks), default=0)
    stack_peak = max(
        (week["approved"] + week["sent_back"] for week in weeks),
        default=0,
    )
    for week in weeks:
        week["approved_pct"] = round(week["approved"] / peak * 100) if peak else 0
        week["sent_back_pct"] = round(week["sent_back"] / peak * 100) if peak else 0
        # The stacked bar on the dashboard: both segments against the tallest
        # week's total, so the two can share one column.
        week["approved_stack_pct"] = (
            round(week["approved"] / stack_peak * 100) if stack_peak else 0
        )
        week["sent_back_stack_pct"] = (
            round(week["sent_back"] / stack_peak * 100) if stack_peak else 0
        )

    by_type = Counter(item.item_type for item in open_items)
    by_track = Counter(
        item.compliance.track_code for item in open_items if item.is_exit_request
    )
    ageing = []
    for low, high, label in ageing_buckets(settings.ABDM_REVIEW_ATTENTION_DAYS):
        count = sum(
            1
            for item in open_items
            if item.age_days >= low and (high is None or item.age_days <= high)
        )
        ageing.append({"label": label, "count": count, "over": high is None})
    load = Counter(
        item.assignee.display_name if item.assignee else "Unassigned"
        for item in open_items
    )
    reviewer_load = sorted(load.items(), key=lambda pair: (-pair[1], pair[0]))
    if "Unassigned" not in load:
        reviewer_load.append(("Unassigned", 0))

    return {
        "in_queue": len(open_items),
        "in_queue_by_status": [
            (str(ReviewItem.Status(status).label), by_status.get(status, 0))
            for status in (
                ReviewItem.Status.NEW,
                ReviewItem.Status.IN_REVIEW,
                ReviewItem.Status.QUERY_RAISED,
            )
        ],
        "median_days": _median(durations),
        "decided_count": len(durations),
        "queries_awaiting": ReviewQuery.objects.filter(
            status=ReviewQuery.Status.OPEN,
        ).count(),
        "approved_this_month": sum(approved_month.values()),
        "approved_by_milestone": sorted(approved_month.items()),
        "needs_decision": needs_decision,
        "attention_days": settings.ABDM_REVIEW_ATTENTION_DAYS,
        "weeks": weeks,
        "queue_by_type": [
            (str(ReviewItem.Type(item_type).label), by_type.get(item_type, 0))
            for item_type in ReviewItem.Type.values
        ],
        "queue_by_type_max": max(by_type.values(), default=0),
        "exit_by_track": sorted(by_track.items()),
        "exit_by_track_max": max(by_track.values(), default=0),
        "ageing": ageing,
        "ageing_max": max((bucket["count"] for bucket in ageing), default=0),
        "reviewer_load": reviewer_load,
        "approved_in_window": sum(week["approved"] for week in weeks),
        "sent_back_in_window": sum(week["sent_back"] for week in weeks),
        "over_attention": sum(1 for item in open_items if item.needs_attention),
        "my_open": sum(
            1 for item in open_items if user and item.assignee_id == user.pk
        ),
        "generated_at": now,
        "milestone_label": milestone_label,
    }
