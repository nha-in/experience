from __future__ import annotations

from django.urls import resolve
from django.urls import reverse


def test_home():
    assert reverse("htmx-demo") == "/htmx-demo/"
    assert resolve("/htmx-demo/").view_name == "home"


def test_htmx_time():
    assert reverse("htmx-time") == "/htmx/time/"
    assert resolve("/htmx/time/").view_name == "htmx-time"


def test_htmx_counter():
    assert reverse("htmx-counter") == "/htmx/counter/"
    assert resolve("/htmx/counter/").view_name == "htmx-counter"


def test_htmx_search():
    assert reverse("htmx-search") == "/htmx/search/"
    assert resolve("/htmx/search/").view_name == "htmx-search"


def test_htmx_greet():
    assert reverse("htmx-greet") == "/htmx/greet/"
    assert resolve("/htmx/greet/").view_name == "htmx-greet"
