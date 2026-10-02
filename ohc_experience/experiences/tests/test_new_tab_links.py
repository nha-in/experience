"""A link that opens a new tab says so, the same way everywhere."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse

from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401

TEMPLATES = Path(settings.APPS_DIR) / "templates"
#: The sign-in pages follow the landing page's design, arrow and all.
SIGN_IN = TEMPLATES / "account" / "base_entrance.html"
NEW_TAB_LINK = re.compile(r'<a\b[^>]*\btarget="_blank"[^>]*>(.*?)</a>', re.S)
#: How such a link ends: components/new_tab_mark.html.
MARK = " ".join(render_to_string("components/new_tab_mark.html").split())


def compact(response):
    return " ".join(response.content.decode().split())


def test_no_link_that_opens_a_new_tab_ends_with_a_bare_arrow():
    """The arrow was too small to see and said nothing to a screen reader."""
    found = [
        f"{path.relative_to(TEMPLATES)}: {' '.join(content.split())[:60]}"
        for path in sorted(TEMPLATES.rglob("*.html"))
        if path != SIGN_IN
        for content in NEW_TAB_LINK.findall(path.read_text())
        if "↗" in content
    ]
    assert found == []


def test_the_mark_explains_itself_in_a_tooltip_and_to_screen_readers():
    assert 'd="M15 3h6v6M10 14 21 3' in MARK
    assert '<span class="sr-only"> (opens in a new tab)</span>' in MARK
    assert (
        '<span class="ui-tooltip ui-tooltip--top" aria-hidden="true">'
        "Opens in a new tab</span>"
    ) in MARK


@pytest.mark.django_db
def test_a_label_that_names_a_link_says_it_opens_a_new_tab(environment, client):  # noqa: F811
    """An aria-label replaces the link's words, the mark's among them."""
    client.force_login(environment["applicant"])

    html = compact(
        client.get(
            reverse(
                "experiences:agent-skills",
                args=[environment["product"].reference],
            ),
        ),
    )

    labels = re.findall(r'aria-label="(Docs for [^"]+)">Docs ', html)
    assert labels
    assert all(label.endswith(" (opens in a new tab)") for label in labels)
    assert f"How Agent Skills work {MARK}" in html
