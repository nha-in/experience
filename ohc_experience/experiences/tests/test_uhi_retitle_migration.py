"""UHI1 shown as UHI on the requests opened under the old code.

A request's title is written once, when it is opened, so a code the catalog
renames lives on in every earlier title unless the migration rewrites it.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

#: The events app is pinned too, or the state at BEFORE carries an Event model
#: from before `program` and `category` were added to it.
EVENTS = (
    "events_and_activities",
    "0004_rename_events_even_publish_26dca1_idx_events_and__publish_78801a_idx_and_more",
)
BEFORE = [("experiences", "0034_rename_hiecm_track_to_abdm"), EVENTS]


def latest():
    """The tip, so the next test does not start a migration behind it."""
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def product_with(apps, titles, *, experience_type="abdm"):
    """A product holding one request per milestone key, titled as given."""
    organisation = OrganisationFactory.create()
    user = UserFactory.create()
    product = apps.get_model("experiences", "Product").objects.create(
        organisation_id=organisation.pk,
        name=f"Product {organisation.pk}",
        slug=f"product-{organisation.pk}",
        description="Applies for UHI.",
        created_by_id=user.pk,
    )
    apps.get_model("experiences", "ProductWorkspace").objects.create(
        product_id=product.pk,
        reference=f"SBX-{organisation.pk}",
        experience_type=experience_type,
        solution_type=["clinical_hmis"],
        applied_milestones=["ABDM:m1", "UHI:uhi1"],
    )
    for key, title in titles.items():
        application = apps.get_model(
            "experiences",
            "ApplicationInstance",
        ).objects.create(
            reference=f"APP-{organisation.pk}-{key}",
            application_type="milestone_application",
            title=title,
            product_id=product.pk,
            created_by_id=user.pk,
            status="approved",
        )
        apps.get_model("experiences", "Milestone").objects.create(
            product_id=product.pk,
            key=key,
            application_id=application.pk,
        )
    # Postgres refuses to ALTER a table with deferred checks pending, and
    # `transaction=True` would truncate the reference data migrations seed.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return product


def titles(product):
    return dict(
        ApplicationInstance.objects.filter(product_id=product.pk).values_list(
            "milestone__key",
            "title",
        ),
    )


def test_a_uhi_request_is_retitled_and_the_others_are_left_alone(at_before):
    product = product_with(
        at_before,
        {
            "uhi1": "UHI1 - UHI participation",
            "m1": "M1 - ABHA Creation and Verification",
        },
    )

    MigrationExecutor(connection).migrate(latest())

    assert titles(product) == {
        "uhi1": "UHI - UHI participation",
        "m1": "M1 - ABHA Creation and Verification",
    }


def test_another_program_keeps_its_own_titles(at_before):
    """The code is this catalog's, and another program may spell it its way."""
    product = product_with(
        at_before,
        {"uhi1": "UHI1 - UHI participation"},
        experience_type="example",
    )

    MigrationExecutor(connection).migrate(latest())

    assert titles(product) == {"uhi1": "UHI1 - UHI participation"}
