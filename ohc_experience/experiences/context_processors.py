from django.db.models import Q

from ohc_experience.organisations.selectors import get_membership_for

from . import permissions
from .models import ProductWorkspace
from .registry import get_program


def workspaces_for(user):
    """Return only products visible to this account, across shell and portal."""
    query = ProductWorkspace.objects.select_related("product__organisation")
    if not permissions.reviewer(user):
        query = query.filter(product__organisation__memberships__user=user)
    elif not user.is_superuser:
        query = query.filter(
            Q(product__in=permissions.visible_reviews(user).values("product_id"))
            | Q(
                product__in=permissions.visible_tickets(user).values(
                    "product_id",
                ),
            ),
        )
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


def product_scope(workspace):
    """The selected product's reviews, and organisation-level ones with no product."""
    scope = Q(product__isnull=True)
    if workspace:
        scope |= Q(product=workspace.product)
    return scope


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
        for track in permissions.allowed_tracks(request.user, workspace.definition):
            keys = workspace.definition.applied_keys(
                track,
                workspace.applied_milestones,
            )
            if keys:
                tracks.append(
                    {
                        "definition": track,
                        "approved": sum(key in approved for key in keys),
                        "count": len(keys),
                    },
                )
    tickets = permissions.visible_tickets(request.user).filter(
        status__in=["open", "awaiting_vendor"],
    )
    if not is_reviewer:
        tickets = tickets.filter(organisation=organisation)
        if workspace:
            tickets = tickets.filter(product=workspace.product)
    return {
        "workspace": workspace,
        "workspaces": workspaces_for(request.user),
        "reviewer": is_reviewer,
        "can_read_general": not is_reviewer
        or permissions.has_access(
            request.user,
            "review",
            program=workspace.definition.key if workspace else None,
        ),
        "can_read_reviews": permissions.has_area(request.user, "review"),
        "can_read_support": not is_reviewer
        or permissions.has_area(request.user, "support"),
        "can_read_events": not is_reviewer
        or permissions.has_area(request.user, "events"),
        "can_create_events": permissions.has_area(request.user, "events", "write"),
        "can_manage_events": permissions.has_area(request.user, "events"),
        "organisation": organisation,
        "can_integrate": bool(
            organisation and permissions.can_integrate(request.user, organisation),
        ),
        "nav_tracks": tracks,
        "nav_event_count": permissions.visible_events(request.user).upcoming().count(),
        "nav_ticket_count": tickets.count(),
        "query_count": permissions.visible_reviews(request.user)
        .filter(
            product_scope(workspace),
            organisation=organisation,
            status="query_raised",
        )
        .count()
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
