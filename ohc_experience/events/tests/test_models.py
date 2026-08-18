"""Which events a vendor sees, in which order, and when.

The dashboard and the events page both read straight off these querysets, so
the "published, not finished, soonest first" rule is pinned here rather than in
a view test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone

from ohc_experience.events.models import Event

pytestmark = pytest.mark.django_db


def make_event(
    title: str,
    *,
    starts_in: timedelta,
    duration: timedelta | None = timedelta(hours=1),
    published: bool = True,
) -> Event:
    starts_at = timezone.now() + starts_in
    return Event.objects.create(
        title=title,
        kind=Event.Kind.WEBINAR,
        starts_at=starts_at,
        ends_at=starts_at + duration if duration else None,
        published_at=timezone.now() - timedelta(days=1) if published else None,
    )


def titles(queryset) -> list[str]:
    return list(queryset.values_list("title", flat=True))


class TestUpcoming:
    def test_published_events_come_soonest_first(self):
        make_event("Next month", starts_in=timedelta(days=30))
        make_event("Tomorrow", starts_in=timedelta(days=1))
        make_event("Next week", starts_in=timedelta(days=7))

        assert titles(Event.objects.upcoming()) == [
            "Tomorrow",
            "Next week",
            "Next month",
        ]

    def test_a_draft_is_not_shown_to_vendors(self):
        make_event("Announced", starts_in=timedelta(days=2))
        make_event("Still a draft", starts_in=timedelta(days=3), published=False)

        assert titles(Event.objects.upcoming()) == ["Announced"]

    def test_an_event_that_has_finished_drops_off(self):
        make_event("Finished yesterday", starts_in=timedelta(days=-1))
        make_event("Starts tomorrow", starts_in=timedelta(days=1))

        assert titles(Event.objects.upcoming()) == ["Starts tomorrow"]

    def test_an_event_in_progress_is_still_upcoming(self):
        make_event(
            "Running right now",
            starts_in=timedelta(minutes=-10),
            duration=timedelta(hours=2),
        )

        assert titles(Event.objects.upcoming()) == ["Running right now"]

    def test_an_open_ended_event_is_judged_on_its_start(self):
        make_event("Started an hour ago", starts_in=timedelta(hours=-1), duration=None)
        make_event("Starts in an hour", starts_in=timedelta(hours=1), duration=None)

        assert titles(Event.objects.upcoming()) == ["Starts in an hour"]


class TestPast:
    def test_past_is_the_complement_of_upcoming(self):
        make_event("Last month", starts_in=timedelta(days=-30))
        make_event("Yesterday", starts_in=timedelta(days=-1))
        make_event("Tomorrow", starts_in=timedelta(days=1))
        make_event(
            "Unpublished and long gone",
            starts_in=timedelta(days=-5),
            published=False,
        )

        assert titles(Event.objects.past()) == ["Yesterday", "Last month"]
        assert titles(Event.objects.upcoming()) == ["Tomorrow"]

    def test_a_draft_is_not_shown_in_the_archive_either(self):
        make_event(
            "Held, never published",
            starts_in=timedelta(days=-2),
            published=False,
        )

        assert titles(Event.objects.past()) == []

    def test_an_open_ended_event_that_started_is_past(self):
        make_event("Started an hour ago", starts_in=timedelta(hours=-1), duration=None)

        assert titles(Event.objects.past()) == ["Started an hour ago"]

    def test_upcoming_and_past_never_overlap(self):
        for offset in (-40, -3, 2, 20):
            make_event(f"Event {offset}", starts_in=timedelta(days=offset))

        upcoming = set(titles(Event.objects.upcoming()))
        past = set(titles(Event.objects.past()))

        assert upcoming & past == set()
        assert upcoming | past == set(titles(Event.objects.published()))


class TestPublishing:
    def test_publish_and_unpublish_round_trip(self):
        event = make_event("Office hours", starts_in=timedelta(days=3), published=False)

        event.publish()
        event.save()
        event.refresh_from_db()

        assert event.is_published is True
        assert titles(Event.objects.upcoming()) == ["Office hours"]

        event.unpublish()
        event.save()
        event.refresh_from_db()

        assert event.is_published is False
        assert event.published_at is None
        assert titles(Event.objects.upcoming()) == []

    def test_publishing_twice_keeps_the_first_publication_date(self):
        event = make_event("Office hours", starts_in=timedelta(days=3), published=False)
        event.publish()
        event.save()
        published_at = event.published_at

        event.publish()
        event.save()
        event.refresh_from_db()

        assert event.published_at == published_at

    def test_republishing_after_a_retraction_stamps_a_new_date(self):
        event = make_event("Office hours", starts_in=timedelta(days=3))
        event.unpublish()
        event.save()

        event.publish()
        event.save()
        event.refresh_from_db()

        assert event.is_published is True


class TestProperties:
    def test_is_past_reads_the_end_when_there_is_one(self):
        running = make_event(
            "Running now",
            starts_in=timedelta(minutes=-5),
            duration=timedelta(hours=1),
        )
        finished = make_event(
            "Finished",
            starts_in=timedelta(hours=-3),
            duration=timedelta(hours=1),
        )

        assert running.is_past is False
        assert finished.is_past is True

    def test_an_event_with_no_venue_is_online(self):
        online = make_event("Webinar", starts_in=timedelta(days=1))
        in_person = make_event("Workshop", starts_in=timedelta(days=1))
        in_person.location = "Thiruvananthapuram"

        assert online.is_online is True
        assert in_person.is_online is False


class TestSlug:
    def test_the_slug_is_built_from_the_title(self):
        event = make_event("Care API office hours", starts_in=timedelta(days=1))

        assert event.slug == "care-api-office-hours"

    def test_a_repeated_title_gets_a_numbered_slug(self):
        slugs = [
            make_event("Care API office hours", starts_in=timedelta(days=1)).slug
            for _ in range(3)
        ]

        assert slugs == [
            "care-api-office-hours",
            "care-api-office-hours-2",
            "care-api-office-hours-3",
        ]


class TestEndsAfterItStarts:
    def test_an_end_before_the_start_is_rejected(self):
        starts_at = timezone.now() + timedelta(days=1)

        with pytest.raises(IntegrityError), transaction.atomic():
            Event.objects.create(
                title="Backwards",
                starts_at=starts_at,
                ends_at=starts_at - timedelta(hours=1),
            )

    def test_an_end_equal_to_the_start_is_rejected(self):
        starts_at = timezone.now() + timedelta(days=1)

        with pytest.raises(IntegrityError), transaction.atomic():
            Event.objects.create(
                title="Zero length",
                starts_at=starts_at,
                ends_at=starts_at,
            )

    def test_an_open_ended_event_is_allowed(self):
        event = make_event("No end time", starts_in=timedelta(days=1), duration=None)

        assert event.ends_at is None
