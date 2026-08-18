from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView
from django.views.generic import UpdateView

from .forms import InvitationForm
from .forms import MembershipRoleForm
from .forms import OrganisationProfileForm
from .models import Invitation
from .models import Membership
from .models import Organisation
from .models import Role
from .selectors import get_membership_for

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse

# Where an invite token waits while an invited person signs up or signs in.
INVITATION_SESSION_KEY = "pending_invitation_token"


class OrganisationMixin(LoginRequiredMixin):
    """Resolves the signed-in user's organisation, 403-ing if they have none.

    `require_manage` gates the whole view on the owner/admin roles; views that
    only gate their POST check `self.membership.can_manage` themselves.
    """

    require_manage = False

    def dispatch(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        self.membership = get_membership_for(request.user)
        if self.membership is None:
            msg = _("You are not a member of any organisation.")
            raise PermissionDenied(msg)
        self.organisation = self.membership.organisation
        if self.require_manage and not self.membership.can_manage:
            msg = _("Only the owner and admins can change this.")
            raise PermissionDenied(msg)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["organisation"] = self.organisation
        context["membership"] = self.membership
        context["can_manage"] = self.membership.can_manage
        return context


class OnboardingView(OrganisationMixin, UpdateView):
    """Screen 1b — one form, save and continue, then straight to the dashboard."""

    model = Organisation
    form_class = OrganisationProfileForm
    template_name = "organisations/onboarding.html"

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if self.organisation.is_onboarded:
            return redirect("dashboard")
        return super().get(request, *args, **kwargs)

    def get_object(self, queryset=None) -> Organisation:
        return self.organisation

    def get_initial(self) -> dict:
        initial = super().get_initial()
        # The person setting the account up is the technical contact more often
        # than not, so pre-fill rather than ask twice.
        initial.setdefault("legal_name", self.organisation.name)
        initial.setdefault("technical_contact_name", self.request.user.name)
        initial.setdefault("technical_contact_email", self.request.user.email)
        return initial

    def form_valid(self, form):
        organisation = form.save(commit=False)
        organisation.mark_onboarded()
        organisation.save()
        messages.success(
            self.request,
            _("Your vendor profile is set up. Welcome to the hub."),
        )
        return HttpResponseRedirect(reverse("dashboard"))


class OrganisationDetailView(OrganisationMixin, UpdateView):
    """Settings → Organization."""

    model = Organisation
    form_class = OrganisationProfileForm
    template_name = "organisations/organisation_detail.html"
    success_url = reverse_lazy("organisations:detail")

    def get_object(self, queryset=None) -> Organisation:
        return self.organisation

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["nav_section"] = "settings"
        context["settings_section"] = "organisation"
        return context

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not self.membership.can_manage:
            msg = _("Only the owner and admins can edit the organisation profile.")
            raise PermissionDenied(msg)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Organisation profile updated."))
        return response


class TeamView(OrganisationMixin, FormView):
    """Settings → Team: the roster, pending invites, and the invite form.

    GET renders the table; POST is the invite. Role changes and removals are
    their own POST endpoints so each row's action has a distinct URL.
    """

    template_name = "organisations/team.html"
    form_class = InvitationForm
    success_url = reverse_lazy("organisations:team")

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["organisation"] = self.organisation
        return kwargs

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not self.membership.can_manage:
            msg = _("Only the owner and admins can invite teammates.")
            raise PermissionDenied(msg)
        return super().post(request, *args, **kwargs)

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        memberships = list(self.organisation.memberships.select_related("user").all())
        context.update(
            {
                "nav_section": "settings",
                "settings_section": "team",
                "memberships": memberships,
                "member_count": len(memberships),
                "invitations": list(self.organisation.invitations.pending()),
                "assignable_roles": Role.assignable(),
            },
        )
        return context

    def form_valid(self, form: InvitationForm):
        form.instance.invited_by = self.request.user
        invitation = form.save()
        send_invitation_email(self.request, invitation)
        messages.success(
            self.request,
            _("Invite sent to %(email)s.") % {"email": invitation.email},
        )
        return super().form_valid(form)


class TeamActionView(OrganisationMixin, View):
    """Base for the one-shot POST actions on the team screen."""

    require_manage = True
    success_url = reverse_lazy("organisations:team")

    def get_invitation(self, pk: int) -> Invitation:
        return get_object_or_404(
            Invitation,
            pk=pk,
            organisation=self.organisation,
            accepted_at__isnull=True,
        )

    def get_membership(self, pk: int) -> Membership:
        return get_object_or_404(
            Membership,
            pk=pk,
            organisation=self.organisation,
        )


class InvitationResendView(TeamActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        invitation = self.get_invitation(kwargs["pk"])
        invitation.refresh_token()
        invitation.revoked_at = None
        invitation.save(update_fields=["token", "expires_at", "revoked_at"])
        send_invitation_email(request, invitation)
        messages.success(
            request,
            _("Invite resent to %(email)s.") % {"email": invitation.email},
        )
        return redirect(self.success_url)


class InvitationRevokeView(TeamActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        invitation = self.get_invitation(kwargs["pk"])
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
        messages.success(
            request,
            _("Invite to %(email)s revoked.") % {"email": invitation.email},
        )
        return redirect(self.success_url)


class MembershipRoleUpdateView(TeamActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        membership = self.get_membership(kwargs["pk"])
        form = MembershipRoleForm(request.POST, instance=membership)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                _("%(name)s is now a %(role)s.")
                % {
                    "name": membership.user.name or membership.user.email,
                    "role": membership.get_role_display(),
                },
            )
        else:
            messages.error(request, _("That role change is not allowed."))
        return redirect(self.success_url)


class MembershipRemoveView(TeamActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        membership = self.get_membership(kwargs["pk"])
        if membership.is_owner:
            messages.error(request, _("The owner cannot be removed."))
            return redirect(self.success_url)
        name = membership.user.name or membership.user.email
        membership.delete()
        messages.success(
            request,
            _("%(name)s was removed from the team.") % {"name": name},
        )
        return redirect(self.success_url)


class InvitationAcceptView(View):
    """Redeem an invite link.

    Anonymous visitors are parked at signup with the token in their session, so
    the account they create joins this organisation instead of making a new one.
    """

    def get(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        invitation = Invitation.objects.filter(token=kwargs["token"]).first()
        if invitation is None or not invitation.is_pending:
            messages.error(
                request,
                _("That invite link is no longer valid. Ask for a fresh one."),
            )
            return redirect("home")
        if not request.user.is_authenticated:
            request.session[INVITATION_SESSION_KEY] = invitation.token
            messages.info(
                request,
                _("Create your account to join %(organisation)s.")
                % {"organisation": invitation.organisation.name},
            )
            return redirect("account_signup")
        if get_membership_for(request.user) is not None:
            messages.error(
                request,
                _(
                    "You already belong to an organisation, "
                    "so this invite cannot be accepted.",
                ),
            )
            return redirect("dashboard")
        if request.user.email.lower() != invitation.email.lower():
            messages.error(request, _("This invite was sent to a different address."))
            # They have no organisation (checked above), so the dashboard would
            # only 403 at them — the landing page is the honest destination.
            return redirect("home")
        invitation.accept(request.user)
        request.session.pop(INVITATION_SESSION_KEY, None)
        messages.success(
            request,
            _("You joined %(organisation)s.")
            % {"organisation": invitation.organisation.name},
        )
        return redirect("dashboard")


def send_invitation_email(request: HttpRequest, invitation: Invitation) -> None:
    """Email the invite link.

    A plain function rather than a Celery task: the hub sends a handful of these
    a day, and a failed send should surface in the request rather than vanish
    into a worker log.
    """
    context = {
        "invitation": invitation,
        "organisation": invitation.organisation,
        "accept_url": request.build_absolute_uri(invitation.get_absolute_url()),
        "invited_by": invitation.invited_by,
    }
    subject = render_to_string(
        "organisations/email/invitation_subject.txt",
        context,
    ).strip()
    body = render_to_string("organisations/email/invitation_body.txt", context)
    send_mail(
        subject=subject,
        message=body,
        from_email=None,
        recipient_list=[invitation.email],
        fail_silently=False,
    )
