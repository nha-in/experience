from datetime import UTC
from datetime import datetime
from itertools import count

from ohc_experience.legacy_import.organisations import read_integrators

ids = count(1)
TWO_ORGANISATIONS = 2


def registration(email, organisation, **values):
    return {
        "sd_id": next(ids),
        "email": email,
        "application_type": "3" if organisation else "2",
        "organization": organisation,
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
        **values,
    }


def organisations_for(*rows):
    return sorted(
        sorted(row["sd_id"] for row in integrator.rows)
        for integrator in read_integrators(rows)
    )


def test_one_email_holds_one_organisation_however_often_it_registered():
    first = registration("owner@acme.in", "Acme Health")
    second = registration("owner@acme.in", "Acme Health")

    assert organisations_for(first, second) == [[first["sd_id"], second["sd_id"]]]


def test_one_email_registering_two_companies_still_holds_one_organisation():
    first = registration("consultant@agency.in", "Acme Health")
    second = registration("consultant@agency.in", "Zenith Labs")

    assert organisations_for(first, second) == [[first["sd_id"], second["sd_id"]]]


def test_colleagues_registering_the_same_company_stay_apart():
    first = registration("a@acme.in", "Acme Health", gst_no="29AAAAA0000A1Z5")
    second = registration("b@acme.in", "Acme Health", gst_no="29AAAAA0000A1Z5")

    assert len(organisations_for(first, second)) == TWO_ORGANISATIONS


def test_an_email_that_only_ever_registered_as_an_individual_is_a_person():
    person = read_integrators([registration("a@acme.in", "")])[0]
    company = read_integrators([registration("a@acme.in", "Acme Health")])[0]

    assert person.is_person
    assert not company.is_person
