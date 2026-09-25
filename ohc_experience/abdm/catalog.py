from ohc_experience.experiences.definitions import MilestoneDefinition
from ohc_experience.experiences.definitions import SupportCategoryDefinition
from ohc_experience.experiences.definitions import TrackDefinition
from ohc_experience.organisations.models import GOVERNMENT

from .docs import docs_page

#: Where a product proves a person's identity: M1 on ABDM, P1 on PHR & Health
#: Locker. The two tracks cannot be applied for together, so a product has one.
IDENTITY_MILESTONES = ("m1", "p1")

MILESTONES = {
    item.key: item
    for item in (
        MilestoneDefinition(
            "m1",
            "M1",
            "ABHA Creation and Verification",
            description="Create and verify ABHA identities for people receiving care.",
            docs_url=docs_page("/docs/hiecm/v3/milestones/m1"),
        ),
        MilestoneDefinition(
            "m2",
            "M2",
            "Health Information Provider Services",
            "m1",
            "Link care contexts and share health records with consent.",
            docs_page("/docs/hiecm/v3/milestones/m2"),
            needs_callback=True,
        ),
        MilestoneDefinition(
            "m3",
            "M3",
            "Health Information User Services",
            "m1",
            "Request consent and retrieve health records held by other providers.",
            docs_page("/docs/hiecm/v3/milestones/m3"),
            needs_callback=True,
        ),
        MilestoneDefinition(
            "m4",
            "M4",
            "Register Healthcare Professionals and Facilities",
            "m1",
            description="Register your health facility and professionals for "
            "ABDM services.",
            docs_url=docs_page("/docs/hiecm/v3/milestones/m4"),
        ),
        MilestoneDefinition(
            "p1",
            "P1",
            "Identity and profile",
            description="Register people in a PHR application, sign them in and "
            "manage their profile.",
            docs_url=docs_page("/docs/hiecm/v3/milestones/p1"),
        ),
        MilestoneDefinition(
            "p2",
            "P2",
            "Linking and records",
            "p1",
            "Help people discover health records held elsewhere and link them "
            "to their ABHA.",
            docs_page("/docs/hiecm/v3/milestones/p2"),
            needs_callback=True,
        ),
        MilestoneDefinition(
            "p3",
            "P3",
            "Subscription flow",
            "p1",
            "Subscribe to a person's records, and let them grant and revoke consent.",
            docs_page("/docs/hiecm/v3/milestones/p3"),
            needs_callback=True,
        ),
        MilestoneDefinition(
            "p4",
            "P4",
            "Health locker",
            "p1",
            description="Store and retrieve personal health records in a health "
            "locker.",
            docs_url=docs_page(
                "/docs/hiecm/v3/concepts/phr#where-the-citizen-is-the-hip",
            ),
            needs_callback=True,
        ),
        MilestoneDefinition(
            "uhi1",
            "UHI1",
            "UHI participation",
            IDENTITY_MILESTONES,
            "Join the network for discovery, booking and delivery of health services.",
            docs_page("/docs/uhi/v1"),
            related=("m2",),
            needs_callback=True,
            stands_alone=True,
        ),
        MilestoneDefinition(
            "nhcx1",
            "NHCX1",
            "Claims exchange flows",
            IDENTITY_MILESTONES,
            "Exchange health insurance claims and pre-authorisation requests.",
            docs_page("/docs/nhcx/v1"),
            needs_callback=True,
        ),
    )
}


TRACKS = (
    TrackDefinition(
        "ABDM",
        "Milestones",
        "ABHA identity, health information exchange and facility registration.",
        ("m1", "m2", "m3", "m4"),
        docs_page("/docs/hiecm/v3"),
    ),
    TrackDefinition(
        "PHR",
        "PHR & Health Locker",
        "Personal health record application flows, and storage and retrieval "
        "of records in a health locker.",
        ("p1", "p2", "p3", "p4"),
        docs_page("/docs/hiecm/v3/milestones/p1"),
    ),
    TrackDefinition(
        "UHI",
        "Unified Health Interface",
        "UHI enables the discovery and delivery of digital health services. UHI "
        "onboarding requires M1 or P1, and M2 is suggested alongside it. These "
        "milestones may also be reused across other ABDM tracks where applicable.",
        ("uhi1",),
        docs_page("/docs/uhi/v1"),
    ),
    TrackDefinition(
        "NHCX",
        "National Health Claims Exchange",
        "Claims and pre-authorisation exchange between payers and providers.",
        ("nhcx1",),
        docs_page("/docs/nhcx/v1"),
    ),
)
TRACK_MAP = {track.code: track for track in TRACKS}

#: A product holds health records either as a provider or as a citizen's own
#: application, so it applies for one of these two tracks, never both.
EXCLUSIVE_TRACKS = ("ABDM", "PHR")


def milestone_predecessors(key, organisation=None):
    """Milestones that open this one, any one of which will do.

    A government body registers facilities and professionals under its own
    authority, so M4 opens for it without M1.
    """
    if key == "m4" and organisation and organisation.entity_type == GOVERNMENT:
        return ()
    return MILESTONES[key].predecessors


def excluded_track(selected):
    """The track these selections rule out, or "" when they rule out neither."""
    if "ABDM" in selected:
        return "PHR"
    if "PHR" in selected:
        return "ABDM"
    return ""

#: What a ticket is filed under, and what a support permission is granted for.
#: Finer than the tracks the same program is reviewed by, because the milestone
#: a question is about is what decides who can answer it. "Others" is the
#: catch-all for work that belongs to no track; it sits last so the milestones
#: lead and the catch-all is the fallback.
SUPPORT_CATEGORIES = (
    SupportCategoryDefinition(
        "abdm-m1",
        "ABDM - Milestone 1",
        "ABDM",
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
        "ABDM",
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
        "ABDM",
        ("Consent Management (Request)", "Data Request", "FHIR Bundle Decryption"),
    ),
    SupportCategoryDefinition(
        "abdm-m4",
        "ABDM - Milestone 4",
        "ABDM",
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
        "ABDM",
        ("Review of ABDM Milestones (M1/M2/M3/M4)",),
    ),
    SupportCategoryDefinition(
        "abdm-scan-share",
        "ABDM - Scan & Share",
        "ABDM",
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
    SupportCategoryDefinition(
        "phr-app-p4",
        "PHR App - P4",
        "PHR",
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
        "others",
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
    "phr": ("p1", "p2", "p3"),
    "health_locker": ("p1", "p2", "p3", "p4"),
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
