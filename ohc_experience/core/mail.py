"""Mail delivery shared by every app that sends a notification.

Every email in the portal is a courtesy copy of something already recorded
on a screen, so delivery never gets to undo the write that prompted it:
``deliver`` logs a failed send and carries on. ``team_recipients`` answers
"who hears about work for the review team": the shared inbox when the
deployment configures one, otherwise every active team member.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def deliver(subject: str, body: str, recipients: list[str]) -> None:
    """Send one email, and never let a mail outage undo what prompted it."""
    try:
        send_mail(subject, body, None, recipients, fail_silently=False)
    except Exception:
        logger.exception("Could not send %r to %s", subject, recipients)


def absolute_url(path: str) -> str:
    domain = Site.objects.get_current().domain
    scheme = "http" if settings.DEBUG else "https"
    return f"{scheme}://{domain}{path}"


def team_recipients() -> list[str]:
    if settings.ABDM_REVIEW_INBOX:
        return [settings.ABDM_REVIEW_INBOX]
    return list(
        get_user_model()
        .objects.filter(is_ohc_team=True, is_active=True)
        .exclude(email="")
        .order_by("email")
        .values_list("email", flat=True),
    )
