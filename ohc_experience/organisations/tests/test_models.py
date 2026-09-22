"""Model behaviour: slugs, roles, roster ordering and the invitation lifecycle."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from ohc_experience.organisations.models import INVITATION_TTL
from ohc_experience.organisations.models import SOLE_PROPRIETOR
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.models import initials_for
from ohc_experience.organisations.tests.factories import InvitationFactory
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

# A urlsafe token over 32 bytes is far longer than this; the floor just pins
# that something random was generated rather than left blank.
MIN_TOKEN_LENGTH = 20


class TestOrganisationSlug:
    def test_slug_is_built_from_the_name(self):
        organisation = Organisation.objects.create(name="Sunrise Health Systems")

        assert organisation.slug == "sunrise-health-systems"

    def test_colliding_names_get_a_numbered_slug(self):
        slugs = [
            Organisation.objects.create(name="Sunrise Health Systems").slug
            for _ in range(3)
        ]

        assert slugs == [
            "sunrise-health-systems",
            "sunrise-health-systems-2",
            "sunrise-health-systems-3",
        ]

    def test_a_name_that_slugifies_to_nothing_falls_back(self):
        organisation = Organisation.objects.create(name="!!!")

        assert organisation.slug == "organisation"

    def test_renaming_keeps_the_original_slug(self):
        organisation = Organisation.objects.create(name="Sunrise Health Systems")

        organisation.name = "Sunrise Health Systems Pvt Ltd"
        organisation.save()
        organisation.refresh_from_db()

        assert organisation.slug == "sunrise-health-systems"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Meera Krishnan", "MK"),
        ("meera@x.in", "ME"),
        ("", "?"),
        ("Meera", "ME"),
        ("meera.krishnan@x.in", "MK"),
        ("   ", "?"),
        # The local part can vanish once "@", "." and "_" are stripped out,
        # which reaches the second "?" guard rather than the first.
        ("@sunrise.in", "?"),
        ("...", "?"),
    ],
)
def test_initials_for(value: str, expected: str):
    assert initials_for(value) == expected


class TestOrganisationRoles:
    @pytest.mark.parametrize(
        ("role", "can_manage"),
        [
            (Role.OWNER, True),
            (Role.ADMIN, True),
            (Role.DEVELOPER, False),
            (Role.SUPPORT, False),
        ],
    )
    def test_role_of_and_can_manage(self, role: str, can_manage: bool):  # noqa: FBT001
        membership = MembershipFactory.create(role=role)
        organisation = membership.organisation

        assert organisation.role_of(membership.user) == role
        assert organisation.can_manage(membership.user) is can_manage

    def test_a_stranger_has_no_role(self, organisation: Organisation):
        stranger = UserFactory.create()

        assert organisation.role_of(stranger) is None
        assert organisation.can_manage(stranger) is False

    def test_an_anonymous_visitor_has_no_role(self, organisation: Organisation):
        assert organisation.role_of(AnonymousUser()) is None
        assert organisation.can_manage(AnonymousUser()) is False

    def test_owner_property_returns_the_owning_user(
        self,
        organisation: Organisation,
    ):
        owner = MembershipFactory.create(organisation=organisation, role=Role.OWNER)
        MembershipFactory.create(organisation=organisation, role=Role.DEVELOPER)

        assert organisation.owner == owner.user

    def test_owner_is_not_an_assignable_role(self):
        assert Role.OWNER not in dict(Role.assignable())
        assert set(dict(Role.assignable())) == {
            Role.ADMIN,
            Role.DEVELOPER,
            Role.SUPPORT,
        }

    def test_mark_onboarded_stamps_once(self, organisation: Organisation):
        assert organisation.is_onboarded is False

        organisation.mark_onboarded()
        first = organisation.onboarded_at
        organisation.mark_onboarded()

        assert organisation.is_onboarded is True
        assert organisation.onboarded_at == first


class TestMembershipOrdering:
    def test_the_roster_lists_the_owner_first(self, organisation: Organisation):
        for role, name in [
            (Role.SUPPORT, "Zara Support"),
            (Role.DEVELOPER, "Arun Developer"),
            (Role.OWNER, "Meera Owner"),
            (Role.ADMIN, "Nikhil Admin"),
        ]:
            MembershipFactory.create(
                organisation=organisation,
                role=role,
                user__name=name,
            )

        roles = [membership.role for membership in organisation.memberships.all()]

        assert roles == [Role.OWNER, Role.ADMIN, Role.DEVELOPER, Role.SUPPORT]

    def test_members_of_the_same_role_sort_by_name(self, organisation: Organisation):
        for name in ["Zara Iyer", "Arun Nair"]:
            MembershipFactory.create(
                organisation=organisation,
                role=Role.DEVELOPER,
                user__name=name,
            )

        names = [membership.user.name for membership in organisation.memberships.all()]

        assert names == ["Arun Nair", "Zara Iyer"]

    def test_a_user_can_only_be_a_member_once(self, organisation: Organisation):
        membership = MembershipFactory.create(organisation=organisation)

        assert membership.is_owner is False
        assert (
            Membership.objects.filter(
                organisation=organisation,
                user=membership.user,
            ).count()
            == 1
        )


class TestInvitation:
    def test_token_and_expiry_are_issued_on_save(self, organisation: Organisation):
        invitation = InvitationFactory.create(organisation=organisation)

        assert len(invitation.token) > MIN_TOKEN_LENGTH
        assert invitation.expires_at - timezone.now() > INVITATION_TTL - timedelta(
            minutes=1,
        )

    def test_tokens_are_unique(self, organisation: Organisation):
        first = InvitationFactory.create(organisation=organisation)
        second = InvitationFactory.create(organisation=organisation)

        assert first.token != second.token

    def test_a_fresh_invite_is_pending(self, organisation: Organisation):
        invitation = InvitationFactory.create(organisation=organisation)

        assert invitation.is_expired is False
        assert invitation.is_pending is True

    def test_an_expired_invite_is_not_pending(self, organisation: Organisation):
        invitation = InvitationFactory.create(organisation=organisation, expired=True)

        assert invitation.is_expired is True
        assert invitation.is_pending is False

    def test_a_revoked_invite_is_not_pending(self, organisation: Organisation):
        invitation = InvitationFactory.create(organisation=organisation, revoked=True)

        assert invitation.is_pending is False

    def test_an_accepted_invite_is_not_pending(self, organisation: Organisation):
        invitation = InvitationFactory.create(organisation=organisation, accepted=True)

        assert invitation.is_pending is False

    def test_the_pending_queryset_only_returns_live_invites(
        self,
        organisation: Organisation,
    ):
        live = InvitationFactory.create(organisation=organisation)
        InvitationFactory.create(organisation=organisation, expired=True)
        InvitationFactory.create(organisation=organisation, revoked=True)
        InvitationFactory.create(organisation=organisation, accepted=True)

        assert list(organisation.invitations.pending()) == [live]

    def test_refresh_token_issues_a_new_token_and_extends_the_expiry(
        self,
        organisation: Organisation,
    ):
        invitation = InvitationFactory.create(organisation=organisation, expired=True)
        old_token = invitation.token
        old_expiry = invitation.expires_at

        invitation.refresh_token()
        invitation.save(update_fields=["token", "expires_at"])
        invitation.refresh_from_db()

        assert invitation.token != old_token
        assert invitation.expires_at > old_expiry
        assert invitation.is_pending is True

    def test_accept_creates_a_membership_and_stamps_accepted_at(
        self,
        organisation: Organisation,
    ):
        invitation = InvitationFactory.create(
            organisation=organisation,
            role=Role.ADMIN,
        )
        user = UserFactory.create(email=invitation.email)

        membership = invitation.accept(user)
        invitation.refresh_from_db()

        assert membership.organisation == organisation
        assert membership.user == user
        assert membership.role == Role.ADMIN
        assert invitation.accepted_at is not None
        assert invitation.is_pending is False

    def test_accept_is_idempotent_for_an_existing_member(
        self,
        organisation: Organisation,
    ):
        existing = MembershipFactory.create(
            organisation=organisation,
            role=Role.DEVELOPER,
        )
        invitation = InvitationFactory.create(
            organisation=organisation,
            email=existing.user.email,
            role=Role.ADMIN,
        )

        membership = invitation.accept(existing.user)

        assert membership.pk == existing.pk
        assert membership.role == Role.DEVELOPER
        assert organisation.memberships.count() == 1

    def test_get_absolute_url_points_at_the_accept_link(
        self,
        organisation: Organisation,
    ):
        invitation = InvitationFactory.create(organisation=organisation)

        assert invitation.get_absolute_url() == (
            f"/invitations/{invitation.token}/accept/"
        )


def test_organisation_display_name_prefers_the_legal_name():
    organisation = OrganisationFactory.create(
        name="Sunrise",
        legal_name="Sunrise Health Systems Pvt Ltd",
    )

    assert organisation.display_name == "Sunrise Health Systems Pvt Ltd"
    assert organisation.initials == "SU"


@pytest.mark.parametrize(
    ("website", "email", "expected"),
    [
        ("https://sunrise.in", "meera@sunrise.in", ""),
        ("https://www.sunrise.in/about", "meera@sunrise.in", ""),
        ("https://sunrise.in:8443", "Meera@Sunrise.IN", ""),
        ("sunrise.in", "meera@sunrise.in", ""),
        # A subdomain on either side still belongs to the organisation.
        ("https://sunrise.in", "meera@mail.sunrise.in", ""),
        ("https://portal.sunrise.in", "meera@sunrise.in", ""),
        ("https://sunrise.in", "meera@gmail.com", "sunrise.in"),
        ("https://www.sunrise.in", "meera@notsunrise.in", "sunrise.in"),
        # With no website there is nothing to hold the address against.
        ("", "meera@gmail.com", ""),
    ],
)
def test_an_email_is_held_against_the_website_domain(website, email, expected):
    organisation = OrganisationFactory.build(
        website=website,
        entity_type="private_company",
    )

    assert organisation.email_off_website(email) == expected


def test_a_sole_proprietorship_may_use_a_personal_address():
    organisation = OrganisationFactory.build(
        website="https://rao.example",
        entity_type=SOLE_PROPRIETOR,
    )

    assert organisation.email_off_website("anita@gmail.com") == ""
