"""Read-only filtering and counts for the support inbox."""

from django.contrib.auth import get_user_model
from django.db.models import Count
from django.db.models import Q
from django.db.models.functions import Lower


def assignee_choices(tickets, user):
    """What the Assignee filter offers: each person who holds one of `tickets`.

    Someone with nothing assigned is left out, since choosing them would show
    nothing, and so is the viewer, who has Assigned to me.
    """
    people = (
        get_user_model()
        .objects.filter(pk__in=tickets.values("assignee"))
        .exclude(pk=user.pk)
        .order_by(Lower("name"), Lower("email"))
    )
    return [
        ("me", "Assigned to me"),
        ("unassigned", "Unassigned"),
        *((str(person.pk), person.display_name) for person in people),
    ]


def support_inbox(tickets, params, form, *, user, reviewer=False):
    total = tickets.count()
    # Only staff are assigned tickets, so an integrator has no such filter.
    assignees = assignee_choices(tickets, user) if reviewer else []
    filters = {}
    for key in ("category", "priority"):
        value = params.get(key, "")
        filters[key] = value if value in dict(form.fields[key].choices) else ""
        if filters[key]:
            tickets = tickets.filter(**{key: filters[key]})
    assignee = params.get("assignee", "")
    filters["assignee"] = assignee if assignee in dict(assignees) else ""
    if filters["assignee"] == "me":
        tickets = tickets.filter(assignee=user)
    elif filters["assignee"] == "unassigned":
        tickets = tickets.filter(assignee=None)
    elif filters["assignee"]:
        tickets = tickets.filter(assignee_id=filters["assignee"])
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
    has_search_filters = any(
        filters[key] for key in ("category", "priority", "assignee", "q")
    )
    return {
        "ticket_total": total,
        "ticket_statuses": statuses,
        "status_tabs": tabs,
        "ticket_filters": filters,
        "assignee_choices": assignees,
        "has_ticket_filters": has_search_filters
        or (status_was_selected and status_is_valid),
        "has_ticket_search_filters": has_search_filters,
        "tickets": tickets.select_related("product", "assignee").order_by(
            "-updated_at",
        ),
    }
