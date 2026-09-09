import copy
import os
import runpy
from unittest.mock import Mock
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest
from storages.backends.s3 import S3Storage

from config.settings import base


@pytest.fixture(autouse=True)
def production_storage_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "DATABASES", copy.deepcopy(base.DATABASES))
    monkeypatch.setattr(base, "INSTALLED_APPS", list(base.INSTALLED_APPS))
    monkeypatch.setattr("sentry_sdk.init", Mock())
    for name in list(os.environ):
        if name.startswith(("AWS_", "DJANGO_AWS_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-aws-keys"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "configuration-test-value")
    monkeypatch.setenv("DJANGO_AWS_STORAGE_BUCKET_NAME", "storage-test-bucket")
    monkeypatch.setenv("DJANGO_AWS_S3_REGION_NAME", "ap-south-1")
    monkeypatch.setenv("DJANGO_ADMIN_URL", "admin/")
    monkeypatch.setenv("SENTRY_DSN", "")


@pytest.mark.parametrize("value", [None, ""])
def test_production_can_start_without_explicit_aws_keys(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("DJANGO_AWS_ACCESS_KEY_ID", value)
        monkeypatch.setenv("DJANGO_AWS_SECRET_ACCESS_KEY", value)
    result = runpy.run_module("config.settings.production")
    assert "AWS_ACCESS_KEY_ID" not in result
    assert "AWS_SECRET_ACCESS_KEY" not in result
    options = result["STORAGES"]["default"]["OPTIONS"]
    assert "access_key" not in options
    assert "secret_key" not in options
    assert options["bucket_name"] == "storage-test-bucket"
    assert options["querystring_auth"] is True


def test_production_storage_uses_ecs_task_role(monkeypatch):
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/test-task-role")
    fetch = Mock(
        return_value={
            "AccessKeyId": "task-role-test-key",
            "SecretAccessKey": "task-role-test-secret",
            "Token": "task-role-test-token",
            "Expiration": "2099-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        "botocore.utils.ContainerMetadataFetcher.retrieve_full_uri",
        fetch,
    )
    result = runpy.run_module("config.settings.production")
    storage = S3Storage(**result["STORAGES"]["default"]["OPTIONS"])
    url = storage.url("evidence.pdf")
    fetch.assert_called_once_with(
        "http://169.254.170.2/test-task-role",
        headers=None,
    )
    assert urlsplit(url).path == "/media/evidence.pdf"
    assert ["task-role-test-token"] in parse_qs(urlsplit(url).query).values()


def test_production_preserves_explicit_credentials(monkeypatch):
    monkeypatch.setenv("DJANGO_AWS_ACCESS_KEY_ID", "explicit-test-key")
    monkeypatch.setenv("DJANGO_AWS_SECRET_ACCESS_KEY", "explicit-test-secret")
    result = runpy.run_module("config.settings.production")
    storage = S3Storage(**result["STORAGES"]["default"]["OPTIONS"])
    assert storage.access_key == "explicit-test-key"
    assert storage.secret_key == "explicit-test-secret"  # noqa: S105
    assert urlsplit(storage.url("evidence.pdf")).query


def test_production_accepts_standard_aws_environment_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "environment-test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "environment-test-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "environment-test-token")
    result = runpy.run_module("config.settings.production")
    storage = S3Storage(**result["STORAGES"]["default"]["OPTIONS"])
    query = parse_qs(urlsplit(storage.url("evidence.pdf")).query)
    assert ["environment-test-token"] in query.values()
