"""Stop an outside team's key from opening the API.

    python manage.py deactivate_api_caller <id>

The caller stays, so the calls it made still name it. To replace a key, add the
new one first, then deactivate the old.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ohc_experience.integrations.models import ApiCaller


class Command(BaseCommand):
    help = "Deactivate an API caller by the id add_api_caller printed."

    def add_arguments(self, parser):
        parser.add_argument("id", type=int)

    def handle(self, *args, **options):
        caller = ApiCaller.objects.filter(pk=options["id"]).first()
        if caller is None:
            msg = f"No API caller {options['id']}."
            raise CommandError(msg)
        ApiCaller.objects.filter(pk=caller.pk).update(active=False)
        self.stdout.write(f"Deactivated API caller {caller.pk} ({caller.name}).\n")
