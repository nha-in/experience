from __future__ import annotations

from allauth.account.adapter import get_adapter
from allauth.account.forms import ChangeEmailForm
from allauth.account.forms import ChangePasswordForm
from allauth.account.forms import ChangePhoneForm
from allauth.account.forms import ConfirmEmailVerificationCodeForm
from allauth.account.forms import LoginForm
from allauth.account.forms import ResetPasswordForm
from allauth.account.forms import ResetPasswordKeyForm
from allauth.account.forms import SetPasswordForm
from allauth.account.forms import SignupForm
from allauth.account.forms import VerifyPhoneForm
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
from .fields import INDIA
from .models import User

MIN_PASSWORD_LENGTH = 12
MOBILE_NUMBER_LENGTH = 10
# allauth's wording for an address or number that belongs to another account.
EMAIL_TAKEN = "email_taken"
PHONE_TAKEN = "phone_taken"
# A six-digit code, on a phone keyboard, offered to the phone's SMS autofill.
CODE_INPUT_ATTRS = {
    "inputmode": "numeric",
    "maxlength": "6",
    "autocomplete": "one-time-code",
}


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


class PasswordConfirmationMixin:
    """Report a mistyped confirmation even when the new password is rejected.

    allauth compares the two only after the new password passes validation.
    """

    def clean_password2(self) -> str:
        password2 = self.cleaned_data["password2"]
        if password2 != self["password1"].data:
            msg = _("You must type the same password each time.")
            raise forms.ValidationError(msg, code="password_mismatch")
        return password2


class UserSignupForm(
    SignupVerificationMixin,
    OrganisationSignupMixin,
    PasswordConfirmationMixin,
    SignupForm,
):
    """Integrator account creation — screen 1a of the hub mockups."""

    name = forms.CharField(
        label=_("Full Name"),
        max_length=255,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    mobile_number = forms.CharField(
        label=_("Mobile Number"),
        max_length=10,
        error_messages={
            "required": _("Enter your mobile number."),
            "max_length": _(
                "Enter a valid 10-digit phone number without the country code.",
            ),
        },
        widget=forms.TextInput(
            attrs={
                "autocomplete": "tel",
                "inputmode": "numeric",
                "maxlength": "10",
                "pattern": "[0-9]{10}",
            },
        ),
    )
    organisation = forms.CharField(
        label=_("Organisation/Business Name"),
        max_length=255,
        error_messages={"required": _("Enter your organisation or business name.")},
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )

    organisation_type = forms.ChoiceField(label=_("Type of Entity"))
    website = forms.URLField(
        label=_("Website"),
        required=False,
        widget=forms.URLInput(attrs={"autocomplete": "url"}),
    )

    field_order = [
        "organisation_type",
        "organisation",
        "website",
        "name",
        "email",
        "mobile_number",
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
        if "password2" in self.fields:
            self.fields["password2"].label = _("Confirm Password")

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

    def clean_mobile_number(self) -> str:
        mobile_number = self.cleaned_data["mobile_number"].strip()
        if not mobile_number.isdigit() or len(mobile_number) != MOBILE_NUMBER_LENGTH:
            raise forms.ValidationError(
                _("Enter a valid 10-digit phone number without the country code."),
            )
        return mobile_number

    def clean(self) -> dict:
        cleaned_data = super().clean()
        # Only once the captcha passes, so signup can't be used to probe for accounts.
        if self.account_already_exists and not (
            self.has_error("email") or self.has_error("captcha")
        ):
            msg = _("An account with this email already exists.")
            self.add_error("email", forms.ValidationError(msg, code="account_exists"))
        if self._number_belongs_to_another_account(cleaned_data.get("mobile_number")):
            # Its code could never confirm this account, and would text whoever
            # registered the number.
            self.add_error(
                "mobile_number",
                get_adapter().validation_error(PHONE_TAKEN),
            )
        return cleaned_data

    def _number_belongs_to_another_account(self, mobile_number: str | None) -> bool:
        if not mobile_number or self.has_error("captcha"):
            return False
        return bool(get_adapter().get_user_by_phone(f"{INDIA}{mobile_number}"))

    @property
    def account_exists(self) -> bool:
        return self.has_error("email", "account_exists")

    @transaction.atomic
    def save(self, request):
        user = super().save(request)
        user.name = self.cleaned_data["name"].strip()
        # The field takes the ten digits; the gateway needs the country code.
        # The number is kept unverified until its code is confirmed, so a later
        # sign-in can ask for that code again.
        user.phone_number = f"{INDIA}{self.cleaned_data['mobile_number']}"
        user.save(update_fields=["name", "phone_number"])
        self.attach_organisation(user)
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


class UserLoginForm(LoginForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["login"].label = _("Work email")
        if "remember" in self.fields:
            self.fields["remember"].label = _("Keep me signed in")


class UserResetPasswordForm(ResetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].label = _("Work email")


class UserChangePasswordForm(PasswordConfirmationMixin, ChangePasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["oldpassword"].label = _("Current password")
        self.fields["password1"].label = _("New password")
        self.fields["password2"].label = _("Confirm new password")


class UserSetPasswordForm(PasswordConfirmationMixin, SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password2"].label = _("Confirm password")


class UserResetPasswordKeyForm(PasswordConfirmationMixin, ResetPasswordKeyForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].label = _("New password")
        self.fields["password2"].label = _("Confirm new password")


class UserChangeEmailForm(ChangeEmailForm):
    """Correcting an address to one that is already registered gets nowhere.

    allauth hides the clash to prevent enumeration: it takes the address,
    notifies the account that holds it, and only then refuses every code. Say
    so here instead, while the visitor can still type another.
    """

    def clean_email(self) -> str:
        email = super().clean_email()
        if self.account_already_exists:
            raise get_adapter().validation_error(EMAIL_TAKEN)
        return email


class UserChangePhoneForm(ChangePhoneForm):
    """The same for a number, which would otherwise be texted a code that
    belongs to whoever already registered it."""

    def clean_phone(self) -> str:
        phone = super().clean_phone()
        if self.account_already_exists:
            raise get_adapter().validation_error(PHONE_TAKEN)
        return phone


class CodeInputMixin:
    """Every code field is the same, on this screen and in settings."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].widget.attrs.update(CODE_INPUT_ATTRS)


class UserConfirmEmailVerificationCodeForm(
    CodeInputMixin,
    ConfirmEmailVerificationCodeForm,
):
    pass


class UserVerifyPhoneForm(CodeInputMixin, VerifyPhoneForm):
    pass


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
