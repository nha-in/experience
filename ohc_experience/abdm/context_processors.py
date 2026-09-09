"""The product the integrator is working in, for the sidebar on every page.

Overview, Credentials and the five track links all need a product to point
at, including on pages that have none of their own (Events, Support,
Settings). ``ProductMixin`` remembers the last product opened in the session;
this falls back to the organisation's first product when there is none.

The rail also carries the prototype's small counters: approved/applied per
track, upcoming events, open tickets. They are computed here, once per page,
so the nav never has to ask.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from ohc_experience.organisations.selectors import get_organisation_for

from .models import Product
from .tracks import TRACKS

if TYPE_CHECKING:
    from django.http import HttpRequest

SESSION_PRODUCT_KEY = "current_product_sandbox_id"

# The onboarding header's three steps (layouts/onboarding.html).
ONBOARDING_STEPS = (
    (1, _("Account")),
    (2, _("Organisation")),
    (3, _("Product")),
)


def nav_tracks(product) -> list[dict[str, Any]]:
    """One row per track the product applied for: approved and applied counts.

    The rail follows the product rather than the catalogue: a track it is not
    on is left out, and Edit product is where one is added.
    """
    if product is None:
        return []
    rows = []
    for track in product.tracks:
        approved, count = product.approved_count(track)
        rows.append({"track": track, "approved": approved, "count": count})
    return rows


def pick_current_product(
    request: HttpRequest,
    products: list[Product],
) -> Product | None:
    """The product the rail points at: last opened in this session, else the first."""
    session = getattr(request, "session", None)
    wanted = session.get(SESSION_PRODUCT_KEY) if session is not None else None
    product = next((item for item in products if item.sandbox_id == wanted), None)
    if product is None and products:
        product = products[0]
    return product


def current_product(request: HttpRequest) -> dict[str, Any]:
    from ohc_experience.events.selectors import upcoming_events  # noqa: PLC0415
    from ohc_experience.support.models import Ticket  # noqa: PLC0415

    user = getattr(request, "user", None)
    organisation = get_organisation_for(user)
    common = {
        "tracks": TRACKS,
        "abdm_docs_url": settings.ABDM_DOCS_URL,
        "onboarding_steps": ONBOARDING_STEPS,
    }
    if organisation is None:
        return {
            **common,
            "current_product": None,
            "organisation_products": [],
            "nav_tracks": nav_tracks(None),
            "nav_event_count": 0,
            "nav_ticket_count": 0,
        }
    products = list(
        Product.objects.for_organisation(organisation).prefetch_related(
            "compliance_records",
        ),
    )
    product = pick_current_product(request, products)
    return {
        **common,
        "current_product": product,
        "organisation_products": products,
        "nav_tracks": nav_tracks(product),
        "nav_event_count": upcoming_events().count(),
        "nav_ticket_count": Ticket.objects.for_organisation(organisation)
        .open_only()
        .count(),
    }


def certification_desk(request: HttpRequest) -> dict[str, Any]:
    """The console rail's desk card: how many reviewers, how many in the queue.

    Only computed for the review team; everybody else gets nothing, and the
    rail never draws the card for them anyway.
    """
    from django.contrib.auth import get_user_model  # noqa: PLC0415

    from .models import ReviewItem  # noqa: PLC0415

    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False) or not user.is_ohc_team:
        return {}
    return {
        "desk_queue_count": ReviewItem.objects.open().count(),
        "desk_reviewer_count": get_user_model()
        .objects.filter(is_ohc_team=True, is_active=True)
        .count(),
    }
