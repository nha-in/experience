"""NHCX1 requests move to the Payer or Provider role.

The catalog no longer knows `nhcx1`, so a product still holding one, even
disabled, fails wherever its milestones are looked up.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.experiences.models import Milestone
from ohc_experience.experiences.models import Product
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BEFORE = [("experiences", "0038_constraint_error_messages")]
AFTER = [("experiences", "0039_split_nhcx_into_roles")]


def latest():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def product_with(apps, solution_type, keys, *, nhcx_on, nhcx_enabled=True):
    """A product holding a request per key, and NHCX1 built on `nhcx_on`."""
    organisation = OrganisationFactory.create()
    user = UserFactory.create()
    selections = [f"ABDM:{key}" for key in keys if key.startswith("m")]
    selections += [f"PHR:{key}" for key in keys if key.startswith("p")]
    product = apps.get_model("experiences", "Product").objects.create(
        organisation_id=organisation.pk,
        name=f"Product {organisation.pk}",
        slug=f"product-{organisation.pk}",
        description="Applies for NHCX.",
        created_by_id=user.pk,
        reference=f"APP-2026-{organisation.pk:05d}",
        experience_type="abdm",
        solution_type=solution_type,
        applied_milestones=[*selections, "NHCX:nhcx1"] if nhcx_enabled else selections,
    )
    applications = {}
    for key in (*keys, "nhcx1"):
        application = apps.get_model(
            "experiences",
            "ApplicationInstance",
        ).objects.create(
            reference=f"REQ-{organisation.pk}-{key}",
            application_type="milestone_application",
            title="NHCX1 - Claims exchange flows" if key == "nhcx1" else key,
            product_id=product.pk,
            created_by_id=user.pk,
            metadata={"milestone": key},
        )
        apps.get_model("experiences", "Milestone").objects.create(
            product_id=product.pk,
            key=key,
            application_id=application.pk,
            enabled=key != "nhcx1" or nhcx_enabled,
        )
        applications[key] = application
    apps.get_model("experiences", "ApplicationDependency").objects.create(
        application_id=applications["nhcx1"].pk,
        depends_on_id=applications[nhcx_on].pk,
    )
    # Postgres refuses to ALTER a table with deferred checks pending, and
    # `transaction=True` would truncate the reference data migrations seed.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return product.pk


def nhcx(pk):
    milestone = Milestone.objects.select_related("application").get(
        product_id=pk,
        key__startswith="nhcx",
    )
    application = milestone.application
    return (
        milestone.key,
        application.title,
        application.metadata["milestone"],
        sorted(application.dependencies.values_list("milestone__key", flat=True)),
    )


def test_an_insurance_product_becomes_a_payer_on_m1_and_m3(at_before):
    pk = product_with(at_before, ["insurance"], ["m1", "m3"], nhcx_on="m1")

    MigrationExecutor(connection).migrate(AFTER)

    assert nhcx(pk) == (
        "nhcx_payer",
        "Payer - Claims exchange as a payer",
        "nhcx_payer",
        ["m1", "m3"],
    )
    assert Product.objects.get(pk=pk).applied_milestones[-1] == "NHCX:nhcx_payer"


def test_any_other_product_becomes_a_provider_on_what_it_has(at_before):
    """A provider needs M1 and M2; a product without M2 waits on M1 alone."""
    pk = product_with(at_before, ["hmis"], ["m1", "m2", "m3"], nhcx_on="m1")
    without_m2 = product_with(at_before, ["telemedicine"], ["m1"], nhcx_on="m1")

    MigrationExecutor(connection).migrate(AFTER)

    assert nhcx(pk)[0] == "nhcx_provider"
    assert nhcx(pk)[3] == ["m1", "m2"]
    assert nhcx(without_m2)[3] == ["m1"]


def test_nhcx1_on_a_phr_product_becomes_a_patient_app_on_p1(at_before):
    """The demo's PHR App: NHCX was ticked and then dropped, leaving NHCX1 disabled."""
    pk = product_with(
        at_before,
        ["phr"],
        ["p1", "p2", "p3"],
        nhcx_on="p1",
        nhcx_enabled=False,
    )

    MigrationExecutor(connection).migrate(AFTER)

    assert nhcx(pk) == (
        "nhcx_patient_app",
        "Patient app - Claim updates in a patient app",
        "nhcx_patient_app",
        ["p1"],
    )
    assert not Milestone.objects.get(product_id=pk, key="nhcx_patient_app").enabled
    assert Product.objects.get(pk=pk).applied_milestones == [
        "PHR:p1",
        "PHR:p2",
        "PHR:p3",
    ]


def test_a_phr_product_on_nhcx_keeps_nhcx_selected_as_a_patient_app(at_before):
    pk = product_with(at_before, ["phr"], ["p1", "p2", "p3"], nhcx_on="p1")

    MigrationExecutor(connection).migrate(AFTER)

    assert nhcx(pk)[0] == "nhcx_patient_app"
    assert Product.objects.get(pk=pk).applied_milestones == [
        "PHR:p1",
        "PHR:p2",
        "PHR:p3",
        "NHCX:nhcx_patient_app",
    ]
