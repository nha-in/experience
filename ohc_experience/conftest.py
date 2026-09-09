from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ohc_experience.integrations.local import reset_local_state
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


@pytest.fixture(autouse=True)
def _reset_local_integrations() -> None:
    """Their state is cache-backed, so it would otherwise leak between tests."""
    reset_local_state()


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


@pytest.fixture
def lgd_lookup(monkeypatch):
    """Keep workflow/demo tests independent of the live LGD service."""
    locations = [
        {
            "state": "KARNATAKA",
            "state_code": "29",
            "district": "BENGALURU URBAN",
            "district_code": "525",
        },
    ]

    def lookup(pincode):
        return locations if pincode == "560001" else []

    monkeypatch.setattr("ohc_experience.abdm.forms.lookup_pincode", lookup)
    monkeypatch.setattr("ohc_experience.abdm.demo.lookup_pincode", lookup)
    monkeypatch.setattr("ohc_experience.organisations.lgd.lookup_pincode", lookup)
    return locations
