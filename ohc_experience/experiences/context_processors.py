from ohc_experience.events.models import Event
from ohc_experience.organisations.selectors import get_membership_for
from ohc_experience.support.models import Ticket

from . import permissions
from .models import ProductWorkspace
from .models import ReviewItem
from .registry import get_program


def workspaces_for(user):
    """Return only products visible to this account, across shell and portal."""
    query = ProductWorkspace.objects.select_related("product__organisation")
    if not permissions.reviewer(user):
        query = query.filter(product__organisation__memberships__user=user)
    return query.order_by("product__name")


def selected_workspace(request):
    if not request.user.is_authenticated:
        return None
    reference = request.GET.get(
        "product",
        request.session.get("experience_product", ""),
    )
    query = workspaces_for(request.user)
    return query.filter(reference=reference).first() or query.first()


def navigation_context(request, workspace=None):
    """Presentation data only; workflow state remains owned by the engine."""
    is_reviewer = permissions.reviewer(request.user)
    membership = get_membership_for(request.user)
    organisation = (
        workspace.product.organisation
        if workspace
        else (membership.organisation if membership else None)
    )
    tracks = []
    if workspace:
        approved = set(
            workspace.product.milestones.filter(
                enabled=True,
                application__review_item__status="approved",
            ).values_list("key", flat=True),
        )
        for track in workspace.definition.tracks:
            keys = [
                key
                for key in track.keys
                if f"{track.code}:{key}" in workspace.applied_milestones
            ]
            if keys:
                tracks.append(
                    {
                        "definition": track,
                        "approved": sum(key in approved for key in keys),
                        "count": len(keys),
                    },
                )
    tickets = Ticket.objects.filter(status__in=["open", "awaiting_vendor"])
    if not is_reviewer:
        tickets = tickets.filter(organisation=organisation)
        if workspace:
            tickets = tickets.filter(experience_context__product=workspace.product)
    return {
        "workspace": workspace,
        "workspaces": workspaces_for(request.user),
        "reviewer": is_reviewer,
        "organisation": organisation,
        "can_integrate": bool(
            organisation and permissions.can_integrate(request.user, organisation),
        ),
        "nav_tracks": tracks,
        "nav_event_count": Event.objects.upcoming().count(),
        "nav_ticket_count": tickets.count(),
        "query_count": ReviewItem.objects.filter(
            organisation=organisation,
            status="query_raised",
        ).count()
        if organisation
        else 0,
    }


def experience_program(request):
    context = {"experience_program": get_program()}
    if request.user.is_authenticated:
        if hasattr(request, "experience_navigation"):
            context.update(request.experience_navigation)
        else:
            workspace = (
                None
                if permissions.reviewer(request.user)
                else selected_workspace(request)
            )
            context.update(navigation_context(request, workspace))
        if context.get("workspace"):
            context["experience_program"] = context["workspace"].definition
    return context
