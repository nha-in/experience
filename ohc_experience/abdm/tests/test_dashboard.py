"""The reviewer dashboard's numbers."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.models import ReviewItem
from ohc_experience.abdm.models import ReviewQuery
from ohc_experience.abdm.selectors import dashboard_stats
from ohc_experience.abdm.tests.factories import ComplianceRecordFactory
from ohc_experience.abdm.tests.factories import ReviewItemFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

OPEN_COUNT = 3
FLAGGED_AGE = 12


@pytest.fixture
def reviewer(db):
    return UserFactory.create(name="Nandita Shah", is_ohc_team=True)


@pytest.fixture
def populated(reviewer):
    """Three open items (one old, one assigned, one with a query), two decided."""
    old = ReviewItemFactory.create()
    ReviewItem.objects.filter(pk=old.pk).update(
        submitted_on=timezone.now() - timedelta(days=FLAGGED_AGE),
    )
    assigned = ReviewItemFactory.create(
        status=ReviewItem.Status.IN_REVIEW,
        assignee=reviewer,
        compliance=ComplianceRecordFactory.create(milestone_code="M2"),
    )
    queried = ReviewItemFactory.create(
        status=ReviewItem.Status.QUERY_RAISED,
        compliance=ComplianceRecordFactory.create(
            track_code="UHI",
            milestone_code="UHI1",
        ),
    )
    ReviewQuery.objects.create(item=queried, question="?", raised_by=reviewer)
    approved = ReviewItemFactory.create(
        status=ReviewItem.Status.APPROVED,
        decided_on=timezone.localdate(),
        decided_by=reviewer,
    )
    ReviewItem.objects.filter(pk=approved.pk).update(
        submitted_on=timezone.now() - timedelta(days=4),
    )
    sent_back = ReviewItemFactory.create(
        status=ReviewItem.Status.SENT_BACK,
        decided_on=timezone.localdate(),
        decided_by=reviewer,
    )
    ReviewItem.objects.filter(pk=sent_back.pk).update(
        submitted_on=timezone.now() - timedelta(days=2),
    )
    return {"old": old, "assigned": assigned, "queried": queried}


class TestDashboardStats:
    def test_counts_and_breakdowns(self, populated, reviewer):
        stats = dashboard_stats(user=reviewer)

        assert stats["in_queue"] == OPEN_COUNT
        assert dict(stats["in_queue_by_status"]) == {
            "New": 1,
            "In review": 1,
            "Query raised": 1,
        }
        assert stats["median_days"] == 3  # noqa: PLR2004 — median of 4 and 2
        assert stats["decided_count"] == 2  # noqa: PLR2004
        assert stats["queries_awaiting"] == 1
        assert stats["approved_this_month"] == 1
        assert stats["approved_by_milestone"] == [("HI-CM M1", 1)]
        assert stats["needs_decision"][0] == populated["old"]
        assert stats["needs_decision"][0].needs_attention
        assert dict(stats["queue_by_type"])["Exit request"] == OPEN_COUNT
        assert stats["exit_by_track"] == [("HI-CM", 2), ("UHI", 1)]
        assert [bucket["count"] for bucket in stats["ageing"]] == [2, 0, 1]
        assert stats["ageing"][-1]["over"] is True
        assert stats["over_attention"] == 1
        assert stats["reviewer_load"] == [("Unassigned", 2), ("Nandita Shah", 1)]
        assert stats["my_open"] == 1
        assert len(stats["weeks"]) == 8  # noqa: PLR2004
        assert stats["weeks"][-1]["approved"] == 1
        assert stats["weeks"][-1]["sent_back"] == 1
        assert stats["weeks"][-1]["approved_pct"] == 100  # noqa: PLR2004

    def test_an_empty_portal_has_honest_zeros(self):
        stats = dashboard_stats()

        assert stats["in_queue"] == 0
        assert stats["median_days"] is None
        assert stats["needs_decision"] == []
        assert stats["reviewer_load"] == [("Unassigned", 0)]
        assert all(week["approved_pct"] == 0 for week in stats["weeks"])


class TestDashboardPage:
    def test_renders_every_card(self, sign_in, reviewer, populated):
        response = sign_in(reviewer).get(reverse("assess:dashboard"))
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert response.context["nav_section"] == "dashboard"
        for heading in (
            "In queue",
            "Unassigned",
            "Approved this month",
            "Needs a decision",
            "Decisions over the last 8 weeks",
            "Exit requests by track",
            "Approved by milestone",
        ):
            assert heading in html
        # The median is a sentence under the queue split rather than a card of
        # its own; ageing and reviewer load read off the Needs-a-decision table.
        assert "Median 3 days to a decision" in html
        assert "12 days" in html
        assert "Nandita Shah" in html
