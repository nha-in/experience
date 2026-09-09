from html import escape

import pytest
from django import forms
from django.template.loader import render_to_string

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import CertificationAgency

pytestmark = pytest.mark.django_db


def test_agency_is_a_dropdown_with_no_default_selection():
    form = ExitEvidenceForm()
    field = form.fields["wasa_agency"]
    assert isinstance(field, forms.ChoiceField)
    assert isinstance(field.widget, forms.Select)
    assert field.choices[0] == ("", "Select an audit agency")
    assert CertificationAgency.objects.filter(program="abdm").count() == 255  # noqa: PLR2004
    html = render_to_string("experiences/partials/form.html", {"form": form})
    assert '<select name="wasa_agency"' in html
    assert '<input type="text" name="wasa_agency"' not in html
    assert '<option value="" selected>Select an audit agency</option>' in html
    assert 'value="M/s A3S Tech &amp; Company"' in html
    assert "M/s TÜV-SÜD South Asia Pvt. Ltd." in html


@pytest.mark.parametrize("draft", [True, False])
def test_unknown_posted_agency_cannot_extend_the_dropdown(draft):
    form = ExitEvidenceForm(data={"wasa_agency": "Unlisted new agency"}, draft=draft)
    assert not form.is_valid()
    assert form.errors.as_data()["wasa_agency"][0].code == "invalid_choice"
    assert not form.fields["wasa_agency"].valid_value("Unlisted new agency")


def test_empty_agency_is_allowed_only_in_draft():
    assert ExitEvidenceForm(data={}, draft=True).is_valid()
    submission = ExitEvidenceForm(data=evidence_data() | {"wasa_agency": ""})
    assert not submission.is_valid()
    assert submission.errors.as_data()["wasa_agency"][0].code == "required"


@pytest.mark.parametrize(
    "agency",
    [
        "M/s A3S Tech & Company",
        "M/s TÜV-SÜD South Asia Pvt. Ltd.",
        "M/s BSCIC Certifications Private Limited ",
        "M/s CMS IT Services Pvt. Ltd. ",
        "M/s TAC InfoSec Limited ",
    ],
)
def test_legacy_option_values_are_saved_exactly(agency):
    form = ExitEvidenceForm(
        data=evidence_data() | {"wasa_agency": agency},
        files=files(),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["wasa_agency"] == agency


def test_valid_agency_stays_selected_after_another_field_fails():
    agency = "M/s A3S Tech & Company"
    form = ExitEvidenceForm(data={"wasa_agency": agency})
    assert not form.is_valid()
    assert "wasa_agency" not in form.errors
    assert f'<option value="{escape(agency)}" selected>' in str(form["wasa_agency"])


def test_previously_saved_free_text_is_retained_only_for_its_own_form():
    previous = "SecureStack Audit Services (demo)"
    form = ExitEvidenceForm(
        data=evidence_data() | {"wasa_agency": previous},
        initial={"wasa_agency": previous},
        files=files(),
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["wasa_agency"] == previous
    assert (previous, f"{previous} (previously saved)") in form.fields[
        "wasa_agency"
    ].choices
    assert "previously saved" in str(form["wasa_agency"])
    assert not ExitEvidenceForm().fields["wasa_agency"].valid_value(previous)
    assert not ExitEvidenceForm.base_fields["wasa_agency"].valid_value(previous)


def test_old_saved_agency_does_not_allow_new_free_text():
    form = ExitEvidenceForm(
        data={"wasa_agency": "Different unlisted agency"},
        initial={"wasa_agency": "Previously saved agency"},
        draft=True,
    )
    assert not form.is_valid()
    assert form.errors.as_data()["wasa_agency"][0].code == "invalid_choice"


def test_selected_agency_is_persisted_and_restored(environment):  # noqa: F811
    agency = "M/s A3S Tech & Company"
    item, form, saved = workflows.save_review_form(
        milestone(environment),
        environment["applicant"],
        data=evidence_data() | {"wasa_agency": agency},
        files=files(),
        submit=True,
    )
    assert saved, form.errors
    assert item.selected_submission.data["wasa_agency"] == agency
    schema = next(
        field
        for field in item.selected_submission.field_schema
        if field["key"] == "wasa_agency"
    )
    assert {"value": agency, "label": agency} in schema["choices"]
    restored = workflows.build_form(item)
    assert restored["wasa_agency"].value() == agency
    assert f'<option value="{escape(agency)}" selected>' in str(restored["wasa_agency"])


def test_admin_added_agencies_appear_without_code_changes():
    added = CertificationAgency.objects.create(
        program="abdm",
        name="New security audit agency",
        sort_order=1,
    )
    form = ExitEvidenceForm(data={"wasa_agency": added.name}, draft=True)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["wasa_agency"] == added.name


def test_inactive_and_other_program_agencies_cannot_be_new_selections():
    inactive = CertificationAgency.objects.create(
        program="abdm",
        name="Inactive audit agency",
        is_active=False,
    )
    other_program = CertificationAgency.objects.create(
        program="another_program",
        name="Another program's agency",
    )
    for agency in (inactive, other_program):
        form = ExitEvidenceForm(data={"wasa_agency": agency.name}, draft=True)
        assert not form.is_valid()
        assert form.errors.as_data()["wasa_agency"][0].code == "invalid_choice"


def test_deactivation_preserves_a_previously_saved_answer():
    agency = CertificationAgency.objects.get(program="abdm", name="Army Cyber Group")
    previous = agency.name
    agency.is_active = False
    agency.save()
    form = ExitEvidenceForm(
        data={"wasa_agency": previous},
        initial={"wasa_agency": previous},
        draft=True,
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["wasa_agency"] == previous
    assert not ExitEvidenceForm().fields["wasa_agency"].valid_value(previous)


def test_renaming_agency_updates_choices_without_rewriting_saved_answers():
    agency = CertificationAgency.objects.get(program="abdm", name="Army Cyber Group")
    previous = agency.name
    agency.name = "Renamed audit agency"
    agency.save()
    current = ExitEvidenceForm()
    assert current.fields["wasa_agency"].valid_value(agency.name)
    assert not current.fields["wasa_agency"].valid_value(previous)
    saved = ExitEvidenceForm(
        data={"wasa_agency": previous},
        initial={"wasa_agency": previous},
        draft=True,
    )
    assert saved.is_valid(), saved.errors
    assert saved.cleaned_data["wasa_agency"] == previous


def test_admin_order_is_used_for_dropdown():
    first = CertificationAgency.objects.create(
        program="abdm",
        name="AAA priority agency",
        sort_order=0,
    )
    last = CertificationAgency.objects.create(
        program="abdm",
        name="Last agency",
        sort_order=1000,
    )
    options = ExitEvidenceForm().fields["wasa_agency"].choices
    assert options[1][0] == first.name
    assert options[-1][0] == last.name


def test_empty_agency_table_has_no_hardcoded_fallback():
    CertificationAgency.objects.filter(program="abdm").update(is_active=False)
    form = ExitEvidenceForm(data={"wasa_agency": "Army Cyber Group"}, draft=True)
    assert list(form.fields["wasa_agency"].choices) == [("", "Select an audit agency")]
    assert not form.is_valid()
