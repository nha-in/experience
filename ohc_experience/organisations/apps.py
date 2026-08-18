from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OrganisationsConfig(AppConfig):
    name = "ohc_experience.organisations"
    verbose_name = _("Organisations")
