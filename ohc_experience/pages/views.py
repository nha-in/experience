from __future__ import annotations

from typing import TYPE_CHECKING

from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from ohc_experience.events.selectors import dashboard_events
from ohc_experience.organisations.selectors import get_membership_for
from ohc_experience.organisations.views import OrganisationMixin
from ohc_experience.users.permissions import is_ohc_team

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse

# Setup checklist rows on the dashboard. Each is (label, url name, done-flag key).
SETUP_STEPS = [
    (_("Complete your company profile"), "organisations:detail", "profile_complete"),
    (_("Sign in to your sandbox facility"), None, "sandbox_ready"),
    (_("Invite your team"), "organisations:team", "team_invited"),
    (_("Finish Care Basic certification"), None, "certified"),
]


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


class DashboardView(OrganisationMixin, TemplateView):
    """Screen 1c — status at a glance plus what to do next.

    The sandbox, certification, ticket and deployment tiles read as zero/pending
    until those subsystems land; the shape is here so adding them is a data
    change rather than a layout change.
    """

    template_name = "dashboard/dashboard.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not self.organisation.is_onboarded:
            return redirect("organisations:onboarding")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        organisation = self.organisation
        team_size = organisation.memberships.count()
        pending_invites = organisation.invitations.pending().count()

        completed = {
            "profile_complete": organisation.is_onboarded,
            "sandbox_ready": False,
            "team_invited": team_size > 1 or pending_invites > 0,
            "certified": False,
        }
        steps = [
            {
                "label": label,
                "url_name": url_name,
                "done": completed[key],
            }
            for label, url_name, key in SETUP_STEPS
        ]
        done_count = sum(1 for step in steps if step["done"])

        context.update(
            {
                "nav_section": "dashboard",
                "team_size": team_size,
                "pending_invites": pending_invites,
                "setup_steps": steps,
                "setup_done": done_count,
                "setup_total": len(steps),
                "setup_percent": round(done_count / len(steps) * 100),
                "recent_members": organisation.memberships.select_related(
                    "user",
                ).order_by(
                    "-joined_at",
                )[:5],
                # Events are published to every vendor, so this is not scoped
                # to the organisation — see events.selectors.
                "upcoming_events": dashboard_events(),
            },
        )
        return context


def resolve_post_login_destination(user) -> str:
    """Where a freshly signed-in user belongs.

    No organisation is an odd state (it only happens if a membership was deleted
    out from under them), so send them to the landing page rather than a 403.
    """
    # OHC team have no organisation; their home is the support console.
    if is_ohc_team(user):
        return "ohc:queue"
    membership = get_membership_for(user)
    if membership is None:
        return "home"
    if not membership.organisation.is_onboarded:
        return "organisations:onboarding"
    return "dashboard"
