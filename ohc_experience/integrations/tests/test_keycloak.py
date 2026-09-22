"""B3 acceptance criteria, one test apiece where possible."""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from ohc_experience.integrations.http import reset_breakers
from ohc_experience.integrations.keycloak.adapter import KeycloakIdpAdmin
from ohc_experience.integrations.keycloak.adapter import new_client_id
from ohc_experience.integrations.keycloak.roles import role_names_for
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ClientSpec
from ohc_experience.integrations.registry import get_idp_admin
from ohc_experience.integrations.tests.keycloak_stub import ADMIN
from ohc_experience.integrations.tests.keycloak_stub import CREATED_CLIENT_UUID
from ohc_experience.integrations.tests.keycloak_stub import SERVICE_ACCOUNT_USER_ID
from ohc_experience.integrations.tests.keycloak_stub import TOKEN_PATH
from ohc_experience.integrations.tests.keycloak_stub import KeycloakStubTransport

SANDBOX_ROLES = ("healthId", "hip", "hiu")
SPEC = ClientSpec(
    reference="SBX-2026-00001",
    display_name="Demo HMIS",
    role_names=SANDBOX_ROLES,
)

SERVER_ERROR = 500
CLIENT_ID_SUFFIX_LENGTH = 16
DISTINCT_IDS = 200

keycloak_settings = override_settings(
    KEYCLOAK_BASE_URL="http://kc.test",
    KEYCLOAK_REALM="abdm-sandbox",
    KEYCLOAK_CLIENT_ID="sandbox-admin",
    KEYCLOAK_CLIENT_SECRET="admin-client-secret",  # noqa: S106 - test value
    KEYCLOAK_USERNAME="provisioning-admin",
    KEYCLOAK_PASSWORD="admin-password",  # noqa: S106 - test value
    KEYCLOAK_API_KEY="gateway-api-key",
    KEYCLOAK_ROLE_NAMES={"SANDBOX": SANDBOX_ROLES},
)


@pytest.fixture(autouse=True)
def _isolated():
    reset_breakers()
    with keycloak_settings:
        yield
    reset_breakers()


@pytest.fixture
def transport():
    return KeycloakStubTransport()


@pytest.fixture
def adapter(transport):
    built = KeycloakIdpAdmin(transport=transport)
    yield built
    built.close()


# Create


def test_creating_a_client_returns_its_id_uuid_and_first_secret(adapter, transport):
    created = adapter.create_client(SPEC)

    assert created.external_id == CREATED_CLIENT_UUID
    assert created.initial_secret == transport.secret
    assert created.client_id.startswith("SBX_")


def test_the_new_client_is_confidential_and_not_full_scope(adapter, transport):
    adapter.create_client(SPEC)

    (body,) = transport.bodies("POST", "/clients")
    assert body["serviceAccountsEnabled"] is True
    assert body["publicClient"] is False
    # true here would hand the integrator every realm role — the legacy over-grant
    assert body["fullScopeAllowed"] is False
    assert body["standardFlowEnabled"] is False


def test_the_secret_is_never_in_the_dtos_repr(adapter):
    created = adapter.create_client(SPEC)

    assert created.initial_secret not in repr(created)


# Roles


def test_roles_are_resolved_by_name_at_call_time(adapter, transport):
    adapter.create_client(SPEC)

    looked_up = [p for p in transport.paths("GET") if "/roles/" in p]
    assert looked_up == [f"{ADMIN}/roles/{name}" for name in SANDBOX_ROLES]


def test_a_role_is_both_scope_mapped_and_granted_to_the_service_account(
    adapter,
    transport,
):
    """Scope-mapping alone puts nothing in a client_credentials token."""
    adapter.create_client(SPEC)

    assert transport.scope_mapped == list(SANDBOX_ROLES)
    assert transport.granted_to_service_account == list(SANDBOX_ROLES)
    assert f"{ADMIN}/users/{SERVICE_ACCOUNT_USER_ID}/role-mappings/realm" in (
        transport.paths("POST")
    )


def test_only_the_configured_roles_are_granted(adapter, transport):
    adapter.create_client(SPEC)

    assert set(transport.granted_to_service_account) == set(SANDBOX_ROLES)


def test_an_unknown_role_name_fails_the_create(adapter):
    with pytest.raises(AdapterError) as error:
        adapter.create_client(
            ClientSpec(reference="r", display_name="d", role_names=("nope",)),
        )

    assert error.value.code == "HTTP_404"
    assert error.value.retryable is False


def test_a_client_with_no_roles_skips_role_calls_entirely(adapter, transport):
    adapter.create_client(
        ClientSpec(reference="r", display_name="d", role_names=()),
    )

    assert transport.scope_mapped == []
    assert not [p for p in transport.paths("GET") if "/roles/" in p]


# The legacy bug


def test_reading_the_secret_never_posts_to_the_rotate_endpoint(adapter, transport):
    """Legacy bound `getSecret` to POST, so every read rotated a live credential."""
    before = transport.secret

    adapter.create_client(SPEC)

    assert transport.secret == before
    assert f"{ADMIN}/clients/{CREATED_CLIENT_UUID}/client-secret" in (
        transport.paths("GET")
    )
    assert f"{ADMIN}/clients/{CREATED_CLIENT_UUID}/client-secret" not in (
        transport.paths("POST")
    )


def test_rotating_is_the_one_path_that_posts_to_client_secret(adapter, transport):
    created = adapter.create_client(SPEC)

    rotated = adapter.rotate_client_secret(created.external_id)

    assert rotated.secret != created.initial_secret
    assert (
        transport.paths("POST").count(
            f"{ADMIN}/clients/{CREATED_CLIENT_UUID}/client-secret",
        )
        == 1
    )


# Client id


def test_client_ids_are_not_derivable():
    """Legacy ids were `SBXID_(sdId+55)` and doubled as public bridge ids."""
    generated = {new_client_id() for _ in range(DISTINCT_IDS)}

    assert len(generated) == DISTINCT_IDS
    suffixes = [value.removeprefix("SBX_") for value in generated]
    assert all(re.fullmatch(r"[0-9A-F]{16}", s) for s in suffixes)
    assert all(len(s) == CLIENT_ID_SUFFIX_LENGTH for s in suffixes)


# Disable


def test_disabling_sends_enabled_false(adapter, transport):
    adapter.disable_client(CREATED_CLIENT_UUID)

    (body,) = transport.bodies("PUT", CREATED_CLIENT_UUID)
    assert body == {"enabled": False}
    assert transport.disabled is True


def test_disabling_a_missing_client_succeeds(adapter, transport):
    transport.failures[("PUT", f"{ADMIN}/clients/{CREATED_CLIENT_UUID}")] = 404

    adapter.disable_client(CREATED_CLIENT_UUID)  # B8 reruns this; must not raise


def test_disabling_still_reports_a_real_failure(adapter, transport):
    transport.failures[("PUT", f"{ADMIN}/clients/{CREATED_CLIENT_UUID}")] = SERVER_ERROR

    with pytest.raises(AdapterError) as error:
        adapter.disable_client(CREATED_CLIENT_UUID)

    assert error.value.retryable is True


def test_enabling_sends_enabled_true(adapter, transport):
    adapter.disable_client(CREATED_CLIENT_UUID)

    adapter.enable_client(CREATED_CLIENT_UUID)

    assert list(transport.bodies("PUT", CREATED_CLIENT_UUID))[-1] == {"enabled": True}
    assert transport.disabled is False


def test_enabling_a_missing_client_fails(adapter, transport):
    """Unlike disabling: there is nothing to bring back."""
    transport.failures[("PUT", f"{ADMIN}/clients/{CREATED_CLIENT_UUID}")] = 404

    with pytest.raises(AdapterError) as error:
        adapter.enable_client(CREATED_CLIENT_UUID)

    assert error.value.retryable is False


# Token handling


def test_the_admin_token_is_fetched_once_and_reused(adapter, transport):
    adapter.create_client(SPEC)
    adapter.rotate_client_secret(CREATED_CLIENT_UUID)

    assert transport.token_calls == 1


def test_the_token_is_requested_as_legacy_requested_it(adapter, transport):
    """NHA issued only legacy's master-realm admin sign-in."""
    adapter.create_client(SPEC)

    token_call = next(c for c in transport.calls if c.url.path == TOKEN_PATH)
    assert token_call.method == "POST"
    assert token_call.headers["apikey"] == "gateway-api-key"
    assert parse_qs(token_call.content.decode()) == {
        "grant_type": ["password"],
        "scope": ["openid"],
        "client_id": ["sandbox-admin"],
        "client_secret": ["admin-client-secret"],
        "username": ["provisioning-admin"],
        "password": ["admin-password"],
    }


def test_only_the_token_call_carries_the_api_key(adapter, transport):
    adapter.create_client(SPEC)

    admin_calls = [c for c in transport.calls if c.url.path.startswith(ADMIN)]
    assert admin_calls
    assert not any("apikey" in c.headers for c in admin_calls)


def test_no_api_key_header_is_sent_when_none_is_configured(transport):
    with override_settings(KEYCLOAK_API_KEY=""):
        adapter = KeycloakIdpAdmin(transport=transport)
        adapter.create_client(SPEC)
        adapter.close()

    token_call = next(c for c in transport.calls if c.url.path == TOKEN_PATH)
    assert "apikey" not in token_call.headers


def test_admin_calls_carry_the_bearer_token(adapter, transport):
    adapter.create_client(SPEC)

    admin_calls = [c for c in transport.calls if c.url.path.startswith(ADMIN)]
    assert admin_calls
    assert all(c.headers["Authorization"] == "Bearer token-1" for c in admin_calls)


# Error mapping


def test_a_non_json_response_becomes_an_adapter_error(adapter, transport):
    transport.failures[("GET", f"{ADMIN}/roles/healthId")] = SERVER_ERROR

    with pytest.raises(AdapterError) as error:
        adapter.create_client(SPEC)

    assert error.value.system == "KEYCLOAK"


def test_a_create_with_no_location_header_is_reported_not_crashed(adapter, transport):
    transport.failures[("POST", f"{ADMIN}/clients")] = 200

    with pytest.raises(AdapterError) as error:
        adapter.create_client(SPEC)

    assert error.value.code == "MALFORMED_RESPONSE"
    assert error.value.retryable is False


# Config


def test_the_adapter_resolves_through_the_port_registry(transport):
    with override_settings(
        INTEGRATION_PORTS={
            "IDP": "ohc_experience.integrations.keycloak.adapter.KeycloakIdpAdmin",
        },
    ):
        assert isinstance(get_idp_admin(), KeycloakIdpAdmin)


def test_no_secret_is_ever_logged(adapter, transport, caplog):
    """Legacy emailed secrets in plaintext and kept a copy in `sd_status`."""
    with caplog.at_level(logging.DEBUG):
        created = adapter.create_client(SPEC)
        rotated = adapter.rotate_client_secret(created.external_id)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    logged += json.dumps(
        [record.__dict__ for record in caplog.records],
        default=str,
    )
    assert created.initial_secret not in logged
    assert rotated.secret not in logged
    assert settings.KEYCLOAK_CLIENT_SECRET not in logged
    assert settings.KEYCLOAK_PASSWORD not in logged


def test_role_names_come_from_settings_by_kind():
    assert role_names_for("SANDBOX") == SANDBOX_ROLES


def test_an_unconfigured_kind_is_a_configuration_error():
    with pytest.raises(ImproperlyConfigured):
        role_names_for("HCX")


def test_no_realm_uuid_is_configured_anywhere():
    """Legacy pinned role UUIDs and containerIds in YAML; ours are names."""
    uuid_like = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", re.IGNORECASE)

    for names in [role_names_for("SANDBOX")]:
        assert not any(uuid_like.search(name) for name in names)
