"""The split of "PHR App" into its four phases, and UHI's return to the menu.

A category the menu splits has to leave its holders with the same reach, or the
migration quietly takes work away from the people doing it. A track the menu
answers for again has to be expanded rather than left matching nothing.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.experiences.models import AccessGrant
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BEFORE = [("experiences", "0026_rename_sent_back_to_rejected")]
PHASES = {"phr-app-p1", "phr-app-p2", "phr-app-p3", "phr-app-p4"}


def latest():
    """The tip, so the tables match the models these assertions read through."""
    return MigrationExecutor(connection).loader.graph.leaf_nodes()


def categories(user, area):
    return set(
        AccessGrant.objects.filter(user=user, area=area).values_list(
            "category",
            flat=True,
        ),
    )


@pytest.fixture
def at_before():
    MigrationExecutor(connection).migrate(BEFORE)
    yield MigrationExecutor(connection).loader.project_state(BEFORE).apps
    MigrationExecutor(connection).migrate(latest())


def grant(apps, user, area, category, **flags):
    apps.get_model("experiences", "AccessGrant").objects.create(
        user_id=user.pk,
        program="abdm",
        area=area,
        category=category,
        can_read=True,
        **flags,
    )


def test_the_app_hands_its_holder_every_phase_it_became(at_before):
    user = UserFactory(is_nha_team=True)
    grant(at_before, user, "support", "phr-app", can_write=True)

    MigrationExecutor(connection).migrate(latest())

    assert categories(user, "support") == PHASES
    assert all(row.can_write for row in AccessGrant.objects.filter(user=user))


def test_a_track_the_menu_answers_for_again_is_expanded_not_dropped(at_before):
    """A UHI grant made by hand after the last migration now has somewhere to go."""
    user = UserFactory(is_nha_team=True)
    grant(at_before, user, "support", "UHI")
    grant(at_before, user, "support", "HealthLocker")

    MigrationExecutor(connection).migrate(latest())

    assert categories(user, "support") == {
        "uhi-integration",
        "uhi-service",
        "phr-app-p4",
    }


def test_the_catch_all_and_the_wildcard_are_left_alone(at_before):
    user = UserFactory(is_nha_team=True)
    for category in ("", "*"):
        grant(at_before, user, "support", category)

    MigrationExecutor(connection).migrate(latest())

    assert categories(user, "support") == {"", "*"}


def test_other_areas_keep_their_tracks(at_before):
    user = UserFactory(is_nha_team=True)
    grant(at_before, user, "review", "PHR")
    grant(at_before, user, "events", "UHI")

    MigrationExecutor(connection).migrate(latest())

    assert categories(user, "review") == {"PHR"}
    assert categories(user, "events") == {"UHI"}


def test_the_way_back_collapses_the_phases_into_the_app(at_before):
    user = UserFactory(is_nha_team=True)
    grant(at_before, user, "support", "phr-app")
    MigrationExecutor(connection).migrate(latest())

    MigrationExecutor(connection).migrate(BEFORE)

    assert categories(user, "support") == {"phr-app"}
