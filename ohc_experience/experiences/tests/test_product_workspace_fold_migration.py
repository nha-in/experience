"""Folding `ProductWorkspace` into `Product`.

Every workspace field moves onto its product. A product that somehow has no
workspace still needs a reference, since the column becomes unique and the
URLs are built from it.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from ohc_experience.experiences.models import Product
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BEFORE = [("experiences", "0036_retitle_phr_milestones")]
AFTER = [("experiences", "0037_fold_product_workspace")]


def latest():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def product(apps, name):
    return apps.get_model("experiences", "Product").objects.create(
        organisation_id=OrganisationFactory.create().pk,
        name=name,
        slug=name.lower(),
        description="A product.",
        created_by_id=UserFactory.create().pk,
    )


def settle():
    # Postgres refuses to ALTER a table with deferred checks pending, and
    # `transaction=True` would truncate the reference data migrations seed.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_workspace_fields_move_onto_the_product(at_before):
    registered = timezone.now()
    row = product(at_before, "Registered")
    at_before.get_model("experiences", "ProductWorkspace").objects.create(
        product_id=row.pk,
        reference="APP-2026-00001",
        experience_type="abdm",
        solution_type=["hmis"],
        applied_milestones=["ABDM:m1", "UHI:uhi1"],
        registered_at=registered,
    )
    settle()

    MigrationExecutor(connection).migrate(AFTER)

    moved = Product.objects.get(pk=row.pk)
    assert moved.reference == "APP-2026-00001"
    assert moved.experience_type == "abdm"
    assert moved.solution_type == ["hmis"]
    assert moved.applied_milestones == ["ABDM:m1", "UHI:uhi1"]
    assert moved.registered_at == registered


def test_a_product_without_a_workspace_is_given_a_reference(at_before):
    row = product(at_before, "Orphan")
    settle()

    MigrationExecutor(connection).migrate(AFTER)

    moved = Product.objects.get(pk=row.pk)
    assert moved.reference == f"PRD-{timezone.localdate().year}-{row.pk:05d}"
    assert moved.experience_type == ""
    assert moved.applied_milestones == []


def test_the_way_back_restores_the_workspace_row(at_before):
    row = product(at_before, "Registered")
    at_before.get_model("experiences", "ProductWorkspace").objects.create(
        product_id=row.pk,
        reference="APP-2026-00002",
        experience_type="abdm",
        solution_type=["eua"],
        applied_milestones=["PHR:p1"],
    )
    settle()
    MigrationExecutor(connection).migrate(AFTER)

    MigrationExecutor(connection).migrate(BEFORE)

    restored = at_before.get_model("experiences", "ProductWorkspace").objects.get(
        product_id=row.pk,
    )
    assert restored.reference == "APP-2026-00002"
    assert restored.experience_type == "abdm"
    assert restored.solution_type == ["eua"]
    assert restored.applied_milestones == ["PHR:p1"]
    assert restored.registered_at is None
