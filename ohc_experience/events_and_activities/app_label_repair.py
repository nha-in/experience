"""Repair databases that still carry this app's old "events" label.

The app shipped as "events" and was renamed to "events_and_activities" after
databases had already been migrated. Django derives migration records, table
names and content types from the app label, so a database migrated before the
rename disagrees with the current code and `migrate` aborts with
InconsistentMigrationHistory.

This cannot be fixed from inside a migration. Django detects the conflict while
it loads the migration graph, which happens before any migration runs. So the
old names are normalised first and `migrate` is left to carry on as usual. The
`migrate` command in this package is what calls it.

Every step is idempotent and does nothing once a database is on the new label,
so this is equally safe on a database created before the rename, one created
after it, and a brand new one.
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
        # Foreign keys follow the table, so referencing rows stay intact.
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
    # Relabelling in place keeps the primary key, so permissions, the group and
    # user grants built on them, and admin log entries all survive.
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
    """Move any leftover "events" records onto the current app label."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        # A brand new database has nothing to repair; migrate builds it on the
        # current label.
        if not _table_exists(cursor, "django_migrations"):
            return
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        _rename_table(cursor)
        _relabel_migrations(cursor)
        _relabel_content_types(cursor)
