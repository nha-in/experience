"""Portal handoff options reflect current approvals without creating tokens."""

# ruff: noqa: F811
from unittest.mock import Mock

import pytest
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError

from ohc_experience.abdm import dhis
from ohc_experience.abdm.definitions import ABDM
from ohc_experience.abdm.tests.test_dhis_product import approve_milestones
from ohc_experience.abdm.tests.test_dhis_product import change_solutions
from ohc_experience.abdm.tests.test_dhis_product import eligible_hmis  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.wasa import current_wasa

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def configured_dhis(settings):
    settings.ABDM_DHIS_URL = "https://dhis.abdm.gov.in/DHIS/"
    settings.ABDM_DHIS_JWT_SECRET = "synthetic-signing-secret"  # noqa: S105
    settings.ABDM_DHIS_AES_KEY = "0123456789abcdef"
    settings.ABDM_DHIS_AES_IV = "SyntheticInitVec"


def options(environment, *, actor=None):
    return {
        row["key"]: row
        for row in ABDM.handoffs["dhis"].options(
            environment["workspace"].product,
            actor=actor or environment["applicant"],
        )
    }


def test_options_show_five_solutions_without_generating_tokens(
    eligible_hmis,
    monkeypatch,
):
    encoder = Mock()
    monkeypatch.setattr(dhis, "create_handoff_url", encoder)

    rows = options(eligible_hmis)

    assert list(rows) == ["hmis", "lmis", "telemedicine", "health_locker", "pharmacy"]
    assert rows["hmis"]["enabled"]
    assert rows["hmis"]["reason"] == ""
    assert rows["health_locker"]["label"] == "Health Locker"
    assert all(not row["enabled"] for key, row in rows.items() if key != "hmis")
    assert "not in the product registration" in rows["lmis"]["reason"]
    encoder.assert_not_called()


def test_all_approved_solutions_can_be_offered_together(environment):
    change_solutions(environment, list(dhis.SOLUTION_MILESTONES))
    approve_milestones(environment, ("m1", "m2", "m3", "phr1", "locker1"))

    assert all(row["enabled"] for row in options(environment).values())


@pytest.mark.parametrize(
    ("solution", "approved", "missing_name"),
    [
        ("hmis", ("m1", "m2"), "M3"),
        ("health_locker", ("m1", "phr1"), "Health Locker"),
        ("health_locker", ("m1", "locker1"), "PHR"),
    ],
)
def test_missing_milestones_have_readable_names(
    environment,
    solution,
    approved,
    missing_name,
):
    change_solutions(environment, [solution])
    approve_milestones(environment, approved)

    row = options(environment)[solution]

    assert not row["enabled"]
    assert row["reason"] == (
        f"Complete the required milestone approvals before DHIS: {missing_name}."
    )


def test_a_saved_solution_change_replaces_the_options_at_once(eligible_hmis):
    assert options(eligible_hmis)["hmis"]["enabled"]
    change_solutions(eligible_hmis, ["lmis"])

    rows = options(eligible_hmis)

    assert rows["lmis"]["enabled"]
    assert not rows["hmis"]["enabled"]
    assert "not in the product registration" in rows["hmis"]["reason"]


def test_an_unverified_organisation_is_offered_no_solution(eligible_hmis):
    eligible_hmis["org"].set_verification("pending")

    rows = options(eligible_hmis)

    assert all(not row["enabled"] for row in rows.values())
    assert "Organisation verification must be approved" in rows["hmis"]["reason"]


def test_revoked_current_wasa_disables_previously_eligible_option(eligible_hmis):
    assert options(eligible_hmis)["hmis"]["enabled"]
    approval = current_wasa(eligible_hmis["workspace"].product)
    approval.status = "revoked"
    approval.save(update_fields=["status"])

    row = options(eligible_hmis)["hmis"]

    assert not row["enabled"]
    assert "current, approved WASA" in row["reason"]


@pytest.mark.parametrize("actor_key", ["outsider", "reviewer", "admin"])
def test_options_are_restricted_to_organisation_integrators(
    environment,
    actor_key,
):
    with pytest.raises(PermissionDenied):
        options(environment, actor=environment[actor_key])


def test_missing_configuration_disables_handoff_with_safe_retry_message(
    eligible_hmis,
    settings,
):
    settings.ABDM_DHIS_AES_KEY = "invalid-key"

    row = options(eligible_hmis)["hmis"]

    assert not row["enabled"]
    assert row["reason"] == dhis.UNAVAILABLE_MESSAGE
    with pytest.raises(ValidationError) as error:
        dhis.DHISHandoff.create_url(
            eligible_hmis["workspace"].product,
            option="hmis",
            actor=eligible_hmis["applicant"],
        )
    assert error.value.messages == [dhis.UNAVAILABLE_MESSAGE]
    assert error.value.code == "unavailable"
