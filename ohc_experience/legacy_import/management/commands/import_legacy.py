import csv
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from ohc_experience.experiences.models import Product
from ohc_experience.legacy_import.checks import run_checks
from ohc_experience.legacy_import.checks import target_drift
from ohc_experience.legacy_import.clean import IST
from ohc_experience.legacy_import.files import LEGACY_FOLDER
from ohc_experience.legacy_import.source import Legacy
from ohc_experience.legacy_import.writer import Importer
from ohc_experience.organisations.models import Organisation

#: The reports go to storage as well as to disk: the task that writes them keeps
#: nothing, so in production this is the only copy that outlives the run.
REPORT_FOLDER = "legacy_data"


class Command(BaseCommand):
    help = "Import the legacy ABDM sandbox into an empty experience database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--legacy-dsn",
            default=os.environ.get("LEGACY_DATABASE_URL", ""),
        )
        parser.add_argument(
            "--report-dir",
            type=Path,
            default=Path("legacy-import-report"),
        )
        parser.add_argument("--no-files", action="store_true")
        parser.add_argument(
            "--password",
            default="",
            help=(
                "Give every imported account this password, so the team can sign in "
                "as any of them. For a rehearsal, never the live run."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Import only the first N organisations.",
        )

    def keep_report(self, directory, started_at):
        """Put the run's CSVs where they can be read after the task is gone."""
        folder = f"{REPORT_FOLDER}/{started_at.astimezone(IST):%Y-%m-%d-%H%M}"
        try:
            saved = upload_report(directory, folder)
        except Exception as error:  # noqa: BLE001 - a lost report is not a failed import
            self.stderr.write(
                self.style.WARNING(
                    f"The report is in {directory} but could not be stored under "
                    f"{folder}: {type(error).__name__}: {error}",
                ),
            )
            return
        self.stdout.write(f"Report in {directory} and in {folder} ({len(saved)} files)")

    def handle(self, **options):
        problem = refusal(options)
        if problem:
            raise CommandError(problem)

        started, started_at = time.monotonic(), timezone.now()
        self.stdout.write("Migrating")
        call_command("migrate", verbosity=0)
        importer = Importer(
            Legacy(options["legacy_dsn"]),
            store_files=not options["no_files"],
            stdout=self.stdout,
            password=options["password"],
        )
        self.stdout.write("Reading the legacy database")
        importer.load()
        self.stdout.write("Importing staff")
        importer.import_staff()
        registrations = importer.select_valid_integrator_users()
        self.stdout.write(f"Importing users for {len(registrations)} registrations")
        users = importer.import_users(registrations)
        self.stdout.write("Importing organisations and products")
        importer.import_all(registrations, users, options["limit"])

        report = importer.report
        report.counts["bytes of files written"] = importer.files.bytes_written
        write_report(report, options["report_dir"])
        self.keep_report(options["report_dir"], started_at)
        for name, count in sorted(report.counts.items()):
            self.stdout.write(f"  {name}: {count}")
        self.stdout.write(f"Imported in {time.monotonic() - started:.0f}s")
        failed = run_checks(self.stdout)
        if report.review.get("failed organisations"):
            msg = "Some organisations failed; see review-failed-organisations.csv."
            raise CommandError(msg)
        if failed:
            msg = f"{len(failed)} checks failed: {failed}"
            raise CommandError(msg)


def upload_report(directory, folder):
    """Copy a run's CSVs into storage, and say what they were stored as."""
    saved = []
    for path in sorted(directory.glob("*.csv")):
        with path.open("rb") as handle:
            saved.append(default_storage.save(f"{folder}/{path.name}", File(handle)))
    return saved


def refusal(options):  # noqa: PLR0911
    if not options["legacy_dsn"]:
        return "Pass --legacy-dsn or set LEGACY_DATABASE_URL."
    if not settings.EXPERIENCE_CREDENTIAL_KEY:
        return "Set EXPERIENCE_CREDENTIAL_KEY; the final run needs production's key."
    drift = target_drift()
    if drift:
        moved = "\n  ".join(drift)
        return f"This checkout has moved on from what the import writes:\n  {moved}"
    history = Legacy(options["legacy_dsn"]).wasa_history()
    if history:
        return (
            "This dump has legacy's per-submission WASA history (SDFI-7965: "
            f"{', '.join(history)}), which the import does not read yet."
        )
    tables = set(connection.introspection.table_names())
    models = (Organisation, Product)
    if {model._meta.db_table for model in models} <= tables and any(  # noqa: SLF001
        model.objects.exists() for model in models
    ):
        return "The database already has organisations or products."
    if not options["no_files"] and _files_already_written():
        return f"Storage already holds files under {LEGACY_FOLDER}."
    return ""


def _files_already_written():
    try:
        folders, files = default_storage.listdir(LEGACY_FOLDER)
    except FileNotFoundError:
        return False
    return bool(folders or files)


def write_report(report, directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "counts.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["measure", "count"])
        writer.writerows(sorted(report.counts.items()))
    with (directory / "skipped.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["legacy_table", "legacy_key", "reason"])
        writer.writerows(report.skipped)
    with (directory / "mapping.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["legacy_table", "legacy_key", "experience_model", "experience_pk"],
        )
        writer.writerows(report.mapping)
    for name, rows in report.review.items():
        if not rows:
            continue
        path = directory / f"review-{name.replace(' ', '-')}.csv"
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
