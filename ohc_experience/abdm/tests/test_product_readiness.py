from types import SimpleNamespace

from django import forms

from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.experiences.forms import ReviewForm
from ohc_experience.experiences.templatetags.experience_product_ui import (
    evidence_readiness,
)


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
    assert readiness["total"] == 7  # noqa: PLR2004
    assert readiness["completed"] == 3  # noqa: PLR2004
    assert readiness["missing"] == 4  # noqa: PLR2004
    assert all(row["field_id"] != "id_supporting_evidence" for row in readiness["rows"])
    assert any(row["field_id"] == "id_wasa_date" for row in readiness["rows"])


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
            "done": False,
        },
        {
            "label": "Signed attestation",
            "field_id": "id_attestation",
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
