from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import ApplicationAccess
from .registry import registry

if TYPE_CHECKING:
    from .definitions import RoleDefinition


@dataclass(frozen=True)
class EffectiveAccess:
    role: RoleDefinition | None
    permissions: frozenset[str]
    grant: ApplicationAccess | None

    def allows(self, permission: str) -> bool:
        return permission in self.permissions


def get_effective_access(application, user) -> EffectiveAccess:
    definition = registry.get(application.application_type)
    if not getattr(user, "is_authenticated", False):
        return EffectiveAccess(None, frozenset(), None)
    if getattr(user, "is_superuser", False):
        return EffectiveAccess(
            None,
            frozenset(item.key for item in definition.permissions),
            None,
        )

    grant = next(
        (item for item in application.access_grants.all() if item.user_id == user.pk),
        None,
    )
    if grant is None:
        grant = ApplicationAccess.objects.filter(
            application=application,
            user=user,
        ).first()

    role = definition.get_role(grant.role_key) if grant else None
    # A damaged or manually edited access row must never strand the creator.
    if role is None and application.created_by_id == user.pk:
        role = definition.get_role(definition.owner_role_key)

    known = {item.key for item in definition.permissions}
    permissions = set(role.permissions if role else ())
    if grant and role:
        audience_permissions = set().union(
            *(
                candidate.permissions
                for candidate in definition.roles
                if candidate.audience == role.audience
            ),
        )
        permissions.update(set(grant.direct_permissions) & audience_permissions)
    return EffectiveAccess(role, frozenset(permissions & known), grant)


def has_application_permission(application, user, permission: str) -> bool:
    return get_effective_access(application, user).allows(permission)
