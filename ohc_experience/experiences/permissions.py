from django.core.exceptions import PermissionDenied
from django.db.models import BigIntegerField
from django.db.models import Q
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast

from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.users.permissions import is_nha_team

from .models import AccessGrant
from .models import AuditEvent
from .models import FormSubmission
from .models import Product
from .models import ReviewItem
from .registry import get_program
from .registry import registry


def reviewer(user):
    """Staff identity only, not authorization for an operation."""
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.is_superuser or is_nha_team(user)),
    )


def can_integrate(user, organisation):
    return bool(
        user.is_authenticated
        and user.is_active
        and not reviewer(user)
        and organisation.memberships.filter(
            user=user,
            role__in=[Role.OWNER, Role.ADMIN, Role.DEVELOPER],
        ).exists(),
    )


def require_integrator(user, organisation):
    if not can_integrate(user, organisation):
        msg = "Only this organisation's integrators can change its submissions."
        raise PermissionDenied(msg)


def grants(user, area, action="read", program=None):
    if action not in {"read", "write", "approve"}:
        msg = "Unknown permission action."
        raise ValueError(msg)
    query = AccessGrant.objects.none()
    if reviewer(user):
        query = AccessGrant.objects.filter(user=user, area=area, can_read=True)
        query = query.filter(**{f"can_{action}": True})
    if program:
        query = query.filter(program=program)
    return query


def has_area(user, area, action="read", program=None):
    """A grant in any running program opens the area.

    Each screen behind it lists every program the grants reach, so gating on
    the portal's own program would shut a reviewer out of work they hold.
    """
    programs = [program] if program else [item.key for item in registry.programs()]
    return reviewer(user) and (
        user.is_superuser
        or grants(user, area, action).filter(program__in=programs).exists()
    )


def has_access(user, area, category="", action="read", program=None):
    return reviewer(user) and (
        user.is_superuser
        or grants(user, area, action, program or get_program().key)
        .filter(category__in=["*", category])
        .exists()
    )


def require_area(user, area):
    if reviewer(user) and not has_area(user, area):
        msg = f"You do not have {area} access."
        raise PermissionDenied(msg)


def allowed_tracks(user, program=None):
    program = program or get_program()
    return [
        track
        for track in program.tracks
        if not reviewer(user)
        or has_access(user, "review", track.code, program=program.key)
    ]


def review_scope(program, category):
    general = Q(
        application__isnull=True,
        form__metadata__program=program.key,
    ) | Q(
        product__workspace__experience_type=program.key,
        kind=ReviewItem.Kind.PRODUCT,
    )
    product_program = Q(product__workspace__experience_type=program.key)
    if program.applications.certification:
        general |= product_program & Q(
            kind=ReviewItem.Kind.APPLICATION,
            application__application_type=program.applications.certification.key,
        )
    if category == "*":
        return general | product_program
    if not category:
        return general
    track = program.track_map().get(category)
    if track is None:
        return Q(pk__in=[])
    return product_program & track_items(program, track)


def track_items(program, track):
    """A track's chosen milestones, and the related ones they build on or beside."""
    prerequisites = track.related_milestones(program.milestones)
    query = Q(pk__in=[])
    for key in track.keys:
        query |= Q(
            application__milestone__key__in=[*prerequisites, key],
            product__workspace__applied_milestones__contains=[f"{track.code}:{key}"],
        )
    return query


def visible_reviews(user, action="read"):
    query = ReviewItem.objects.all()
    if not user.is_authenticated or not user.is_active:
        return query.none()
    if not reviewer(user):
        if action != "read":
            return query.none()
        return query.filter(organisation__memberships__user=user).distinct()
    if user.is_superuser:
        return query
    scope = Q(pk__in=[])
    programs = {program.key: program for program in registry.programs()}
    for grant in grants(user, "review", action):
        if grant.program in programs:
            scope |= review_scope(programs[grant.program], grant.category)
    return query.filter(scope)


def visible_products(user, action="read"):
    """Products represented by at least one review the reviewer may access."""
    query = Product.objects.all()
    if not reviewer(user):
        return query.none()
    return query.filter(
        pk__in=visible_reviews(user, action).values("product_id"),
    )


def visible_organisations(user, action="read"):
    """Organisations represented by the reviewer's permission-scoped requests."""
    query = Organisation.objects.all()
    if not reviewer(user):
        return query.none()
    return query.filter(
        pk__in=visible_reviews(user, action).values("organisation_id"),
    )


def visible_submissions(user):
    query = FormSubmission.objects.all()
    if not user.is_authenticated or not user.is_active:
        return query.none()
    if not reviewer(user):
        return query.filter(form__organisation__memberships__user=user).distinct()
    if user.is_superuser:
        return query
    items = visible_reviews(user)
    historical_pins = AuditEvent.objects.filter(
        item__in=items,
        action__in=["Reused product evidence", "UHI participation form upgraded"],
    ).annotate(
        submission_pk=Cast(
            KeyTextTransform("submission_id", "detail"),
            BigIntegerField(),
        ),
    )
    # Reuse grants access to current and historical pins, not the source's history.
    return query.filter(
        Q(origin_application__in=items.values("application_id"))
        | Q(pk__in=items.values("selected_submission_id"))
        | Q(pk__in=historical_pins.values("submission_pk"))
        | Q(
            origin_application__isnull=True,
            form__in=items.filter(application__isnull=True).values("form_id"),
        ),
    )


def visible_tickets(user, action="read"):
    from ohc_experience.support.models import Ticket  # noqa: PLC0415

    query = Ticket.objects.all()
    if not user.is_authenticated or not user.is_active:
        return query.none()
    if not reviewer(user):
        if action != "read":
            return query.none()
        return query.filter(organisation__memberships__user=user).distinct()
    if user.is_superuser:
        return query
    scope = Q(pk__in=[])
    for grant in grants(user, "support", action):
        program = Q(
            product__workspace__experience_type=grant.program,
        )
        if grant.category == "*":
            scope |= program
        else:
            scope |= program & Q(category=grant.category)
    return query.filter(scope)


def visible_events(user, action="read"):
    from ohc_experience.events_and_activities.models import Event  # noqa: PLC0415

    query = Event.objects.all()
    if not user.is_authenticated or not user.is_active:
        return query.none()
    if not reviewer(user):
        return query.published() if action == "read" else query.none()
    if user.is_superuser:
        return query
    scope = Q(pk__in=[])
    for grant in grants(user, "events", action):
        condition = Q(program=grant.program)
        if grant.category != "*":
            condition &= Q(category=grant.category)
        scope |= condition
    return query.filter(scope)


def eligible_reviewer(user, item):
    return (
        visible_reviews(user, "write").filter(pk=item.pk).exists()
        or visible_reviews(user, "approve").filter(pk=item.pk).exists()
    )


def can_review(user, item, action):
    """Category grants alone decide; the assignee only labels and filters work."""
    return reviewer(user) and visible_reviews(user, action).filter(pk=item.pk).exists()


def can_decide(user, item):
    return can_review(user, item, "write") or can_review(user, item, "approve")


def available_review_actions(user, item):
    """What the actor's grants allow. Pending prerequisites narrow it further."""
    if not item.pending:
        return []
    if item.definition.auto_approve:
        # Only an override approval is offered: it is otherwise recorded on
        # its own, and reject/query never apply to it.
        return ["approve"] if can_review(user, item, "approve") else []
    actions = []
    if can_review(user, item, "approve"):
        actions.extend(["approve", "reject"])
    if can_review(user, item, "write"):
        actions.append("query")
    return actions


def require_decider(user, item, action="approve"):
    if not can_review(user, item, action):
        msg = "This action requires the matching category permission."
        raise PermissionDenied(msg)


def can_reply_ticket(user, ticket):
    if reviewer(user):
        return visible_tickets(user, "write").filter(pk=ticket.pk).exists()
    return can_integrate(user, ticket.organisation)


def can_close_ticket(user, ticket):
    """Reviewers close what they may approve; integrators close their own tickets."""
    if reviewer(user):
        return visible_tickets(user, "approve").filter(pk=ticket.pk).exists()
    return can_integrate(user, ticket.organisation)


def staff_home(user):
    for area, route in (
        ("review", "experiences:assess-dashboard"),
        ("support", "experiences:support"),
        ("events", "experiences:events"),
    ):
        if has_area(user, area):
            return route
    msg = "Your account has no portal permissions. Contact an administrator."
    raise PermissionDenied(msg)
