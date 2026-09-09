from http import HTTPStatus

import pytest
from django.urls import resolve
from django.urls import reverse

from ohc_experience.core.health import ping


def test_ping_is_registered_at_root():
    assert reverse("ping") == "/ping/"
    assert resolve("/ping/").func is ping


@pytest.mark.parametrize("secure", [False, True])
def test_ping_accepts_alb_probes_without_database_or_auth(client, settings, secure):
    settings.SECURE_SSL_REDIRECT = True
    settings.ALLOWED_HOSTS = ["portal.example.test"]
    response = client.get(
        "/ping/",
        secure=secure,
        HTTP_HOST="10.0.1.5:5000",
        HTTP_USER_AGENT="ELB-HealthChecker/2.0",
    )
    assert response.status_code == HTTPStatus.OK
    assert response.content == b"OK"
    assert response["Content-Type"] == "text/plain"
    assert "Location" not in response
    assert not response.cookies


def test_ping_supports_head(client):
    response = client.head("/ping/")
    assert response.status_code == HTTPStatus.OK
    assert response.content == b""


def test_ping_view_does_not_open_a_transaction(client, settings):
    settings.MIDDLEWARE = []
    response = client.get("/ping/")
    assert response.status_code == HTTPStatus.OK


@pytest.mark.parametrize("path", ["/", "/ping", "/ping/extra/"])
def test_only_ping_bypasses_https_redirects(client, settings, path):
    settings.SECURE_SSL_REDIRECT = True
    response = client.get(path)
    assert response.status_code == HTTPStatus.MOVED_PERMANENTLY
    assert response["Location"] == f"https://testserver{path}"


def test_other_paths_still_validate_hosts(client, settings):
    settings.ALLOWED_HOSTS = ["portal.example.test"]
    response = client.get("/", HTTP_HOST="10.0.1.5:5000")
    assert response.status_code == HTTPStatus.BAD_REQUEST


def test_ping_rejects_unsafe_methods(client):
    response = client.post("/ping/")
    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
