# ruff: noqa: F811, PLR2004
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.utils import timezone

from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.tests.test_workflow import reverify
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def submitted_pair(environment):
    return submit(environment, "m1"), submit(environment, "m2")


def revisions(*items):
    return {str(item.pk): str(item.selected_submission_id) for item in items}


def decide_all(environment, items, *, action="approve", note="Reviewed the evidence."):
    return workflows.decide_product(
        environment["workspace"].product,
        environment["admin"],
        action=action,
        expected_revisions=revisions(*items),
        note=note,
    )


def assert_pending(*items):
    for item in items:
        item.refresh_from_db()
        assert item.pending
        assert item.decided_at is None
        if item.application_id:
            assert not item.application.product.outcomes.filter(
                source_application=item.application,
                outcome_type="milestone_approval",
            ).exists()


def test_accepts_submitted_chain_in_dependency_order_only(
    environment,
    submitted_pair,
    django_capture_on_commit_callbacks,
):
    m1, m2 = submitted_pair
    draft = milestone(environment, "m3")
    with (
        patch.object(workflows, "notify_decision") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        decided = decide_all(environment, [m2, m1], note="Evidence accepted.")
        assert notify.call_count == 0
    assert [item.pk for item in decided] == [m1.pk, m2.pk]
    assert len(callbacks) == 2
    assert notify.call_count == 2
    for item in decided:
        assert item.status == ReviewItem.Status.APPROVED
        assert item.decision_note == "Evidence accepted."
        assert item.decided_by == environment["admin"]
        assert AuditEvent.objects.filter(item=item, action="Approved").count() == 1
    draft.refresh_from_db()
    assert draft.status == ReviewItem.Status.DRAFT
    assert draft.selected_submission_id is None


def test_rejects_submitted_chain_with_shared_reason(environment, submitted_pair):
    m1, m2 = submitted_pair
    decided = decide_all(
        environment,
        [m1, m2],
        action="reject",
        note="  Update the evidence for both milestones.  ",
    )
    assert [item.pk for item in decided] == [m2.pk, m1.pk]
    for item in decided:
        assert item.status == ReviewItem.Status.REJECTED
        assert item.application.status == "draft"
        assert item.decision_note == "Update the evidence for both milestones."
        event = AuditEvent.objects.get(item=item, action="Rejected")
        assert event.detail["submission_id"] == item.selected_submission_id
    assert milestone(environment, "m3").status == ReviewItem.Status.DRAFT


def test_single_rejection_still_requires_approved_prerequisites(
    environment,
    submitted_pair,
):
    _, m2 = submitted_pair
    with pytest.raises(ValidationError, match="M1"):
        workflows.decide(
            m2,
            environment["admin"],
            action="reject",
            note="Fix the evidence.",
        )


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_unselected_pending_prerequisite_blocks_batch(
    environment,
    submitted_pair,
    action,
):
    m1, m2 = submitted_pair
    with pytest.raises(ValidationError, match="M1"):
        decide_all(environment, [m2], action=action, note="Fix the evidence.")
    assert_pending(m1, m2)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_organisation_prerequisite_cannot_be_bypassed(
    environment,
    submitted_pair,
    action,
):
    reverify(environment)
    with pytest.raises(ValidationError, match="organisation verification"):
        decide_all(environment, submitted_pair, action=action, note="Fix the evidence.")
    assert_pending(*submitted_pair)


def test_later_unresolved_query_rolls_back_earlier_approval_and_notifications(
    environment,
    submitted_pair,
    django_capture_on_commit_callbacks,
):
    m1, m2 = submitted_pair
    workflows.decide(
        m2,
        environment["admin"],
        action="query",
        note="Explain the report.",
    )
    initial_audit_count = AuditEvent.objects.count()
    with (
        patch.object(workflows, "notify_decision") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(ValidationError, match="Resolve all queries"),
    ):
        decide_all(environment, [m1, m2])
    assert not callbacks
    notify.assert_not_called()
    assert_pending(m1, m2)
    assert AuditEvent.objects.count() == initial_audit_count
    assert m2.status == ReviewItem.Status.QUERY


def test_expired_evidence_rolls_back_entire_batch(environment, submitted_pair):
    m1, m2 = submitted_pair
    submission = m2.selected_submission
    submission.data["wasa_valid_until"] = (
        timezone.localdate() - timedelta(days=1)
    ).isoformat()
    submission.save(update_fields=["data"])
    with pytest.raises(ValidationError, match="expired"):
        decide_all(environment, [m1, m2])
    assert_pending(m1, m2)


def test_automatic_release_is_rolled_back_without_a_notice_on_later_failure(
    environment,
    submitted_pair,
    django_capture_on_commit_callbacks,
):
    m1, m2 = submitted_pair
    automatic = submit(environment, "uhi1")
    locker = submit(environment, "p1")
    workflows.decide(locker, environment["admin"], action="query", note="Clarify this.")
    with (
        patch.object(workflows, "notify_decision") as decision_notice,
        patch.object(workflows, "notify_review") as review_notice,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(ValidationError, match="Resolve all queries"),
    ):
        decide_all(environment, [m1, m2, locker])
    assert not callbacks
    decision_notice.assert_not_called()
    review_notice.assert_not_called()
    assert_pending(m1, m2, locker, automatic)


def test_unselected_new_submission_is_not_decided(environment, submitted_pair):
    another = submit(environment, "m3")
    decide_all(environment, submitted_pair)
    assert_pending(another)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_stale_revision_prevents_all_decisions(environment, submitted_pair, action):
    m1, m2 = submitted_pair
    expected = revisions(m1, m2)
    expected[str(m2.pk)] = "0"
    with pytest.raises(ValidationError, match="submission changed"):
        workflows.decide_product(
            environment["workspace"].product,
            environment["admin"],
            action=action,
            expected_revisions=expected,
            note="Fix the evidence.",
        )
    assert_pending(m1, m2)


def test_previous_decision_cannot_be_replayed(environment, submitted_pair):
    m1, m2 = submitted_pair
    workflows.decide(
        m1,
        environment["admin"],
        action="approve",
        note="Evidence accepted.",
    )
    with pytest.raises(ValidationError, match="submission changed"):
        decide_all(environment, [m1, m2])
    m1.refresh_from_db()
    assert m1.status == ReviewItem.Status.APPROVED
    assert_pending(m2)


def test_every_selected_category_requires_approval_permission(
    environment,
    submitted_pair,
):
    staff = UserFactory(is_nha_team=True)
    AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="review",
        category="PHR",
        can_read=True,
        can_approve=True,
    )
    locker = submit(environment, "p1")
    with pytest.raises(PermissionDenied):
        workflows.decide_product(
            environment["workspace"].product,
            staff,
            action="approve",
            expected_revisions=revisions(locker, *submitted_pair),
        )
    assert_pending(locker, *submitted_pair)


@pytest.mark.parametrize("invalid", [{}, {"invalid": "1"}, None, {999999999: "1"}])
def test_invalid_selection_is_rejected(environment, submitted_pair, invalid):
    with pytest.raises(ValidationError):
        workflows.decide_product(
            environment["workspace"].product,
            environment["admin"],
            action="approve",
            expected_revisions=invalid,
        )
    assert_pending(*submitted_pair)


def test_cannot_replay_completed_organisation_review(environment, submitted_pair):
    organisation_review = environment["org"].review_items.get(
        kind=ReviewItem.Kind.ORGANISATION,
    )
    with pytest.raises(ValidationError, match="submission changed"):
        decide_all(environment, [*submitted_pair, organisation_review])
    assert_pending(*submitted_pair)


def test_accepts_organisation_then_product_chain_with_committed_notices(
    environment,
    submitted_pair,
    django_capture_on_commit_callbacks,
):
    organisation_review = reverify(environment)
    m1, m2 = submitted_pair
    with (
        patch.object(workflows, "notify_decision") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        decided = decide_all(
            environment,
            [m2, m1, organisation_review],
            note="Organisation and milestone evidence accepted.",
        )
        notify.assert_not_called()
    assert [item.pk for item in decided] == [organisation_review.pk, m1.pk, m2.pk]
    assert len(callbacks) == notify.call_count == 3
    assert all(item.status == ReviewItem.Status.APPROVED for item in decided)
    environment["org"].refresh_from_db()
    assert environment["org"].is_verified
    assert milestone(environment, "m3").status == ReviewItem.Status.DRAFT


def test_rejects_product_chain_then_organisation_with_one_reason(
    environment,
    submitted_pair,
):
    organisation_review = reverify(environment)
    m1, m2 = submitted_pair
    decided = decide_all(
        environment,
        [organisation_review, m1, m2],
        action="reject",
        note="Correct the organisation and milestone evidence.",
    )
    assert [item.pk for item in decided] == [m2.pk, m1.pk, organisation_review.pk]
    assert all(item.status == ReviewItem.Status.REJECTED for item in decided)
    assert all(
        item.decision_note == "Correct the organisation and milestone evidence."
        for item in decided
    )
    environment["org"].refresh_from_db()
    assert environment["org"].verification_status == "rejected"
    assert milestone(environment, "m3").status == ReviewItem.Status.DRAFT


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_preview_includes_selected_organisation_prerequisite(
    environment,
    submitted_pair,
    action,
):
    organisation_review = reverify(environment)
    assert not workflows.product_decision_blockers(
        [*submitted_pair, organisation_review],
        environment["admin"],
        action=action,
    )


def test_organisation_approval_rolls_back_after_later_milestone_failure(
    environment,
    submitted_pair,
    django_capture_on_commit_callbacks,
):
    organisation_review = reverify(environment)
    m1, m2 = submitted_pair
    workflows.decide(m2, environment["admin"], action="query", note="Clarify testing.")
    initial_audit_count = AuditEvent.objects.count()
    with (
        patch.object(workflows, "notify_decision") as notify,
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(ValidationError, match="Resolve all queries"),
    ):
        decide_all(environment, [organisation_review, m1, m2])
    assert not callbacks
    notify.assert_not_called()
    assert_pending(organisation_review, m1, m2)
    environment["org"].refresh_from_db()
    assert environment["org"].verification_status == "pending"
    assert environment["org"].verified_at is None
    assert AuditEvent.objects.count() == initial_audit_count


def test_organisation_selection_requires_general_approval_permission(
    environment,
    submitted_pair,
):
    organisation_review = reverify(environment)
    reviewer = UserFactory(is_nha_team=True)
    AccessGrant.objects.create(
        user=reviewer,
        program="abdm",
        area="review",
        category="ABDM",
        can_read=True,
        can_approve=True,
    )
    with pytest.raises(PermissionDenied):
        workflows.decide_product(
            environment["workspace"].product,
            reviewer,
            action="approve",
            expected_revisions=revisions(*submitted_pair, organisation_review),
        )
    assert_pending(organisation_review, *submitted_pair)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_stale_organisation_snapshot_prevents_the_whole_batch(
    environment,
    submitted_pair,
    action,
):
    organisation_review = reverify(environment)
    expected = revisions(*submitted_pair, organisation_review)
    expected[str(organisation_review.pk)] = "0"
    with pytest.raises(ValidationError, match="submission changed"):
        workflows.decide_product(
            environment["workspace"].product,
            environment["admin"],
            action=action,
            expected_revisions=expected,
            note="Please correct the evidence.",
        )
    assert_pending(organisation_review, *submitted_pair)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_cannot_select_another_organisations_verification(
    environment,
    submitted_pair,
    action,
):
    owner = MembershipFactory(role="owner")
    unrelated = workflows.organisation_review(owner.organisation, owner.user)
    unrelated, form, saved = workflows.save_review_form(
        unrelated,
        owner.user,
        data=organisation_data(),
        files={"supporting_document": pdf()},
        submit=True,
    )
    assert saved, form.errors
    with pytest.raises(ValidationError, match="belong to this product"):
        decide_all(
            environment,
            [*submitted_pair, unrelated],
            action=action,
            note="Please correct the evidence.",
        )
    assert_pending(*submitted_pair, unrelated)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_shared_organisation_does_not_allow_other_product_requests(
    environment,
    submitted_pair,
    action,
):
    workspace, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data={**product_data(), "name": "Another product"},
    )
    assert workspace, form.errors
    other = submit({**environment, "workspace": workspace})
    organisation_review = reverify(environment)
    with pytest.raises(ValidationError, match="belong to this product"):
        decide_all(
            environment,
            [*submitted_pair, organisation_review, other],
            action=action,
            note="Please correct the evidence.",
        )
    assert_pending(*submitted_pair, organisation_review, other)


def test_cannot_include_automatically_recorded_request(environment, submitted_pair):
    automatic = submit(environment, "uhi1")
    with pytest.raises(ValidationError, match="Automatically recorded"):
        decide_all(environment, [*submitted_pair, automatic])
    assert_pending(*submitted_pair, automatic)


@pytest.mark.parametrize(
    "note",
    ["", "   ", "Too short", "x" * (workflows.MAX_REVIEW_TEXT + 1)],
)
def test_reject_all_requires_valid_shared_reason(environment, submitted_pair, note):
    with pytest.raises(ValidationError):
        decide_all(environment, submitted_pair, action="reject", note=note)
    assert_pending(*submitted_pair)


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_preview_allows_selected_prerequisites_but_identifies_external_blockers(
    environment,
    submitted_pair,
    action,
):
    m1, m2 = submitted_pair
    assert (
        workflows.product_decision_blockers(
            [m1, m2],
            environment["admin"],
            action=action,
        )
        == []
    )
    blockers = workflows.product_decision_blockers(
        [m2],
        environment["admin"],
        action=action,
    )
    assert len(blockers) == 1
    assert "M1" in blockers[0]


def test_single_decision_checks_expected_revision(environment, submitted_pair):
    m1, _ = submitted_pair
    with pytest.raises(ValidationError, match="submission changed"):
        workflows.decide(
            m1,
            environment["admin"],
            action="query",
            note="Explain the evidence.",
            expected_revision="0",
        )
    assert not m1.queries.exists()
    assert_pending(m1)
