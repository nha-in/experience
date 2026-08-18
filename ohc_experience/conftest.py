from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.test import Client

    from ohc_experience.organisations.models import Membership
    from ohc_experience.organisations.models import Organisation
    from ohc_experience.users.models import User


@pytest.fixture(autouse=True)
def media_storage(settings, tmpdir) -> None:
    """Keep anything uploaded during a test inside that test's tmpdir."""
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture
def user(db) -> User:
    return UserFactory.create()


@pytest.fixture
def organisation(db) -> Organisation:
    """A vendor company that signed up but has not finished onboarding."""
    return OrganisationFactory.create(name="Sunrise Health Systems")


@pytest.fixture
def onboarded_organisation(db) -> Organisation:
    return OrganisationFactory.create(name="Sunrise Health Systems", onboarded=True)


@pytest.fixture
def owner_membership(onboarded_organisation: Organisation) -> Membership:
    """The owner of an onboarded organisation — the common signed-in actor."""
    return MembershipFactory.create(
        organisation=onboarded_organisation,
        role="owner",
        user__name="Meera Krishnan",
        user__email="meera@sunrise.in",
    )


@pytest.fixture
def sign_in(client: Client) -> Callable[[User], Client]:
    """Sign a user into the shared test client and hand the client back."""

    def _sign_in(signed_in_user: User) -> Client:
        client.force_login(signed_in_user)
        return client

    return _sign_in
