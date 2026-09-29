from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ohc_experience.legacy_import.checks import run_checks


class Command(BaseCommand):
    help = "Check an imported database for rows the portal would not have written."

    def handle(self, **options):
        failed = run_checks(self.stdout)
        if failed:
            msg = f"{len(failed)} checks failed: {failed}"
            raise CommandError(msg)
