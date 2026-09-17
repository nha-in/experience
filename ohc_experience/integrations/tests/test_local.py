"""The local adapters must behave like the real thing where it matters."""

from __future__ import annotations

import time
from typing import Any

import pytest

from ohc_experience.integrations import local
from ohc_experience.integrations.local import LocalApiGateway
from ohc_experience.integrations.local import LocalBridgeRegistry
from ohc_experience.integrations.local import LocalIdpAdmin
from ohc_experience.integrations.local import LocalNotificationGateway
from ohc_experience.integrations.ports import AdapterError
from ohc_experience.integrations.ports import ApiGateway
from ohc_experience.integrations.ports import BridgeRegistry
from ohc_experience.integrations.ports import BridgeSpec
from ohc_experience.integrations.ports import ClientSpec
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.ports import GatewayAppSpec
from ohc_experience.integrations.ports import IdpAdmin
from ohc_experience.integrations.ports import NotificationChannel
from ohc_experience.integrations.ports import NotificationContentType
from ohc_experience.integrations.ports import NotificationGateway
from ohc_experience.integrations.ports import NotificationMessage
from ohc_experience.integrations.secret_ref import store_secret

SPEC = ClientSpec(reference="SBX-2026-00001", display_name="Acme", role_names=("hip",))
APP_SPEC = GatewayAppSpec(reference="SBX-2026-00001", name="Acme", api_ids=("abha",))
#: WSO2 derives the name from the reference, and so does the local adapter.
APP_NAME = "sbx-SBX-2026-00001"
BRIDGE_SPEC = BridgeSpec(
    bridge_id="SBX_ABC",
    name="Acme",
    url="https://acme.test",
    entity="Private",
)
# A pointer into a secret store, never a secret value. Deliberately never parked,
# so it stands in for one that has expired.
SECRET_REF = "vault://x"  # noqa: S105
LATENCY = 0.05
OTP_MESSAGE = NotificationMessage(
    channel=NotificationChannel.SMS,
    receiver="9999999999",
    template_id="1007164181681962323",
    subject="Mobile verification",
    values=("123456",),
    content_type=NotificationContentType.OTP,
)


@pytest.fixture(autouse=True)
def _no_activation_delay(settings):
    settings.LOCAL_BRIDGE_ACTIVATION_DELAY_SECONDS = 0.0


def client_record(idp: LocalIdpAdmin, external_id: str) -> dict[str, Any]:
    record = idp.get_client(external_id)
    assert record is not None
    return record


def app_record(gateway: LocalApiGateway, external_id: str) -> dict[str, Any]:
    record = gateway.get_application(external_id)
    assert record is not None
    return record


def test_local_adapters_satisfy_their_protocols() -> None:
    """Conformance is structural: mypy fails this file if one drifts."""
    idp: IdpAdmin = LocalIdpAdmin()
    gateway: ApiGateway = LocalApiGateway()
    registry: BridgeRegistry = LocalBridgeRegistry()

    assert isinstance(idp, LocalIdpAdmin)
    assert isinstance(gateway, LocalApiGateway)
    assert isinstance(registry, LocalBridgeRegistry)


# Keycloak


def test_created_client_gets_a_non_derivable_id_and_a_secret():
    first = LocalIdpAdmin().create_client(SPEC)
    second = LocalIdpAdmin().create_client(SPEC)

    assert first.client_id != second.client_id  # never sequence-derived
    assert SPEC.reference not in first.client_id
    assert first.initial_secret
    assert first.initial_secret != second.initial_secret


def test_reading_a_client_never_rotates_its_secret():
    """The legacy system's exact bug: getSecret was bound to POST."""
    idp = LocalIdpAdmin()
    created = idp.create_client(SPEC)

    first = client_record(idp, created.external_id)["secret"]
    second = client_record(idp, created.external_id)["secret"]

    assert first == second == created.initial_secret


def test_rotate_returns_a_new_secret():
    idp = LocalIdpAdmin()
    created = idp.create_client(SPEC)

    rotated = idp.rotate_client_secret(created.external_id)

    assert rotated.secret != created.initial_secret
    assert client_record(idp, created.external_id)["secret"] == rotated.secret


def test_rotating_an_unknown_client_is_a_non_retryable_error():
    with pytest.raises(AdapterError) as excinfo:
        LocalIdpAdmin().rotate_client_secret("nope")

    assert excinfo.value.retryable is False


def test_disable_is_idempotent_even_for_a_missing_client():
    idp = LocalIdpAdmin()
    created = idp.create_client(SPEC)

    idp.disable_client(created.external_id)
    idp.disable_client(created.external_id)
    idp.disable_client("never-existed")  # B8 requires this to succeed

    assert client_record(idp, created.external_id)["enabled"] is False


def test_requested_roles_are_recorded_by_name():
    idp = LocalIdpAdmin()
    created = idp.create_client(ClientSpec("SBX-1", "Acme", ("hip", "hiu")))

    assert client_record(idp, created.external_id)["roles"] == ["hip", "hiu"]


# WSO2


def test_gateway_application_tracks_subscriptions():
    gateway = LocalApiGateway()
    created = gateway.create_application(APP_SPEC)

    gateway.subscribe(created.external_id, ("hip", "hiu"))
    gateway.unsubscribe(created.external_id, ("hip",))

    assert app_record(gateway, created.external_id)["subscriptions"] == ["abha", "hiu"]


def test_unsubscribe_is_idempotent_for_a_missing_application():
    LocalApiGateway().unsubscribe("never-existed", ("abha",))


def test_map_keys_stores_a_reference_not_a_secret():
    gateway = LocalApiGateway()
    created = gateway.create_application(APP_SPEC)
    ref = store_secret("the-actual-secret")

    gateway.map_keys(created.external_id, consumer_key="ck", secret_ref=ref)

    record = app_record(gateway, created.external_id)
    assert record["keys_mapped"] is True
    assert record["secret_ref"] == ref
    assert "the-actual-secret" not in str(record)


def test_map_keys_refuses_a_reference_that_has_expired():
    """It has to fail here too, or the secret-expiry dead-end is invisible in CI."""
    gateway = LocalApiGateway()
    created = gateway.create_application(APP_SPEC)

    with pytest.raises(AdapterError) as exc:
        gateway.map_keys(
            created.external_id,
            consumer_key="ck",
            secret_ref=SECRET_REF,
        )

    assert exc.value.code == "SECRET_REF_EXPIRED"


# HIE-CM


def test_bridge_becomes_active_after_the_configured_delay(settings):
    settings.LOCAL_BRIDGE_ACTIVATION_DELAY_SECONDS = 0.05
    registry = LocalBridgeRegistry()
    registry.create_bridge(BRIDGE_SPEC)

    assert registry.get_bridge_status(BRIDGE_SPEC.bridge_id).active is False

    time.sleep(0.06)

    assert registry.get_bridge_status(BRIDGE_SPEC.bridge_id).active is True


def test_deactivated_bridge_reports_inactive_and_deactivate_is_idempotent():
    registry = LocalBridgeRegistry()
    registry.create_bridge(BRIDGE_SPEC)

    registry.deactivate_bridge(BRIDGE_SPEC.bridge_id)
    registry.deactivate_bridge(BRIDGE_SPEC.bridge_id)
    registry.deactivate_bridge("never-existed")

    assert registry.get_bridge_status(BRIDGE_SPEC.bridge_id).active is False


def test_status_of_an_unknown_bridge_is_a_non_retryable_error():
    with pytest.raises(AdapterError) as excinfo:
        LocalBridgeRegistry().get_bridge_status("nope")

    assert excinfo.value.retryable is False


# Failure injection


def test_fail_next_fires_once_then_clears():
    local.fail_next(ExternalSystem.KEYCLOAK, "create_client")
    idp = LocalIdpAdmin()

    with pytest.raises(AdapterError) as excinfo:
        idp.create_client(SPEC)
    assert excinfo.value.code == "LOCAL_FAILURE"

    assert idp.create_client(SPEC).client_id  # second call succeeds


def test_fail_next_is_scoped_to_one_operation():
    local.fail_next(ExternalSystem.KEYCLOAK, "rotate_client_secret")

    created = LocalIdpAdmin().create_client(SPEC)  # different op, unaffected

    with pytest.raises(AdapterError):
        LocalIdpAdmin().rotate_client_secret(created.external_id)


def test_always_fail_persists_until_cleared():
    local.always_fail(ExternalSystem.WSO2, code="LOCAL_DOWN", retryable=True)
    gateway = LocalApiGateway()

    for _ in range(3):
        with pytest.raises(AdapterError) as excinfo:
            gateway.create_application(APP_SPEC)
        assert excinfo.value.code == "LOCAL_DOWN"

    local.clear_failures(ExternalSystem.WSO2)

    assert gateway.create_application(APP_SPEC).name == APP_NAME


def test_failure_injection_is_scoped_to_one_system():
    local.always_fail(ExternalSystem.WSO2)

    assert LocalIdpAdmin().create_client(SPEC).client_id  # Keycloak unaffected


def test_injected_failures_are_adapter_errors_with_a_retryable_flag():
    local.always_fail(ExternalSystem.HIECM, code="LOCAL_4XX", retryable=False)

    with pytest.raises(AdapterError) as excinfo:
        LocalBridgeRegistry().create_bridge(BRIDGE_SPEC)

    assert excinfo.value.system is ExternalSystem.HIECM
    assert excinfo.value.retryable is False


# Notifications


def test_a_notification_is_kept_instead_of_sent():
    gateway: NotificationGateway = LocalNotificationGateway()

    gateway.send(OTP_MESSAGE)

    assert LocalNotificationGateway().sent() == [
        {
            "channel": "sms",
            "receiver": "9999999999",
            "template_id": "1007164181681962323",
            "subject": "Mobile verification",
            "values": ["123456"],
            "content_type": "otp",
        },
    ]


def test_a_failing_notification_is_not_kept():
    local.fail_next(ExternalSystem.NOTIFICATION, "send", retryable=False)

    with pytest.raises(AdapterError):
        LocalNotificationGateway().send(OTP_MESSAGE)

    assert LocalNotificationGateway().sent() == []


def test_latency_injection_delays_the_call():
    local.set_latency(ExternalSystem.KEYCLOAK, LATENCY)

    started = time.monotonic()
    LocalIdpAdmin().create_client(SPEC)

    assert time.monotonic() - started >= LATENCY


# Reset


def test_reset_clears_state_and_failure_knobs():
    idp = LocalIdpAdmin()
    created = idp.create_client(SPEC)
    local.always_fail(ExternalSystem.WSO2)

    local.reset_local_state()

    assert idp.get_client(created.external_id) is None
    assert LocalApiGateway().create_application(APP_SPEC).name == APP_NAME
