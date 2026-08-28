from __future__ import annotations

from django.db.models import Count
from django.db.models import Q

from .models import ApplicationInstance
from .models import QueryStatus

PENDING_QUERY_STATUSES = (
    QueryStatus.AWAITING_APPLICANT,
    QueryStatus.AWAITING_REVIEWER,
)


def applications_for_user(user):
    return ApplicationInstance.objects.visible_to(user).with_workspace_data()


def filter_applications(queryset, selected: dict[str, str]):
    queryset = queryset.annotate(
        open_query_count=Count(
            "query_threads",
            filter=Q(query_threads__status__in=PENDING_QUERY_STATUSES),
            distinct=True,
        ),
    )
    if status := selected.get("status"):
        queryset = queryset.filter(status=status)
    if application_type := selected.get("application_type"):
        queryset = queryset.filter(application_type=application_type)
    if query := selected.get("q"):
        queryset = queryset.filter(
            Q(reference__icontains=query)
            | Q(title__icontains=query)
            | Q(organisation__name__icontains=query)
            | Q(metadata__product_name__icontains=query),
        )
    if selected.get("query_state") == "pending":
        queryset = queryset.filter(open_query_count__gt=0)
    elif selected.get("query_state") == "clear":
        queryset = queryset.filter(open_query_count=0)
    return queryset
