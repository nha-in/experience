from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SupportConfig(AppConfig):
    name = "ohc_experience.support"
    verbose_name = _("Support")
