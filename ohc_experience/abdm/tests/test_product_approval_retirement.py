"""Registrations left mid-review become records, and locked milestones open."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests import test_workflow as workflow_fixtures
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import ApplicationFormUse
from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import ProductWorkspace
from ohc_experience.experiences.models import ReviewItem

pytestmark = pytest.mark.django_db
environment = workflow_fixtures.environment

BEFORE = [("experiences", "0015_product_production_client_id")]
AFTER = [("experiences", "0017_remove_productworkspace_registration_status")]


def registration(workspace):
    return workspace.product.review_items.get(kind="product_registration")


def another_product(environment, name):
    workspace, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data(name),
    )
    assert workspace, form.errors
    return workspace


def set_legacy_state(item, status, application_status, reviewer=None):
    ReviewItem.objects.filter(pk=item.pk).update(
        status=status,
        decided_at=timezone.now() if reviewer else None,
        decided_by=reviewer,
        decision_note="Describe the product's intended use." if reviewer else "",
    )
    ApplicationInstance.objects.filter(pk=item.application_id).update(
        status=application_status,
        decided_at=None,
        decided_by=None,
    )


def migrate(target):
    # The fixture's rows still have deferred foreign key checks queued, and
    # PostgreSQL alters no table while any are pending.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    MigrationExecutor(connection).migrate(target)


def retirement(item):
    return AuditEvent.objects.get(item=item, action="Product approval retired")


def test_open_registrations_are_recorded_and_locked_milestones_open(environment):
    withdrawn = registration(environment["workspace"])
    approved = withdrawn.selected_submission
    AuditEvent.objects.create(
        organisation=withdrawn.organisation,
        product=withdrawn.product,
        item=withdrawn,
        actor=environment["reviewer"],
        action="Approved",
        detail={"submission_id": approved.pk},
    )
    withdrawn, form, saved = workflows.save_review_form(
        withdrawn,
        environment["applicant"],
        data={**product_data(), "description": "A change that was withdrawn."},
        submit=True,
    )
    assert saved, form.errors
    withdrawn_change = withdrawn.selected_submission
    in_review = registration(another_product(environment, "Product under review"))
    sent_back = registration(another_product(environment, "Product sent back"))
    locked = milestone(environment, "m2")

    migrate(BEFORE)
    try:
        set_legacy_state(withdrawn, "draft", "draft")
        set_legacy_state(in_review, "in_review", "under_review")
        set_legacy_state(sent_back, "sent_back", "draft", environment["reviewer"])
        ApplicationInstance.objects.filter(pk=locked.application_id).update(
            status="locked",
        )
        ProductWorkspace.objects.filter(product=in_review.product).update(
            registered_at=None,
        )

        migrate(AFTER)
    finally:
        migrate(AFTER)

    for item in (withdrawn, in_review, sent_back):
        item.refresh_from_db()
        assert item.status == ReviewItem.Status.APPROVED
        assert item.decided_by is None
        assert item.decision_note == ""
        assert ApplicationInstance.objects.get(pk=item.application_id).status == (
            "approved"
        )
    assert retirement(in_review).detail == {"previous_status": "in_review"}
    assert retirement(sent_back).detail == {"previous_status": "sent_back"}
    # The withdrawn change put the approved details back on the product.
    assert retirement(withdrawn).detail == {
        "previous_status": "draft",
        "submission_id": approved.pk,
    }
    assert withdrawn.selected_submission_id == approved.pk
    assert (
        ApplicationFormUse.objects.get(
            application=withdrawn.application,
        ).selected_submission_id
        == approved.pk
    )
    assert FormSubmission.objects.get(pk=approved.pk).is_current
    assert not FormSubmission.objects.get(pk=withdrawn_change.pk).is_current
    assert (
        ProductWorkspace.objects.get(product=in_review.product).registered_at
        == in_review.product.created_at
    )
    assert ApplicationInstance.objects.get(pk=locked.application_id).status == "draft"
