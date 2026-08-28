from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ExperiencesConfig(AppConfig):
    name = "ohc_experience.experiences"
    verbose_name = _("Experience manager")

    def ready(self) -> None:
        # Importing definitions registers them. This module performs no database work.
        from .abdm import definition  # noqa: F401, PLC0415
