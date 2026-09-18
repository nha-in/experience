from __future__ import annotations

import time
from datetime import UTC
from datetime import datetime
from typing import TYPE_CHECKING

from allauth.account import app_settings as allauth_settings
from allauth.account import views as account_views
from allauth.account.adapter import get_adapter
from allauth.account.internal.flows.email_verification_by_code import (
    EMAIL_VERIFICATION_CODE_SESSION_KEY,
)
from allauth.account.internal.flows.email_verification_by_code import (
    EmailVerificationProcess,
)
from allauth.account.internal.flows.phone_verification import (
    PhoneVerificationStageProcess,
)
from allauth.account.stages import LoginStageController
from allauth.account.utils import has_verified_email
from allauth.account.views import SignupView
from allauth.core.exceptions import RateLimited
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import RedirectView
from django.views.generic import TemplateView
from django.views.generic import UpdateView

from ohc_experience.organisations.models import Invitation
from ohc_experience.organisations.views import INVITATION_SESSION_KEY
from ohc_experience.pages.views import resolve_post_login_destination
from ohc_experience.users.forms import UserChangeEmailForm
from ohc_experience.users.forms import UserChangePhoneForm
from ohc_experience.users.forms import UserConfirmEmailVerificationCodeForm
from ohc_experience.users.forms import UserProfileForm
from ohc_experience.users.forms import UserVerifyPhoneForm
from ohc_experience.users.models import User
from ohc_experience.users.stages import VerificationStage

if TYPE_CHECKING:
    from django.db.models import QuerySet

# Form prefixes: both code boxes are on one page, so their fields need
# names of their own.
EMAIL = "email"
PHONE = "phone"
EMAIL_CHANGE = "email_change"
PHONE_CHANGE = "phone_change"


def _expired(state: dict, timeout: int) -> bool:
    return time.time() - state["at"] > timeout


def _code_form(form_class, prefix: str, data=None, **kwargs):
    return form_class(data, prefix=prefix, **kwargs)


def _resend_at(state: dict | None) -> datetime | None:
    """When a new code can be asked for, or None once it can be."""
    if not state:
        return None
    ready = state["at"] + settings.VERIFICATION_RESEND_AFTER_SECONDS
    if ready <= time.time():
        return None
    return datetime.fromtimestamp(ready, tz=UTC)


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


class AccountVerificationView(TemplateView):
    """Signup's one screen for the email and mobile codes.

    Each channel keeps its own allauth verification process, so either can be
    confirmed, corrected or sent again while the other is still outstanding. A
    channel that is done shows as verified, and the login resumes once nothing
    is left.
    """

    template_name = "account/verification.html"

    def dispatch(self, request, *args, **kwargs):
        self.stage = LoginStageController.enter(request, VerificationStage.key)
        if self.stage is None:
            return redirect("account_login")
        self.user = self.stage.login.user
        # An address and a number outlive their codes. Expired, the screen
        # offers a new one rather than dropping what is already confirmed, so
        # the email process is resumed only while its code is still good —
        # resuming an expired one ends the login.
        self.email_state = request.session.get(EMAIL_VERIFICATION_CODE_SESSION_KEY)
        self.email_process = (
            None if self._email_expired() else EmailVerificationProcess.resume(request)
        )
        self.phone_process = PhoneVerificationStageProcess.resume(self.stage)
        self.pending_phone = self.stage.state.get("phone", "")
        email_done = self._email_done()
        if email_done and self._phone_done():
            return self.stage.exit()
        if not email_done and self.email_state is None:
            return self.stage.abort()
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        handler = self._actions().get(request.POST.get("action", ""))
        if handler is None:
            return redirect(VerificationStage.urlname)
        return handler()

    def _actions(self) -> dict:
        """Only what the screen offers: nothing more for a channel that is
        done, or out of changes or resends."""
        actions = {}
        if not self._email_done():
            if self.email_process is None:
                actions["resend_email"] = self._restart_email
            else:
                actions["verify_email"] = self._verify_email
                if self.email_process.can_resend and not _resend_at(self.email_state):
                    actions["resend_email"] = self._resend_email
                if self.email_process.can_change:
                    actions["change_email"] = self._change_email
        if self._phone_done():
            return actions
        if self.phone_process is None:
            actions["resend_phone"] = self._restart_phone
            return actions
        actions["verify_phone"] = self._verify_phone
        if self.phone_process.can_resend and not _resend_at(self.stage.state):
            actions["resend_phone"] = self._resend_phone
        if self.phone_process.can_change:
            actions["change_phone"] = self._change_phone
        return actions

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        email_done = self._email_done()
        phone_done = self._phone_done()
        email_live = None if email_done else self.email_process
        phone_live = None if phone_done else self.phone_process
        email_resend_at = _resend_at(self.email_state)
        phone_resend_at = _resend_at(self.stage.state)
        context.update(
            email=self.user.email if email_done else self.email_state["email"],
            email_done=email_done,
            email_expired=not email_done and email_live is None,
            email_resend_at=email_resend_at if email_live else None,
            can_resend_email=not email_done
            and (email_live is None or email_live.can_resend),
            can_change_email=email_live is not None and email_live.can_change,
            phone_pending=bool(self.pending_phone),
            phone=self.pending_phone,
            phone_done=phone_done,
            phone_expired=bool(self.pending_phone)
            and not phone_done
            and not phone_live,
            phone_resend_at=phone_resend_at if phone_live else None,
            can_resend_phone=not phone_done
            and (phone_live is None or phone_live.can_resend),
            can_change_phone=phone_live is not None and phone_live.can_change,
        )
        context.setdefault(
            "email_form",
            _code_form(UserConfirmEmailVerificationCodeForm, EMAIL),
        )
        context.setdefault("phone_form", _code_form(UserVerifyPhoneForm, PHONE))
        # The box waiting for a code is the one to type in.
        waiting = context["email_form" if not email_done else "phone_form"]
        waiting.fields["code"].widget.attrs["autofocus"] = True
        context.setdefault(
            "email_change_form",
            UserChangeEmailForm(prefix=EMAIL_CHANGE),
        )
        context.setdefault(
            "phone_change_form",
            UserChangePhoneForm(prefix=PHONE_CHANGE, user=self.user),
        )
        return context

    def _email_done(self) -> bool:
        return has_verified_email(self.user)

    def _email_expired(self) -> bool:
        return self.email_state is not None and _expired(
            self.email_state,
            allauth_settings.EMAIL_VERIFICATION_BY_CODE_TIMEOUT,
        )

    def _phone_done(self) -> bool:
        return not self.pending_phone or self.user.phone_verified

    def _verify_email(self):
        form = _code_form(
            UserConfirmEmailVerificationCodeForm,
            EMAIL,
            self.request.POST,
            code=self.email_process.code,
            user=self.email_process.user,
            email=self.email_process.email,
        )
        if not form.is_valid():
            return self._wrong_code(self.email_process, email_form=form)
        self.email_process.finish()
        return self._resume()

    def _verify_phone(self):
        form = _code_form(
            UserVerifyPhoneForm,
            PHONE,
            self.request.POST,
            code=self.phone_process.code,
            phone=self.phone_process.phone,
            user=self.phone_process.user,
        )
        if not form.is_valid():
            return self._wrong_code(self.phone_process, phone_form=form)
        self.phone_process.finish()
        return self._resume()

    def _change_email(self):
        form = UserChangeEmailForm(
            self.request.POST,
            prefix=EMAIL_CHANGE,
            email=self.email_process.email,
        )
        if not form.is_valid():
            return self.render_to_response(
                self.get_context_data(email_change_form=form),
            )
        if self._send(
            lambda: self.email_process.change_to(
                form.cleaned_data["email"],
                form.account_already_exists,
            ),
        ):
            self._restart_clock(self.email_process)
        return redirect(VerificationStage.urlname)

    def _change_phone(self):
        form = UserChangePhoneForm(
            self.request.POST,
            prefix=PHONE_CHANGE,
            phone=self.phone_process.phone,
            user=self.user,
        )
        if not form.is_valid():
            return self.render_to_response(
                self.get_context_data(phone_change_form=form),
            )
        if self._send(
            lambda: self.phone_process.change_to(
                form.cleaned_data["phone"],
                form.account_already_exists,
            ),
        ):
            self._restart_clock(self.phone_process)
        return redirect(VerificationStage.urlname)

    def _resend_email(self):
        if self._send(self.email_process.resend):
            self._restart_clock(self.email_process)
        return redirect(VerificationStage.urlname)

    def _resend_phone(self):
        if self._send(self.phone_process.resend):
            self._restart_clock(self.phone_process)
        return redirect(VerificationStage.urlname)

    def _restart_email(self):
        """The address is still waiting, but its code has expired."""
        self._send(
            lambda: EmailVerificationProcess.initiate(
                request=self.request,
                user=self.user,
                email=self.email_state["email"],
            ),
        )
        return redirect(VerificationStage.urlname)

    def _restart_phone(self):
        """The number is still waiting, but its code has expired."""
        self._send(
            lambda: PhoneVerificationStageProcess.initiate(
                stage=self.stage,
                phone=self.pending_phone,
            ),
        )
        return redirect(VerificationStage.urlname)

    def _send(self, send) -> bool:
        """Too soon for another code is a message, not an error page."""
        adapter = get_adapter(self.request)
        try:
            send()
        except RateLimited:
            adapter.add_message(
                self.request,
                messages.ERROR,
                message=adapter.error_messages["rate_limited"],
            )
            return False
        return True

    def _restart_clock(self, process) -> None:
        """A new code starts its own window; allauth keeps the first one's."""
        process.state["at"] = time.time()
        process.persist()

    def _wrong_code(self, process, **forms):
        if process.record_invalid_attempt():
            return self.render_to_response(self.get_context_data(**forms))
        adapter = get_adapter(self.request)
        adapter.add_message(
            self.request,
            messages.ERROR,
            message=adapter.error_messages["too_many_login_attempts"],
        )
        return self.stage.abort()

    def _resume(self):
        if self._email_done() and self._phone_done():
            return self.stage.exit()
        return redirect(VerificationStage.urlname)


account_verification_view = AccountVerificationView.as_view()
user_signup_view = UserSignupView.as_view()
user_profile_view = UserProfileView.as_view()
user_redirect_view = UserRedirectView.as_view()
