"""Which gateway APIs a product subscribes to.

Ids, as the legacy portal subscribed: they are what NHA's WSO2 holds, so the
config is tied to that instance.

There is no default. Guessing would subscribe to nothing or to the wrong thing,
both silently.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def api_ids_for(kind: str) -> tuple[str, ...]:
    ids = tuple(settings.WSO2_API_IDS.get(kind, ()))
    if not ids:
        msg = (
            f"No WSO2 API subscription set configured for application type "
            f"{kind!r}. Set settings.WSO2_API_IDS[{kind!r}] (or the "
            f"WSO2_SANDBOX_API_IDS env var) to the API ids."
        )
        raise ImproperlyConfigured(msg)
    return ids
