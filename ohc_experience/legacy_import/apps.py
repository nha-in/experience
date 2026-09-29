from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LegacyImportConfig(AppConfig):
    name = "ohc_experience.legacy_import"
    verbose_name = _("Legacy sandbox import")
