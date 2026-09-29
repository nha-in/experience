"""Open P1 to P4 requests retitled with the documentation's names."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BEFORE = [("experiences", "0035_retitle_uhi_requests")]
AFTER = [("experiences", "0036_retitle_phr_milestones")]


def latest():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def request_titled(apps, title, *, experience_type="abdm"):
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
        applied_milestones=["PHR:p1"],
    )
    application = apps.get_model("experiences", "ApplicationInstance").objects.create(
        reference=f"APP-{organisation.pk}",
        application_type="milestone_application",
        title=title,
        product_id=product.pk,
        created_by_id=user.pk,
        status="approved",
    )
    # Postgres refuses to ALTER a table with deferred checks pending, and
    # `transaction=True` would truncate the reference data migrations seed.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return application.pk


def test_open_phr_requests_take_the_documentation_names(at_before):
    old = (
        "P1 - Identity and profile",
        "P2 - Linking and records",
        "P3 - Subscription flow",
        "P4 - Health locker",
    )
    pks = [request_titled(at_before, title) for title in old]

    MigrationExecutor(connection).migrate(AFTER)

    assert [ApplicationInstance.objects.get(pk=pk).title for pk in pks] == [
        "P1 - Registration and login",
        "P2 - Consents Management",
        "P3 - Subscription",
        "P4 - Locker",
    ]


def test_another_programs_requests_keep_their_titles(at_before):
    pk = request_titled(
        at_before,
        "P1 - Identity and profile",
        experience_type="example",
    )

    MigrationExecutor(connection).migrate(AFTER)

    assert ApplicationInstance.objects.get(pk=pk).title == "P1 - Identity and profile"
