"""The ABDM compliance tracks and their milestones, as NHA publishes them.

Tracks and milestones are code, not rows: NHA publishes them, integrators pick
from them, and nothing in the portal can invent one. A milestone is identified
by its track and code — ``HI-CM:M2`` — and that pair is what a compliance
record is keyed on.

One milestone is shared. PHR's first milestone *is* HI-CM's M1 (decision 2 in
the design doc), so the PHR tile for M1 is an alias: it resolves to the
``HI-CM:M1`` record and there is never a second record for it. ``canonical_key``
is the identity every other module works with; ``key`` is only what a tile or
a checkbox is labelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass(frozen=True)
class Milestone:
    code: str
    name: str
    track_code: str
    order: int
    description: str = ""
    docs_path: str = ""
    # (track_code, code) of the record this tile stands for, when it is another
    # track's milestone shown here.
    alias_of: tuple[str, str] | None = None

    @property
    def key(self) -> str:
        """How the tile is addressed on its own track: ``PHR:M1``."""
        return f"{self.track_code}:{self.code}"

    @property
    def canonical(self) -> tuple[str, str]:
        return self.alias_of or (self.track_code, self.code)

    @property
    def canonical_key(self) -> str:
        """The record's identity: ``HI-CM:M1`` even when reached through PHR."""
        track_code, code = self.canonical
        return f"{track_code}:{code}"

    @property
    def is_alias(self) -> bool:
        return self.alias_of is not None

    @property
    def label(self) -> str:
        return f"{self.code} · {self.name}"

    @property
    def docs_url(self) -> str:
        base = settings.ABDM_DOCS_URL.rstrip("/")
        return f"{base}/{self.docs_path}" if self.docs_path else base


@dataclass(frozen=True)
class Track:
    code: str
    name: str
    description: str
    docs_path: str
    milestones: tuple[Milestone, ...]

    @property
    def has_milestones(self) -> bool:
        return bool(self.milestones)

    @property
    def docs_url(self) -> str:
        base = settings.ABDM_DOCS_URL.rstrip("/")
        return f"{base}/{self.docs_path}" if self.docs_path else base

    def get_milestone(self, code: str) -> Milestone | None:
        return next((item for item in self.milestones if item.code == code), None)


HI_CM = Track(
    code="HI-CM",
    name=str(_("Health information exchange and consent manager")),
    description=str(
        _(
            "Consented exchange of health records between providers and users "
            "through the ABDM gateway, starting with ABHA verification.",
        ),
    ),
    docs_path="hi-cm",
    milestones=(
        Milestone(
            "M1",
            str(_("ABHA and identity")),
            "HI-CM",
            1,
            str(_("ABHA creation, verification and profile flows.")),
            "hi-cm/m1",
        ),
        Milestone(
            "M2",
            str(_("HIP services")),
            "HI-CM",
            2,
            str(_("Care-context linking and consented health record sharing.")),
            "hi-cm/m2",
        ),
        Milestone(
            "M3",
            str(_("HIU services")),
            "HI-CM",
            3,
            str(_("Consent requests and consented health record access.")),
            "hi-cm/m3",
        ),
        Milestone(
            "M4",
            str(_("HFR registration")),
            "HI-CM",
            4,
            str(_("Health facility registry integration and facility onboarding.")),
            "hi-cm/m4",
        ),
    ),
)

UHI = Track(
    code="UHI",
    name=str(_("Unified health interface")),
    description=str(
        _(
            "Discovery and fulfilment of health services between end-user "
            "applications and health service providers.",
        ),
    ),
    docs_path="uhi",
    milestones=(
        Milestone(
            "UHI1",
            str(_("UHI participant flows")),
            "UHI",
            1,
            str(_("Search, select, book and fulfil flows for a UHI participant.")),
            "uhi/uhi1",
        ),
    ),
)

NHCX = Track(
    code="NHCX",
    name=str(_("National health claims exchange")),
    description=str(
        _(
            "Claims exchange between payers, providers and TPAs. NHA has not "
            "published milestones for this track yet.",
        ),
    ),
    docs_path="nhcx",
    milestones=(),
)

PHR = Track(
    code="PHR",
    name=str(_("Personal health records")),
    description=str(
        _(
            "PHR applications that let citizens link, view and share their "
            "health records. M1 is HI-CM's M1: one approval covers both tracks.",
        ),
    ),
    docs_path="phr",
    milestones=(
        Milestone(
            "M1",
            str(_("ABHA and identity")),
            "PHR",
            1,
            str(_("ABHA creation, verification and profile flows.")),
            "hi-cm/m1",
            alias_of=("HI-CM", "M1"),
        ),
        Milestone(
            "PHR1",
            str(_("PHR application flows")),
            "PHR",
            2,
            str(_("Linking, viewing and sharing records from a PHR application.")),
            "phr/phr1",
        ),
    ),
)

HEALTH_LOCKER = Track(
    code="HealthLocker",
    name=str(_("Health locker")),
    description=str(
        _(
            "Long-term storage of a citizen's health records with consent-based "
            "access.",
        ),
    ),
    docs_path="health-locker",
    milestones=(
        Milestone(
            "PHR1",
            str(_("Locker flows")),
            "HealthLocker",
            1,
            str(_("Subscription, auto-approval and locker record flows.")),
            "health-locker/phr1",
        ),
    ),
)

# Sidebar order, per the design doc.
TRACKS: tuple[Track, ...] = (HI_CM, UHI, NHCX, PHR, HEALTH_LOCKER)
# The product form's order: the tracks with milestones first, NHCX last.
FORM_TRACKS: tuple[Track, ...] = (HI_CM, PHR, HEALTH_LOCKER, UHI, NHCX)
TRACKS_BY_CODE: dict[str, Track] = {track.code: track for track in TRACKS}
TRACK_CHOICES: list[tuple[str, str]] = [(track.code, track.code) for track in TRACKS]
# What a new product applies for until the integrator says otherwise: every
# ABDM integration starts with ABHA, and PHR's first tile is this same record.
DEFAULT_MILESTONE_KEY = HI_CM.milestones[0].key


def get_track(code: str) -> Track:
    try:
        return TRACKS_BY_CODE[code]
    except KeyError as exc:
        msg = _("Unknown track: %(code)s") % {"code": code}
        raise ValidationError(msg) from exc


def get_milestone(track_code: str, code: str) -> Milestone:
    milestone = get_track(track_code).get_milestone(code)
    if milestone is None:
        msg = _("Unknown milestone: %(track)s %(code)s") % {
            "track": track_code,
            "code": code,
        }
        raise ValidationError(msg)
    return milestone


def parse_key(key: str) -> tuple[str, str]:
    track_code, separator, code = str(key).partition(":")
    if not separator or not code:
        msg = _("Unknown milestone: %(key)s") % {"key": key}
        raise ValidationError(msg)
    return track_code, code


def canonical_key(key: str) -> str:
    track_code, code = parse_key(key)
    return get_milestone(track_code, code).canonical_key


def milestone_label(track_code: str, code: str) -> str:
    return get_milestone(track_code, code).label


def previous_milestone(track: Track, milestone: Milestone) -> Milestone | None:
    """The milestone that has to be approved before this one opens."""
    index = track.milestones.index(milestone)
    return track.milestones[index - 1] if index else None


def validate_selection(keys: Iterable[str]) -> tuple[set[str], set[str]]:
    """Resolve a checkbox selection to canonical record keys and applied tracks.

    Milestones open in order, so a later milestone cannot be applied for
    without the ones before it on the same track. The check runs on canonical
    keys, which is what lets ``HI-CM:M1`` satisfy PHR's first tile.
    """
    canonical: set[str] = set()
    tracks: set[str] = set()
    for key in keys:
        track_code, code = parse_key(key)
        milestone = get_milestone(track_code, code)
        canonical.add(milestone.canonical_key)
        tracks.add(track_code)
        tracks.add(milestone.canonical[0])
    for track_code in sorted(tracks):
        track = get_track(track_code)
        gap: Milestone | None = None
        for milestone in track.milestones:
            if milestone.canonical_key in canonical:
                if gap is not None:
                    msg = _(
                        "%(later)s on %(track)s needs %(earlier)s applied first.",
                    ) % {
                        "later": milestone.code,
                        "track": track.code,
                        "earlier": gap.code,
                    }
                    raise ValidationError(msg)
            elif gap is None:
                gap = milestone
    return canonical, tracks
