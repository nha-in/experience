from __future__ import annotations

from .models import MANAGER_ROLES
from .models import Membership


def get_membership_for(user) -> Membership | None:
    """The user's single membership, or None.

    One organisation per user today (the signup form creates it), so this
    returns the first membership rather than asking callers to pick one. The
    Membership model already supports many members per organisation, so adding
    an organisation switcher later only changes this function's contract.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return (
        Membership.objects.filter(user=user)
        .select_related("organisation", "user")
        .first()
    )


def get_organisation_for(user):
    membership = get_membership_for(user)
    return membership.organisation if membership else None


def notification_recipients(organisation) -> list[str]:
    """Who hears about the organisation's affairs: its owner and admins, plus
    the technical contact when one is on file."""
    emails = list(
        organisation.memberships.filter(role__in=MANAGER_ROLES).values_list(
            "user__email",
            flat=True,
        ),
    )
    if organisation.technical_contact_email:
        emails.append(organisation.technical_contact_email)
    return sorted({email for email in emails if email})
