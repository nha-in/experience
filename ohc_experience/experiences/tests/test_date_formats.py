import datetime
import re
from pathlib import Path

from django.conf import settings
from django.utils import formats
from django.utils import translation

TEMPLATES = Path(settings.APPS_DIR) / "templates"
# Formats that print no date for a person to read: ISO for machines and
# comparisons, times, the weekday, and the calendar tiles' separate day and
# short month.
NOT_A_DATE = {"c", "Y-m-d", "H:i", "D", "D, H:i", "d", "M"}


def test_templates_print_dates_day_first():
    # A bare |date takes the language's default, which LocaleMiddleware can make
    # "4 septembre 2026", so every date spells its format out.
    found = []
    for path in sorted(TEMPLATES.rglob("*")):
        if path.suffix not in {".html", ".txt"}:
            continue
        text = path.read_text()
        name = path.relative_to(TEMPLATES)
        found += [f"{name}: |date" for _ in re.findall(r"\|date(?![:\w])", text)]
        found += [
            f"{name}: {fmt}"
            for _, fmt in re.findall(r"""\|date:(["'])(.*?)\1""", text)
            if fmt not in NOT_A_DATE and "d/m/Y" not in fmt
        ]
    assert found == []


def test_english_defaults_are_day_first():
    moment = datetime.datetime(2026, 9, 4, 21, 13)  # noqa: DTZ001
    for language in ("en-us", "en-in"):
        with translation.override(language):
            assert formats.date_format(moment) == "04/09/2026"
            assert formats.date_format(moment, "DATETIME_FORMAT") == "04/09/2026, 21:13"
