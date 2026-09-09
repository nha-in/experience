"""The reviewer side: the gate, the queue, the review detail and every decision."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm import services
from ohc_experience.abdm.forms import ExitRequestForm
from ohc_experience.abdm.models import ComplianceRecord
from ohc_experience.abdm.models import Product
from ohc_experience.abdm.models import ReviewHistory
from ohc_experience.abdm.models import ReviewItem
from ohc_experience.organisations.models import Organisation
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import UserFactory

from .test_tracks_views import exit_data
from .test_tracks_views import pdf

pytestmark = pytest.mark.django_db


@pytest.fixture
def reviewer(db):
    return UserFactory.create(
        name="Nandita Shah",
        email="nandita@nha.gov.in",
        is_ohc_team=True,
    )


@pytest.fixture
def admin(db):
    return UserFactory.create(
        name="Portal Admin",
        email="admin@nha.gov.in",
        is_ohc_team=True,
        is_superuser=True,
        is_staff=True,
    )


@pytest.fixture
def exit_item(product, owner_membership) -> ReviewItem:
    record = product.compliance_records.get(milestone_code="M1")
    form = ExitRequestForm(
        exit_data(),
        {
            "functional_certificate": pdf("cert.pdf"),
            "functional_report": pdf("rep.pdf"),
        },
        instance=record,
    )
    assert form.is_valid(), form.errors
    services.save_exit_draft(record=record, user=owner_membership.user, form=form)
    item = services.request_exit(record=record, user=owner_membership.user)
    mail.outbox.clear()
    return item


@pytest.fixture
def organisation_item(owner_membership) -> ReviewItem:
    item = services.submit_organisation_for_verification(
        organisation=owner_membership.organisation,
        user=owner_membership.user,
    )
    mail.outbox.clear()
    return item


def review_url(item, name="review", **kwargs) -> str:
    return reverse(f"assess:{name}", kwargs={"reference": item.reference, **kwargs})


def history_kinds(item) -> list[str]:
    return list(item.history.order_by("created_at").values_list("kind", flat=True))


# ── the gate ───────────────────────────────────────────────────────────────


def gated_urls(item) -> list[tuple[str, str]]:
    return [
        ("get", reverse("assess:dashboard")),
        ("get", reverse("assess:queue")),
        ("get", review_url(item)),
        ("post", review_url(item, "review-start")),
        ("post", review_url(item, "review-assign")),
        ("post", review_url(item, "review-query")),
        ("post", review_url(item, "review-approve")),
        ("post", review_url(item, "review-send-back")),
    ]


class TestTheGate:
    def test_anonymous_is_sent_to_sign_in(self, client, exit_item):
        for method, url in gated_urls(exit_item):
            response = getattr(client, method)(url)

            assert response.status_code == HTTPStatus.FOUND, url
            assert response.url.startswith(reverse("account_login")), url

    def test_an_integrator_is_refused_even_for_their_own_item(
        self,
        sign_in,
        owner_membership,
        exit_item,
    ):
        client = sign_in(owner_membership.user)

        for method, url in gated_urls(exit_item):
            response = getattr(client, method)(url)

            assert response.status_code == HTTPStatus.FORBIDDEN, url
        exit_item.refresh_from_db()
        assert exit_item.status == ReviewItem.Status.NEW

    def test_a_reviewer_gets_through(self, sign_in, reviewer, exit_item):
        client = sign_in(reviewer)

        for url in (
            reverse("assess:dashboard"),
            reverse("assess:queue"),
            review_url(exit_item),
        ):
            assert client.get(url).status_code == HTTPStatus.OK, url


# ── queue ──────────────────────────────────────────────────────────────────


class TestQueue:
    def test_oldest_first_with_type_chips_and_mine(
        self,
        sign_in,
        reviewer,
        exit_item,
        organisation_item,
    ):
        ReviewItem.objects.filter(pk=organisation_item.pk).update(
            submitted_on=timezone.now() - timedelta(days=12),
        )
        services.assign_reviewer(
            item=exit_item,
            actor=UserFactory.create(is_superuser=True, is_ohc_team=True),
            assignee=reviewer,
        )
        client = sign_in(reviewer)
        registration = exit_item.product.review_items.get(
            item_type=ReviewItem.Type.PRODUCT_REGISTRATION,
        )

        response = client.get(reverse("assess:queue"))
        assert [item.pk for item in response.context["items"]] == [
            organisation_item.pk,
            registration.pk,
            exit_item.pk,
        ]
        assert "12 days" in response.content.decode()

        exits = client.get(reverse("assess:queue"), {"chip": "exit_request"})
        assert [item.pk for item in exits.context["items"]] == [exit_item.pk]

        mine = client.get(reverse("assess:queue"), {"chip": "mine"})
        assert [item.pk for item in mine.context["items"]] == [exit_item.pk]

        organisations = client.get(
            reverse("assess:queue"),
            {"chip": "organisation_verification"},
        )
        assert [item.pk for item in organisations.context["items"]] == [
            organisation_item.pk,
        ]

    def test_decided_items_leave_the_open_queue(self, sign_in, reviewer, exit_item):
        services.approve(item=exit_item, user=reviewer)
        client = sign_in(reviewer)

        open_pks = [
            item.pk for item in client.get(reverse("assess:queue")).context["items"]
        ]
        assert exit_item.pk not in open_pks
        decided = client.get(reverse("assess:queue"), {"scope": "decided"})
        assert [item.pk for item in decided.context["items"]] == [exit_item.pk]

    def test_an_htmx_chip_returns_only_the_results(self, sign_in, reviewer, exit_item):
        response = sign_in(reviewer).get(
            reverse("assess:queue"),
            {"chip": "exit_request"},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert 'id="queue-results"' in html
        assert 'id="ohc-nav"' not in html


# ── decisions ──────────────────────────────────────────────────────────────


class TestDecisionServices:
    def test_approving_an_exit_request_unlocks_the_next_milestone(
        self,
        exit_item,
        reviewer,
        owner_membership,
    ):
        services.approve(
            item=exit_item,
            user=reviewer,
            approved_on=timezone.localdate(),
            note="All M1 flows verified.",
        )

        exit_item.refresh_from_db()
        record = exit_item.compliance
        record.refresh_from_db()
        product = record.product
        assert exit_item.status == ReviewItem.Status.APPROVED
        assert exit_item.decided_by == reviewer
        assert record.status == ComplianceRecord.Status.APPROVED
        assert record.approved_by == reviewer
        assert record.decision_note == "All M1 flows verified."
        next_record = product.compliance_records.get(milestone_code="M2")
        assert next_record.status == ComplianceRecord.Status.OPEN
        assert history_kinds(exit_item)[-1] == ReviewHistory.Kind.APPROVED
        assert owner_membership.user.email in mail.outbox[-1].to
        assert "approved" in mail.outbox[-1].subject.lower()
        assert "Production onboarding" in mail.outbox[-1].body

    def test_approving_a_verification_verifies_and_issues_credentials(
        self,
        organisation_item,
        product,
        reviewer,
    ):
        services.approve(item=organisation_item, user=reviewer)

        organisation = organisation_item.organisation
        organisation.refresh_from_db()
        product.refresh_from_db()
        assert organisation.is_verified
        assert organisation.verified_by == reviewer
        assert product.credential.is_active
        subjects = [message.subject for message in mail.outbox]
        assert any(
            "Organisation verification approved" in subject for subject in subjects
        )
        assert any("credentials issued" in subject.lower() for subject in subjects)

    def test_approving_a_registration_marks_the_product_registered(
        self,
        product,
        reviewer,
    ):
        item = product.review_items.get(item_type=ReviewItem.Type.PRODUCT_REGISTRATION)

        services.approve(item=item, user=reviewer)

        product.refresh_from_db()
        assert product.registration_status == Product.RegistrationStatus.REGISTERED
        assert product.registered_by == reviewer

    def test_sending_back_unlocks_the_form_with_the_reason(
        self,
        exit_item,
        reviewer,
        owner_membership,
    ):
        services.send_back(
            item=exit_item,
            user=reviewer,
            reason="The report is unsigned.",
        )

        exit_item.refresh_from_db()
        record = exit_item.compliance
        record.refresh_from_db()
        assert exit_item.status == ReviewItem.Status.SENT_BACK
        assert record.status == ComplianceRecord.Status.IN_PROGRESS
        assert record.sent_back_by == reviewer
        assert record.sent_back_reason == "The report is unsigned."
        assert record.was_sent_back
        assert "sent back" in mail.outbox[-1].subject.lower()
        assert "The report is unsigned." in mail.outbox[-1].body

    def test_sending_back_a_verification_and_a_registration(
        self,
        organisation_item,
        product,
        reviewer,
    ):
        services.send_back(item=organisation_item, user=reviewer, reason="Wrong PAN.")
        registration = product.review_items.get(
            item_type=ReviewItem.Type.PRODUCT_REGISTRATION,
        )
        services.send_back(item=registration, user=reviewer, reason="Name the product.")

        organisation = organisation_item.organisation
        organisation.refresh_from_db()
        product.refresh_from_db()
        assert organisation.is_sent_back
        assert organisation.verification_reason == "Wrong PAN."
        assert product.registration_status == Product.RegistrationStatus.SENT_BACK
        assert product.sent_back_reason == "Name the product."

    def test_send_back_needs_a_reason(self, exit_item, reviewer):
        with pytest.raises(ValidationError, match="reason"):
            services.send_back(item=exit_item, user=reviewer, reason="  ")

    def test_a_query_pauses_the_item_and_the_record(
        self,
        exit_item,
        reviewer,
        owner_membership,
    ):
        query = services.raise_query(
            item=exit_item,
            user=reviewer,
            field_key="wasa_agency",
            field_label="WASA audit agency",
            question="Is this agency CERT-In empanelled?",
        )

        exit_item.refresh_from_db()
        record = exit_item.compliance
        record.refresh_from_db()
        assert exit_item.status == ReviewItem.Status.QUERY_RAISED
        assert record.status == ComplianceRecord.Status.QUERY_RAISED
        assert query.against_label == "WASA audit agency"
        assert owner_membership.user.email in mail.outbox[-1].to
        assert "Query on your" in mail.outbox[-1].subject

        with pytest.raises(ValidationError, match="open query"):
            services.approve(item=exit_item, user=reviewer)

    def test_answer_then_resolve_returns_the_item_to_review(
        self,
        exit_item,
        reviewer,
        owner_membership,
    ):
        query = services.raise_query(
            item=exit_item,
            user=reviewer,
            field_key="form",
            field_label="",
            question="Attach the demo recording link too.",
        )
        services.reply_to_query(
            query=query,
            user=owner_membership.user,
            reply="Attached.",
        )
        exit_item.refresh_from_db()
        assert exit_item.status == ReviewItem.Status.IN_REVIEW

        services.resolve_query(query=query, user=reviewer)

        query.refresh_from_db()
        assert query.is_resolved
        assert query.resolved_by == reviewer
        assert history_kinds(exit_item)[-1] == ReviewHistory.Kind.QUERY_RESOLVED

    def test_start_review_and_assignment(self, exit_item, reviewer, admin):
        services.start_review(item=exit_item, user=reviewer)
        exit_item.refresh_from_db()
        assert exit_item.status == ReviewItem.Status.IN_REVIEW

        with pytest.raises(PermissionDenied):
            services.assign_reviewer(item=exit_item, actor=reviewer, assignee=reviewer)

        services.assign_reviewer(item=exit_item, actor=admin, assignee=reviewer)
        exit_item.refresh_from_db()
        assert exit_item.assignee == reviewer
        assert exit_item.assigned_by == admin
        assert history_kinds(exit_item)[-1] == ReviewHistory.Kind.ASSIGNED

        integrator = MembershipFactory.create(role=Role.OWNER).user
        with pytest.raises(ValidationError, match="Only NHA reviewers"):
            services.assign_reviewer(item=exit_item, actor=admin, assignee=integrator)

    def test_an_integrator_cannot_decide(self, exit_item, owner_membership):
        with pytest.raises(PermissionDenied):
            services.approve(item=exit_item, user=owner_membership.user)

    def test_a_decided_item_cannot_be_decided_again(self, exit_item, reviewer):
        services.approve(item=exit_item, user=reviewer)

        with pytest.raises(ValidationError, match="already approved"):
            services.send_back(item=exit_item, user=reviewer, reason="Changed my mind.")


# ── review detail ──────────────────────────────────────────────────────────


class TestReviewDetail:
    def test_shows_the_submitted_form_with_query_actions_and_context(
        self,
        sign_in,
        reviewer,
        exit_item,
    ):
        product = exit_item.product
        product.organisation.set_verification(Organisation.VerificationStatus.VERIFIED)
        services.issue_credentials(product=product)
        services.raise_query(
            item=exit_item,
            user=reviewer,
            field_key="wasa_agency",
            field_label="WASA audit agency",
            question="Empanelled?",
        )

        response = sign_in(reviewer).get(review_url(exit_item))
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert response.context["nav_section"] == "review-queue"
        assert "Example CERT-In auditor" in html
        assert "Download certificate" in html
        assert "Query open" in html
        assert "Query the whole form" in html
        assert "Integrator context" in html
        assert product.credential.client_id in html
        # Reviewers never see the client secret.
        assert product.credential.secret not in html
        assert "Waiting for the integrator." in html

    def test_a_query_action_preselects_the_field(self, sign_in, reviewer, exit_item):
        response = sign_in(reviewer).get(
            review_url(exit_item),
            {"tab": "query", "field": "wasa_issued_on", "label": "WASA issued on"},
        )

        assert response.context["decision_tab"] == "query"
        assert response.context["query_form"]["field_key"].value() == "wasa_issued_on"
        assert "WASA issued on" in response.content.decode()

    def test_approving_from_the_page_without_scripting(
        self,
        sign_in,
        reviewer,
        exit_item,
    ):
        response = sign_in(reviewer).post(
            review_url(exit_item, "review-approve"),
            data={"approved_on": timezone.localdate().isoformat(), "note": "Verified."},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == review_url(exit_item)
        exit_item.refresh_from_db()
        assert exit_item.status == ReviewItem.Status.APPROVED
        html = sign_in(reviewer).get(review_url(exit_item)).content.decode()
        assert "Decision recorded" in html
        assert "Nandita Shah" in html

    def test_an_htmx_send_back_swaps_the_workspace(self, sign_in, reviewer, exit_item):
        response = sign_in(reviewer).post(
            review_url(exit_item, "review-send-back"),
            data={"reason": "The certificate is for another product."},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert 'id="review-workspace"' in html
        assert "Sent back to the integrator" in html
        assert 'hx-swap-oob="innerHTML"' in html
        assert "<!DOCTYPE html>" not in html

    def test_a_send_back_without_a_reason_is_a_200_with_the_error(
        self,
        sign_in,
        reviewer,
        exit_item,
    ):
        response = sign_in(reviewer).post(
            review_url(exit_item, "review-send-back"),
            data={"reason": ""},
        )

        assert response.status_code == HTTPStatus.OK
        assert "Give the integrator a reason." in response.content.decode()
        exit_item.refresh_from_db()
        assert exit_item.status == ReviewItem.Status.NEW

    def test_raising_and_resolving_a_query_from_the_page(
        self,
        sign_in,
        reviewer,
        exit_item,
        owner_membership,
    ):
        client = sign_in(reviewer)

        raised = client.post(
            review_url(exit_item, "review-query"),
            data={
                "field_key": "wasa_agency",
                "field_label": "WASA audit agency",
                "question": "Which agency?",
            },
        )
        assert raised.status_code == HTTPStatus.FOUND
        query = exit_item.queries.get()
        services.reply_to_query(query=query, user=owner_membership.user, reply="ACME.")

        resolved = client.post(
            review_url(exit_item, "query-resolve", pk=query.pk),
            headers={"HX-Request": "true"},
        )

        assert resolved.status_code == HTTPStatus.OK
        query.refresh_from_db()
        assert query.is_resolved
        assert "Resolved" in resolved.content.decode()

    def test_only_an_admin_sees_and_uses_the_assignment_form(
        self,
        sign_in,
        reviewer,
        admin,
        exit_item,
    ):
        assign_url = review_url(exit_item, "review-assign")
        assert (
            assign_url
            not in sign_in(reviewer).get(review_url(exit_item)).content.decode()
        )

        response = sign_in(admin).post(
            review_url(exit_item, "review-assign"),
            data={"assignee": reviewer.pk},
        )

        assert response.status_code == HTTPStatus.FOUND
        exit_item.refresh_from_db()
        assert exit_item.assignee == reviewer

    def test_an_organisation_review_lists_its_documents(
        self,
        sign_in,
        reviewer,
        organisation_item,
    ):
        html = sign_in(reviewer).get(review_url(organisation_item)).content.decode()

        assert "AAACS1234K" in html
        assert "Supporting document" in html
        assert (
            reverse(
                "products:document",
                args=[
                    "organisation",
                    organisation_item.organisation.pk,
                    "verification_document",
                ],
            )
            in html
        )

    def test_an_unknown_reference_is_a_404(self, sign_in, reviewer):
        response = sign_in(reviewer).get(
            reverse("assess:review", kwargs={"reference": "REV-2026-99999"}),
        )

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestConsoleOrganisationPage:
    def test_links_to_the_verification_review(
        self,
        sign_in,
        reviewer,
        organisation_item,
    ):
        html = (
            sign_in(reviewer)
            .get(
                reverse("ohc:organisation", args=[organisation_item.organisation.slug]),
            )
            .content.decode()
        )

        assert organisation_item.reference in html
        assert review_url(organisation_item) in html
