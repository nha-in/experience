from ohc_experience.experiences.definitions import MilestoneDefinition
from ohc_experience.experiences.definitions import SupportCategoryDefinition
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
            description="Register your health facility and professionals for "
            "ABDM services.",
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
            description="Store and retrieve personal health records in a health "
            "locker.",
            docs_url="https://abdm-docs.dev.eka.care/docs/hiecm/v3/concepts/phr#where-the-citizen-is-the-hip",
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
        "https://abdm-docs.dev.eka.care/docs/hiecm/v3",
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
        "https://abdm-docs.dev.eka.care/docs/hiecm/v3/concepts/phr#where-the-citizen-is-the-hip",
    ),
)
TRACK_MAP = {track.code: track for track in TRACKS}

#: What a ticket is filed under, and what a support permission is granted for.
#: Finer than the tracks the same program is reviewed by, because the milestone
#: a question is about is what decides who can answer it. "Others" is the blank
#: category the portal has always used for work that belongs to no track; it
#: sits last so the milestones lead and the catch-all is the fallback.
SUPPORT_CATEGORIES = (
    SupportCategoryDefinition(
        "abdm-m1",
        "ABDM - Milestone 1",
        "HIE-CM",
        (
            "ABHA Creation",
            "ABHA Verification",
            "Get ABHA Card",
            "Get ABHA Profile",
            "Profile Update",
        ),
    ),
    SupportCategoryDefinition(
        "abdm-m2",
        "ABDM - Milestone 2",
        "HIE-CM",
        (
            "Bridge Service",
            "HIP Initiated Linking",
            "Discovery Flow",
            "Data Transfer",
            "PHR Bundle / Encryption",
        ),
    ),
    SupportCategoryDefinition(
        "abdm-m3",
        "ABDM - Milestone 3",
        "HIE-CM",
        ("Consent Management (Request)", "Data Request", "FHIR Bundle Decryption"),
    ),
    SupportCategoryDefinition(
        "abdm-m4",
        "ABDM - Milestone 4",
        "HIE-CM",
        (
            "Creation - HPR",
            "Creation - HFR",
            "Search - Professional",
            "Search - Facility",
        ),
    ),
    SupportCategoryDefinition(
        "abdm-review",
        "ABDM - Review (demo)",
        "HIE-CM",
        ("Review of ABDM Milestones (M1/M2/M3/M4)",),
    ),
    SupportCategoryDefinition(
        "abdm-scan-share",
        "ABDM - Scan & Share",
        "HIE-CM",
        ("Profile On Share",),
    ),
    SupportCategoryDefinition(
        "phr-app-p1",
        "PHR App - P1",
        "PHR",
        ("Registration Flow", "Login Flow"),
    ),
    SupportCategoryDefinition(
        "phr-app-p2",
        "PHR App - P2",
        "PHR",
        (
            "Health Facility Record Linking",
            "Health Program Record Linking",
            "Consent Flow",
            "Scan and Register",
        ),
    ),
    SupportCategoryDefinition(
        "phr-app-p3",
        "PHR App - P3",
        "PHR",
        ("HIU Services",),
    ),
    # The locker is its own track, so a product that applied for the locker
    # alone still reaches this category, and a PHR app reaches it too.
    SupportCategoryDefinition(
        "phr-app-p4",
        "PHR App - P4",
        "HealthLocker",
        ("Health Locker",),
    ),
    SupportCategoryDefinition(
        "nhcx-auth",
        "NHCX - Authentication & Access",
        "NHCX",
        ("Login/Token Issues", "Role & Permission Errors", "Header Problems"),
    ),
    SupportCategoryDefinition(
        "nhcx-workflow",
        "NHCX - Workflow & Business Logic",
        "NHCX",
        ("Request/Response Error", "Status Errors"),
    ),
    SupportCategoryDefinition(
        "nhcx-data",
        "NHCX - Data & Payload",
        "NHCX",
        (
            "Invalid Payload Format",
            "Data type mismatch",
            "Encryption/Decryption Errors",
            "Payload Size Issue",
        ),
    ),
    SupportCategoryDefinition(
        "uhi-integration",
        "UHI - Integration / APIs",
        "UHI",
        (
            "Sandbox / Production Access",
            "API Credentials & Authentication",
            "API Callback issues",
        ),
    ),
    SupportCategoryDefinition(
        "uhi-service",
        "UHI - Service",
        "UHI",
        (
            (
                "Facility & Hospital Discovery "
                "(PMJAY Hospital, NOTTO Hospital, Blood Bank)"
            ),
            "Medicine & Pharmacy Discovery (Jan Aushadhi Kendra, AMRIT Pharmacy)",
            "Emergency & Ambulance Booking",
            "Doctor Consultations Booking",
        ),
    ),
    SupportCategoryDefinition(
        "",
        "Others",
        issue_types=("Access / General Inquiry / Concerns",),
        description="Anything the other categories do not cover",
    ),
)
SUPPORT_CATEGORY_MAP = {category.code: category for category in SUPPORT_CATEGORIES}

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
