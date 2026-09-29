from datetime import UTC
from datetime import datetime
from itertools import count

from ohc_experience.legacy_import.organisations import group_integrators
from ohc_experience.legacy_import.organisations import read_registrants

ids = count(1)
TWO_ORGANISATIONS = 2


def registration(email, organisation, **values):
    return {
        "sd_id": next(ids),
        "email": email,
        "application_type": "3" if organisation else "2",
        "organization": organisation,
        "gst_no": "",
        "website": "",
        "mobile": "",
        "address": "",
        "product_name": "",
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
        **values,
    }


def integrators_for(*rows):
    registrants = read_registrants(rows, addresses={}, exits={}, enrolments={})
    return sorted(
        (sorted(row["sd_id"] for row in integrator.rows), integrator.linked_by)
        for integrator in group_integrators(registrants)
    )


def test_colleagues_registering_one_company_with_its_gstin_share_an_organisation():
    first = registration("a@acme.in", "Acme Health", gst_no="29AAAAA0000A1Z5")
    second = registration("b@gmail.com", "ACME HEALTH", gst_no="29AAAAA0000A1Z5")

    assert integrators_for(first, second) == [
        ([first["sd_id"], second["sd_id"]], ["GSTIN"]),
    ]


def test_the_same_name_with_nothing_else_in_common_stays_separate():
    first = registration("a@gmail.com", "City Clinic")
    second = registration("b@gmail.com", "City Clinic")

    assert integrators_for(first, second) == [
        ([first["sd_id"]], []),
        ([second["sd_id"]], []),
    ]


def test_names_differing_only_by_legal_suffix_merge_on_a_shared_website():
    first = registration(
        "a@x.in",
        "Acme Health Pvt Ltd",
        website="https://acmehealth.in",
    )
    second = registration(
        "b@y.in",
        "Acme Health Private Limited",
        website="acmehealth.in/about",
    )

    assert integrators_for(first, second) == [
        ([first["sd_id"], second["sd_id"]], ["website"]),
    ]


def test_two_companies_under_one_name_but_no_shared_pan_stay_separate():
    first = registration(
        "a@x.in",
        "Acme Health",
        gst_no="29AAAAA0000A1Z5",
        website="https://acmehealth.in",
    )
    second = registration(
        "b@y.in",
        "Acme Health",
        gst_no="29BBBBB1111B1Z5",
        website="acmehealth.in/about",
    )

    assert len(integrators_for(first, second)) == TWO_ORGANISATIONS


def test_one_company_registered_in_two_states_still_merges():
    first = registration(
        "a@x.in",
        "Acme Health",
        gst_no="29AAAAA0000A1Z5",
        website="https://acmehealth.in",
    )
    second = registration(
        "b@y.in",
        "Acme Health",
        gst_no="27AAAAA0000A1Z5",
        website="acmehealth.in/about",
    )

    assert integrators_for(first, second) == [
        ([first["sd_id"], second["sd_id"]], ["website"]),
    ]


def test_one_login_holding_two_companies_keeps_them_together():
    first = registration(
        "owner@acme.in",
        "Acme Health Pvt Ltd",
        gst_no="29AAAAA0000A1Z5",
    )
    second = registration(
        "owner@acme.in",
        "Acme Health Private Limited",
        gst_no="29BBBBB1111B1Z5",
    )

    assert integrators_for(first, second) == [
        ([first["sd_id"], second["sd_id"]], ["email domain", "same email"]),
    ]


def test_one_weak_signal_is_not_enough_to_merge():
    first = registration("a@x.in", "City Clinic", mobile="9000000001")
    second = registration("b@y.in", "City Clinic", mobile="9000000001")

    assert len(integrators_for(first, second)) == TWO_ORGANISATIONS


def test_two_weak_signals_agreeing_do_merge():
    first = registration(
        "a@x.in",
        "City Clinic",
        mobile="9000000001",
        product_name="Clinic Connect",
    )
    second = registration(
        "b@y.in",
        "City Clinic",
        mobile="9000000001",
        product_name="clinic connect",
    )

    assert integrators_for(first, second) == [
        ([first["sd_id"], second["sd_id"]], ["mobile", "product name"]),
    ]


def test_one_person_registering_two_different_companies_gets_two_organisations():
    first = registration("consultant@agency.in", "Acme Health")
    second = registration("consultant@agency.in", "Zenith Labs")

    assert len(integrators_for(first, second)) == TWO_ORGANISATIONS


def test_individuals_are_never_merged_into_a_company():
    person = registration("a@acme.in", "")
    company = registration("a@acme.in", "Acme Health")

    assert len(integrators_for(person, company)) == TWO_ORGANISATIONS
