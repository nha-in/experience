"""Read access to the restored legacy database."""

from collections import defaultdict

import psycopg
from psycopg.rows import dict_row

EXIT_COLUMNS = """
    id, sd_id, final_status, admin_status, admin_coment, admin_status_date,
    final_status_date, created_date, created_at, integration_detail,
    organization_evaluate, sare_date, bridge_url, product_name, organisation_website,
    brief_on_organisation, self_declaration_id, supporting_doc_type, gstn_id,
    exempted_gst, spoc_email, company_logo_url,
    wasa_file is not null as has_wasa_file,
    host_file is not null as has_host_file,
    function_testing_file is not null as has_function_testing_file,
    suporting_doc is not null as has_suporting_doc,
    ext_wasafile, ext_hostfile, testing_file_ext, suporting_doc_ext, suporting_doc_name
"""
INLINE_FILES = {
    "wasa_file": "ext_wasafile",
    "host_file": "ext_hostfile",
    "function_testing_file": "testing_file_ext",
    "suporting_doc": "suporting_doc_ext",
}
EXIT_TABLES = {"sd_exit", "sd_exit_live"}


class Legacy:
    def __init__(self, dsn):
        self.connection = psycopg.connect(
            dsn,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on",
            autocommit=True,
        )

    def rows(self, sql, params=None):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def logins(self):
        return self.rows(
            """
            select sd_id, role_id, name, email, mobile, password, status,
                   application_type, organization, gst_no, entity_type, address,
                   register_india_status, website, ecosystem, type_of_application,
                   category, product_name, integration_level, solution_type,
                   solution_type_others, production_client_id, application_id,
                   field_detail, business_type,
                   certificate is not null as has_certificate,
                   upload_time, created_at, updated_at
            from sd_login
            """,
        )

    def roles(self):
        rows = self.rows("select role_id, role_name from mst_role")
        return {row["role_id"]: row["role_name"] for row in rows}

    def statuses(self):
        rows = self.rows(
            """
            select sd_id, final_status, admin_status, admin_comment, date, client_id,
                   gen_securate, htc1_comment, htc2_comment, htc3_comment, htc4_comment
            from sd_status where sd_id is not null
            """,
        )
        return {row["sd_id"]: row for row in rows}

    def addresses(self):
        rows = self.rows(
            """
            select sd_id, complete_address, pin_code, state_name, district_name
            from address
            """,
        )
        return {row["sd_id"]: row for row in rows}

    def exits(self):
        """Exit rows per registration.

        `sd_exit_live` is a second exit table legacy's code never names, so only
        the production-approved rows it holds alone are read.
        """
        current = self.rows(f"select {EXIT_COLUMNS}, 'sd_exit' as source from sd_exit")  # noqa: S608
        snapshot_only = self.rows(
            f"""
            select {EXIT_COLUMNS}, 'sd_exit_live' as source from sd_exit_live
            where final_status = 5 and id not in (select id from sd_exit)
            """,  # noqa: S608
        )
        grouped = defaultdict(list)
        for row in current + snapshot_only:
            grouped[row["sd_id"]].append(row)
        return grouped

    def declarations(self):
        rows = self.rows("select * from self_declaration")
        by_id = {row["id"]: row for row in rows}
        by_login = defaultdict(list)
        for row in rows:
            by_login[row["sd_id"]].append(row)
        return by_id, by_login

    def documents(self):
        rows = self.rows(
            """
            select id, exit_id, doc_type_id, file_name, file_ext, supporting_doc_type,
                   created_at, md5(files) as digest, length(files) as size
            from sd_exit_docs
            """,
        )
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["exit_id"]].append(row)
        return grouped

    def nhcx_decisions(self):
        rows = self.rows(
            """
            select sd_id, nhcx_final_status, nhcx_admin_status, nhcx_admin_comment,
                   nhcx_admin_status_updated_at, created_at
            from nhcx_exit
            """,
        )
        return {row["sd_id"]: row for row in rows}

    def wasa(self):
        rows = self.rows(
            """
            select sd_id, wasa_issue_date, wasa_expiry_date, wasa_status, milestone,
                   updated_at
            from wasa_dhis_initiation_details
            """,
        )
        return {row["sd_id"]: row for row in rows}

    def uhi(self):
        rows = self.rows(
            """
            select sd_id, intent_for_request, type_of_service, tell_us_about,
                   extra_details, created_at
            from sd_uhi
            """,
        )
        return {row["sd_id"]: row for row in rows}

    def nhcx_enrolments(self):
        rows = self.rows(
            """
            select h.sd_id, h.solution_type, h.entity_type, h.type_of_application,
                   h.registered_in_india_status, h.gst_no, h.product_name, h.website,
                   a.registered_address, a.state_name, a.district_name
            from hcx h left join hcx_address a on a.hcx_id = h.id
            """,
        )
        return {row["sd_id"]: row for row in rows}

    def wasa_history(self):
        """Columns of the per-submission WASA history legacy added in SDFI-7965."""
        rows = self.rows(
            """
            select table_name || '.' || column_name as name
            from information_schema.columns
            where table_schema = 'public'
              and (
                (table_name = 'wasa_dhis_initiation_details'
                 and column_name in ('security_agency_name', 'admin_status'))
                or (table_name = 'sd_exit_docs' and column_name = 'wasa_initiation_id')
              )
            order by 1
            """,
        )
        return [row["name"] for row in rows]

    def document_bytes(self, document_id):
        rows = self.rows("select files from sd_exit_docs where id = %s", (document_id,))
        return bytes(rows[0]["files"]) if rows else b""

    def certificate_bytes(self, sd_id):
        rows = self.rows("select certificate from sd_login where sd_id = %s", (sd_id,))
        value = rows[0]["certificate"] if rows else None
        return bytes(value) if value is not None else b""

    def inline_bytes(self, table, exit_id, column):
        if table not in EXIT_TABLES or column not in INLINE_FILES:
            msg = f"Unexpected inline file {table}.{column}"
            raise ValueError(msg)
        rows = self.rows(f"select {column} from {table} where id = %s", (exit_id,))  # noqa: S608
        value = rows[0][column] if rows else None
        return bytes(value) if value is not None else b""
