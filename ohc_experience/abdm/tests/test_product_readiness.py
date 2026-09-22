from types import SimpleNamespace

import pytest
from django import forms
from django.template.loader import render_to_string

from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.experiences.forms import ReviewForm
from ohc_experience.experiences.templatetags.experience_product_ui import (
    evidence_readiness,
)


@pytest.mark.django_db
def test_readiness_uses_required_schema_fields_and_saved_attachments():
    form = ExitEvidenceForm(
        initial={"wasa_agency": "Saved audit agency"},
        existing_files={
            "functional_certificate": [object()],
            "functional_report": [object()],
        },
        draft=True,
    )
    readiness = evidence_readiness(form)
    assert readiness["total"] == 3  # noqa: PLR2004
    assert readiness["completed"] == 0
    assert readiness["missing"] == 3  # noqa: PLR2004
    assert [row["label"] for row in readiness["rows"]] == [
        "Sandbox testing",
        "WASA audit",
        "Functional testing",
    ]
    assert readiness["rows"][1]["field_ids"] == [
        "id_wasa_certificate",
        "id_wasa_agency",
        "id_wasa_date",
        "id_wasa_valid_until",
    ]
    assert readiness["rows"][2]["field_ids"] == [
        "id_functional_certificate",
        "id_functional_report",
        "id_undertaking_form",
    ]


@pytest.mark.django_db
def test_unsaved_fields_do_not_count_towards_saved_readiness():
    form = ExitEvidenceForm(
        data={"wasa_agency": "Unsaved edit"},
        initial={},
        draft=True,
    )
    assert evidence_readiness(form)["completed"] == 0


def test_required_checkbox_and_valid_zero_have_distinct_readiness():
    class ExampleForm(forms.Form):
        count = forms.IntegerField()
        consent = forms.BooleanField()

    form = ExampleForm(initial={"count": 0, "consent": False})
    readiness = evidence_readiness(form)
    assert readiness["rows"][0]["done"] is True
    assert readiness["rows"][1]["done"] is False


def test_non_abdm_schema_counts_required_upload_and_consent_in_draft():
    class EvidenceForm(ReviewForm):
        required_uploads = ("attestation",)
        consent = forms.BooleanField(label="Permission to proceed")
        attestation = forms.FileField(required=False, label="Signed attestation")
        notes = forms.CharField(required=False)

    form = EvidenceForm(
        initial={"consent": False, "notes": "Optional saved detail"},
        existing_files={"attestation": [SimpleNamespace(pk=17)]},
        draft=True,
    )
    readiness = evidence_readiness(form)
    assert readiness["total"] == 2  # noqa: PLR2004
    assert readiness["completed"] == 1
    assert readiness["rows"] == [
        {
            "label": "Permission to proceed",
            "field_id": "id_consent",
            "field_ids": ["id_consent"],
            "done": False,
        },
        {
            "label": "Signed attestation",
            "field_id": "id_attestation",
            "field_ids": ["id_attestation"],
            "done": True,
        },
    ]


def test_saved_file_readiness_changes_only_when_removal_is_saved():
    class EvidenceForm(ReviewForm):
        required_uploads = ("proof",)
        proof = forms.FileField(required=False)

    removal_not_saved = EvidenceForm(
        data={"remove_files__proof": ["17"]},
        existing_files={"proof": [SimpleNamespace(pk=17)]},
        draft=True,
    )
    assert removal_not_saved.retained_existing_files("proof") == []
    assert evidence_readiness(removal_not_saved)["completed"] == 1

    removal_saved = EvidenceForm(existing_files={}, draft=True)
    assert evidence_readiness(removal_saved)["completed"] == 0


def test_readiness_jump_uses_first_missing_prefixed_field():
    class EvidenceForm(forms.Form):
        name = forms.CharField()
        consent = forms.BooleanField()

    form = EvidenceForm(initial={"name": "Saved", "consent": False}, prefix="evidence")
    assert (
        evidence_readiness(form)["first_missing"]["field_id"] == "id_evidence-consent"
    )
    html = render_to_string(
        "experiences/partials/product_evidence_readiness.html",
        {"form": form},
    )
    assert 'href="#id_evidence-consent"' in html
    assert 'data-readiness-form="evidence-form"' in html
    assert 'data-readiness-item="id_evidence-consent"' in html
    assert "section need attention" in html


def test_complete_readiness_links_to_actions_without_claiming_approval():
    class EvidenceForm(forms.Form):
        consent = forms.BooleanField()

    form = EvidenceForm(initial={"consent": True})
    assert evidence_readiness(form)["first_missing"] is None
    html = render_to_string(
        "experiences/partials/product_evidence_readiness.html",
        {"form": form, "submission_blocked": "Organisation approval is pending."},
    )
    assert 'href="#evidence-actions"' in html
    assert "Go to form actions" in html
    # The blocker belongs beside the form's actions; repeating it in the
    # sidebar put the same sentence on screen twice.
    assert "Organisation approval is pending." not in html


def test_milestone_picker_exposes_focusable_error_summary_target():
    class ProductForm(forms.Form):
        applied_milestones = forms.MultipleChoiceField(choices=[("one", "One")])

    form = ProductForm(data={})
    html = render_to_string(
        "experiences/partials/product_milestone_picker.html",
        {"form": form, "field": form["applied_milestones"]},
    )
    assert 'id="id_applied_milestones"' in html
    assert 'tabindex="-1"' in html
    assert 'aria-invalid="true"' in html
    assert 'id="id_applied_milestones-error-1"' in html
    assert (
        'aria-describedby="milestone-picker-hint id_applied_milestones-error-1"' in html
    )


def test_revealed_secret_keeps_authenticated_mask_and_reveal_template():
    html = render_to_string(
        "experiences/partials/secret.html",
        {
            "credential": SimpleNamespace(status="active"),
            "workspace": SimpleNamespace(reference="SBX-2026-00001"),
            "revealed_secret": "temporary-test-value",
            "csrf_token": "test-token",
        },
    )
    assert "data-revealed-secret" in html
    assert "data-secret-masked" in html
    assert 'action="/products/SBX-2026-00001/credentials/"' in html
    assert 'name="intent" value="reveal"' in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert "Reveal secret" in html


def test_inactive_credentials_do_not_offer_reveal_in_mask_template():
    html = render_to_string(
        "experiences/partials/secret.html",
        {"credential": SimpleNamespace(status="revoked")},
    )
    assert "data-secret-masked" not in html
    assert "Reveal secret" not in html
