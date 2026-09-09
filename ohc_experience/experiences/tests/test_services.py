from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.utils.datastructures import MultiValueDict

from ohc_experience.experiences import permission_keys
from ohc_experience.experiences.models import ApplicationAccess
from ohc_experience.experiences.models import ApplicationFormSubmission
from ohc_experience.experiences.models import QueryStatus
from ohc_experience.experiences.permissions import get_effective_access
from ohc_experience.experiences.services import application_context
from ohc_experience.experiences.services import assignable_roles
from ohc_experience.experiences.services import create_application
from ohc_experience.experiences.services import grant_application_access
from ohc_experience.experiences.services import perform_application_action
from ohc_experience.experiences.services import perform_form_action
from ohc_experience.experiences.services import post_query_reply
from ohc_experience.experiences.services import recalculate_progress
from ohc_experience.experiences.services import resolve_query
from ohc_experience.experiences.services import save_form_submission
from ohc_experience.experiences.tests.sample import SampleExperience
from ohc_experience.experiences.views import _submission_rows
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

APPLICATION_TYPE = SampleExperience.key
FORM_COUNT = 7
BASE_REQUIRED_FORM_COUNT = 6
SCOPED_COMPLETED_FORM_COUNT = 3
SCOPED_PROGRESS_PERCENT = 43
BASE_PROGRESS_PERCENT = 33
MULTI_FILE_COUNT = 2
RENEWED_SUBMISSION_NUMBER = 2
EDITED_REVISION_NUMBER = 2
EDITED_VERSION_COUNT = 2
RENEWAL_HISTORY_VERSION_COUNT = 3
TOTAL_MULTI_FILE_COUNT = 4
MAX_CERTIFICATE_FILES = 5
APPENDED_CERTIFICATE_FILE_COUNT = 3
RETAINED_SUPPORTING_FILE_COUNT = 1


def pdf(name: str, content: bytes = b"%PDF-1.4 sample") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="application/pdf")


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
    Membership.objects.create(organisation=organisation, user=owner, role=Role.OWNER)
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
    for form_definition in SampleExperience.forms:
        ApplicationFormSubmission.objects.create(
            application=application,
            form_key=form_definition.key,
            data={},
            submitted_by=owner,
        )


def test_new_application_grants_owner_permissions_and_gates_forms(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    context = application_context(application, owner)
    states = SampleExperience.form_states(context)

    assert application.reference.startswith("SMP-")
    assert get_effective_access(application, owner).role.key == "applicant_owner"
    assert context.has_permission(permission_keys.SUBMIT_APPLICATION)
    assert states[0].can_submit is True
    assert states[1].visible is False
    assert states[-1].visible is False
    assert application.events.filter(kind="created").exists()


def test_only_members_can_start_an_application(actors):
    organisation, _owner, _contributor, reviewer = actors

    with pytest.raises(PermissionDenied):
        create_application(
            application_type=APPLICATION_TYPE,
            organisation=organisation,
            user=reviewer,
        )


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


def test_damaged_owner_grant_never_strands_the_creator(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    application.access_grants.filter(user=owner).update(role_key="nonsense")

    access = get_effective_access(application, owner)

    assert access.role.key == "applicant_owner"
    assert access.allows(permission_keys.SUBMIT_APPLICATION)


def test_application_actions_use_effective_permissions(application, actors):
    _organisation, owner, _contributor, reviewer = actors
    complete_all_forms(application, owner)
    owner_grant = application.access_grants.get(user=owner)
    owner_grant.direct_permissions = [permission_keys.APPROVE_APPLICATION]
    owner_grant.save(update_fields=["direct_permissions", "updated_at"])

    owner_states = SampleExperience.action_states(
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
    reviewer_states = SampleExperience.action_states(
        application_context(application, reviewer),
    )
    reviewer_actions = {item.definition.key: item.available for item in reviewer_states}
    assert application.status == "submitted"
    assert application.submitted_at is not None
    assert reviewer_actions["start_review"] is True
    assert reviewer_actions["ask_review_team"] is False
    assert "withdraw" not in reviewer_actions


def test_progress_uses_forms_applicable_to_the_current_scope(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    for form_key, data in (
        ("profile", {}),
        ("scope", {"channels": ["web", "locker"]}),
        ("locker_operations", {}),
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
    locker_state = next(
        state
        for state in SampleExperience.form_states(context)
        if state.definition.key == "locker_operations"
    )

    assert application.metadata["completed_forms"] == SCOPED_COMPLETED_FORM_COUNT
    assert application.metadata["required_forms"] == FORM_COUNT
    assert application.progress_percent == SCOPED_PROGRESS_PERCENT
    assert locker_state.applicable is True
    assert locker_state.visible is True

    scope = application.submissions.get(form_key="scope")
    scope.data = {"channels": ["web"]}
    scope.save(update_fields=["data", "updated_at"])
    recalculate_progress(application, user=owner)
    application.refresh_from_db()
    context = application_context(application, owner)
    locker_state = next(
        state
        for state in SampleExperience.form_states(context)
        if state.definition.key == "locker_operations"
    )

    assert application.metadata["required_forms"] == BASE_REQUIRED_FORM_COUNT
    assert application.progress_percent == BASE_PROGRESS_PERCENT
    assert locker_state.applicable is False
    assert locker_state.visible is False


def test_completed_form_update_policy_is_declared_per_form(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    complete_all_forms(application, owner)
    context = application_context(application, owner)

    profile_available, _reason = SampleExperience.get_form("profile").availability(
        context,
    )
    declaration_available, declaration_reason = SampleExperience.get_form(
        "declaration",
    ).availability(context)

    assert profile_available is True
    assert declaration_available is False
    assert "locked" in str(declaration_reason).lower()


def test_editable_form_saves_immutable_revisions(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    form_definition = SampleExperience.get_form("profile")
    first_data = {
        "legal_name": "Original Health Private Limited",
        "contact_email": owner.email,
    }
    first_form = form_definition.build_form(
        context=application_context(application, owner),
        data=first_data,
    )
    assert first_form.is_valid(), first_form.errors
    first = save_form_submission(
        application=application,
        form_key=form_definition.key,
        form=first_form,
        user=owner,
    )
    first.field_schema[0]["label"] = "Historic legal name"
    first.save(update_fields=["field_schema"])
    application.refresh_from_db()
    assert application.metadata["legal_name"] == "Original Health Private Limited"

    updated_data = {**first_data, "legal_name": "Renamed Health Private Limited"}
    updated_form = form_definition.build_form(
        context=application_context(application, owner),
        data=updated_data,
        submission_mode="edit",
    )
    assert updated_form.is_valid(), updated_form.errors
    updated = save_form_submission(
        application=application,
        form_key=form_definition.key,
        form=updated_form,
        user=owner,
        submission_mode="edit",
    )
    first.refresh_from_db()

    assert first.is_current is False
    assert first.data["legal_name"] == "Original Health Private Limited"
    assert updated.is_current is True
    assert updated.submission_number == first.submission_number
    assert updated.revision == EDITED_REVISION_NUMBER
    assert (
        application.submissions.filter(form_key=form_definition.key).count()
        == EDITED_VERSION_COUNT
    )
    historical_rows = _submission_rows(form_definition, first)
    assert historical_rows[0]["label"] == "Historic legal name"
    assert historical_rows[0]["value"] == "Original Health Private Limited"


def test_repeatable_form_retains_history_and_multiple_file_groups(  # noqa: PLR0915
    application,
    actors,
):
    _organisation, owner, _contributor, _reviewer = actors
    complete_all_forms(application, owner)
    application.status = "approved"
    application.save(update_fields=["status", "updated_at"])
    current = application.submissions.get(form_key="certification", is_current=True)
    current.data = {
        "certificate_number": "CERT-OLD",
        "expires_on": (timezone.localdate() + timedelta(days=10)).isoformat(),
    }
    current.valid_until = timezone.localdate() + timedelta(days=10)
    current.save(update_fields=["data", "valid_until", "updated_at"])

    context = application_context(application, owner)
    state = next(
        item
        for item in SampleExperience.form_states(context)
        if item.definition.key == "certification"
    )
    assert state.renewal_due is True
    assert state.action_label == "Renew"
    assert state.can_submit is True

    form_definition = SampleExperience.get_form("certification")
    renewal_form = form_definition.build_form(context=context)
    edit_current_form = form_definition.build_form(
        context=context,
        submission_mode="edit",
    )
    assert renewal_form.initial == {}
    assert renewal_form.existing_files == {}
    assert edit_current_form.initial["certificate_number"] == "CERT-OLD"
    renewal_data = {
        "certificate_number": "ISO-NEW-2026",
        "issued_on": (timezone.localdate() - timedelta(days=5)).isoformat(),
        "expires_on": (timezone.localdate() + timedelta(days=365)).isoformat(),
    }
    form = form_definition.build_form(
        context=context,
        data=renewal_data,
        files=MultiValueDict(
            {
                "certificate_documents": [
                    pdf("certificate.pdf"),
                    pdf("scope-annexure.pdf"),
                ],
                "supporting_documents": [
                    pdf("control-map.pdf"),
                    SimpleUploadedFile(
                        "audit-cover.png",
                        b"image",
                        content_type="image/png",
                    ),
                ],
            },
        ),
    )
    assert form.is_valid(), form.errors

    renewed = save_form_submission(
        application=application,
        form_key="certification",
        form=form,
        user=owner,
        submission_mode="renew",
    )
    current.refresh_from_db()
    application.refresh_from_db()

    assert application.status == "approved"
    assert current.is_current is False
    assert renewed.is_current is True
    assert renewed.submission_number == RENEWED_SUBMISSION_NUMBER
    assert renewed.valid_until == timezone.localdate() + timedelta(days=365)
    assert len(renewed.data["certificate_documents"]) == MULTI_FILE_COUNT
    assert len(renewed.data["supporting_documents"]) == MULTI_FILE_COUNT
    assert (
        renewed.attachments.filter(field_key="certificate_documents").count()
        == MULTI_FILE_COUNT
    )
    assert (
        renewed.attachments.filter(field_key="supporting_documents").count()
        == MULTI_FILE_COUNT
    )
    renewed_context = application_context(application, owner)
    too_many_files_form = form_definition.build_form(
        context=renewed_context,
        data={**renewal_data, "certificate_number": "ISO-TOO-MANY-2026"},
        files=MultiValueDict(
            {
                "certificate_documents": [
                    pdf(f"extra-{index}.pdf")
                    for index in range(MAX_CERTIFICATE_FILES - 1)
                ],
            },
        ),
        submission_mode="edit",
    )
    assert too_many_files_form.is_valid() is False
    assert "no more than 5 files" in str(
        too_many_files_form.errors["certificate_documents"],
    )

    supporting_to_remove = renewed.attachments.filter(
        field_key="supporting_documents",
        is_current=True,
    ).first()
    assert supporting_to_remove is not None
    edited_form = form_definition.build_form(
        context=renewed_context,
        data={
            **renewal_data,
            "certificate_number": "ISO-EDITED-2026",
            "remove_files__supporting_documents": str(supporting_to_remove.pk),
        },
        files=MultiValueDict({"certificate_documents": [pdf("new-annexure.pdf")]}),
        submission_mode="edit",
    )
    assert edited_form.is_valid(), edited_form.errors
    edited = save_form_submission(
        application=application,
        form_key="certification",
        form=edited_form,
        user=owner,
        submission_mode="edit",
    )
    renewed.refresh_from_db()

    assert renewed.is_current is False
    assert edited.submission_number == RENEWED_SUBMISSION_NUMBER
    assert edited.revision == EDITED_REVISION_NUMBER
    assert edited.data["certificate_number"] == "ISO-EDITED-2026"
    assert len(edited.data["certificate_documents"]) == APPENDED_CERTIFICATE_FILE_COUNT
    assert len(edited.data["supporting_documents"]) == RETAINED_SUPPORTING_FILE_COUNT
    assert set(
        edited.attachments.filter(
            field_key="certificate_documents",
            is_current=True,
        ).values_list("original_name", flat=True),
    ) == {"certificate.pdf", "scope-annexure.pdf", "new-annexure.pdf"}
    assert not edited.attachments.filter(
        original_name=supporting_to_remove.original_name,
        is_current=True,
    ).exists()
    assert edited.attachments.filter(is_current=True).count() == TOTAL_MULTI_FILE_COUNT
    assert renewed.attachments.filter(is_current=True).count() == TOTAL_MULTI_FILE_COUNT
    assert renewed.attachments.filter(pk=supporting_to_remove.pk).exists()
    history = application_context(application, owner).form_history("certification")
    assert len(history) == RENEWAL_HISTORY_VERSION_COUNT
    assert (
        len({item.submission_number for item in history}) == RENEWED_SUBMISSION_NUMBER
    )


def test_expired_repeatable_form_is_no_longer_complete(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    complete_all_forms(application, owner)
    certification = application.submissions.get(
        form_key="certification",
        is_current=True,
    )
    certification.valid_until = timezone.localdate() - timedelta(days=1)
    certification.save(update_fields=["valid_until", "updated_at"])

    recalculate_progress(application, user=owner)
    application.refresh_from_db()
    context = application_context(application, owner)
    state = next(
        item
        for item in SampleExperience.form_states(context)
        if item.definition.key == "certification"
    )

    assert state.is_expired is True
    assert state.is_completed is False
    assert application.metadata["completed_forms"] == BASE_REQUIRED_FORM_COUNT - 1
    assert application.metadata["required_forms"] == BASE_REQUIRED_FORM_COUNT


def test_admin_form_action_runs_after_completion(application, actors):
    _organisation, owner, _contributor, reviewer = actors
    complete_all_forms(application, owner)
    application.status = "under_review"
    application.save(update_fields=["status", "updated_at"])

    with pytest.raises(PermissionDenied):
        perform_form_action(
            application=application,
            form_key="compliance",
            action_key="verify_evidence",
            user=owner,
        )

    result = perform_form_action(
        application=application,
        form_key="compliance",
        action_key="verify_evidence",
        user=reviewer,
    )
    submission = application.submissions.get(form_key="compliance")

    assert str(result.message) == "Evidence verified"
    assert submission.metadata["verified_revision"] == submission.revision
    assert submission.metadata["verified_by"] == reviewer.pk
    assert application.events.filter(
        action_key="compliance.verify_evidence",
        submission=submission,
    ).exists()

    with pytest.raises(PermissionDenied):
        perform_form_action(
            application=application,
            form_key="compliance",
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
            "subject": "Confirm acceptable evidence",
            "message": "Can a signed partner letter be submitted?",
            "related_form": "scope",
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
        body="Yes, provided it names the product scope.",
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
            "subject": "Clarify the assessment scope",
            "message": "Confirm that the production host was in scope.",
            "related_form": "compliance",
            "due_at": timezone.localdate() + timedelta(days=5),
        },
    )
    application.refresh_from_db()
    assert application.status == "changes_requested"
    assert query.status == QueryStatus.AWAITING_APPLICANT
    assert query.submission == application.submissions.get(form_key="compliance")

    blocked = {
        item.definition.key: item
        for item in SampleExperience.action_states(
            application_context(application, owner),
        )
    }
    assert blocked["submit"].available is False
    assert "open query" in str(blocked["submit"].reason)

    post_query_reply(
        thread=query,
        user=owner,
        body="The host is listed in section 4.2 of the report.",
    )
    query.refresh_from_db()
    assert query.status == QueryStatus.AWAITING_REVIEWER

    perform_application_action(
        application=application,
        action_key="submit",
        user=owner,
    )
    application.refresh_from_db()
    assert application.status == "revision_submitted"
    assert application.metadata["submission_count"] == EDITED_REVISION_NUMBER

    with pytest.raises(PermissionDenied):
        perform_application_action(
            application=application,
            action_key="approve",
            user=reviewer,
            cleaned_data={},
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
            "client_id": "CLIENT-1001",
            "effective_date": timezone.localdate() + timedelta(days=1),
            "note": "All evidence verified.",
        },
    )

    application.refresh_from_db()
    assert application.status == "approved"
    assert application.outcome["client_id"] == "CLIENT-1001"
    assert application.decided_by == reviewer
    assert application.decided_at is not None
    assert application.events.filter(action_key="approve").exists()
