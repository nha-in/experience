from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class UsersConfig(AppConfig):
    name = "ohc_experience.users"
    verbose_name = _("Users")

    def ready(self):
        from . import signals  # noqa: F401, PLC0415
