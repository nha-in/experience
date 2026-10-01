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
            description="Create an ABHA, the 14-digit health ID, for patients who "
            "have none, and verify the ones they bring. Also covers their profile, "
            "ABHA card and QR code, and Scan and Register at your counter.",
            docs_url=docs_page("/docs/hiecm/v3/milestones/m1"),
        ),
        MilestoneDefinition(
            "m2",
            "M2",
            "Health Information Provider Services",
            "m1",
            "As a Health Information Provider (HIP), link each visit's records to "
            "the patient's ABHA address, help them find older records from their "
            "app, and share records, encrypted, when they consent. Needs a "
            "facility ID, registered on the NHPR portal or through M4.",
            docs_page("/docs/hiecm/v3/milestones/m2"),
            needs_callback=True,
        ),
        MilestoneDefinition(
            "m3",
            "M3",
            "Health Information User Services",
            "m1",
            "As a Health Information User (HIU), ask patients for consent by their "
            "ABHA address, then fetch and decrypt the records they grant from other "
            "providers. Access must stop when consent is revoked or expires.",
            docs_page("/docs/hiecm/v3/milestones/m3"),
            needs_callback=True,
        ),
        MilestoneDefinition(
            "m4",
            "M4",
            "Register Healthcare Professionals and Facilities",
            "m1",
            description="From your software, register healthcare professionals on "
            "the Healthcare Professionals Registry (HPR) and facilities on the "
            "Health Facility Registry (HFR), then link the facility to your "
            "software. M2 and M3 need a facility ID to go live.",
            docs_url=docs_page("/docs/hiecm/v3/milestones/m4"),
        ),
        MilestoneDefinition(
            "p1",
            "P1",
            "Registration and login",
            description="Let people create an ABHA address in your PHR app, with a "
            "mobile number, ABHA number or Aadhaar, and sign in. Every login route "
            "is required.",
            docs_url=docs_page("/docs/hiecm/v3/milestones/p1"),
        ),
        MilestoneDefinition(
            "p2",
            "P2",
            "Consents Management",
            "p1",
            "Let people manage their profile, ABHA card and QR code, share their "
            "profile at a facility by scanning its QR code, and find and link "
            "records from facilities they have visited.",
            docs_page("/docs/hiecm/v3/milestones/p2"),
            needs_callback=True,
        ),
        MilestoneDefinition(
            "p3",
            "P3",
            "Subscription",
            "p1",
            "Let people grant, deny and revoke consent, get notified when a record "
            "is linked to their ABHA address, and fetch and show the records a "
            "consent covers.",
            docs_page("/docs/hiecm/v3/milestones/p3"),
            needs_callback=True,
        ),
        MilestoneDefinition(
            "p4",
            "P4",
            "Locker",
            ("p1", "p2", "p3"),
            description="Keep people's health records for the long term: set up a "
            "locker for each person, subscribe to their ABHA address, and fetch "
            "each new record with their consent.",
            docs_url=docs_page("/docs/hiecm/v3/milestones/p4"),
            needs_callback=True,
            requires_all=True,
        ),
        MilestoneDefinition(
            "uhi1",
            # NHA calls the milestone UHI; the key keeps its 1 for stored data.
            "UHI",
            "UHI participation",
            IDENTITY_MILESTONES,
            "Join the UHI network as a patient-facing app (EUA) or a provider "
            "platform (HSPA), to discover and book health services such as "
            "consultations, ambulances, blood banks and Jan Aushadhi medicines.",
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
            "Exchange insurance claims between hospitals and insurers on NHCX, a "
            "separate claims gateway: eligibility checks, pre-authorisation, "
            "claims, queries and payment notices.",
            docs_page("/docs/nhcx/v1"),
            needs_callback=True,
        ),
    )
}


TRACKS = (
    TrackDefinition(
        "ABDM",
        "Milestones",
        "ABHA identity, consent-based sharing of health records, and registering "
        "facilities and professionals.",
        ("m1", "m2", "m3", "m4"),
        docs_page("/docs/hiecm/v3"),
    ),
    TrackDefinition(
        "PHR",
        "PHR & Health Locker",
        "A person's own health record app: ABHA sign-up and login, finding and "
        "linking their records, managing consent, and keeping records in a "
        "health locker.",
        ("p1", "p2", "p3", "p4"),
        docs_page("/docs/hiecm/v3/concepts/participants/phr"),
    ),
    TrackDefinition(
        "UHI",
        "Unified Health Interface",
        "Discover and book health services, such as consultations, ambulances and "
        "Jan Aushadhi medicines, on the UHI network. UHI onboarding requires M1 or "
        "P1, and M2 is suggested alongside it. These milestones may also be reused "
        "across other ABDM tracks where applicable.",
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
