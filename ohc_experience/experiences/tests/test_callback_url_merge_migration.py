"""The merge of `bridge_url` into `callback_url`.

Imported credentials hold the URL in `bridge_url` only, and an empty
`callback_url` is a legitimate state, so dropping the column without carrying
the value over would lose those endpoints silently.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.secrets import cipher
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BEFORE = [("experiences", "0028_uhi_builds_on_m1")]
AFTER = [("experiences", "0029_merge_bridge_url_into_callback_url")]

IMPORTED = "https://imported.example/callback"
SAVED = "https://saved.example/callback"


def latest():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def credential(apps, *, callback_url, bridge_url):
    organisation = OrganisationFactory.create()
    product = apps.get_model("experiences", "Product").objects.create(
        organisation_id=organisation.pk,
        name=f"Product {callback_url}{bridge_url}",
        slug=f"product-{organisation.pk}",
        description="Imported from the legacy sandbox.",
        created_by_id=UserFactory.create().pk,
    )
    row = apps.get_model("experiences", "ProductCredential").objects.create(
        product_id=product.pk,
        client_id=f"client-{product.pk}",
        encrypted_secret=cipher().encrypt(b"legacy-secret").decode(),
        gateway_url="https://gateway.example",
        callback_url=callback_url,
        bridge_url=bridge_url,
        rotation_due=timezone.now(),
    )
    # Postgres refuses to ALTER a table with deferred checks pending, and
    # `transaction=True` would truncate the reference data migrations seed.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return row


def test_an_imported_bridge_url_becomes_the_callback_url(at_before):
    row = credential(at_before, callback_url="", bridge_url=IMPORTED)

    MigrationExecutor(connection).migrate(AFTER)

    assert ProductCredential.objects.get(pk=row.pk).callback_url == IMPORTED


def test_a_saved_callback_url_is_not_overwritten(at_before):
    row = credential(at_before, callback_url=SAVED, bridge_url=IMPORTED)

    MigrationExecutor(connection).migrate(AFTER)

    assert ProductCredential.objects.get(pk=row.pk).callback_url == SAVED


def test_a_credential_with_neither_stays_empty(at_before):
    row = credential(at_before, callback_url="", bridge_url="")

    MigrationExecutor(connection).migrate(AFTER)

    assert ProductCredential.objects.get(pk=row.pk).callback_url == ""


def test_the_way_back_restores_a_column_holding_the_url(at_before):
    row = credential(at_before, callback_url="", bridge_url=IMPORTED)
    MigrationExecutor(connection).migrate(AFTER)

    MigrationExecutor(connection).migrate(BEFORE)

    restored = at_before.get_model("experiences", "ProductCredential").objects.get(
        pk=row.pk,
    )
    assert restored.bridge_url == IMPORTED
    assert restored.callback_url == IMPORTED
