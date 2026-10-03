# ruff: noqa: F811
import re
from datetime import date
from datetime import timedelta

import pytest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.forms import EXPIRED_CERTIFICATE
from ohc_experience.abdm.forms import NOT_ONE_YEAR_NOTE
from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.abdm.forms import WasaReviewForm
from ohc_experience.abdm.tests.test_wasa_lifecycle import certificate_data
from ohc_experience.abdm.tests.test_wasa_lifecycle import request_renewal
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import review_section
from ohc_experience.abdm.wasa import WASA_VALIDITY_YEARS
from ohc_experience.abdm.wasa import expiry_warnings
from ohc_experience.abdm.wasa import one_year_expiry
from ohc_experience.experiences.definitions import AnswerWarning

pytestmark = pytest.mark.django_db

FORMS = [WasaReviewForm, ExitEvidenceForm]


def test_wasa_validity_is_one_year():
    assert WASA_VALIDITY_YEARS == 1


@pytest.mark.parametrize(
    ("audit", "expiry"),
    [
        (date(2026, 9, 4), date(2027, 9, 3)),
        # A year that opens on the 1st ends at the close of the month before.
        (date(2026, 3, 1), date(2027, 2, 28)),
        (date(2027, 3, 1), date(2028, 2, 29)),
        # 29 February has no anniversary in a common year.
        (date(2024, 2, 29), date(2025, 2, 28)),
    ],
)
def test_a_year_from_the_audit_ends_the_day_before_its_anniversary(audit, expiry):
    """The audit day is the year's first, as project.js derives the expiry."""
    assert one_year_expiry(audit) == expiry


def test_an_expiry_a_year_from_the_audit_is_not_noted():
    assert not expiry_warnings(
        {"wasa_date": "2026-09-04", "wasa_valid_until": "2027-09-03"},
    )


@pytest.mark.parametrize(
    "expiry",
    ["2027-09-04", "2027-03-03", "2028-09-03"],
    ids=["the anniversary", "six months", "two years"],
)
def test_any_other_period_is_noted_with_the_date_a_year_would_end(expiry):
    assert expiry_warnings(
        {"wasa_date": "2026-09-04", "wasa_valid_until": expiry},
    ) == {
        "wasa_valid_until": AnswerWarning(
            "Not one year",
            "A year from the audit date would run to 03/09/2027.",
        ),
    }


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"wasa_date": "2026-09-04"},
        {"wasa_valid_until": "2027-03-03"},
        {"wasa_date": "", "wasa_valid_until": "2027-03-03"},
    ],
)
def test_nothing_is_noted_without_both_dates(data):
    assert not expiry_warnings(data)


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
def test_the_expiry_is_required_and_the_integrator_can_correct_it(form_class):
    field = form_class().fields["wasa_valid_until"]
    assert field.required
    assert not field.widget.attrs.get("readonly")
    assert not field.disabled


@pytest.mark.parametrize("form_class", FORMS)
def test_the_expiry_input_is_editable_in_the_page(form_class):
    html = render_to_string(
        "experiences/partials/form.html",
        {"form": form_class()},
    )
    tag = re.search(r'<input[^>]*name="wasa_valid_until"[^>]*>', html).group(0)
    assert "readonly" not in tag
    assert "disabled" not in tag


@pytest.mark.parametrize("form_class", FORMS)
def test_an_expiry_before_the_audit_is_still_refused(form_class):
    """Any period is accepted, but never one that ends before it begins."""
    today = timezone.localdate()
    form = form_class(
        data={
            "wasa_date": today.isoformat(),
            "wasa_valid_until": (today - timedelta(days=1)).isoformat(),
        },
    )

    assert not form.is_valid()
    assert form.errors["wasa_valid_until"] == [
        "The expiry date must be on or after the audit date.",
    ]


@pytest.mark.parametrize("form_class", FORMS)
@pytest.mark.parametrize("days", [1, 180, 366, 730])
def test_an_expiry_of_any_period_after_the_audit_is_accepted(form_class, days):
    """The certificate states its own period; the form notes it, never refuses."""
    audit = timezone.localdate() - timedelta(days=1)
    form = form_class(
        data={
            "wasa_date": audit.isoformat(),
            "wasa_valid_until": (audit + timedelta(days=days)).isoformat(),
        },
    )

    form.is_valid()

    assert "wasa_valid_until" not in form.errors


@pytest.mark.parametrize("form_class", FORMS)
def test_the_expiry_input_carries_its_floor_its_refusal_and_its_note(form_class):
    """What project.js needs to refuse an expired date and note another period."""
    html = render_to_string(
        "experiences/partials/form.html",
        {"form": form_class(initial={"wasa_date": "2026-09-04"})},
    )
    tag = re.search(r'<input[^>]*name="wasa_valid_until"[^>]*>', html).group(0)
    assert f'min="{timezone.localdate().isoformat()}"' in tag
    assert f'data-expired-message="{EXPIRED_CERTIFICATE}"' in tag
    assert f'data-period-message="{NOT_ONE_YEAR_NOTE}"' in tag.replace("&#x27;", "'")
    # A certificate states its own period, so nothing caps it a year out: only
    # the four-digit year every date field stops at.
    assert re.search(r'max="([^"]*)"', tag).group(1) == "9999-12-31"


def _not_one_year(environment):
    """A renewal whose certificate runs six weeks rather than a year."""
    certificate = certificate_data(expires_in=30, audited_ago=12)
    audit = date.fromisoformat(certificate["wasa_date"])
    return request_renewal(environment, certificate=certificate), audit


def review_card(client, item):
    """The request's card on the staff product page."""
    url = reverse("experiences:product-detail", args=[item.product.reference])
    return review_section(client.get(url).content.decode(), item)


def test_the_reviewer_is_shown_an_expiry_that_is_not_one_year(environment, client):
    item, audit = _not_one_year(environment)
    client.force_login(environment["reviewer"])

    html = review_card(client, item)

    assert "Not one year</span>" in html
    assert (
        f"A year from the audit date would run to {one_year_expiry(audit):%d/%m/%Y}."
    ) in html


def test_a_one_year_certificate_is_shown_to_the_reviewer_without_a_note(
    environment,
    client,
):
    audit = timezone.localdate() - timedelta(days=12)
    item = request_renewal(
        environment,
        certificate=certificate_data(audited_ago=12)
        | {"wasa_valid_until": one_year_expiry(audit).isoformat()},
    )
    client.force_login(environment["reviewer"])

    html = review_card(client, item)

    assert "Not one year" not in html
    assert "A year from the audit date would run to" not in html


def test_the_integrator_sees_the_same_note_on_what_they_submitted(
    environment,
    client,
):
    _item, audit = _not_one_year(environment)
    client.force_login(environment["applicant"])

    html = client.get(
        reverse(
            "experiences:product-certification",
            args=[environment["product"].reference],
        ),
    ).content.decode()

    assert "Not one year</span>" in html
    assert (
        f"A year from the audit date would run to {one_year_expiry(audit):%d/%m/%Y}."
    ) in html
