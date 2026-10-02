# ruff: noqa: PLR2004
"""What the staff screens say when a request fails or access is refused, and
how they show a wait: toasts that carry only words, the words the error pages
lend the failed-request notice, and the loading bar every request in the shell
runs."""

import re
from html.parser import HTMLParser
from http import HTTPStatus

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse

from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


class Marked(HTMLParser):
    """The attributes and text of each element carrying one of `markers`."""

    def __init__(self, *markers):
        super().__init__()
        self.markers = markers
        self.found = {}
        self.open = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        for marker in self.markers:
            if marker in attributes:
                self.found[marker] = {"tag": tag, "attrs": attributes, "text": ""}
                self.open.append((tag, marker))

    def handle_endtag(self, tag):
        if self.open and self.open[-1][0] == tag:
            self.open.pop()

    def handle_data(self, data):
        for _tag, marker in self.open:
            self.found[marker]["text"] += data


def marked(html, *markers):
    parser = Marked(*markers)
    parser.feed(html)
    for found in parser.found.values():
        found["text"] = " ".join(found["text"].split())
    return parser.found


def administrator():
    return UserFactory(is_superuser=True, is_nha_team=True)


class Note:
    """A Django message, as components/messages.html reads one."""

    def __init__(self, level_tag):
        self.level_tag = level_tag

    def __str__(self):
        return "Production details saved."


def test_a_toast_holds_no_button_but_its_dismiss():
    """A toast says what happened; what to do about it stays on the page."""
    html = render_to_string(
        "components/toaster.html",
        {"messages": [Note("success"), Note("error")]},
    )
    buttons = re.findall(r"<button\b[^>]*>", html)

    # One for each message, and one for the failed-request notice.
    assert len(buttons) == 3
    for button in buttons:
        assert "ui-toast-close" in button


def test_a_refusal_marks_its_reason_for_the_notice(client):
    client.force_login(UserFactory(is_nha_team=True))
    response = client.get(reverse("experiences:assess-dashboard"))
    found = marked(
        response.content.decode(),
        "data-error-heading",
        "data-error-message",
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert found["data-error-heading"]["text"] == "You do not have access to this page"
    assert found["data-error-message"]["text"] == "This area is for reviewers."


def test_a_missing_page_marks_its_words_for_the_notice(client):
    client.force_login(administrator())
    response = client.get(reverse("experiences:review", args=[999999]))
    found = marked(
        response.content.decode(),
        "data-error-heading",
        "data-error-message",
    )

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert found["data-error-heading"]["text"] == "We could not find that page"
    assert found["data-error-message"]["text"].startswith("The link may be out of date")


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
