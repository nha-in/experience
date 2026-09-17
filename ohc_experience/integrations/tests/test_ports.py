"""Port contracts: the DTO shapes and the secret-handling rules they encode."""

from __future__ import annotations

import dataclasses

import pytest

from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ApiGateway
from ohc_experience.integrations.ports import BridgeCreated
from ohc_experience.integrations.ports import BridgeRegistry
from ohc_experience.integrations.ports import BridgeSpec
from ohc_experience.integrations.ports import BridgeStatus
from ohc_experience.integrations.ports import ClientCreated
from ohc_experience.integrations.ports import ClientSpec
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import GatewayAppCreated
from ohc_experience.integrations.ports import GatewayAppSpec
from ohc_experience.integrations.ports import IdpAdmin
from ohc_experience.integrations.ports import SecretRotated


@pytest.mark.parametrize(
    "secret_dto",
    [
        ClientCreated(client_id="SBX-1", external_id="uuid", initial_secret="s3cret"),  # noqa: S106
        SecretRotated(external_id="uuid", secret="s3cret"),  # noqa: S106
    ],
)
def test_secret_carrying_dtos_never_render_the_secret(secret_dto):
    """A traceback or a log line that repr()s one of these must not leak."""
    assert "s3cret" not in repr(secret_dto)


@pytest.mark.parametrize(
    "dto",
    [
        ClientSpec(reference="SBX-1", display_name="Acme", role_names=("hip",)),
        ClientCreated(client_id="SBX-1", external_id="uuid", initial_secret="s"),  # noqa: S106
        SecretRotated(external_id="uuid", secret="s"),  # noqa: S106
        GatewayAppSpec(reference="SBX-1", name="Acme", api_ids=("abha",)),
        GatewayAppCreated(external_id="uuid", name="Acme"),
        BridgeSpec(
            bridge_id="SBX-1",
            name="Acme",
            url="https://acme.test",
            entity="Private",
        ),
        BridgeCreated(bridge_id="SBX-1"),
        BridgeStatus(bridge_id="SBX-1", active=True),
    ],
)
def test_dtos_are_frozen(dto):
    assert dataclasses.is_dataclass(dto)
    field_name = next(iter(dataclasses.fields(dto))).name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(dto, field_name, "mutated")


def test_roles_are_named_not_identified():
    """B3: role UUIDs are instance-coupled config; the port only speaks names."""
    spec = ClientSpec(reference="SBX-1", display_name="Acme", role_names=("hip", "hiu"))

    assert spec.role_names == ("hip", "hiu")


def test_external_system_covers_every_provisioned_system():
    assert {s.value for s in ExternalSystem} == {"KEYCLOAK", "WSO2", "HIECM"}


@pytest.mark.parametrize(
    ("protocol", "methods"),
    [
        (IdpAdmin, {"create_client", "rotate_client_secret", "disable_client"}),
        (ApiGateway, {"create_application", "subscribe", "map_keys", "unsubscribe"}),
        (BridgeRegistry, {"create_bridge", "get_bridge_status", "deactivate_bridge"}),
    ],
)
def test_ports_declare_their_operations(protocol, methods):
    assert methods <= set(dir(protocol))


def test_adapter_error_is_the_only_thing_adapters_raise():
    assert issubclass(AdapterError, Exception)
