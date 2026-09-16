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
        MilestoneDefinition("locker1", "HL1", "Health locker flows"),
        MilestoneDefinition("uhi1", "UHI1", "UHI participation", "m1"),
        MilestoneDefinition("nhcx1", "NHCX1", "Claims exchange flows", "m1"),
    )
}


TRACKS = (
    TrackDefinition(
        "HIE-CM",
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
        "ABHA identity and personal health record application flows.",
        ("phr1",),
    ),
    TrackDefinition(
        "HealthLocker",
        "Health Locker",
        "Storage and retrieval of personal health records.",
        ("locker1",),
    ),
)
TRACK_MAP = {track.code: track for track in TRACKS}
#: Milestones each solution type requires, from NHA's intent-for-request matrix.
#: Registration preselects them and warns when one is left unchecked, but still
#: saves. Types not listed require none.
REQUIRED_MILESTONES = {
    "hmis": ("m1", "m2", "m3"),
    "clinical_hmis": ("m1", "m2", "m3"),
    "healthtech": ("m1", "m2", "m3"),
    "lmis": ("m1", "m2", "m3"),
    "insurance": ("m1", "m3"),
    "telemedicine": ("m1", "m2", "m3"),
    "pharmacy": ("m1", "m2", "m3"),
}
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
