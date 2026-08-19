"""Reads for the events app.

Everything a vendor sees goes through here, and every function below starts
from a published queryset. That is the whole point: a draft must be invisible
to a vendor even at a guessed slug, and keeping the published filter in one
place means a new view cannot forget it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.shortcuts import get_object_or_404

from .models import Event

if TYPE_CHECKING:
    from .models import EventQuerySet

# How many events the dashboard's "Upcoming events" card lists.
DASHBOARD_EVENT_LIMIT = 3


def upcoming_events() -> EventQuerySet:
    """Published events that have not finished, soonest first."""
    return Event.objects.upcoming()


def past_events() -> EventQuerySet:
    """Published events that are over, most recent first."""
    return Event.objects.past()


def dashboard_events(limit: int = DASHBOARD_EVENT_LIMIT) -> EventQuerySet:
    """The short list for the dashboard card."""
    return upcoming_events()[:limit]


def get_published_event(slug: str) -> Event:
    """One published event, or 404.

    The published filter is part of the lookup rather than a check afterwards,
    so an unpublished slug is indistinguishable from a slug that never existed.
    """
    return get_object_or_404(Event.objects.published(), slug=slug)
