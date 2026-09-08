"""Onboarding, organisation settings, the team roster and invite redemption."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.messages import get_messages
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from ohc_experience.abdm.models import ReviewItem
from ohc_experience.abdm.models import ReviewQuery
from ohc_experience.abdm.tests.factories import ReviewItemFactory
from ohc_experience.organisations.models import Invitation
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import InvitationFactory
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.organisations.views import INVITATION_SESSION_KEY
from ohc_experience.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpResponse
    from django.test import Client

    from ohc_experience.users.models import User

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
    "verification_document_number": "AAACS1234K",
}


def document():
    return SimpleUploadedFile(
        "pan.pdf",
        b"%PDF-1.4 demo",
        content_type="application/pdf",
    )


TEAM_URL = reverse("organisations:team")


def message_texts(response: HttpResponse) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def htmx_fragment(response: HttpResponse) -> str:
    """The response body with its whitespace squashed to single spaces.

    Lets a test read the markup an htmx response carries without caring how the
    template happened to be wrapped.
    """
    return " ".join(response.content.decode().split())


def opening_tag(html: str, element_id: str) -> str:
    """The opening tag carrying this id, so a test can read its attributes."""
    start = html.rindex("<", 0, html.index(f'id="{element_id}"'))
    return html[start : html.index(">", start) + 1]


@pytest.fixture
def admin_membership(onboarded_organisation: Organisation) -> Membership:
    return MembershipFactory.create(
        organisation=onboarded_organisation,
        role=Role.ADMIN,
        user__name="Nikhil Raj",
    )


@pytest.fixture
def developer_membership(onboarded_organisation: Organisation) -> Membership:
    return MembershipFactory.create(
        organisation=onboarded_organisation,
        role=Role.DEVELOPER,
        user__name="Arun Nair",
    )


class TestOnboardingView:
    def test_requires_login(self, client: Client):
        response = client.get(reverse("organisations:onboarding"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"].startswith(reverse("account_login"))

    def test_renders_the_details_form(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
            user__name="Meera Krishnan",
            user__email="meera@sunrise.in",
        )

        response = sign_in(membership.user).get(reverse("organisations:onboarding"))
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert response.context["form"].instance == organisation
        assert response.context["onboarding_step"] == 2  # noqa: PLR2004
        assert "Submit for verification" in html
        assert 'enctype="multipart/form-data"' in html

    def test_skips_onboarding_once_details_are_submitted(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).get(
            reverse("organisations:onboarding"),
        )

        # Details are in, no product yet: straight on to step three.
        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("products:onboarding-product")

    def test_submitting_creates_the_verification_review(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        response = sign_in(membership.user).post(
            reverse("organisations:onboarding"),
            data={**DETAILS_DATA, "verification_document": document()},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("products:onboarding-product")
        organisation.refresh_from_db()
        assert organisation.has_submitted_details is True
        assert organisation.is_onboarded is True
        assert organisation.is_pending
        assert organisation.district == "Ernakulam"
        item = ReviewItem.objects.get()
        assert item.item_type == ReviewItem.Type.ORGANISATION_VERIFICATION
        assert item.organisation == organisation
        assert message_texts(response) == [
            (
                "Your organisation details are with the NHA review team. "
                "You can register your first product while they verify them."
            ),
        ]

    def test_an_incomplete_form_does_not_submit(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        response = sign_in(membership.user).post(
            reverse("organisations:onboarding"),
            data={**DETAILS_DATA, "name": "", "verification_document": document()},
        )

        assert response.status_code == HTTPStatus.OK
        assert "name" in response.context["form"].errors
        organisation.refresh_from_db()
        assert organisation.has_submitted_details is False
        assert not ReviewItem.objects.exists()

    def test_a_developer_cannot_submit(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.DEVELOPER,
        )

        response = sign_in(membership.user).post(
            reverse("organisations:onboarding"),
            data={**DETAILS_DATA, "verification_document": document()},
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        assert not ReviewItem.objects.exists()

    def test_a_developer_sees_who_has_to_finish_the_step(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.DEVELOPER,
        )

        response = sign_in(membership.user).get(reverse("organisations:onboarding"))
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert '<form id="organisation-form"' not in html
        assert "Only the owner and admins can submit" in html

    def test_an_htmx_submit_hands_the_client_the_redirect(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        response = sign_in(membership.user).post(
            reverse("organisations:onboarding"),
            data={**DETAILS_DATA, "verification_document": document()},
            headers={"HX-Request": "true"},
        )

        # Leaving onboarding is a real navigation, so htmx is told to redirect
        # rather than handed a fragment.
        assert response.status_code == HTTPStatus.OK
        assert response["HX-Redirect"] == reverse("products:onboarding-product")
        organisation.refresh_from_db()
        assert organisation.has_submitted_details is True

    def test_an_htmx_form_error_swaps_the_form_back_in(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        response = sign_in(membership.user).post(
            reverse("organisations:onboarding"),
            data={**DETAILS_DATA, "name": "", "verification_document": document()},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert "HX-Redirect" not in response
        assert '<form id="organisation-form"' in html
        assert "<!DOCTYPE html>" not in html
        assert "This field is required." in html
        organisation.refresh_from_db()
        assert organisation.has_submitted_details is False

    def test_the_form_posts_on_its_own_without_javascript(
        self,
        sign_in: Callable[[User], Client],
        organisation: Organisation,
    ):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        html = (
            sign_in(membership.user)
            .get(
                reverse("organisations:onboarding"),
            )
            .content.decode()
        )

        # The htmx attributes are enhancement: the form has to stay a plain
        # multipart POST to a real URL for a browser without them. Every POST
        # form on the page carries exactly one token.
        assert 'method="post"' in html
        assert f'action="{reverse("organisations:onboarding")}"' in html
        assert 'enctype="multipart/form-data"' in html
        assert html.count("csrfmiddlewaretoken") == html.count('method="post"')


class TestOrganisationDetailView:
    def test_requires_login(self, client: Client):
        response = client.get(reverse("organisations:detail"))

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"].startswith(reverse("account_login"))

    def test_an_admin_can_save_changes(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
    ):
        response = sign_in(admin_membership.user).post(
            reverse("organisations:detail"),
            data={**DETAILS_DATA, "district": "Thiruvananthapuram"},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("organisations:detail")
        admin_membership.organisation.refresh_from_db()
        assert admin_membership.organisation.district == "Thiruvananthapuram"
        # The saved document survives a save that uploads nothing new.
        assert admin_membership.organisation.verification_document
        assert message_texts(response) == ["Organisation details updated."]

    def test_saving_after_a_send_back_resubmits(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
    ):
        organisation = admin_membership.organisation
        item = ReviewItemFactory.create(
            item_type=ReviewItem.Type.ORGANISATION_VERIFICATION,
            compliance=None,
            product=None,
            organisation=organisation,
            status=ReviewItem.Status.SENT_BACK,
        )
        organisation.set_verification(
            Organisation.VerificationStatus.SENT_BACK,
            reason="The PAN does not match the entity name.",
        )

        response = sign_in(admin_membership.user).post(
            reverse("organisations:detail"),
            data={**DETAILS_DATA, "verification_document_number": "AAACS1234L"},
        )

        assert response.status_code == HTTPStatus.FOUND
        organisation.refresh_from_db()
        item.refresh_from_db()
        assert organisation.is_pending
        assert organisation.verification_reason == ""
        assert item.status == ReviewItem.Status.IN_REVIEW
        assert item.resubmission_count == 1
        assert message_texts(response) == [
            "Organisation details updated and resubmitted for verification.",
        ]

    def test_a_developer_can_read_the_details(
        self,
        sign_in: Callable[[User], Client],
        developer_membership: Membership,
    ):
        response = sign_in(developer_membership.user).get(
            reverse("organisations:detail"),
        )

        assert response.status_code == HTTPStatus.OK
        assert response.context["can_manage"] is False
        assert response.context["settings_section"] == "organisation"

    def test_a_developer_cannot_save_changes(
        self,
        sign_in: Callable[[User], Client],
        developer_membership: Membership,
    ):
        response = sign_in(developer_membership.user).post(
            reverse("organisations:detail"),
            data={**DETAILS_DATA, "district": "Thiruvananthapuram"},
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        developer_membership.organisation.refresh_from_db()
        assert developer_membership.organisation.district != "Thiruvananthapuram"

    def test_someone_without_an_organisation_is_refused(
        self,
        sign_in: Callable[[User], Client],
        user: User,
    ):
        response = sign_in(user).get(reverse("organisations:detail"))

        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_an_htmx_save_sends_the_client_back_here(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
    ):
        response = sign_in(admin_membership.user).post(
            reverse("organisations:detail"),
            data={**DETAILS_DATA, "district": "Thiruvananthapuram"},
            headers={"HX-Request": "true"},
        )

        # Saving is a real navigation back to this page, so the verification
        # card and the flash refresh together.
        assert response.status_code == HTTPStatus.OK
        assert response["HX-Redirect"] == reverse("organisations:detail")
        admin_membership.organisation.refresh_from_db()
        assert admin_membership.organisation.district == "Thiruvananthapuram"
        assert message_texts(response) == ["Organisation details updated."]

    def test_an_invalid_htmx_save_swaps_the_errors_in(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
    ):
        response = sign_in(admin_membership.user).post(
            reverse("organisations:detail"),
            data={**DETAILS_DATA, "district": "Thiruvananthapuram", "pincode": "12"},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        # 200, not 4xx: htmx swaps the fragment and the errors become visible.
        assert response.status_code == HTTPStatus.OK
        assert '<form id="organisation-form"' in html
        assert "<!DOCTYPE html>" not in html
        assert "Enter a valid six-digit Indian pincode." in html
        assert "Organisation details updated." not in html
        admin_membership.organisation.refresh_from_db()
        assert admin_membership.organisation.district != "Thiruvananthapuram"

    def test_a_developer_gets_the_read_only_panel_with_no_form(
        self,
        sign_in: Callable[[User], Client],
        developer_membership: Membership,
    ):
        response = sign_in(developer_membership.user).get(
            reverse("organisations:detail"),
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert '<form id="organisation-form"' not in html
        assert "Only the owner and admins can edit the organisation details." in html
        assert "AAACS1234K" in html

    def test_the_form_posts_on_its_own_without_javascript(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
    ):
        html = (
            sign_in(admin_membership.user)
            .get(
                reverse("organisations:detail"),
            )
            .content.decode()
        )

        assert 'method="post"' in html
        assert f'action="{reverse("organisations:detail")}"' in html
        assert html.count("csrfmiddlewaretoken") == html.count('method="post"')

    def test_the_verification_card_lists_open_queries_with_a_reply_box(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
    ):
        item = ReviewItemFactory.create(
            item_type=ReviewItem.Type.ORGANISATION_VERIFICATION,
            compliance=None,
            product=None,
            organisation=admin_membership.organisation,
            status=ReviewItem.Status.QUERY_RAISED,
        )
        query = ReviewQuery.objects.create(
            item=item,
            field_key="verification_document_number",
            field_label="Document number",
            question="Which PAN is the right one?",
            raised_by=UserFactory.create(is_ohc_team=True),
        )

        response = sign_in(admin_membership.user).get(reverse("organisations:detail"))
        html = response.content.decode()

        assert "Which PAN is the right one?" in html
        assert reverse("products:query-reply", args=[query.pk]) in html

    def test_replying_to_a_query_from_the_settings_page(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
    ):
        item = ReviewItemFactory.create(
            item_type=ReviewItem.Type.ORGANISATION_VERIFICATION,
            compliance=None,
            product=None,
            organisation=admin_membership.organisation,
            status=ReviewItem.Status.QUERY_RAISED,
        )
        query = ReviewQuery.objects.create(
            item=item,
            question="Which PAN is the right one?",
            raised_by=UserFactory.create(is_ohc_team=True),
        )

        response = sign_in(admin_membership.user).post(
            reverse("products:query-reply", args=[query.pk]),
            data={"reply": "AAACS1234K.", "next": reverse("organisations:detail")},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("organisations:detail")
        query.refresh_from_db()
        item.refresh_from_db()
        assert query.status == ReviewQuery.Status.ANSWERED
        assert item.status == ReviewItem.Status.IN_REVIEW

    def test_a_query_of_another_organisation_cannot_be_answered(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
    ):
        query = ReviewQuery.objects.create(
            item=ReviewItemFactory.create(),
            question="Not yours.",
            raised_by=UserFactory.create(is_ohc_team=True),
        )

        response = sign_in(admin_membership.user).post(
            reverse("products:query-reply", args=[query.pk]),
            data={"reply": "Hi."},
        )

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestTeamView:
    def test_lists_the_roster_and_the_pending_invites(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
        developer_membership: Membership,
    ):
        invitation = InvitationFactory.create(
            organisation=owner_membership.organisation,
        )
        InvitationFactory.create(
            organisation=owner_membership.organisation,
            revoked=True,
        )

        response = sign_in(owner_membership.user).get(TEAM_URL)

        assert response.status_code == HTTPStatus.OK
        assert response.context["memberships"] == [
            owner_membership,
            developer_membership,
        ]
        assert response.context["member_count"] == len(
            response.context["memberships"],
        )
        assert response.context["invitations"] == [invitation]
        assert response.context["can_manage"] is True

    def test_a_developer_sees_the_roster_read_only(
        self,
        sign_in: Callable[[User], Client],
        developer_membership: Membership,
    ):
        response = sign_in(developer_membership.user).get(TEAM_URL)

        assert response.status_code == HTTPStatus.OK
        assert response.context["can_manage"] is False

    def test_inviting_a_teammate_sends_one_email(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).post(
            TEAM_URL,
            data={"email": "arun@sunrise.in", "role": Role.DEVELOPER},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == TEAM_URL
        invitation = Invitation.objects.get(email="arun@sunrise.in")
        assert invitation.organisation == owner_membership.organisation
        assert invitation.invited_by == owner_membership.user
        assert invitation.role == Role.DEVELOPER
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.to == ["arun@sunrise.in"]
        assert invitation.token in sent.body
        assert message_texts(response) == ["Invite sent to arun@sunrise.in."]

    def test_a_developer_cannot_invite(
        self,
        sign_in: Callable[[User], Client],
        developer_membership: Membership,
    ):
        response = sign_in(developer_membership.user).post(
            TEAM_URL,
            data={"email": "arun@sunrise.in", "role": Role.DEVELOPER},
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        assert Invitation.objects.count() == 0
        assert mail.outbox == []

    def test_a_duplicate_invite_is_shown_as_a_form_error(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        InvitationFactory.create(
            organisation=owner_membership.organisation,
            email="arun@sunrise.in",
        )

        response = sign_in(owner_membership.user).post(
            TEAM_URL,
            data={"email": "arun@sunrise.in", "role": Role.DEVELOPER},
        )

        assert response.status_code == HTTPStatus.OK
        assert response.context["form"].errors["email"] == [
            "An invite is already pending for that address.",
        ]
        assert Invitation.objects.count() == 1
        assert mail.outbox == []

    def test_every_row_carries_the_id_its_actions_target(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
        developer_membership: Membership,
    ):
        invitation = InvitationFactory.create(
            organisation=owner_membership.organisation,
        )

        html = htmx_fragment(sign_in(owner_membership.user).get(TEAM_URL))

        # The page includes the same partials the fragments are rendered from,
        # so these ids are the contract between the two.
        for element_id in (
            f"member-{developer_membership.pk}",
            f"member-card-{developer_membership.pk}",
            f"invitation-{invitation.pk}",
            f"invitation-card-{invitation.pk}",
            "invitation-rows",
            "invite-form",
        ):
            assert f'id="{element_id}"' in html
        # Something is pending, so the heading is on show.
        assert "hidden" not in opening_tag(html, "invitation-heading-row")
        assert "hidden" not in opening_tag(html, "invitation-heading-card")

    def test_the_pending_heading_hides_itself_while_there_is_nothing_pending(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        html = htmx_fragment(sign_in(owner_membership.user).get(TEAM_URL))

        assert "hidden" in opening_tag(html, "invitation-heading-row")
        assert "hidden" in opening_tag(html, "invitation-heading-card")

    def test_an_htmx_invite_returns_the_new_row_and_an_emptied_form(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).post(
            TEAM_URL,
            data={"email": "arun@sunrise.in", "role": Role.DEVELOPER},
            headers={"HX-Request": "true"},
        )
        html = htmx_fragment(response)
        invitation = Invitation.objects.get(email="arun@sunrise.in")

        assert response.status_code == HTTPStatus.OK
        assert "<!DOCTYPE html>" not in html
        # The row is the ordinary swap; the card, the headings and the form all
        # ride along out of band.
        assert f'<tr id="invitation-{invitation.pk}">' in html
        # The card travels inside a carrier that holds the hx-swap-oob, because
        # htmx swaps the children for every style except outerHTML.
        assert (
            '<ul hx-swap-oob="afterend:#invitation-heading-card"> '
            f'<li id="invitation-card-{invitation.pk}"'
        ) in html
        assert "hidden" not in opening_tag(html, "invitation-heading-row")
        assert "hidden" not in opening_tag(html, "invitation-heading-card")
        assert 'hx-swap-oob="outerHTML"' in opening_tag(html, "invite-form")
        # Unbound, so the email field is empty again.
        assert 'value="arun@sunrise.in"' not in html
        assert "Invite sent to arun@sunrise.in." in html
        assert len(mail.outbox) == 1

    def test_an_htmx_duplicate_invite_swaps_the_form_errors_in(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        InvitationFactory.create(
            organisation=owner_membership.organisation,
            email="arun@sunrise.in",
        )

        response = sign_in(owner_membership.user).post(
            TEAM_URL,
            data={"email": "arun@sunrise.in", "role": Role.DEVELOPER},
            headers={"HX-Request": "true"},
        )
        html = htmx_fragment(response)

        # 200, not 4xx: htmx will not swap a 4xx, and a form with errors on it
        # is a perfectly ordinary response.
        assert response.status_code == HTTPStatus.OK
        assert 'hx-swap-oob="outerHTML"' in opening_tag(html, "invite-form")
        assert "An invite is already pending for that address." in html
        # Nothing was created, so nothing is filed into the table.
        assert "<tr " not in html
        assert Invitation.objects.count() == 1
        assert mail.outbox == []


class TestMembershipActions:
    def test_an_admin_can_change_a_role(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
        developer_membership: Membership,
    ):
        response = sign_in(admin_membership.user).post(
            reverse(
                "organisations:member-role",
                kwargs={"pk": developer_membership.pk},
            ),
            data={"role": Role.SUPPORT},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == TEAM_URL
        developer_membership.refresh_from_db()
        assert developer_membership.role == Role.SUPPORT
        assert message_texts(response) == ["Arun Nair is now a Support."]

    def test_the_owners_role_cannot_be_changed(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("organisations:member-role", kwargs={"pk": owner_membership.pk}),
            data={"role": Role.SUPPORT},
        )

        assert response.status_code == HTTPStatus.FOUND
        owner_membership.refresh_from_db()
        assert owner_membership.role == Role.OWNER
        assert message_texts(response) == ["That role change is not allowed."]

    def test_a_developer_cannot_change_roles(
        self,
        sign_in: Callable[[User], Client],
        developer_membership: Membership,
        admin_membership: Membership,
    ):
        response = sign_in(developer_membership.user).post(
            reverse("organisations:member-role", kwargs={"pk": admin_membership.pk}),
            data={"role": Role.SUPPORT},
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        admin_membership.refresh_from_db()
        assert admin_membership.role == Role.ADMIN

    def test_removing_a_member_deletes_the_membership(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
        developer_membership: Membership,
    ):
        response = sign_in(owner_membership.user).post(
            reverse(
                "organisations:member-remove",
                kwargs={"pk": developer_membership.pk},
            ),
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == TEAM_URL
        assert not Membership.objects.filter(pk=developer_membership.pk).exists()
        assert message_texts(response) == ["Arun Nair was removed from the team."]

    def test_the_owner_cannot_be_removed(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("organisations:member-remove", kwargs={"pk": owner_membership.pk}),
        )

        assert response.status_code == HTTPStatus.FOUND
        assert Membership.objects.filter(pk=owner_membership.pk).exists()
        assert message_texts(response) == ["The owner cannot be removed."]

    def test_another_organisations_member_is_not_reachable(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        outsider = MembershipFactory.create(organisation=OrganisationFactory.create())

        response = sign_in(owner_membership.user).post(
            reverse("organisations:member-remove", kwargs={"pk": outsider.pk}),
        )

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert Membership.objects.filter(pk=outsider.pk).exists()

    def test_an_htmx_role_change_swaps_the_row_back_in(
        self,
        sign_in: Callable[[User], Client],
        admin_membership: Membership,
        developer_membership: Membership,
    ):
        response = sign_in(admin_membership.user).post(
            reverse(
                "organisations:member-role",
                kwargs={"pk": developer_membership.pk},
            ),
            data={"role": Role.SUPPORT},
            headers={"HX-Request": "true"},
        )
        html = htmx_fragment(response)

        assert response.status_code == HTTPStatus.OK
        assert "<!DOCTYPE html>" not in html
        assert f'<tr id="member-{developer_membership.pk}">' in html
        assert 'hx-swap-oob="outerHTML"' in opening_tag(
            html,
            f"member-card-{developer_membership.pk}",
        )
        # The picker comes back showing the role the database now holds.
        assert '<option value="support" selected>' in html
        assert "Arun Nair is now a Support." in html
        developer_membership.refresh_from_db()
        assert developer_membership.role == Role.SUPPORT

    def test_an_htmx_role_change_that_is_refused_snaps_the_row_back(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        response = sign_in(owner_membership.user).post(
            reverse("organisations:member-role", kwargs={"pk": owner_membership.pk}),
            data={"role": Role.SUPPORT},
            headers={"HX-Request": "true"},
        )
        html = htmx_fragment(response)

        assert response.status_code == HTTPStatus.OK
        assert f'<tr id="member-{owner_membership.pk}">' in html
        assert "That role change is not allowed." in html
        # The owner has no picker at all, so the row must still say Owner.
        assert "Owner</span>" in html
        owner_membership.refresh_from_db()
        assert owner_membership.role == Role.OWNER

    def test_an_htmx_removal_leaves_nothing_to_put_in_the_row(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
        developer_membership: Membership,
    ):
        response = sign_in(owner_membership.user).post(
            reverse(
                "organisations:member-remove",
                kwargs={"pk": developer_membership.pk},
            ),
            headers={"HX-Request": "true"},
        )
        html = htmx_fragment(response)

        assert response.status_code == HTTPStatus.OK
        # Once htmx lifts the out-of-band parts out there is nothing left, and
        # swapping nothing over the row's outerHTML is what removes it.
        assert f'<tr id="member-{developer_membership.pk}">' not in html
        assert 'hx-swap-oob="delete"' in opening_tag(
            html,
            f"member-card-{developer_membership.pk}",
        )
        assert "Arun Nair was removed from the team." in html
        assert not Membership.objects.filter(pk=developer_membership.pk).exists()


class TestInvitationActions:
    def test_resending_issues_a_new_token_and_another_email(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        invitation = InvitationFactory.create(
            organisation=owner_membership.organisation,
            email="arun@sunrise.in",
            expired=True,
        )
        old_token = invitation.token

        response = sign_in(owner_membership.user).post(
            reverse(
                "organisations:invitation-resend",
                kwargs={"pk": invitation.pk},
            ),
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == TEAM_URL
        invitation.refresh_from_db()
        assert invitation.token != old_token
        assert invitation.is_pending is True
        assert len(mail.outbox) == 1
        assert invitation.token in mail.outbox[0].body
        assert message_texts(response) == ["Invite resent to arun@sunrise.in."]

    def test_revoking_takes_the_invite_out_of_the_pending_list(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        invitation = InvitationFactory.create(
            organisation=owner_membership.organisation,
            email="arun@sunrise.in",
        )

        response = sign_in(owner_membership.user).post(
            reverse(
                "organisations:invitation-revoke",
                kwargs={"pk": invitation.pk},
            ),
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == TEAM_URL
        invitation.refresh_from_db()
        assert invitation.revoked_at is not None
        assert list(owner_membership.organisation.invitations.pending()) == []
        assert message_texts(response) == ["Invite to arun@sunrise.in revoked."]

    def test_an_htmx_resend_swaps_the_row_back_in(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        invitation = InvitationFactory.create(
            organisation=owner_membership.organisation,
            email="arun@sunrise.in",
        )
        old_token = invitation.token
        old_expiry = invitation.expires_at

        response = sign_in(owner_membership.user).post(
            reverse(
                "organisations:invitation-resend",
                kwargs={"pk": invitation.pk},
            ),
            headers={"HX-Request": "true"},
        )
        html = htmx_fragment(response)

        assert response.status_code == HTTPStatus.OK
        assert "<!DOCTYPE html>" not in html
        assert f'<tr id="invitation-{invitation.pk}">' in html
        assert 'hx-swap-oob="outerHTML"' in opening_tag(
            html,
            f"invitation-card-{invitation.pk}",
        )
        assert "Invite resent to arun@sunrise.in." in html
        invitation.refresh_from_db()
        # The row was re-rendered because the invite it draws has moved on.
        assert invitation.token != old_token
        assert invitation.expires_at > old_expiry
        assert len(mail.outbox) == 1

    def test_an_htmx_revoke_leaves_nothing_to_put_in_the_row(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        invitation = InvitationFactory.create(
            organisation=owner_membership.organisation,
            email="arun@sunrise.in",
        )

        response = sign_in(owner_membership.user).post(
            reverse(
                "organisations:invitation-revoke",
                kwargs={"pk": invitation.pk},
            ),
            headers={"HX-Request": "true"},
        )
        html = htmx_fragment(response)

        assert response.status_code == HTTPStatus.OK
        assert f'<tr id="invitation-{invitation.pk}">' not in html
        assert 'hx-swap-oob="delete"' in opening_tag(
            html,
            f"invitation-card-{invitation.pk}",
        )
        # That was the last one pending, so both headings hide themselves again.
        assert "hidden" in opening_tag(html, "invitation-heading-row")
        assert "hidden" in opening_tag(html, "invitation-heading-card")
        assert "Invite to arun@sunrise.in revoked." in html
        invitation.refresh_from_db()
        assert invitation.revoked_at is not None

    def test_a_developer_cannot_revoke(
        self,
        sign_in: Callable[[User], Client],
        developer_membership: Membership,
    ):
        invitation = InvitationFactory.create(
            organisation=developer_membership.organisation,
        )

        response = sign_in(developer_membership.user).post(
            reverse(
                "organisations:invitation-revoke",
                kwargs={"pk": invitation.pk},
            ),
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        invitation.refresh_from_db()
        assert invitation.revoked_at is None

    def test_another_organisations_invite_is_not_reachable(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        invitation = InvitationFactory.create()

        response = sign_in(owner_membership.user).post(
            reverse(
                "organisations:invitation-revoke",
                kwargs={"pk": invitation.pk},
            ),
        )

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestInvitationAcceptView:
    def test_an_anonymous_visitor_is_parked_at_signup(
        self,
        client: Client,
        onboarded_organisation: Organisation,
    ):
        invitation = InvitationFactory.create(organisation=onboarded_organisation)

        response = client.get(invitation.get_absolute_url())

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("account_signup")
        assert client.session[INVITATION_SESSION_KEY] == invitation.token

    def test_a_signed_in_invitee_joins_the_organisation(
        self,
        sign_in: Callable[[User], Client],
        onboarded_organisation: Organisation,
    ):
        user = UserFactory.create(email="arun@sunrise.in")
        invitation = InvitationFactory.create(
            organisation=onboarded_organisation,
            email="Arun@Sunrise.in",
            role=Role.SUPPORT,
        )

        response = sign_in(user).get(invitation.get_absolute_url())

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("dashboard")
        membership = Membership.objects.get(user=user)
        assert membership.organisation == onboarded_organisation
        assert membership.role == Role.SUPPORT
        invitation.refresh_from_db()
        assert invitation.accepted_at is not None
        assert message_texts(response) == [
            f"You joined {onboarded_organisation.name}.",
        ]

    def test_a_different_address_is_refused(
        self,
        sign_in: Callable[[User], Client],
        onboarded_organisation: Organisation,
    ):
        user = UserFactory.create(email="someone-else@sunrise.in")
        invitation = InvitationFactory.create(
            organisation=onboarded_organisation,
            email="arun@sunrise.in",
        )

        response = sign_in(user).get(invitation.get_absolute_url())

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("home")
        assert not Membership.objects.filter(user=user).exists()
        assert message_texts(response) == [
            "This invite was sent to a different address.",
        ]

    def test_someone_who_already_has_an_organisation_is_refused(
        self,
        sign_in: Callable[[User], Client],
        owner_membership: Membership,
    ):
        invitation = InvitationFactory.create(email=owner_membership.user.email)

        response = sign_in(owner_membership.user).get(invitation.get_absolute_url())

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("dashboard")
        assert Membership.objects.filter(user=owner_membership.user).count() == 1
        invitation.refresh_from_db()
        assert invitation.accepted_at is None

    @pytest.mark.parametrize("state", ["expired", "revoked"])
    def test_a_dead_token_is_refused(
        self,
        sign_in: Callable[[User], Client],
        onboarded_organisation: Organisation,
        state: str,
    ):
        user = UserFactory.create(email="arun@sunrise.in")
        invitation = InvitationFactory.create(
            organisation=onboarded_organisation,
            email=user.email,
            **{state: True},
        )

        response = sign_in(user).get(invitation.get_absolute_url())

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("home")
        assert not Membership.objects.filter(user=user).exists()
        assert message_texts(response) == [
            "That invite link is no longer valid. Ask for a fresh one.",
        ]

    def test_an_unknown_token_is_refused(self, client: Client):
        response = client.get(
            reverse(
                "organisations:invitation-accept",
                kwargs={"token": "not-a-real-token"},
            ),
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == reverse("home")
        assert INVITATION_SESSION_KEY not in client.session
