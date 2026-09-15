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


def test_queue_tabs_keep_search_and_assignee_filters(client, review_item):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    response = client.get(
        reverse("experiences:queue"),
        {
            "kind": "application",
            "q": "Water pump",
            "assignee": reviewer.pk,
            "scope": "ready",
        },
    )
    assert response.status_code == HTTPStatus.OK
    assert list(response.context["page"]) == [review_item]
    counts = {row["value"]: row["count"] for row in response.context["queue_tabs"]}
    assert counts[""] == counts["mine"] == counts["application"] == 1
    assert counts["organisation_verification"] == 0
    assert response.context["queue_scope"] == "ready"


def test_queue_scope_tracks_a_real_decision(client, review_item):
    reviewer = ReviewerFactory(is_nha_team=True)
    workflows.assign_review(review_item, UserFactory(is_superuser=True), reviewer)
    client.force_login(reviewer)
    url = reverse("experiences:queue")
    assert review_item not in client.get(url, {"scope": "decided"}).context["page"]
    workflows.decide(review_item, reviewer, action="approve", note="Verified")
    assert review_item in client.get(url, {"scope": "decided"}).context["page"]
    assert review_item not in client.get(url, {"scope": "ready"}).context["page"]
    assert review_item not in client.get(url).context["page"]
    assert review_item in client.get(url, {"scope": "all"}).context["page"]


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
    workflows.decide(review_item, reviewer, action="approve", note="Verified")
    response = client.get(url)
    assert response.context["my_open"] == 0
    approved = {
        row["label"]: row["count"] for row in response.context["approved_by_milestone"]
    }
    assert approved["INS"] == 1
