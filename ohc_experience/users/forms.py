from __future__ import annotations

from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from django import forms
from django.contrib.auth import forms as admin_forms
from django.db import transaction
from django.forms import EmailField
from django.utils.translation import gettext_lazy as _

from ohc_experience.experiences.registry import get_program
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role

from .captcha import SignupVerificationMixin
from .models import User

MIN_PASSWORD_LENGTH = 12


class UserAdminChangeForm(admin_forms.UserChangeForm):
    class Meta(admin_forms.UserChangeForm.Meta):
        model = User
        field_classes = {"email": EmailField}


class UserAdminCreationForm(admin_forms.AdminUserCreationForm):
    """
    Form for User Creation in the Admin Area.
    To change user signup, see UserSignupForm and UserSocialSignupForm.
    """

    class Meta(admin_forms.UserCreationForm.Meta):
        model = User
        fields = ("email",)
        field_classes = {"email": EmailField}
        error_messages = {
            "email": {"unique": _("This email has already been taken.")},
        }


class NhaTeamCreationForm(admin_forms.AdminUserCreationForm):
    """Create an NHA team account from the admin.

    Same as the normal add form except the NHA flag is set for you — the whole
    point of the screen — and the account is given staff access so the person
    can reach the admin they were just created in.
    """

    class Meta(admin_forms.AdminUserCreationForm.Meta):
        model = User
        fields = ("email", "name")
        field_classes = {"email": EmailField}
        error_messages = {
            "email": {"unique": _("This email has already been taken.")},
        }

    def save(self, commit=True) -> User:  # noqa: FBT002
        user = super().save(commit=False)
        user.is_nha_team = True
        user.is_staff = True
        if commit:
            user.save()
        return user


class OrganisationSignupMixin:
    """Creates the signing-up user's organisation, or joins the inviting one.

    One organisation per user: the signup form's Organisation field creates it
    and makes the user its owner. When the signup came from an invite link, the
    field is dropped and the invitation's organisation is joined instead — that
    is the only way to end up in someone else's organisation.
    """

    def attach_organisation(self, user: User) -> None:
        if self.invitation is not None:
            self.invitation.accept(user)
            return
        organisation = Organisation.objects.create(
            name=self.cleaned_data["organisation"].strip(),
            entity_type=self.cleaned_data.get("organisation_type", ""),
            website=self.cleaned_data.get("website", ""),
        )
        Membership.objects.create(
            organisation=organisation,
            user=user,
            role=Role.OWNER,
        )


class UserSignupForm(SignupVerificationMixin, OrganisationSignupMixin, SignupForm):
    """Integrator account creation — screen 1a of the hub mockups."""

    name = forms.CharField(
        label=_("Full name"),
        max_length=255,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    mobile_number = forms.CharField(
        label=_("Mobile number"),
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
    )
    organisation = forms.CharField(
        label=_("Organisation/business name"),
        max_length=255,
        error_messages={"required": _("Enter your organisation or business name.")},
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )

    organisation_type = forms.ChoiceField(label=_("Type of entity"))
    website = forms.URLField(
        label=_("Website"),
        required=False,
        widget=forms.URLInput(attrs={"autocomplete": "url"}),
    )

    field_order = [
        "name",
        "email",
        "mobile_number",
        "organisation",
        "organisation_type",
        "website",
        "password1",
        "password2",
    ]

    def __init__(self, *args, invitation=None, **kwargs):
        self.invitation = invitation
        super().__init__(*args, **kwargs)
        self.fields["organisation_type"].choices = [
            ("", _("Select a type")),
            *get_program().signup_organisation_choices,
        ]
        if invitation is not None:
            # The organisation is already decided by the invite.
            del self.fields["organisation"]
            del self.fields["organisation_type"]
        self.fields["email"].widget.attrs["autocomplete"] = "email"

    def clean_organisation(self) -> str:
        organisation = self.cleaned_data["organisation"].strip()
        if not organisation:
            raise forms.ValidationError(
                self.fields["organisation"].error_messages["required"],
                code="required",
            )
        return organisation

    def clean_email(self) -> str:
        email = super().clean_email()
        if (
            self.invitation is not None
            and email.lower() != self.invitation.email.lower()
        ):
            msg = _("Sign up with the address the invite was sent to.")
            raise forms.ValidationError(msg)
        return email

    def clean(self) -> dict:
        cleaned_data = super().clean()
        # Only once the captcha passes, so signup can't be used to probe for accounts.
        if self.account_already_exists and not (
            self.has_error("email") or self.has_error("captcha")
        ):
            msg = _("An account with this email already exists.")
            self.add_error("email", forms.ValidationError(msg, code="account_exists"))
        return cleaned_data

    @property
    def account_exists(self) -> bool:
        return self.has_error("email", "account_exists")

    @transaction.atomic
    def save(self, request):
        user = super().save(request)
        user.name = self.cleaned_data["name"].strip()
        user.phone_number = self.cleaned_data["mobile_number"].strip()
        user.save(update_fields=["name", "phone_number"])
        self.attach_organisation(user)
        request.session.pop("signup_challenge", None)
        return user


class UserSocialSignupForm(OrganisationSignupMixin, SocialSignupForm):
    """Signup completion for accounts arriving from a social provider."""

    organisation = forms.CharField(
        label=_("Organisation/business name"),
        max_length=255,
        error_messages={"required": _("Enter your organisation or business name.")},
    )

    def __init__(self, *args, invitation=None, **kwargs):
        self.invitation = invitation
        super().__init__(*args, **kwargs)
        if invitation is not None:
            del self.fields["organisation"]

    def clean_organisation(self) -> str:
        organisation = self.cleaned_data["organisation"].strip()
        if not organisation:
            raise forms.ValidationError(
                self.fields["organisation"].error_messages["required"],
                code="required",
            )
        return organisation

    @transaction.atomic
    def save(self, request):
        user = super().save(request)
        self.attach_organisation(user)
        return user


class UserProfileForm(forms.ModelForm):
    """The signed-in user's own details."""

    email = forms.EmailField(label=_("Email"), disabled=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].initial = self.instance.email

    class Meta:
        model = User
        fields = ["name"]
        labels = {"name": _("Full name")}
