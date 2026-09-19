"""Each test rewinds part of the test database to the old "events" label and
checks the repair lands it on the current one.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connection

from ohc_experience.events_and_activities.app_label_repair import (
    repair_events_app_label,
)
from ohc_experience.events_and_activities.models import Event

pytestmark = pytest.mark.django_db


def table_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [f"public.{name}"])
        return cursor.fetchone()[0]


def migration_rows(app: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name FROM django_migrations WHERE app = %s ORDER BY name",
            [app],
        )
        return [name for (name,) in cursor.fetchall()]


def test_the_table_is_renamed_onto_the_current_label():
    with connection.cursor() as cursor:
        cursor.execute(
            'ALTER TABLE "events_and_activities_event" RENAME TO "events_event"',
        )

    repair_events_app_label(connection)

    assert table_exists("events_and_activities_event")
    assert not table_exists("events_event")


def test_migration_history_moves_onto_the_current_label():
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE django_migrations SET app = 'events'"
            " WHERE app = 'events_and_activities'",
        )

    repair_events_app_label(connection)

    assert migration_rows("events") == []
    assert "0001_initial" in migration_rows("events_and_activities")


def test_history_already_on_the_current_label_is_not_duplicated():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO django_migrations (app, name, applied)
            SELECT 'events', name, applied
              FROM django_migrations
             WHERE app = 'events_and_activities'
            """,
        )

    repair_events_app_label(connection)

    assert migration_rows("events") == []
    assert len(migration_rows("events_and_activities")) == len(
        set(migration_rows("events_and_activities")),
    )


def test_permissions_survive_the_content_type_relabel():
    content_type = ContentType.objects.get_for_model(Event)
    permission = Permission.objects.filter(content_type=content_type).first()
    ContentType.objects.filter(pk=content_type.pk).update(app_label="events")

    repair_events_app_label(connection)

    content_type.refresh_from_db()
    assert content_type.app_label == "events_and_activities"
    assert Permission.objects.get(pk=permission.pk).content_type_id == content_type.pk


def test_repair_is_a_no_op_on_a_current_database():
    before = migration_rows("events_and_activities")

    repair_events_app_label(connection)
    repair_events_app_label(connection)

    assert migration_rows("events_and_activities") == before
    assert migration_rows("events") == []
    assert table_exists("events_and_activities_event")
