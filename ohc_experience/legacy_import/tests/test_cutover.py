import gzip

from cryptography.fernet import Fernet

from ohc_experience.legacy_import.management.commands.cutover import roles_named
from ohc_experience.legacy_import.management.commands.import_legacy import unusable_key

# ruff: noqa: E501
#: A dump shaped like legacy's: owners, grants, default privileges, and data
#: whose first column happens to read like SQL.
DUMP = """\
SET statement_timeout = 0;
CREATE TABLE public.sd_login (sd_id bigint);
ALTER TABLE public.sd_login OWNER TO appprdusrsandbox;
COPY public.sd_exit (organization_evaluate, sd_id) FROM stdin;
GRANT Thornton Bharat LLP TO auditteam\t7
ALTER course OWNER TO someone else\t8
\\.
GRANT SELECT ON TABLE public.sd_login TO nharanalyst;
GRANT ALL ON SCHEMA public TO PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE appprdusrsandbox GRANT SELECT ON TABLES TO "sandboxportaluser";
REVOKE ALL ON FUNCTION public.f() FROM PUBLIC;
"""


def dump_file(tmp_path, text):
    path = tmp_path / "dump.sql.gz"
    with gzip.open(path, "wt") as handle:
        handle.write(text)
    return path


def test_every_role_a_dump_expects_is_read_from_its_own_statements(tmp_path):
    assert roles_named(dump_file(tmp_path, DUMP)) == [
        "appprdusrsandbox",
        "nharanalyst",
        "sandboxportaluser",
    ]


def test_data_that_reads_like_sql_names_no_role(tmp_path):
    """A product name beginning with GRANT would otherwise invent a role."""
    data = (
        "COPY public.sd_exit (organization_evaluate) FROM stdin;\n"
        "GRANT Thornton Bharat LLP TO auditteam\n"
        "\\.\n"
    )

    assert roles_named(dump_file(tmp_path, data)) == []


def test_a_dump_that_names_nobody_asks_for_nothing(tmp_path):
    assert roles_named(dump_file(tmp_path, "CREATE TABLE x (a int);\n")) == []


def test_a_key_that_is_only_set_is_refused_before_the_run(settings):
    """It cost 5,969 organisations once: the key is proved, not just present."""
    settings.EXPERIENCE_CREDENTIAL_KEY = "looks-like-a-key"

    assert "not a usable Fernet key" in unusable_key()


def test_a_real_key_passes(settings):
    settings.EXPERIENCE_CREDENTIAL_KEY = Fernet.generate_key().decode()

    assert unusable_key() == ""


def test_no_key_at_all_says_so(settings):
    settings.EXPERIENCE_CREDENTIAL_KEY = ""
    settings.EXPERIENCE_ALLOW_INSECURE_DEMO_KEY = False

    assert "Set EXPERIENCE_CREDENTIAL_KEY" in unusable_key()
