from __future__ import annotations

from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from django import forms
from django.contrib.auth import forms as admin_forms
from django.db import transaction
from django.forms import EmailField
from django.utils.translation import gettext_lazy as _

from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role

from . import captcha
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


class OhcTeamCreationForm(admin_forms.AdminUserCreationForm):
    """Create an OHC team account from the admin.

    Same as the normal add form except the OHC flag is set for you — the whole
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
        user.is_ohc_team = True
        user.is_staff = True
        if commit:
            user.save()
        return user


# The three-way choice on the sign-up card, mapped onto the full entity types
# the organisation details form asks for later.
SIGNUP_ORGANISATION_TYPES = [
    ("company", _("Company")),
    ("government", _("Government")),
    ("sole_proprietor", _("Sole proprietor")),
]
SIGNUP_ENTITY_TYPES = {
    "company": Organisation.EntityType.PRIVATE_COMPANY,
    "government": Organisation.EntityType.GOVERNMENT_BODY,
    "sole_proprietor": Organisation.EntityType.SOLE_PROPRIETORSHIP,
}


class OrganisationSignupMixin:
    """Creates the signing-up user's organisation, or joins the inviting one.

    One organisation per user: the signup form's entity name creates it and
    makes the user its owner. When the signup came from an invite link, the
    fields are dropped and the invitation's organisation is joined instead —
    that is the only way to end up in someone else's organisation.
    """

    def attach_organisation(self, user: User) -> None:
        if self.invitation is not None:
            self.invitation.accept(user)
            return
        organisation = Organisation.objects.create(
            name=self.cleaned_data["organisation"].strip(),
            entity_type=SIGNUP_ENTITY_TYPES[self.cleaned_data["organisation_type"]],
        )
        Membership.objects.create(
            organisation=organisation,
            user=user,
            role=Role.OWNER,
        )


class UserSignupForm(OrganisationSignupMixin, SignupForm):
    """Integrator account creation — sign-up (design doc 5.1)."""

    name = forms.CharField(
        label=_("Your name"),
        max_length=255,
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    organisation = forms.CharField(
        label=_("Name of the entity"),
        max_length=255,
        error_messages={"required": _("Tell us which organisation you work for.")},
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    organisation_type = forms.ChoiceField(
        label=_("Type of organisation"),
        choices=SIGNUP_ORGANISATION_TYPES,
        initial="company",
        widget=forms.RadioSelect,
    )
    captcha = forms.IntegerField(
        label=_("Captcha"),
        widget=forms.NumberInput(attrs={"autocomplete": "off", "inputmode": "numeric"}),
        error_messages={"required": _("Answer the sum to prove you are a person.")},
    )

    field_order = [
        "name",
        "organisation",
        "organisation_type",
        "email",
        "password1",
        "password2",
        "captcha",
    ]

    def __init__(self, *args, invitation=None, session=None, **kwargs):
        self.invitation = invitation
        self.session = session
        super().__init__(*args, **kwargs)
        if invitation is not None:
            # The organisation is already decided by the invite.
            del self.fields["organisation"]
            del self.fields["organisation_type"]
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        question = captcha.current_question(session)
        self.fields["captcha"].help_text = (
            _("What is %(question)s?") % {"question": question} if question else ""
        )

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

    def clean_captcha(self) -> int:
        answer = self.cleaned_data["captcha"]
        if not captcha.verify(self.session, answer):
            # verify() has issued a fresh challenge; show it with the error.
            self.fields["captcha"].help_text = _("What is %(question)s?") % {
                "question": captcha.current_question(self.session),
            }
            msg = _("That answer is not right. Try the new sum.")
            raise forms.ValidationError(msg)
        return answer

    @transaction.atomic
    def save(self, request):
        user = super().save(request)
        user.name = self.cleaned_data["name"].strip()
        user.save(update_fields=["name"])
        self.attach_organisation(user)
        return user


class UserSocialSignupForm(OrganisationSignupMixin, SocialSignupForm):
    """Signup completion for accounts arriving from a social provider."""

    organisation = forms.CharField(
        label=_("Name of the entity"),
        max_length=255,
        error_messages={"required": _("Tell us which organisation you work for.")},
    )
    organisation_type = forms.ChoiceField(
        label=_("Type of organisation"),
        choices=SIGNUP_ORGANISATION_TYPES,
    )

    def __init__(self, *args, invitation=None, **kwargs):
        self.invitation = invitation
        super().__init__(*args, **kwargs)
        if invitation is not None:
            del self.fields["organisation"]
            del self.fields["organisation_type"]

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

    class Meta:
        model = User
        fields = ["name"]
        labels = {"name": _("Full name")}
