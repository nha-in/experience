"""Form rules: organisation details, invites and role changes."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from ohc_experience.organisations.forms import InvitationForm
from ohc_experience.organisations.forms import MembershipRoleForm
from ohc_experience.organisations.forms import OrganisationProfileForm
from ohc_experience.organisations.models import Invitation
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import InvitationFactory
from ohc_experience.organisations.tests.factories import MembershipFactory

pytestmark = pytest.mark.django_db

DETAILS_DATA = {
    "name": "Sunrise Health Systems",
    "description": "Hospital information system for district hospitals.",
    "entity_type": "private_company",
    "category": "india_entity",
    "website": "https://sunrise.in",
    "registered_address": "12 MG Road, Kochi",
    "pincode": "682001",
    "state": "Kerala",
    "district": "Ernakulam",
    "verification_document_type": "PAN",
    "verification_document_number": "aaacs1234k",
}


def document(name: str = "pan.pdf", body: bytes = b"%PDF-1.4 demo"):
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def details_form(organisation, data=None, files=None) -> OrganisationProfileForm:
    return OrganisationProfileForm(
        data if data is not None else DETAILS_DATA,
        files if files is not None else {"verification_document": document()},
        instance=organisation,
    )


class TestOrganisationProfileForm:
    def test_saves_the_details_and_upper_cases_the_document_number(
        self,
        organisation: Organisation,
    ):
        form = details_form(organisation)

        assert form.is_valid(), form.errors
        saved = form.save()

        assert saved.entity_type == Organisation.EntityType.PRIVATE_COMPANY
        assert saved.district == "Ernakulam"
        assert saved.verification_document_number == "AAACS1234K"
        assert saved.verification_document.name.endswith("pan.pdf")
        assert saved.details_complete is True

    @pytest.mark.parametrize(
        "missing",
        [
            "name",
            "description",
            "entity_type",
            "category",
            "registered_address",
            "pincode",
            "state",
            "district",
            "verification_document_type",
            "verification_document_number",
        ],
    )
    def test_requires_every_detail_the_reviewer_needs(
        self,
        organisation: Organisation,
        missing: str,
    ):
        form = details_form(organisation, {**DETAILS_DATA, missing: ""})

        assert not form.is_valid()
        assert missing in form.errors

    def test_website_and_logo_are_optional(self, organisation: Organisation):
        form = details_form(organisation, {**DETAILS_DATA, "website": ""})

        assert form.is_valid(), form.errors

    def test_the_supporting_document_is_required(self, organisation: Organisation):
        form = details_form(organisation, files={})

        assert not form.is_valid()
        assert form.errors["verification_document"] == [
            "Upload the supporting document.",
        ]

    def test_a_saved_document_satisfies_the_requirement(
        self,
        onboarded_organisation: Organisation,
    ):
        form = details_form(onboarded_organisation, files={})

        assert form.is_valid(), form.errors

    def test_removing_the_saved_document_without_a_replacement_is_refused(
        self,
        onboarded_organisation: Organisation,
    ):
        form = details_form(
            onboarded_organisation,
            {
                **DETAILS_DATA,
                "remove_files__verification_document": "verification_document",
            },
            files={},
        )

        assert not form.is_valid()
        assert "verification_document" in form.errors

    @pytest.mark.parametrize(
        ("document_type", "number"),
        [
            ("PAN", "AAACS1234"),
            ("GSTIN", "32AAACS1234K1Z"),
            ("CIN", "U72900KA2024PTC12345"),
        ],
    )
    def test_document_numbers_must_match_their_format(
        self,
        organisation: Organisation,
        document_type: str,
        number: str,
    ):
        form = details_form(
            organisation,
            {
                **DETAILS_DATA,
                "verification_document_type": document_type,
                "verification_document_number": number,
            },
        )

        assert not form.is_valid()
        assert "verification_document_number" in form.errors

    def test_a_gstin_and_a_cin_are_accepted(self, organisation: Organisation):
        for document_type, number in (
            ("GSTIN", "32AAACS1234K1Z5"),
            ("CIN", "U72900KA2024PTC123456"),
        ):
            form = details_form(
                organisation,
                {
                    **DETAILS_DATA,
                    "verification_document_type": document_type,
                    "verification_document_number": number,
                },
            )

            assert form.is_valid(), form.errors

    def test_the_pincode_must_be_six_digits(self, organisation: Organisation):
        form = details_form(organisation, {**DETAILS_DATA, "pincode": "12345"})

        assert not form.is_valid()
        assert form.errors["pincode"] == ["Enter a valid six-digit Indian pincode."]

    def test_the_document_must_be_a_pdf_or_image(self, organisation: Organisation):
        form = details_form(
            organisation,
            files={"verification_document": document("pan.exe", b"MZ")},
        )

        assert not form.is_valid()
        assert "file types" in form.errors["verification_document"][0]

    def test_the_logo_must_be_an_image(self, organisation: Organisation):
        form = details_form(
            organisation,
            files={"verification_document": document(), "logo": document("logo.pdf")},
        )

        assert not form.is_valid()
        assert "logo" in form.errors


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
