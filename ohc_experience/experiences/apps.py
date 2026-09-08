from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ExperiencesConfig(AppConfig):
    name = "ohc_experience.experiences"
    verbose_name = _("Experience manager")
