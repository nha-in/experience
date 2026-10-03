from datetime import UTC
from datetime import datetime

import pytest

from ohc_experience.experiences import legacy
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.legacy_import.organisations import Integrator
from ohc_experience.legacy_import.writer import Importer
from ohc_experience.legacy_import.writer import staff_grants
from ohc_experience.organisations.models import Organisation


class BrokenImporter(Importer):
    def import_integrator(self, integrator, users):
        self.report.counts["products"] += 1
        self.report.review["duplicate production client ids"].append({"sd_id": 7})
        Organisation.objects.create(name="Acme Health")
        msg = "value too long"
        raise ValueError(msg)


def importer(cls=Importer):
    return cls(None, store_files=False, stdout=None)


#: `mst_role` as legacy named its own roles.
LEGACY_ROLES = {1: "Super Admin", 2: "User", 3: "HTC"}


def login(sd_id, role_id, email):
    return {
        "sd_id": sd_id,
        "role_id": role_id,
        "email": email,
        "name": "Asha Rao",
        "organization": "Acme Health",
    }


@pytest.mark.django_db
def test_an_organisation_that_fails_is_rolled_back_and_reported():
    broken = importer(BrokenImporter)
    broken.import_isolated(
        Integrator(email="a@acme.in", rows=[{"sd_id": 7, "created_at": None}]),
        users={},
    )

    assert not Organisation.objects.exists()
    assert broken.report.counts == {"organisations failed": 1}
    assert broken.report.review["duplicate production client ids"] == []
    assert broken.report.review["failed organisations"] == [
        {"sd_ids": "7", "error": "ValueError: value too long"},
    ]


def test_registrations_under_a_staff_email_are_skipped():
    loader = importer()
    loader.logins = [login(1, 2, "Reviewer@acme.in"), login(2, 2, "owner@acme.in")]
    loader.roles = LEGACY_ROLES
    loader.statuses = {}
    loader.staff_emails = {"reviewer@acme.in"}

    selected = loader.select_valid_integrator_users()

    assert [row["sd_id"] for row in selected] == [2]
    assert loader.report.skipped == [
        ("sd_login", 1, "email belongs to a staff login"),
    ]


def test_a_staff_role_earns_its_grants_from_its_name():
    assert staff_grants("htc") == [("*", True, True, True)]
    assert staff_grants("super admin") == []
    assert staff_grants("role_admin_view") == [("*", True, False, False)]
    assert staff_grants("uhi application") == [("UHI", True, False, False)]
    assert staff_grants("nhcx") == [("NHCX", True, False, False)]
    assert staff_grants("a role legacy added later") is None


def test_an_exit_keeps_its_own_wasa_and_leaves_the_account_dates_to_the_review():
    loader = importer()
    loader.declarations_by_id, loader.declarations_by_login = {}, {}
    loader.agencies = {}
    loader.wasa = {
        7: {
            "wasa_issue_date": datetime(2025, 9, 25, tzinfo=UTC),
            "wasa_expiry_date": datetime(2026, 9, 24, tzinfo=UTC),
        },
    }
    exit_row = {
        "organization_evaluate": "Paladion Networks",
        "self_declaration_id": None,
        "created_date": None,
        "created_at": None,
        "sare_date": None,
    }

    data = loader.exit_data(exit_row, "m1", 7)

    assert data["wasa_agency"] == "Paladion Networks"
    assert "wasa_date" not in data
    assert "wasa_valid_until" not in data


def test_a_registration_declaring_both_tracks_keeps_both_as_a_gap():
    loader = importer()
    loader.declarations_by_login = {7: [{"complete_mil": "M1,M2,PHR"}]}
    loader.uhi, loader.nhcx, loader.enrolments = {}, {}, {}

    keys, gaps = loader.milestones_and_gaps({"sd_id": 7, "field_detail": ""}, [], {})

    assert {"m1", "m2", "p1", "p2", "p3"} <= keys
    assert gaps == [legacy.TRACKS]
    assert loader.report.counts["products holding both exclusive tracks"] == 1


def integrator_of(*sd_ids):
    return Integrator(
        email="a@acme.in",
        rows=[{"sd_id": sd_id, "created_at": None} for sd_id in sd_ids],
    )


def test_an_nhcx_approval_lands_on_the_newest_exit_when_none_names_it():
    """Legacy replaces a declaration, so its NHCX wording can be gone by the dump."""
    loader = importer()
    older = FormSubmission(
        submission_number=1,
        submitted_at=datetime(2026, 2, 5, tzinfo=UTC),
    )
    newest = FormSubmission(
        submission_number=2,
        submitted_at=datetime(2026, 6, 19, tzinfo=UTC),
    )
    filings = {
        "m1": [({"final_status": 3}, older), ({"final_status": 3}, newest)],
    }
    decided = datetime(2026, 6, 23, tzinfo=UTC)

    decision = loader.decision_for(
        "nhcx_provider",
        filings,
        {
            "nhcx_final_status": 10,
            "nhcx_admin_status_updated_at": decided,
            "nhcx_admin_comment": "Approved",
        },
    )

    assert decision.status == "approved"
    assert decision.submission is newest
    assert decision.decided == decided


def test_a_rejected_organisation_keeps_the_decision_legacy_made():
    loader = importer()
    loader.statuses = {
        7: {
            "final_status": 2,
            "date": datetime(2025, 4, 2, tzinfo=UTC),
            "admin_comment": "The GST certificate did not match the company name.",
        },
    }
    organisation = Organisation(
        verification_status=Organisation.VerificationStatus.REJECTED,
    )

    decision, decided, note = loader.verification_decision(
        organisation,
        integrator_of(7),
        None,
    )

    assert decision == "rejected"
    assert decided == datetime(2025, 4, 2, tzinfo=UTC)
    assert note == "The GST certificate did not match the company name."


def test_a_verification_waits_only_when_legacy_had_it_in_hand():
    loader = importer()
    loader.statuses = {7: {"final_status": 0, "date": None, "admin_comment": None}}
    pending = Organisation(
        verification_status=Organisation.VerificationStatus.PENDING,
    )

    assert loader.verification_decision(pending, integrator_of(7), None) == (
        "new",
        None,
        "",
    )
    # A login legacy never gave a status row was never put in front of anyone.
    assert loader.verification_decision(pending, integrator_of(9), None) == (
        "draft",
        None,
        "",
    )


def test_payers_and_providers_at_registration_are_not_an_nhcx_application():
    """No registration names NHCX: legacy opened its form only after M1."""
    loader = importer()
    loader.declarations_by_login = {}
    loader.uhi, loader.nhcx, loader.enrolments = {}, {}, {}

    keys, gaps = loader.milestones_and_gaps(
        {"sd_id": 7, "field_detail": "PHR App, Providers"},
        [],
        {},
    )

    assert keys == {"p1", "p2", "p3"}
    assert gaps == []


def test_an_nhcx_exit_is_filed_under_the_role_the_product_holds():
    loader = importer()
    loader.declarations_by_login = {7: [{"complete_mil": "M1"}]}
    loader.uhi, loader.nhcx = {}, {}
    # The NHCX form legacy only opened once M1 was approved.
    loader.enrolments = {7: {"payer_category": "Insurance company"}}
    # Legacy writes "NHCX" on the exit, which reads as the provider role.
    exit_keys = {5: ["m1", "nhcx_provider"]}

    keys, gaps = loader.milestones_and_gaps(
        {"sd_id": 7, "field_detail": "", "solution_type": "", "category": ""},
        [],
        exit_keys,
    )

    assert keys == {"m1", "nhcx_payer"}
    assert gaps == []
    assert exit_keys == {5: ["m1", "nhcx_payer"]}


def test_one_track_alone_leaves_no_gap():
    loader = importer()
    loader.declarations_by_login = {7: [{"complete_mil": "M1,M2"}]}
    loader.uhi, loader.nhcx, loader.enrolments = {}, {}, {}

    keys, gaps = loader.milestones_and_gaps({"sd_id": 7, "field_detail": ""}, [], {})

    assert keys == {"m1", "m2"}
    assert gaps == []
