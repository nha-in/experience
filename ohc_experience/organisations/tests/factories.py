from __future__ import annotations

from datetime import timedelta

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
    name = Sequence(lambda n: f"Integrator {n} Health Systems")

    class Meta:
        model = Organisation

    class Params:
        # OrganisationFactory(onboarded=True) — past the company profile form.
        onboarded = Trait(onboarded_at=LazyFunction(timezone.now))


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
