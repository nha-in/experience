"""How the staff screens show a wait: the loading bar every request in the
shell runs."""

import re
from http import HTTPStatus

import pytest
from django.conf import settings
from django.urls import reverse

from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def administrator():
    return UserFactory(is_superuser=True, is_nha_team=True)


def test_every_request_in_the_shell_runs_the_loading_bar(client):
    client.force_login(administrator())
    response = client.get(reverse("experiences:assess-dashboard"))
    html = response.content.decode()
    portal = re.search(r'<div id="portal"[^>]*>', html).group(0)

    assert response.status_code == HTTPStatus.OK
    assert 'hx-indicator="#nav-progress"' in portal
    assert 'id="nav-progress"' in html


def test_a_form_with_its_own_spinner_names_itself_the_indicator():
    """htmx marks only the indicator, so the bar the shell hands down would
    leave a spinner inside a form dark unless the form names itself."""
    templates = settings.APPS_DIR / "templates"
    spinners = [
        path
        for path in templates.rglob("*.html")
        if 'class="htmx-indicator' in path.read_text() and path.name != "base.html"
    ]

    assert spinners
    for path in spinners:
        assert 'hx-indicator="this"' in path.read_text(), path
