"""The move of support permissions from tracks onto the support menu.

A track that the menu splits has to leave its holders with the same reach, or
the migration quietly takes work away from the people doing it. A track the
menu does not cover has to leave nothing behind, or the staff editor shows
access that no ticket can ever match.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.experiences.models import AccessGrant
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BEFORE = [("experiences", "0024_withdrawn_organisation_verifications")]
AFTER = [("experiences", "0025_support_grants_by_category")]

ABDM = {
    "abdm-m1",
    "abdm-m2",
    "abdm-m3",
    "abdm-m4",
    "abdm-review",
    "abdm-scan-share",
}


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
    MigrationExecutor(connection).migrate(AFTER)


def grant(apps, user, area, category, **flags):
    apps.get_model("experiences", "AccessGrant").objects.create(
        user_id=user.pk,
        program="abdm",
        area=area,
        category=category,
        can_read=True,
        **flags,
    )


def test_a_split_track_hands_its_holder_every_category_it_became(at_before):
    user = UserFactory(is_nha_team=True)
    grant(at_before, user, "support", "HIE-CM", can_write=True)

    MigrationExecutor(connection).migrate(AFTER)

    assert categories(user, "support") == ABDM
    assert all(row.can_write for row in AccessGrant.objects.filter(user=user))


def test_a_track_the_menu_does_not_cover_leaves_no_support_access(at_before):
    user = UserFactory(is_nha_team=True)
    for category in ("UHI", "HealthLocker"):
        grant(at_before, user, "support", category)

    MigrationExecutor(connection).migrate(AFTER)

    assert not categories(user, "support")


def test_the_catch_all_and_the_wildcard_are_left_alone(at_before):
    user = UserFactory(is_nha_team=True)
    for category in ("", "*"):
        grant(at_before, user, "support", category)

    MigrationExecutor(connection).migrate(AFTER)

    assert categories(user, "support") == {"", "*"}


def test_review_grants_keep_their_tracks(at_before):
    user = UserFactory(is_nha_team=True)
    grant(at_before, user, "review", "HIE-CM")
    grant(at_before, user, "events", "UHI")

    MigrationExecutor(connection).migrate(AFTER)

    assert categories(user, "review") == {"HIE-CM"}
    assert categories(user, "events") == {"UHI"}


def test_the_way_back_collapses_the_categories_into_their_track(at_before):
    user = UserFactory(is_nha_team=True)
    grant(at_before, user, "support", "HIE-CM")
    grant(at_before, user, "support", "PHR")
    MigrationExecutor(connection).migrate(AFTER)

    MigrationExecutor(connection).migrate(BEFORE)

    assert categories(user, "support") == {"HIE-CM", "PHR"}
