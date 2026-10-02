from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Exists
from django.db.models import OuterRef
from django.shortcuts import redirect
from django.views.generic import TemplateView

from ohc_experience.experiences.models import Milestone
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.selectors import get_membership_for
from ohc_experience.users.permissions import is_nha_team

from .abdm_dashboard import current_figures

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        figures = current_figures() or {}
        context["abdm_figures"] = {
            name: indian_grouping(count) for name, count in figures.items()
        }
        context["successful_integrators"] = indian_grouping(
            successful_integrator_count(),
        )
        return context


def successful_integrator_count() -> int:
    """Organisations with at least one approved milestone."""
    approved = Milestone.objects.filter(
        product__organisation=OuterRef("pk"),
        enabled=True,
        application__status="approved",
    )
    return Organisation.objects.filter(Exists(approved)).count()


def indian_grouping(number: int) -> str:
    """Group digits as India writes them: 1224714978 is "1,22,47,14,978"."""
    digits = str(number)
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([*groups, tail])


def resolve_post_login_destination(user) -> str:
    """Where a freshly signed-in user belongs.

    Most people have exactly one integrator organisation. OHC staff usually
    have none — the console is their home, so send them there rather than to a
    dashboard that would 403 or a landing page that tells them nothing. Staff
    who also belong to an integrator keep the integrator route; the console is
    one click away in the sidebar.
    """
    membership = get_membership_for(user)
    if membership is None:
        return "experiences:home" if is_nha_team(user) or user.is_superuser else "home"
    if not membership.organisation.is_onboarded:
        return "experiences:organisation"
    return "experiences:home"
