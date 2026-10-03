import pytest

from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import FormRecord
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.legacy_import import clean
from ohc_experience.legacy_import import writer
from ohc_experience.legacy_import.checks import _wasa_outcomes_without_a_certificate
from ohc_experience.legacy_import.checks import target_drift


def test_the_import_tables_still_match_the_portal():
    assert target_drift() == []


def test_drift_names_every_table_the_portal_has_moved_past(monkeypatch):
    monkeypatch.setattr(clean, "SOLUTION_ORDER", [*clean.SOLUTION_ORDER, "payers"])
    monkeypatch.setattr(clean, "ENTITY_TYPES", {**clean.ENTITY_TYPES, "ngo": "charity"})
    monkeypatch.setattr(
        clean,
        "MILESTONE_ORDER",
        [key for key in clean.MILESTONE_ORDER if key != "m2"],
    )

    assert target_drift() == [
        "solution type 'payers' is no longer offered",
        "entity type 'charity' is no longer offered",
        "milestone 'm2' is missing from the import's order",
    ]


def outcome_on(product, user, submission, title, note=""):
    """A WASA approval, under an application of its own as the portal keeps it."""
    application = ApplicationInstance.objects.create(
        reference=f"APP-2026-{title}",
        application_type="milestone",
        title=title,
        product=product,
        created_by=user,
        status="approved",
    )
    return ProductOutcome.objects.create(
        product=product,
        outcome_type="wasa_approval",
        name="WASA certification approved",
        source_application=application,
        data={"submission_id": submission.pk},
        metadata={"legacy_note": note} if note else {},
    )


def submission_on(record, user, number, **fields):
    return FormSubmission.objects.create(
        form=record,
        form_key=record.form_key,
        submitted_by=user,
        submission_number=number,
        is_current=False,
        **fields,
    )


@pytest.mark.django_db
def test_a_wasa_approval_passes_on_the_certificate_its_submission_names(
    organisation,
    user,
):
    """A certificate carried forward belongs to the submission that uploaded it,
    under another application than the approval that reads it."""
    product = Product.objects.create(
        organisation=organisation,
        name="Clinic",
        slug="clinic",
        description="A clinic app",
        experience_type="abdm",
        created_by=user,
    )
    record = FormRecord.objects.create(
        reference="FR-1",
        form_key="m2",
        name="M2",
        organisation=organisation,
        product=product,
        created_by=user,
    )
    uploaded = submission_on(record, user, 1)
    attachment = FormAttachment.objects.create(
        submission=uploaded,
        field_key="wasa_certificate",
        file="experience-attachments/legacy/wasa.pdf",
        original_name="wasa.pdf",
        uploaded_by=user,
    )
    carried = submission_on(
        record,
        user,
        2,
        data={"wasa_certificate": {"attachment_id": attachment.pk}},
    )
    bare = submission_on(record, user, 3)
    outcome_on(product, user, carried, "M3")
    outcome_on(product, user, bare, "M4", note=writer.WASA_NONE)
    missing = outcome_on(product, user, bare, "M2")

    assert _wasa_outcomes_without_a_certificate() == [missing.pk]
