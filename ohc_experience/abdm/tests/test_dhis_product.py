"""DHIS handoffs use product identity, its registration and approved evidence."""

# ruff: noqa: F811
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.utils import timezone

from ohc_experience.abdm import dhis
from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_wasa_lifecycle import certificate_data
from ohc_experience.abdm.tests.test_wasa_lifecycle import decide
from ohc_experience.abdm.tests.test_wasa_lifecycle import request_milestone
from ohc_experience.abdm.tests.test_wasa_lifecycle import request_renewal
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.tests.test_workflow import workspace_for
from ohc_experience.abdm.wasa import current_wasa
from ohc_experience.experiences import workflows
from ohc_experience.organisations.models import Membership
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

HANDOFF_URL = "https://dhis.abdm.gov.in/DHIS/?value=synthetic"


@pytest.fixture
def encoder(monkeypatch):
    encode = Mock(return_value=HANDOFF_URL)
    monkeypatch.setattr(dhis, "create_handoff_url", encode)
    return encode


def approve_milestones(environment, keys):
    source = None
    for key in keys:
        item = decide(
            environment,
            request_milestone(environment, key, source=source),
        )
        if source is None:
            source = item.selected_submission
    return source


def workspace_of(environment, keys):
    """The product these milestones sit on: ABDM and PHR never share one."""
    return workspace_for(environment, keys[0])


def change_solutions(environment, solution_types, *, workspace=None, submit=True):
    """Re-declare the solutions, keeping the milestones the product applied for."""
    workspace = workspace or environment["workspace"]
    item = workspace.product.review_items.get(kind="product_registration")
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data={
            **product_data(workspace.product.name),
            "solution_type": solution_types,
            "applied_milestones": workspace.applied_milestones,
        },
        submit=submit,
    )
    assert saved, form.errors
    return item


def handoff(environment, solution_type="hmis", *, workspace=None, actor=None):
    return dhis.create_product_handoff_url(
        (workspace or environment["workspace"]).product,
        solution_type=solution_type,
        actor=actor or environment["applicant"],
    )


@pytest.fixture
def eligible_hmis(environment):
    approve_milestones(environment, ("m1", "m2", "m3"))
    return environment


@pytest.mark.parametrize(
    ("solution_type", "keys", "intent", "terminal"),
    [
        ("hmis", ("m1", "m2", "m3"), "HMIS", "M3"),
        ("lmis", ("m1", "m2"), "LMIS", "M2"),
        ("telemedicine", ("m1", "m2", "m3"), "Telemedicine", "M3"),
        (
            "health_locker",
            ("p1", "p2", "p3", "p4"),
            "HealthLocker",
            "Healthlocker",
        ),
        ("pharmacy", ("m1", "m2"), "Pharmacy", "M2"),
    ],
)
def test_solution_handoff_uses_product_id_and_approved_wasa(  # noqa: PLR0913, PLR0917
    environment,
    encoder,
    solution_type,
    keys,
    intent,
    terminal,
):
    workspace = workspace_of(environment, keys)
    change_solutions(environment, [solution_type], workspace=workspace)
    source = approve_milestones(environment, keys)

    assert handoff(environment, solution_type, workspace=workspace) == HANDOFF_URL

    payload = encoder.call_args.args[0]
    assert payload["client_id"] == str(workspace.product.pk)
    assert payload["intent_request"] == intent
    assert payload["integration_level"] == terminal
    assert payload["wasa_valid_upto_date"] == [
        {
            "milestone": terminal,
            "expiryDate": source.data["wasa_valid_until"],
            "status": "active",
        },
    ]


@pytest.mark.parametrize(
    ("solution_type", "approved_keys"),
    [
        ("hmis", ("m1", "m2")),
        ("lmis", ("m1",)),
        ("telemedicine", ("m1", "m2")),
        ("health_locker", ("p1", "p2", "p3")),
        ("health_locker", ("p1", "p4")),
        ("pharmacy", ("m1",)),
    ],
)
def test_incomplete_milestones_do_not_generate_a_handoff(
    environment,
    encoder,
    solution_type,
    approved_keys,
):
    workspace = workspace_of(environment, approved_keys)
    change_solutions(environment, [solution_type], workspace=workspace)
    approve_milestones(environment, approved_keys)

    with pytest.raises(ValidationError):
        handoff(environment, solution_type, workspace=workspace)

    encoder.assert_not_called()


@pytest.mark.parametrize("actor_key", ["outsider", "reviewer", "admin"])
def test_only_product_organisation_integrators_can_initiate(
    eligible_hmis,
    encoder,
    actor_key,
):
    with pytest.raises(PermissionDenied):
        handoff(eligible_hmis, actor=eligible_hmis[actor_key])

    encoder.assert_not_called()


def test_other_integrator_still_sends_product_creator_identity(
    eligible_hmis,
    encoder,
):
    creator = eligible_hmis["applicant"]
    creator.phone_number = "+919876543210"
    creator.save(update_fields=["phone_number"])
    teammate = UserFactory(name="Different teammate", phone_number="+919123456789")
    Membership.objects.create(
        organisation=eligible_hmis["org"],
        user=teammate,
        role="developer",
    )

    assert handoff(eligible_hmis, actor=teammate) == HANDOFF_URL

    payload = encoder.call_args.args[0]
    assert payload["email"] == creator.email
    assert payload["mobile"] == creator.phone_number
    assert teammate.email not in payload.values()
    assert teammate.phone_number not in payload.values()


def test_a_solution_change_applies_to_the_next_handoff(eligible_hmis, encoder):
    """Product registration is recorded when saved, with no approval to wait on."""
    change_solutions(eligible_hmis, ["lmis"])

    assert handoff(eligible_hmis, "lmis") == HANDOFF_URL
    with pytest.raises(ValidationError):
        handoff(eligible_hmis)

    encoder.assert_called_once()


def test_a_solution_draft_cannot_replace_the_recorded_selections(
    eligible_hmis,
    encoder,
):
    with pytest.raises(ValidationError, match="Submit your updated answers"):
        change_solutions(eligible_hmis, ["lmis"], submit=False)

    assert handoff(eligible_hmis) == HANDOFF_URL
    with pytest.raises(ValidationError):
        handoff(eligible_hmis, "lmis")

    encoder.assert_called_once()


def test_multiple_solutions_share_one_product_identity(eligible_hmis, encoder):
    change_solutions(eligible_hmis, ["hmis", "lmis"])

    assert handoff(eligible_hmis, "hmis") == HANDOFF_URL
    assert handoff(eligible_hmis, "lmis") == HANDOFF_URL

    payloads = [call.args[0] for call in encoder.call_args_list]
    assert {payload["intent_request"] for payload in payloads} == {"HMIS", "LMIS"}
    assert {payload["client_id"] for payload in payloads} == {
        str(eligible_hmis["workspace"].product.pk),
    }


@pytest.mark.parametrize("solution_type", ["lmis", "pharmacy", "phr"])
def test_hmis_approval_does_not_grant_other_solution_types(
    eligible_hmis,
    encoder,
    solution_type,
):
    with pytest.raises(ValidationError):
        handoff(eligible_hmis, solution_type)

    encoder.assert_not_called()


def test_organisation_draft_uses_last_approved_identity(
    eligible_hmis,
    encoder,
):
    item = eligible_hmis["org"].review_items.get(kind="organisation_verification")
    original_name = item.selected_submission.data["name"]
    item, form, saved = workflows.save_review_form(
        item,
        eligible_hmis["applicant"],
        data=organisation_data("Unapproved replacement name"),
        files={"supporting_document": pdf()},
        submit=False,
    )
    assert saved, form.errors

    assert handoff(eligible_hmis) == HANDOFF_URL

    payload = encoder.call_args.args[0]
    assert payload["name"] == original_name
    assert "Unapproved replacement name" not in payload.values()


def test_pending_renewal_preserves_current_certificate(eligible_hmis, encoder):
    original = current_wasa(eligible_hmis["workspace"].product)
    request_renewal(
        eligible_hmis,
        certificate=certificate_data(expires_in=90, audited_ago=1),
    )

    assert handoff(eligible_hmis) == HANDOFF_URL

    row = encoder.call_args.args[0]["wasa_valid_upto_date"][0]
    assert row["expiryDate"] == original.valid_until.isoformat()


def test_approved_renewal_is_read_on_each_handoff(eligible_hmis, encoder):
    assert handoff(eligible_hmis) == HANDOFF_URL
    original_expiry = encoder.call_args.args[0]["wasa_valid_upto_date"][0]["expiryDate"]
    renewal = decide(
        eligible_hmis,
        request_renewal(
            eligible_hmis,
            certificate=certificate_data(expires_in=90, audited_ago=1),
        ),
    )

    assert handoff(eligible_hmis) == HANDOFF_URL

    row = encoder.call_args.args[0]["wasa_valid_upto_date"][0]
    assert row["expiryDate"] != original_expiry
    assert row["expiryDate"] == renewal.selected_submission.data["wasa_valid_until"]
    assert row["milestone"] == "M3"


@pytest.mark.parametrize("status", ["revoked", "expired"])
def test_invalid_latest_certificate_does_not_fall_back_to_old_approval(
    eligible_hmis,
    encoder,
    status,
):
    product = eligible_hmis["workspace"].product
    decide(
        eligible_hmis,
        request_renewal(
            eligible_hmis,
            certificate=certificate_data(expires_in=90, audited_ago=1),
        ),
    )
    latest = current_wasa(product)
    latest.status = status
    latest.save(update_fields=["status"])

    with pytest.raises(ValidationError, match="WASA"):
        handoff(eligible_hmis)

    encoder.assert_not_called()


def test_certificate_expiry_is_checked_at_handoff_time(eligible_hmis, encoder):
    latest = current_wasa(eligible_hmis["workspace"].product)
    latest.valid_until = timezone.localdate() - timedelta(days=1)
    latest.save(update_fields=["valid_until"])

    with pytest.raises(ValidationError, match="WASA"):
        handoff(eligible_hmis)

    encoder.assert_not_called()


def test_missing_approved_certificate_blocks_handoff(environment, encoder):
    request_renewal(environment)

    with pytest.raises(ValidationError, match="WASA"):
        handoff(environment)

    encoder.assert_not_called()


def test_revoked_milestone_approval_blocks_handoff(eligible_hmis, encoder):
    approval = eligible_hmis["workspace"].product.outcomes.get(
        outcome_type="milestone_approval",
        source_application__milestone__key="m2",
    )
    approval.status = "revoked"
    approval.save(update_fields=["status"])

    with pytest.raises(ValidationError):
        handoff(eligible_hmis)

    encoder.assert_not_called()
