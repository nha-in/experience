import re

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from ohc_experience.abdm.forms import EXPIRED_CERTIFICATE
from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.abdm.forms import WasaReviewForm
from ohc_experience.abdm.wasa import WASA_VALIDITY_YEARS

pytestmark = pytest.mark.django_db

FORMS = [WasaReviewForm, ExitEvidenceForm]


def test_wasa_validity_is_one_year():
    assert WASA_VALIDITY_YEARS == 1


@pytest.mark.parametrize("form_class", FORMS)
def test_audit_date_offers_an_expiry_one_year_out(form_class):
    html = render_to_string(
        "experiences/partials/form.html",
        {"form": form_class()},
    )
    assert 'data-autofill-target="wasa_valid_until"' in html
    assert f'data-autofill-years="{WASA_VALIDITY_YEARS}"' in html
    assert "one year from the audit date" in html


@pytest.mark.parametrize("form_class", FORMS)
def test_only_the_audit_date_drives_the_autofill(form_class):
    form = form_class()
    assert "data-autofill-target" in form.fields["wasa_date"].widget.attrs
    for name, field in form.fields.items():
        if name != "wasa_date":
            assert "data-autofill-target" not in field.widget.attrs


@pytest.mark.parametrize("form_class", FORMS)
def test_expiry_stays_required_and_editable(form_class):
    field = form_class().fields["wasa_valid_until"]
    assert field.required
    assert not field.disabled
    assert not field.widget.attrs.get("readonly")


@pytest.mark.parametrize("form_class", FORMS)
def test_the_expiry_input_carries_its_floor_and_its_refusal(form_class):
    """What project.js needs to refuse an expired date the moment it lands."""
    html = render_to_string(
        "experiences/partials/form.html",
        {"form": form_class()},
    )
    tag = re.search(r'<input[^>]*name="wasa_valid_until"[^>]*>', html).group(0)
    assert f'min="{timezone.localdate().isoformat()}"' in tag
    assert f'data-expired-message="{EXPIRED_CERTIFICATE}"' in tag
