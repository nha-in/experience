"""Requests still marked new move to under review.

"New" only said that nobody was assigned, which the assignee already says.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BEFORE = [("experiences", "0039_split_nhcx_into_roles")]
AFTER = [("experiences", "0040_submitted_requests_are_under_review")]


def latest():
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def verification(apps, status):
    organisation = OrganisationFactory.create()
    user = UserFactory.create()
    form = apps.get_model("experiences", "FormRecord").objects.create(
        reference=f"FORM-{organisation.pk}",
        form_key="organisation_verification",
        name="Organisation verification",
        reuse_scope="organisation",
        organisation_id=organisation.pk,
        created_by_id=user.pk,
    )
    return (
        apps.get_model("experiences", "ReviewItem")
        .objects.create(
            kind="organisation_verification",
            organisation_id=organisation.pk,
            form_id=form.pk,
            status=status,
        )
        .pk
    )


def test_new_requests_move_to_under_review_and_the_rest_stay(at_before):
    statuses = ["new", "in_review", "query_raised", "approved", "draft"]
    reviews = {verification(at_before, status): status for status in statuses}
    # Postgres refuses to ALTER a table with deferred checks pending.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    MigrationExecutor(connection).migrate(AFTER)

    apps = MigrationExecutor(connection).loader.project_state(AFTER).apps
    review = apps.get_model("experiences", "ReviewItem")
    assert {pk: review.objects.get(pk=pk).status for pk in reviews} == {
        pk: "in_review" if status == "new" else status for pk, status in reviews.items()
    }
