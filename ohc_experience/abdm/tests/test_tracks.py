"""The track and milestone registry NHA publishes, as code."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from ohc_experience.abdm import tracks

HI_CM_MILESTONE_COUNT = 4


def test_tracks_are_listed_in_sidebar_order():
    assert [track.code for track in tracks.TRACKS] == [
        "HI-CM",
        "UHI",
        "NHCX",
        "PHR",
        "HealthLocker",
    ]


def test_hi_cm_publishes_four_milestones_ending_with_hfr_registration():
    track = tracks.get_track("HI-CM")

    assert len(track.milestones) == HI_CM_MILESTONE_COUNT
    assert [item.code for item in track.milestones] == ["M1", "M2", "M3", "M4"]
    assert track.milestones[-1].name == "HFR registration"


def test_phr_first_milestone_is_hi_cm_m1():
    milestone = tracks.get_milestone("PHR", "M1")

    assert milestone.is_alias
    assert milestone.key == "PHR:M1"
    assert milestone.canonical_key == "HI-CM:M1"
    assert tracks.canonical_key("PHR:M1") == "HI-CM:M1"


def test_nhcx_has_no_published_milestones():
    assert tracks.get_track("NHCX").has_milestones is False


def test_unknown_track_and_milestone_are_refused():
    with pytest.raises(ValidationError):
        tracks.get_track("ABC")
    with pytest.raises(ValidationError):
        tracks.get_milestone("HI-CM", "M9")
    with pytest.raises(ValidationError):
        tracks.parse_key("HI-CM")


def test_previous_milestone_follows_track_order():
    track = tracks.get_track("HI-CM")

    assert tracks.previous_milestone(track, track.milestones[0]) is None
    assert tracks.previous_milestone(track, track.milestones[2]).code == "M2"


def test_selection_requires_earlier_milestones_first():
    with pytest.raises(ValidationError, match="M2 on HI-CM needs M1"):
        tracks.validate_selection({"HI-CM:M2"})
    with pytest.raises(ValidationError, match="PHR1 on PHR needs M1"):
        tracks.validate_selection({"PHR:PHR1"})


def test_selection_resolves_aliases_and_applied_tracks():
    canonical, applied = tracks.validate_selection({"PHR:M1", "PHR:PHR1"})

    assert canonical == {"HI-CM:M1", "PHR:PHR1"}
    assert applied == {"PHR", "HI-CM"}


def test_hi_cm_m1_satisfies_phr_first_tile():
    canonical, applied = tracks.validate_selection({"HI-CM:M1", "PHR:PHR1", "UHI:UHI1"})

    assert canonical == {"HI-CM:M1", "PHR:PHR1", "UHI:UHI1"}
    assert applied == {"HI-CM", "PHR", "UHI"}


def test_docs_urls_hang_off_the_configured_base(settings):
    settings.ABDM_DOCS_URL = "https://docs.example.test/"

    assert tracks.get_track("HI-CM").docs_url == "https://docs.example.test/hi-cm"
    assert (
        tracks.get_milestone("HI-CM", "M2").docs_url
        == "https://docs.example.test/hi-cm/m2"
    )
