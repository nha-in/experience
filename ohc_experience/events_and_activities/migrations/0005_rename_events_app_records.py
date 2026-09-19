from django.db import migrations

# Databases that were migrated while this app was still labelled "events" carry
# the old label in their table names and content types. The migration history
# itself is claimed back by the `replaces` entries on 0001 to 0003; this
# migration repairs what those entries cannot. Every step is a no-op on a
# database that was created after the rename.

RENAME_TABLE = """
DO $$
BEGIN
    IF to_regclass('public.events_event') IS NOT NULL
        AND to_regclass('public.events_and_activities_event') IS NULL THEN
        ALTER TABLE events_event RENAME TO events_and_activities_event;
    END IF;
END $$;
"""


def rename_content_type(apps, schema_editor):
    content_types = apps.get_model("contenttypes", "ContentType").objects.using(
        schema_editor.connection.alias
    )
    if content_types.filter(app_label="events_and_activities", model="event").exists():
        return
    # Updating in place keeps the primary key, so permissions and admin log
    # entries pointing at it survive the rename.
    content_types.filter(app_label="events", model="event").update(
        app_label="events_and_activities"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        (
            "events_and_activities",
            "0004_rename_events_even_publish_26dca1_idx_events_and__publish_78801a_idx_and_more",
        ),
    ]

    operations = [
        migrations.RunSQL(RENAME_TABLE, reverse_sql=migrations.RunSQL.noop),
        migrations.RunPython(rename_content_type, migrations.RunPython.noop),
    ]
