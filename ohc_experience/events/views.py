"""Integrator-facing events — the list every organisation sees, and one event.

Events are authored by the NHA team and published to everybody, so there is no
organisation scoping here; the only gate is being signed in. What an
integrator may see is decided in selectors.py, which never leaves the
published queryset. Registration is the one thing a person changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView
from django.views.generic import TemplateView

from .models import Event
from .models import EventRegistration
from .notifications import notify_registration
from .selectors import filter_events
from .selectors import get_published_event
from .selectors import past_events
from .selectors import registered_event_ids
from .selectors import upcoming_events

if TYPE_CHECKING:
    from typing import Any

    from django.http import HttpRequest
    from django.http import HttpResponse

CHIPS = [
    ("", _("All")),
    ("registered", _("Registered")),
    *Event.Kind.choices,
]


class EventsNavMixin:
    """Light the Events item in the sidebar for every page in this app."""

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["nav_section"] = "events"
        return context


class EventListView(LoginRequiredMixin, EventsNavMixin, TemplateView):
    """What is coming up, filtered by chip, with everything already run folded away."""

    template_name = "events/event_list.html"

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        chip = self.request.GET.get("chip", "")
        if chip not in dict(CHIPS):
            chip = ""
        user = self.request.user
        registered_ids = registered_event_ids(user)
        context.update(
            {
                "upcoming_events": filter_events(upcoming_events(), chip, user),
                "past_events": filter_events(past_events(), chip, user),
                "registered_ids": registered_ids,
                # The rail: what this person is signed up for, soonest first.
                "my_registrations": upcoming_events().filter(pk__in=registered_ids),
                "upcoming_count": upcoming_events().count(),
                "chips": CHIPS,
                "chip": chip,
            },
        )
        return context


class EventDetailView(LoginRequiredMixin, EventsNavMixin, DetailView):
    """One event in full. Drafts 404 rather than 403 — see get_published_event."""

    model = Event
    template_name = "events/event_detail.html"
    context_object_name = "event"

    def get_object(self, queryset=None) -> Event:
        return get_published_event(self.kwargs["slug"])

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["is_registered"] = self.object.is_registered(self.request.user)
        return context


class RegistrationView(LoginRequiredMixin, View):
    """Register for, or withdraw from, a published event.

    Answers htmx with the registration control, and everyone else with
    POST → redirect → flash back to the event.
    """

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        event = get_published_event(kwargs["slug"])
        if event.is_past:
            messages.error(request, _("This event has already finished."))
        elif kwargs["action"] == "register":
            registration, created = EventRegistration.objects.get_or_create(
                event=event,
                user=request.user,
            )
            if created:
                notify_registration(registration)
                messages.success(
                    request,
                    _("You are registered for %(title)s. A confirmation is on its way.")
                    % {"title": event.title},
                )
            else:
                messages.info(request, _("You were already registered."))
        else:
            deleted, _rows = EventRegistration.objects.filter(
                event=event,
                user=request.user,
            ).delete()
            if deleted:
                messages.success(request, _("Registration withdrawn."))
            else:
                messages.info(request, _("You were not registered."))
        if request.htmx:
            return render(
                request,
                "events/partials/registration_control_swap.html",
                {"event": event, "is_registered": event.is_registered(request.user)},
            )
        return redirect(event)
