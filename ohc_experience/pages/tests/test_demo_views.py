from __future__ import annotations

from http import HTTPStatus

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_home_includes_htmx_examples(client):
    response = client.get(reverse("htmx-demo"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert 'hx-headers=\'{"X-CSRFToken":' in content
    assert 'hx-get="' in content
    assert "Fetch server time" in content
    assert "Hospital intake" in content


def test_server_time_partial(client):
    response = client.get(reverse("htmx-time"), HTTP_HX_REQUEST="true")

    assert response.status_code == HTTPStatus.OK
    assert b"UTC" in response.content
    assert b"<html" not in response.content


def test_counter_increment_and_reset(client):
    increment = client.post(reverse("htmx-counter"), {"action": "inc"})
    assert increment.status_code == HTTPStatus.OK
    assert b">1<" in increment.content

    increment = client.post(reverse("htmx-counter"), {"action": "inc"})
    assert b">2<" in increment.content

    decrement = client.post(reverse("htmx-counter"), {"action": "dec"})
    assert b">1<" in decrement.content

    reset = client.post(reverse("htmx-counter"), {"action": "reset"})
    assert b">0<" in reset.content


def test_search_filters_experiences(client):
    response = client.get(reverse("htmx-search"), {"q": "pharmacy"})

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "Pharmacy refill" in content
    assert "Hospital intake" not in content


def test_search_empty_results(client):
    response = client.get(reverse("htmx-search"), {"q": "no-such-experience"})

    assert response.status_code == HTTPStatus.OK
    assert b"No experiences match" in response.content


def test_greet_uses_submitted_name(client):
    response = client.post(reverse("htmx-greet"), {"name": "Ada"})

    assert response.status_code == HTTPStatus.OK
    assert b"Ada" in response.content


def test_greet_defaults_when_name_missing(client):
    response = client.post(reverse("htmx-greet"), {"name": "  "})

    assert response.status_code == HTTPStatus.OK
    assert b"friend" in response.content
