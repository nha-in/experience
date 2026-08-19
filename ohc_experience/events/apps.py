from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class EventsConfig(AppConfig):
    name = "ohc_experience.events"
    verbose_name = _("Events")
