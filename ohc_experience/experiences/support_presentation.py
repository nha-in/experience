"""Read-only filtering and counts for the support inbox."""

from django.db.models import Count
from django.db.models import Q


def support_inbox(tickets, params, form, *, reviewer=False):
    total = tickets.count()
    for key in ("category", "priority"):
        value = params.get(key, "")
        if value in dict(form.fields[key].choices):
            tickets = tickets.filter(**{key: value})
    search = params.get("q", "").strip()
    if search:
        tickets = tickets.filter(
            Q(subject__icontains=search) | Q(reference__icontains=search),
        )
    statuses = [
        ("", "All tickets"),
        ("open", "Open"),
        ("awaiting_vendor", "Awaiting vendor" if reviewer else "Awaiting your reply"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
    ]
    counts = dict(
        tickets.order_by().values_list("status").annotate(count=Count("pk")),
    )
    tabs = [
        {
            "value": value,
            "label": label,
            "count": counts.get(value, 0) if value else sum(counts.values()),
        }
        for value, label in statuses
    ]
    if params.get("status") in {"open", "awaiting_vendor", "resolved", "closed"}:
        tickets = tickets.filter(status=params["status"])
    return {
        "ticket_total": total,
        "ticket_statuses": statuses,
        "status_tabs": tabs,
        "tickets": tickets.select_related("experience_context__product").order_by(
            "-updated_at",
        ),
    }
