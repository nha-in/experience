"""The whole cutover as one command: reset, restore the dump, import it.

Everything here was a step in a runbook once, and every step that bit us is a
step a command can take itself: the storage prefix, the roles the dump expects,
the privileges they need, the database a failed attempt left behind.
"""

import gzip
import os
import re
import shutil
import subprocess
from importlib import import_module
from pathlib import Path

import psycopg
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from ohc_experience.experiences.models import CertificationAgency
from ohc_experience.legacy_import.files import LEGACY_FOLDER
from ohc_experience.legacy_import.management.commands.import_legacy import unusable_key

#: Names a dump's grants may carry that are not roles to create.
NOT_A_ROLE = frozenset(
    {"public", "postgres", "current_user", "session_user", "none", "group"},
)
#: What a restored dump should hold, printed so the run can be compared.
LEGACY_TABLES = (
    "sd_login",
    "sd_status",
    "sd_exit",
    "self_declaration",
    "sd_exit_docs",
    "hcx",
    "nhcx_exit",
    "sd_uhi",
)
#: The only reference rows a migration seeded rather than derived, so the only
#: ones `flush` takes away for good. M2 cannot be submitted without them: the
#: form's agency choices are these rows.
WASA_SEED = (
    "ohc_experience.experiences.migrations.0008_seed_wasa_certification_agencies"
)
#: The role a statement expects, after the word that introduces it.
NAMED_ROLE = re.compile(
    r"\b(?:OWNER TO|FOR ROLE|GRANTED BY|TO|FROM)\s+(\"?[A-Za-z_][\w$]*\"?)",
)


def roles_named(path):
    """Every role the dump's own statements expect to exist already.

    `psql` stops at the first role Postgres has never heard of, and ownership,
    grants and default privileges each name one. Data is skipped: a product
    description is free to begin with the word GRANT.
    """
    found = set()
    with gzip.open(path, "rt", errors="replace") as dump:
        copying = False
        for line in dump:
            if copying:
                copying = line.rstrip("\n") != "\\."
            elif line.startswith("COPY ") and line.rstrip().endswith("FROM stdin;"):
                copying = True
            elif line.startswith(("ALTER ", "GRANT ", "REVOKE ")):
                found.update(
                    name.strip('"')
                    for name in NAMED_ROLE.findall(line)
                    if name.strip('"').lower() not in NOT_A_ROLE
                )
    return sorted(found)


def libpq_environment():
    """Put the portal's own database settings where psql and psycopg read them."""
    database = settings.DATABASES["default"]
    for name, value in (
        ("PGHOST", database["HOST"]),
        ("PGPORT", str(database["PORT"] or "5432")),
        ("PGUSER", database["USER"]),
        ("PGPASSWORD", database["PASSWORD"]),
    ):
        if value:
            os.environ[name] = value


def run_sql(dsn, statements):
    """Each statement on its own connection, so one refusal does not undo the rest."""
    for statement in statements:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(statement)


def table_counts(dsn):
    with psycopg.connect(dsn) as connection:
        return {
            table: connection.execute(f"select count(*) from {table}").fetchone()[0]  # noqa: S608
            for table in LEGACY_TABLES
        }


class Command(BaseCommand):
    help = "Reset, restore the legacy dump and import it, in one run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dump-key",
            required=True,
            help="The dump's key in the storage bucket, at its root, not under media/.",
        )
        parser.add_argument("--legacy-db", default="sandbox_legacy")
        parser.add_argument(
            "--admin-dsn",
            default="dbname=postgres",
            help="Where to create the database and its roles, if not as the app user.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Empty the portal first: every row, and the imported files.",
        )
        parser.add_argument(
            "--cleanup",
            action="store_true",
            help="Drop the legacy database and the roles this run created.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Prove the image and the environment, change nothing, and stop.",
        )
        parser.add_argument(
            "--site-domain",
            default="",
            help="The portal's own domain, which flush resets to example.com.",
        )
        parser.add_argument("--password", default="")
        parser.add_argument("--no-files", action="store_true")
        parser.add_argument(
            "--report-dir",
            type=Path,
            default=Path("legacy-import-report"),
        )

    def say(self, message):
        self.stdout.write(message)

    def handle(self, **options):
        libpq_environment()
        self.preflight(options)
        if options["check"]:
            self.report(options)
            return
        dump = self.fetch(options["dump_key"])
        if options["reset"]:
            self.reset(options)
        created = self.prepare_roles(dump, options["admin_dsn"])
        self.restore(dump, options)
        call_command(
            "import_legacy",
            legacy_dsn=f"dbname={options['legacy_db']}",
            password=options["password"],
            no_files=options["no_files"],
            report_dir=options["report_dir"],
            limit=None,
        )
        if options["cleanup"]:
            self.clean_up(options, created)

    def preflight(self, options):
        """Refuse before anything is touched, naming what to fix."""
        problems = [
            f"{tool} is not on PATH; this image cannot restore a dump."
            for tool in ("psql", "gunzip")
            if not shutil.which(tool)
        ]
        key = unusable_key()
        if key:
            problems.append(key)
        database = self.unusable_database(options)
        if database:
            problems.append(database)
        if not options["reset"] and self.held_files():
            problems.append(
                f"Storage already holds files under {LEGACY_FOLDER}; pass --reset.",
            )
        if problems:
            raise CommandError("\n  ".join(["", *problems]))

    def unusable_database(self, options):
        """Why the restore's own connection cannot work, or "" when it can.

        One connection answers all three: that it can be made at all, that this
        user may create a database, and that no earlier attempt left one behind.
        """
        try:
            with psycopg.connect(options["admin_dsn"]) as connection:
                allowed = connection.execute(
                    "select rolcreatedb or rolsuper from pg_roles "
                    "where rolname = current_user",
                ).fetchone()
                held = connection.execute(
                    "select 1 from pg_database where datname = %s",
                    [options["legacy_db"]],
                ).fetchone()
        except Exception as error:  # noqa: BLE001 - every failure is the same answer
            return (
                f"{options['admin_dsn']} cannot be reached: "
                f"{type(error).__name__}: {error}"
            )
        if not (allowed and allowed[0]):
            return (
                "This database user cannot create a database, which the restore needs."
            )
        if held and not options["reset"]:
            return (
                f'The database "{options["legacy_db"]}" is left from an earlier run; '
                "pass --reset, or --legacy-db for another name."
            )
        return ""

    def report(self, options):
        """What the run would use, for a last look before half an hour starts."""
        try:
            client = default_storage.bucket.meta.client
            size = client.head_object(
                Bucket=default_storage.bucket_name,
                Key=options["dump_key"],
            )["ContentLength"]
        except Exception as error:
            msg = (
                f"{options['dump_key']} cannot be read from "
                f"{default_storage.bucket_name}: {type(error).__name__}: {error}"
            )
            raise CommandError(msg) from error
        version = subprocess.run(
            ["psql", "--version"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        site = Site.objects.filter(pk=settings.SITE_ID).first()
        lines = (
            f"Dump: {options['dump_key']}, {size} bytes",
            f"Free in /tmp: {shutil.disk_usage('/tmp').free} bytes",  # noqa: S108
            f"Client: {version}",
            f"Email backend: {settings.EMAIL_BACKEND}",
            (
                f"Site domain: {site.domain if site else 'no site row'}"
                f" -> {options['site_domain'] or 'left as it is'}"
            ),
            f"WASA agencies held: {CertificationAgency.objects.count()}",
            f"One shared password: {'yes' if options['password'] else 'no'}",
        )
        for line in lines:
            self.say(line)
        self.say("Nothing was changed. The run has everything it needs.")

    def held_files(self):
        """The imported files storage still holds, by the path the import checks.

        `default_storage` paths are relative to its own location, so this is the
        one reading that cannot disagree with the import's own refusal.
        """
        return list(self.stored_files(LEGACY_FOLDER))

    def stored_files(self, path):
        directories, files = default_storage.listdir(path)
        for name in files:
            yield f"{path}/{name}"
        for name in directories:
            yield from self.stored_files(f"{path}/{name}")

    def fetch(self, key):
        """The dump on local disk, downloaded through the storage's own client."""
        local = Path("/tmp") / Path(key).name  # noqa: S108
        client = default_storage.bucket.meta.client
        size = client.head_object(Bucket=default_storage.bucket_name, Key=key)[
            "ContentLength"
        ]
        free = shutil.disk_usage(local.parent).free
        if free < size * 1.1:
            msg = f"{local.parent} holds {free} bytes free, and the dump needs {size}."
            raise CommandError(msg)
        if local.exists() and local.stat().st_size == size:
            self.say(f"Dump already downloaded: {local}")
            return local
        self.say(f"Downloading {key} ({size} bytes)")
        client.download_file(default_storage.bucket_name, key, str(local))
        return local

    def reset(self, options):
        """Empty the portal: the import refuses to write over an earlier run."""
        self.say("Emptying the portal database (every user goes, superusers included)")
        call_command("flush", "--noinput")
        held = self.held_files()
        self.say(f"Deleting {len(held)} imported files from storage")
        for name in held:
            default_storage.delete(name)
        run_sql(
            options["admin_dsn"],
            [f'drop database if exists "{options["legacy_db"]}"'],
        )
        self.reseed(options["site_domain"])

    def reseed(self, domain):
        """Put back what flush truncates and no later step writes again.

        The agencies have to be here before the import reads them: it spells a
        legacy agency name the portal's way, or leaves it as legacy wrote it.
        """
        seeded = import_module(WASA_SEED).LEGACY_WASA_AGENCIES
        CertificationAgency.objects.bulk_create(
            [
                CertificationAgency(program="abdm", name=name, sort_order=order)
                for order, name in enumerate(seeded)
            ],
            ignore_conflicts=True,
        )
        self.say(f"WASA agencies: {CertificationAgency.objects.count()}")
        if domain:
            Site.objects.filter(pk=settings.SITE_ID).update(domain=domain, name=domain)
        site = Site.objects.filter(pk=settings.SITE_ID).first()
        self.say(f"Site domain: {site.domain if site else 'no site row'}")

    def prepare_roles(self, dump, admin_dsn):
        """Create the roles the dump names, and join those that will have us.

        A role that does not exist stops the restore. A role the restoring user
        is not a member of stops it later, at the default privileges. RDS keeps
        `rdsadmin` to itself, and no one can be made a member of it: the grant
        is what the dump may need, not what it must have, so a refusal is said
        out loud and the restore is left to answer for itself.
        """
        self.say("Reading the roles the dump expects")
        wanted = roles_named(dump)
        with psycopg.connect(admin_dsn) as connection:
            held = {
                name for (name,) in connection.execute("select rolname from pg_roles")
            }
        missing = [name for name in wanted if name not in held]
        self.say(f"Roles named: {', '.join(wanted) or 'none'}")
        if missing:
            self.say(f"Creating: {', '.join(missing)}")
        run_sql(admin_dsn, [f'create role "{name}" nologin' for name in missing])
        for name in wanted:
            try:
                run_sql(admin_dsn, [f'grant "{name}" to current_user'])
            except psycopg.Error as error:
                self.say(f"Not joined: {name}: {str(error).strip()}")
        return missing

    def restore(self, dump, options):
        name = options["legacy_db"]
        run_sql(options["admin_dsn"], [f'create database "{name}"'])
        self.say(f"Restoring into {name}; a failure rolls the whole dump back")
        read = subprocess.Popen(["gunzip", "-c", str(dump)], stdout=subprocess.PIPE)  # noqa: S603, S607
        load = subprocess.run(  # noqa: S603
            ["psql", "-v", "ON_ERROR_STOP=1", "--single-transaction", "-d", name],  # noqa: S607
            stdin=read.stdout,
            check=False,
        )
        read.stdout.close()
        read.wait()
        if load.returncode:
            msg = f"The restore failed and rolled back; {name} is empty."
            raise CommandError(msg)
        counts = table_counts(f"dbname={name}")
        for table, count in counts.items():
            self.say(f"  {table}: {count}")
        if not counts["sd_login"]:
            msg = "The restore left no logins; the dump is not the one we expect."
            raise CommandError(msg)

    def clean_up(self, options, created):
        self.say("Dropping the legacy database and the roles this run created")
        run_sql(
            options["admin_dsn"],
            [f'drop database if exists "{options["legacy_db"]}"']
            + [f'drop role if exists "{name}"' for name in created],
        )
