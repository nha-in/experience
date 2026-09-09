"""Offline asset build only. Never use these settings to serve the application."""

from .base import *  # noqa: F403
from .static_assets import *  # noqa: F403
from .static_assets import STATICFILES_STORAGE_BACKEND

DEBUG = False
SECRET_KEY = "static-asset-build-not-a-runtime-secret"  # noqa: S105
DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": STATICFILES_STORAGE_BACKEND},
}
