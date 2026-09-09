"""A transient hand-off for the one secret we deliberately never store.

`ApiGateway.map_keys` needs the Keycloak secret to hand to WSO2. Rather than
persist it, the provisioning chain parks it here under an opaque reference with
a short TTL, and the one caller that needs it reads it back within the same run.
Cache-backed, so it expires on its own even if a chain dies mid-way.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.core.cache import cache

from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ExternalSystem

_KEY = "secret_ref:{ref}"


def store_secret(value: str) -> str:
    ref = secrets.token_urlsafe(24)
    cache.set(_KEY.format(ref=ref), value, settings.SECRET_REF_TTL_SECONDS)
    return ref


def resolve_secret(ref: str, system: ExternalSystem) -> str:
    """Expired or unknown refs are retryable: the chain can re-run from Keycloak."""
    value = cache.get(_KEY.format(ref=ref))
    if not value:
        raise AdapterError(
            system,
            "SECRET_REF_EXPIRED",
            retryable=True,
            message="the referenced secret is no longer available",
        )
    return value


def has_secret(ref: str) -> bool:
    return bool(ref) and cache.get(_KEY.format(ref=ref)) is not None


def discard_secret(ref: str) -> None:
    cache.delete(_KEY.format(ref=ref))
