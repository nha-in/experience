"""
Kept out of the general notification outbox because an approval or rejection is
addressed to the applicant who submitted, not to the whole organisation.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from ohc_experience.core.mail import GLOBAL_EMAIL_BACKEND
from ohc_experience.core.mail import apply_gateway_template
from ohc_experience.core.mail import get_delivery_backend

from .models import ReviewItem

TEMPLATE_KEY = "review_decision"
NOTICE_TEMPLATE_KEY = "review_notice"

EVENT_LABELS = {
    "received": _("Submitted for review"),
    "withdrawn": _("Withdrawn by the integrator"),
    "assigned": _("Assigned to you"),
    "recorded": _("Recorded without a decision"),
    "query_raised": _("Query raised"),
    "query_answered": _("Query answered"),
    "rejected": _("Rejected, changes needed"),
}

KIND_LABELS = {
    ReviewItem.Kind.ORGANISATION: _("Organisation Review"),
    ReviewItem.Kind.PRODUCT: _("Product Registration"),
    ReviewItem.Kind.APPLICATION: _("Application Review"),
}


def _portal_url(item: ReviewItem) -> str:
    """The redirect view sends the NHA team and the integrator to different pages."""
    path = reverse("experiences:review-open", args=[item.pk])
    return f"{settings.SITE_BASE_URL.rstrip('/')}{path}"


def _is_registration(item: ReviewItem, event: str) -> bool:
    """A new product's record opens its thread, so it introduces the product."""
    return (
        event == "recorded"
        and item.kind == ReviewItem.Kind.PRODUCT
        and not item.resubmission_count
    )


def thread_anchor_id(item: ReviewItem) -> str:
    """One mail thread per product per review subject, so replies group together."""
    product = item.product if item.product_id else None
    workspace = getattr(product, "workspace", None)
    scope = getattr(workspace, "reference", None) or item.organisation_id
    return f"<{scope}.{item.reference}@{settings.SUPPORT_EMAIL_DOMAIN}>"


def _applicant(item: ReviewItem):
    submission = item.selected_submission
    return submission.submitted_by if submission else None


def _copies(*people) -> list[str]:
    return sorted({person.email for person in people if person and person.email})


def _send(item: ReviewItem, subject: str, body: str, cc: list[str], key: str) -> None:
    email = EmailMessage(
        subject=subject,
        body=body,
        to=[settings.SUPPORT_INBOX_EMAIL],
        cc=cc or None,
    )
    if get_delivery_backend() == GLOBAL_EMAIL_BACKEND:
        apply_gateway_template(email, key)
    else:
        anchor = thread_anchor_id(item)
        email.extra_headers = {
            "X-ABDM-Review": item.reference,
            "In-Reply-To": anchor,
            "References": anchor,
        }
    email.send(fail_silently=False)


def notify_review(
    item: ReviewItem,
    event: str,
    *,
    note: str = "",
    reason: str = "",
) -> None:
    """One running thread per review: stable subject, the body reports the change.

    Support is the recipient; the applicant and the assigned decision maker are
    copied so an assignment or status update reaches them without a new subject.
    """
    context = {
        "item": item,
        "label": EVENT_LABELS[event],
        "kind": KIND_LABELS[item.kind],
        "applicant": _applicant(item),
        "registered": _is_registration(item, event),
        "reason": reason,
        "note": note.strip(),
        "url": _portal_url(item),
    }
    subject = render_to_string(
        "experiences/email/review_notice_subject.txt",
        context,
    ).strip()
    _send(
        item,
        subject,
        render_to_string("experiences/email/review_notice_body.txt", context),
        _copies(_applicant(item), item.assignee),
        NOTICE_TEMPLATE_KEY,
    )


def notify_decision(item: ReviewItem, note: str = "") -> None:
    """The decision leaves the thread: its own mail to support, applicant, reviewer."""
    context = {
        "item": item,
        "note": note.strip(),
        "applicant": _applicant(item),
        "ticket_url": _portal_url(item),
    }
    _send(
        item,
        render_to_string("experiences/email/decision_subject.txt", context).strip(),
        render_to_string("experiences/email/decision_body.txt", context),
        _copies(_applicant(item), item.assignee, item.decided_by),
        TEMPLATE_KEY,
    )
