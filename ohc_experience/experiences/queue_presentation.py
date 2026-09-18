"""Product-sized review queue entries, with permission-scoped status context."""

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db.models import Case
from django.db.models import F
from django.db.models import FilteredRelation
from django.db.models import Max
from django.db.models import Min
from django.db.models import Q
from django.db.models import Subquery
from django.db.models import When
from django.db.models.functions import Coalesce
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
    products = permissions.visible_products(user).filter(workspace__isnull=False)
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


def grouped_requests(query, sort="newest"):
    """Group merged requests before counting or paginating."""
    aggregate = Min if sort == "oldest" else Max
    direction = "" if sort == "oldest" else "-"
    return (
        query.order_by()
        .annotate(
            standalone_id=Case(When(queue_product_id__isnull=True, then=F("pk"))),
        )
        .values("queue_product_id", "standalone_id")
        .annotate(
            submitted_at=aggregate("submitted_at"),
            sort_id=aggregate("pk"),
        )
        .order_by(
            f"{direction}submitted_at",
            f"{direction}sort_id",
            "queue_product_id",
        )
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
            return self.product.workspace.reference
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
        ).select_related("workspace", "organisation")
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
            "product__workspace",
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
