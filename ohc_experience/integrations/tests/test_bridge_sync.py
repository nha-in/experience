"""The bridge follows the integrator's callback URL, not the chain."""

from __future__ import annotations

import pytest
from django.test import override_settings

from ohc_experience.experiences.models import ProductCredential
from ohc_experience.integrations import local
from ohc_experience.integrations.local import always_fail
from ohc_experience.integrations.local import fail_next
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisionedResourceState
from ohc_experience.integrations.models import ProvisionedSystem
from ohc_experience.integrations.ports import ExternalSystem
from ohc_experience.integrations.selectors import bridge_state
from ohc_experience.integrations.tasks import sync_bridge

pytestmark = pytest.mark.django_db

SECOND_URL = "https://integrator.example/abdm/v2/callback"


def _row(product):
    return ProvisionedResource.objects.filter(
        product=product,
        system=ProvisionedSystem.HIECM,
    ).first()


def _stored(bridge_id):
    return local._store(ExternalSystem.HIECM)[bridge_id]  # noqa: SLF001


def test_a_saved_url_registers_a_bridge_pointing_at_it(provision, register_bridge):
    product = provision()

    credential = register_bridge()

    row = _row(product)
    assert row.state == ProvisionedResourceState.ACTIVE
    assert _stored(row.external_ref)["url"] == credential.callback_url


def test_the_bridge_is_named_after_the_keycloak_client(provision, register_bridge):
    product = provision()

    register_bridge()

    client = ProvisionedResource.objects.get(
        product=product,
        system=ProvisionedSystem.KEYCLOAK,
    )
    assert _row(product).external_ref == client.public_ref


def test_saving_a_new_url_moves_the_bridge_rather_than_adding_one(
    provision,
    register_bridge,
):
    product = provision()
    register_bridge()
    before = _row(product).pk

    register_bridge(SECOND_URL)

    row = _row(product)
    assert row.pk == before
    assert _stored(row.external_ref)["url"] == SECOND_URL


def test_a_product_with_no_saved_url_gets_no_bridge(provision):
    product = provision()

    sync_bridge(product.pk)

    assert _row(product) is None
    assert bridge_state(product) == "none"


@override_settings(PROVISIONING_MAX_ATTEMPTS=1)
def test_a_refused_registration_is_recorded_so_the_panel_can_say_so(
    provision,
    register_bridge,
):
    product = provision()
    always_fail(ExternalSystem.HIECM, code="HTTP_422", retryable=False)

    register_bridge()

    assert _row(product).state == ProvisionedResourceState.FAILED
    assert bridge_state(product) == "failed"
    assert product.audit_events.filter(action="Bridge registration failed").exists()


@override_settings(PROVISIONING_MAX_ATTEMPTS=1)
def test_registering_again_after_a_failure_succeeds(provision, register_bridge):
    product = provision()
    fail_next(ExternalSystem.HIECM, "create_bridge", retryable=False)
    register_bridge()
    assert bridge_state(product) == "failed"

    register_bridge()

    assert bridge_state(product) == "registered"


def test_a_revoked_credential_keeps_its_url_off_the_gateway(provision, register_bridge):
    """Teardown works from the ledger row the sync wrote."""
    product = provision()
    register_bridge()
    bridge_id = _row(product).external_ref

    from ohc_experience.integrations.tasks import deprovision_hiecm  # noqa: PLC0415

    deprovision_hiecm(product.pk)

    assert local.LocalBridgeRegistry().get_bridge_status(bridge_id).active is False


def test_clearing_the_url_leaves_the_existing_bridge_alone(provision, register_bridge):
    """Revoking takes a bridge down; an empty box is not a revocation."""
    product = provision()
    register_bridge()

    credential = ProductCredential.objects.get(product=product)
    credential.callback_url = ""
    credential.save(update_fields=["callback_url"])
    sync_bridge(product.pk)

    assert _row(product).state == ProvisionedResourceState.ACTIVE
