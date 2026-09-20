"""Offline state and district names for when the LGD service cannot be reached.

The list carries names only. LGD codes and the PIN-to-district mapping are not
in this data, so a selection made from it is unverified: the codes stay empty.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "states_and_districts.json"


@cache
def _catalogue() -> dict[str, tuple[str, ...]]:
    with DATA_FILE.open(encoding="utf-8") as handle:
        entries = json.load(handle)
    return {entry["state"]: tuple(entry["districts"]) for entry in entries}


def state_names() -> tuple[str, ...]:
    return tuple(_catalogue())


def district_names(state: str) -> tuple[str, ...]:
    """Districts of `state`, matched without regard to casing, or ()."""
    districts = _catalogue().get(state)
    if districts is not None:
        return districts
    folded = state.casefold().strip()
    for name, names in _catalogue().items():
        if name.casefold() == folded:
            return names
    return ()


def canonical(state: str, district: str) -> tuple[str, str] | None:
    """The stored spelling of a known pair, or None when it is not in the list."""
    for name in _catalogue():
        if name.casefold() == state.casefold().strip():
            for value in _catalogue()[name]:
                if value.casefold() == district.casefold().strip():
                    return name, value
            return None
    return None


def as_choices() -> list[dict[str, object]]:
    """The whole list in the shape the PIN lookup endpoint returns it."""
    return [
        {"state": state, "districts": list(districts)}
        for state, districts in _catalogue().items()
    ]
