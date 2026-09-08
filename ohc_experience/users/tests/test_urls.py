from django.urls import resolve
from django.urls import reverse


def test_profile():
    assert reverse("users:profile") == "/settings/profile/"
    assert resolve("/settings/profile/").view_name == "users:profile"


def test_redirect():
    assert reverse("users:redirect") == "/~redirect/"
    assert resolve("/~redirect/").view_name == "users:redirect"
