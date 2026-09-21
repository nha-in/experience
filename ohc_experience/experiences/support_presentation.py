"""Read-only filtering and counts for the support inbox."""

from django.db.models import Count
from django.db.models import Q

#: The category filter's value for "Others". Its own code is blank, which the
#: filter reads as every category.
OTHERS = "others"


def support_inbox(tickets, params, form, *, reviewer=False):
    total = tickets.count()
    category_choices = [
        (code or OTHERS, label) for code, label in form.fields["category"].choices
    ]
    filters = {}
    for key, choices in (
        ("category", category_choices),
        ("priority", form.fields["priority"].choices),
    ):
        value = params.get(key, "")
        filters[key] = value if value in dict(choices) else ""
        if filters[key]:
            tickets = tickets.filter(**{key: "" if value == OTHERS else value})
    search = params.get("q", "").strip()
    filters["q"] = search
    if search:
        tickets = tickets.filter(
            Q(subject__icontains=search) | Q(reference__icontains=search),
        )
    statuses = [
        ("", "All tickets"),
        ("open", "Needs a reply" if reviewer else "With NHA team"),
        (
            "awaiting_integrator",
            "Awaiting integrator" if reviewer else "Awaiting your reply",
        ),
        ("closed", "Resolved"),
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
        "category_choices": category_choices,
        "ticket_statuses": statuses,
        "status_tabs": tabs,
        "ticket_filters": filters,
        "has_ticket_filters": any(filters[key] for key in ("category", "priority", "q"))
        or (status_was_selected and status_is_valid),
        "has_ticket_search_filters": any(
            filters[key] for key in ("category", "priority", "q")
        ),
        "tickets": tickets.select_related("product", "product__workspace").order_by(
            "-updated_at",
        ),
    }
