from datetime import UTC
from datetime import date
from datetime import datetime

from ohc_experience.legacy_import import clean


def test_milestone_spellings_from_every_portal_era_map_to_catalog_keys():
    value = "mileStone1,Milestone 2, M3,phr,Health Locker,HealthLocker,NHCX,M1"

    assert clean.milestone_keys(value) == [
        "m1",
        "m2",
        "m3",
        "p1",
        "p2",
        "p3",
        "p4",
        "nhcx_provider",
    ]


def test_registration_tracks_map_from_every_form_era():
    assert clean.registration_tracks(
        "ABHA Creation/Verification - M1, Building Health Information User (HIU) - M3",
    ) == ["m1", "m3"]
    assert clean.registration_tracks("hip, health_locker") == ["m2", "p4"]
    assert clean.registration_tracks("PHR App, Providers") == [
        "p1",
        "p2",
        "p3",
        "nhcx_provider",
    ]
    assert clean.registration_tracks("Payers") == ["nhcx_payer"]
    assert clean.registration_tracks("Health Repository Provider") == []


def test_a_registration_holding_its_eras_whole_list_picked_no_track():
    assert clean.registration_tracks("hip, hiu, hrp, health_locker") == []
    assert (
        clean.registration_tracks(
            "Health Info Provider, Health Info User, Health Locker, "
            "Health Repository Provider, PHR App",
        )
        == []
    )
    assert clean.registration_tracks(
        "Health Info Provider, Health Info User, Health Repository Provider",
    ) == ["m2", "m3"]


def test_milestones_are_ordered_so_predecessors_come_first():
    assert clean.ordered_milestones({"m3", "p1", "m1", "m2"}) == [
        "m1",
        "p1",
        "m2",
        "m3",
    ]


def test_legacy_solution_types_fold_onto_the_form_choices():
    solutions, unlisted = clean.solution_types(
        "PHR App, Govt Program, Payers & Providers, Hospital, Health Tech, Others",
        "Something new",
    )

    assert solutions == [
        "hmis",
        "clinical_hmis",
        "phr",
        "healthtech",
        "govt_program",
        "other",
    ]
    assert unlisted == ["Something new"]


def test_a_government_variant_keeps_its_type_and_adds_the_programme():
    solutions, unlisted = clean.solution_types("Govt HMIS, Govt PHR")

    assert solutions == ["hmis", "phr", "govt_program"]
    assert unlisted == []


def test_the_other_box_names_the_types_the_list_dropped():
    solutions, unlisted = clean.solution_types("End user applications (EUA), Providers")

    assert solutions == ["hmis"]
    assert unlisted == ["End user application (EUA)"]
    assert clean.other_solution_type(unlisted, "NA", "IMHIS") == (
        "End user application (EUA); IMHIS"
    )


def test_the_forms_whole_option_list_is_not_an_answer():
    for era in (
        (
            "Clinic HMIS,End User Applications (EUA),Govt Program,Govt HMIS,Govt PHR,"
            "Health Locker,Healthtech,HMIS,Insurance,LMIS,Payers,Pharmacy,PHR,"
            "Providers,Telemedicine"
        ),
        (
            "HMIS,LMIS,Telemedicine,PHR App,Insurance,Health Locker,Government Program,"
            "Pharmacy"
        ),
        "HMIS,LMIS,Telemedicine,PHR App,Insurance,Health Locker,Government Program",
    ):
        assert clean.every_option(era)

    # The order legacy wrote them in, and a stray Others tick, are the same list.
    assert clean.every_option(
        "HMIS,LMIS,Telemedicine,PHR App,Insurance,Health Locker,Pharmacy,"
        "Government Program,Others",
    )


def test_a_deliberate_handful_of_types_is_still_an_answer():
    assert not clean.every_option("HMIS,LMIS,Telemedicine")
    assert not clean.every_option(
        "HMIS,LMIS,Telemedicine,PHR App,Insurance,Health Locker,Pharmacy",
    )


def test_entity_types_without_a_form_choice_stay_blank():
    assert clean.entity_type("Proprietorship  Firm") == "sole_proprietor"
    assert clean.entity_type("A Section 8 registered non-profit") == "section8"
    assert clean.entity_type("Institution") == ""
    assert clean.entity_type("Organization") == ""


def test_only_well_formed_gstins_are_kept():
    assert clean.gstin("NA", " 29aaaaa0000a1z5 ") == "29AAAAA0000A1Z5"
    assert clean.gstin("27AAPFU0939F1Z") == ""


def test_websites_gain_a_scheme_and_placeholders_are_dropped():
    assert clean.website("N/A", "www.example.in") == "https://www.example.in"
    assert clean.website("not a site") == ""


def test_date_only_values_keep_their_day_whichever_midnight_legacy_stored():
    utc_midnight = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
    ist_midnight = datetime(2024, 3, 1, 0, 0, tzinfo=clean.IST)

    assert clean.ist_date(utc_midnight).isoformat() == "2024-03-01"
    assert clean.ist_date(ist_midnight).isoformat() == "2024-03-01"


def test_html_entities_and_invisible_characters_are_removed():
    assert clean.text("Arogya &amp; Co\u200b\xa0 Health") == "Arogya & Co Health"


def test_test_accounts_are_recognised_by_name_email_or_organisation():
    assert clean.is_test_account("Dummy User", "person@example.in", "")
    assert clean.is_test_account("Real Person", "qa.test1@example.in", "")
    assert not clean.is_test_account("Contessa", "contessa@example.in", "Testbed Labs")


def test_file_types_are_sniffed_from_their_bytes():
    assert clean.sniff_content_type(b"%PDF-1.7") == ("application/pdf", "pdf")
    assert clean.sniff_content_type(b"PK\x03\x04") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    )


def test_a_partnership_firm_is_its_own_entity_type():
    assert clean.entity_type("Partnership  Firm") == "partnership"
    assert clean.entity_type("partnership firm") == "partnership"
    assert clean.entity_type("Proprietorship Firm") == "sole_proprietor"


def test_a_reject_remark_is_filed_under_the_reason_it_names():
    assert clean.exit_reject_reason("Incorrect document") == "Incorrect document"
    assert (
        clean.exit_reject_reason("WASA report not uploaded") == "FT/WASA report missing"
    )
    assert (
        clean.exit_reject_reason("agency is not empaneled for FT")
        == "FT/WASA not done by an empaneled agency"
    )


def test_a_remark_naming_two_reasons_keeps_the_one_written_first():
    assert (
        clean.exit_reject_reason("Incorrect document,Incomplete integration")
        == "Incorrect document"
    )
    assert (
        clean.exit_reject_reason("Incomplete integration,Incorrect document")
        == "Incomplete integration"
    )


def test_a_reject_note_drops_the_reason_it_opens_with():
    reason = "Incorrect document"

    assert clean.reject_note("Incorrect document.", reason) == ""
    assert clean.reject_note("Incorrect document,Incomplete integration", reason) == (
        "Incomplete integration"
    )
    assert clean.reject_note("incorrect documentation", reason) == (
        "incorrect documentation"
    )
    assert clean.reject_note("Please apply again", "") == "Please apply again"


def test_a_remark_that_names_no_listed_reason_is_left_to_the_note():
    assert clean.exit_reject_reason("Please apply again") == ""
    assert clean.exit_reject_reason("") == ""


def test_an_undated_certificate_expires_a_year_after_its_upload():
    today = date(2026, 9, 24)

    assert clean.lapsed_wasa_expiry(date(2024, 5, 1), today) == date(2025, 4, 30)
    assert clean.lapsed_wasa_expiry(date(2026, 5, 1), today) is None
    assert clean.lapsed_wasa_expiry(None, today) is None


def test_a_registration_remark_is_kept_as_the_reviewer_wrote_it():
    assert clean.paragraph("OK.") == "OK."
    assert clean.paragraph(" GST verified<br>against the certificate ") == (
        "GST verified\nagainst the certificate"
    )
