from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from ohc_experience.experiences import permission_keys
from ohc_experience.experiences.models import ApplicationAccess
from ohc_experience.experiences.models import ApplicationFormSubmission
from ohc_experience.experiences.models import QueryStatus
from ohc_experience.experiences.permissions import get_effective_access
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.services import application_context
from ohc_experience.experiences.services import assignable_roles
from ohc_experience.experiences.services import create_application
from ohc_experience.experiences.services import grant_application_access
from ohc_experience.experiences.services import perform_application_action
from ohc_experience.experiences.services import perform_form_action
from ohc_experience.experiences.services import post_query_reply
from ohc_experience.experiences.services import recalculate_progress
from ohc_experience.experiences.services import resolve_query
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

APPLICATION_TYPE = "abdm_production_access"
FORM_COUNT = 8
BASE_REQUIRED_FORM_COUNT = 7
SCOPED_COMPLETED_FORM_COUNT = 3
SCOPED_PROGRESS_PERCENT = 38
BASE_PROGRESS_PERCENT = 43


@pytest.fixture
def actors():
    organisation = OrganisationFactory(onboarded=True)
    owner = UserFactory(email="owner@example.in")
    contributor = UserFactory(email="contributor@example.in")
    reviewer = UserFactory(
        email="reviewer@ohc.network",
        is_ohc_team=True,
        is_staff=True,
    )
    Membership.objects.create(
        organisation=organisation,
        user=owner,
        role=Role.OWNER,
    )
    Membership.objects.create(
        organisation=organisation,
        user=contributor,
        role=Role.DEVELOPER,
    )
    return organisation, owner, contributor, reviewer


@pytest.fixture
def application(actors):
    organisation, owner, _contributor, reviewer = actors
    application = create_application(
        application_type=APPLICATION_TYPE,
        organisation=organisation,
        user=owner,
    )
    ApplicationAccess.objects.create(
        application=application,
        user=reviewer,
        role_key="decision_maker",
        granted_by=reviewer,
    )
    return application


def complete_all_forms(application, owner) -> None:
    definition = registry.get(APPLICATION_TYPE)
    for form_definition in definition.forms:
        ApplicationFormSubmission.objects.create(
            application=application,
            form_key=form_definition.key,
            data={},
            submitted_by=owner,
        )


def test_definition_exposes_static_forms_roles_permissions_and_actions():
    definition = registry.get(APPLICATION_TYPE)

    assert len(definition.forms) == FORM_COUNT
    assert {role.key for role in definition.roles} >= {
        "applicant_owner",
        "reviewer",
        "decision_maker",
    }
    assert {permission.key for permission in definition.permissions} >= {
        permission_keys.EDIT_FORMS,
        permission_keys.OPEN_QUERY,
        permission_keys.RAISE_QUERY,
        permission_keys.APPROVE_APPLICATION,
    }
    assert {action.key for action in definition.actions} == {
        "submit",
        "ask_review_team",
        "start_review",
        "raise_query",
        "approve",
        "reject",
    }
    assert permission_keys.WITHDRAW_APPLICATION not in {
        permission.key for permission in definition.permissions
    }


def test_new_application_grants_owner_permissions_and_gates_forms(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    context = application_context(application, owner)
    states = registry.get(APPLICATION_TYPE).form_states(context)

    assert get_effective_access(application, owner).role.key == "applicant_owner"
    assert context.has_permission(permission_keys.SUBMIT_APPLICATION)
    assert states[0].can_submit is True
    assert states[1].visible is False
    assert states[-1].visible is False


def test_contributor_cannot_receive_platform_permissions(application, actors):
    _organisation, owner, contributor, _reviewer = actors

    with pytest.raises(PermissionDenied):
        grant_application_access(
            application=application,
            actor=owner,
            target_user=contributor,
            role_key="applicant_contributor",
            direct_permissions=[permission_keys.APPROVE_APPLICATION],
        )


def test_delegated_inviter_cannot_grant_more_than_they_have(application, actors):
    organisation, owner, contributor, _reviewer = actors
    invitee = UserFactory(email="invitee@example.in")
    Membership.objects.create(
        organisation=organisation,
        user=invitee,
        role=Role.DEVELOPER,
    )
    grant_application_access(
        application=application,
        actor=owner,
        target_user=contributor,
        role_key="applicant_contributor",
        direct_permissions=[permission_keys.MANAGE_APPLICANT_ACCESS],
    )

    role_keys = {role.key for role in assignable_roles(application, contributor)}
    assert role_keys == {"applicant_contributor", "applicant_viewer"}

    grant = grant_application_access(
        application=application,
        actor=contributor,
        target_user=invitee,
        role_key="applicant_viewer",
    )
    assert grant.role_key == "applicant_viewer"

    with pytest.raises(PermissionDenied):
        grant_application_access(
            application=application,
            actor=contributor,
            target_user=invitee,
            role_key="applicant_submitter",
        )
    with pytest.raises(PermissionDenied):
        grant_application_access(
            application=application,
            actor=contributor,
            target_user=invitee,
            role_key="applicant_viewer",
            direct_permissions=[permission_keys.SUBMIT_APPLICATION],
        )


def test_application_owner_role_cannot_be_replaced(application, actors):
    _organisation, owner, _contributor, reviewer = actors

    with pytest.raises(PermissionDenied):
        grant_application_access(
            application=application,
            actor=reviewer,
            target_user=owner,
            role_key="review_observer",
        )


def test_application_actions_use_effective_permissions(application, actors):
    _organisation, owner, _contributor, reviewer = actors
    complete_all_forms(application, owner)
    owner_grant = application.access_grants.get(user=owner)
    owner_grant.direct_permissions = [permission_keys.APPROVE_APPLICATION]
    owner_grant.save(update_fields=["direct_permissions", "updated_at"])

    owner_states = registry.get(APPLICATION_TYPE).action_states(
        application_context(application, owner),
    )
    owner_actions = {item.definition.key: item.available for item in owner_states}
    assert owner_actions["submit"] is True
    assert owner_actions["ask_review_team"] is True
    assert owner_actions["approve"] is False
    assert not get_effective_access(application, owner).allows(
        permission_keys.APPROVE_APPLICATION,
    )

    perform_application_action(
        application=application,
        action_key="submit",
        user=owner,
    )
    application.refresh_from_db()
    reviewer_states = registry.get(APPLICATION_TYPE).action_states(
        application_context(application, reviewer),
    )
    reviewer_actions = {item.definition.key: item.available for item in reviewer_states}
    assert reviewer_actions["start_review"] is True
    assert reviewer_actions["ask_review_team"] is False
    assert "withdraw" not in reviewer_actions


def test_progress_uses_forms_applicable_to_the_current_scope(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    for form_key, data in (
        ("organisation_profile", {}),
        ("product_use_case", {}),
        ("integration_scope", {"abdm_roles": ["hip", "health_locker"]}),
    ):
        ApplicationFormSubmission.objects.create(
            application=application,
            form_key=form_key,
            data=data,
            submitted_by=owner,
        )

    recalculate_progress(application, user=owner)
    application.refresh_from_db()
    context = application_context(application, owner)
    health_locker_state = next(
        state
        for state in registry.get(APPLICATION_TYPE).form_states(context)
        if state.definition.key == "health_locker_operations"
    )

    assert application.metadata["completed_forms"] == SCOPED_COMPLETED_FORM_COUNT
    assert application.metadata["required_forms"] == FORM_COUNT
    assert application.progress_percent == SCOPED_PROGRESS_PERCENT
    assert health_locker_state.applicable is True
    assert health_locker_state.visible is True

    integration = application.submissions.get(form_key="integration_scope")
    integration.data = {"abdm_roles": ["hip"]}
    integration.save(update_fields=["data", "updated_at"])
    recalculate_progress(application, user=owner)
    application.refresh_from_db()
    context = application_context(application, owner)
    health_locker_state = next(
        state
        for state in registry.get(APPLICATION_TYPE).form_states(context)
        if state.definition.key == "health_locker_operations"
    )

    assert application.metadata["required_forms"] == BASE_REQUIRED_FORM_COUNT
    assert application.progress_percent == BASE_PROGRESS_PERCENT
    assert health_locker_state.applicable is False
    assert health_locker_state.visible is False


def test_completed_form_update_policy_is_declared_per_form(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    complete_all_forms(application, owner)
    context = application_context(application, owner)
    definition = registry.get(APPLICATION_TYPE)

    profile_available, _reason = definition.get_form(
        "organisation_profile",
    ).availability(context)
    declaration_available, declaration_reason = definition.get_form(
        "declaration",
    ).availability(context)

    assert profile_available is True
    assert declaration_available is False
    assert "locked" in str(declaration_reason).lower()


def test_admin_form_action_runs_after_completion(application, actors):
    _organisation, owner, _contributor, reviewer = actors
    complete_all_forms(application, owner)
    application.status = "under_review"
    application.save(update_fields=["status", "updated_at"])

    with pytest.raises(PermissionDenied):
        perform_form_action(
            application=application,
            form_key="security_compliance",
            action_key="verify_evidence",
            user=owner,
        )

    result = perform_form_action(
        application=application,
        form_key="security_compliance",
        action_key="verify_evidence",
        user=reviewer,
    )
    submission = application.submissions.get(form_key="security_compliance")

    assert str(result.message) == "Security evidence verified"
    assert submission.metadata["verified_revision"] == submission.revision
    assert submission.metadata["evidence_verified_by"] == reviewer.pk
    assert application.events.filter(
        action_key="security_compliance.verify_evidence",
        submission=submission,
    ).exists()

    with pytest.raises(PermissionDenied):
        perform_form_action(
            application=application,
            form_key="security_compliance",
            action_key="verify_evidence",
            user=reviewer,
        )


def test_applicant_can_open_query_without_changing_application_status(
    application,
    actors,
):
    _organisation, owner, _contributor, reviewer = actors

    _result, query = perform_application_action(
        application=application,
        action_key="ask_review_team",
        user=owner,
        cleaned_data={
            "subject": "Confirm acceptable custodian evidence",
            "message": "Can a signed technology-partner letter be submitted?",
            "related_form": "integration_scope",
        },
    )

    application.refresh_from_db()
    assert application.status == "draft"
    assert query.status == QueryStatus.AWAITING_REVIEWER
    assert query.opened_by == owner
    assert query.assigned_to is None
    assert query.messages.get().author == owner

    post_query_reply(
        thread=query,
        user=reviewer,
        body="Yes, provided it identifies the custodian and product scope.",
    )
    query.refresh_from_db()
    assert query.status == QueryStatus.AWAITING_APPLICANT


def test_full_query_resubmission_and_approval_flow(application, actors):
    _organisation, owner, _contributor, reviewer = actors
    complete_all_forms(application, owner)

    perform_application_action(
        application=application,
        action_key="submit",
        user=owner,
    )
    perform_application_action(
        application=application,
        action_key="start_review",
        user=reviewer,
    )
    _result, query = perform_application_action(
        application=application,
        action_key="raise_query",
        user=reviewer,
        cleaned_data={
            "subject": "Clarify the security assessment scope",
            "message": "Confirm that the gateway callback host was in scope.",
            "related_form": "security_compliance",
            "due_at": timezone.localdate() + timedelta(days=5),
        },
    )
    application.refresh_from_db()
    assert application.status == "changes_requested"
    assert query.status == QueryStatus.AWAITING_APPLICANT

    post_query_reply(
        thread=query,
        user=owner,
        body="The callback host is listed in section 4.2 of the report.",
    )
    query.refresh_from_db()
    assert query.status == QueryStatus.AWAITING_REVIEWER

    perform_application_action(
        application=application,
        action_key="submit",
        user=owner,
    )
    resolve_query(thread=query, user=reviewer)
    perform_application_action(
        application=application,
        action_key="start_review",
        user=reviewer,
    )
    perform_application_action(
        application=application,
        action_key="approve",
        user=reviewer,
        cleaned_data={
            "production_client_id": "PROD-CLIENT-1001",
            "approved_milestones": ["m1", "m2", "m3"],
            "effective_date": timezone.localdate() + timedelta(days=1),
            "certificate_reference": "CERT-2026-1001",
            "note": "All evidence verified.",
        },
    )

    application.refresh_from_db()
    assert application.status == "approved"
    assert application.outcome["production_client_id"] == "PROD-CLIENT-1001"
    assert application.decided_by == reviewer
    assert application.events.filter(action_key="approve").exists()
