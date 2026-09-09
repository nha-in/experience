"""Existing UHI applications move to participation without losing their history."""

from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import uhi_data
from ohc_experience.abdm.tests import test_workflow as workflow_fixtures
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import ApplicationFormUse
from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import FormRecord
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Notification
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.models import ReviewQuery

pytestmark = pytest.mark.django_db
environment = workflow_fixtures.environment

UHI_FORM_KEY = "sandbox_uhi_participation"
UHI_APPLICATION_KEY = "abdm_uhi_participation"


def run_upgrade():
    migration = import_module(
        "ohc_experience.experiences.migrations.0011_upgrade_uhi_participation",
    )
    historical_apps = (
        MigrationExecutor(connection)
        .loader.project_state(
            [("experiences", "0010_notification_delivery")],
        )
        .apps
    )
    with connection.schema_editor() as editor:
        migration.upgrade_uhi_participation(historical_apps, editor)


@pytest.fixture
def legacy_uhi(environment):
    m1 = approve(environment)
    item = milestone(environment, "uhi1")
    unused_form = item.form
    ApplicationInstance.objects.filter(pk=item.application_id).update(
        application_type="abdm_sandbox_exit",
    )
    ApplicationFormUse.objects.filter(application_id=item.application_id).update(
        form=m1.form,
        form_key=m1.form.form_key,
        selected_submission=None,
    )
    ReviewItem.objects.filter(pk=item.pk).update(
        form=m1.form,
        selected_submission=None,
    )
    unused_form.delete()
    item.refresh_from_db()
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=evidence_data(),
        files=files(),
    )
    assert saved, form.errors
    return item


def next_snapshot(item, data):
    """Build the historical revisions that older registrations could contain."""
    previous = item.selected_submission
    previous.form.submissions.filter(is_current=True).update(is_current=False)
    snapshot = FormSubmission.objects.create(
        form=previous.form,
        origin_application=item.application,
        form_key=previous.form_key,
        data=data,
        field_schema=previous.field_schema,
        submission_number=previous.submission_number,
        revision=previous.revision + 1,
        submitted_by=previous.submitted_by,
    )
    ReviewItem.objects.filter(pk=item.pk).update(selected_submission=snapshot)
    ApplicationFormUse.objects.filter(application=item.application).update(
        selected_submission=snapshot,
    )
    item.refresh_from_db()
    return snapshot


def rows(model, **filters):
    return list(model.objects.filter(**filters).order_by("pk").values())


@pytest.mark.parametrize(
    ("status", "dependency_ready", "enabled"),
    [
        ("draft", True, True),
        ("new", True, True),
        ("in_review", True, True),
        ("query_raised", True, True),
        ("sent_back", True, True),
        ("approved", True, True),
        ("draft", False, True),
        ("draft", True, False),
    ],
)
def test_upgrade_preserves_identity_and_history(  # noqa: PLR0915
    environment,
    legacy_uhi,
    status,
    dependency_ready,
    enabled,
):
    item = legacy_uhi
    old_submission = item.selected_submission
    old_form_id = item.form_id
    now = timezone.now()
    reviewer = environment["reviewer"]
    state = {
        "status": status,
        "submitted_at": now,
        "decided_at": now,
        "decided_by": reviewer,
    }
    ReviewItem.objects.filter(pk=item.pk).update(
        **state,
        assignee=reviewer,
        decision_note="Historical review decision.",
    )
    ApplicationInstance.objects.filter(pk=item.application_id).update(**state)
    item.application.milestone.enabled = enabled
    item.application.milestone.save(update_fields=["enabled"])
    if not dependency_ready:
        ApplicationInstance.objects.filter(
            pk=milestone(environment).application_id,
        ).update(status="draft")
    ReviewQuery.objects.create(
        item=item,
        submission=old_submission,
        field_key="wasa_certificate",
        question="Clarify the original certificate scope.",
        raised_by=reviewer,
    )
    ProductOutcome.objects.create(
        product=item.product,
        source_application=item.application,
        outcome_type="milestone_approval",
        name="Historical UHI approval",
        data={"submission_id": old_submission.pk},
    )
    item.refresh_from_db()
    old_reference = item.application.reference
    old_use = item.application.form_uses.get()
    dependency_ids = list(item.application.dependencies.values_list("pk", flat=True))
    history = {
        "forms": rows(FormRecord, pk=old_form_id),
        "submissions": rows(FormSubmission, form_id=old_form_id),
        "attachments": rows(FormAttachment, submission__form_id=old_form_id),
        "queries": rows(ReviewQuery, item=item),
        "outcomes": rows(ProductOutcome, product=item.product),
        "m1": rows(ReviewItem, pk=milestone(environment).pk),
    }
    notification_ids = list(Notification.objects.values_list("pk", flat=True))

    run_upgrade()

    item.refresh_from_db()
    old_use.refresh_from_db()
    assert item.application_id == old_use.application_id
    assert item.application.reference == old_reference
    assert item.application.application_type == UHI_APPLICATION_KEY
    assert item.application.milestone.enabled is enabled
    assert list(item.application.dependencies.values_list("pk", flat=True)) == (
        dependency_ids
    )
    assert item.form_id != old_form_id
    assert item.form.form_key == UHI_FORM_KEY
    assert item.form.reuse_scope == "product"
    assert item.form.product_id == item.product_id
    assert old_use.form_id == item.form_id
    assert old_use.form_key == UHI_FORM_KEY
    assert old_use.selected_submission_id == item.selected_submission_id
    assert item.selected_submission.origin_application_id == item.application_id
    assert item.selected_submission.status == "needs_changes"
    assert item.selected_submission.metadata["complete"] is False
    assert item.selected_submission.metadata["grandfathered"] is (status == "approved")
    assert item.application.metadata["uhi_form_upgrade"]["grandfathered"] is (
        status == "approved"
    )
    assert {field["key"] for field in item.selected_submission.field_schema} == set(
        uhi_data(),
    )
    assert not item.selected_submission.attachments.exists()
    assert {
        "forms": rows(FormRecord, pk=old_form_id),
        "submissions": rows(FormSubmission, form_id=old_form_id),
        "attachments": rows(FormAttachment, submission__form_id=old_form_id),
        "queries": rows(ReviewQuery, item=item),
        "outcomes": rows(ProductOutcome, product=item.product),
        "m1": rows(ReviewItem, pk=milestone(environment).pk),
    } == history
    assert list(Notification.objects.values_list("pk", flat=True)) == notification_ids
    event = item.history.get(action="UHI participation form upgraded")
    assert event.detail["submission_id"] == old_submission.pk
    assert event.detail["previous_state"]["status"] == status
    if status == "approved":
        assert item.status == item.application.status == "approved"
        assert item.decided_at == item.application.decided_at == now
        assert item.submitted_at == item.application.submitted_at == now
        assert item.decided_by_id == item.application.decided_by_id == reviewer.pk
    else:
        assert item.status == "draft"
        assert item.application.status == ("draft" if dependency_ready else "locked")
        assert item.assignee is None
        assert item.decided_by is None
        assert item.decided_at is None
        assert item.submitted_at is None
        assert item.decision_note == ""
        assert item.application.decided_by is None
        assert item.application.decided_at is None
        assert item.application.submitted_at is None


def test_upgrade_recovers_registration_history(environment, legacy_uhi):
    registration = legacy_uhi.product.review_items.get(kind="product_registration")
    source = registration.selected_submission
    source.data = {**source.data, **uhi_data()}
    source.save(update_fields=["data"])
    next_snapshot(
        registration,
        {
            key: value
            for key, value in source.data.items()
            if not key.startswith("uhi_")
        },
    )

    run_upgrade()

    legacy_uhi.refresh_from_db()
    snapshot = legacy_uhi.selected_submission
    assert snapshot.data == uhi_data()
    assert snapshot.metadata["source_submission_id"] == source.pk


def test_upgrade_prefers_latest_coherent_application_answers(environment, legacy_uhi):
    registration = legacy_uhi.product.review_items.get(kind="product_registration")
    registration.selected_submission.data = {
        **registration.selected_submission.data,
        **uhi_data(),
    }
    registration.selected_submission.save(update_fields=["data"])
    next_snapshot(legacy_uhi, {**evidence_data(), **uhi_data()})
    answers = {
        "uhi_role": ["hspa"],
        "uhi_services": [],
        "uhi_tell_us_about": "",
        "uhi_extra_details": "Latest application answers.",
    }
    source = next_snapshot(legacy_uhi, {**evidence_data(), **answers})
    next_snapshot(legacy_uhi, evidence_data())

    run_upgrade()

    legacy_uhi.refresh_from_db()
    snapshot = legacy_uhi.selected_submission
    assert snapshot.data == answers
    assert snapshot.metadata["source_submission_id"] == source.pk


@pytest.mark.parametrize("source_kind", ["application", "registration"])
def test_upgrade_preserves_deliberately_cleared_answers(legacy_uhi, source_kind):
    registration = legacy_uhi.product.review_items.get(kind="product_registration")
    next_snapshot(registration, uhi_data())
    item = legacy_uhi if source_kind == "application" else registration
    next_snapshot(item, uhi_data())
    cleared = {
        "uhi_role": [],
        "uhi_services": [],
        "uhi_tell_us_about": "",
        "uhi_extra_details": "",
    }
    source = next_snapshot(item, cleared)
    next_snapshot(item, {"description": "UHI questions moved to participation."})

    run_upgrade()

    legacy_uhi.refresh_from_db()
    snapshot = legacy_uhi.selected_submission
    assert snapshot.data == cleared
    assert snapshot.metadata["source_submission_id"] == source.pk


@pytest.mark.parametrize("has_snapshot", [True, False])
def test_upgrade_without_answers_starts_empty_and_can_be_submitted(
    environment,
    legacy_uhi,
    has_snapshot,
):
    if not has_snapshot:
        ReviewItem.objects.filter(pk=legacy_uhi.pk).update(selected_submission=None)
        ApplicationFormUse.objects.filter(
            application_id=legacy_uhi.application_id,
        ).update(selected_submission=None)
    run_upgrade()
    legacy_uhi.refresh_from_db()
    assert not any(legacy_uhi.selected_submission.data.values())
    assert set(legacy_uhi.selected_submission.data) <= set(uhi_data())
    assert legacy_uhi.selected_submission.metadata.get("source_submission_id") is None

    item, form, saved = workflows.save_review_form(
        legacy_uhi,
        environment["applicant"],
        data=uhi_data(),
        submit=True,
    )

    assert saved, form.errors
    assert item.status == item.application.status == "approved"
    assert item.decided_by is None
    assert item.selected_submission.data == uhi_data()
    assert not item.selected_submission.attachments.exists()


def test_upgrade_is_idempotent(legacy_uhi):
    run_upgrade()
    model_types = (
        ApplicationInstance,
        ApplicationFormUse,
        FormRecord,
        FormSubmission,
        ReviewItem,
        AuditEvent,
        Notification,
    )
    before = {model: rows(model) for model in model_types}

    run_upgrade()

    assert {model: rows(model) for model in model_types} == before


def test_modern_uhi_application_is_unchanged(environment):
    approve(environment)
    item, form, saved = workflows.save_review_form(
        milestone(environment, "uhi1"),
        environment["applicant"],
        data=uhi_data(),
        submit=True,
    )
    assert saved, form.errors
    model_types = (
        ApplicationInstance,
        ApplicationFormUse,
        FormRecord,
        FormSubmission,
        ReviewItem,
        AuditEvent,
        Notification,
    )
    before = {model: rows(model) for model in model_types}

    run_upgrade()

    assert {model: rows(model) for model in model_types} == before
    item.refresh_from_db()
    assert item.selected_submission.data == uhi_data()
