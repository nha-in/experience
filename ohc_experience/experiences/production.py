"""Production details, added by staff once a product's exit is approved.

The gateway team issues production credentials outside the portal and hands
the secret to the integrator. The portal keeps the client ID and the day it
was issued, on the product.
"""

import re
from collections import defaultdict
from datetime import date
from datetime import datetime
from functools import partial

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Exists
from django.db.models import F
from django.db.models import OuterRef
from django.db.models import Prefetch
from django.db.models import Q
from django.db.models import Subquery
from django.utils import timezone
from django.utils.formats import date_format

from ohc_experience.integrations.notification.templates import PRODUCTION_APPROVED
from ohc_experience.integrations.ports import NotificationMessage
from ohc_experience.integrations.registry import get_notification_gateway
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Role

from .credentials import rate_limit
from .models import AuditEvent
from .models import Milestone
from .models import Product
from .models import ProductCredential
from .permissions import has_access
from .workflows import audit
from .workflows import integrator_emails

ADDED = "Production client ID added"
CHANGED = "Production client ID changed"
REMOVED = "Production client ID removed"
DATED = "Production issue date changed"
#: The wording used before this screen took NHA's own; its events stay as saved.
RECORDED = "Production client ID recorded"
ACTIONS = (ADDED, CHANGED, REMOVED, DATED, RECORDED)
#: The actions that name whoever the current ID came from, newest first.
AUTHORED = (ADDED, CHANGED, RECORDED)

CLIENT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]*")
CLIENT_ID_MIN_LENGTH = 3
CLIENT_ID_MAX_LENGTH = 255

#: The two stages of production approval, which the screen switches between:
#: an approved exit waiting on its production client ID, then the ID added.
STAGES = ("pending", "approved")
#: The register's older view names, and the stage that now holds their products.
TABS = {"awaiting": "pending", "added": "approved", "all": "approved"}

STALE = "This changed after you opened it. Reload and try again."
IN_USE = "This client ID is already in use."
NOT_ELIGIBLE = "Production details follow an approved milestone exit."
FUTURE_DATE = "The production issue date cannot be in the future."

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def enabled(program):
    """Whether this program records production client IDs at all."""
    return program.production_credentials is not None


def can_view(user, program):
    return enabled(program) and has_access(user, "review", "", "read", program.key)


def can_manage(user, program):
    return enabled(program) and has_access(user, "review", "", "approve", program.key)


def validate_client_id(value):
    value = (value or "").strip()
    if not CLIENT_ID_MIN_LENGTH <= len(value) <= CLIENT_ID_MAX_LENGTH:
        msg = (
            f"Enter between {CLIENT_ID_MIN_LENGTH} and {CLIENT_ID_MAX_LENGTH} "
            "characters."
        )
        raise ValidationError(msg)
    if not CLIENT_ID_PATTERN.fullmatch(value):
        msg = (
            "Use letters, numbers and . _ : @ - only, starting with a letter or number."
        )
        raise ValidationError(msg)
    return value


def validate_issued_on(value):
    """The gateway team cannot have issued credentials that are still to come."""
    if value and value > timezone.localdate():
        raise ValidationError(FUTURE_DATE)
    return value


def _exits(program):
    """Approved exits: the standard milestone request, not an override like UHI."""
    return Q(
        enabled=True,
        application__status="approved",
        application__application_type=program.applications.milestone.key,
    )


def approved_exits(product):
    """The product's approved exits, in catalog order, with their applications."""
    program = product.workspace.definition
    order = list(program.milestones)
    return sorted(
        product.milestones.filter(_exits(program)).select_related("application"),
        key=lambda milestone: (
            order.index(milestone.key) if milestone.key in order else len(order)
        ),
    )


def eligible(product):
    program = product.workspace.definition
    return enabled(program) and product.milestones.filter(_exits(program)).exists()


def state(product):
    """What the product's pages show; None when the program doesn't record it."""
    if not enabled(product.workspace.definition):
        return None
    client_id = product.production_client_id
    return {
        "client_id": client_id,
        "issued_on": product.production_issued_on,
        "recorded_at": product.production_recorded_at,
        "eligible": bool(client_id) or eligible(product),
    }


def _lock_for_change(actor, product):
    """Check the actor, then hold the product so two approvers cannot race.

    Returns the ID and issue date as saved, which the caller's copy of the
    product may predate.
    """
    if not can_manage(actor, product.workspace.definition):
        msg = "Only onboarding approvers can change production details."
        raise PermissionDenied(msg)
    rate_limit(actor, "production", limit=10)
    return (
        Product.objects.select_for_update()
        .values_list("production_client_id", "production_issued_on")
        .get(pk=product.pk)
    )


def _save(product, **fields):
    Product.objects.filter(pk=product.pk).update(**fields)
    for name, value in fields.items():
        setattr(product, name, value)


@transaction.atomic
def record(product, actor, *, client_id, expected, issued_on=None):
    """Add or change the product's production client ID and issue date."""
    before, dated = _lock_for_change(actor, product)
    if before != (expected or ""):
        raise ValidationError(STALE)
    if not eligible(product):
        raise ValidationError(NOT_ELIGIBLE)
    client_id = validate_client_id(client_id)
    issued_on = validate_issued_on(issued_on) or timezone.localdate()
    if client_id == before:
        # The same ID under a corrected issue date: a quiet fix, not news the
        # integrator needs mailed to them again.
        if issued_on == dated:
            return
        _save(product, production_issued_on=issued_on)
        audit(
            actor=actor,
            action=DATED,
            product=product,
            detail={"before": _date(dated), "after": _date(issued_on)},
        )
        return
    # A sandbox client ID pasted by mistake, whatever its case. Another product's
    # production ID fails the unique index, which ignores case too.
    if ProductCredential.objects.filter(client_id__iexact=client_id).exists():
        raise ValidationError(IN_USE)
    try:
        with transaction.atomic():
            _save(
                product,
                production_client_id=client_id,
                production_issued_on=issued_on,
                production_recorded_at=timezone.now(),
            )
    except IntegrityError:
        raise ValidationError(IN_USE) from None
    # Read before this change is audited: a correction, or an ID removed and
    # entered again, is not news to the integrator.
    announced = AuditEvent.objects.filter(
        product=product,
        action__in=AUTHORED,
    ).exists()
    audit(
        actor=actor,
        action=CHANGED if before else ADDED,
        product=product,
        detail={
            "before": before,
            "after": client_id,
            "issued_on": _date(issued_on),
        },
    )
    if not announced:
        _notify(product)


@transaction.atomic
def remove(product, actor, *, expected):
    """Take mistaken production details back off the product."""
    before, _dated = _lock_for_change(actor, product)
    if before != (expected or ""):
        raise ValidationError(STALE)
    if not before:
        return
    _save(
        product,
        production_client_id="",
        production_issued_on=None,
        production_recorded_at=None,
    )
    audit(actor=actor, action=REMOVED, product=product, detail={"before": before})


def _notify(product):
    """Only ABDM's approved wording may go out until NHA agrees to our own.

    Ours named the Credentials page and linked to it; it is in this file's
    history if that answer comes back yes.
    """
    gateway = get_notification_gateway()
    for email in integrator_emails(product.organisation):
        message = NotificationMessage(
            template=PRODUCTION_APPROVED,
            receiver=email,
            values=(),
        )
        # After commit: a gateway failure must not undo the recorded ID.
        transaction.on_commit(partial(gateway.send, message))


def listing(program, *, stage="all", q=""):
    """Products through production approval, with the counts each stage shows.

    A product joins the register on its first approved exit and stays pending
    until an approver adds the client ID the gateway team issued outside the
    portal. One holding an ID counts as approved even if its exit later went.
    """
    exits = Milestone.objects.filter(_exits(program), product=OuterRef("pk"))
    recorder = AuditEvent.objects.filter(
        product=OuterRef("pk"),
        action__in=AUTHORED,
    ).order_by("-created_at", "-pk")
    query = (
        Product.objects.filter(workspace__experience_type=program.key)
        .annotate(
            has_exit=Exists(exits),
            first_exit_at=Subquery(
                exits.order_by("application__decided_at").values(
                    "application__decided_at",
                )[:1],
            ),
            sandbox_client_id=F("credential__client_id"),
            recorded_by_name=Subquery(recorder.values("actor__name")[:1]),
            recorded_by_email=Subquery(recorder.values("actor__email")[:1]),
        )
        .filter(Q(has_exit=True) | ~Q(production_client_id=""))
        .select_related("organisation", "workspace")
    )
    if q:
        query = query.filter(
            Q(name__icontains=q)
            | Q(workspace__reference__icontains=q)
            | Q(organisation__name__icontains=q)
            | Q(organisation__legal_name__icontains=q)
            | Q(production_client_id__icontains=q)
            | Q(credential__client_id__icontains=q),
        )
    waiting = query.filter(production_client_id="")
    issued = query.exclude(production_client_id="")
    counts = {
        "all": query.count(),
        "pending": waiting.count(),
        "approved": issued.count(),
    }
    if stage == "pending":
        # The longest wait first, which is what NHA is most overdue on.
        query = waiting.order_by("first_exit_at", "pk")
    elif stage == "approved":
        query = issued.order_by(
            F("production_issued_on").desc(nulls_last=True),
            "-pk",
        )
    else:
        query = query.order_by(F("first_exit_at").desc(nulls_last=True), "-pk")
    return query, counts


def approved_codes(products):
    """Approved milestone codes per product pk, in each program's catalog order."""
    products = {product.pk: product for product in products}
    keys = defaultdict(set)
    for product_id, key in Milestone.objects.filter(
        product_id__in=products,
        enabled=True,
        application__status="approved",
    ).values_list("product_id", "key"):
        keys[product_id].add(key)
    return {
        product_id: [
            milestone.code
            for key, milestone in product.workspace.definition.milestones.items()
            if key in keys[product_id]
        ]
        for product_id, product in products.items()
    }


def with_codes(products):
    """Listed products with their approved milestone codes attached."""
    products = list(products)
    codes = approved_codes(products)
    for product in products:
        product.approved_codes = codes[product.pk]
    return products


def history(product, limit=20):
    """Production entries from the audit trail, ready for the activity feed.

    Audit details keep ISO days and raw client IDs. The feed prints one prepared
    line per event rather than reading a payload only this module understands.
    """
    events = list(
        AuditEvent.objects.filter(product=product, action__in=ACTIONS).select_related(
            "actor",
        )[:limit],
    )
    for event in events:
        detail = event.detail or {}
        before, after = detail.get("before"), detail.get("after")
        event.change, event.change_mono = "", False
        if event.action == DATED:
            event.change = (
                f"{_day(_from_iso(before)) or 'Not entered'} → {_day(_from_iso(after))}"
            )
        elif before or after:
            event.change = " → ".join(part for part in (before, after) if part)
            event.change_mono = True
        issued_on = _from_iso(detail.get("issued_on"))
        event.footnote = f"Issued on {_day(issued_on)}" if issued_on else ""
    return events


def _day(value):
    return date_format(value, "j M Y") if value else ""


def _from_iso(value):
    try:
        return date.fromisoformat(value) if value else None
    except TypeError, ValueError:
        return None


CSV_HEADER = (
    "Product reference",
    "Product",
    "Organisation",
    "State",
    "District",
    "Owner",
    "Owner email",
    "Owner mobile",
    "Approved milestones",
    "Production approved on",
    "Sandbox client ID",
    "Production client ID",
    "Production issue date",
    "Added on",
    "Added by",
)


def _cell(value):
    """Spreadsheets run a cell that starts with a formula character."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def _date(value):
    """An ISO day, from either a stored timestamp or a date staff entered."""
    if not value:
        return ""
    if isinstance(value, datetime):
        value = timezone.localtime(value).date()
    return value.isoformat()


def csv_rows(query):
    products = with_codes(
        query.prefetch_related(
            Prefetch(
                "organisation__memberships",
                queryset=Membership.objects.filter(role=Role.OWNER).select_related(
                    "user",
                ),
                to_attr="owner_memberships",
            ),
        ),
    )
    yield CSV_HEADER
    for product in products:
        organisation = product.organisation
        owners = organisation.owner_memberships
        owner = owners[0].user if owners else None
        yield tuple(
            _cell(value)
            for value in (
                product.workspace.reference,
                product.name,
                organisation.display_name,
                organisation.state,
                organisation.city,
                owner.name if owner else "",
                owner.email if owner else "",
                owner.phone_number if owner else "",
                ", ".join(product.approved_codes),
                _date(product.first_exit_at),
                product.sandbox_client_id,
                product.production_client_id,
                _date(product.production_issued_on),
                _date(product.production_recorded_at),
                product.recorded_by_email,
            )
        )
