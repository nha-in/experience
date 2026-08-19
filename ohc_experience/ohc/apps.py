from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OhcConfig(AppConfig):
    name = "ohc_experience.ohc"
    verbose_name = _("OHC console")
