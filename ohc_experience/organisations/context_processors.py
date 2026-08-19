from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from .selectors import get_membership_for

if TYPE_CHECKING:
    from django.http import HttpRequest


def current_organisation(request: HttpRequest) -> dict[str, Any]:
    """Expose the signed-in user's organisation and role to every template.

    The app shell (sidebar footer, org name in the header) needs these on every
    page, so resolving them once here beats threading them through each view.
    """
    membership = get_membership_for(getattr(request, "user", None))
    return {
        "current_membership": membership,
        "current_organisation": membership.organisation if membership else None,
    }
