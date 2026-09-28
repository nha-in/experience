from django import forms
from django.template import Context
from django.template import Template
from django.template.loader import render_to_string


class ContactForm(forms.Form):
    email = forms.EmailField(
        help_text="Use your work email.",
        widget=forms.EmailInput(attrs={"aria-describedby": "email-policy"}),
    )


class EvidenceForm(forms.Form):
    """An upload the form checks itself, and a field another answer decides."""

    required_uploads = ("certificate",)
    conditional_requirements = ("website",)

    name = forms.CharField()
    nickname = forms.CharField(required=False)
    website = forms.URLField()
    certificate = forms.FileField(required=False)
    solutions = forms.MultipleChoiceField(
        choices=[("hmis", "HMIS"), ("lmis", "LMIS")],
        widget=forms.CheckboxSelectMultiple,
    )
    agreed = forms.BooleanField()


class DraftEvidenceForm(EvidenceForm):
    """Relaxes every field to save a draft, the way ReviewForm does."""

    draft = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False


class ProfileForm(forms.Form):
    email = forms.EmailField(disabled=True)


class SignInForm(forms.Form):
    mark_required = False

    login = forms.CharField()
    remember = forms.BooleanField(required=False)


class ScheduleForm(forms.Form):
    starts_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    audited_on = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    issued_on = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "max": "2026-09-22"}),
    )


def render_field(field):
    return Template("{% load careui %}{% ui_field field %}").render(
        Context({"field": field}),
    )


def marker_hidden(html, marker):
    """Whether the label's `marker` span is rendered hidden."""
    return "hidden" in html.split(marker, 1)[1].split(">", 1)[0]


def test_invalid_field_describes_both_help_and_validation_errors():
    form = ContactForm({"contact-email": "invalid"}, prefix="contact")
    html = Template("{% load careui %}{% ui_field form.email %}").render(
        Context({"form": form}),
    )
    assert 'aria-invalid="true"' in html
    assert (
        'aria-describedby="email-policy id_contact-email_helptext '
        'id_contact-email_errors"' in html
    )
    assert 'id="id_contact-email_errors"' in html
    assert "Enter a valid email address." in html


def test_errors_left_to_the_summary_are_not_repeated_under_the_field():
    form = ContactForm({"contact-email": "invalid"}, prefix="contact")
    html = Template(
        "{% load careui %}{% ui_field form.email inline_errors=False %}",
    ).render(Context({"form": form}))
    assert 'aria-invalid="true"' in html
    assert (
        'aria-describedby="email-policy id_contact-email_helptext '
        'id_contact-email_error_summary"' in html
    )
    assert "id_contact-email_errors" not in html
    assert "Enter a valid email address." not in html


def test_error_summary_links_to_prefixed_field_and_includes_general_error():
    form = ContactForm({"contact-email": "invalid"}, prefix="contact")
    form.add_error(None, "Review your contact details.")
    html = render_to_string("components/form_errors.html", {"form": form})
    assert "data-error-summary" in html
    assert 'tabindex="-1"' in html
    assert 'href="#id_contact-email"' in html
    assert 'id="id_contact-email_error_summary"' in html
    assert 'data-error-field="contact-email"' in html
    assert "Review your contact details." in html


def test_valid_form_does_not_render_error_summary():
    html = render_to_string(
        "components/form_errors.html",
        {"form": ContactForm({"email": "integrator@example.com"})},
    )
    assert "data-error-summary" not in html


def test_date_fields_take_a_four_digit_year():
    # With no max, Chrome's yyyy segment takes up to six digits (year 275760).
    form = ScheduleForm()
    assert 'max="9999-12-31T23:59"' in render_field(form["starts_at"])
    assert 'max="9999-12-31"' in render_field(form["audited_on"])
    issued_on = render_field(form["issued_on"])
    assert 'max="2026-09-22"' in issued_on
    assert "9999" not in issued_on


def test_a_required_field_is_marked_with_an_asterisk():
    html = render_field(EvidenceForm()["name"])
    assert 'class="ui-required"' in html
    assert "(optional)" not in html
    assert " required" in html.split("<input", 1)[1]


def test_an_optional_field_says_so_and_takes_no_asterisk():
    html = render_field(EvidenceForm()["nickname"])
    assert "(optional)" in html
    assert "ui-required" not in html


def test_an_upload_the_form_checks_itself_is_marked_required():
    html = render_field(EvidenceForm()["certificate"])
    assert 'class="ui-required"' in html
    assert "(optional)" not in html
    assert 'aria-required="true"' in html


def test_a_checkbox_group_marks_its_legend_and_every_box():
    form = EvidenceForm()
    html = render_field(form["solutions"])
    assert 'class="ui-required"' in html
    assert html.count('aria-required="true"') == len(form.fields["solutions"].choices)
    assert " required" not in html


def test_a_required_checkbox_is_marked_beside_its_own_label():
    html = render_field(EvidenceForm()["agreed"])
    assert 'class="ui-required"' in html


def test_a_draft_promises_what_submitting_will_demand():
    form = DraftEvidenceForm()
    assert 'class="ui-required"' in render_field(form["name"])
    assert 'aria-required="true"' in render_field(form["name"])
    assert "(optional)" in render_field(form["nickname"])


def test_a_field_another_answer_decides_carries_both_markers():
    html = render_field(EvidenceForm()["website"])
    assert not marker_hidden(html, "data-required-marker")
    assert marker_hidden(html, "data-optional-marker")

    form = EvidenceForm()
    form.fields["website"].required = False
    relaxed = render_field(form["website"])
    assert marker_hidden(relaxed, "data-required-marker")
    assert not marker_hidden(relaxed, "data-optional-marker")


def test_a_form_that_marks_nothing_leaves_the_asterisk_off():
    html = render_field(SignInForm()["login"])
    assert "ui-required" not in html
    assert " required" in html.split("<input", 1)[1]
    assert "(optional)" not in render_field(SignInForm()["remember"])


def test_a_disabled_field_is_marked_neither_way():
    form = ProfileForm()
    assert "ui-required" not in render_field(form["email"])
    assert "(optional)" not in render_field(form["email"])
