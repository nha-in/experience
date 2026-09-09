from http import HTTPStatus

import pytest
from django.apps import apps
from django.db import connection
from django.urls import resolve
from django.urls import reverse

from ohc_experience.experiences.registry import get_program
from ohc_experience.experiences.registry import registry

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "path",
    [
        "/api/users/",
        "/api/docs",
        "/about/",
        "/htmx-demo/",
        "/ohc/",
        "/applications/",
        "/products/",
        "/events/",
        "/support/",
        "/sandbox/",
        "/sandbox/request/",
        "/settings/organisation/",
        "/onboarding/",
        "/~update/",
        "/users/1/",
    ],
)
def test_retired_routes_are_not_exposed(client, user, path):
    client.force_login(user)
    assert client.get(path).status_code == HTTPStatus.NOT_FOUND
    assert client.post(path, {}).status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    ("app_label", "model"),
    [
        ("organisations", "Sandbox"),
        ("experiences", "ApplicationAccess"),
        ("experiences", "ApplicationEvent"),
        ("experiences", "ApplicationQueryThread"),
        ("experiences", "ApplicationQueryMessage"),
    ],
)
def test_retired_models_and_tables_are_removed(app_label, model):
    with pytest.raises(LookupError):
        apps.get_model(app_label, model)
    assert f"{app_label}_{model.lower()}" not in connection.introspection.table_names()


def test_only_current_applications_are_registered():
    assert {definition.key for definition in registry.all()} == {
        get_program().product_application.key,
        get_program().milestone_application.key,
    }


def test_dashboard_uses_current_portal():
    assert (
        resolve(reverse("dashboard")).func is resolve(reverse("experiences:home")).func
    )


def test_navigation_between_portal_and_settings_uses_shared_shell(
    client,
    owner_membership,
):
    client.force_login(owner_membership.user)
    response = client.get(reverse("experiences:products"))
    assert response.status_code == HTTPStatus.OK
    assert f'href="{reverse("organisations:team")}"' in response.text
    assert 'hx-select-oob="#app-nav"' in response.text
    assert 'hx-target="#main-content"' in response.text
    response = client.get(reverse("organisations:team"))
    assert response.status_code == HTTPStatus.OK
    assert (
        f'href="{reverse("experiences:organisation")}" hx-boost="false"'
        in response.text
    )
