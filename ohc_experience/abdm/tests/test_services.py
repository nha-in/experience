"""The write boundary: organisation verification submissions and query replies."""

from __future__ import annotations

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.utils import timezone

from ohc_experience.abdm import services
from ohc_experience.abdm.models import AuditLog
from ohc_experience.abdm.models import ReviewHistory
from ohc_experience.abdm.models import ReviewItem
from ohc_experience.abdm.models import ReviewQuery
from ohc_experience.abdm.tests.factories import ReviewItemFactory
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def reviewer(db):
    return UserFactory.create(
        name="Nandita Shah",
        email="nandita@nha.gov.in",
        is_ohc_team=True,
    )


def history_kinds(item) -> list[str]:
    return list(item.history.order_by("created_at").values_list("kind", flat=True))


class TestOrganisationVerification:
    def test_submitting_creates_the_review_item(self, owner_membership, reviewer):
        organisation = owner_membership.organisation

        item = services.submit_organisation_for_verification(
            organisation=organisation,
            user=owner_membership.user,
        )

        organisation.refresh_from_db()
        assert item.item_type == ReviewItem.Type.ORGANISATION_VERIFICATION
        assert item.status == ReviewItem.Status.NEW
        assert item.organisation == organisation
        assert organisation.is_pending
        assert organisation.verification_submitted_at is not None
        assert history_kinds(item) == [ReviewHistory.Kind.SUBMITTED]
        assert AuditLog.objects.filter(
            action="submit_verification",
            model_label="organisations.organisation",
        ).exists()
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [reviewer.email]
        assert "Organisation verification" in mail.outbox[0].subject
        assert item.reference in mail.outbox[0].body

    def test_incomplete_details_are_refused(self, organisation):
        membership = MembershipFactory.create(
            organisation=organisation,
            role=Role.OWNER,
        )

        with pytest.raises(ValidationError, match="Complete these details first"):
            services.submit_organisation_for_verification(
                organisation=organisation,
                user=membership.user,
            )

        assert not ReviewItem.objects.exists()

    def test_a_developer_cannot_submit(self, onboarded_organisation):
        membership = MembershipFactory.create(
            organisation=onboarded_organisation,
            role=Role.DEVELOPER,
        )

        with pytest.raises(PermissionDenied):
            services.submit_organisation_for_verification(
                organisation=onboarded_organisation,
                user=membership.user,
            )

    def test_submitting_twice_is_refused_while_under_review(self, owner_membership):
        services.submit_organisation_for_verification(
            organisation=owner_membership.organisation,
            user=owner_membership.user,
        )

        with pytest.raises(ValidationError, match="already with the review team"):
            services.submit_organisation_for_verification(
                organisation=owner_membership.organisation,
                user=owner_membership.user,
            )

    def test_resubmission_after_a_send_back_reuses_the_item(
        self,
        owner_membership,
        reviewer,
    ):
        organisation = owner_membership.organisation
        item = services.submit_organisation_for_verification(
            organisation=organisation,
            user=owner_membership.user,
        )
        # What send_back() will do in a later task, done by hand here.
        item.status = ReviewItem.Status.SENT_BACK
        item.decided_on = timezone.localdate()
        item.decided_by = reviewer
        item.decision_note = "The PAN does not match the entity name."
        item.save()
        organisation.set_verification(
            Organisation.VerificationStatus.SENT_BACK,
            actor=reviewer,
            reason="The PAN does not match the entity name.",
        )

        resubmitted = services.submit_organisation_for_verification(
            organisation=organisation,
            user=owner_membership.user,
        )

        organisation.refresh_from_db()
        assert resubmitted.pk == item.pk
        assert resubmitted.status == ReviewItem.Status.IN_REVIEW
        assert resubmitted.resubmission_count == 1
        assert resubmitted.decided_on is None
        assert resubmitted.decision_note == ""
        assert organisation.is_pending
        assert organisation.verification_reason == ""
        assert history_kinds(resubmitted) == [
            ReviewHistory.Kind.SUBMITTED,
            ReviewHistory.Kind.RESUBMITTED,
        ]

    def test_a_verified_organisation_cannot_resubmit(self, owner_membership):
        organisation = owner_membership.organisation
        organisation.set_verification(Organisation.VerificationStatus.VERIFIED)

        with pytest.raises(ValidationError, match="already verified"):
            services.submit_organisation_for_verification(
                organisation=organisation,
                user=owner_membership.user,
            )


class TestQueryReply:
    @pytest.fixture
    def query(self, owner_membership, reviewer) -> ReviewQuery:
        item = ReviewItemFactory.create(
            item_type=ReviewItem.Type.ORGANISATION_VERIFICATION,
            compliance=None,
            product=None,
            organisation=owner_membership.organisation,
            status=ReviewItem.Status.QUERY_RAISED,
            assignee=reviewer,
        )
        return ReviewQuery.objects.create(
            item=item,
            field_key="verification_document_number",
            field_label="Document number",
            question="The PAN on the document reads AAACS1234L. Which is right?",
            raised_by=reviewer,
        )

    def test_a_reply_answers_the_query_and_resumes_the_review(
        self,
        query,
        owner_membership,
        reviewer,
    ):
        answered = services.reply_to_query(
            query=query,
            user=owner_membership.user,
            reply="AAACS1234K is correct; the scan is smudged.",
        )

        item = answered.item
        item.refresh_from_db()
        assert answered.status == ReviewQuery.Status.ANSWERED
        assert answered.replied_by == owner_membership.user
        assert answered.replied_at is not None
        assert item.status == ReviewItem.Status.IN_REVIEW
        assert history_kinds(item) == [ReviewHistory.Kind.QUERY_ANSWERED]
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [reviewer.email]
        assert "AAACS1234K is correct" in mail.outbox[0].body

    def test_the_item_stays_paused_while_another_query_is_open(
        self,
        query,
        owner_membership,
        reviewer,
    ):
        ReviewQuery.objects.create(
            item=query.item,
            question="Also attach the GST certificate.",
            raised_by=reviewer,
        )

        services.reply_to_query(
            query=query,
            user=owner_membership.user,
            reply="Done.",
        )

        query.item.refresh_from_db()
        assert query.item.status == ReviewItem.Status.QUERY_RAISED

    def test_an_answered_query_cannot_be_answered_again(
        self,
        query,
        owner_membership,
    ):
        services.reply_to_query(query=query, user=owner_membership.user, reply="Yes.")

        with pytest.raises(ValidationError, match="already been answered"):
            services.reply_to_query(
                query=query,
                user=owner_membership.user,
                reply="No.",
            )

    def test_a_support_member_cannot_reply(self, query, owner_membership):
        support = MembershipFactory.create(
            organisation=owner_membership.organisation,
            role=Role.SUPPORT,
        )

        with pytest.raises(PermissionDenied):
            services.reply_to_query(query=query, user=support.user, reply="Hi.")

    def test_a_member_of_another_organisation_cannot_reply(self, query):
        outsider = MembershipFactory.create(role=Role.OWNER)

        with pytest.raises(PermissionDenied):
            services.reply_to_query(query=query, user=outsider.user, reply="Hi.")
