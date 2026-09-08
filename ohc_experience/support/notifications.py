"""Ticket emails: every reply reaches the other side of the conversation."""

from __future__ import annotations

from django.core.mail import send_mail
from django.template.loader import render_to_string

from ohc_experience.abdm.notifications import absolute_url
from ohc_experience.abdm.notifications import integrator_recipients
from ohc_experience.abdm.notifications import reviewer_recipients


def _recipients(message) -> list[str]:
    ticket = message.ticket
    if message.from_ohc_team:
        emails = set(integrator_recipients(ticket.organisation))
        if ticket.created_by and ticket.created_by.email:
            emails.add(ticket.created_by.email)
        return sorted(emails)
    if ticket.assignee and ticket.assignee.email:
        return [ticket.assignee.email]
    return reviewer_recipients()


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
    send_mail(subject, body, None, recipients, fail_silently=False)
