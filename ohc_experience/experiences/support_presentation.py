"""Read-only filtering and counts for the support inbox."""

from django.db.models import Count
from django.db.models import Q


def support_inbox(tickets, params, form, *, reviewer=False):
    total = tickets.count()
    filters = {}
    for key in ("category", "priority"):
        value = params.get(key, "")
        filters[key] = value if value in dict(form.fields[key].choices) else ""
        if filters[key]:
            tickets = tickets.filter(**{key: filters[key]})
    search = params.get("q", "").strip()
    filters["q"] = search
    if search:
        tickets = tickets.filter(
            Q(subject__icontains=search) | Q(reference__icontains=search),
        )
    statuses = [
        ("", "All tickets"),
        ("open", "Open"),
        (
            "awaiting_integrator",
            "Awaiting integrator" if reviewer else "Awaiting your reply",
        ),
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
    status_was_selected = "status" in params
    status = params.get("status", "open")
    status_is_valid = status in dict(statuses)
    filters["status"] = status if status_is_valid else ""
    if filters["status"]:
        tickets = tickets.filter(status=filters["status"])
    return {
        "ticket_total": total,
        "ticket_statuses": statuses,
        "status_tabs": tabs,
        "ticket_filters": filters,
        "has_ticket_filters": any(filters[key] for key in ("category", "priority", "q"))
        or (status_was_selected and status_is_valid),
        "has_ticket_search_filters": any(
            filters[key] for key in ("category", "priority", "q")
        ),
        "tickets": tickets.select_related("product").order_by(
            "-updated_at",
        ),
    }
