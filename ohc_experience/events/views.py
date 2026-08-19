"""Vendor-facing events — the list every partner sees, and one event's page.

Events are authored by the OHC team and published to everybody, so there is no
organisation scoping here; the only gate is being signed in. What a vendor may
see is decided in selectors.py, which never leaves the published queryset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import DetailView
from django.views.generic import TemplateView

from .models import Event
from .selectors import get_published_event
from .selectors import past_events
from .selectors import upcoming_events

if TYPE_CHECKING:
    from typing import Any


class EventsNavMixin:
    """Light the Events item in the sidebar for every page in this app."""

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["nav_section"] = "events"
        return context


class EventListView(LoginRequiredMixin, EventsNavMixin, TemplateView):
    """What is coming up, with everything already run folded away beneath it."""

    template_name = "events/event_list.html"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["upcoming_events"] = upcoming_events()
        context["past_events"] = past_events()
        return context


class EventDetailView(LoginRequiredMixin, EventsNavMixin, DetailView):
    """One event in full. Drafts 404 rather than 403 — see get_published_event."""

    model = Event
    template_name = "events/event_detail.html"
    context_object_name = "event"

    def get_object(self, queryset=None) -> Event:
        return get_published_event(self.kwargs["slug"])
