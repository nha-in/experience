"""The ABDM track becoming HIE-CM again.

The inverse of 0034, and as with it a track code is written down in four places:
the product's selections, the registration form's own copy, the grants that
hand a reviewer the track, and the events filed under it. A rename reaching
only some of them leaves a product whose catalog lookup raises on a track the
catalog no longer has, or a reviewer holding access to a track nothing matches.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Product
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

EVENTS = ("events_and_activities", "0005_constraint_error_messages")
BEFORE = [("experiences", "0040_submitted_requests_are_under_review"), EVENTS]
AFTER = [("experiences", "0041_rename_abdm_track_to_hiecm"), EVENTS]


def latest():
    """The tip, so the next test does not start a migration behind it."""
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def product_on_the_old_track(apps, *, experience_type="abdm"):
    """A product applied for ABDM M1 and UHI, as the portal wrote them."""
    organisation = OrganisationFactory.create()
    user = UserFactory.create()
    product = apps.get_model("experiences", "Product").objects.create(
        organisation_id=organisation.pk,
        name=f"Product {organisation.pk}",
        slug=f"product-{organisation.pk}",
        description="Applies for the ABDM track.",
        created_by_id=user.pk,
        reference=f"SBX-2026-{organisation.pk:05d}",
        experience_type=experience_type,
        solution_type=["clinical_hmis"],
        applied_milestones=["ABDM:m1", "ABDM:m2", "UHI:uhi1"],
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
            "applied_milestones": ["ABDM:m1", "ABDM:m2", "UHI:uhi1"],
        },
        submitted_by_id=user.pk,
    )
    # Postgres refuses to ALTER a table with deferred checks pending, and
    # `transaction=True` would truncate the reference data migrations seed.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return product


def selections(product):
    return Product.objects.get(pk=product.pk).applied_milestones


def grant(apps, user, area, category, *, program="abdm"):
    return apps.get_model("experiences", "AccessGrant").objects.create(
        user_id=user.pk,
        program=program,
        area=area,
        category=category,
        can_read=True,
    )


def categories(user, area):
    return set(
        AccessGrant.objects.filter(user=user, area=area).values_list(
            "category",
            flat=True,
        ),
    )


def test_a_products_selections_and_its_registration_copy_move_together(at_before):
    product = product_on_the_old_track(at_before)

    MigrationExecutor(connection).migrate(AFTER)

    assert selections(product) == ["HIE-CM:m1", "HIE-CM:m2", "UHI:uhi1"]
    submission = FormSubmission.objects.get(form__product_id=product.pk)
    assert submission.data["applied_milestones"] == [
        "HIE-CM:m1",
        "HIE-CM:m2",
        "UHI:uhi1",
    ]
    assert submission.data["name"] == product.name


def test_the_selections_of_another_program_are_left_where_they_are(at_before):
    product = product_on_the_old_track(at_before, experience_type="equipment")

    MigrationExecutor(connection).migrate(AFTER)

    assert selections(product) == ["ABDM:m1", "ABDM:m2", "UHI:uhi1"]


def test_review_and_events_grants_follow_the_track_and_support_does_not(at_before):
    user = UserFactory(is_nha_team=True)
    grant(at_before, user, "review", "ABDM")
    grant(at_before, user, "events", "ABDM")
    grant(at_before, user, "review", "UHI")
    # Support is held by support category; "ABDM" there is a code a ticket
    # was filed under, not a track this rename has any say over.
    grant(at_before, user, "support", "ABDM")

    MigrationExecutor(connection).migrate(AFTER)

    assert categories(user, "review") == {"HIE-CM", "UHI"}
    assert categories(user, "events") == {"HIE-CM"}
    assert categories(user, "support") == {"ABDM"}


def test_an_event_filed_under_the_track_is_refiled(at_before):
    user = UserFactory(is_nha_team=True)
    at_before.get_model("events_and_activities", "Event").objects.create(
        title="HIE-CM integration office hours",
        slug="hiecm-office-hours",
        program="abdm",
        category="ABDM",
        kind="event",
        summary="A working session.",
        description="Bring your questions.",
        starts_at="2026-01-01T10:00:00Z",
        ends_at="2026-01-01T11:00:00Z",
        created_by_id=user.pk,
    )

    MigrationExecutor(connection).migrate(AFTER)

    assert Event.objects.get(slug="hiecm-office-hours").category == "HIE-CM"


def test_the_way_back_returns_the_track_to_its_old_code(at_before):
    user = UserFactory(is_nha_team=True)
    product = product_on_the_old_track(at_before)
    grant(at_before, user, "review", "ABDM")
    MigrationExecutor(connection).migrate(AFTER)

    MigrationExecutor(connection).migrate(BEFORE)

    assert selections(product) == ["ABDM:m1", "ABDM:m2", "UHI:uhi1"]
    assert categories(user, "review") == {"ABDM"}
