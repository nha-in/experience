from __future__ import annotations

from typing import TYPE_CHECKING

from django.urls import resolve
from django.urls import reverse

if TYPE_CHECKING:
    from ohc_experience.users.models import User


def test_detail(user: User):
    assert reverse("users:detail", kwargs={"pk": user.pk}) == f"/users/{user.pk}/"
    assert resolve(f"/users/{user.pk}/").view_name == "users:detail"


def test_update():
    assert reverse("users:update") == "/~update/"
    assert resolve("/~update/").view_name == "users:update"


def test_redirect():
    assert reverse("users:redirect") == "/~redirect/"
    assert resolve("/~redirect/").view_name == "users:redirect"
