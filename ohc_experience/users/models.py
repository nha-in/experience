from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.db.models import CharField
from django.db.models import EmailField
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class User(AbstractUser):
    """
    Default custom user model for OHC Experience.
    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """

    # First and last name do not cover name patterns around the globe
    name = CharField(_("Name of User"), blank=True, max_length=255)
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]
    email = EmailField(_("email address"), unique=True)
    phone_number = CharField(_("Mobile number"), blank=True, max_length=32)
    username = None  # type: ignore[assignment]

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()

    def get_absolute_url(self) -> str:
        """Get URL for the user's own account settings.

        Returns:
            str: URL for the profile page.

        """
        return reverse("users:profile")

    @property
    def display_name(self) -> str:
        return self.name or self.email.split("@")[0]

    @property
    def first_name_or_email(self) -> str:
        """First word of the name, for the dashboard's "Good afternoon, Meera"."""
        return (self.name or self.email.split("@")[0]).split()[0]
