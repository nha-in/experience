"""Event emails: a confirmation on registration, a reminder the day before."""

from __future__ import annotations

from django.core.mail import send_mail
from django.template.loader import render_to_string

from ohc_experience.abdm.notifications import absolute_url


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
    send_mail(subject, body, None, [registration.user.email], fail_silently=False)


def notify_registration(registration) -> None:
    _send("registration", registration)


def notify_reminder(registration) -> None:
    _send("reminder", registration)
