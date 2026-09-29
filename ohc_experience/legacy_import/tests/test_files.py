from datetime import UTC
from datetime import datetime

from ohc_experience.legacy_import import files

EARLY = datetime(2021, 1, 1, tzinfo=UTC)
LATE = datetime(2024, 1, 1, tzinfo=UTC)


def document(row_id, supporting_type, created_at):
    return {
        "id": row_id,
        "doc_type_id": files.SUPPORTING,
        "file_name": f"document-{row_id}.pdf",
        "file_ext": "pdf",
        "supporting_doc_type": supporting_type,
        "created_at": created_at,
        "digest": str(row_id),
    }


def test_a_gst_certificate_from_an_exit_beats_the_registration_certificate():
    exits = [{"id": 1, "has_suporting_doc": False}]
    documents = {1: [document(10, "GSTIN", EARLY), document(11, "PAN", LATE)]}
    certificate = files.registration_certificate({"sd_id": 5, "created_at": LATE})

    chosen = files.verification_document(exits, documents, "GSTIN", [certificate])

    assert (chosen.table, chosen.row_id) == ("sd_exit_docs", 10)


def test_the_registration_certificate_beats_any_other_exit_document():
    exits = [{"id": 1, "has_suporting_doc": False}]
    documents = {1: [document(11, "PAN", LATE)]}
    certificate = files.registration_certificate({"sd_id": 5, "created_at": EARLY})

    chosen = files.verification_document(exits, documents, "GSTIN", [certificate])

    assert (chosen.table, chosen.row_id) == ("sd_login", 5)


def test_an_organisation_without_documents_has_none():
    assert files.verification_document([], {}, "") is None


def wasa_document(row_id, created_at):
    return {**document(row_id, "", created_at), "doc_type_id": files.WASA}


def report(row_id, file_name, created_at):
    return {
        **document(row_id, "", created_at),
        "doc_type_id": files.TESTING_REPORT,
        "file_name": file_name,
    }


def test_each_milestone_takes_the_report_named_for_it():
    reports = [
        report(10, "M1 FT Report.pdf", EARLY),
        report(11, "M2_FT_Report.pdf", EARLY),
        report(12, "Summary.pdf", LATE),
    ]
    exit_row = {"id": 1, "has_function_testing_file": False}

    def chosen(key):
        return files.exit_evidence(exit_row, reports, key)["functional_report"][0]

    assert chosen("m1").row_id == reports[0]["id"]
    assert chosen("m2").row_id == reports[1]["id"]
    assert chosen("m3").row_id == reports[2]["id"]


def test_a_name_listing_several_milestones_is_for_none_of_them():
    assert files.names_milestone("M1 FT report.pdf", "m1")
    assert files.names_milestone("PHR_FT_M1.xlsx", "p2")
    assert not files.names_milestone("M1_M2_M3 FT report.pdf", "m1")
    assert not files.names_milestone("FTM10.pdf", "m1")


def test_the_newest_certificate_on_the_account_leads_whichever_exit_carried_it():
    exits = [{"id": 1}, {"id": 2}]
    documents = {1: [wasa_document(10, LATE)], 2: [wasa_document(11, EARLY)]}

    certificates = files.wasa_certificates(exits, documents)

    assert [source.row_id for source in certificates] == [10, 11]
