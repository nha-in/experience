from ohc_experience.experiences.definitions import MilestoneDefinition
from ohc_experience.experiences.definitions import TrackDefinition

MILESTONES = {
    item.key: item
    for item in (
        MilestoneDefinition(
            "m1",
            "M1",
            "ABHA and identity",
            description="Create and verify ABHA identities for people receiving care.",
            docs_url="https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones/m1",
        ),
        MilestoneDefinition(
            "m2",
            "M2",
            "HIP services",
            "m1",
            "Link care contexts and share health records with consent.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones/m2",
        ),
        MilestoneDefinition(
            "m3",
            "M3",
            "HIU services",
            "m1",
            "Request consent and retrieve health records held by other providers.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones/m3",
        ),
        MilestoneDefinition(
            "m4",
            "M4",
            "HFR Registration",
            description="Register your health facility and professionals for ABDM services.",
            docs_url="https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones/m4",
        ),
        MilestoneDefinition(
            "phr1",
            "PHR1",
            "PHR application flows",
            "m1",
            "Help people discover, link and control access to their health records.",
            "https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones/p1",
        ),
        MilestoneDefinition(
            "locker1",
            "HL1",
            "Health locker flows",
            description="Store and retrieve personal health records in a health locker.",
            docs_url="https://abdm-docs.dev.eka.care/docs/hiecm/v3/getting-started/glossary?#health-locker",
        ),
        MilestoneDefinition(
            "uhi1",
            "UHI1",
            "UHI participation",
            "m2",
            "Join the network for discovery, booking and delivery of health services.",
            "https://abdm-docs.dev.eka.care/docs/uhi/v1",
        ),
        MilestoneDefinition(
            "nhcx1",
            "NHCX1",
            "Claims exchange flows",
            "m1",
            "Exchange health insurance claims and pre-authorisation requests.",
            "https://abdm-docs.dev.eka.care/docs/nhcx/v1",
        ),
    )
}


TRACKS = (
    TrackDefinition(
        "HIE-CM",
        "Health information & consent management",
        "ABHA identity, health information exchange and facility registration.",
        ("m1", "m2", "m3", "m4"),
        "https://abdm-docs.dev.eka.care/docs/hiecm/v3/getting-started/glossary?#hmis-his-hims",
    ),
    TrackDefinition(
        "UHI",
        "Unified Health Interface",
        "Discovery and delivery of digital health services.",
        ("uhi1",),
        "https://abdm-docs.dev.eka.care/docs/uhi/v1",
    ),
    TrackDefinition(
        "NHCX",
        "National Health Claims Exchange",
        "Claims and pre-authorisation exchange between payers and providers.",
        ("nhcx1",),
        "https://abdm-docs.dev.eka.care/docs/nhcx/v1",
    ),
    TrackDefinition(
        "PHR",
        "Personal Health Records",
        "ABHA identity and personal health record application flows.",
        ("phr1",),
        "https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones/p1",
    ),
    TrackDefinition(
        "HealthLocker",
        "Health Locker",
        "Storage and retrieval of personal health records.",
        ("locker1",),
        "https://abdm-docs.dev.eka.care/docs/hiecm/v3/getting-started/glossary?#health-locker",
    ),
)
TRACK_MAP = {track.code: track for track in TRACKS}
#: Milestones each solution type requires, from NHA's intent-for-request matrix.
#: Registration preselects them and warns when one is left unchecked, but still
#: saves. Types not listed require none.
REQUIRED_MILESTONES = {
    "hmis": ("m1", "m2", "m3", "m4"),
    "clinical_hmis": ("m1", "m2", "m3", "m4"),
    "lmis": ("m1", "m2", "m3", "m4"),
    "pharmacy": ("m1", "m2", "m3", "m4"),
    "phr": ("phr1", "locker1"),
    "health_locker": ("locker1",),
    "healthtech": ("m1", "m2", "m3", "m4"),
    "insurance": ("m1", "m3"),
    "telemedicine": ("m1", "m2", "m3", "m4"),
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
