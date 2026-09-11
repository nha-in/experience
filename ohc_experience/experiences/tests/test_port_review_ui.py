from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.tests.example_program import SupplierQuality
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def review_item(monkeypatch, settings, owner_membership):
    for attribute in ("_definitions", "_forms", "_programs"):
        monkeypatch.setattr(registry, attribute, dict(getattr(registry, attribute)))
    registry.register_program(SupplierQuality)
    settings.EXPERIENCE_PORTAL = SupplierQuality.key
    workspace, form = workflows.register_product(
        owner_membership.organisation,
        owner_membership.user,
        data={
            "equipment_name": "Water pump",
            "summary": "Industrial equipment",
            "checks": ["Quality:inspection", "Quality:release"],
        },
    )
    assert workspace, form.errors
    item = workspace.product.milestones.get(key="inspection").application.review_item
    item, form, saved = workflows.save_review_form(
        item,
        owner_membership.user,
        data={"report_reference": "Q-PORT-1", "score": 95},
        submit=True,
    )
    assert saved, form.errors
    return item


@pytest.mark.parametrize("route", ["assess-dashboard", "queue", "pending-queries"])
def test_reviewer_surfaces_render_with_another_program(review_item, client, route):
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.get(reverse(f"experiences:{route}"))
    assert response.status_code == HTTPStatus.OK
    assert b"Supplier Quality Portal" in response.content
    assert b"ABDM" not in response.content
    assert b"HIE-CM" not in response.content


def test_queue_filters_still_work_when_requested_through_htmx(review_item, client):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    response = client.get(
        reverse("experiences:queue"),
        {"kind": "mine", "item": "Quality", "q": "Water pump", "status": "in_review"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == HTTPStatus.OK
    assert list(response.context["page"]) == [review_item]
    assert review_item.get_absolute_url().encode() in response.content
    assert b'aria-label="Queue pagination"' in response.content
    response = client.get(reverse("experiences:queue"), {"q": "No such equipment"})
    assert response.context["page"].paginator.count == 0
    assert b"No reviews match these filters." in response.content


def test_queue_filters_by_exact_product_and_preserves_it_in_navigation(
    review_item,
    owner_membership,
    client,
):
    other_workspace, form = workflows.register_product(
        owner_membership.organisation,
        owner_membership.user,
        data={
            "equipment_name": "Water pump accessory",
            "summary": "A separate product with a similar name",
            "checks": ["Quality:inspection", "Quality:release"],
        },
    )
    assert other_workspace, form.errors
    other_item = (
        other_workspace.product.milestones.get(key="inspection")
        .application.review_item
    )
    other_item, form, saved = workflows.save_review_form(
        other_item,
        owner_membership.user,
        data={"report_reference": "Q-PORT-2", "score": 90},
        submit=True,
    )
    assert saved, form.errors

    reviewer = ReviewerFactory(is_nha_team=True)
    client.force_login(reviewer)
    reference = review_item.product.workspace.reference
    response = client.get(
        reverse("experiences:queue"),
        {"scope": "open", "product": reference},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.context["filters"]["product"] == reference
    assert {
        item.product_id for item in response.context["page"]
    } == {review_item.product_id}
    assert other_item not in response.context["page"]
    assert set(
        response.context["product_choices"].values_list("reference", flat=True),
    ) == {reference, other_workspace.reference}
    assert f'value="{reference}"'.encode() in response.content
    assert (
        f"?scope=decided&amp;kind=&amp;status=&amp;item=&amp;product={reference}".encode()
        in response.content
    )
    assert (
        f"?kind=mine&amp;scope=open&amp;status=&amp;item=&amp;product={reference}".encode()
        in response.content
    )
    assert f"product={reference}".encode() in response.context["filter_query"].encode()
    assert b'href="?kind=&amp;scope=open"' in response.content


def test_product_overview_pending_review_card_is_reviewer_only(
    review_item,
    owner_membership,
    client,
):
    workspace = review_item.product.workspace
    reviewer = ReviewerFactory(is_nha_team=True)
    client.force_login(reviewer)
    response = client.get(workspace.get_absolute_url())
    expected_count = review_item.product.review_items.filter(
        status__in=["new", "in_review", "query_raised"],
    ).count()

    assert response.status_code == HTTPStatus.OK
    assert response.context["pending_review_count"] == expected_count
    assert b"Pending review" in response.content
    assert (
        f"{reverse('experiences:queue')}?scope=open&amp;product={workspace.reference}".encode()
        in response.content
    )

    client.force_login(owner_membership.user)
    response = client.get(workspace.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert b"Pending review" not in response.content
    assert client.get(reverse("experiences:queue")).status_code == HTTPStatus.FORBIDDEN


def test_review_decisions_follow_grants_and_assignment_only_labels(review_item, client):
    reviewer = ReviewerFactory(is_nha_team=True)
    client.force_login(reviewer)
    response = client.get(review_item.get_absolute_url())
    assert b"data-decision-form" in response.content
    assert b"field=form#decision" in response.content
    assert b"field=score#decision" not in response.content
    assert b'name="assignee"' not in response.content

    read_only = UserFactory(is_nha_team=True)
    AccessGrant.objects.create(
        user=read_only,
        program=SupplierQuality.key,
        area=AccessGrant.Area.REVIEW,
        category="*",
    )
    client.force_login(read_only)
    response = client.get(review_item.get_absolute_url())
    assert b"data-decision-form" not in response.content
    assert b"does not include queries or decisions" in response.content

    client.force_login(UserFactory(is_superuser=True))
    response = client.get(review_item.get_absolute_url())
    assert b'name="assignee"' in response.content
    response = client.post(
        review_item.get_absolute_url(),
        {"intent": "assign", "assignee": reviewer.pk},
    )
    assert response.status_code == HTTPStatus.FOUND
    review_item.refresh_from_db()
    assert review_item.assignee == reviewer

    # Someone other than the assignee can still record the decision.
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.post(review_item.get_absolute_url(), {"action": "approve"})
    assert response.status_code == HTTPStatus.FOUND
    review_item.refresh_from_db()
    assert review_item.status == "approved"
    assert review_item.assignee == reviewer


def test_query_validation_reply_resolution_and_approval_through_portal(
    review_item,
    owner_membership,
    client,
):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    url = review_item.get_absolute_url()
    response = client.post(
        url,
        {"action": "query", "field_key": "score", "note": ""},
    )
    assert response.status_code == HTTPStatus.OK
    assert response.context["decision_action"] == "query"
    assert response.context["query_field"] == "score"
    assert b"Enter a reason or question" in response.content

    response = client.post(
        url,
        {"action": "query", "field_key": "score", "note": "Confirm this score."},
    )
    assert response.status_code == HTTPStatus.FOUND
    query = review_item.queries.get()
    response = client.get(url)
    assert response.context["unresolved_query_count"] == 1
    assert response.context["awaiting_reply_count"] == 1
    assert b'data-approval-blocked="true"' in response.content
    assert b"Confirm this score." in response.content
    assert b"waiting for the integrator" in response.content

    client.force_login(owner_membership.user)
    response = client.get(reverse("experiences:pending-queries"))
    assert b"Reply needed" in response.content
    response = client.post(
        reverse("experiences:query-action", args=[query.pk]),
        {"body": "Confirmed against the inspection report."},
    )
    assert response.status_code == HTTPStatus.FOUND
    client.force_login(reviewer)
    response = client.get(url)
    assert response.context["unresolved_query_count"] == 1
    assert response.context["awaiting_reply_count"] == 0
    assert b"Mark resolved" in response.content
    assert b"Review replies" in response.content
    assert b"ready for your review" in response.content
    pending = client.get(reverse("experiences:pending-queries"))
    assert b"Replies received" in pending.content
    assert b"Awaiting reply" not in pending.content
    assert f"{url}#queries".encode() in pending.content
    response = client.post(
        reverse("experiences:query-action", args=[query.pk]),
        {"intent": "resolve"},
    )
    assert response.status_code == HTTPStatus.FOUND
    response = client.post(url, {"action": "approve", "note": "Evidence verified."})
    assert response.status_code == HTTPStatus.FOUND
    review_item.refresh_from_db()
    assert review_item.status == "approved"
    response = client.get(url)
    assert b"Evidence verified." in response.content
    assert b"data-decision-form" not in response.content
    snapshot_url = reverse(
        "experiences:submission",
        args=[review_item.pk, review_item.selected_submission_id],
    )
    assert snapshot_url.encode() in response.content
    response = client.get(snapshot_url)
    assert response.status_code == HTTPStatus.OK
    assert b"Q-PORT-1" in response.content
    assert b"Water pump" in response.content
    assert review_item.reference.encode() in response.content


@pytest.mark.parametrize(
    ("scope", "incompatible_status", "expected_statuses"),
    [
        ("open", "approved", {"new", "in_review", "query_raised"}),
        ("decided", "in_review", {"approved", "sent_back"}),
        (
            "all",
            "draft",
            {"new", "in_review", "query_raised", "approved", "sent_back"},
        ),
    ],
)
def test_queue_scope_removes_conflicting_status_without_losing_other_filters(
    review_item,
    client,
    scope,
    incompatible_status,
    expected_statuses,
):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    if scope == "decided":
        response = client.post(review_item.get_absolute_url(), {"action": "approve"})
        assert response.status_code == HTTPStatus.FOUND

    response = client.get(
        reverse("experiences:queue"),
        {
            "scope": scope,
            "status": incompatible_status,
            "kind": "mine",
            "item": "Quality",
            "q": "Water pump",
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == HTTPStatus.OK
    assert list(response.context["page"]) == [review_item]
    assert "status" not in response.context["filters"]
    assert "status=" not in response.context["filter_query"]
    assert set(dict(response.context["statuses"])) == expected_statuses
    assert response.context["filters"]["kind"] == "mine"
    assert response.context["filters"]["item"] == "Quality"
    assert response.context["filters"]["q"] == "Water pump"
    assert f'href="?kind=mine&amp;scope={scope}"'.encode() in response.content


def test_empty_personal_queue_keeps_its_scope_when_clearing_search(
    review_item,
    client,
):
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.get(
        reverse("experiences:queue"),
        {"scope": "open", "kind": "mine", "q": "not found"},
    )
    assert b"No reviews match these filters." in response.content
    assert b'href="?kind=mine&amp;scope=open"' in response.content
    response = client.get(
        reverse("experiences:queue"),
        {"scope": "open", "kind": "mine"},
    )
    assert b"No requests in this view" in response.content
    assert b"View all requests" in response.content
    assert b"Clear filters" not in response.content
