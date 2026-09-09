from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ExperiencesConfig(AppConfig):
    """The experience engine. Definitions are registered by the apps that own them."""

    name = "ohc_experience.experiences"
    verbose_name = _("Experience manager")
