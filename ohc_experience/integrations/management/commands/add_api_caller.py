"""Let an outside team call the API with a key of its own.

    python manage.py add_api_caller "HIE-CM team"

Run it in a shell on the task. The key is printed once, for the team, and only
its hash is stored.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from ohc_experience.integrations.models import ApiCaller
from ohc_experience.integrations.models import hash_key
from ohc_experience.integrations.models import new_key


class Command(BaseCommand):
    help = "Add an API caller and print its key once."

    def add_arguments(self, parser):
        parser.add_argument("name", help="The team, as the logs should name it.")

    def handle(self, *args, **options):
        key = new_key()
        caller = ApiCaller.objects.create(name=options["name"], key_hash=hash_key(key))
        self.stdout.write(f"Added API caller {caller.pk} ({caller.name}).\n")
        self.stdout.write(f"Key, shown only now:\n{key}\n")
