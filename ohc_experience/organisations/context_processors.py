from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.utils.translation import gettext_lazy as _

from .selectors import get_membership_for

if TYPE_CHECKING:
    from django.http import HttpRequest

# Sidebar sections the hub design draws but this release does not ship yet.
# They render disabled with a "Soon" chip so the nav has its final shape from
# day one rather than growing a link at a time.
SOON_SECTION_LABELS = [
    _("Sandbox"),
    _("Certifications"),
    _("Deployments"),
    _("Events"),
    _("Support"),
]


def current_organisation(request: HttpRequest) -> dict[str, Any]:
    """Expose the signed-in user's organisation and role to every template.

    The app shell (sidebar footer, org name in the header) needs these on every
    page, so resolving them once here beats threading them through each view.
    """
    membership = get_membership_for(getattr(request, "user", None))
    return {
        "current_membership": membership,
        "current_organisation": membership.organisation if membership else None,
        "soon_section_labels": SOON_SECTION_LABELS,
    }
