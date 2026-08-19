from __future__ import annotations

from django.urls import resolve
from django.urls import reverse


def test_htmx_demo_home():
    assert reverse("htmx-demo") == "/htmx-demo/"
    assert resolve("/htmx-demo/").view_name == "htmx-demo"


def test_htmx_time():
    assert reverse("htmx-time") == "/htmx-demo/htmx/time/"
    assert resolve("/htmx-demo/htmx/time/").view_name == "htmx-time"


def test_htmx_counter():
    assert reverse("htmx-counter") == "/htmx-demo/htmx/counter/"
    assert resolve("/htmx-demo/htmx/counter/").view_name == "htmx-counter"


def test_htmx_search():
    assert reverse("htmx-search") == "/htmx-demo/htmx/search/"
    assert resolve("/htmx-demo/htmx/search/").view_name == "htmx-search"


def test_htmx_greet():
    assert reverse("htmx-greet") == "/htmx-demo/htmx/greet/"
    assert resolve("/htmx-demo/htmx/greet/").view_name == "htmx-greet"
