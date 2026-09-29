"""Which legacy registrations belong to one organisation.

A registrant is everything one login filed under one company name. An integrator
is the registrants that turn out to be the same company; it becomes one
organisation in the portal, and `linked_by` says what put them together.
"""

import itertools
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field

from . import clean

#: A consultant files for several companies under one mobile, and an exit names
#: whoever handled it, so a mobile or an exit contact under more than a handful of
#: company names identifies nobody. Deliberately low: missing a merge leaves two
#: organisations to join by hand, while a wrong one puts two companies in a single
#: workspace.
SHARE_LIMIT = 5
#: One address written two ways: most of the words agree.
ADDRESS_OVERLAP = 0.6
#: Below this an address or a product name tells two registrations apart from
#: nothing: four words of an address, four letters of a name.
MIN_TO_MATCH = 4
#: Signals that name one company by themselves: its login, its GSTIN, and what
#: only it holds — its website, its email domain, its address.
IDENTIFIES_A_COMPANY = ("same email", "GSTIN", "website", "email domain", "address")
#: A PIN, a mobile, an exit contact or a product name fits thousands of companies,
#: so one of them never merges alone.
ENOUGH_WEAK_SIGNALS = 2
#: `ApplicationType.ORGANIZATION` in legacy's enums, beside admin and individual.
ORGANISATION_APPLICATION = "3"


@dataclass
class Registrant:
    """One login's registrations for one company name, and what identifies it."""

    email: str
    name_key: str
    variant_key: str
    rows: list = field(default_factory=list)
    gstins: set = field(default_factory=set)
    pincodes: set = field(default_factory=set)
    sites: set = field(default_factory=set)
    domains: set = field(default_factory=set)
    mobiles: set = field(default_factory=set)
    contacts: set = field(default_factory=set)
    addresses: list = field(default_factory=list)
    products: set = field(default_factory=set)

    @property
    def key(self):
        return (self.email, self.name_key)


@dataclass(frozen=True)
class Shares:
    """How many registrants each mobile and each exit contact turns up under."""

    mobiles: Counter
    contacts: Counter


@dataclass
class Integrator:
    """One company, as the registrants that add up to it."""

    registrants: list
    linked_by: list

    @property
    def rows(self):
        return sorted(
            (row for registrant in self.registrants for row in registrant.rows),
            key=lambda row: (clean.when(row["created_at"]), row["sd_id"]),
        )

    @property
    def is_person(self):
        return all(not registrant.name_key for registrant in self.registrants)


def _add_gstin(registrant, row, enrolment, row_exits):
    exit_gstins = (exit_row["gstn_id"] for exit_row in row_exits)
    gst = clean.gstin(row["gst_no"], enrolment.get("gst_no"), *exit_gstins)
    if gst:
        registrant.gstins.add(gst)


def _add_pincode(registrant, location):
    pin = clean.pincode(location.get("pin_code"))
    if pin:
        registrant.pincodes.add(pin)


def _add_website(registrant, row, enrolment, row_exits):
    websites = [
        row["website"],
        enrolment.get("website"),
        *(exit_row["organisation_website"] for exit_row in row_exits),
    ]
    registrant.sites.update(site for site in map(clean.site_domain, websites) if site)


def _add_domain(registrant, email):
    domain = email.split("@", 1)[-1]
    if domain and domain not in clean.WEBMAIL:
        registrant.domains.add(domain)


def _add_phone_number(registrant, row):
    number = clean.mobile(row["mobile"])
    if number:
        registrant.mobiles.add(number)


def _add_contacts(registrant, row_exits):
    contacts = (clean.email(exit_row.get("spoc_email")) for exit_row in row_exits)
    registrant.contacts.update(contact for contact in contacts if contact)


def _add_address(registrant, location, row):
    address = location.get("complete_address") or row["address"]
    words = set(clean.name_key_words(address))
    if len(words) >= MIN_TO_MATCH:
        registrant.addresses.append(words)


def _add_product(registrant, row):
    product = clean.name_key(row["product_name"])
    if len(product) >= MIN_TO_MATCH:
        registrant.products.add(product)


def _add_signals(registrant, row, *, location, enrolment, row_exits):
    _add_gstin(registrant, row, enrolment, row_exits)
    _add_pincode(registrant, location=location)
    _add_website(registrant, row, enrolment, row_exits)
    _add_domain(registrant, registrant.email)
    _add_phone_number(registrant, row)
    _add_contacts(registrant, row_exits)
    _add_address(registrant, location, row)
    _add_product(registrant, row)


def read_registrants(rows, *, addresses, exits, enrolments):
    """Every registration read as a registrant, with what could identify it."""
    registrants = {}
    for row in rows:
        address = clean.email(row["email"])
        is_company = row["application_type"] == ORGANISATION_APPLICATION
        company = is_company and clean.name_key(row["organization"])
        key = (address, company or "")
        if key not in registrants:
            registrants[key] = Registrant(
                email=address,
                name_key=company or "",
                variant_key=clean.variant_key(row["organization"]) if company else "",
            )
        registrants[key].rows.append(row)
        if company:
            _add_signals(
                registrants[key],
                row,
                location=addresses.get(row["sd_id"]) or {},
                enrolment=enrolments.get(row["sd_id"]) or {},
                row_exits=exits.get(row["sd_id"], []),
            )
    return registrants


def _shares_a_contact(first, second, seen):
    """A mobile or an address they share, and that few other registrants carry."""
    return any(seen[shared] <= SHARE_LIMIT for shared in first & second)


def _is_one_address(first, second):
    """One address written two ways: most of the words in common."""
    return any(
        len(one & other) / max(1, len(one | other)) >= ADDRESS_OVERLAP
        for one in first
        for other in second
    )


def _pans(registrant):
    """The PAN inside each GSTIN, which one company repeats in every state."""
    return {gstin[2:12] for gstin in registrant.gstins}


def _are_two_companies(first, second):
    """Both filed a GSTIN and share no PAN, so they are two companies."""
    return bool(first.gstins and second.gstins) and not _pans(first) & _pans(second)


def _in_common(first, second, shares):
    """Everything two registrations have in common."""
    found = []
    if first.email == second.email:
        found.append("same email")
    if first.gstins & second.gstins:
        found.append("GSTIN")
    if first.sites & second.sites:
        found.append("website")
    if first.domains & second.domains:
        found.append("email domain")
    if first.pincodes & second.pincodes:
        found.append("PIN")
    if _shares_a_contact(first.mobiles, second.mobiles, shares.mobiles):
        found.append("mobile")
    if _shares_a_contact(first.contacts, second.contacts, shares.contacts):
        found.append("exit contact")
    if _is_one_address(first.addresses, second.addresses):
        found.append("address")
    if first.products & second.products:
        found.append("product name")
    return found


def _is_one_company(found):
    """Whether what two registrations have in common says they are one company."""
    if any(signal in IDENTIFIES_A_COMPANY for signal in found):
        return True
    return len(found) >= ENOUGH_WEAK_SIGNALS


def _links(first, second, shares):
    """What says these two registrations are one organisation."""
    found = _in_common(first, second, shares)
    if "same email" not in found and _are_two_companies(first, second):
        return []
    return found if _is_one_company(found) else []


def _registrants_sharing_a_name(registrants):
    """Units whose company name, or a variant of it, another registrant also carries.

    Only these are worth comparing: two registrations under unrelated names are
    never one organisation, whatever else they have in common.
    """
    by_name = defaultdict(list)
    for registrant in registrants.values():
        if registrant.name_key:
            by_name[("name", registrant.name_key)].append(registrant)
        if registrant.variant_key:
            by_name[("variant", registrant.variant_key)].append(registrant)
    return [sharing for sharing in by_name.values() if len(sharing) > 1]


class Merges:
    """Registrants merged into integrators, with what linked each merge."""

    def __init__(self, keys):
        self.merged_into = {key: key for key in keys}
        self.links = defaultdict(set)

    def stands_for(self, key):
        """The registrant that stands for the organisation this one ended up in."""
        walked = []
        while self.merged_into[key] != key:
            walked.append(key)
            key = self.merged_into[key]
        for step in walked:
            self.merged_into[step] = key
        return key

    def join(self, first, second, links):
        """Read two registrants as one integrator, keeping what linked them."""
        one, other = self.stands_for(first), self.stands_for(second)
        self.links[one].update(links)
        self.links[other].update(links)
        self.merged_into[one] = other

    def groups(self):
        """Each integrator once: its registrants, in the order they came, and why."""
        members = defaultdict(list)
        for key in self.merged_into:
            members[self.stands_for(key)].append(key)
        for keys in members.values():
            yield keys, sorted({name for key in keys for name in self.links[key]})


def _shares(registrants):
    """How many registrants each mobile and each exit contact turns up under."""
    return Shares(
        mobiles=Counter(
            number
            for registrant in registrants.values()
            for number in registrant.mobiles
        ),
        contacts=Counter(
            contact
            for registrant in registrants.values()
            for contact in registrant.contacts
        ),
    )


def refused_merges(registrants, integrators):
    """Pairs that a shared name links and a clashing GSTIN keeps apart.

    A mistyped GSTIN reads as another company, so these are worth an eye.
    """
    integrator_of = {
        registrant.key: index
        for index, integrator in enumerate(integrators)
        for registrant in integrator.registrants
    }
    shares = _shares(registrants)
    refused = {}
    for sharing in _registrants_sharing_a_name(registrants):
        for first, second in itertools.combinations(sharing, 2):
            if integrator_of[first.key] == integrator_of[second.key]:
                continue
            if _are_two_companies(first, second) and _is_one_company(
                _in_common(first, second, shares),
            ):
                refused[frozenset((first.key, second.key))] = (first, second)
    return list(refused.values())


def group_integrators(registrants):
    """The registrants that read as one integrator, each with what linked them."""
    shares = _shares(registrants)
    merges = Merges(registrants)
    for sharing in _registrants_sharing_a_name(registrants):
        for first, second in itertools.combinations(sharing, 2):
            linked_by = _links(first, second, shares)
            if linked_by:
                merges.join(first.key, second.key, linked_by)
    return [
        Integrator(
            registrants=[registrants[key] for key in keys],
            linked_by=linked_by,
        )
        for keys, linked_by in merges.groups()
    ]
