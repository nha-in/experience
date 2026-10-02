"""Set the landing page's ABDM figures by hand while the KPI service is unreachable.

    python manage.py set_abdm_figures --records-linked 1,22,47,14,978 \\
        --professionals 12,20,798 --facilities 5,85,761

They stay until the daily task's first successful fetch replaces them.
"""

from __future__ import annotations

from argparse import ArgumentTypeError

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ohc_experience.pages.abdm_dashboard import cached_figures
from ohc_experience.pages.abdm_dashboard import parse_count
from ohc_experience.pages.abdm_dashboard import store_figures
from ohc_experience.pages.views import indian_grouping


def _count(text: str) -> int:
    count = parse_count(text)
    if count is None:
        msg = f"{text!r} is not a count, such as 12,20,798 or 1220798."
        raise ArgumentTypeError(msg)
    return count


class Command(BaseCommand):
    help = "Set the landing page's ABDM figures until the KPI service answers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--records-linked",
            required=True,
            type=_count,
            help="ABHA linked health records created, as on the ABDM dashboard.",
        )
        parser.add_argument(
            "--professionals",
            required=True,
            type=_count,
            help="Healthcare professionals registered.",
        )
        parser.add_argument(
            "--facilities",
            required=True,
            type=_count,
            help="Health facilities registered.",
        )

    def handle(self, *args, **options):
        figures = {
            "records_linked": options["records_linked"],
            "professionals_registered": options["professionals"],
            "facilities_registered": options["facilities"],
        }
        store_figures(figures)
        # Production's cache ignores connection errors, so a write that never
        # reached Redis would otherwise look like it succeeded.
        if cached_figures() != figures:
            msg = "The cache did not keep the figures. Check that Redis is reachable."
            raise CommandError(msg)
        self.stdout.write(
            self.style.SUCCESS(
                "Landing page figures set: "
                f"{indian_grouping(figures['records_linked'])} health records linked, "
                f"{indian_grouping(figures['professionals_registered'])} "
                "professionals, "
                f"{indian_grouping(figures['facilities_registered'])} facilities.",
            ),
        )
