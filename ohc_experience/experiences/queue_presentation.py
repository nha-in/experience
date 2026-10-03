"""The review queue's two lists, with permission-scoped status context.

Products lists each product once with its requests. Organisations lists each
organisation's verification, which belongs to no product.
"""

from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db.models import CharField
from django.db.models import Count
from django.db.models import F
from django.db.models import Max
from django.db.models import Min
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.db.models.functions import Lower
from django.db.models.functions import NullIf
from django.urls import reverse
from django.utils import timezone

from . import permissions
from . import tables
from .models import Product
from .models import ReviewItem

if TYPE_CHECKING:
    from datetime import datetime


def submitted_requests(user):
    """Every request the queue shows, once: a reviewer's work, less the drafts.

    `permissions.review_requests` leaves out what nobody decides; a draft is
    the integrator's unsent work, which the queue does not show either.
    """
    return permissions.review_requests(user).exclude(status=ReviewItem.Status.DRAFT)


#: What a reviewer has acted on: raised a query about, rejected or approved.
ACTED_ON = (
    ReviewItem.Status.QUERY,
    ReviewItem.Status.REJECTED,
    ReviewItem.Status.APPROVED,
)


def queue_requests(user):
    """The Products list's requests: every one but the organisations' own
    verifications, which the Organisations list holds."""
    return submitted_requests(user).exclude(kind=ReviewItem.Kind.ORGANISATION)


def verification_requests(user):
    """The Organisations list's requests: each organisation's one verification."""
    return submitted_requests(user).filter(kind=ReviewItem.Kind.ORGANISATION)


QUEUE_SORTS = tuple(
    f"{direction}{key}"
    for key in ("submitted", "age", "title", "requests", "assignee")
    for direction in ("-", "")
)

#: An organisation has one request, so its list has no count of them to sort by.
VERIFICATION_SORTS = tuple(sort for sort in QUEUE_SORTS if "requests" not in sort)


def _assignee_name():
    """A request's assignee as the queue sorts them: by name, else by email."""
    return Lower(
        Coalesce(
            NullIf("assignee__name", Value("")),
            "assignee__email",
            output_field=CharField(),
        ),
    )


def grouped_requests(query, sort="-submitted"):
    """Group each product's requests into its row before counting or paginating.

    A row dates from its newest request, or its oldest when the oldest come
    first; its age is the same date read the other way. By title it sorts on
    its product's name; by requests on how many of its requests match; and by
    assignee on the first name among them, the unassigned last. Ties go newest
    first.
    """
    key = sort.removeprefix("-")
    descending = sort.startswith("-")
    if key == "age":
        key, descending = "submitted", not descending
    aggregate = Min if key == "submitted" and not descending else Max
    query = query.order_by()
    if key == "title":
        query = query.annotate(queue_title=Lower("product__name"))
    elif key == "assignee":
        query = query.annotate(queue_assignee=_assignee_name())
    groups = query.values("product_id").annotate(
        submitted_at=aggregate("submitted_at"),
        sort_id=aggregate("pk"),
    )
    if key == "submitted":
        direction = "-" if descending else ""
        return groups.order_by(
            f"{direction}submitted_at",
            f"{direction}sort_id",
            "product_id",
        )
    groups = groups.annotate(
        sort_key={
            "title": Min("queue_title"),
            "requests": Count("pk"),
            "assignee": Min("queue_assignee"),
        }[key],
    )
    sort_key = F("sort_key")
    return groups.order_by(
        sort_key.desc(nulls_last=True) if descending else sort_key.asc(nulls_last=True),
        "-submitted_at",
        "-sort_id",
        "product_id",
    )


def ordered_verifications(query, sort="-submitted"):
    """Verifications in the order a sort asks for, as `grouped_requests` reads it.

    By title they sort on the organisation's name, and by assignee on theirs,
    the unassigned last. Ties go newest first.
    """
    key = sort.removeprefix("-")
    descending = sort.startswith("-")
    if key == "age":
        key, descending = "submitted", not descending
    field = {
        "submitted": F("submitted_at"),
        "title": Lower(tables.organisation_name("organisation__")),
        "assignee": _assignee_name(),
    }[key]
    return query.select_related("organisation", "assignee").order_by(
        field.desc(nulls_last=True) if descending else field.asc(nulls_last=True),
        "-submitted_at",
        "-pk",
    )


@dataclass
class QueueEntry:
    product: Product
    reviews: list
    matching_reviews: list
    submitted_at: datetime | None
    #: The tab the row is listed under: "ready", "decided" or "all".
    tab: str = "all"

    @property
    def product_id(self):
        return self.product.pk

    @property
    def organisation(self):
        return self.product.organisation

    @property
    def title(self):
        return self.product.name

    @property
    def reference(self):
        return self.product.reference

    @property
    def url(self):
        return reverse("experiences:product-detail", args=[self.reference])

    @property
    def age(self):
        return (timezone.now() - self.submitted_at).days if self.submitted_at else 0

    @property
    def pending(self):
        return bool(self.pending_reviews)

    @property
    def queried(self):
        """Whether one of the product's requests has an open query, which sets
        the whole product aside until the integrator replies."""
        return any(review.status == ReviewItem.Status.QUERY for review in self.reviews)

    @property
    def pending_reviews(self):
        """The matching requests a reviewer can decide now."""
        if self.queried:
            return []
        return [
            review
            for review in self.matching_reviews
            if review.queue_state == ReviewItem.Status.IN_REVIEW
        ]

    @property
    def done_reviews(self):
        """Every request a reviewer cannot decide now, whatever the filters
        match, so a row shows how far its product has got.

        Each tab shows only what its name says. Pending lists what can be
        decided, with what is decided beside it: a request waiting on an
        earlier one is no reviewer's yet. Done leaves out the requests an open
        query sets aside, though a reviewer could decide them. All shows both.
        """
        if self.queried and self.tab == "all":
            return self.reviews
        return [
            review
            for review in self.reviews
            if review.queue_state != ReviewItem.Status.IN_REVIEW
            and not (self.tab == "ready" and review.queue_state == "blocked")
        ]

    @property
    def approved(self):
        return [
            review
            for review in self.reviews
            if review.status == ReviewItem.Status.APPROVED
        ]

    @property
    def blocked(self):
        return [
            review for review in self.done_reviews if review.queue_state == "blocked"
        ]

    @property
    def blocked_on(self):
        """Every prerequisite the blocked requests wait on, once."""
        return list(
            dict.fromkeys(
                name for review in self.blocked for name, _status in review.waiting_on
            ),
        )

    @property
    def assignees(self):
        # Assignment belongs to a request. Never imply a single owner for a
        # product whose matching requests have different reviewers.
        return list(
            {
                review.assignee_id: review.assignee for review in self.matching_reviews
            }.values(),
        )


@dataclass
class VerificationEntry:
    """An organisation's verification, as the Organisations list shows it."""

    review: ReviewItem
    #: The organisation's products the reviewer can open, by name.
    products: list
    #: Requests under review that cannot be decided until the organisation is verified.
    held: int = 0

    @property
    def organisation(self):
        return self.review.organisation

    @property
    def reference(self):
        return self.review.reference

    @property
    def url(self):
        return self.review.get_absolute_url()

    @property
    def status(self):
        return self.review.status

    @property
    def status_label(self):
        return self.review.get_status_display()

    @property
    def submitted_at(self):
        return self.review.submitted_at

    @property
    def age(self):
        return self.review.age

    @property
    def pending(self):
        """Whether a reviewer can decide it now, and so is late after a week."""
        return self.review.status == ReviewItem.Status.IN_REVIEW

    @property
    def assignee(self):
        return self.review.assignee


def review_order(review):
    """Organisation first, then the program's catalog presentation order."""
    if review.kind == ReviewItem.Kind.ORGANISATION:
        return (0, 0, review.pk)
    milestone = getattr(review.application, "milestone", None)
    if milestone:
        keys = list(review.program.milestones)
        return (1, keys.index(milestone.key), review.pk)
    return (2, 0, review.pk)


def populate_queue_page(page, user, matching):
    """Load status context only for this page and the reviewer's visible scope."""
    groups = list(page.object_list)
    products = Product.objects.select_related("organisation").in_bulk(
        [row["product_id"] for row in groups],
    )
    matches = defaultdict(set)
    for product_id, review_id in matching.filter(
        product_id__in=products,
    ).values_list("product_id", "pk"):
        matches[product_id].add(review_id)
    visible = (
        queue_requests(user)
        .filter(product_id__in=products)
        .select_related(
            "product",
            "organisation",
            "application__milestone",
            "assignee",
            "form",
        )
    )
    reviews = defaultdict(list)
    for review in visible:
        milestone = getattr(review.application, "milestone", None)
        review.queue_milestone = (
            review.program.milestones[milestone.key] if milestone else None
        )
        review.queue_label = review.get_status_display()
        reviews[review.product_id].append(review)
    for entry_reviews in reviews.values():
        entry_reviews.sort(key=review_order)
    page.object_list = [
        QueueEntry(
            product=products[group["product_id"]],
            reviews=reviews[group["product_id"]],
            matching_reviews=[
                review
                for review in reviews[group["product_id"]]
                if review.pk in matches[group["product_id"]]
            ],
            submitted_at=group["submitted_at"],
        )
        for group in groups
    ]
    return page


def populate_verification_page(page, user):
    """The page's verifications, each with its organisation's products and the
    reviewer's requests under review that wait on it.

    A form says what it waits on in `pending_prerequisites`; ABDM's milestones
    wait on their organisation's verification, so an unverified organisation's
    count is of the requests that name it there.
    """
    reviews = list(page.object_list)
    organisations = {review.organisation_id for review in reviews}
    products = defaultdict(list)
    for product in (
        permissions.visible_products(user)
        .filter(organisation_id__in=organisations)
        .order_by(Lower("name"))
    ):
        products[product.organisation_id].append(product)
    unverified = {
        review.organisation_id: review
        for review in reviews
        if not review.organisation.is_verified
    }
    held = Counter()
    for request in (
        queue_requests(user)
        .filter(
            organisation_id__in=unverified,
            status=ReviewItem.Status.IN_REVIEW,
        )
        .select_related("organisation", "form")
    ):
        verification = unverified[request.organisation_id]
        if any(
            prerequisite.review == verification
            for prerequisite in request.definition.pending_prerequisites(request)
        ):
            held[request.organisation_id] += 1
    page.object_list = [
        VerificationEntry(
            review=review,
            products=products[review.organisation_id],
            held=held[review.organisation_id],
        )
        for review in reviews
    ]
    return page
