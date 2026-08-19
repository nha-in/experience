"""Who may act on what.

The vendor side is scoped by Membership (see organisations.selectors); this
module covers the platform side — the OHC team, who work across every vendor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse


def is_ohc_team(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_ohc_team", False),
    )


class OhcTeamRequiredMixin(AccessMixin):
    """Gate a view to the OHC team.

    Anonymous users are sent to the login page as usual; a signed-in vendor gets
    a 403 rather than a redirect, because bouncing them to a login form they are
    already past reads as a broken app.
    """

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not getattr(request.user, "is_authenticated", False):
            return self.handle_no_permission()
        if not is_ohc_team(request.user):
            msg = _("This area is for the OHC team.")
            raise PermissionDenied(msg)
        return super().dispatch(request, *args, **kwargs)
