from datetime import timedelta

import pytest
from django.utils import timezone

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.forms import EXPIRED_CERTIFICATE
from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.abdm.tests.test_workflow import files

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("draft", [True, False])
@pytest.mark.parametrize(
    ("field", "offset", "message"),
    [
        (
            "start_date",
            1,
            "The sandbox testing start date cannot be in the future.",
        ),
        (
            "end_date",
            1,
            "The sandbox testing end date cannot be in the future.",
        ),
        (
            "tentative_demo_date",
            -1,
            "The tentative demo date cannot be in the past.",
        ),
    ],
)
def test_dates_outside_today_limits_are_rejected(draft, field, offset, message):
    value = timezone.localdate() + timedelta(days=offset)
    form = ExitEvidenceForm(
        data=evidence_data() | {field: value.isoformat()},
        files=files(),
        draft=draft,
    )

    assert not form.is_valid()
    assert message in form.errors[field]


@pytest.mark.parametrize("draft", [True, False])
def test_all_sandbox_and_demo_dates_can_be_today(draft):
    today = timezone.localdate()
    dates = dict.fromkeys(
        ("start_date", "end_date", "tentative_demo_date"),
        today.isoformat(),
    )
    form = ExitEvidenceForm(
        data=evidence_data() | dates,
        files=files(),
        draft=draft,
    )

    assert form.is_valid(), form.errors
    for field in dates:
        assert form.cleaned_data[field] == today


@pytest.mark.parametrize("draft", [True, False])
def test_past_testing_and_future_demo_dates_are_allowed(draft):
    form = ExitEvidenceForm(data=evidence_data(), files=files(), draft=draft)

    assert form.is_valid(), form.errors


def test_incomplete_draft_does_not_require_sandbox_or_demo_dates():
    form = ExitEvidenceForm(data={}, draft=True)

    assert form.is_valid(), form.errors


@pytest.mark.parametrize("draft", [True, False])
@pytest.mark.parametrize(
    ("field", "offset", "message"),
    [
        ("end_date", -2, "Testing must end on or after its start date."),
        (
            "tentative_demo_date",
            -2,
            "The demo must be on or after the testing end date.",
        ),
    ],
)
def test_date_chronology_is_still_required(draft, field, offset, message):
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)
    dates = {
        "start_date": yesterday.isoformat(),
        "end_date": yesterday.isoformat(),
        "tentative_demo_date": today.isoformat(),
        field: (today + timedelta(days=offset)).isoformat(),
    }
    form = ExitEvidenceForm(
        data=evidence_data() | dates,
        files=files(),
        draft=draft,
    )

    assert not form.is_valid()
    assert message in form.errors[field]


def test_date_picker_limits_refresh_for_each_form(monkeypatch):
    today = timezone.localdate()
    monkeypatch.setattr(timezone, "localdate", lambda: today)
    first = ExitEvidenceForm()
    tomorrow = today + timedelta(days=1)
    monkeypatch.setattr(timezone, "localdate", lambda: tomorrow)
    second = ExitEvidenceForm()

    for form, day in ((first, today), (second, tomorrow)):
        for field in ("start_date", "end_date", "wasa_date"):
            assert form.fields[field].widget.attrs["max"] == day.isoformat()
        assert form.fields["tentative_demo_date"].widget.attrs["min"] == day.isoformat()


def test_wasa_expiry_picker_offers_no_expired_date(monkeypatch):
    """Floored at the day the page is built, like the audit date's cap."""
    today = timezone.localdate()
    for day in (today, today + timedelta(days=1)):
        monkeypatch.setattr(timezone, "localdate", lambda day=day: day)
        attrs = ExitEvidenceForm().fields["wasa_valid_until"].widget.attrs
        assert attrs["min"] == day.isoformat()
        assert "max" not in attrs


def _expired_certificate():
    today = timezone.localdate()
    return {
        "wasa_date": (today - timedelta(days=400)).isoformat(),
        "wasa_valid_until": (today - timedelta(days=30)).isoformat(),
    }


def test_an_expired_certificate_is_refused_on_submission():
    form = ExitEvidenceForm(
        data=evidence_data() | _expired_certificate(),
        files=files(),
    )

    assert not form.is_valid()
    assert form.errors["wasa_valid_until"] == [EXPIRED_CERTIFICATE]


def test_a_draft_keeps_an_expired_certificate():
    """Records migrated with an expired certificate must still save."""
    form = ExitEvidenceForm(
        data=evidence_data() | _expired_certificate(),
        files=files(),
        draft=True,
    )

    assert form.is_valid(), form.errors


def test_a_certificate_expiring_today_is_still_current():
    form = ExitEvidenceForm(
        data=evidence_data() | {"wasa_valid_until": timezone.localdate().isoformat()},
        files=files(),
    )

    assert form.is_valid(), form.errors
