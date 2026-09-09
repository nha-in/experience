from __future__ import annotations

import pytest

from ohc_experience.organisations.models import Role
from ohc_experience.organisations.selectors import get_membership_for
from ohc_experience.organisations.selectors import get_organisation_for
from ohc_experience.organisations.selectors import notification_recipients
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_notification_recipients_are_the_managers_and_the_technical_contact():
    organisation = OrganisationFactory(technical_contact_email="ops@sunrise.in")
    MembershipFactory(
        organisation=organisation,
        role=Role.OWNER,
        user__email="owner@sunrise.in",
    )
    MembershipFactory(
        organisation=organisation,
        role=Role.ADMIN,
        user__email="admin@sunrise.in",
    )
    MembershipFactory(
        organisation=organisation,
        role=Role.DEVELOPER,
        user__email="dev@sunrise.in",
    )
    MembershipFactory(
        organisation=organisation,
        role=Role.ADMIN,
        user__email="ops@sunrise.in",
    )

    assert notification_recipients(organisation) == [
        "admin@sunrise.in",
        "ops@sunrise.in",
        "owner@sunrise.in",
    ]


def test_notification_recipients_skip_a_blank_contact():
    organisation = OrganisationFactory(technical_contact_email="")
    MembershipFactory(
        organisation=organisation,
        role=Role.OWNER,
        user__email="owner@sunrise.in",
    )

    assert notification_recipients(organisation) == ["owner@sunrise.in"]


def test_membership_lookups_answer_none_for_outsiders():
    member = MembershipFactory(user__email="member@sunrise.in")
    outsider = UserFactory(email="outsider@example.in")

    assert get_membership_for(member.user) == member
    assert get_organisation_for(member.user) == member.organisation
    assert get_membership_for(outsider) is None
    assert get_organisation_for(None) is None
