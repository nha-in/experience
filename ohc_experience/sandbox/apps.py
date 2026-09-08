from django.apps import AppConfig


class SandboxConfig(AppConfig):
    name = "ohc_experience.sandbox"
    verbose_name = "ABDM Developer Sandbox"

    def ready(self):
        from . import definitions  # noqa: F401, PLC0415
