from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from ohc_experience.core.mail import GLOBAL_EMAIL_BACKEND
from ohc_experience.core.mail import apply_gateway_template
from ohc_experience.core.mail import get_delivery_backend
from ohc_experience.experiences.registry import get_program

if TYPE_CHECKING:
    from .models import Ticket
    from .models import TicketMessage

TEMPLATE_KEY = "support_ticket"


def _domain() -> str:
    return settings.SUPPORT_EMAIL_DOMAIN


def _inbox() -> str:
    return settings.SUPPORT_INBOX_EMAIL


def thread_anchor_id(ticket: Ticket) -> str:
    return f"<{ticket.reference}@{_domain()}>"


def _ticket_url(ticket: Ticket) -> str:
    """Absolute link to the ticket for the email."""
    return f"{settings.SITE_BASE_URL.rstrip('/')}{ticket.get_absolute_url()}"


def _participants(ticket: Ticket) -> list[str]:
    """Everyone who has taken part in the thread, requester included.

    Copying them keeps the OHC member who answered on the conversation once the
    vendor replies, instead of leaving them to spot it in the shared inbox.
    """
    addresses = set(
        ticket.messages.exclude(author=None).values_list("author__email", flat=True),
    )
    if ticket.created_by:
        addresses.add(ticket.created_by.email)
    return sorted(address for address in addresses if address)


def notify_support(ticket: Ticket, message: TicketMessage) -> None:
    """Send one ticket entry to the support inbox as part of the mail thread."""
    anchor = thread_anchor_id(ticket)
    is_new_ticket = not ticket.messages.exclude(pk=message.pk).exists()
    context = {
        "ticket": ticket,
        "message": message,
        "is_new_ticket": is_new_ticket,
        "ticket_url": _ticket_url(ticket),
        "program": get_program(),
        "experience_context": getattr(ticket, "experience_context", None),
    }
    subject = render_to_string("support/email/ticket_subject.txt", context).strip()
    body = render_to_string("support/email/ticket_body.txt", context)

    headers = {"X-OHC-Ticket": ticket.reference}
    if is_new_ticket:
        # The opening email owns the anchor id; later entries reply to it.
        headers["Message-ID"] = anchor
    else:
        headers["Message-ID"] = f"<{ticket.reference}-{message.pk}@{_domain()}>"
        headers["In-Reply-To"] = anchor
        headers["References"] = anchor

    # This is the only mail a ticket sends: support is the recipient and everyone
    # on the thread is copied, rather than each being mailed separately.
    email = EmailMessage(
        subject=subject,
        body=body,
        to=[_inbox()],
        cc=_participants(ticket) or None,
    )
    if get_delivery_backend() == GLOBAL_EMAIL_BACKEND:
        # The gateway documents no header field and rejects any that are set, so
        # the thread is grouped by subject there rather than by Message-ID.
        apply_gateway_template(email, TEMPLATE_KEY)
    else:
        email.extra_headers = headers
    email.send(fail_silently=False)
