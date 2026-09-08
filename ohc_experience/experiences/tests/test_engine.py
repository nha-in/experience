from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.utils.datastructures import MultiValueDict

from ohc_experience.experiences import permission_keys
from ohc_experience.experiences.models import ApplicationAccess
from ohc_experience.experiences.models import ApplicationDependency
from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import FormReuseScope
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.experiences.models import ProductType
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
from ohc_experience.experiences.services import save_form_submission
from ohc_experience.experiences.views import _submission_rows
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

APPLICATION_TYPE = "abdm_production_access"
FORM_COUNT = 11
BASE_REQUIRED_FORM_COUNT = 8
HEALTH_LOCKER_REQUIRED_FORM_COUNT = 9
PROTOCOL_REQUIRED_FORM_COUNT = 10
SCOPED_COMPLETED_FORM_COUNT = 3
SCOPED_PROGRESS_PERCENT = 33
PROTOCOL_PROGRESS_PERCENT = 30
BASE_PROGRESS_PERCENT = 38
MULTI_FILE_COUNT = 2
RENEWED_SUBMISSION_NUMBER = 2
EDITED_REVISION_NUMBER = 2
EDITED_VERSION_COUNT = 2
RENEWAL_HISTORY_VERSION_COUNT = 3
SHARED_FORM_HISTORY_VERSION_COUNT = 3
TOTAL_MULTI_FILE_COUNT = 4
MAX_CERTIFICATE_FILES = 5
APPENDED_CERTIFICATE_FILE_COUNT = 3
RETAINED_SUPPORTING_FILE_COUNT = 1


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
    product = Product.objects.create(
        organisation=organisation,
        name="Test Health Platform",
        product_type=ProductType.HMIS,
        description="Product used by the experience engine tests.",
        created_by=owner,
    )
    application = create_application(
        application_type=APPLICATION_TYPE,
        product=product,
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
        create_direct_submission(
            application,
            form_definition.key,
            {},
            owner,
        )


def create_direct_submission(application, form_key, data, user):
    form_use = application.form_uses.select_related("form").get(form_key=form_key)
    form_use.form.submissions.filter(is_current=True).update(is_current=False)
    latest = form_use.form.submissions.order_by(
        "-submission_number",
        "-revision",
    ).first()
    submission = FormSubmission.objects.create(
        form=form_use.form,
        origin_application=application,
        form_key=form_key,
        data=data,
        submitted_by=user,
        submission_number=latest.submission_number if latest else 1,
        revision=latest.revision + 1 if latest else 1,
    )
    form_use.selected_submission = submission
    form_use.save(update_fields=["selected_submission", "updated_at"])
    return submission


def selected_submission(application, form_key):
    return (
        application.form_uses.select_related("selected_submission")
        .get(
            form_key=form_key,
        )
        .selected_submission
    )


def form_history(application, form_key):
    return application.form_uses.get(form_key=form_key).form.submissions.all()


def organisation_profile_data(owner, legal_entity_name):
    return {
        "legal_entity_name": legal_entity_name,
        "organisation_type": "company",
        "registration_number": "U12345KA2024PTC123456",
        "registered_address": "1 Original Road",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pincode": "560001",
        "website": "https://original.example.in",
        "authorised_contact_name": owner.display_name,
        "authorised_contact_email": owner.email,
        "authorised_contact_phone": "+91 90000 00000",
    }


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


def test_application_materializes_scoped_forms_and_sandbox_outcome(
    application,
    actors,
):
    _organisation, owner, _contributor, _reviewer = actors
    form_uses = {
        item.form_key: item for item in application.form_uses.select_related("form")
    }

    assert len(form_uses) == FORM_COUNT
    assert (
        form_uses["organisation_profile"].form.reuse_scope
        == FormReuseScope.ORGANISATION
    )
    assert form_uses["organisation_profile"].form.product is None
    assert form_uses["application_plan"].form.reuse_scope == FormReuseScope.PRODUCT
    assert form_uses["application_plan"].form.product == application.product
    assert form_uses["declaration"].form.reuse_scope == FormReuseScope.APPLICATION

    outcome = ProductOutcome.objects.get(
        product=application.product,
        source_application=application,
        outcome_type="sandbox_credentials",
    )
    assert outcome.data["environment"] == "sandbox"
    assert outcome.data["client_id"].startswith("sbx_test_health_platform_")
    assert outcome.data["client_secret"]
    assert outcome.issued_by == owner


def test_new_application_reuses_forms_but_pins_its_selected_revision(
    application,
    actors,
):
    _organisation, owner, _contributor, _reviewer = actors
    shared_profile = create_direct_submission(
        application,
        "organisation_profile",
        {"legal_entity_name": "Original organisation"},
        owner,
    )
    shared_plan = create_direct_submission(
        application,
        "application_plan",
        {"product_version": "1.0"},
        owner,
    )
    private_declaration = create_direct_submission(
        application,
        "declaration",
        {"signatory_name": "First signatory"},
        owner,
    )

    second = create_application(
        application_type=APPLICATION_TYPE,
        product=application.product,
        user=owner,
    )
    second_context = application_context(second, owner)

    assert second_context.submissions["organisation_profile"] == shared_profile
    assert second_context.submissions["application_plan"] == shared_plan
    assert "declaration" not in second_context.submissions
    assert (
        second_context.form_records["organisation_profile"]
        == application_context(application, owner).form_records["organisation_profile"]
    )
    assert (
        second_context.form_records["declaration"]
        != application_context(application, owner).form_records["declaration"]
    )
    assert private_declaration.form.application_uses.count() == 1

    second_plan = create_direct_submission(
        second,
        "application_plan",
        {"product_version": "2.0"},
        owner,
    )

    assert selected_submission(application, "application_plan") == shared_plan
    assert selected_submission(second, "application_plan") == second_plan
    shared_plan.refresh_from_db()
    assert shared_plan.is_current is False
    assert second_plan.revision == shared_plan.revision + 1


def test_ohc_team_implicitly_observes_unassigned_applications(application):
    observer = UserFactory(
        email="observer@ohc.network",
        is_ohc_team=True,
        is_staff=True,
    )

    access = get_effective_access(application, observer)

    assert ApplicationInstance.objects.visible_to(observer).contains(application)
    assert access.role.key == "review_observer"
    assert access.grant is None
    assert access.allows(permission_keys.VIEW_APPLICATION)
    assert access.allows(permission_keys.VIEW_QUERIES)
    assert not access.allows(permission_keys.APPROVE_APPLICATION)


def test_application_dependencies_are_same_product_and_acyclic(application, actors):
    organisation, owner, _contributor, _reviewer = actors
    dependent = create_application(
        application_type=APPLICATION_TYPE,
        product=application.product,
        user=owner,
        dependencies=[application],
    )

    assert list(dependent.dependencies.all()) == [application]

    cycle = ApplicationDependency(
        application=application,
        depends_on=dependent,
    )
    with pytest.raises(ValidationError, match="cycle"):
        cycle.full_clean()

    other_product = Product.objects.create(
        organisation=organisation,
        name="Independent Claims Platform",
        product_type=ProductType.CLAIMS,
        description="A second product owned by the same organisation.",
        created_by=owner,
    )
    other_application = create_application(
        application_type=APPLICATION_TYPE,
        product=other_product,
        user=owner,
    )
    cross_product = ApplicationDependency(
        application=dependent,
        depends_on=other_application,
    )

    with pytest.raises(ValidationError, match="same product"):
        cross_product.full_clean()
    with pytest.raises(ValidationError, match="same product"):
        create_application(
            application_type=APPLICATION_TYPE,
            product=application.product,
            user=owner,
            dependencies=[other_application],
        )


def test_submit_waits_for_prerequisite_approval(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    prerequisite = create_application(
        application_type=APPLICATION_TYPE,
        product=application.product,
        user=owner,
    )
    ApplicationDependency.objects.create(
        application=application,
        depends_on=prerequisite,
    )
    complete_all_forms(application, owner)
    submit = registry.get(APPLICATION_TYPE).get_action("submit")

    available, reason = submit.availability(application_context(application, owner))

    assert available is False
    assert prerequisite.reference in str(reason)

    prerequisite.status = "approved"
    prerequisite.save(update_fields=["status", "updated_at"])
    available, reason = submit.availability(application_context(application, owner))

    assert available is True
    assert reason == ""


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
        ("application_plan", {}),
        ("integration_scope", {"abdm_roles": ["hip", "health_locker"]}),
    ):
        create_direct_submission(application, form_key, data, owner)

    recalculate_progress(application, user=owner)
    application.refresh_from_db()
    context = application_context(application, owner)
    health_locker_state = next(
        state
        for state in registry.get(APPLICATION_TYPE).form_states(context)
        if state.definition.key == "health_locker_operations"
    )

    assert application.metadata["completed_forms"] == SCOPED_COMPLETED_FORM_COUNT
    assert application.metadata["required_forms"] == HEALTH_LOCKER_REQUIRED_FORM_COUNT
    assert application.progress_percent == SCOPED_PROGRESS_PERCENT
    assert health_locker_state.applicable is True
    assert health_locker_state.visible is True

    integration = selected_submission(application, "integration_scope")
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


def test_protocol_forms_are_applicable_only_when_selected(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    for form_key, data in (
        ("organisation_profile", {}),
        ("application_plan", {}),
        (
            "integration_scope",
            {"abdm_roles": [], "protocols": ["nhcx", "uhi"]},
        ),
    ):
        create_direct_submission(application, form_key, data, owner)

    recalculate_progress(application, user=owner)
    application.refresh_from_db()
    context = application_context(application, owner)
    states = {
        state.definition.key: state
        for state in registry.get(APPLICATION_TYPE).form_states(context)
    }

    assert application.metadata["required_forms"] == PROTOCOL_REQUIRED_FORM_COUNT
    assert application.progress_percent == PROTOCOL_PROGRESS_PERCENT
    assert states["nhcx_integration"].applicable is True
    assert states["nhcx_integration"].visible is True
    assert states["uhi_integration"].applicable is True
    assert states["uhi_integration"].visible is True
    assert states["health_locker_operations"].applicable is False

    integration = selected_submission(application, "integration_scope")
    integration.data = {"abdm_roles": [], "protocols": ["nhcx"]}
    integration.save(update_fields=["data", "updated_at"])
    recalculate_progress(application, user=owner)
    application.refresh_from_db()
    states = {
        state.definition.key: state
        for state in registry.get(APPLICATION_TYPE).form_states(
            application_context(application, owner),
        )
    }

    assert application.metadata["required_forms"] == (BASE_REQUIRED_FORM_COUNT + 1)
    assert states["nhcx_integration"].applicable is True
    assert states["uhi_integration"].applicable is False
    assert states["uhi_integration"].visible is False


def test_phr_and_health_locker_require_their_milestones(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    application.product.product_type = ProductType.PHR
    application.product.save(update_fields=["product_type", "updated_at"])
    form_definition = registry.get(APPLICATION_TYPE).get_form("integration_scope")
    context = application_context(application, owner)
    phr_form = form_definition.build_form(
        context=context,
        data={
            "abdm_roles": [],
            "protocols": ["uhi"],
            "milestones": ["m1"],
            "sandbox_client_id": "SBX-PHR-001",
            "integration_approach": "direct",
        },
    )

    assert phr_form.is_valid() is False
    assert "PHR application requires M3" in str(phr_form.errors["milestones"])
    assert "PHR application requires the HIU role" in str(
        phr_form.errors["abdm_roles"],
    )

    application.product.product_type = ProductType.HEALTH_LOCKER
    application.product.save(update_fields=["product_type", "updated_at"])
    locker_form = form_definition.build_form(
        context=application_context(application, owner),
        data={
            "abdm_roles": ["health_locker"],
            "protocols": [],
            "milestones": ["m1"],
            "sandbox_client_id": "SBX-LOCKER-001",
            "hfr_facility_ids": "IN2910000123",
            "health_information_types": ["diagnostic_report"],
            "integration_approach": "direct",
        },
    )

    assert locker_form.is_valid() is False
    assert "requires both M2 and M3" in str(locker_form.errors["milestones"])
    assert "requires HIP, HIU, and Health locker roles" in str(
        locker_form.errors["abdm_roles"],
    )

    application.product.product_type = ProductType.PHR
    application.product.save(update_fields=["product_type", "updated_at"])
    valid_phr_form = form_definition.build_form(
        context=application_context(application, owner),
        data={
            "abdm_roles": ["hiu"],
            "protocols": [],
            "milestones": ["m3"],
            "sandbox_client_id": "SBX-PHR-001",
            "hfr_facility_ids": "IN2910000123",
            "health_information_types": ["diagnostic_report"],
            "integration_approach": "direct",
        },
    )

    assert valid_phr_form.is_valid() is True

    application.product.product_type = ProductType.HEALTH_LOCKER
    application.product.save(update_fields=["product_type", "updated_at"])
    valid_locker_form = form_definition.build_form(
        context=application_context(application, owner),
        data={
            "abdm_roles": ["hip", "hiu", "health_locker"],
            "protocols": [],
            "milestones": ["m2", "m3"],
            "sandbox_client_id": "SBX-LOCKER-001",
            "hfr_facility_ids": "IN2910000123",
            "health_information_types": ["diagnostic_report"],
            "integration_approach": "direct",
        },
    )

    assert valid_locker_form.is_valid() is True


def test_nhcx_and_uhi_forms_save_multi_file_evidence(
    application,
    actors,
    settings,
    tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    _organisation, owner, _contributor, _reviewer = actors
    for form_key, data in (
        ("organisation_profile", {}),
        ("application_plan", {}),
        (
            "integration_scope",
            {"abdm_roles": [], "protocols": ["nhcx", "uhi"]},
        ),
    ):
        create_direct_submission(application, form_key, data, owner)

    definition = registry.get(APPLICATION_TYPE)
    context = application_context(application, owner)
    nhcx_definition = definition.get_form("nhcx_integration")
    nhcx_form = nhcx_definition.build_form(
        context=context,
        data={
            "participant_roles": ["provider", "technology_provider"],
            "participant_id": "NHCX-TEST-001",
            "protocol_version": "0.9",
            "production_callback_url": "https://claims.example.in/callback",
            "public_key_url": "https://claims.example.in/jwks.json",
            "claim_use_cases": ["preauthorization", "claim", "status"],
            "claim_modes": ["cashless"],
            "sandbox_test_reference_ids": "nhcx-test-1001",
            "signed_encrypted_payloads": True,
            "asynchronous_idempotency": True,
            "fhir_validation": True,
            "synthetic_data_only": True,
        },
        files=MultiValueDict(
            {
                "conformance_documents": [
                    SimpleUploadedFile(
                        "nhcx-report.pdf",
                        b"nhcx report",
                        content_type="application/pdf",
                    ),
                    SimpleUploadedFile(
                        "nhcx-results.json",
                        b"{}",
                        content_type="application/json",
                    ),
                ],
            },
        ),
    )
    assert nhcx_form.is_valid(), nhcx_form.errors
    nhcx_submission = save_form_submission(
        application=application,
        form_key=nhcx_definition.key,
        form=nhcx_form,
        user=owner,
    )

    uhi_definition = definition.get_form("uhi_integration")
    uhi_form = uhi_definition.build_form(
        context=application_context(application, owner),
        data={
            "participant_role": "eua_hsp",
            "subscriber_id": "uhi.example.in",
            "protocol_version": "0.0.1",
            "production_callback_url": "https://care.example.in/uhi/callback",
            "service_categories": ["teleconsultation", "appointment_booking"],
            "supported_flows": ["discovery", "confirmation", "status"],
            "hpr_hfr_registry_ids": "71-2345-6789-0123",
            "sandbox_transaction_ids": "uhi-test-2001",
            "catalog_current": True,
            "signed_callbacks": True,
            "consent_and_privacy": True,
            "grievance_email": "support@example.in",
        },
        files=MultiValueDict(
            {
                "conformance_documents": [
                    SimpleUploadedFile(
                        "uhi-report.pdf",
                        b"uhi report",
                        content_type="application/pdf",
                    ),
                ],
            },
        ),
    )
    assert uhi_form.is_valid(), uhi_form.errors
    uhi_submission = save_form_submission(
        application=application,
        form_key=uhi_definition.key,
        form=uhi_form,
        user=owner,
    )

    assert nhcx_submission.attachments.count() == MULTI_FILE_COUNT
    assert len(nhcx_submission.data["conformance_documents"]) == MULTI_FILE_COUNT
    assert uhi_submission.attachments.count() == 1
    assert len(uhi_submission.data["conformance_documents"]) == 1


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


def test_editable_form_saves_immutable_revisions(application, actors):
    organisation, owner, _contributor, _reviewer = actors
    form_definition = registry.get(APPLICATION_TYPE).get_form(
        "organisation_profile",
    )
    first_data = organisation_profile_data(
        owner,
        "Original Health Private Limited",
    )
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
    first.field_schema[0]["label"] = "Historic registered name"
    first.save(update_fields=["field_schema"])

    updated_data = {**first_data, "legal_entity_name": organisation.display_name}
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
    assert first.data["legal_entity_name"] == "Original Health Private Limited"
    assert updated.is_current is True
    assert updated.submission_number == first.submission_number
    assert updated.revision == EDITED_REVISION_NUMBER
    assert (
        form_history(application, form_definition.key).count() == EDITED_VERSION_COUNT
    )
    historical_rows = _submission_rows(form_definition, first)
    assert historical_rows[0]["label"] == "Historic registered name"
    assert historical_rows[0]["value"] == "Original Health Private Limited"


def test_shared_form_updates_keep_each_application_revision_pinned(
    application,
    actors,
):
    _organisation, owner, _contributor, _reviewer = actors
    form_definition = registry.get(APPLICATION_TYPE).get_form(
        "organisation_profile",
    )
    first_form = form_definition.build_form(
        context=application_context(application, owner),
        data=organisation_profile_data(owner, "First legal name"),
    )
    assert first_form.is_valid(), first_form.errors
    first = save_form_submission(
        application=application,
        form_key=form_definition.key,
        form=first_form,
        user=owner,
    )

    second_application = create_application(
        application_type=APPLICATION_TYPE,
        product=application.product,
        user=owner,
    )
    assert selected_submission(second_application, form_definition.key) == first

    second_form = form_definition.build_form(
        context=application_context(second_application, owner),
        data=organisation_profile_data(owner, "Second legal name"),
        submission_mode="edit",
    )
    assert second_form.is_valid(), second_form.errors
    second = save_form_submission(
        application=second_application,
        form_key=form_definition.key,
        form=second_form,
        user=owner,
        submission_mode="edit",
    )

    assert selected_submission(application, form_definition.key) == first
    assert selected_submission(second_application, form_definition.key) == second

    first_edit_form = form_definition.build_form(
        context=application_context(application, owner),
        data=organisation_profile_data(owner, "First application correction"),
        submission_mode="edit",
    )
    assert first_edit_form.is_valid(), first_edit_form.errors
    first_correction = save_form_submission(
        application=application,
        form_key=form_definition.key,
        form=first_edit_form,
        user=owner,
        submission_mode="edit",
    )

    assert first_correction.revision == second.revision + 1
    assert selected_submission(application, form_definition.key) == first_correction
    assert selected_submission(second_application, form_definition.key) == second
    assert (
        form_history(application, form_definition.key).count()
        == SHARED_FORM_HISTORY_VERSION_COUNT
    )


def test_repeatable_form_retains_history_and_multiple_file_groups(  # noqa: PLR0915
    application,
    actors,
    settings,
    tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    _organisation, owner, _contributor, _reviewer = actors
    complete_all_forms(application, owner)
    application.status = "approved"
    application.save(update_fields=["status", "updated_at"])
    current = selected_submission(application, "security_certification")
    current.data = {
        "certificate_number": "CERT-OLD",
        "expires_on": (timezone.localdate() + timedelta(days=10)).isoformat(),
    }
    current.valid_until = timezone.localdate() + timedelta(days=10)
    current.save(update_fields=["data", "valid_until", "updated_at"])

    definition = registry.get(APPLICATION_TYPE)
    context = application_context(application, owner)
    state = next(
        item
        for item in definition.form_states(context)
        if item.definition.key == "security_certification"
    )
    assert state.renewal_due is True
    assert state.action_label == "Renew"
    assert state.can_submit is True

    form_definition = definition.get_form("security_certification")
    renewal_form = form_definition.build_form(context=context)
    edit_current_form = form_definition.build_form(
        context=context,
        submission_mode="edit",
    )
    assert renewal_form.initial == {}
    assert renewal_form.existing_files == {}
    assert edit_current_form.initial["certificate_number"] == "CERT-OLD"
    renewal_data = {
        "certification_type": "iso_27001",
        "certification_name": "ISO 27001 certification",
        "issuing_body": "Example Assurance Body",
        "certificate_number": "ISO-NEW-2026",
        "issued_on": (timezone.localdate() - timedelta(days=5)).isoformat(),
        "expires_on": (timezone.localdate() + timedelta(days=365)).isoformat(),
        "scope_summary": "ABDM production services and supporting cloud controls.",
    }
    form = form_definition.build_form(
        context=context,
        data=renewal_data,
        files=MultiValueDict(
            {
                "certificate_documents": [
                    SimpleUploadedFile(
                        "certificate.pdf",
                        b"certificate",
                        content_type="application/pdf",
                    ),
                    SimpleUploadedFile(
                        "scope-annexure.pdf",
                        b"annexure",
                        content_type="application/pdf",
                    ),
                ],
                "supporting_documents": [
                    SimpleUploadedFile(
                        "control-map.pdf",
                        b"controls",
                        content_type="application/pdf",
                    ),
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
        form_key="security_certification",
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
                    SimpleUploadedFile(
                        f"extra-{index}.pdf",
                        b"extra",
                        content_type="application/pdf",
                    )
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
        files=MultiValueDict(
            {
                "certificate_documents": [
                    SimpleUploadedFile(
                        "new-annexure.pdf",
                        b"new annexure",
                        content_type="application/pdf",
                    ),
                ],
            },
        ),
        submission_mode="edit",
    )
    assert edited_form.is_valid(), edited_form.errors
    edited = save_form_submission(
        application=application,
        form_key="security_certification",
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
    history = application_context(application, owner).form_history(
        "security_certification",
    )
    assert len(history) == RENEWAL_HISTORY_VERSION_COUNT
    assert (
        len({item.submission_number for item in history}) == RENEWED_SUBMISSION_NUMBER
    )


def test_expired_repeatable_form_is_no_longer_complete(application, actors):
    _organisation, owner, _contributor, _reviewer = actors
    complete_all_forms(application, owner)
    certification = selected_submission(application, "security_certification")
    certification.valid_until = timezone.localdate() - timedelta(days=1)
    certification.save(update_fields=["valid_until", "updated_at"])

    recalculate_progress(application, user=owner)
    application.refresh_from_db()
    context = application_context(application, owner)
    state = next(
        item
        for item in registry.get(APPLICATION_TYPE).form_states(context)
        if item.definition.key == "security_certification"
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
    submission = selected_submission(application, "security_compliance")

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
    production_outcome = ProductOutcome.objects.get(
        product=application.product,
        source_application=application,
        outcome_type="production_access",
    )
    assert production_outcome.data["production_client_id"] == "PROD-CLIENT-1001"
    assert production_outcome.data["approved_milestones"] == ["m1", "m2", "m3"]
    assert production_outcome.issued_by == reviewer
