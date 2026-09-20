"""The offline state and district list: its shape, and how names are matched."""

from __future__ import annotations

import json
import re

import pytest

from ohc_experience.organisations import states
from ohc_experience.organisations.widgets import PincodeInput

EXPECTED_STATES = 36


def test_every_state_is_named_once_and_has_districts():
    names = states.state_names()
    assert len(names) == EXPECTED_STATES
    assert len(set(names)) == len(names)
    assert all(states.district_names(name) for name in names)


def test_the_data_file_is_sorted_and_free_of_stray_spacing():
    with states.DATA_FILE.open(encoding="utf-8") as handle:
        entries = json.load(handle)
    assert [entry["state"] for entry in entries] == sorted(
        entry["state"] for entry in entries
    )
    for entry in entries:
        assert entry["districts"] == sorted(set(entry["districts"]))
        for name in (entry["state"], *entry["districts"]):
            assert name == name.strip()
            assert "  " not in name


def test_no_name_carries_a_bracket_an_ampersand_or_an_html_entity():
    """Districts too: the source had a "Lahaul &amp; Spiti" that reached a page."""
    names = [
        name
        for state in states.state_names()
        for name in (state, *states.district_names(state))
    ]
    assert "Jammu and Kashmir" in names
    assert "Lahaul and Spiti" in names
    assert not [name for name in names if set(name) & set("(&;")]


@pytest.mark.parametrize(
    ("state", "district"),
    [
        ("Kerala", "Ernakulam"),
        ("KERALA", "ERNAKULAM"),
        ("  kerala  ", "  ernakulam  "),
    ],
)
def test_canonical_matches_without_regard_to_casing_or_padding(state, district):
    assert states.canonical(state, district) == ("Kerala", "Ernakulam")


@pytest.mark.parametrize(
    ("state", "district"),
    [
        ("Kerala", "Bengaluru Urban"),  # A real district, but of another state.
        ("Kerala", "Nowhere"),
        ("Atlantis", "Ernakulam"),
        ("", ""),
    ],
)
def test_canonical_rejects_a_pair_the_list_does_not_hold(state, district):
    assert states.canonical(state, district) is None


def test_district_names_is_empty_for_an_unknown_state():
    assert states.district_names("Atlantis") == ()


def test_the_pincode_widget_carries_the_list_into_the_page():
    """A failed lookup cannot fetch the names, so the page must already hold them."""
    rendered = PincodeInput().render("pincode", "", attrs={"id": "id_pincode"})
    assert 'data-offline-locations="id_pincode_offline_locations"' in rendered
    embedded = re.search(
        r'id="id_pincode_offline_locations"[^>]*>(.*?)</script>',
        rendered,
        re.DOTALL,
    )
    assert embedded, rendered
    payload = json.loads(embedded.group(1))
    assert {entry["state"] for entry in payload} == set(states.state_names())


def test_as_choices_carries_the_whole_list():
    choices = states.as_choices()
    assert len(choices) == EXPECTED_STATES
    assert {entry["state"] for entry in choices} == set(states.state_names())
    districts = {entry["state"]: entry["districts"] for entry in choices}
    assert districts["Kerala"] == list(states.district_names("Kerala"))
