from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import FormView

from ohc_experience.users.permissions import is_ohc_team

from .forms import InvitationForm
from .forms import MembershipRoleForm
from .lgd import LGDLookupError
from .lgd import lookup_pincode
from .models import Invitation
from .models import Membership
from .models import Role
from .selectors import get_membership_for

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponse

# Where an invite token waits while an invited person signs up or signs in.
INVITATION_SESSION_KEY = "pending_invitation_token"


class PincodeLookupView(LoginRequiredMixin, View):
    """Resolve an organisation member's PIN without exposing the provider key."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest) -> JsonResponse:
        if get_membership_for(request.user) is None:
            return JsonResponse(
                {"error": _("You are not a member of any organisation.")},
                status=403,
            )
        pincode = request.GET.get("pincode", "").strip()
        try:
            locations = lookup_pincode(pincode)
        except ValidationError as exc:
            return JsonResponse({"error": exc.messages[0]}, status=400)
        except LGDLookupError as exc:
            return JsonResponse({"error": str(exc)}, status=503)
        if not locations:
            return JsonResponse(
                {"error": _("No state or district was found for this PIN code.")},
                status=404,
            )
        return JsonResponse({"pincode": pincode, "locations": locations})


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
                    _("Opening the review dashboard for your reviewer account."),
                )
                return redirect("experiences:assess-dashboard")
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
