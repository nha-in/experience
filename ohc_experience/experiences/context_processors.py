from django.db.models import Q

from ohc_experience.organisations.selectors import get_membership_for

from . import permissions
from .models import Product
from .registry import get_program


def products_for(user):
    """Return only products visible to this account, across shell and portal."""
    query = Product.objects.select_related("organisation")
    if not permissions.reviewer(user):
        query = query.filter(organisation__memberships__user=user)
    elif not user.is_superuser:
        query = query.filter(pk__in=permissions.visible_products(user))
    # The key breaks ties between same-named products, or pages can repeat one
    # and skip another.
    return query.order_by("name", "pk")


def selected_product(request):
    if not request.user.is_authenticated:
        return None
    reference = request.GET.get(
        "product",
        request.session.get("experience_product", ""),
    )
    query = products_for(request.user)
    return query.filter(reference=reference).first() or query.first()


def product_scope(product):
    """The selected product's reviews, and organisation-level ones with no product."""
    scope = Q(product__isnull=True)
    if product:
        scope |= Q(product=product)
    return scope


def navigation_context(request, product=None):
    """Presentation data only; workflow state remains owned by the engine."""
    is_reviewer = permissions.reviewer(request.user)
    membership = get_membership_for(request.user)
    organisation = (
        product.organisation
        if product
        else (membership.organisation if membership else None)
    )
    tracks = []
    if product:
        enabled = set(
            product.milestones.filter(enabled=True).values_list(
                "key",
                flat=True,
            ),
        )
        approved = set(
            product.milestones.filter(
                enabled=True,
                application__review_item__status="approved",
            ).values_list("key", flat=True),
        )
        for track in permissions.allowed_tracks(request.user, product.definition):
            # A related milestone the product never applied for has no request of
            # its own, so counting it would hold the track short of its total.
            keys = [
                key
                for key in product.definition.applied_keys(
                    track,
                    product.applied_milestones,
                )
                if key in enabled
            ]
            if keys:
                tracks.append(
                    {
                        "definition": track,
                        "approved": sum(key in approved for key in keys),
                        "count": len(keys),
                    },
                )
    tickets = permissions.visible_tickets(request.user).filter(
        status__in=["open", "awaiting_integrator"],
    )
    if not is_reviewer:
        tickets = tickets.filter(organisation=organisation)
        if product:
            tickets = tickets.filter(product=product)
    return {
        "product": product,
        "selected_product": product,
        "products": products_for(request.user),
        "reviewer": is_reviewer,
        "can_read_general": not is_reviewer
        or permissions.has_access(
            request.user,
            "review",
            program=product.definition.key if product else None,
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
            product_scope(product),
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
            product = (
                None
                if permissions.reviewer(request.user)
                else selected_product(request)
            )
            context.update(navigation_context(request, product))
        if context.get("product"):
            context["experience_program"] = context["product"].definition
    return context
