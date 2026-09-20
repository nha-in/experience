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
def test_production_sends_verification_codes_through_the_gateway():
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


@pytest.fixture
def load_base(monkeypatch):
    """Re-evaluate base settings, where the send URL derives from the host."""
    monkeypatch.setenv("DJANGO_READ_DOT_ENV_FILE", "false")

    def load(host, **environment):
        monkeypatch.setenv("NOTIFICATION_APP_BASE_URL", host)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        result = runpy.run_module("config.settings.base")
        return result["ANYMAIL"]["GLOBAL_EMAIL_API_URL"], result

    return load


def test_gateway_email_follows_the_notification_host(load_base):
    api_url, _ = load_base("http://notification-app.internal:9102")
    assert api_url == (
        "http://notification-app.internal:9102/internal/v3/notification/email/send"
    )


def test_a_trailing_slash_does_not_double_up(load_base):
    api_url, _ = load_base("http://notification-app.internal:9102/")
    assert api_url == (
        "http://notification-app.internal:9102/internal/v3/notification/email/send"
    )


def test_verification_codes_and_email_share_one_host(load_base):
    api_url, result = load_base("http://notification-app.internal:9102")
    assert api_url.startswith(result["NOTIFICATION_APP_BASE_URL"])


def test_a_stale_endpoint_cannot_redirect_email(load_base):
    api_url, _ = load_base(
        "http://notification-app.internal:9102",
        GLOBAL_EMAIL_API_URL="http://elsewhere.internal/send",
    )
    assert api_url == (
        "http://notification-app.internal:9102/internal/v3/notification/email/send"
    )
