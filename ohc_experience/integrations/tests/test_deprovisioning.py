"""What revoking causes: every system switched off, not just the first one."""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied
from django.test import override_settings

from ohc_experience.experiences import credentials as credential_services
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.integrations import local
from ohc_experience.integrations.local import always_fail
from ohc_experience.integrations.local import fail_next
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisionedResourceState
from ohc_experience.integrations.models import ProvisionedSystem
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.selectors import teardown_is_incomplete
from ohc_experience.integrations.tasks import deprovision_keycloak

pytestmark = pytest.mark.django_db


def _states(product) -> dict[str, str]:
    return {
        row.system: row.state
        for row in ProvisionedResource.objects.filter(product=product)
    }


# ── Happy path ───────────────────────────────────────────────────────────────


def test_teardown_disables_all_three(provision, teardown):
    product = provision()

    teardown()

    assert set(_states(product).values()) == {ProvisionedResourceState.DISABLED}
    client = ProvisionedResource.objects.get(
        product=product,
        system=ProvisionedSystem.KEYCLOAK,
    )
    record = local.LocalIdpAdmin().get_client(client.external_ref)
    assert record is not None
    assert record["enabled"] is False


def test_the_bridge_and_the_subscription_go_too(provision, register_bridge, teardown):
    """Disabling the client alone leaves gateway access and the bridge live."""
    product = provision()
    register_bridge()
    wso2 = ProvisionedResource.objects.get(
        product=product,
        system=ProvisionedSystem.WSO2,
    )

    teardown()

    record = local.LocalApiGateway().get_application(wso2.external_ref)
    assert record is not None
    assert record["subscriptions"] == []
    bridge = ProvisionedResource.objects.get(
        product=product,
        system=ProvisionedSystem.HIECM,
    )
    assert (
        local.LocalBridgeRegistry().get_bridge_status(bridge.external_ref).active
        is False
    )


def test_a_partially_provisioned_product_is_cleaned_up_too(provision, teardown):
    """A chain that failed at the bridge still created a client and a gateway app."""
    fail_next(ExternalSystem.HIECM, "create_bridge", retryable=False)
    product = provision()

    teardown()

    assert _states(product) == {
        ProvisionedSystem.KEYCLOAK: ProvisionedResourceState.DISABLED,
        ProvisionedSystem.WSO2: ProvisionedResourceState.DISABLED,
    }


# ── Through the console ────────────────────────────────────────────────────────


def test_revoking_runs_the_teardown_and_closes_the_credential(
    provision,
    superadmin,
    django_capture_on_commit_callbacks,
):
    product = provision()
    credential = ProductCredential.objects.get(product=product)

    with django_capture_on_commit_callbacks(execute=True):
        credential_services.revoke(credential, superadmin)

    credential.refresh_from_db()
    assert credential.status == "revoked"
    assert set(_states(product).values()) == {ProvisionedResourceState.DISABLED}
    assert not teardown_is_incomplete(product)


def test_revoking_twice_is_a_no_op(
    provision,
    superadmin,
    django_capture_on_commit_callbacks,
):
    product = provision()
    credential = ProductCredential.objects.get(product=product)
    with django_capture_on_commit_callbacks(execute=True):
        credential_services.revoke(credential, superadmin)

    with django_capture_on_commit_callbacks(execute=True):
        credential_services.revoke(credential, superadmin)

    assert set(_states(product).values()) == {ProvisionedResourceState.DISABLED}


def test_an_integrator_cannot_revoke_their_own_credentials(provision, owner):
    product = provision()
    credential = ProductCredential.objects.get(product=product)

    with pytest.raises(PermissionDenied):
        credential_services.revoke(credential, owner)

    credential.refresh_from_db()
    assert credential.status == "active"
    assert ProvisionedResourceState.DISABLED not in set(_states(product).values())


def test_an_unprovisioned_product_has_nothing_to_tear_down(product, teardown):
    teardown()

    assert _states(product) == {}
    assert not teardown_is_incomplete(product)


# ── Idempotency and failure ──────────────────────────────────────────────────


def test_re_running_the_teardown_is_harmless(provision, teardown):
    product = provision()
    teardown()

    deprovision_keycloak.delay(product.pk)

    assert set(_states(product).values()) == {ProvisionedResourceState.DISABLED}


@override_settings(PROVISIONING_MAX_ATTEMPTS=1)
def test_one_failed_step_does_not_strand_the_others(
    provision,
    register_bridge,
    teardown,
):
    """The opposite rule to provisioning: a resource left on is a live credential,
    so a step that gives up must still let the next one run."""
    product = provision()
    register_bridge()
    always_fail(ExternalSystem.KEYCLOAK, code="REALM_DOWN", retryable=True)

    teardown()

    assert _states(product) == {
        ProvisionedSystem.KEYCLOAK: ProvisionedResourceState.FAILED,
        ProvisionedSystem.WSO2: ProvisionedResourceState.DISABLED,
        ProvisionedSystem.HIECM: ProvisionedResourceState.DISABLED,
    }


@override_settings(PROVISIONING_MAX_ATTEMPTS=1)
def test_a_failed_step_is_what_a_retry_is_for(provision, teardown):
    product = provision()
    always_fail(ExternalSystem.KEYCLOAK, code="REALM_DOWN", retryable=True)
    teardown()
    assert teardown_is_incomplete(product)

    local.clear_failures(ExternalSystem.KEYCLOAK)
    teardown()

    assert not teardown_is_incomplete(product)
