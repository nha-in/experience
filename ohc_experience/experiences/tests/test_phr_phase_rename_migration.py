"""PHR1 becoming P1, and the locker becoming P4.

A milestone key is written down in four places — the milestone row, the
application's title and metadata, the workspace's selections and the
registration form's own copy — and a rename that reaches only some of them
leaves a product whose catalog lookup raises on a key the catalog dropped.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Milestone
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BEFORE = [("experiences", "0030_drop_callback_probe_readings")]


def latest():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def product_with(apps, *, experience_type, milestones):
    """A product holding one milestone request per key, as the portal writes them."""
    organisation = OrganisationFactory.create()
    user = UserFactory.create()
    product = apps.get_model("experiences", "Product").objects.create(
        organisation_id=organisation.pk,
        name=f"Product {organisation.pk}",
        slug=f"product-{organisation.pk}",
        description="Applies for the PHR track.",
        created_by_id=user.pk,
    )
    apps.get_model("experiences", "ProductWorkspace").objects.create(
        product_id=product.pk,
        reference=f"SBX-{organisation.pk}",
        experience_type=experience_type,
        solution_type=["phr"],
        applied_milestones=[
            "HIE-CM:m1",
            *(f"{track}:{key}" for track, key in milestones),
        ],
    )
    for track, key in milestones:
        application = apps.get_model(
            "experiences",
            "ApplicationInstance",
        ).objects.create(
            reference=f"APP-{organisation.pk}-{key}",
            application_type="milestone_application",
            title=f"{track} - flows",
            product_id=product.pk,
            created_by_id=user.pk,
            status="approved",
            metadata={"milestone": key, "note": "kept"},
        )
        apps.get_model("experiences", "Milestone").objects.create(
            product_id=product.pk,
            key=key,
            application_id=application.pk,
        )
    form = apps.get_model("experiences", "FormRecord").objects.create(
        reference=f"FORM-{organisation.pk}",
        form_key="product_registration",
        name="Product registration",
        organisation_id=organisation.pk,
        product_id=product.pk,
        created_by_id=user.pk,
    )
    apps.get_model("experiences", "FormSubmission").objects.create(
        form_id=form.pk,
        form_key="product_registration",
        data={
            "name": product.name,
            "applied_milestones": [f"{track}:{key}" for track, key in milestones],
        },
        submitted_by_id=user.pk,
    )
    # Postgres refuses to ALTER a table with deferred checks pending, and
    # `transaction=True` would truncate the reference data migrations seed.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return product


def test_a_phr_request_carries_over_as_p1_and_the_locker_as_p4(at_before):
    product = product_with(
        at_before,
        experience_type="abdm",
        milestones=[("PHR", "phr1"), ("HealthLocker", "locker1")],
    )

    MigrationExecutor(connection).migrate(latest())

    keys = Milestone.objects.filter(product_id=product.pk).values_list("key", flat=True)
    assert set(keys) == {"p1", "p4"}
    workspace = ProductWorkspace.objects.get(product_id=product.pk)
    assert workspace.applied_milestones == ["HIE-CM:m1", "PHR:p1", "HealthLocker:p4"]
    assert set(
        FormSubmission.objects.get(form__product_id=product.pk).data[
            "applied_milestones"
        ],
    ) == {"PHR:p1", "HealthLocker:p4"}


def test_the_request_is_retitled_without_losing_the_rest_of_its_metadata(at_before):
    product = product_with(
        at_before,
        experience_type="abdm",
        milestones=[("PHR", "phr1")],
    )

    MigrationExecutor(connection).migrate(latest())

    application = ApplicationInstance.objects.get(product_id=product.pk)
    assert application.title == "P1 - Identity and profile"
    assert application.metadata == {"milestone": "p1", "note": "kept"}


def test_another_program_keeps_its_own_keys(at_before):
    """The keys are this catalog's, and another program may spell them its way."""
    product = product_with(
        at_before,
        experience_type="example",
        milestones=[("PHR", "phr1")],
    )

    MigrationExecutor(connection).migrate(latest())

    assert Milestone.objects.get(product_id=product.pk).key == "phr1"
