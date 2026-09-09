"""B4 acceptance criteria."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from ohc_experience.integrations.http import reset_breakers
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import GatewayAppSpec
from ohc_experience.integrations.registry import get_api_gateway
from ohc_experience.integrations.secret_ref import resolve_secret
from ohc_experience.integrations.secret_ref import store_secret
from ohc_experience.integrations.tests.wso2_stub import DEVPORTAL
from ohc_experience.integrations.tests.wso2_stub import Wso2StubTransport
from ohc_experience.integrations.wso2.adapter import Wso2ApiGateway
from ohc_experience.integrations.wso2.apis import api_names_for

API_NAMES = ("abha-v3", "hip-v3")
SPEC = GatewayAppSpec(
    reference="SBX-2026-00001",
    name="Demo HMIS",
    api_names=API_NAMES,
)
APP_NAME = "sbx-SBX-2026-00001"

SERVER_ERROR = 500
CONSUMER_KEY = "SBX_ABCDEF0123456789"

wso2_settings = override_settings(
    WSO2_BASE_URL="https://wso2.test",
    WSO2_DEVPORTAL_PATH=DEVPORTAL,
    WSO2_TOKEN_PATH="/oauth2/token",  # noqa: S106 - a URL path, not a token
    WSO2_CLIENT_ID="portal",
    WSO2_CLIENT_SECRET="portal-secret",  # noqa: S106 - test value
    WSO2_USERNAME="portal-admin",
    WSO2_PASSWORD="portal-password",  # noqa: S106 - test value
    WSO2_API_NAMES={"SANDBOX": API_NAMES},
)


@pytest.fixture(autouse=True)
def _isolated():
    reset_breakers()
    cache.clear()
    with wso2_settings:
        yield
    reset_breakers()


@pytest.fixture
def transport():
    return Wso2StubTransport()


@pytest.fixture
def gateway(transport):
    built = Wso2ApiGateway(transport=transport)
    yield built
    built.close()


# Create — the re-run safety B7 depends on


def test_creating_an_application_returns_its_id(gateway, transport):
    created = gateway.create_application(SPEC)

    assert created.name == APP_NAME
    assert transport.applications[APP_NAME] == created.external_id


def test_the_application_name_is_derived_from_the_reference(gateway):
    """B7 re-runs the chain; the name is how it finds what it already made."""
    first = gateway.create_application(SPEC)
    second = gateway.create_application(SPEC)

    assert first.external_id == second.external_id


def test_a_re_run_does_not_create_a_second_application(gateway, transport):
    gateway.create_application(SPEC)
    gateway.create_application(SPEC)

    assert transport.created_applications == 1


def test_an_application_made_by_someone_else_is_adopted(gateway, transport):
    """Lost the race with a concurrent chain run: the winner's app is ours too."""
    existing = transport.publish_application(APP_NAME)

    created = gateway.create_application(SPEC)

    assert created.external_id == existing
    assert transport.created_applications == 0


def test_a_conflict_with_no_findable_application_still_raises(gateway, transport):
    transport.failures[("POST", f"{DEVPORTAL}/applications")] = 409

    with pytest.raises(AdapterError) as error:
        gateway.create_application(SPEC)

    assert error.value.code == "HTTP_409"


# Subscribe


def test_subscribing_resolves_api_names_to_ids(gateway, transport):
    created = gateway.create_application(SPEC)

    gateway.subscribe(created.external_id, API_NAMES)

    assert set(transport.subscriptions[created.external_id]) == set(API_NAMES)
    assert f"{DEVPORTAL}/apis" in transport.paths("GET")


def test_subscribing_twice_adds_nothing_the_second_time(gateway, transport):
    created = gateway.create_application(SPEC)

    gateway.subscribe(created.external_id, API_NAMES)
    before = dict(transport.subscriptions[created.external_id])
    gateway.subscribe(created.external_id, API_NAMES)

    assert transport.subscriptions[created.external_id] == before


def test_subscribing_to_nothing_makes_no_call(gateway, transport):
    created = gateway.create_application(SPEC)
    calls = len(transport.calls)

    gateway.subscribe(created.external_id, ())

    assert len(transport.calls) == calls


def test_an_unpublished_api_name_fails_loudly(gateway, transport):
    created = gateway.create_application(SPEC)

    with pytest.raises(AdapterError) as error:
        gateway.subscribe(created.external_id, ("no-such-api",))

    assert error.value.code == "HTTP_404"
    assert error.value.retryable is False


# Unsubscribe — the thing legacy never did at all


def test_unsubscribing_removes_the_named_subscriptions(gateway, transport):
    created = gateway.create_application(SPEC)
    gateway.subscribe(created.external_id, API_NAMES)

    gateway.unsubscribe(created.external_id, API_NAMES)

    assert transport.subscriptions[created.external_id] == {}


def test_unsubscribing_leaves_the_others_alone(gateway, transport):
    created = gateway.create_application(SPEC)
    gateway.subscribe(created.external_id, API_NAMES)

    gateway.unsubscribe(created.external_id, ("abha-v3",))

    assert set(transport.subscriptions[created.external_id]) == {"hip-v3"}


def test_unsubscribing_what_was_never_subscribed_succeeds(gateway):
    created = gateway.create_application(SPEC)

    gateway.unsubscribe(created.external_id, API_NAMES)  # B8 reruns this


def test_unsubscribing_survives_a_subscription_vanishing_underneath(
    gateway,
    transport,
):
    created = gateway.create_application(SPEC)
    gateway.subscribe(created.external_id, ("abha-v3",))
    subscription_id = transport.subscriptions[created.external_id]["abha-v3"]
    transport.failures[("DELETE", f"{DEVPORTAL}/subscriptions/{subscription_id}")] = 404

    gateway.unsubscribe(created.external_id, ("abha-v3",))


# Key mapping and the secret


def test_mapping_keys_sends_the_dereferenced_secret(gateway, transport):
    created = gateway.create_application(SPEC)
    ref = store_secret("the-keycloak-secret")

    gateway.map_keys(created.external_id, CONSUMER_KEY, ref)

    sent = transport.mapped_keys[created.external_id]
    assert sent["consumerKey"] == CONSUMER_KEY
    assert sent["consumerSecret"] == "the-keycloak-secret"


def test_an_expired_secret_ref_is_retryable(gateway):
    """The chain can re-run from Keycloak, so this is worth another attempt."""
    created = gateway.create_application(SPEC)

    with pytest.raises(AdapterError) as error:
        gateway.map_keys(created.external_id, CONSUMER_KEY, "long-gone")

    assert error.value.code == "SECRET_REF_EXPIRED"
    assert error.value.retryable is True


def test_a_secret_ref_does_not_contain_the_secret():
    ref = store_secret("super-secret-value")

    assert "super-secret-value" not in ref
    assert resolve_secret(ref, ExternalSystem.WSO2) == "super-secret-value"


def test_no_secret_is_ever_logged(gateway, transport, caplog):
    created = gateway.create_application(SPEC)
    ref = store_secret("the-keycloak-secret")

    with caplog.at_level(logging.DEBUG):
        gateway.map_keys(created.external_id, CONSUMER_KEY, ref)

    logged = "\n".join(r.getMessage() for r in caplog.records)
    logged += json.dumps([r.__dict__ for r in caplog.records], default=str)
    assert "the-keycloak-secret" not in logged


# Transport policy


def test_tls_verification_is_never_disabled():
    """Legacy routed every WSO2 call through a trust-everything RestTemplate."""
    # Built at runtime so this scan does not match its own source.
    needle = "verify" + "=False"
    source = Path(__file__).resolve().parents[1]
    offenders = [
        str(path.relative_to(source))
        for path in source.rglob("*.py")
        if needle in path.read_text()
    ]

    assert not offenders, f"TLS verification disabled in: {offenders}"


def test_the_token_is_fetched_once_and_reused(gateway, transport):
    created = gateway.create_application(SPEC)
    gateway.subscribe(created.external_id, API_NAMES)

    assert transport.token_calls == 1


def test_a_server_error_is_retryable(gateway, transport):
    transport.failures[("GET", f"{DEVPORTAL}/applications")] = SERVER_ERROR

    with pytest.raises(AdapterError) as error:
        gateway.create_application(SPEC)

    assert error.value.retryable is True


def test_a_malformed_response_is_reported_not_crashed(gateway, transport):
    """A gateway in front of WSO2 can answer HTML; that must not be a KeyError."""
    transport.raw_bodies[("GET", f"{DEVPORTAL}/applications")] = "<html>502</html>"

    with pytest.raises(AdapterError) as error:
        gateway.create_application(SPEC)

    assert error.value.code == "MALFORMED_RESPONSE"
    assert error.value.retryable is False


# Config


def test_the_adapter_resolves_through_the_port_registry():
    dotted = "ohc_experience.integrations.wso2.adapter.Wso2ApiGateway"
    with override_settings(INTEGRATION_PORTS={"API_GATEWAY": dotted}):
        assert isinstance(get_api_gateway(), Wso2ApiGateway)


def test_api_names_come_from_settings_by_kind():
    assert api_names_for("SANDBOX") == API_NAMES


def test_an_unconfigured_kind_refuses_rather_than_subscribing_to_nothing():
    """An empty list would provision a client that silently reaches no API."""
    with (
        override_settings(WSO2_API_NAMES={"SANDBOX": ()}),
        pytest.raises(ImproperlyConfigured),
    ):
        api_names_for("SANDBOX")
