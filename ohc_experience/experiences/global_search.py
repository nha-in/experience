"""What the staff search box in the top bar, and the lists it opens, find.

The box offers a row per field, as Odoo's search does: "Search Organisation for:
care", "Search Sandbox ID for: care" (`rows`). A row opens the list the field
belongs to, searched in that field alone. The Organisations and Products lists
search with the same filters, in one field when given one and in all of them
otherwise. A row that matched on something it does not show says what
(`organisation_match`, `product_match`).
"""

import re
from dataclasses import dataclass

from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models import prefetch_related_objects
from django.urls import reverse
from django.utils.http import urlencode

from ohc_experience.organisations.models import Membership

#: The longest text searched; anything longer is cut.
MAX_LENGTH = 100
#: A mobile number's own digits, without the country code.
MOBILE_DIGITS = 10
#: Part of a number shorter than this, searched in every field, is more likely
#: a year or a reference than a mobile number.
MIN_MOBILE_DIGITS = 5
#: A number as people write one: digits, with spaces, brackets, dashes or +91.
NUMBER = re.compile(r"\+?[\d\s().-]+")

#: The fields each list can be searched in alone, by `field` value.
ORGANISATION_FIELDS = {"name": "Organisation", "email": "Email", "mobile": "Mobile"}
PRODUCT_FIELDS = {
    "product": "Product",
    "sandbox": "Sandbox ID",
    "production": "Production ID",
}


@dataclass(frozen=True)
class Row:
    """A "Search <label> for: <text>" row, and the list it opens without the text."""

    label: str
    url: str


def rows(*, support):
    """The search box's rows, in the order it offers them.

    A list that opens on a tab of its own is opened on its All tab instead, so
    a search finds a record whatever state it is in: the queue on All, Support
    on All tickets.

    The Ticket row is for staff who can read support. The rest are for every
    reviewer, as the box is.
    """
    organisations = reverse("experiences:organizations")
    products = reverse("experiences:products")
    found = [
        Row(ORGANISATION_FIELDS["name"], f"{organisations}?field=name"),
        Row(PRODUCT_FIELDS["product"], f"{products}?field=product"),
        Row(PRODUCT_FIELDS["sandbox"], f"{products}?field=sandbox"),
        Row(PRODUCT_FIELDS["production"], f"{products}?field=production"),
        Row(ORGANISATION_FIELDS["email"], f"{organisations}?field=email"),
        Row(ORGANISATION_FIELDS["mobile"], f"{organisations}?field=mobile"),
        Row("Review request", f"{reverse('experiences:queue')}?scope=all"),
    ]
    if support:
        found.append(Row("Ticket", f"{reverse('experiences:support')}?status="))
    return found


def searched(url, text):
    """A row's list, searched for `text`."""
    return f"{url}{'&' if '?' in url else '?'}{urlencode({'q': text})}"


def mobile_digits(text):
    """The digits to look for in mobile numbers when `text` is (part of) one."""
    text = text.strip()
    if not NUMBER.fullmatch(text):
        return ""
    digits = re.sub(r"\D", "", text)
    return digits[-MOBILE_DIGITS:] if len(digits) >= MIN_MOBILE_DIGITS else ""


def members_matching(text, field=""):
    """Memberships whose person has this email or mobile, as `field` asks."""
    if field == "name":
        return Membership.objects.none()
    if field == "mobile":
        digits = re.sub(r"\D", "", text)[-MOBILE_DIGITS:]
        if not digits:
            return Membership.objects.none()
        return Membership.objects.filter(user__phone_number__contains=digits)
    match = Q(user__email__icontains=text)
    if not field and (digits := mobile_digits(text)):
        match |= Q(user__phone_number__contains=digits)
    return Membership.objects.filter(match)


def organisation_filter(text, field=""):
    """Organisations matching `text` in one of ORGANISATION_FIELDS, or in any."""
    by_name = Q(name__icontains=text) | Q(legal_name__icontains=text)
    by_member = Exists(
        members_matching(text, field).filter(organisation=OuterRef("pk")),
    )
    if field == "name":
        return by_name
    if field in ORGANISATION_FIELDS:
        return Q(by_member)
    return by_name | by_member


def product_filter(text, field=""):
    """Products matching `text` in one of PRODUCT_FIELDS, or in any.

    In any field, that takes in the organisation's name and the application ID
    legacy gave the product too.
    """
    by_product = Q(name__icontains=text) | Q(reference__icontains=text)
    sandbox = Q(credential__client_id__icontains=text)
    production = Q(production_client_id__icontains=text)
    if field == "product":
        return by_product
    if field == "sandbox":
        return sandbox
    if field == "production":
        return production
    return (
        by_product
        | Q(organisation__name__icontains=text)
        | Q(organisation__legal_name__icontains=text)
        | sandbox
        | production
        | Q(metadata__legacy_application_id__icontains=text)
    )


def with_organisation_matches(organisations, text, field=""):
    """Each organisation with `search_match`, as `organisation_match` words it."""
    organisations = list(organisations)
    prefetch_related_objects(
        organisations,
        Prefetch(
            "memberships",
            queryset=members_matching(text, field)
            .select_related("user")
            .order_by("pk"),
            to_attr="matching_members",
        ),
    )
    for organisation in organisations:
        organisation.search_match = organisation_match(organisation, text, field)
    return organisations


def organisation_match(organisation, text, field=""):
    """What an organisation matched that its row does not show, or "".

    The row shows the legal name when there is one, so a match on the name it
    trades under, or on a member's email or mobile, is spelled out. Needs
    `with_organisation_matches` for the members.
    """
    folded = text.casefold()
    if field in {"", "name"}:
        if folded in organisation.display_name.casefold():
            return ""
        if folded in organisation.name.casefold():
            return f"name {organisation.name}"
    member = next(iter(getattr(organisation, "matching_members", [])), None)
    if member is None:
        return ""
    user = member.user
    if field == "mobile" or (not field and folded not in user.email.casefold()):
        return f"member {user.display_name} · {user.phone_number}"
    return f"member {user.display_name} · {user.email}"


def product_match(product, text, field=""):
    """What a product matched that its row does not show, or ""."""
    folded = text.casefold()
    organisation = product.organisation
    client_id = getattr(getattr(product, "credential", None), "client_id", "")
    production = product.production_client_id
    if field == "sandbox":
        return f"Sandbox ID {client_id}"
    if field == "production":
        return f"Production ID {production}"
    shown = (product.name, product.reference or "", organisation.display_name)
    if field == "product" or any(folded in value.casefold() for value in shown):
        return ""
    unshown = (
        ("organisation", organisation.name),
        ("Sandbox ID", client_id),
        ("Production ID", production),
        (
            "legacy application ID",
            str(product.metadata.get("legacy_application_id") or ""),
        ),
    )
    return next(
        (
            f"{label} {value}"
            for label, value in unshown
            if value and folded in value.casefold()
        ),
        "",
    )
