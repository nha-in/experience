from __future__ import annotations

from typing import TYPE_CHECKING

from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView

from ohc_experience.abdm.context_processors import SESSION_PRODUCT_KEY
from ohc_experience.abdm.selectors import products_for
from ohc_experience.organisations.selectors import get_membership_for
from ohc_experience.organisations.views import OrganisationMixin
from ohc_experience.users.permissions import is_ohc_team

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse


class LandingView(TemplateView):
    """The signed-out front door."""

    template_name = "pages/home.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if request.user.is_authenticated:
            destination = resolve_post_login_destination(request.user)
            # A signed-in user with no organisation has nowhere to be sent —
            # show them the landing page rather than bouncing them in a loop.
            if destination != "home":
                return redirect(destination)
        return super().get(request, *args, **kwargs)


class DashboardView(OrganisationMixin, View):
    """The integrator's home is a product's overview; this hop finds it.

    Kept as `dashboard` because every shell link and post-login hop points
    here: the organisation still onboarding goes to the step it is on, and
    everyone else lands on the product they last opened.
    """

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        organisation = self.organisation
        if not organisation.has_submitted_details:
            return redirect("organisations:onboarding")
        products = list(products_for(organisation))
        if not products:
            return redirect("products:onboarding-product")
        wanted = request.session.get(SESSION_PRODUCT_KEY)
        product = next((item for item in products if item.sandbox_id == wanted), None)
        return redirect((product or products[0]).get_absolute_url())


def resolve_post_login_destination(user) -> str:
    """Where a freshly signed-in user belongs, as a URL name.

    Integrators go to the onboarding step they are on, or to `dashboard`, which
    sends them on to their product. OHC staff usually have no organisation —
    the console is their home. Staff who also belong to an organisation keep
    the integrator route; the console is one click away in the sidebar.
    """
    membership = get_membership_for(user)
    if membership is None:
        return "assess:dashboard" if is_ohc_team(user) else "home"
    organisation = membership.organisation
    if not organisation.has_submitted_details:
        return "organisations:onboarding"
    if not products_for(organisation).exists():
        return "products:onboarding-product"
    return "dashboard"
