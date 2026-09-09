"""Which gateway APIs a product subscribes to.

Names, never ids, for the same reason as the Keycloak realm roles: an `apiId`
pins the config to one WSO2 deployment and tells a reader nothing.

There is no default. NHA has not published the sandbox API names, and guessing
would subscribe to nothing or to the wrong thing, both silently.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def api_names_for(kind: str) -> tuple[str, ...]:
    names = tuple(settings.WSO2_API_NAMES.get(kind, ()))
    if not names:
        msg = (
            f"No WSO2 API subscription set configured for application type "
            f"{kind!r}. Set settings.WSO2_API_NAMES[{kind!r}] (or the "
            f"WSO2_SANDBOX_API_NAMES env var) to the published API names."
        )
        raise ImproperlyConfigured(msg)
    return names
