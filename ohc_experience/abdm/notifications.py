"""Every email the portal sends, one function per event in the design doc.

Integrator mail goes to the organisation's owner and admins (and its technical
contact, when one is on file). Reviewer mail goes to the item's assignee when
there is one, else to ``ABDM_REVIEW_INBOX``, else to every active reviewer.
Each function renders a subject/body pair from ``templates/abdm/email``.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from django.template.loader import render_to_string

from ohc_experience.organisations.models import MANAGER_ROLES


def absolute_url(path: str) -> str:
    domain = Site.objects.get_current().domain
    scheme = "http" if settings.DEBUG else "https"
    return f"{scheme}://{domain}{path}"


def integrator_recipients(organisation) -> list[str]:
    emails = list(
        organisation.memberships.filter(role__in=MANAGER_ROLES).values_list(
            "user__email",
            flat=True,
        ),
    )
    if organisation.technical_contact_email:
        emails.append(organisation.technical_contact_email)
    return sorted({email for email in emails if email})


def reviewer_recipients(item=None) -> list[str]:
    if item is not None and item.assignee_id and item.assignee.email:
        return [item.assignee.email]
    if settings.ABDM_REVIEW_INBOX:
        return [settings.ABDM_REVIEW_INBOX]
    return list(
        get_user_model()
        .objects.filter(is_ohc_team=True, is_active=True)
        .exclude(email="")
        .order_by("email")
        .values_list("email", flat=True),
    )


def _send(stem: str, context: dict, recipients: list[str]) -> None:
    if not recipients:
        return
    context = {**context, "portal_url": absolute_url("/")}
    subject = render_to_string(f"abdm/email/{stem}_subject.txt", context).strip()
    body = render_to_string(f"abdm/email/{stem}_body.txt", context)
    send_mail(subject, body, None, recipients, fail_silently=False)


def _item_url(item) -> str:
    return absolute_url(f"/assess/review/{item.reference}/")


def notify_item_submitted(item) -> None:
    """An organisation, product or exit request is waiting on the NHA queue."""
    _send(
        "item_submitted",
        {"item": item, "review_url": _item_url(item)},
        reviewer_recipients(item),
    )


def notify_item_approved(item) -> None:
    _send(
        "item_approved",
        {"item": item, "organisation": item.organisation},
        integrator_recipients(item.organisation),
    )


def notify_item_sent_back(item, reason: str) -> None:
    _send(
        "item_sent_back",
        {"item": item, "organisation": item.organisation, "reason": reason},
        integrator_recipients(item.organisation),
    )


def notify_query_raised(query) -> None:
    _send(
        "query_raised",
        {"query": query, "item": query.item, "organisation": query.item.organisation},
        integrator_recipients(query.item.organisation),
    )


def notify_query_answered(query) -> None:
    _send(
        "query_answered",
        {"query": query, "item": query.item, "review_url": _item_url(query.item)},
        reviewer_recipients(query.item),
    )


def notify_milestones_added(product, keys: list[str]) -> None:
    _send(
        "milestones_added",
        {"product": product, "keys": keys},
        reviewer_recipients(),
    )


def notify_credentials_issued(product) -> None:
    _send(
        "credentials_issued",
        {"product": product, "organisation": product.organisation},
        integrator_recipients(product.organisation),
    )


def notify_callback_failing(product) -> None:
    _send(
        "callback_failing",
        {"product": product, "credential": product.credential},
        integrator_recipients(product.organisation),
    )
