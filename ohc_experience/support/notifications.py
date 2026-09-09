"""Ticket emails: every reply reaches the other side of the conversation."""

from __future__ import annotations

from django.template.loader import render_to_string

from ohc_experience.core.mail import absolute_url
from ohc_experience.core.mail import deliver
from ohc_experience.core.mail import team_recipients
from ohc_experience.organisations.selectors import notification_recipients


def _recipients(message) -> list[str]:
    ticket = message.ticket
    if message.from_ohc_team:
        emails = set(notification_recipients(ticket.organisation))
        if ticket.created_by and ticket.created_by.email:
            emails.add(ticket.created_by.email)
        return sorted(emails)
    if ticket.assignee and ticket.assignee.email:
        return [ticket.assignee.email]
    return team_recipients()


def notify_reply(message) -> None:
    recipients = _recipients(message)
    if not recipients:
        return
    ticket = message.ticket
    path = (
        f"/support/{ticket.reference}/"
        if message.from_ohc_team
        else f"/ohc/tickets/{ticket.reference}/"
    )
    context = {
        "ticket": ticket,
        "message": message,
        "ticket_url": absolute_url(path),
    }
    subject = render_to_string("support/email/reply_subject.txt", context).strip()
    body = render_to_string("support/email/reply_body.txt", context)
    deliver(subject, body, recipients)
