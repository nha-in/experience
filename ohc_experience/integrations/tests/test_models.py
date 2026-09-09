"""Ledger schema guarantees.

The uniqueness rule is the chain's idempotency backstop: a retried run must not
be able to create a second Keycloak client for the same product. Asserted
against the database, not the model layer, because that is where a concurrent
retry would collide.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError
from django.db import transaction
from django.db.models import ProtectedError

from ohc_experience.experiences.models import Product
from ohc_experience.integrations.models import ProvisionedResource
from ohc_experience.integrations.models import ProvisionedSystem

pytestmark = pytest.mark.django_db


def _resource(product, **overrides) -> ProvisionedResource:
    defaults = {
        "product": product,
        "system": ProvisionedSystem.KEYCLOAK,
        "external_ref": "SBXID_1001",
    }
    return ProvisionedResource.objects.create(**{**defaults, **overrides})


def test_one_resource_per_product_and_system(product):
    _resource(product)

    with transaction.atomic(), pytest.raises(IntegrityError):
        _resource(product, external_ref="SBXID_1002")


def test_the_same_product_may_hold_one_resource_per_system(product):
    first = _resource(product)

    second = _resource(
        product,
        system=ProvisionedSystem.WSO2,
        external_ref="wso2-app-1",
    )

    assert second.pk != first.pk


def test_only_a_provisionable_system_may_be_recorded(product):
    with transaction.atomic(), pytest.raises(IntegrityError):
        _resource(product, system="NOTIFICATION")


def test_state_must_be_a_known_value(product):
    with transaction.atomic(), pytest.raises(IntegrityError):
        _resource(product, state="WOBBLY")


def test_the_product_cannot_be_deleted_from_under_the_ledger(product):
    _resource(product)

    with transaction.atomic(), pytest.raises(ProtectedError):
        Product.objects.filter(pk=product.pk).delete()


def test_secret_ref_defaults_to_empty_and_holds_a_reference_only(product):
    assert _resource(product).secret_ref == ""
