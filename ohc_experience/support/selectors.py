"""Reads for the support screens.

Queries live here rather than in the views so the vendor inbox and the OHC
console can ask the same questions of the same data. Every ticket read starts
from `Ticket.objects.for_organisation(...)`: scoping is a property of the
selector, not something each view is trusted to remember.
"""

from __future__ import annotations

import statistics
from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ohc_experience.organisations.models import initials_for

from .models import Category
from .models import Priority
from .models import Status
from .models import Ticket

if TYPE_CHECKING:
    from ohc_experience.organisations.models import Organisation

    from .models import TicketMessage
    from .models import TicketQuerySet

# The window the inbox reports its median first response over.
RESPONSE_WINDOW_DAYS = 30

# Below this many answered tickets a median is noise — one slow Friday would
# swing it — so the inbox says it has nothing to report rather than printing a
# number that reads like a service level.
MEDIAN_MINIMUM_SAMPLE = 3

SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60


def tickets_for(organisation: Organisation) -> TicketQuerySet:
    """Every ticket this vendor may see, ready to render."""
    return Ticket.objects.for_organisation(organisation).with_related()


def filter_tickets(
    queryset: TicketQuerySet,
    *,
    status: str = "",
    category: str = "",
    priority: str = "",
) -> TicketQuerySet:
    """Narrow a ticket queryset by the inbox's three pickers.

    Anything that is not a real choice is ignored rather than raising: these
    values arrive in a querystring a person can type, and an unknown one means
    "no filter", not "error page".
    """
    if status in Status.values:
        queryset = queryset.filter(status=status)
    if category in Category.values:
        queryset = queryset.filter(category=category)
    if priority in Priority.values:
        queryset = queryset.filter(priority=priority)
    return queryset


def median_first_response(
    organisation: Organisation,
    *,
    days: int = RESPONSE_WINDOW_DAYS,
) -> timedelta | None:
    """Median time to the Care team's first reply, or None if too few to say.

    Median rather than mean because one ticket opened over a long weekend would
    drag an average past anything a vendor actually experienced.
    """
    since = timezone.now() - timedelta(days=days)
    waits = [
        responded - created
        for created, responded in Ticket.objects.for_organisation(organisation)
        .filter(created_at__gte=since, first_responded_at__isnull=False)
        .values_list("created_at", "first_responded_at")
    ]
    if len(waits) < MEDIAN_MINIMUM_SAMPLE:
        return None
    return statistics.median(waits)


def format_response_time(delta: timedelta) -> str:
    """A wait as the inbox prints it: "2 h 14 m"."""
    total_minutes = int(delta.total_seconds() // SECONDS_PER_MINUTE)
    hours, minutes = divmod(total_minutes, MINUTES_PER_HOUR)
    if hours and minutes:
        return _("%(hours)s h %(minutes)s m") % {"hours": hours, "minutes": minutes}
    if hours:
        return _("%(hours)s h") % {"hours": hours}
    if minutes:
        return _("%(minutes)s m") % {"minutes": minutes}
    return str(_("under a minute"))


def thread_messages(ticket: Ticket) -> list[TicketMessage]:
    """The conversation, oldest first, each entry ready for its avatar."""
    return [
        with_avatar(message)
        for message in ticket.messages.select_related("author").all()
    ]


def with_avatar(message: TicketMessage) -> TicketMessage:
    """Hang the avatar initials off a message.

    Computed here rather than on the model because the model layer is fixed and
    an author is a plain User with no avatar of its own. Both the whole thread
    and a single message swapped in by htmx come through this function, so a
    freshly posted reply draws exactly like a reloaded one.
    """
    message.initials = initials_for(message.author_label)
    return message
