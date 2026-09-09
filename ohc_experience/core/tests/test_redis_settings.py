import copy
import runpy
import ssl
from unittest.mock import Mock

import pytest
from celery import Celery
from django_redis.cache import RedisCache
from django_redis.pool import ConnectionFactory
from redis import SSLConnection

from config.settings import base


@pytest.fixture(params=["local", "production"])
def redis_settings(request, monkeypatch):
    monkeypatch.setattr(base, "DATABASES", copy.deepcopy(base.DATABASES))
    monkeypatch.setattr(base, "INSTALLED_APPS", list(base.INSTALLED_APPS))
    monkeypatch.setattr(base, "MIDDLEWARE", list(base.MIDDLEWARE))
    monkeypatch.setattr(ConnectionFactory, "_pools", {})
    monkeypatch.setattr("sentry_sdk.init", Mock())
    monkeypatch.setenv("DJANGO_SECRET_KEY", "configuration-test-value")
    monkeypatch.setenv("DJANGO_AWS_STORAGE_BUCKET_NAME", "redis-test-bucket")
    monkeypatch.setenv("DJANGO_ADMIN_URL", "admin/")
    monkeypatch.setenv("SENTRY_DSN", "")
    monkeypatch.setenv("DJANGO_READ_DOT_ENV_FILE", "false")
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("CELERY_RESULT_BACKEND", raising=False)

    def load(url, token):
        for name, value in (("REDIS_URL", url), ("REDIS_AUTH_TOKEN", token)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        values = runpy.run_module("config.settings.base")
        for name, value in values.items():
            if name.startswith(("REDIS_", "CELERY_")):
                monkeypatch.setattr(base, name, value)
        return runpy.run_module(f"config.settings.{request.param}")

    return load


@pytest.mark.parametrize(
    ("url", "token", "expected_password", "username"),
    [
        (None, None, None, None),
        ("redis://redis:6379/0", "", None, None),
        ("redis://redis:6379/0", "local-auth", "local-auth", None),
        ("rediss://redis.example:6379/0", "aws-auth", "aws-auth", None),
        (
            "rediss://redis.example:6379/2",
            "$literal!&#^<>-%25@:/?+",
            "$literal!&#^<>-%25@:/?+",
            None,
        ),
        (
            "rediss://portal@redis.example:6379/0",
            "acl-auth",
            "acl-auth",
            "portal",
        ),
        (
            "rediss://portal@[::1]:6380/2",
            "ipv6-auth",
            "ipv6-auth",
            "portal",
        ),
        (
            "rediss://:existing%40password@redis.example:6379/0",
            None,
            "existing@password",
            None,
        ),
        (
            "rediss://portal:existing%40password@redis.example:6379/0",
            "separate-token",
            "existing@password",
            "portal",
        ),
    ],
)
def test_cache_and_celery_use_the_same_redis_authentication(
    redis_settings,
    url,
    token,
    expected_password,
    username,
):
    config = redis_settings(url, token)
    expected_url = url or "redis://redis:6379/0"
    assert config["REDIS_URL"] == expected_url
    assert config["REDIS_AUTH_TOKEN"] == (token or None)
    cache_settings = config["CACHES"]["default"]
    assert cache_settings["LOCATION"] == expected_url
    assert config["CELERY_BROKER_URL"] == expected_url
    assert config["CELERY_RESULT_BACKEND"] == expected_url

    cache = RedisCache(cache_settings["LOCATION"], cache_settings)
    cache_params = cache.client.get_client().connection_pool.connection_kwargs
    assert cache_params.get("password") == expected_password
    assert cache_params.get("username") == username

    # Inspect the real clients without opening connections or scheduling tasks.
    with Celery("redis-settings-test", set_as_current=False) as app:
        app.config_from_object(config, namespace="CELERY")
        with app.connection_for_write() as broker:
            assert broker.password == expected_password
            assert broker.userid == username
            backend_params = app.backend.connparams
            assert backend_params.get("password") == expected_password
            assert backend_params.get("username") == username
            assert broker.hostname == cache_params["host"] == backend_params["host"]
            assert broker.port == cache_params["port"] == backend_params["port"]
            assert (
                int(broker.virtual_host) == cache_params["db"] == backend_params["db"]
            )
            if expected_url.startswith("rediss://"):
                assert broker.ssl["ssl_cert_reqs"] == ssl.CERT_REQUIRED
                assert broker.ssl["ssl_check_hostname"] is True
                assert backend_params["ssl_cert_reqs"] == ssl.CERT_REQUIRED
                assert backend_params["ssl_check_hostname"] is True
                assert (
                    cache.client.get_client().connection_pool.connection_class
                    is SSLConnection
                )
            else:
                assert not broker.ssl
                assert "ssl_cert_reqs" not in backend_params


def test_flower_broker_url_override_keeps_auth_token(redis_settings):
    url = "rediss://redis.example:6379/0"
    config = redis_settings(url, "flower-auth")
    with Celery("flower-settings-test", set_as_current=False) as app:
        app.config_from_object(config, namespace="CELERY")
        app.conf.broker_url = url
        with app.connection_for_write() as broker:
            assert broker.password == config["REDIS_AUTH_TOKEN"]
            assert broker.ssl["ssl_cert_reqs"] == ssl.CERT_REQUIRED
