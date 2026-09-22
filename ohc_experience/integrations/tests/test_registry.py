"""The settings toggle that lets a port resolve to a real adapter or a local one."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from ohc_experience.integrations import registry
from ohc_experience.integrations.local import LocalApiGateway
from ohc_experience.integrations.local import LocalBridgeRegistry
from ohc_experience.integrations.local import LocalIdpAdmin
from ohc_experience.integrations.local import LocalNotificationGateway
from ohc_experience.integrations.ports import ClientCreated
from ohc_experience.integrations.ports import ClientSpec
from ohc_experience.integrations.ports import SecretRotated


class StubIdpAdmin:
    def create_client(self, spec: ClientSpec) -> ClientCreated:
        return ClientCreated(
            client_id=spec.reference,
            external_id="uuid",
            initial_secret="s",  # noqa: S106
        )

    def rotate_client_secret(self, external_id: str) -> SecretRotated:
        return SecretRotated(external_id=external_id, secret="s")  # noqa: S106

    def disable_client(self, external_id: str) -> None:
        return None

    def enable_client(self, external_id: str) -> None:
        return None


STUB = "ohc_experience.integrations.tests.test_registry.StubIdpAdmin"


def test_port_resolves_to_the_configured_adapter(settings):
    settings.INTEGRATION_PORTS = {"IDP": STUB}

    assert isinstance(registry.get_idp_admin(), StubIdpAdmin)


def test_unconfigured_port_fails_loudly(settings):
    settings.INTEGRATION_PORTS = {}

    with pytest.raises(ImproperlyConfigured, match="No adapter configured"):
        registry.get_idp_admin()


def test_unimportable_adapter_fails_loudly(settings):
    settings.INTEGRATION_PORTS = {"IDP": "ohc_experience.integrations.nope.Missing"}

    with pytest.raises(ImproperlyConfigured, match="could not be imported"):
        registry.get_idp_admin()


@pytest.mark.parametrize(
    ("accessor", "port"),
    [
        (registry.get_idp_admin, "IDP"),
        (registry.get_api_gateway, "API_GATEWAY"),
        (registry.get_bridge_registry, "BRIDGE_REGISTRY"),
        (registry.get_notification_gateway, "NOTIFICATION"),
    ],
)
def test_every_port_has_an_accessor(settings, accessor, port):
    settings.INTEGRATION_PORTS = {port: STUB}

    assert isinstance(accessor(), StubIdpAdmin)


@pytest.mark.parametrize(
    ("accessor", "expected"),
    [
        (registry.get_idp_admin, LocalIdpAdmin),
        (registry.get_api_gateway, LocalApiGateway),
        (registry.get_bridge_registry, LocalBridgeRegistry),
        (registry.get_notification_gateway, LocalNotificationGateway),
    ],
)
def test_shipped_defaults_resolve_to_the_local_adapters(accessor, expected):
    """No env vars set means offline dev works out of the box."""
    assert isinstance(accessor(), expected)
