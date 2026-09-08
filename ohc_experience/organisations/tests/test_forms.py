"""Form rules: company profile, invites and role changes."""

from __future__ import annotations

import pytest

from ohc_experience.organisations.forms import InvitationForm
from ohc_experience.organisations.forms import MembershipRoleForm
from ohc_experience.organisations.models import Invitation
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import InvitationFactory
from ohc_experience.organisations.tests.factories import MembershipFactory

pytestmark = pytest.mark.django_db


class TestInvitationForm:
    def test_creates_a_pending_invite(self, organisation: Organisation):
        form = InvitationForm(
            {"email": "Arun@Sunrise.in", "role": Role.SUPPORT},
            organisation=organisation,
        )

        assert form.is_valid(), form.errors
        invitation = form.save()

        assert invitation.organisation == organisation
        assert invitation.email == "arun@sunrise.in"
        assert invitation.role == Role.SUPPORT
        assert invitation.is_pending is True

    def test_owner_cannot_be_handed_out(self, organisation: Organisation):
        form = InvitationForm(
            {"email": "arun@sunrise.in", "role": Role.OWNER},
            organisation=organisation,
        )

        assert not form.is_valid()
        assert "role" in form.errors

    def test_rejects_someone_who_is_already_on_the_team(
        self,
        organisation: Organisation,
    ):
        member = MembershipFactory.create(
            organisation=organisation,
            user__email="arun@sunrise.in",
        )
        form = InvitationForm(
            {"email": member.user.email.upper(), "role": Role.DEVELOPER},
            organisation=organisation,
        )

        assert not form.is_valid()
        assert form.errors["email"] == ["That person is already on your team."]

    def test_rejects_a_second_pending_invite_for_the_same_address(
        self,
        organisation: Organisation,
    ):
        InvitationFactory.create(organisation=organisation, email="arun@sunrise.in")
        form = InvitationForm(
            {"email": "ARUN@sunrise.in", "role": Role.DEVELOPER},
            organisation=organisation,
        )

        assert not form.is_valid()
        assert form.errors["email"] == [
            "An invite is already pending for that address.",
        ]

    def test_a_pending_invite_elsewhere_does_not_block_this_team(
        self,
        organisation: Organisation,
    ):
        InvitationFactory.create(email="arun@sunrise.in")
        form = InvitationForm(
            {"email": "arun@sunrise.in", "role": Role.DEVELOPER},
            organisation=organisation,
        )

        assert form.is_valid(), form.errors

    def test_reuses_a_revoked_row_instead_of_tripping_the_unique_constraint(
        self,
        organisation: Organisation,
    ):
        revoked = InvitationFactory.create(
            organisation=organisation,
            email="arun@sunrise.in",
            role=Role.SUPPORT,
            revoked=True,
        )
        form = InvitationForm(
            {"email": "arun@sunrise.in", "role": Role.ADMIN},
            organisation=organisation,
        )

        assert form.is_valid(), form.errors
        invitation = form.save()

        assert invitation.pk == revoked.pk
        assert invitation.revoked_at is None
        assert invitation.role == Role.ADMIN
        assert invitation.token != revoked.token
        assert invitation.is_pending is True
        assert Invitation.objects.filter(organisation=organisation).count() == 1

    def test_reuses_an_expired_row(self, organisation: Organisation):
        expired = InvitationFactory.create(
            organisation=organisation,
            email="arun@sunrise.in",
            expired=True,
        )
        form = InvitationForm(
            {"email": "arun@sunrise.in", "role": Role.DEVELOPER},
            organisation=organisation,
        )

        assert form.is_valid(), form.errors
        invitation = form.save()

        assert invitation.pk == expired.pk
        assert invitation.is_pending is True
        assert Invitation.objects.filter(organisation=organisation).count() == 1

    def test_reuses_an_accepted_row_when_the_person_left_and_is_re_invited(
        self,
        organisation: Organisation,
    ):
        accepted = InvitationFactory.create(
            organisation=organisation,
            email="arun@sunrise.in",
            accepted=True,
        )
        form = InvitationForm(
            {"email": "arun@sunrise.in", "role": Role.DEVELOPER},
            organisation=organisation,
        )

        assert form.is_valid(), form.errors
        invitation = form.save()

        assert invitation.pk == accepted.pk
        assert invitation.accepted_at is None
        assert Invitation.objects.filter(organisation=organisation).count() == 1


class TestMembershipRoleForm:
    def test_changes_a_members_role(self, organisation: Organisation):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.DEVELOPER,
        )
        form = MembershipRoleForm({"role": Role.ADMIN}, instance=membership)

        assert form.is_valid(), form.errors
        assert form.save().role == Role.ADMIN

    def test_refuses_to_change_the_owners_role(self, organisation: Organisation):
        owner = MembershipFactory.create(organisation=organisation, role=Role.OWNER)
        form = MembershipRoleForm({"role": Role.ADMIN}, instance=owner)

        assert not form.is_valid()
        assert form.errors["role"] == ["The owner's role cannot be changed."]
        owner.refresh_from_db()
        assert owner.role == Role.OWNER

    def test_refuses_to_promote_someone_to_owner(self, organisation: Organisation):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.DEVELOPER,
        )
        form = MembershipRoleForm({"role": Role.OWNER}, instance=membership)

        assert not form.is_valid()
        assert "role" in form.errors


def test_invitation_form_defaults_to_developer(organisation: Organisation):
    form = InvitationForm(organisation=organisation)

    assert form.fields["role"].initial == Role.DEVELOPER
    assert Role.OWNER not in dict(form.fields["role"].choices)
