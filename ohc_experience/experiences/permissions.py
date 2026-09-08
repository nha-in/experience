from django.core.exceptions import PermissionDenied

from ohc_experience.organisations.models import Role
from ohc_experience.users.permissions import is_ohc_team


def reviewer(user):
    return bool(
        user.is_authenticated
        and user.is_active
        and (user.is_superuser or is_ohc_team(user)),
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


def can_decide(user, item):
    return reviewer(user) and (user.is_superuser or item.assignee_id == user.pk)


def require_decider(user, item):
    if not can_decide(user, item):
        msg = "An administrator must assign this review to you first."
        raise PermissionDenied(msg)
