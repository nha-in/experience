from __future__ import annotations

from typing import TYPE_CHECKING

from django.shortcuts import redirect
from django.views.generic import TemplateView

from ohc_experience.organisations.selectors import get_membership_for
from ohc_experience.users.permissions import is_ohc_team

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse


class LandingView(TemplateView):
    """Screen 1a's marketing half — the signed-out front door."""

    template_name = "pages/home.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.user.is_authenticated:
            destination = resolve_post_login_destination(request.user)
            # A signed-in user with no organisation has nowhere to be sent —
            # show them the marketing page rather than bouncing them in a loop.
            if destination != "home":
                return redirect(destination)
        return super().get(request, *args, **kwargs)


def resolve_post_login_destination(user) -> str:
    """Where a freshly signed-in user belongs.

    Most people have exactly one vendor organisation. OHC staff usually have
    none — the console is their home, so send them there rather than to a
    dashboard that would 403 or a landing page that tells them nothing. Staff
    who also belong to a vendor keep the vendor route; the console is one click
    away in the sidebar.
    """
    membership = get_membership_for(user)
    if membership is None:
        return (
            "sandbox:assess-dashboard"
            if is_ohc_team(user) or user.is_superuser
            else "home"
        )
    if not membership.organisation.is_onboarded:
        return "sandbox:organisation"
    return "sandbox:home"
