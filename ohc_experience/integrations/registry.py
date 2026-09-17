"""Resolves each port to a real adapter or a local stand-in, per environment.

Domain code calls `get_idp_admin()` and cannot tell which it got, which is what
lets the whole portal run offline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

if TYPE_CHECKING:
    from ohc_experience.integrations.ports import ApiGateway
    from ohc_experience.integrations.ports import BridgeRegistry
    from ohc_experience.integrations.ports import IdpAdmin
    from ohc_experience.integrations.ports import NotificationGateway


def _build(port: str) -> object:
    """Not cached: adapters hold httpx clients and settings are overridden per test."""
    try:
        dotted_path = settings.INTEGRATION_PORTS[port]
    except KeyError as exc:
        msg = (
            f"No adapter configured for integration port {port!r}. "
            f"Set settings.INTEGRATION_PORTS[{port!r}]."
        )
        raise ImproperlyConfigured(msg) from exc

    try:
        adapter_class = import_string(dotted_path)
    except ImportError as exc:
        msg = f"Adapter {dotted_path!r} for port {port!r} could not be imported."
        raise ImproperlyConfigured(msg) from exc

    return adapter_class()


def get_idp_admin() -> IdpAdmin:
    return cast("IdpAdmin", _build("IDP"))


def get_api_gateway() -> ApiGateway:
    return cast("ApiGateway", _build("API_GATEWAY"))


def get_bridge_registry() -> BridgeRegistry:
    return cast("BridgeRegistry", _build("BRIDGE_REGISTRY"))


def get_notification_gateway() -> NotificationGateway:
    return cast("NotificationGateway", _build("NOTIFICATION"))
