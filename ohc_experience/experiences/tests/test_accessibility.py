"""What the portal shows reaches a keyboard and a screen reader, and none of it
rests on colour alone."""

# ruff: noqa: F811
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences.models import ReviewItem

TEMPLATES = Path(settings.APPS_DIR) / "templates"
THEME = Path(settings.BASE_DIR) / "theme" / "static_src" / "src"
#: A focus ring drawn as a translucent box-shadow: too pale to see, and gone in
#: forced colours, where only an outline still shows.
FAINT_RING = re.compile(r"focus-visible:ring-[\w-]+/\d+")
#: The hands of components/nav_icon.html's clock.
CLOCK = '<path d="M12 7v5l3 2" />'


def page(client, url, **params):
    """The page's HTML, its runs of whitespace closed up."""
    return " ".join(client.get(url, params).content.decode().split())


def test_no_focus_ring_is_a_faint_shadow():
    """A field may glow on focus, as long as its border turns solid with it."""
    found = [
        f"{path.name}:{number}"
        for path in [*TEMPLATES.rglob("*.html"), *THEME.glob("*.css")]
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if "focus-visible:outline-none" in line
        or (FAINT_RING.search(line) and "focus-visible:border-ring" not in line)
    ]
    assert found == []


def test_keyboard_focus_draws_a_solid_ring_by_default():
    styles = (THEME / "styles.css").read_text()

    assert "--ring: var(--primary-600);" in styles
    assert re.search(
        r':focus-visible:not\(\[tabindex="-1"\]\) \{\s*'
        r"@apply rounded-\[4px\] outline-2 outline-offset-2 outline-ring;",
        styles,
    )


@pytest.mark.django_db
def test_a_request_waiting_over_a_week_carries_a_clock_as_well_as_red(
    environment,
    client,
):
    item = submit(environment)
    client.force_login(environment["admin"])
    queue = reverse("experiences:queue")

    assert CLOCK not in page(client, queue)

    ReviewItem.objects.filter(pk=item.pk).update(
        submitted_at=timezone.now() - timedelta(days=10),
    )

    html = page(client, queue)
    assert html.count(CLOCK) == 1
    age = html[html.index(CLOCK) :].split("</span>", 1)[0]
    assert "10 days" in age


@pytest.mark.django_db
def test_the_protected_mark_names_itself_to_screen_readers(environment, client):
    """An aria-label on a plain span is ignored, so the words are in the span."""
    client.force_login(environment["admin"])

    html = page(client, reverse("experiences:staff-list"))

    assert '<span class="sr-only">Protected superadmin account</span>' in html
    assert 'aria-label="Protected superadmin account"' not in html
