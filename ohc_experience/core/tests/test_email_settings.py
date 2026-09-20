import copy
import runpy
from unittest.mock import Mock

import pytest

from config.settings import base
from ohc_experience.core.mail import QUEUED_GLOBAL_EMAIL_BACKEND
from ohc_experience.core.mail.templates import APPROVED_TEMPLATE_IDS


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
        "http://notification-app.internal:9102/internal/v3/notification/message"
    )


def test_a_trailing_slash_does_not_double_up(load_base):
    api_url, _ = load_base("http://notification-app.internal:9102/")
    assert api_url == (
        "http://notification-app.internal:9102/internal/v3/notification/message"
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
        "http://notification-app.internal:9102/internal/v3/notification/message"
    )


def test_approved_ids_need_no_environment(load_base, monkeypatch):
    """A body deploys in the image; its template ID has to arrive with it."""
    monkeypatch.delenv("GLOBAL_EMAIL_TEMPLATE_IDS", raising=False)
    _, result = load_base("http://notification-app.internal:9102")
    assert result["GLOBAL_EMAIL_TEMPLATE_IDS"] == APPROVED_TEMPLATE_IDS


def test_an_override_changes_one_purpose_and_keeps_the_rest(load_base):
    """A gateway that registered its own ID for one email must not silently
    drop the three the deployment did not mention."""
    _, result = load_base(
        "http://notification-app.internal:9102",
        GLOBAL_EMAIL_TEMPLATE_IDS='{"organisation_invitation": "74050"}',
    )
    ids = result["GLOBAL_EMAIL_TEMPLATE_IDS"]
    assert ids["organisation_invitation"] == "74050"
    assert ids["review"] == APPROVED_TEMPLATE_IDS["review"]
    assert ids["notification"] == APPROVED_TEMPLATE_IDS["notification"]


def test_the_ses_path_is_one_variable_away(load_base):
    """The SES endpoint 404s on the production gateway, so portal email takes
    the multi-channel one until NHA deploys it."""
    api_url, _ = load_base(
        "http://notification-app.internal:9102",
        GLOBAL_EMAIL_USE_MESSAGE_ENDPOINT="false",
    )
    assert api_url == (
        "http://notification-app.internal:9102/internal/v3/notification/email/send"
    )
