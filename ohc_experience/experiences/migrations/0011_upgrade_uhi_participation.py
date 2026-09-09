"""Move existing UHI milestones off the shared exit-evidence form.

Keep application identities, approvals and all historical evidence intact. Only
the active form changes; unapproved requests return to a draft for the integrator
to confirm the participation answers. This intentionally has no reverse operation:
rolling back must not discard answers subsequently recorded on the new form.
"""

from uuid import uuid4

from django.db import migrations
from django.db.models import Max

UHI_FORM_KEY = "sandbox_uhi_participation"
UHI_FIELDS = (
    "uhi_role",
    "uhi_services",
    "uhi_tell_us_about",
    "uhi_extra_details",
)
# Freeze the new snapshot schema here, independently of future form definitions.
UHI_SCHEMA = [
    {
        "key": "uhi_role",
        "label": "Role",
        "type": "MultipleChoiceField",
        "required": True,
        "choices": [
            {"value": "eua", "label": "End User Applications (EUA)"},
            {"value": "hspa", "label": "Health Service Provider Application (HSPA)"},
        ],
    },
    {
        "key": "uhi_services",
        "label": "Services",
        "type": "MultipleChoiceField",
        "required": True,
        "choices": [
            {"value": "blood_bank_discovery", "label": "Blood Bank Discovery"},
            {"value": "physical_consultation", "label": "Physical Consultation"},
            {"value": "teleconsultation", "label": "Teleconsultation"},
            {"value": "pmjay_hem_find_hospital", "label": "PMJAY HEM Find Hospital"},
        ],
    },
    {
        "key": "uhi_tell_us_about",
        "label": "Tell us about your UHI integration",
        "type": "CharField",
        "required": False,
        "choices": [],
    },
    {
        "key": "uhi_extra_details",
        "label": "Any extra details you would like to share",
        "type": "CharField",
        "required": False,
        "choices": [],
    },
]


def _answer_source(submissions, application_id, product_id):
    candidates = (
        submissions.filter(origin_application_id=application_id),
        submissions.filter(
            form__product_id=product_id,
            form_key="sandbox_product_registration",
        ),
    )
    for queryset in candidates:
        for snapshot in queryset.order_by("-submitted_at", "-pk").iterator():
            # Present-but-empty answers are a deliberate saved draft. Only scan
            # past snapshots whose schema no longer contained any UHI fields.
            if any(key in snapshot.data for key in UHI_FIELDS):
                return snapshot
    return None


def _previous_state(item, application):
    state = {
        "application_type": application.application_type,
        "application_status": application.status,
        "form_id": item.form_id,
        "selected_submission_id": item.selected_submission_id,
        "status": item.status,
        "assignee_id": item.assignee_id,
        "decided_by_id": item.decided_by_id,
        "decision_note": item.decision_note,
        "resubmission_count": item.resubmission_count,
    }
    for key in ("submitted_at", "decided_at"):
        value = getattr(item, key)
        state[key] = value.isoformat() if value else None
        value = getattr(application, key)
        state[f"application_{key}"] = value.isoformat() if value else None
    state["application_decided_by_id"] = application.decided_by_id
    return state


def upgrade_uhi_participation(apps, schema_editor):
    alias = schema_editor.connection.alias
    milestones = apps.get_model("experiences", "Milestone").objects.using(alias)
    applications = apps.get_model("experiences", "ApplicationInstance").objects.using(
        alias,
    )
    records = apps.get_model("experiences", "FormRecord").objects.using(alias)
    submissions = apps.get_model("experiences", "FormSubmission").objects.using(alias)
    uses = apps.get_model("experiences", "ApplicationFormUse").objects.using(alias)
    reviews = apps.get_model("experiences", "ReviewItem").objects.using(alias)
    events = apps.get_model("experiences", "AuditEvent").objects.using(alias)
    dependencies = apps.get_model("experiences", "ApplicationDependency").objects.using(
        alias,
    )
    legacy = milestones.filter(
        key="uhi1",
        product__workspace__experience_type="abdm",
        application__application_type="abdm_sandbox_exit",
        application__review_item__form__form_key="sandbox_exit_evidence",
    ).select_related("application__review_item", "product")
    for milestone in legacy.iterator():
        application = milestone.application
        item = application.review_item
        previous = _previous_state(item, application)
        approved = item.status == "approved"
        source = _answer_source(submissions, application.pk, milestone.product_id)
        data = (
            {key: source.data[key] for key in UHI_FIELDS if key in source.data}
            if source
            else {}
        )
        record, _ = records.get_or_create(
            product_id=milestone.product_id,
            form_key=UHI_FORM_KEY,
            reuse_scope="product",
            defaults={
                "reference": f"FORM-{uuid4().hex[:24].upper()}",
                "name": "UHI participation",
                "organisation_id": item.organisation_id,
                "created_by_id": application.created_by_id,
                "metadata": {"schema_version": 1},
            },
        )
        number = (
            submissions.filter(form_id=record.pk).aggregate(
                number=Max("submission_number"),
            )["number"]
            or 0
        ) + 1
        submissions.filter(form_id=record.pk, is_current=True).update(is_current=False)
        snapshot = submissions.create(
            form_id=record.pk,
            form_key=UHI_FORM_KEY,
            origin_application_id=application.pk,
            data=data,
            field_schema=UHI_SCHEMA,
            metadata={
                "complete": False,
                "migration": "0011_upgrade_uhi_participation",
                "source_submission_id": source.pk if source else None,
                "grandfathered": approved,
            },
            status="needs_changes",
            submission_number=number,
            revision=1,
            submitted_by_id=source.submitted_by_id
            if source
            else application.created_by_id,
        )
        # Never change the old product-shared form or its immutable snapshots.
        uses.filter(application_id=application.pk, form_id=item.form_id).update(
            form_id=record.pk,
            form_key=UHI_FORM_KEY,
            selected_submission_id=snapshot.pk,
        )
        review_changes = {"form_id": record.pk, "selected_submission_id": snapshot.pk}
        application_changes = {
            "application_type": "abdm_uhi_participation",
            "metadata": {
                **application.metadata,
                "uhi_form_upgrade": {
                    "previous_form_id": item.form_id,
                    "previous_submission_id": item.selected_submission_id,
                    "grandfathered": approved,
                },
            },
        }
        if not approved:
            review_changes.update(
                status="draft",
                assignee_id=None,
                submitted_at=None,
                decided_at=None,
                decided_by_id=None,
                decision_note="",
                resubmission_count=0,
            )
            locked = (
                dependencies.filter(application_id=application.pk)
                .exclude(depends_on__status="approved")
                .exists()
            )
            application_changes.update(
                status="locked" if locked else "draft",
                submitted_at=None,
                decided_at=None,
                decided_by_id=None,
            )
        reviews.filter(pk=item.pk).update(**review_changes)
        applications.filter(pk=application.pk).update(**application_changes)
        events.create(
            organisation_id=item.organisation_id,
            product_id=milestone.product_id,
            item_id=item.pk,
            action="UHI participation form upgraded",
            detail={
                "submission_id": item.selected_submission_id,
                "previous_state": previous,
                "source_submission_id": source.pk if source else None,
                "new_submission_id": snapshot.pk,
                "note": (
                    "UHI participation now has its own questions. Previous evidence "
                    "and review history remain available here. "
                    + (
                        "The previously approved milestone remains complete."
                        if approved
                        else "Confirm your participation answers and submit "
                        "the UHI application."
                    )
                ),
            },
        )


class Migration(migrations.Migration):
    dependencies = [("experiences", "0010_notification_delivery")]

    operations = [
        migrations.RunPython(upgrade_uhi_participation, migrations.RunPython.noop),
    ]
