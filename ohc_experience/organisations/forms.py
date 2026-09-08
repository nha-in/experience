from __future__ import annotations

from django import forms
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Invitation
from .models import Membership
from .models import Organisation
from .models import Role


class InvitationForm(forms.ModelForm):
    """Invite a teammate by email with a role."""

    class Meta:
        model = Invitation
        fields = ["email", "role"]
        labels = {"email": _("Email"), "role": _("Role")}

    def __init__(self, *args, organisation: Organisation, **kwargs):
        self.organisation = organisation
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = Role.assignable()
        self.fields["role"].initial = Role.DEVELOPER
        self.fields["email"].widget.attrs.setdefault(
            "placeholder",
            _("colleague@example.in"),
        )

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].lower()
        if Membership.objects.filter(
            organisation=self.organisation,
            user__email__iexact=email,
        ).exists():
            msg = _("That person is already on your team.")
            raise forms.ValidationError(msg)
        if (
            Invitation.objects.filter(
                organisation=self.organisation,
                email__iexact=email,
            )
            .pending()
            .exists()
        ):
            msg = _("An invite is already pending for that address.")
            raise forms.ValidationError(msg)
        return email

    def save(self, commit=True) -> Invitation:  # noqa: FBT002
        invitation = super().save(commit=False)
        invitation.organisation = self.organisation
        # Reuse a spent or expired row for this address so the unique
        # constraint on open invites does not block a legitimate re-invite.
        # Expired rows count: they are still "open" as far as the constraint is
        # concerned, so a fresh insert for the same address would collide.
        stale = (
            Invitation.objects.filter(
                organisation=self.organisation,
                email__iexact=invitation.email,
            )
            .filter(
                Q(accepted_at__isnull=False)
                | Q(revoked_at__isnull=False)
                | Q(expires_at__lte=timezone.now()),
            )
            .first()
        )
        if stale is not None:
            stale.email = invitation.email
            stale.role = invitation.role
            stale.accepted_at = None
            stale.revoked_at = None
            stale.invited_by = invitation.invited_by
            stale.refresh_token()
            if commit:
                stale.save()
            return stale
        if commit:
            invitation.save()
        return invitation


class MembershipRoleForm(forms.ModelForm):
    """Change one member's role from the team table."""

    class Meta:
        model = Membership
        fields = ["role"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = Role.assignable()

    def clean_role(self) -> str:
        role = self.cleaned_data["role"]
        if self.instance.is_owner:
            msg = _("The owner's role cannot be changed.")
            raise forms.ValidationError(msg)
        return role
