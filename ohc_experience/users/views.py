from __future__ import annotations

from typing import TYPE_CHECKING

from allauth.account import views as account_views
from allauth.account.stages import LoginStageController
from allauth.account.stages import PhoneVerificationStage
from allauth.account.views import SignupView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import RedirectView
from django.views.generic import UpdateView

from ohc_experience.organisations.models import Invitation
from ohc_experience.organisations.views import INVITATION_SESSION_KEY
from ohc_experience.pages.views import resolve_post_login_destination
from ohc_experience.users.forms import UserProfileForm
from ohc_experience.users.models import User

if TYPE_CHECKING:
    from django.db.models import QuerySet


class UserSignupView(SignupView):
    """Signup, aware of a pending invite.

    Arriving through an invite link parks the token in the session; this view
    hands it to the form so the new account joins the inviting organisation
    instead of creating one, and so the Organisation field disappears.
    """

    template_name = "account/signup.html"

    def get_invitation(self) -> Invitation | None:
        token = self.request.session.get(INVITATION_SESSION_KEY)
        if not token:
            return None
        invitation = (
            Invitation.objects.filter(token=token)
            .select_related("organisation")
            .first()
        )
        if invitation is None or not invitation.is_pending:
            self.request.session.pop(INVITATION_SESSION_KEY, None)
            return None
        return invitation

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["invitation"] = self.get_invitation()
        kwargs["request"] = self.request
        return kwargs

    def get_initial(self) -> dict:
        initial = super().get_initial()
        invitation = self.get_invitation()
        if invitation is not None:
            initial.setdefault("email", invitation.email)
        return initial

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["invitation"] = self.get_invitation()
        context.update(
            page_title="Create account",
            onboarding=True,
            onboarding_step="account",
        )
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.session.pop(INVITATION_SESSION_KEY, None)
        return response


class UserProfileView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    """The signed-in user's own account settings."""

    model = User
    form_class = UserProfileForm
    template_name = "users/profile.html"
    # The page includes this fragment; htmx swaps the same file back in.
    partial_template_name = "users/partials/profile_form.html"
    success_message = _("Your details were updated.")
    success_url = reverse_lazy("users:profile")

    def get_object(self, queryset: QuerySet | None = None) -> User:
        return self.request.user

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["nav_section"] = "settings"
        context["settings_section"] = "profile"
        return context

    def form_valid(self, form):
        # SuccessMessageMixin saves and queues the flash; only the response
        # shape changes for htmx.
        response = super().form_valid(form)
        if self.request.htmx:
            # Swap the saved form back in — rebound to the stored instance —
            # and let the flash ride along out of band into #flash-messages.
            return render(
                self.request,
                self.partial_template_name,
                self.get_context_data(
                    form=self.get_form_class()(instance=self.object),
                    oob_flash=True,
                ),
            )
        return response

    def form_invalid(self, form):
        # 200 with the re-rendered fragment, so htmx swaps the errors in; the
        # no-JS path still gets the whole page back, also with a 200.
        if self.request.htmx:
            return render(
                self.request,
                self.partial_template_name,
                self.get_context_data(form=form),
            )
        return super().form_invalid(form)


class UserRedirectView(LoginRequiredMixin, RedirectView):
    """Post-login landing: onboarding while it is unfinished, else the dashboard."""

    permanent = False

    def get_redirect_url(self, *args, **kwargs) -> str:
        return reverse(resolve_post_login_destination(self.request.user))


def _in_settings_shell(response, request, template_name):
    """Signed in, a verification code belongs in the app shell: the entrance
    shell it uses during signup reads as a signed-out landing page."""
    if request.user.is_authenticated and hasattr(response, "template_name"):
        response.template_name = template_name
    return response


def verify_phone_view(request):
    return _in_settings_shell(
        account_views.verify_phone(request),
        request,
        "account/confirm_phone_verification_code_settings.html",
    )


def email_verification_sent_view(request):
    return _in_settings_shell(
        account_views.email_verification_sent(request),
        request,
        "account/confirm_email_verification_code_settings.html",
    )


@require_POST
def skip_phone_verification_view(request):
    """Finish signing up without the mobile number, which stays unsaved."""
    stage = LoginStageController.enter(request, PhoneVerificationStage.key)
    if stage is None:
        return redirect("account_login")
    return stage.exit()


user_signup_view = UserSignupView.as_view()
user_profile_view = UserProfileView.as_view()
user_redirect_view = UserRedirectView.as_view()
