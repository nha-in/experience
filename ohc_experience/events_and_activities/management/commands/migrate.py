"""`migrate`, with the events app rename repaired first.

Overriding the command covers every entry point: the release task, the local
start script, CI, and `manage.py migrate` by hand.
"""

from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import DEFAULT_DB_ALIAS
from django.db import connections

from ohc_experience.events_and_activities.app_label_repair import (
    repair_events_app_label,
)


class Command(MigrateCommand):
    def handle(self, *args, **options):
        database = options.get("database") or DEFAULT_DB_ALIAS
        repair_events_app_label(connections[database])
        return super().handle(*args, **options)
