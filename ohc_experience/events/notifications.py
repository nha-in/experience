"""Event emails: a confirmation on registration, a reminder the day before."""

from __future__ import annotations

from django.template.loader import render_to_string

from ohc_experience.core.mail import absolute_url
from ohc_experience.core.mail import deliver


def _send(stem: str, registration) -> None:
    if not registration.user.email:
        return
    context = {
        "event": registration.event,
        "user": registration.user,
        "event_url": absolute_url(registration.event.get_absolute_url()),
    }
    subject = render_to_string(f"events/email/{stem}_subject.txt", context).strip()
    body = render_to_string(f"events/email/{stem}_body.txt", context)
    deliver(subject, body, [registration.user.email])


def notify_registration(registration) -> None:
    _send("registration", registration)


def notify_reminder(registration) -> None:
    _send("reminder", registration)
