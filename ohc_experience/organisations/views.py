from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView
from django.views.generic import TemplateView
from django.views.generic import UpdateView
from django_htmx.http import HttpResponseClientRedirect

from ohc_experience.users.permissions import is_ohc_team

from .forms import InvitationForm
from .forms import MembershipRoleForm
from .forms import OrganisationProfileForm
from .models import Invitation
from .models import Membership
from .models import Organisation
from .models import Role
from .models import Sandbox
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
            # OHC staff routinely have no vendor account. Sending them to the
            # console beats a 403 that reads as breakage on a page they were
            # never meant to open. Staff who *do* belong to a vendor keep the
            # vendor route; the console stays one click away in the sidebar.
            if is_ohc_team(request.user):
                messages.info(
                    request,
                    _("That is a vendor page. Here is the OHC console instead."),
                )
                return redirect("ohc:queue")
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
    # The page includes this fragment; htmx swaps the same file back in.
    partial_template_name = "organisations/partials/organisation_form.html"

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

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["form_layout"] = "onboarding"
        return context

    def form_valid(self, form):
        organisation = form.save(commit=False)
        organisation.mark_onboarded()
        organisation.save()
        messages.success(
            self.request,
            _("Your vendor profile is set up. Welcome to the hub."),
        )
        if self.request.htmx:
            # Finishing onboarding leaves this screen for good, so there is no
            # fragment worth swapping: hand htmx a real client-side redirect and
            # the browser lands on the dashboard exactly as the no-JS path does.
            return HttpResponseClientRedirect(reverse("dashboard"))
        return HttpResponseRedirect(reverse("dashboard"))

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


class OrganisationDetailView(OrganisationMixin, UpdateView):
    """Settings → Organization."""

    model = Organisation
    form_class = OrganisationProfileForm
    template_name = "organisations/organisation_detail.html"
    # The page includes this fragment; htmx swaps the same file back in.
    partial_template_name = "organisations/partials/organisation_form.html"
    success_url = reverse_lazy("dashboard")

    def get_object(self, queryset=None) -> Organisation:
        return self.organisation

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["nav_section"] = "settings"
        context["settings_section"] = "organisation"
        context["form_layout"] = "settings"
        return context

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not self.membership.can_manage:
            msg = _("Only the owner and admins can edit the organisation profile.")
            raise PermissionDenied(msg)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Organisation profile updated."))
        if self.request.htmx:
            # A full navigation home: the dashboard reloads with the new name in
            # the sidebar and the flash riding along in Django's message store.
            return HttpResponseClientRedirect(reverse("dashboard"))
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


class TeamFragmentMixin:
    """Context for the team partials, in the shape the whole page gives them.

    team.html and the htmx endpoints render the very same files, so the names
    have to match on both paths or a swapped row would quietly differ from a
    reloaded one. `invitations` is only passed where a fragment carries the
    pending-invitations heading, whose visibility depends on it.
    """

    def fragment_context(self, **extra) -> dict:
        context = {
            "organisation": self.organisation,
            "membership": self.membership,
            "can_manage": self.membership.can_manage,
            "assignable_roles": Role.assignable(),
        }
        context.update(extra)
        return context


class TeamView(OrganisationMixin, TeamFragmentMixin, FormView):
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
        if self.request.htmx:
            # The new row is the swap; the emptied form, the stacked card and
            # the flash ride along out of band. An unbound form is what clears
            # the fields — the server decides, not the browser.
            return render(
                self.request,
                "organisations/partials/invitation_created.html",
                self.fragment_context(
                    i=invitation,
                    form=InvitationForm(organisation=self.organisation),
                    invitations=list(self.organisation.invitations.pending()),
                ),
            )
        return super().form_valid(form)

    def form_invalid(self, form: InvitationForm):
        # 200 with the re-rendered form, so htmx swaps the errors in; the no-JS
        # path still gets the whole page back, also with a 200.
        if self.request.htmx:
            return render(
                self.request,
                "organisations/partials/invite_form_swap.html",
                self.fragment_context(form=form),
            )
        return super().form_invalid(form)


class TeamActionView(OrganisationMixin, TeamFragmentMixin, View):
    """Base for the one-shot POST actions on the team screen.

    Each action answers an htmx request with the smallest fragment that tells
    the truth, and answers everyone else exactly as it always did: POST →
    redirect → flash, on the team page.
    """

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
        if request.htmx:
            # Only the expiry date moved, so only the row comes back.
            return render(
                request,
                "organisations/partials/invitation_swap.html",
                self.fragment_context(i=invitation),
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
        if request.htmx:
            # Nothing left to swap into the row's place, so the row goes; the
            # heading follows the pending list, which may now be empty.
            return render(
                request,
                "organisations/partials/invitation_removed.html",
                self.fragment_context(
                    invitation_pk=invitation.pk,
                    invitations=list(self.organisation.invitations.pending()),
                ),
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
            # The form has already written the rejected value onto the instance,
            # so read the row back from the database before drawing it again.
            membership.refresh_from_db()
            messages.error(request, _("That role change is not allowed."))
        if request.htmx:
            # A refused change swaps the unchanged row back in, which is how the
            # picker snaps back to the role the server actually holds.
            return render(
                request,
                "organisations/partials/member_swap.html",
                self.fragment_context(m=membership),
            )
        return redirect(self.success_url)


class MembershipRemoveView(TeamActionView):
    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        membership = self.get_membership(kwargs["pk"])
        if membership.is_owner:
            messages.error(request, _("The owner cannot be removed."))
            if request.htmx:
                # Nobody left, so the row must not vanish: send it back intact.
                return render(
                    request,
                    "organisations/partials/member_swap.html",
                    self.fragment_context(m=membership),
                )
            return redirect(self.success_url)
        member_pk = membership.pk
        name = membership.user.name or membership.user.email
        membership.delete()
        messages.success(
            request,
            _("%(name)s was removed from the team.") % {"name": name},
        )
        if request.htmx:
            # Nothing left to swap into the row's place, so the row goes.
            return render(
                request,
                "organisations/partials/member_removed.html",
                self.fragment_context(member_pk=member_pk),
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


def sandbox_frontend_context() -> dict:
    """The Care app the sandbox is used through, which is not the plugin's API."""
    return {"sandbox_frontend_url": settings.CARE_SANDBOX_FRONTEND_URL}


class SandboxView(OrganisationMixin, TemplateView):
    """Vendor sandbox page — visible to every team member."""

    template_name = "organisations/sandbox.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["nav_section"] = "sandbox"
        context["sandbox"] = getattr(self.organisation, "sandbox", None)
        context.update(sandbox_frontend_context())
        return context


class SandboxStatusView(OrganisationMixin, TemplateView):
    """HTMX-polled status fragment for the sandbox page."""

    template_name = "organisations/partials/sandbox_status.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        context["sandbox"] = getattr(self.organisation, "sandbox", None)
        context.update(sandbox_frontend_context())
        return context


class SandboxRequestView(OrganisationMixin, View):
    """Managers request the team's single sandbox."""

    require_manage = True

    def post(self, request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if getattr(self.organisation, "sandbox", None) is not None:
            messages.info(request, _("Your team already has a sandbox."))
            return redirect("organisations:sandbox")

        is_empty = request.POST.get("data_scope") == "empty"
        Sandbox.objects.create(
            organisation=self.organisation,
            requested_by=request.user,
            is_facility_empty=is_empty,
            facility_name=f"{self.organisation.display_name} Sandbox",
            status=Sandbox.Status.REQUESTED,
        )
        messages.success(
            request,
            _("Sandbox requested. The OHC team will provision it shortly."),
        )
        return redirect("organisations:sandbox")
