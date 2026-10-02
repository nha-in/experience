"""Product-sized review queue entries, with permission-scoped status context."""

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db.models import Case
from django.db.models import CharField
from django.db.models import Count
from django.db.models import F
from django.db.models import FilteredRelation
from django.db.models import Max
from django.db.models import Min
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models import Subquery
from django.db.models import Value
from django.db.models import When
from django.db.models.functions import Coalesce
from django.db.models.functions import Lower
from django.db.models.functions import NullIf
from django.urls import reverse
from django.utils import timezone

from . import permissions
from .models import Product
from .models import ReviewItem

if TYPE_CHECKING:
    from datetime import datetime


def queue_requests(user):
    """Represent an organisation review on each product the reviewer can open.

    Expand before filters: an organisation-only match must still lead to its
    product page, including when no product request is currently submitted.
    The filtered left join retains a standalone row when no product is visible.
    """
    products = permissions.visible_products(user)
    return (
        permissions.visible_reviews(user)
        .exclude(kind=ReviewItem.Kind.PRODUCT)
        .exclude(status=ReviewItem.Status.DRAFT)
        .annotate(
            organisation_product=FilteredRelation(
                "organisation__products",
                condition=Q(
                    product_id__isnull=True,
                    organisation__products__pk__in=Subquery(products.values("pk")),
                ),
            ),
        )
        .annotate(
            queue_product_id=Coalesce("product_id", "organisation_product__pk"),
        )
    )


QUEUE_SORTS = tuple(
    f"{direction}{key}"
    for key in ("submitted", "age", "title", "requests", "assignee")
    for direction in ("-", "")
)


def grouped_requests(query, sort="-submitted"):
    """Group merged requests before counting or paginating.

    A group dates from its newest request, or its oldest when the oldest come
    first; its age is the same date read the other way. By title it sorts on
    its product's name, or the organisation's for a request with no product;
    by requests on how many of its requests match; and by assignee on the
    first name among them, the unassigned last. Ties go newest first.
    """
    key = sort.removeprefix("-")
    descending = sort.startswith("-")
    if key == "age":
        key, descending = "submitted", not descending
    aggregate = Min if key == "submitted" and not descending else Max
    query = query.order_by().annotate(
        standalone_id=Case(When(queue_product_id__isnull=True, then=F("pk"))),
    )
    if key == "title":
        names = Product.objects.filter(pk=OuterRef("queue_product_id")).values("name")
        query = query.annotate(
            queue_title=Lower(Coalesce(Subquery(names[:1]), F("organisation__name"))),
        )
    elif key == "assignee":
        query = query.annotate(
            queue_assignee=Lower(
                Coalesce(
                    NullIf("assignee__name", Value("")),
                    "assignee__email",
                    output_field=CharField(),
                ),
            ),
        )
    groups = query.values("queue_product_id", "standalone_id").annotate(
        submitted_at=aggregate("submitted_at"),
        sort_id=aggregate("pk"),
    )
    if key == "submitted":
        direction = "-" if descending else ""
        return groups.order_by(
            f"{direction}submitted_at",
            f"{direction}sort_id",
            "queue_product_id",
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
        "queue_product_id",
    )


@dataclass
class QueueEntry:
    product: Product | None
    reviews: list
    matching_reviews: list
    submitted_at: datetime | None

    @property
    def product_id(self):
        return self.product.pk if self.product else None

    @property
    def organisation(self):
        return self.reviews[0].organisation

    @property
    def title(self):
        return self.product.name if self.product else self.reviews[0].title

    @property
    def reference(self):
        if self.product:
            return self.product.reference
        return self.reviews[0].reference

    @property
    def url(self):
        if self.product:
            return reverse("experiences:product-detail", args=[self.reference])
        return self.reviews[0].get_absolute_url()

    @property
    def age(self):
        return (timezone.now() - self.submitted_at).days if self.submitted_at else 0

    @property
    def pending(self):
        return any(review.pending for review in self.matching_reviews)

    @property
    def open_reviews(self):
        """The tab's requests still to decide; approved ones get a row of their own."""
        return [
            review
            for review in self.matching_reviews
            if review.status != ReviewItem.Status.APPROVED
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
        return [review for review in self.matching_reviews if review.waiting_on]

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
    products = {
        product.pk: product
        for product in Product.objects.filter(
            pk__in=[row["queue_product_id"] for row in groups],
        ).select_related("organisation")
    }
    standalone_ids = [row["standalone_id"] for row in groups if row["standalone_id"]]
    matches = defaultdict(set)
    for product_id, review_id in matching.filter(
        Q(queue_product_id__in=products) | Q(pk__in=standalone_ids),
    ).values_list("queue_product_id", "pk"):
        matches[(product_id, None if product_id else review_id)].add(review_id)
    subjects = (
        Q(product_id__in=products)
        | Q(pk__in=standalone_ids)
        | Q(
            kind=ReviewItem.Kind.ORGANISATION,
            organisation_id__in={
                product.organisation_id for product in products.values()
            },
        )
    )
    visible = (
        permissions.visible_reviews(user)
        .filter(subjects)
        .exclude(kind=ReviewItem.Kind.PRODUCT)
        .exclude(status=ReviewItem.Status.DRAFT)
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
        if review.product_id:
            reviews[(review.product_id, None)].append(review)
        else:
            reviews[(None, review.pk)].append(review)
            for product in products.values():
                if product.organisation_id == review.organisation_id:
                    reviews[(product.pk, None)].append(review)
    for entry_reviews in reviews.values():
        entry_reviews.sort(key=review_order)
    page.object_list = [
        QueueEntry(
            product=products.get(group["queue_product_id"]),
            reviews=reviews[(group["queue_product_id"], group["standalone_id"])],
            matching_reviews=[
                review
                for review in reviews[
                    (group["queue_product_id"], group["standalone_id"])
                ]
                if review.pk
                in matches[(group["queue_product_id"], group["standalone_id"])]
            ],
            submitted_at=group["submitted_at"],
        )
        for group in groups
    ]
    return page
