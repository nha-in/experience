"""The wrapper adapter: one POST does what the DevPortal sequence does in four."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.test import override_settings

from ohc_experience.integrations.http import reset_breakers
from ohc_experience.integrations.naming import APP_NAME_TEMPLATE
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import GatewayAppSpec
from ohc_experience.integrations.registry import get_api_gateway
from ohc_experience.integrations.secret_ref import discard_secret
from ohc_experience.integrations.secret_ref import store_secret
from ohc_experience.integrations.tests.wso2_wrapper_stub import ADD_SUBSCRIPTIONS
from ohc_experience.integrations.tests.wso2_wrapper_stub import Wso2WrapperStubTransport
from ohc_experience.integrations.wso2.wrapper import Wso2WrapperApiGateway

API_IDS = ("api-0001-abha", "api-0002-hip")
SPEC = GatewayAppSpec(
    reference="SBX-2026-00001",
    name="Demo HMIS",
    api_ids=API_IDS,
)
APP_NAME = "sbx-SBX-2026-00001"

CONSUMER_KEY = "SBX_ABCDEF0123456789"
CONSUMER_SECRET = "keycloak-issued-secret"  # noqa: S105 - test value
SERVER_ERROR = 500

wrapper_settings = override_settings(
    WSO2_WRAPPER_BASE_URL="https://wso2-wrapper.test",
    WSO2_WRAPPER_PATH=ADD_SUBSCRIPTIONS,
    WSO2_READ_TIMEOUT_SECONDS=15.0,
)


@pytest.fixture(autouse=True)
def _isolated():
    reset_breakers()
    cache.clear()
    with wrapper_settings:
        yield
    reset_breakers()


@pytest.fixture
def transport():
    return Wso2WrapperStubTransport()


@pytest.fixture
def gateway(transport):
    built = Wso2WrapperApiGateway(transport=transport)
    yield built
    built.close()


@pytest.fixture
def secret_ref():
    return store_secret(CONSUMER_SECRET)


# Naming — the two steps that call nothing


def test_creating_an_application_calls_the_wrapper_not_at_all(gateway, transport):
    """The wrapper only acts once it has the secret, which arrives at map_keys."""
    created = gateway.create_application(SPEC)

    assert created.external_id == APP_NAME
    assert created.name == APP_NAME
    assert transport.calls == []


def test_the_name_matches_the_devportal_adapter(gateway):
    """Switching back must find the application this adapter had made."""
    assert gateway.create_application(SPEC).name == APP_NAME_TEMPLATE.format(
        reference=SPEC.reference,
    )


def test_subscribing_is_left_to_the_wrapper(gateway, transport):
    gateway.subscribe(APP_NAME, API_IDS)

    assert transport.calls == []


# The one call


def test_mapping_keys_posts_the_credentials_to_the_wrapper(
    gateway,
    transport,
    secret_ref,
):
    gateway.map_keys(APP_NAME, consumer_key=CONSUMER_KEY, secret_ref=secret_ref)

    assert transport.bodies == [
        {
            "applicationName": APP_NAME,
            "consumerKey": CONSUMER_KEY,
            "consumerSecret": CONSUMER_SECRET,
        },
    ]


def test_the_whole_step_is_a_single_post(gateway, transport, secret_ref):
    created = gateway.create_application(SPEC)
    gateway.subscribe(created.external_id, API_IDS)
    gateway.map_keys(
        created.external_id,
        consumer_key=CONSUMER_KEY,
        secret_ref=secret_ref,
    )

    assert [(c.method, c.url.path) for c in transport.calls] == [
        ("POST", ADD_SUBSCRIPTIONS),
    ]


def test_an_expired_secret_ref_is_retryable(gateway, transport, secret_ref):
    """The chain can re-mint from Keycloak, so this must not be terminal."""
    discard_secret(secret_ref)

    with pytest.raises(AdapterError) as raised:
        gateway.map_keys(APP_NAME, consumer_key=CONSUMER_KEY, secret_ref=secret_ref)

    assert raised.value.code == "SECRET_REF_EXPIRED"
    assert raised.value.retryable is True
    assert transport.calls == []


def test_a_wrapper_outage_is_retryable(gateway, transport, secret_ref):
    transport.forced_status = SERVER_ERROR

    with pytest.raises(AdapterError) as raised:
        gateway.map_keys(APP_NAME, consumer_key=CONSUMER_KEY, secret_ref=secret_ref)

    assert raised.value.system == ExternalSystem.WSO2
    assert raised.value.retryable is True


# Teardown — the gap this adapter cannot close


def test_unsubscribing_fails_loudly(gateway, transport):
    """Teardown marks the row FAILED and moves on; silence would hide a live app."""
    with pytest.raises(AdapterError) as raised:
        gateway.unsubscribe(APP_NAME, API_IDS)

    assert raised.value.code == "UNSUPPORTED"
    assert raised.value.retryable is False
    assert APP_NAME in raised.value.message
    assert transport.calls == []


# Selection


def test_the_registry_can_be_pointed_at_the_wrapper():
    ports = {
        "IDP": "ohc_experience.integrations.local.LocalIdpAdmin",
        "API_GATEWAY": "ohc_experience.integrations.wso2.wrapper.Wso2WrapperApiGateway",
        "BRIDGE_REGISTRY": "ohc_experience.integrations.local.LocalBridgeRegistry",
        "NOTIFICATION": "ohc_experience.integrations.local.LocalNotificationGateway",
    }
    with override_settings(INTEGRATION_PORTS=ports):
        gateway = get_api_gateway()

    assert isinstance(gateway, Wso2WrapperApiGateway)
    gateway.close()
