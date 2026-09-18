# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import reverify
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def product_url(environment):
    return reverse(
        "experiences:product-detail",
        args=[environment["workspace"].reference],
    )


def batch_data(items, action="approve", note=""):
    return {
        "intent": "bulk_decision",
        "action": action,
        "reviews": [f"{item.pk}:{item.selected_submission_id}" for item in items],
        "note": note,
    }


def test_product_review_combines_submissions_without_exposing_drafts(
    environment,
    client,
):
    m1 = submit(environment)
    m2 = submit(environment, "m2")
    m3, form, saved = workflows.save_review_form(
        milestone(environment, "m3"),
        environment["applicant"],
        data=evidence_data(),
    )
    assert saved, form.errors
    client.force_login(environment["reviewer"])

    response = client.get(product_url(environment))

    assert response.status_code == HTTPStatus.OK
    sections = {row["item"].pk: row for row in response.context["review_sections"]}
    assert sections[m1.pk]["snapshot"] == m1.selected_submission
    assert sections[m2.pk]["snapshot"] == m2.selected_submission
    assert m3.pk not in sections
    assert {item.pk for item in response.context["bulk_items"]} == {m1.pk, m2.pk}
    assert response.context["bulk_approve_blockers"] == []
    assert response.context["bulk_reject_blockers"] == []
    for item in (m1, m2):
        assert f'id="review-{item.pk}"'.encode() in response.content
    assert f'id="review-{m3.pk}"'.encode() not in response.content
    assert b"Not submitted" not in response.content
    assert all(
        tile["item"].pk != m3.pk
        for track in response.context["tracks"]
        for tile in track["tiles"]
    )
    assert b"Accept all" in response.content
    assert b"Reject all" in response.content
    assert f'value="{m3.pk}:{m3.selected_submission_id}"'.encode() not in (
        response.content
    )


@pytest.mark.parametrize(
    ("action", "status"),
    [("approve", "approved"), ("send_back", "sent_back")],
)
def test_bulk_decision_handles_submitted_siblings_and_leaves_m3_pending(
    environment,
    client,
    action,
    status,
):
    m1 = submit(environment)
    m2 = submit(environment, "m2")
    m3 = milestone(environment, "m3")
    client.force_login(environment["reviewer"])

    response = client.post(
        product_url(environment),
        batch_data([m2, m1], action, "Review of both submitted milestones."),
    )

    assert response.status_code == HTTPStatus.FOUND
    assert response.url == product_url(environment) + "#decisions"
    for item in (m1, m2):
        item.refresh_from_db()
        assert item.status == status
        assert item.decision_note == "Review of both submitted milestones."
    m3.refresh_from_db()
    assert m3.status == "draft"


def test_reject_all_requires_a_shared_reason_without_changing_any_review(
    environment,
    client,
):
    items = [submit(environment), submit(environment, "m2")]
    client.force_login(environment["reviewer"])

    response = client.post(
        product_url(environment),
        batch_data(items, "send_back", "  "),
    )

    assert response.status_code == HTTPStatus.OK
    assert b"Enter a reason or question" in response.content
    for item in items:
        item.refresh_from_db()
        assert item.pending


def test_unsent_corrections_after_rejection_are_not_shown_to_reviewers(
    environment,
    client,
):
    item = submit(environment)
    workflows.decide(
        item,
        environment["reviewer"],
        action="send_back",
        reason="Incorrect document",
        note="Please correct the evidence.",
    )
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=evidence_data(),
    )
    assert saved, form.errors
    assert item.status == "sent_back"
    client.force_login(environment["reviewer"])

    response = client.get(product_url(environment))

    section = next(
        row for row in response.context["review_sections"] if row["item"].pk == item.pk
    )
    assert section["snapshot"] is None
    assert response.context["bulk_items"] == []


def test_read_only_reviewer_has_no_bulk_or_individual_decisions(environment, client):
    item = submit(environment)
    reader = UserFactory(is_nha_team=True)
    AccessGrant.objects.create(
        user=reader,
        program="abdm",
        area="review",
        category="HIE-CM",
        can_read=True,
    )
    client.force_login(reader)
    response = client.get(product_url(environment))
    assert response.context["bulk_items"] == []
    assert all(
        not row["available_actions"] for row in response.context["review_sections"]
    )
    assert client.post(product_url(environment), batch_data([item])).status_code == (
        HTTPStatus.FORBIDDEN
    )


def test_single_review_rejects_a_stale_submission_and_retains_the_note(
    environment,
    client,
):
    item = submit(environment)
    client.force_login(environment["reviewer"])

    response = client.post(
        product_url(environment),
        {
            "intent": "decision",
            "review_id": item.pk,
            "revision": "0",
            "action": "approve",
            "note": "Evidence reviewed carefully.",
        },
    )

    assert response.status_code == HTTPStatus.OK
    item.refresh_from_db()
    assert item.pending
    assert b"Evidence reviewed carefully." in response.content


def test_product_actions_cannot_target_another_product(environment, client):
    other, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data={**product_data(), "name": "Other product"},
    )
    assert other, form.errors
    item = other.product.milestones.get(key="m1").application.review_item
    client.force_login(environment["admin"])

    response = client.post(
        product_url(environment),
        {
            "intent": "decision",
            "review_id": item.pk,
            "revision": "0",
            "action": "approve",
        },
    )

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_organisation_verification_is_reviewed_in_the_product(environment, client):
    m1 = submit(environment)
    m2 = submit(environment, "m2")
    verification = reverify(environment)
    client.force_login(environment["reviewer"])

    response = client.get(product_url(environment))

    section = response.context["review_sections"][0]
    assert section["item"] == verification
    assert section["snapshot"] == verification.selected_submission
    assert {item.pk for item in response.context["bulk_items"]} == {
        verification.pk,
        m1.pk,
        m2.pk,
    }
    assert not response.context["bulk_approve_blockers"]
    assert not response.context["bulk_reject_blockers"]

    response = client.post(
        product_url(environment),
        {
            "intent": "decision",
            "review_id": verification.pk,
            "revision": verification.selected_submission_id,
            "action": "approve",
        },
    )
    assert response.url == product_url(environment) + f"#review-{verification.pk}"
    environment["org"].refresh_from_db()
    assert environment["org"].is_verified
    for item in (m1, m2):
        item.refresh_from_db()
        assert item.pending


@pytest.mark.parametrize(
    ("action", "status"),
    [("approve", "approved"), ("send_back", "sent_back")],
)
def test_product_bulk_decisions_include_organisation_verification(
    environment,
    client,
    action,
    status,
):
    items = [submit(environment), submit(environment, "m2"), reverify(environment)]
    client.force_login(environment["reviewer"])

    response = client.post(
        product_url(environment),
        batch_data(items, action, "Review the organisation and product together."),
    )

    assert response.status_code == HTTPStatus.FOUND
    for item in items:
        item.refresh_from_db()
        assert item.status == status


def test_product_actions_cannot_target_another_organisation(environment, client):
    other = Organisation.objects.create(name="Other organisation")
    Membership.objects.create(
        organisation=other,
        user=environment["applicant"],
        role="owner",
    )
    verification = workflows.organisation_review(other, environment["applicant"])
    client.force_login(environment["admin"])

    response = client.post(
        product_url(environment),
        {
            "intent": "decision",
            "review_id": verification.pk,
            "revision": "1",
            "action": "approve",
        },
    )

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_organisation_queries_return_to_the_product(environment, client):
    verification = reverify(environment)
    client.force_login(environment["reviewer"])
    response = client.post(
        product_url(environment),
        {
            "intent": "decision",
            "review_id": verification.pk,
            "revision": verification.selected_submission_id,
            "action": "query",
            "note": "Please explain the organisation document.",
        },
    )
    assert response.url == product_url(environment) + f"#review-{verification.pk}"
    query = verification.queries.get()
    workflows.reply_query(query, environment["applicant"], "It is our certificate.")
    response = client.get(product_url(environment))
    assert (
        f'name="return_to_product" value="{environment["workspace"].reference}"'
        in " ".join(response.content.decode().split())
    )
    response = client.post(
        reverse("experiences:query-action", args=[query.pk]),
        {
            "intent": "resolve",
            "return_to_product": environment["workspace"].reference,
        },
    )
    assert response.url == product_url(environment) + f"#review-{verification.pk}"
    query.refresh_from_db()
    assert query.status == "resolved"


def test_queries_can_be_raised_and_resolved_from_the_product(environment, client):
    item = submit(environment)
    client.force_login(environment["reviewer"])
    response = client.post(
        product_url(environment),
        {
            "intent": "decision",
            "review_id": item.pk,
            "revision": item.selected_submission_id,
            "action": "query",
            "field_key": "form",
            "note": "Please explain the testing scope.",
        },
    )
    assert response.url == product_url(environment) + f"#review-{item.pk}"
    query = item.queries.get()
    workflows.reply_query(query, environment["applicant"], "All required APIs.")

    response = client.get(product_url(environment))
    assert response.context["bulk_approve_blockers"]
    assert not response.context["bulk_reject_blockers"]
    response = client.post(
        reverse("experiences:query-action", args=[query.pk]),
        {"intent": "resolve", "return_to_product": "1"},
    )
    assert response.url == product_url(environment) + f"#review-{item.pk}"
    query.refresh_from_db()
    assert query.status == "resolved"


@pytest.mark.parametrize("reviews", [[], ["bad"], ["1:2", "1:2"], ["1:2:3"]])
def test_malformed_bulk_selection_is_rejected(environment, client, reviews):
    client.force_login(environment["admin"])
    response = client.post(
        product_url(environment),
        {"intent": "bulk_decision", "action": "approve", "reviews": reviews},
    )
    assert response.status_code == HTTPStatus.OK


def test_integrators_cannot_post_product_review_decisions(environment, client):
    item = submit(environment)
    client.force_login(environment["applicant"])
    response = client.post(product_url(environment), batch_data([item]))
    assert response.status_code == HTTPStatus.FORBIDDEN
