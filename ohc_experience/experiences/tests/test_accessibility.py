"""What the portal shows reaches a keyboard and a screen reader, and none of it
rests on colour alone."""

import re
from pathlib import Path

from django.conf import settings

TEMPLATES = Path(settings.APPS_DIR) / "templates"
THEME = Path(settings.BASE_DIR) / "theme" / "static_src" / "src"
#: A focus ring drawn as a translucent box-shadow: too pale to see, and gone in
#: forced colours, where only an outline still shows.
FAINT_RING = re.compile(r"focus-visible:ring-[\w-]+/\d+")


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
