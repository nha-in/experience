from django import forms
from django.template import Context
from django.template import Template
from django.template.loader import render_to_string


class ContactForm(forms.Form):
    email = forms.EmailField(
        help_text="Use your work email.",
        widget=forms.EmailInput(attrs={"aria-describedby": "email-policy"}),
    )


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


def test_error_summary_links_to_prefixed_field_and_includes_general_error():
    form = ContactForm({"contact-email": "invalid"}, prefix="contact")
    form.add_error(None, "Review your contact details.")
    html = render_to_string("components/form_errors.html", {"form": form})
    assert "data-error-summary" in html
    assert 'tabindex="-1"' in html
    assert 'href="#id_contact-email"' in html
    assert 'data-error-field="contact-email"' in html
    assert "Review your contact details." in html


def test_valid_form_does_not_render_error_summary():
    html = render_to_string(
        "components/form_errors.html",
        {"form": ContactForm({"email": "integrator@example.com"})},
    )
    assert "data-error-summary" not in html
