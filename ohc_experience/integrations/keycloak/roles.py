"""Which realm roles a product's client gets.

Names, never ids: a pinned realm role UUID ties the deployment to one Keycloak
instance and breaks whenever the realm is rebuilt. `KeycloakIdpAdmin` resolves
these at call time.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def role_names_for(kind: str) -> tuple[str, ...]:
    try:
        return tuple(settings.KEYCLOAK_ROLE_NAMES[kind])
    except KeyError as exc:
        msg = (
            f"No Keycloak role set configured for application type {kind!r}. "
            f"Add it to settings.KEYCLOAK_ROLE_NAMES."
        )
        raise ImproperlyConfigured(msg) from exc
