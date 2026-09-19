"""Move databases still on this app's old "events" label onto the current one.

A migration cannot do this: Django detects the mismatch while loading the
migration graph, before any migration runs. Idempotent.
"""

from django.db import transaction

OLD_LABEL = "events"
NEW_LABEL = "events_and_activities"
OLD_TABLE = "events_event"
NEW_TABLE = "events_and_activities_event"


def _table_exists(cursor, name):
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [f"public.{name}"])
    return cursor.fetchone()[0]


def _rename_table(cursor):
    if _table_exists(cursor, OLD_TABLE) and not _table_exists(cursor, NEW_TABLE):
        cursor.execute(f'ALTER TABLE "{OLD_TABLE}" RENAME TO "{NEW_TABLE}"')


def _relabel_migrations(cursor):
    cursor.execute(
        """
        DELETE FROM django_migrations
         WHERE app = %s
           AND name IN (SELECT name FROM django_migrations WHERE app = %s)
        """,
        [OLD_LABEL, NEW_LABEL],
    )
    cursor.execute(
        "UPDATE django_migrations SET app = %s WHERE app = %s",
        [NEW_LABEL, OLD_LABEL],
    )


def _relabel_content_types(cursor):
    if not _table_exists(cursor, "django_content_type"):
        return
    # Relabelling in place keeps the primary key, so permission grants survive.
    cursor.execute(
        """
        UPDATE django_content_type AS stale
           SET app_label = %s
         WHERE stale.app_label = %s
           AND NOT EXISTS (
                SELECT 1
                  FROM django_content_type AS current
                 WHERE current.app_label = %s
                   AND current.model = stale.model
           )
        """,
        [NEW_LABEL, OLD_LABEL, NEW_LABEL],
    )


def repair_events_app_label(connection):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        if not _table_exists(cursor, "django_migrations"):
            return
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        _rename_table(cursor)
        _relabel_migrations(cursor)
        _relabel_content_types(cursor)
