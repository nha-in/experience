from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ohc_experience.experiences.registry import get_program


class Command(BaseCommand):
    help = (
        "Seed the configured experience's development demo. --reset deletes local data."
    )

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true")
        parser.add_argument("--password", default="experience-demo-2026")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            message = "Demo seeding is only available with DEBUG enabled."
            raise CommandError(message)
        get_program().seed_demo(
            reset=options["reset"],
            password=options["password"],
            stdout=self.stdout,
            style=self.style,
        )
