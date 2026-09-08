from __future__ import annotations

from django.utils import timezone
from factory import LazyAttribute
from factory import LazyFunction
from factory import Sequence
from factory import SubFactory
from factory.django import DjangoModelFactory

from ohc_experience.abdm.models import ComplianceRecord
from ohc_experience.abdm.models import Product
from ohc_experience.abdm.models import ReviewItem
from ohc_experience.abdm.references import next_review_reference
from ohc_experience.abdm.references import next_sandbox_id
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory


class ProductFactory(DjangoModelFactory[Product]):
    organisation = SubFactory(OrganisationFactory, onboarded=True)
    sandbox_id = LazyFunction(next_sandbox_id)
    name = Sequence(lambda n: f"Arogya HMIS {n}")
    description = "A hospital management system with ABHA-linked registration."
    category = Product.Category.HMIS
    solution_type = Product.SolutionType.CLINICAL_HMIS
    applied_tracks = LazyFunction(lambda: ["HI-CM"])
    applied_milestones = LazyFunction(lambda: ["HI-CM:M1", "HI-CM:M2"])
    created_by = SubFactory(UserFactory)

    class Meta:
        model = Product


class ComplianceRecordFactory(DjangoModelFactory[ComplianceRecord]):
    product = SubFactory(ProductFactory)
    track_code = "HI-CM"
    milestone_code = "M1"
    status = ComplianceRecord.Status.OPEN

    class Meta:
        model = ComplianceRecord


class ReviewItemFactory(DjangoModelFactory[ReviewItem]):
    reference = LazyFunction(next_review_reference)
    item_type = ReviewItem.Type.EXIT_REQUEST
    compliance = SubFactory(
        ComplianceRecordFactory,
        status=ComplianceRecord.Status.UNDER_REVIEW,
    )
    product = LazyAttribute(lambda item: item.compliance.product)
    organisation = LazyAttribute(lambda item: item.compliance.product.organisation)
    submitted_on = LazyFunction(timezone.now)

    class Meta:
        model = ReviewItem
