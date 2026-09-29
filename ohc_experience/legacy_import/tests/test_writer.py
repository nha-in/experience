from datetime import UTC
from datetime import datetime

import pytest

from ohc_experience.legacy_import.organisations import Integrator
from ohc_experience.legacy_import.organisations import Registrant
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
    registrant = Registrant(
        email="a@acme.in",
        name_key="acmehealth",
        variant_key="acmehealth",
    )
    registrant.rows.append({"sd_id": 7, "created_at": None})

    broken.import_isolated(
        Integrator(registrants=[registrant], linked_by=[]),
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


def exit_row(exit_id, final_status):
    return {"id": exit_id, "final_status": final_status}


def test_an_account_that_worked_in_both_tracks_becomes_two_products():
    loader = importer()

    groups = loader.track_groups(
        {"m1", "m2", "p1", "p4", "nhcx1"},
        [exit_row(1, 5), exit_row(2, 5)],
        {1: ["m2"], 2: ["p4"]},
    )

    assert groups == [["m1", "m2", "nhcx1"], ["p1", "p4"]]
    assert loader.report.counts["accounts split into two products"] == 1


def test_a_track_only_declared_is_dropped_beside_the_one_submitted():
    loader = importer()

    groups = loader.track_groups(
        {"m1", "m2", "p1", "p2"},
        [exit_row(1, 5)],
        {1: ["m2"]},
    )

    assert groups == [["m1", "m2"]]
    assert loader.report.counts["declared track dropped beside submitted work"] == 1


def test_declaring_both_tracks_and_submitting_neither_keeps_the_longer():
    loader = importer()

    groups = loader.track_groups({"m1", "p1", "p2", "p4"}, [], {})

    assert groups == [["p1", "p2", "p4"]]
    assert loader.report.counts["declared tracks narrowed to one"] == 1


def test_one_track_alone_stays_one_product():
    loader = importer()

    assert loader.track_groups({"m2", "m1", "uhi1"}, [], {}) == [["m1", "m2", "uhi1"]]
