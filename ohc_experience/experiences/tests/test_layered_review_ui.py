# ruff: noqa: F811
from http import HTTPStatus

import pytest
from django.urls import reverse

from ohc_experience.experiences import workflows
from ohc_experience.experiences.tests.test_port_review_ui import (
    review_item,  # noqa: F401
)
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_stage_counts_keep_search_and_assignee_filters(client, review_item):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    response = client.get(
        reverse("experiences:queue"),
        {
            "item": "milestones",
            "q": "Water pump",
            "assignee": reviewer.pk,
            "scope": "ready",
        },
    )
    assert response.status_code == HTTPStatus.OK
    assert [entry.product for entry in response.context["page"]] == [
        review_item.product,
    ]
    assert response.context["page"][0].matching_reviews == [review_item]
    assert response.context["stage_counts"] == {
        "ready": 1,
        "waiting": 0,
        "decided": 0,
        "all": 1,
    }
    assert response.context["queue_scope"] == "ready"


def test_the_stage_select_carries_the_stage_that_the_tabs_show(client, review_item):
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.get(reverse("experiences:queue"), {"scope": "decided"})
    compact = " ".join(response.content.decode().split())

    assert '<option value="decided" selected>Done (0)</option>' in compact
    assert "Pending (1)</option>" in compact
    assert 'type="hidden" name="scope"' not in compact


def test_stage_counts_ignore_a_status_filter(client, review_item):
    client.force_login(ReviewerFactory(is_nha_team=True))
    response = client.get(
        reverse("experiences:queue"),
        {"scope": "ready", "status": "query_raised"},
    )
    assert list(response.context["page"]) == []
    assert response.context["stage_counts"]["ready"] == 1


def test_queue_scope_tracks_a_real_decision(client, review_item):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    url = reverse("experiences:queue")
    assert not client.get(url, {"scope": "decided"}).context["page"]
    workflows.decide(review_item, reviewer, action="approve", note="Verified")
    assert (
        client.get(url, {"scope": "decided"}).context["page"][0].product
        == review_item.product
    )
    assert not client.get(url, {"scope": "ready"}).context["page"]
    assert not client.get(url).context["page"]
    assert (
        client.get(url, {"scope": "all"}).context["page"][0].product
        == review_item.product
    )


def test_dashboard_counts_use_current_reviewer_and_canonical_milestone(
    client,
    review_item,
):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    url = reverse("experiences:assess-dashboard")
    response = client.get(url)
    assert response.context["my_open"] == 1
    # A meter whose total is missing from the context renders full width.
    assert b"width: %" not in response.content
    workflows.decide(review_item, reviewer, action="approve", note="Verified")
    response = client.get(url)
    assert response.context["my_open"] == 0
    approved = {
        row["label"]: row["count"] for row in response.context["approved_by_milestone"]
    }
    assert approved["INS"] == 1
