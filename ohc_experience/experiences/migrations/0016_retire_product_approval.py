"""Retire product approval and milestone locks.

A product registration is now recorded when it is submitted, and milestones are
never locked: prerequisites are checked when a reviewer decides instead.

Registrations still awaiting a decision, sent back or withdrawn become recorded,
each with an audit entry. The product already shows what was last submitted,
except after a withdrawn change, which put the approved details back; that
registration is pinned to the approved revision again. Locked applications
become drafts. Reversing only marks every product registered again, which the
earlier code needs before it accepts milestone submissions.
"""

from django.db import migrations
from django.utils import timezone


def _pin(item, submission_id, form_uses, submissions):
    current = submissions.objects.filter(form_id=item.form_id, is_current=True)
    current.update(is_current=False)
    submissions.objects.filter(pk=submission_id).update(is_current=True)
    form_uses.objects.filter(
        application_id=item.application_id,
        form_id=item.form_id,
    ).update(selected_submission_id=submission_id)
    item.selected_submission_id = submission_id


def retire_product_approval(apps, schema_editor):
    applications = apps.get_model("experiences", "ApplicationInstance")
    audit_events = apps.get_model("experiences", "AuditEvent")
    form_uses = apps.get_model("experiences", "ApplicationFormUse")
    submissions = apps.get_model("experiences", "FormSubmission")
    workspaces = apps.get_model("experiences", "ProductWorkspace")
    reviews = apps.get_model("experiences", "ReviewItem")
    now = timezone.now()

    applications.objects.filter(status="locked").update(status="draft", updated_at=now)

    pending = reviews.objects.filter(kind="product_registration").exclude(
        status="approved",
    )
    for item in pending.select_related("application").iterator():
        previous = item.status
        approval = (
            audit_events.objects.filter(item_id=item.pk, action="Approved")
            .order_by("-created_at", "-pk")
            .first()
        )
        detail = {"previous_status": previous}
        recorded_at = item.submitted_at or now
        if previous == "draft" and approval and approval.detail.get("submission_id"):
            _pin(item, approval.detail["submission_id"], form_uses, submissions)
            detail["submission_id"] = item.selected_submission_id
            recorded_at = approval.created_at
        if not item.selected_submission_id:
            continue
        item.status = "approved"
        item.decided_at = recorded_at
        item.decided_by_id = None
        item.decision_note = ""
        item.save(
            update_fields=[
                "status",
                "decided_at",
                "decided_by",
                "decision_note",
                "selected_submission",
            ],
        )
        application = item.application
        application.status = "approved"
        application.submitted_at = item.submitted_at
        application.decided_at = recorded_at
        application.decided_by_id = None
        application.save(
            update_fields=[
                "status",
                "submitted_at",
                "decided_at",
                "decided_by",
                "updated_at",
            ],
        )
        audit_events.objects.create(
            organisation_id=item.organisation_id,
            product_id=item.product_id,
            item_id=item.pk,
            actor=None,
            action="Product approval retired",
            detail=detail,
        )

    for workspace in workspaces.objects.filter(
        registered_at__isnull=True,
    ).select_related("product"):
        workspace.registered_at = workspace.product.created_at
        workspace.save(update_fields=["registered_at"])


def mark_every_product_registered(apps, schema_editor):
    workspaces = apps.get_model("experiences", "ProductWorkspace")
    workspaces.objects.update(registration_status="registered")


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0015_product_production_client_id"),
    ]

    operations = [
        migrations.RunPython(retire_product_approval, mark_every_product_registered),
    ]
