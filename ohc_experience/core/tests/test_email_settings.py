import copy
import runpy
from unittest.mock import Mock

import pytest

from config.settings import base
from ohc_experience.core.mail import QUEUED_GLOBAL_EMAIL_BACKEND


@pytest.fixture
def production_environment(monkeypatch):
    monkeypatch.setattr(base, "DATABASES", copy.deepcopy(base.DATABASES))
    monkeypatch.setattr(base, "INSTALLED_APPS", list(base.INSTALLED_APPS))
    monkeypatch.setattr("sentry_sdk.init", Mock())
    for name in (
        "DJANGO_SECRET_KEY",
        "DJANGO_AWS_ACCESS_KEY_ID",
        "DJANGO_AWS_SECRET_ACCESS_KEY",
        "DJANGO_AWS_STORAGE_BUCKET_NAME",
    ):
        monkeypatch.setenv(name, "configuration-test-value")
    monkeypatch.setenv("DJANGO_ADMIN_URL", "admin/")
    monkeypatch.setenv("SENTRY_DSN", "")
    monkeypatch.delenv("DJANGO_EMAIL_BACKEND", raising=False)


@pytest.mark.usefixtures("production_environment")
def test_production_defaults_to_queued_gateway():
    result = runpy.run_module("config.settings.production")
    assert result["EMAIL_BACKEND"] == QUEUED_GLOBAL_EMAIL_BACKEND


@pytest.mark.usefixtures("production_environment")
def test_production_sends_verification_codes_through_the_gateway(monkeypatch):
    monkeypatch.delenv("INTEGRATION_NOTIFICATION", raising=False)
    base_ports = dict(base.INTEGRATION_PORTS)
    result = runpy.run_module("config.settings.production")
    assert result["INTEGRATION_PORTS"]["NOTIFICATION"] == (
        "ohc_experience.integrations.notification.adapter.AbdmNotificationGateway"
    )
    assert base_ports == base.INTEGRATION_PORTS


@pytest.mark.usefixtures("production_environment")
def test_production_allows_explicit_backend_override(monkeypatch):
    backend = "django.core.mail.backends.console.EmailBackend"
    monkeypatch.setenv("DJANGO_EMAIL_BACKEND", backend)
    result = runpy.run_module("config.settings.production")
    assert result["EMAIL_BACKEND"] == backend


def test_shell_development_retains_console_mail(monkeypatch):
    monkeypatch.setenv("USE_DOCKER", "no")
    monkeypatch.setenv("DJANGO_EMAIL_BACKEND", QUEUED_GLOBAL_EMAIL_BACKEND)
    monkeypatch.setattr(base, "DATABASES", copy.deepcopy(base.DATABASES))
    monkeypatch.setattr(base, "INSTALLED_APPS", list(base.INSTALLED_APPS))
    monkeypatch.setattr(base, "MIDDLEWARE", list(base.MIDDLEWARE))
    result = runpy.run_module("config.settings.local")
    assert result["EMAIL_BACKEND"] == "django.core.mail.backends.console.EmailBackend"
