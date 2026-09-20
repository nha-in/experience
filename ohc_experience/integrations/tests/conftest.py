"""A registered product, and the chains a caller can put it through.

`provision` and `teardown` are callables rather than fixtures because what they
cause is the subject: a test arms a local adapter to fail, *then* runs one.

They run the chain here rather than scheduling it. `enqueue_chain` defers to
`transaction.on_commit`, which never fires inside a test's transaction, so an
effect left queued would prove nothing.
"""

from __future__ import annotations

import pytest

from ohc_experience.abdm.demo import product_data
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.workflows import register_product
from ohc_experience.integrations.services import provision_inline
from ohc_experience.integrations.tasks import TEARDOWN
from ohc_experience.integrations.tasks import sync_bridge
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory


@pytest.fixture
def owner(db):
    return UserFactory.create(email="owner@integrator.in")


@pytest.fixture
def product(owner):
    """Registered on a verified organisation: one step short of a live client."""
    organisation = OrganisationFactory.create(
        onboarded=True,
        verification_status=Organisation.VerificationStatus.VERIFIED,
    )
    MembershipFactory.create(
        organisation=organisation,
        user=owner,
        role=Role.OWNER,
    )
    workspace, form = register_product(organisation, owner, data=product_data())
    assert workspace, form.errors
    return workspace.product


@pytest.fixture
def provision(product):
    def _provision():
        provision_inline(product)
        product.refresh_from_db()
        return product

    return _provision


@pytest.fixture
def teardown(product):
    def _teardown():
        for task in TEARDOWN:
            task(product.pk)
        product.refresh_from_db()
        return product

    return _teardown


CALLBACK_URL = "https://integrator.example/abdm/callback"


@pytest.fixture
def register_bridge(product):
    """A bridge exists only because a callback URL was saved."""

    def _register(url=CALLBACK_URL):
        credential = ProductCredential.objects.get(product=product)
        credential.callback_url = url
        credential.save(update_fields=["callback_url"])
        sync_bridge(product.pk)
        return credential

    return _register
