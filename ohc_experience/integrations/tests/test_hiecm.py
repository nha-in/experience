"""B5 acceptance criteria."""

from __future__ import annotations

import re
import uuid

import pytest
from django.conf import settings
from django.test import override_settings

from ohc_experience.integrations.hiecm.adapter import CM_ID_HEADER
from ohc_experience.integrations.hiecm.adapter import REQUEST_ID_HEADER
from ohc_experience.integrations.hiecm.adapter import TIMESTAMP_HEADER
from ohc_experience.integrations.hiecm.adapter import HiecmBridgeRegistry
from ohc_experience.integrations.http import reset_breakers
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import BridgeSpec
from ohc_experience.integrations.registry import get_bridge_registry
from ohc_experience.integrations.tests.hiecm_stub import API
from ohc_experience.integrations.tests.hiecm_stub import BRIDGE_PATH
from ohc_experience.integrations.tests.hiecm_stub import KEYCLOAK_TOKEN_PATH
from ohc_experience.integrations.tests.hiecm_stub import HiecmStubTransport

BRIDGE_ID = "SBX_5DB47E2B6E3CDFC1"
SPEC = BridgeSpec(
    bridge_id=BRIDGE_ID,
    name="Demo HMIS",
    url="https://demo-hmis.example/abdm",
    entity="Private",
)

SERVER_ERROR = 500
REGISTRATIONS = 2

hiecm_settings = override_settings(
    HIECM_BASE_URL="https://hiecm.test",
    HIECM_API_PATH=API,
    HIECM_CM_ID="sbx",
    KEYCLOAK_BASE_URL="https://keycloak.test",
    KEYCLOAK_USERNAME="provisioning-admin",
    KEYCLOAK_PASSWORD="admin-password",  # noqa: S106 - test value
)


@pytest.fixture(autouse=True)
def _isolated():
    reset_breakers()
    with hiecm_settings:
        yield
    reset_breakers()


@pytest.fixture
def transport():
    return HiecmStubTransport()


@pytest.fixture
def registry(transport):
    built = HiecmBridgeRegistry(transport=transport)
    yield built
    built.close()


# Create


def test_creating_a_bridge_registers_it_active(registry, transport):
    created = registry.create_bridge(SPEC)

    assert created.bridge_id == BRIDGE_ID
    assert transport.bridges[BRIDGE_ID]["active"] is True
    assert transport.bridges[BRIDGE_ID]["blocklisted"] is False
    assert transport.bridges[BRIDGE_ID]["url"] == SPEC.url
    assert transport.bridges[BRIDGE_ID]["entity"] == "Private"


def test_the_bridge_id_is_whatever_it_was_given(registry, transport):
    """Legacy set bridgeId to the derivable Keycloak client id; we derive nothing."""
    registry.create_bridge(SPEC)

    assert list(transport.bridges) == [BRIDGE_ID]


def test_re_registering_overwrites_rather_than_duplicates(registry, transport):
    """B7 re-runs chains; registration is a PUT precisely so that is safe."""
    registry.create_bridge(SPEC)
    registry.create_bridge(SPEC)

    assert len(transport.bridges) == 1
    assert transport.paths("PUT").count(BRIDGE_PATH) == REGISTRATIONS


def test_re_registering_a_deactivated_bridge_revives_it(registry, transport):
    transport.publish_bridge(BRIDGE_ID, active=False)

    registry.create_bridge(SPEC)

    assert registry.get_bridge_status(BRIDGE_ID).active is True


# Status


def test_status_reports_an_active_bridge(registry, transport):
    registry.create_bridge(SPEC)

    assert registry.get_bridge_status(BRIDGE_ID).active is True


def test_a_blocklisted_bridge_is_not_reported_active(registry, transport):
    """It moves no data; saying "active" would tell C7 the integrator is ready."""
    transport.publish_bridge(BRIDGE_ID, active=True, blocklisted=True)

    assert registry.get_bridge_status(BRIDGE_ID).active is False


def test_status_of_an_unknown_bridge_is_an_adapter_error(registry):
    with pytest.raises(AdapterError) as error:
        registry.get_bridge_status("nope")

    assert error.value.code == "HTTP_404"
    assert error.value.retryable is False


def test_a_response_without_a_bridge_object_is_reported_not_crashed(
    registry,
    transport,
):
    transport.failures[("GET", f"{API}/gateway/v3/bridge-services/{BRIDGE_ID}")] = 200

    with pytest.raises(AdapterError) as error:
        registry.get_bridge_status(BRIDGE_ID)

    assert error.value.code == "MALFORMED_RESPONSE"


# Deactivate — the call legacy never had


def test_deactivating_marks_the_bridge_inactive(registry, transport):
    registry.create_bridge(SPEC)

    registry.deactivate_bridge(BRIDGE_ID)

    assert transport.bridges[BRIDGE_ID]["active"] is False
    assert registry.get_bridge_status(BRIDGE_ID).active is False


def test_deactivating_an_unknown_bridge_succeeds(registry):
    registry.deactivate_bridge("never-existed")  # B8 reruns this


def test_deactivating_twice_succeeds(registry):
    registry.create_bridge(SPEC)

    registry.deactivate_bridge(BRIDGE_ID)
    registry.deactivate_bridge(BRIDGE_ID)


def test_deactivating_still_reports_a_real_failure(registry, transport):
    registry.create_bridge(SPEC)
    transport.failures[("PATCH", BRIDGE_PATH)] = SERVER_ERROR

    with pytest.raises(AdapterError) as error:
        registry.deactivate_bridge(BRIDGE_ID)

    assert error.value.retryable is True


# Gateway headers


def gateway_calls(transport):
    return [c for c in transport.calls if c.url.path.startswith(API)]


def test_every_gateway_call_carries_the_abdm_headers(registry, transport):
    registry.create_bridge(SPEC)

    for call in gateway_calls(transport):
        assert call.headers[CM_ID_HEADER] == "sbx"
        uuid.UUID(call.headers[REQUEST_ID_HEADER])  # raises if malformed
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z",
            call.headers[TIMESTAMP_HEADER],
        ), call.headers[TIMESTAMP_HEADER]


def test_each_call_gets_its_own_request_id(registry, transport):
    registry.create_bridge(SPEC)
    registry.get_bridge_status(BRIDGE_ID)

    request_ids = [c.headers[REQUEST_ID_HEADER] for c in gateway_calls(transport)]
    assert len(set(request_ids)) == len(request_ids)


# Config and transport policy


def test_the_keycloak_admin_token_is_fetched_once_and_reused(registry, transport):
    registry.create_bridge(SPEC)
    registry.get_bridge_status(BRIDGE_ID)

    assert transport.token_calls == 1


def test_the_token_comes_from_keycloak_as_legacy_did(registry, transport):
    """NHA issued no HIE-CM credentials; legacy sent its Keycloak admin token."""
    registry.create_bridge(SPEC)

    token_call = next(c for c in transport.calls if c.url.path == KEYCLOAK_TOKEN_PATH)
    assert token_call.url.host == "keycloak.test"


def test_bridge_calls_carry_the_bearer_token(registry, transport):
    registry.create_bridge(SPEC)

    bridge_calls = [c for c in transport.calls if c.url.path == BRIDGE_PATH]
    assert bridge_calls
    assert all(
        c.headers["Authorization"] == "Bearer keycloak-admin-token-1"
        for c in bridge_calls
    )


def test_no_external_rewrite_path_is_configured():
    """The `/sandbox/v3/v1/*` rewrite is IaC-owned; it leaked into legacy config."""
    configured = f"{settings.HIECM_BASE_URL}{settings.HIECM_API_PATH}"
    assert "/sandbox/" not in configured


def test_the_adapter_resolves_through_the_port_registry():
    dotted = "ohc_experience.integrations.hiecm.adapter.HiecmBridgeRegistry"
    with override_settings(INTEGRATION_PORTS={"BRIDGE_REGISTRY": dotted}):
        assert isinstance(get_bridge_registry(), HiecmBridgeRegistry)
