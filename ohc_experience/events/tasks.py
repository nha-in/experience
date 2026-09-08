"""Scheduled work for events: the day-before reminder (hourly beat, see settings)."""

from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import EventRegistration
from .notifications import notify_reminder

REMINDER_WINDOW = timedelta(hours=24)


@shared_task
def send_event_reminders() -> int:
    """Remind everyone registered for an event that starts within 24 hours. Once."""
    now = timezone.now()
    due = EventRegistration.objects.filter(
        reminded_at__isnull=True,
        event__published_at__isnull=False,
        event__starts_at__gt=now,
        event__starts_at__lte=now + REMINDER_WINDOW,
    ).select_related("event", "user")
    count = 0
    for registration in due:
        notify_reminder(registration)
        registration.reminded_at = now
        registration.save(update_fields=["reminded_at"])
        count += 1
    return count
