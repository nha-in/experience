from __future__ import annotations

from datetime import timedelta

from django.core.files.base import ContentFile
from django.utils import timezone
from factory import LazyFunction
from factory import Sequence
from factory import SubFactory
from factory import Trait
from factory.django import DjangoModelFactory

from ohc_experience.organisations.models import Invitation
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.users.tests.factories import UserFactory


class OrganisationFactory(DjangoModelFactory[Organisation]):
    name = Sequence(lambda n: f"Vendor {n} Health Systems")

    class Meta:
        model = Organisation

    class Params:
        # OrganisationFactory(onboarded=True) — details complete and submitted
        # for verification (onboarding step 2 done), verification still pending.
        onboarded = Trait(
            onboarded_at=LazyFunction(timezone.now),
            verification_submitted_at=LazyFunction(timezone.now),
            description="Hospital information system for district hospitals.",
            entity_type=Organisation.EntityType.PRIVATE_COMPANY,
            category=Organisation.Category.INDIA_ENTITY,
            registered_address="12 MG Road, Kochi",
            pincode="682001",
            state="Kerala",
            district="Ernakulam",
            verification_document_type=Organisation.VerificationDocumentType.PAN,
            verification_document_number="AAACS1234K",
            verification_document=LazyFunction(
                lambda: ContentFile(b"%PDF-1.4 demo", name="pan.pdf"),
            ),
        )
        # OrganisationFactory(verified=True) — the NHA team has verified it.
        verified = Trait(
            verification_status=Organisation.VerificationStatus.VERIFIED,
            verified_at=LazyFunction(timezone.now),
        )


class MembershipFactory(DjangoModelFactory[Membership]):
    organisation = SubFactory(OrganisationFactory)
    user = SubFactory(UserFactory)
    role = Role.DEVELOPER

    class Meta:
        model = Membership


class InvitationFactory(DjangoModelFactory[Invitation]):
    organisation = SubFactory(OrganisationFactory)
    email = Sequence(lambda n: f"invitee{n}@example.in")
    role = Role.DEVELOPER

    class Meta:
        model = Invitation

    class Params:
        expired = Trait(
            expires_at=LazyFunction(lambda: timezone.now() - timedelta(days=1)),
        )
        revoked = Trait(revoked_at=LazyFunction(timezone.now))
        accepted = Trait(accepted_at=LazyFunction(timezone.now))
