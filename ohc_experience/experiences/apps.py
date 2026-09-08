from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ExperiencesConfig(AppConfig):
    name = "ohc_experience.experiences"
    verbose_name = _("Experience manager")

    def ready(self):
        from .registry import load_definitions  # noqa: PLC0415

        load_definitions()
