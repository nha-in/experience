"""Integrator submission actions submit evidence once without changing siblings."""

# ruff: noqa: F811, PLR2004
from datetime import timedelta
from html.parser import HTMLParser
from http import HTTPStatus

import pytest
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.organisations.tests.factories import MembershipFactory

pytestmark = pytest.mark.django_db


def track_url(environment, key="m1", code="ABDM"):
    return (
        reverse(
            "experiences:track",
            args=[environment["workspace"].reference, code],
        )
        + f"?milestone={key}"
    )


def sandbox_dates(index=0):
    today = timezone.localdate()
    return {
        "start_date": (today - timedelta(days=30 - index * 4)).isoformat(),
        "end_date": (today - timedelta(days=3 - index)).isoformat(),
    }


def submit_data(*additional, intent="submit", revision="", **changes):
    return {
        **evidence_data(),
        **dict(files().items()),
        "intent": intent,
        "revision": revision,
        "additional_reviews": [f"{item.pk}:" for item in additional],
        **{
            f"milestone_{item.pk}_{field}": value
            for index, item in enumerate(additional)
            for field, value in sandbox_dates(index).items()
        },
        **changes,
    }


def attributes_by(response, attribute):
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = {}

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if attribute in attributes:
                self.elements[attributes[attribute]] = attributes

    parser = Elements()
    parser.feed(response.content.decode())
    return parser.elements


def review_state(*items):
    """Include evidence and history so a rejected request cannot leave partial work."""
    result = []
    for item in items:
        item.refresh_from_db()
        result.append(
            (
                item.pk,
                item.status,
                item.selected_submission_id,
                item.submitted_at,
                item.application.status,
                item.form.submissions.count(),
                item.history.count(),
            ),
        )
    return result


def save_draft(environment, key):
    item, form, saved = workflows.save_review_form(
        milestone(environment, key),
        environment["applicant"],
        data=evidence_data(),
        files=files(),
        submit=False,
    )
    assert saved, form.errors
    return item


def test_fresh_m1_offers_compatible_m2_and_m3_submission_choices(environment, client):
    client.force_login(environment["applicant"])
    response = client.get(track_url(environment))

    assert response.status_code == HTTPStatus.OK
    choices = {
        choice["code"]: choice for choice in response.context["submission_choices"]
    }
    assert choices.keys() == {"M2", "M3", "M4"}
    panels = attributes_by(response, "data-milestone-dates")
    for key, code in (("m2", "M2"), ("m3", "M3")):
        choice = choices[code]
        assert choice["item"] == milestone(environment, key)
        assert choice["token"] == f"{milestone(environment, key).pk}:"
        assert choice["name"]
        assert "requires" in choice
        assert choice["selected"] is False
        assert f'value="{choice["token"]}"'.encode() in response.content
        assert {field["name"] for field in choice["dates"]} == {
            f"milestone_{choice['item'].pk}_start_date",
            f"milestone_{choice['item'].pk}_end_date",
        }
        for field in choice["dates"]:
            assert field["value"] == ""
            assert not field["errors"]
            assert field["max"] == timezone.localdate().isoformat()
            assert field["id"]
            assert field["label"]
        assert "hidden" in panels[str(choice["item"].pk)]
    assert b'name="additional_reviews"' in response.content


def test_choices_exclude_saved_work_pending_approved_and_auto_approved_requests(
    environment,
    client,
):
    pending = submit(environment)
    draft = save_draft(environment, "m3")
    approved = approve(environment, "m4")
    other_pending = submit(environment, "p1")
    current = milestone(environment, "m2")
    client.force_login(environment["applicant"])
    response = client.get(track_url(environment, "m2"))

    choice_ids = {
        choice["item"].pk for choice in response.context["submission_choices"]
    }
    assert not choice_ids.intersection(
        {
            pending.pk,
            draft.pk,
            approved.pk,
            other_pending.pk,
            current.pk,
            milestone(environment, "uhi1").pk,
        },
    )
    assert not choice_ids


def test_m2_has_one_evidence_form_after_m1_is_submitted(environment, client):
    submit(environment)
    client.force_login(environment["applicant"])
    response = client.get(track_url(environment, "m2"))

    assert response.status_code == HTTPStatus.OK
    assert response.content.count(b'id="evidence-form"') == 1
    assert response.content.count(b"data-request-submit") == 1
    assert b'id="reuse-evidence-title"' not in response.content
    assert b"data-manual-evidence-panel" not in response.content
    assert b"Use different evidence" not in response.content
    assert b'name="source_revision"' not in response.content
    inputs = attributes_by(response, "name")
    for field in ("start_date", "end_date"):
        assert inputs[field].get("value", "") == ""
        assert inputs[field]["type"] == "date"
        assert response.content.count(f'name="{field}"'.encode()) == 1
    assert {choice["code"] for choice in response.context["submission_choices"]} == {
        "M3",
        "M4",
    }


def test_batch_submit_m1_m2_m3_creates_three_pending_reviews(environment, client):
    items = [milestone(environment, key) for key in ("m1", "m2", "m3")]
    client.force_login(environment["applicant"])
    payload = submit_data(*items[1:])
    response = client.post(track_url(environment), payload)

    assert response.status_code == HTTPStatus.FOUND
    for item in items:
        item.refresh_from_db()
        assert item.status == ReviewItem.Status.NEW
        assert item.application.status == "under_review"
        assert item.submitted_at
        assert item.selected_submission.status == "completed"
        assert item.selected_submission.attachments.exists()
        for field in ("start_date", "end_date"):
            posted_field = field if item == items[0] else f"milestone_{item.pk}_{field}"
            assert item.selected_submission.data[field] == payload[posted_field]
    assert len({item.selected_submission_id for item in items}) == 3
    assert milestone(environment, "m4").status == ReviewItem.Status.DRAFT
    assert milestone(environment, "uhi1").status == ReviewItem.Status.DRAFT


def test_invalid_batch_data_preserves_choices_and_submits_nothing(environment, client):
    items = [milestone(environment, key) for key in ("m1", "m2", "m3")]
    before = review_state(*items)
    client.force_login(environment["applicant"])
    response = client.post(
        track_url(environment),
        submit_data(*items[1:], start_date="not-a-date"),
    )

    assert response.status_code == HTTPStatus.OK
    assert "start_date" in response.context["form"].errors
    selected = {
        choice["item"].pk
        for choice in response.context["submission_choices"]
        if choice["selected"]
    }
    assert selected == {item.pk for item in items[1:]}
    assert review_state(*items) == before


@pytest.mark.parametrize(
    ("field", "problem"),
    [
        ("start_date", "missing"),
        ("end_date", "missing"),
        ("start_date", "invalid"),
        ("start_date", "future"),
        ("end_date", "before_start"),
    ],
)
def test_selected_milestone_date_errors_retain_values_and_submit_nothing(
    environment,
    client,
    field,
    problem,
):
    items = [milestone(environment, key) for key in ("m1", "m2", "m3")]
    current, invalid, valid = items
    payload = submit_data(invalid, valid)
    field_name = f"milestone_{invalid.pk}_{field}"
    if problem == "missing":
        payload.pop(field_name)
    elif problem == "invalid":
        payload[field_name] = "not-a-date"
    elif problem == "future":
        payload[field_name] = (timezone.localdate() + timedelta(days=1)).isoformat()
    else:
        payload[field_name] = (timezone.localdate() - timedelta(days=60)).isoformat()
    before = review_state(current, invalid, valid)
    client.force_login(environment["applicant"])
    response = client.post(track_url(environment), payload)

    assert response.status_code == HTTPStatus.OK
    choices = {
        choice["item"].pk: choice for choice in response.context["submission_choices"]
    }
    panels = attributes_by(response, "data-milestone-dates")
    for item in (invalid, valid):
        choice = choices[item.pk]
        assert choice["selected"] is True
        assert "hidden" not in panels[str(item.pk)]
        for date in choice["dates"]:
            assert date["value"] == payload.get(date["name"], "")
    invalid_field = next(
        date for date in choices[invalid.pk]["dates"] if date["name"] == field_name
    )
    assert invalid_field["errors"]
    assert review_state(*items) == before


@pytest.mark.parametrize("field", ["start_date", "end_date"])
def test_current_milestone_submission_requires_its_own_testing_dates(
    environment,
    client,
    field,
):
    source = submit(environment)
    target = milestone(environment, "m2")
    payload = submit_data()
    payload.pop(field)
    before = review_state(source, target)
    client.force_login(environment["applicant"])
    response = client.post(track_url(environment, "m2"), payload)

    assert response.status_code == HTTPStatus.OK
    form = response.context["form"]
    assert field in form.errors
    assert not form[field].value()
    other_field = "end_date" if field == "start_date" else "start_date"
    assert form[other_field].value() == payload[other_field]
    assert review_state(source, target) == before


def test_existing_other_category_evidence_does_not_expand_submission_choices(
    environment,
    client,
):
    first = submit(environment)
    other = submit(environment, "p1")
    assert first.form_id == other.form_id
    client.force_login(environment["applicant"])
    response = client.get(track_url(environment, "m2"))

    assert {choice["code"] for choice in response.context["submission_choices"]} == {
        "M3",
        "M4",
    }
    phr = client.get(track_url(environment, "p1", "PHR"))
    # The PHR track offers its own later phases, and neither M1 nor the locker.
    assert {choice["code"] for choice in phr.context["submission_choices"]} == {
        "P2",
        "P3",
    }


@pytest.mark.parametrize("key", ["p1", "p4"])
def test_batch_rejects_other_categories_even_when_the_form_record_is_shared(
    environment,
    client,
    key,
):
    current = milestone(environment)
    other = milestone(environment, key)
    assert current.form_id == other.form_id
    before = review_state(current, other)
    client.force_login(environment["applicant"])
    response = client.post(track_url(environment), submit_data(other))

    assert response.status_code == HTTPStatus.OK
    assert review_state(current, other) == before
    assert other.pk not in {
        choice["item"].pk for choice in response.context["submission_choices"]
    }


def test_save_draft_ignores_additional_milestone_choices(environment, client):
    current = milestone(environment)
    additional = milestone(environment, "m2")
    previous_pin = additional.selected_submission_id
    previous_history = additional.history.count()
    client.force_login(environment["applicant"])
    response = client.post(
        track_url(environment),
        submit_data(additional, intent="draft"),
    )

    assert response.status_code == HTTPStatus.FOUND
    current.refresh_from_db()
    assert current.status == ReviewItem.Status.DRAFT
    assert current.selected_submission_id
    assert current.selected_submission.status == "needs_changes"
    additional.refresh_from_db()
    assert additional.status == ReviewItem.Status.DRAFT
    assert additional.selected_submission_id == previous_pin
    assert additional.submitted_at is None
    assert additional.history.count() == previous_history


@pytest.mark.parametrize("token", ["not-a-review", "17", "1:2:3", "x:"])
def test_malformed_additional_review_tokens_leave_every_request_unchanged(
    environment,
    client,
    token,
):
    items = [milestone(environment, key) for key in ("m1", "m2")]
    before = review_state(*items)
    client.force_login(environment["applicant"])
    response = client.post(
        track_url(environment),
        submit_data(additional_reviews=[token]),
    )

    assert response.status_code == HTTPStatus.OK
    assert review_state(*items) == before


@pytest.mark.parametrize("other_organisation", [False, True])
def test_batch_rejects_targets_from_another_product_or_organisation(
    environment,
    client,
    other_organisation,
):
    membership = MembershipFactory(role="owner") if other_organisation else None
    workspace, form = workflows.register_product(
        membership.organisation if membership else environment["org"],
        membership.user if membership else environment["applicant"],
        data=product_data("Another product"),
    )
    assert workspace, form.errors
    other = workspace.product.milestones.get(key="m2").application.review_item
    current = milestone(environment)
    before = review_state(current, other)
    client.force_login(environment["applicant"])
    response = client.post(track_url(environment), submit_data(other))

    assert response.status_code == HTTPStatus.OK
    assert review_state(current, other) == before
    assert other.pk not in {
        choice["item"].pk for choice in response.context["submission_choices"]
    }


def test_unauthorised_user_cannot_submit_another_organisations_evidence(
    environment,
    client,
):
    current = milestone(environment)
    before = review_state(current)
    client.force_login(environment["outsider"])
    response = client.post(track_url(environment), submit_data())

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert review_state(current) == before


def test_stale_current_revision_does_not_submit_current_or_additional_milestones(
    environment,
    client,
):
    current = save_draft(environment, "m1")
    additional = milestone(environment, "m2")
    before = review_state(current, additional)
    client.force_login(environment["applicant"])
    response = client.post(track_url(environment), submit_data(additional, revision=""))

    assert response.status_code == HTTPStatus.OK
    assert b"teammate" in response.content
    assert review_state(current, additional) == before
