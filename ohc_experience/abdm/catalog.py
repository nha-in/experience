from ohc_experience.experiences.definitions import MilestoneDefinition
from ohc_experience.experiences.definitions import TrackDefinition

MILESTONES = {
    item.key: item
    for item in (
        MilestoneDefinition("m1", "M1", "ABHA and identity"),
        MilestoneDefinition("m2", "M2", "HIP services", "m1"),
        MilestoneDefinition("m3", "M3", "HIU services", "m2"),
        MilestoneDefinition("m4", "M4", "HFR Registration", "m3"),
        MilestoneDefinition("phr1", "PHR1", "PHR application flows", "m1"),
        MilestoneDefinition("locker1", "PHR1", "Locker flows"),
        MilestoneDefinition("uhi1", "UHI1", "UHI participant flows"),
        # No predecessor on purpose: dependants are unlocked by the event of a
        # predecessor being approved, and NHCX can only ever be selected after M1
        # is already approved, so a predecessor would leave its exit locked for good.
        MilestoneDefinition("nhcx1", "NHCX1", "Claims exchange flows"),
    )
}


TRACKS = (
    TrackDefinition(
        "HI-CM",
        "Health information & consent management",
        "ABHA identity, health information exchange and facility registration.",
        ("m1", "m2", "m3", "m4"),
    ),
    TrackDefinition(
        "UHI",
        "Unified Health Interface",
        "Discovery and delivery of digital health services.",
        ("uhi1",),
    ),
    TrackDefinition(
        "NHCX",
        "National Health Claims Exchange",
        "Claims and pre-authorisation exchange between payers and providers.",
        ("nhcx1",),
    ),
    TrackDefinition(
        "PHR",
        "Personal Health Records",
        "ABHA identity and personal health record application flows. "
        "M1 is shared with HI-CM.",
        ("m1", "phr1"),
    ),
    TrackDefinition(
        "HealthLocker",
        "Health Locker",
        "Storage and retrieval of personal health records.",
        ("locker1",),
    ),
)
TRACK_MAP = {track.code: track for track in TRACKS}
# Tracks that stay unselectable until a milestone elsewhere is approved. NHCX
# needs a compliant PHR track, and PHR1 already depends on M1, so "M1 or a
# complete PHR track" reduces to M1 on its own.
TRACK_GATES = {"NHCX": "m1"}
MILESTONE_CHOICES = [
    (
        track.name,
        [
            (f"{track.code}:{key}", f"{MILESTONES[key].code} - {MILESTONES[key].name}")
            for key in track.keys
        ],
    )
    for track in TRACKS
    if track.keys
]


def canonical_keys(selections):
    return {value.split(":", 1)[1] for value in selections}
