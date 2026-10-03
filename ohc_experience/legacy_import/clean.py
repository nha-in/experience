"""Turning legacy strings into the values experience stores."""

import html
import re
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
EARLIEST = datetime(1970, 1, 1, tzinfo=UTC)

NULLISH = {"", "na", "n/a", "#n/a", "nil", "none", "null", "-", "no", "not applicable"}
INVISIBLE = (*range(0x200B, 0x2010), 0x2060, 0xFEFF, 0xA0)
ZERO_WIDTH = re.compile("[" + "".join(map(chr, INVISIBLE)) + "]")
MOBILE_DIGITS = 10
#: Django's default `URLField` length, which every URL the import writes lands in.
#: A longer one is dropped rather than cut: half a URL is worse than none.
URL_LIMIT = 200

#: Legacy's one PHR milestone covered the whole application, which the portal
#: splits into three phases, so a legacy PHR claim stands for all three.
PHR_PHASES = ("p1", "p2", "p3")
#: NHCX is a choice of role. Legacy recorded no role on a declaration or an exit,
#: so a claim reads as the provider role and `nhcx_role` moves it when the
#: registration's own words name a payer or a patient app.
NHCX_DEFAULT_ROLE = "nhcx_provider"
NHCX_ROLES = ("nhcx_payer", "nhcx_provider", "nhcx_patient_app")
#: What legacy's payer category calls an insurer, beside its own "NA".
PAYER_WORDS = ("payer", "tpa", "insurance company")
MILESTONE_TOKENS = {
    "m1": ("m1",),
    "milestone1": ("m1",),
    "m2": ("m2",),
    "milestone2": ("m2",),
    "m3": ("m3",),
    "milestone3": ("m3",),
    "m4": ("m4",),
    "milestone4": ("m4",),
    "phr": PHR_PHASES,
    "healthlocker": ("p4",),
    "nhcx": (NHCX_DEFAULT_ROLE,),
}

#: The tracks the registration form offered, under each era's names. Health
#: Repository Provider has no track in the portal.
REGISTRATION_TRACKS = {
    "hip": ("m2",),
    "hiu": ("m3",),
    "hrp": (),
    "health_locker": ("p4",),
    "health info provider": ("m2",),
    "health info user": ("m3",),
    "health repository provider": (),
    "health locker": ("p4",),
    "phr app": PHR_PHASES,
    "eua": ("uhi1",),
    "hspa": ("uhi1",),
    "end user applications (eua)": ("uhi1",),
    "health service provider application (hspa)": ("uhi1",),
    "payers": ("nhcx_payer",),
    "providers": ("nhcx_provider",),
    "abha creation/verification - m1": ("m1",),
    "building health information provider (hip) - m2": ("m2",),
    "building health information user (hiu) - m3": ("m3",),
    "national healthcare provider registry (hpr/hfr) - m4": ("m4",),
}
_ROLES_2021 = frozenset(
    {
        "health info provider",
        "health info user",
        "health repository provider",
        "health locker",
        "phr app",
    },
)
_TRACKS_2024 = frozenset(
    {
        "abha creation/verification - m1",
        "building health information provider (hip) - m2",
        "building health information user (hiu) - m3",
        "end user applications (eua)",
        "health service provider application (hspa)",
        "health locker",
        "phr app",
        "payers",
        "providers",
    },
)
#: Each era's entire option list, which a registration saved in place of an answer.
WHOLE_TRACK_LISTS = {
    frozenset({"hip", "hiu", "hrp", "health_locker"}),
    _ROLES_2021,
    _ROLES_2021 | {"eua", "hspa"},
    _ROLES_2021 | {"eua", "hspa", "payers", "providers"},
    _TRACKS_2024,
    _TRACKS_2024 | {"national healthcare provider registry (hpr/hfr) - m4"},
}

#: An NHCX role builds on M2, M3 or P1, so the roles come after all of them.
MILESTONE_ORDER = [
    "m1",
    "p1",
    "m2",
    "p2",
    "p3",
    "p4",
    "uhi1",
    "m3",
    "m4",
    *NHCX_ROLES,
]
MILESTONE_TRACK = {
    "m1": "ABDM",
    "m2": "ABDM",
    "m3": "ABDM",
    "m4": "ABDM",
    "uhi1": "UHI",
    "nhcx_payer": "NHCX",
    "nhcx_provider": "NHCX",
    "nhcx_patient_app": "NHCX",
    "p1": "PHR",
    "p2": "PHR",
    "p3": "PHR",
    "p4": "PHR",
}
DECLARATION_DATES = {
    "m1": "m1",
    "m2": "m2",
    "m3": "m3",
    "m4": "m4",
    "p1": "phr",
    "p2": "phr",
    "p3": "phr",
    "p4": "health_locker",
    "nhcx_payer": "nhcx",
    "nhcx_provider": "nhcx",
    "nhcx_patient_app": "nhcx",
}

#: Legacy names onto the solution types the form offers. A government variant
#: keeps its own type and adds Government Programme, which the list now carries
#: separately, and a payer is an insurer.
SOLUTION_TYPES = {
    "clinic hmis": ["clinical_hmis"],
    "hospital": ["clinical_hmis"],
    "hmis": ["hmis"],
    "govt hmis": ["hmis", "govt_program"],
    "lmis": ["lmis"],
    "phr": ["phr"],
    "phr app": ["phr"],
    "govt phr": ["phr", "govt_program"],
    "health locker": ["health_locker"],
    "government program": ["govt_program"],
    "govt program": ["govt_program"],
    "central government program": ["govt_program"],
    "state government program": ["govt_program"],
    "governmentprogram": ["govt_program"],
    "healthtech": ["healthtech"],
    "health tech": ["healthtech"],
    "insurance": ["insurance"],
    "payers": ["insurance"],
    "payer": ["insurance"],
    "payers & providers": ["hmis"],
    "providers": ["hmis"],
    "pharmacy": ["pharmacy"],
    "telemedicine": ["telemedicine"],
    "others": ["other"],
    "other": ["other"],
}
#: Types the consolidated list dropped. They are named in the Other box rather
#: than folded into a type the integrator never chose.
RETIRED_SOLUTION_TYPES = {
    "end user applications (eua)": "End user application (EUA)",
    "eua": "End user application (EUA)",
}
SOLUTION_ORDER = [
    "hmis",
    "clinical_hmis",
    "lmis",
    "pharmacy",
    "phr",
    "health_locker",
    "healthtech",
    "insurance",
    "telemedicine",
    "govt_program",
    "other",
]
#: Each era of the legacy form, as its complete list of options. A row holding
#: one of these whole is the form's own list saved in place of an answer, in the
#: form's own order, not a product that is genuinely all of these at once.
#: Legacy kept no other record of what was meant, so it is read as unanswered.
LEGACY_OPTION_SETS = (
    frozenset(
        {
            "clinic hmis",
            "end user applications (eua)",
            "govt program",
            "govt hmis",
            "govt phr",
            "health locker",
            "healthtech",
            "hmis",
            "insurance",
            "lmis",
            "payers",
            "pharmacy",
            "phr",
            "providers",
            "telemedicine",
        },
    ),
    frozenset(
        {
            "hmis",
            "lmis",
            "telemedicine",
            "phr app",
            "insurance",
            "health locker",
            "government program",
            "pharmacy",
        },
    ),
    frozenset(
        {
            "hmis",
            "lmis",
            "telemedicine",
            "phr app",
            "insurance",
            "health locker",
            "government program",
        },
    ),
)
#: What the registration form's Other box takes.
OTHER_LIMIT = 255

ENTITY_TYPES = {
    "company": "private_company",
    "company / organization": "private_company",
    "proprietorship firm": "sole_proprietor",
    "firm": "sole_proprietor",
    "partnership firm": "partnership",
    "llp": "llp",
    "government": "government",
    "trust": "trust",
    "society": "trust",
}

UHI_ROLES = {
    "end user applications (eua)": "eua",
    "health service provider application (hspa)": "hspa",
}
UHI_SERVICES = {
    "blood bank discovery": "blood_bank_discovery",
    "physical consultation": "physical_consultation",
    "teleconsultation": "teleconsultation",
    "pmjay hem find hospital": "pmjay_hem_find_hospital",
}

STATE_CODES = {
    "1": "Jammu and Kashmir",
    "2": "Himachal Pradesh",
    "3": "Punjab",
    "4": "Chandigarh",
    "5": "Uttarakhand",
    "6": "Haryana",
    "7": "Delhi",
    "8": "Rajasthan",
    "9": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "27": "Maharashtra",
    "28": "Andhra Pradesh",
    "29": "Karnataka",
    "30": "Goa",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "36": "Telangana",
}

GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PINCODE = re.compile(r"^[1-9][0-9]{5}$")
WEBMAIL = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.in",
    "yahoo.co.in",
    "ymail.com",
    "rocketmail.com",
    "outlook.com",
    "outlook.in",
    "hotmail.com",
    "hotmail.co.in",
    "live.com",
    "live.in",
    "msn.com",
    "rediffmail.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    "mail.com",
    "yandex.com",
    "zohomail.in",
}
GENERIC_SITES = {
    "google.com",
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "play.google.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "gmail.com",
    "example.com",
    "test.com",
    "abc.com",
}
LEGAL_WORDS = {
    "pvt",
    "private",
    "ltd",
    "limited",
    "llp",
    "opc",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "llc",
    "plc",
    "gmbh",
    "the",
    "m",
    "s",
    "ms",
}
TEST_NAME = re.compile(r"\b(test|testing|dummy|demo|sample)\b", re.IGNORECASE)
TEST_EMAIL = re.compile(r"(test|dummy)", re.IGNORECASE)
TEST_ORG = re.compile(r"\b(test|testing|dummy)\b", re.IGNORECASE)


def text(value):
    if value is None:
        return ""
    value = ZERO_WIDTH.sub(" ", html.unescape(str(value)))
    return re.sub(r"\s+", " ", value).strip()


def paragraph(value):
    if value is None:
        return ""
    value = ZERO_WIDTH.sub(" ", html.unescape(str(value)))
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def present(value):
    cleaned = text(value)
    return "" if cleaned.lower() in NULLISH else cleaned


def email(value):
    return text(value).lower()


def mobile(value):
    digits = re.sub(r"[^0-9]", "", text(value))
    return digits[-MOBILE_DIGITS:] if len(digits) >= MOBILE_DIGITS else ""


def tokens(value):
    return [
        token.strip()
        for token in re.split(r",|<br\s*/?>", text(value), flags=re.IGNORECASE)
        if token.strip()
    ]


def milestone_keys(value):
    keys = []
    for token in tokens(value):
        for key in MILESTONE_TOKENS.get(re.sub(r"[^a-z0-9]", "", token.lower()), ()):
            if key not in keys:
                keys.append(key)
    return keys


def ordered_milestones(keys):
    return [key for key in MILESTONE_ORDER if key in keys]


def registration_tracks(value):
    """The tracks a registration picked; none when it saved its era's whole list."""
    picked = frozenset(token.lower() for token in tokens(value))
    if picked in WHOLE_TRACK_LISTS:
        return []
    return ordered_milestones(
        {key for token in picked for key in REGISTRATION_TRACKS.get(token, ())},
    )


def every_option(*values):
    """Whether legacy saved its whole list of options in place of an answer.

    Ticking Other alongside the list is the same non-answer, so the Other
    tokens are set aside before the comparison.
    """
    chosen = {
        token.lower()
        for value in values
        for token in tokens(value)
        if token.lower() not in NULLISH and token.lower() not in {"other", "others"}
    }
    return any(chosen == option_set for option_set in LEGACY_OPTION_SETS)


def solution_types(*values):
    """The types the form offers, and the names of those it no longer does.

    A legacy type with no home here is not guessed at: it is named, and the
    caller puts those words in the Other box beside an Other tick.
    """
    found, unlisted = set(), []
    for value in values:
        for token in tokens(value):
            key = token.lower()
            if key in NULLISH:
                continue
            slugs = SOLUTION_TYPES.get(key)
            found.update(slugs or ())
            name = RETIRED_SOLUTION_TYPES.get(key) or ("" if slugs else token)
            if name and name not in unlisted:
                unlisted.append(name)
    return [slug for slug in SOLUTION_ORDER if slug in found], unlisted


def other_solution_type(unlisted, *values):
    """What the Other box says: the dropped types, then legacy's own words."""
    names = list(unlisted)
    for value in values:
        candidate = present(value)
        if candidate and candidate not in names:
            names.append(candidate)
    return "; ".join(names)[:OTHER_LIMIT]


def entity_type(value):
    cleaned = text(value).lower()
    if "section 8" in cleaned:
        return "section8"
    return ENTITY_TYPES.get(cleaned, "")


def gstin(*values):
    for value in values:
        candidate = re.sub(r"\s", "", text(value)).upper()
        if GSTIN.match(candidate):
            return candidate
    return ""


def pincode(value):
    candidate = text(value)
    return candidate if PINCODE.match(candidate) else ""


def state_name(value):
    cleaned = present(value)
    return STATE_CODES.get(cleaned, cleaned)


def website(*values):
    for value in values:
        candidate = present(value)
        if not candidate or " " in candidate or "." not in candidate:
            continue
        if not re.match(r"^https?://", candidate, flags=re.IGNORECASE):
            candidate = f"https://{candidate}"
        if len(candidate) <= URL_LIMIT:
            return candidate
    return ""


def site_domain(value):
    candidate = present(value).lower()
    candidate = re.sub(r"^(https?://)?(www\.)?", "", candidate)
    candidate = candidate.split("/")[0].strip(".")
    return "" if candidate in GENERIC_SITES or "." not in candidate else candidate


def https_url(value):
    candidate = present(value)
    if re.match(r"^https?://\S+$", candidate) and len(candidate) <= URL_LIMIT:
        return candidate
    return ""


def names_a_payer(value):
    """Whether legacy's payer category names an insurer rather than its own "NA"."""
    said = present(value).lower()
    return any(word in said for word in PAYER_WORDS)


def name_key(value):
    return re.sub(r"[^a-z0-9]", "", text(value).lower())


def name_key_words(value):
    return [word for word in re.split(r"[^a-z0-9]+", text(value).lower()) if word]


def variant_key(value):
    lowered = re.sub(r"&amp;|&", " and ", text(value).lower())
    return "".join(
        word
        for word in re.split(r"[^a-z0-9]+", lowered)
        if word and word not in LEGAL_WORDS
    )


#: A WASA runs a year from its audit, as NHA confirmed, counted inclusively.
WASA_YEAR_DAYS = 364


def lapsed_wasa_expiry(uploaded, today):
    """The latest expiry an undated certificate could have had, once that is past.

    An audit comes before its certificate is uploaded, so a year after the upload
    is the longest it can have run.
    """
    if not uploaded:
        return None
    expiry = uploaded + timedelta(days=WASA_YEAR_DAYS)
    return expiry if expiry < today else None


def ist_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(IST).date()
    if isinstance(value, date):
        return value
    return None


def when(value):
    return value or EARLIEST


def aware(value):
    """Legacy wrote some timestamps as IST wall-clock time without a zone."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value


def iso(value):
    return value.isoformat() if value else None


def is_test_account(name, address, organisation):
    local_part = address.split("@", 1)[0]
    return bool(
        TEST_NAME.search(name or "")
        or TEST_EMAIL.search(local_part)
        or TEST_ORG.search(organisation or ""),
    )


def sniff_content_type(head):
    if head.startswith(b"%PDF"):
        return "application/pdf", "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if head.startswith(b"PK\x03\x04"):
        return (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        )
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "application/vnd.ms-excel", "xls"
    return "application/octet-stream", "bin"


# Legacy reviewers typed a remark where the portal offers a list. That list,
# `abdm/reject_reasons.py`, was compressed from those remarks, so these patterns
# read a remark for the reason it names, in the reviewer's own order of mention,
# and say nothing when the words do not clearly name one. The portal reads a
# blank reason as the note carrying it, so silence is the safe answer.
_FT = r"(?:ft|functional test\w*)"
_AGENCY = r"(?:empanel|external agency|third party|3rd party)"
_NOT_EMPANELED = r"(?:empanel|external agency|third party|3rd party|self att)"
_ABSENT = r"(?:missing|not attached|not uploaded)"
_SENT = r"(?:upload|attach|share|provide|submit)"
EXIT_REASON_PATTERNS = (
    (
        "FT/WASA not done by an empaneled agency",
        (
            rf"{_FT}[^.]{{0,80}}?{_NOT_EMPANELED}",
            rf"{_AGENCY}[^.]{{0,80}}?{_FT}",
            rf"wasa[^.]{{0,80}}?{_NOT_EMPANELED}",
            rf"{_AGENCY}[^.]{{0,80}}?wasa",
        ),
    ),
    (
        "FT/WASA report missing",
        (
            rf"{_FT}[^.]{{0,60}}?{_ABSENT}",
            rf"{_SENT}[^.]{{0,40}}?{_FT}[^.]{{0,20}}?(?:report|cert|mail|pass)",
            rf"wasa[^.]{{0,60}}?{_ABSENT}",
            rf"{_SENT}[^.]{{0,40}}?wasa",
        ),
    ),
    (
        "Duplicate exit request",
        (
            r"duplicate",
            r"already (?:been )?(?:submitted|applied)",
            r"twice",
            r"(?:2|two) applications",
            r"same client id[^.]{0,40}?used",
        ),
    ),
    (
        "Exit requested without NHA review or approval",
        (
            r"without[^.]{0,40}?nha",
            r"without[^.]{0,30}?(?:system review|approval)",
            r"prior approval from nha",
        ),
    ),
    ("Internal demo not cleared", (r"internal demo",)),
    ("HTC demo not cleared", (r"\bhtc\b",)),
    (
        "Filed under the wrong application or track",
        (
            r"incorrect flow",
            r"wrong (?:application|track|form)",
            r"(?:has|have) to be under\b",
        ),
    ),
    (
        "Milestone details missing or incorrect",
        (r"\bm[1-4]\b", r"milestone"),
    ),
    (
        "Incomplete integration",
        (
            r"incomplete integration",
            r"integration (?:is )?(?:not compl|incompl)",
            r"complete[^.]{0,30}?integration",
        ),
    ),
    (
        "Incorrect document",
        (
            r"incorrect document",
            r"invalid document",
            r"correct (?:ft )?(?:certificate|document)",
            r"documents? (?:are|is) incorrect",
            r"incorrect documentation",
        ),
    ),
    (
        "Incomplete documentation",
        (
            r"incomplete document",
            r"documents?[^.]{0,30}?missing",
            r"missing[^.]{0,30}?documents?",
            r"undertaking",
        ),
    ),
)
EXIT_REASONS = tuple(name for name, _ in EXIT_REASON_PATTERNS)
_EXIT_REASON_SEARCH = tuple(
    (name, re.compile("|".join(parts), re.IGNORECASE))
    for name, parts in EXIT_REASON_PATTERNS
)


def exit_reject_reason(remark):
    """The listed reason a legacy remark names, or blank when it names none.

    When a remark names several, the one the reviewer wrote first wins, so a
    remark reading "Incorrect document,Incomplete integration" is filed under
    the document problem it leads with.
    """
    text_value = paragraph(remark)
    if not text_value:
        return ""
    found = [
        (match.start(), name)
        for name, search in _EXIT_REASON_SEARCH
        if (match := search.search(text_value))
    ]
    return min(found)[1] if found else ""


def reject_note(remark, reason):
    """The remark less the reason it opens with, which the reason already shows."""
    note = paragraph(remark)
    if not reason or not note.lower().startswith(reason.lower()):
        return note
    rest = note[len(reason) :]
    if rest and rest[0] not in ",.;:-":
        return note
    rest = rest.lstrip(",.;:- \n")
    return rest[:1].upper() + rest[1:]
