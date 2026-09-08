"""Track pages and exit requests: drafts, requests, withdrawals, downloads."""

from __future__ import annotations

from http import HTTPStatus

import pytest
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm import services
from ohc_experience.abdm.forms import ExitRequestForm
from ohc_experience.abdm.models import ComplianceRecord
from ohc_experience.abdm.models import ReviewHistory
from ohc_experience.abdm.models import ReviewItem
from ohc_experience.abdm.models import ReviewQuery
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 demo", content_type="application/pdf")


def exit_data() -> dict:
    today = timezone.localdate()
    return {
        "start_date": today.replace(day=1).isoformat(),
        "end_date": today.isoformat(),
        "demo_date": today.isoformat(),
        "wasa_agency": "Example CERT-In auditor",
        "wasa_date": today.isoformat(),
    }


def track_url(product, track="HI-CM", milestone="") -> str:
    url = reverse("products:track", args=[product.sandbox_id, track])
    return f"{url}?milestone={milestone}" if milestone else url


def save_url(product, track="HI-CM", milestone="M1") -> str:
    return reverse(
        "products:milestone-save",
        args=[product.sandbox_id, track, milestone],
    )


def withdraw_url(product, track="HI-CM", milestone="M1") -> str:
    return reverse(
        "products:milestone-withdraw",
        args=[product.sandbox_id, track, milestone],
    )


def record_for(product, milestone="M1", track="HI-CM") -> ComplianceRecord:
    return product.compliance_records.get(track_code=track, milestone_code=milestone)


def fill(record, user, *, files=True) -> ComplianceRecord:
    form = ExitRequestForm(
        exit_data(),
        {"functional_certificate": pdf("cert.pdf"), "functional_report": pdf("rep.pdf")}
        if files
        else {},
        instance=record,
    )
    assert form.is_valid(), form.errors
    return services.save_exit_draft(record=record, user=user, form=form)


@pytest.fixture
def reviewer(db):
    return UserFactory.create(email="reviewer@nha.gov.in", is_ohc_team=True)


class TestExitServices:
    def test_a_draft_moves_open_to_in_progress_and_keeps_files(
        self,
        product,
        owner_membership,
    ):
        record = fill(record_for(product), owner_membership.user)

        assert record.status == ComplianceRecord.Status.IN_PROGRESS
        assert record.functional_certificate.name.endswith("cert.pdf")
        assert record.is_complete

        # Saving again without new files keeps the stored ones.
        again = fill(record, owner_membership.user, files=False)
        assert again.functional_certificate.name.endswith("cert.pdf")

    def test_requesting_exit_needs_every_field_and_both_files(
        self,
        product,
        owner_membership,
    ):
        record = fill(record_for(product), owner_membership.user, files=False)

        with pytest.raises(ValidationError, match="Functional testing certificate"):
            services.request_exit(record=record, user=owner_membership.user)

        record.refresh_from_db()
        assert record.status == ComplianceRecord.Status.IN_PROGRESS

    def test_requesting_exit_submits_and_tells_the_reviewers(
        self,
        product,
        owner_membership,
        reviewer,
    ):
        record = fill(record_for(product), owner_membership.user)
        mail.outbox.clear()

        item = services.request_exit(record=record, user=owner_membership.user)

        record.refresh_from_db()
        assert record.status == ComplianceRecord.Status.UNDER_REVIEW
        assert record.submitted_on is not None
        assert item.item_type == ReviewItem.Type.EXIT_REQUEST
        assert item.compliance == record
        assert item.product == product
        assert item.title == "HI-CM M1 exit request"
        assert mail.outbox[0].to == [reviewer.email]
        assert "HI-CM M1 exit request" in mail.outbox[0].subject

    def test_a_locked_milestone_cannot_be_drafted(self, product, owner_membership):
        record = record_for(product, "M2")

        with pytest.raises(
            ValidationError,
            match="cannot be edited while it is locked",
        ):
            fill(record, owner_membership.user)

    def test_withdrawing_unlocks_the_form_and_closes_queries(
        self,
        product,
        owner_membership,
        reviewer,
    ):
        record = fill(record_for(product), owner_membership.user)
        item = services.request_exit(record=record, user=owner_membership.user)
        item.status = ReviewItem.Status.QUERY_RAISED
        item.save()
        record.status = ComplianceRecord.Status.QUERY_RAISED
        record.save()
        query = ReviewQuery.objects.create(
            item=item,
            question="Which agency signed the WASA report?",
            raised_by=reviewer,
        )

        services.withdraw_exit(record=record, user=owner_membership.user)

        record.refresh_from_db()
        item.refresh_from_db()
        query.refresh_from_db()
        assert record.status == ComplianceRecord.Status.IN_PROGRESS
        assert item.status == ReviewItem.Status.WITHDRAWN
        assert query.status == ReviewQuery.Status.RESOLVED
        assert item.history.filter(kind=ReviewHistory.Kind.WITHDRAWN).exists()

    def test_resubmitting_after_a_withdrawal_reuses_the_item(
        self,
        product,
        owner_membership,
    ):
        record = fill(record_for(product), owner_membership.user)
        item = services.request_exit(record=record, user=owner_membership.user)
        services.withdraw_exit(record=record, user=owner_membership.user)

        again = services.request_exit(record=record, user=owner_membership.user)

        record.refresh_from_db()
        assert again.pk == item.pk
        assert again.status == ReviewItem.Status.IN_REVIEW
        assert again.resubmission_count == 1
        assert record.resubmission_count == 1

    def test_a_support_member_cannot_request_exit(self, product, owner_membership):
        record = fill(record_for(product), owner_membership.user)
        support = MembershipFactory.create(
            organisation=product.organisation,
            role=Role.SUPPORT,
        )

        with pytest.raises(PermissionDenied):
            services.request_exit(record=record, user=support.user)


class TestTrackPage:
    def test_tiles_and_the_open_milestone_form(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        response = sign_in(owner_membership.user).get(track_url(product))
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert response.context["nav_section"] == "track"
        assert response.context["nav_track"] == "HI-CM"
        assert "0 of 2 milestones approved" in html
        assert 'id="exit-form"' in html
        assert "Request for exit needs:" in html
        assert "M3" in html
        assert "Not applied" in html

    def test_a_locked_milestone_says_what_unlocks_it(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        html = (
            sign_in(owner_membership.user)
            .get(track_url(product, milestone="M2"))
            .content.decode()
        )

        assert "M2 unlocks once M1 is approved." in html

    def test_nhcx_explains_that_nothing_is_published(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        html = (
            sign_in(owner_membership.user)
            .get(track_url(product, "NHCX"))
            .content.decode()
        )

        assert "No milestones published yet" in html

    def test_a_track_not_applied_for_points_at_edit_product(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        html = (
            sign_in(owner_membership.user)
            .get(track_url(product, "UHI"))
            .content.decode()
        )

        assert "Not applied for" in html
        assert reverse("products:edit", args=[product.sandbox_id]) in html

    def test_phr_shows_the_shared_hi_cm_m1_record(self, sign_in, owner_membership):
        product = services.register_product(
            organisation=owner_membership.organisation,
            user=owner_membership.user,
            data={
                "name": "Sunrise PHR",
                "description": "A PHR application.",
                "category": "phr_app",
                "solution_type": "eua",
                "milestones": ["PHR:M1", "PHR:PHR1"],
            },
        )

        response = sign_in(owner_membership.user).get(track_url(product, "PHR"))
        html = response.content.decode()

        assert "Shared with HI-CM M1" in html
        assert response.context["record"].key == "HI-CM:M1"
        assert "PHR1 unlocks once M1 is approved." in (
            sign_in(owner_membership.user)
            .get(track_url(product, "PHR", "PHR1"))
            .content.decode()
        )

    def test_an_unknown_track_is_a_404(self, sign_in, owner_membership, product):
        response = sign_in(owner_membership.user).get(track_url(product, "NOPE"))

        assert response.status_code == HTTPStatus.NOT_FOUND


class TestMilestoneActions:
    def test_saving_a_draft_redirects_back_to_the_milestone(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        response = sign_in(owner_membership.user).post(
            save_url(product),
            data={**exit_data(), "action": "draft"},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == track_url(product, milestone="M1")
        record = record_for(product)
        assert record.status == ComplianceRecord.Status.IN_PROGRESS
        assert record.wasa_agency == "Example CERT-In auditor"

    def test_an_htmx_draft_swaps_the_detail_card(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        response = sign_in(owner_membership.user).post(
            save_url(product),
            data={**exit_data(), "action": "draft"},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert 'id="milestone-detail"' in html
        assert "Draft saved." in html
        assert "<!DOCTYPE html>" not in html

    def test_an_invalid_draft_shows_the_error(self, sign_in, owner_membership, product):
        data = exit_data()
        data["end_date"] = "2020-01-01"

        response = sign_in(owner_membership.user).post(
            save_url(product),
            data={**data, "action": "draft"},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == HTTPStatus.OK
        assert "The end date has to follow the start date." in response.content.decode()
        assert record_for(product).status == ComplianceRecord.Status.OPEN

    def test_requesting_exit_with_the_form_submits_it(
        self,
        sign_in,
        owner_membership,
        product,
        reviewer,
    ):
        response = sign_in(owner_membership.user).post(
            save_url(product),
            data={
                **exit_data(),
                "action": "request",
                "functional_certificate": pdf("cert.pdf"),
                "functional_report": pdf("rep.pdf"),
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        record = record_for(product)
        assert record.status == ComplianceRecord.Status.UNDER_REVIEW
        assert ReviewItem.objects.filter(compliance=record).exists()
        html = sign_in(owner_membership.user).get(track_url(product)).content.decode()
        assert "Withdraw request" in html
        assert "Download certificate" in html

    def test_requesting_exit_while_incomplete_keeps_the_draft(
        self,
        sign_in,
        owner_membership,
        product,
    ):
        response = sign_in(owner_membership.user).post(
            save_url(product),
            data={**exit_data(), "action": "request"},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert "Complete these before requesting exit" in html
        assert record_for(product).status == ComplianceRecord.Status.IN_PROGRESS

    def test_withdrawing_from_the_page(self, sign_in, owner_membership, product):
        record = fill(record_for(product), owner_membership.user)
        services.request_exit(record=record, user=owner_membership.user)

        response = sign_in(owner_membership.user).post(withdraw_url(product))

        assert response.status_code == HTTPStatus.FOUND
        record.refresh_from_db()
        assert record.status == ComplianceRecord.Status.IN_PROGRESS
        assert record.review_item.status == ReviewItem.Status.WITHDRAWN

    def test_replying_to_a_query_from_the_track_page(
        self,
        sign_in,
        owner_membership,
        product,
        reviewer,
    ):
        record = fill(record_for(product), owner_membership.user)
        item = services.request_exit(record=record, user=owner_membership.user)
        item.status = ReviewItem.Status.QUERY_RAISED
        item.save()
        record.status = ComplianceRecord.Status.QUERY_RAISED
        record.save()
        query = ReviewQuery.objects.create(
            item=item,
            field_key="wasa_agency",
            field_label="WASA audit agency",
            question="Is this agency CERT-In empanelled?",
            raised_by=reviewer,
        )
        client = sign_in(owner_membership.user)

        page = client.get(track_url(product)).content.decode()
        assert "Is this agency CERT-In empanelled?" in page
        assert "The reviewer has a question." in page

        response = client.post(
            reverse("products:query-reply", args=[query.pk]),
            data={"reply": "Yes, empanelled since 2024."},
            headers={"HX-Request": "true"},
        )
        html = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert f'id="queries-{item.pk}"' in html
        assert "Yes, empanelled since 2024." in html
        record.refresh_from_db()
        assert record.status == ComplianceRecord.Status.UNDER_REVIEW

    def test_a_support_member_cannot_save(self, sign_in, product):
        support = MembershipFactory.create(
            organisation=product.organisation,
            role=Role.SUPPORT,
        )

        response = sign_in(support.user).post(
            save_url(product),
            data={**exit_data(), "action": "draft"},
        )

        assert response.status_code == HTTPStatus.FORBIDDEN


class TestDocumentDownloads:
    def test_members_and_reviewers_can_download_and_others_cannot(
        self,
        sign_in,
        owner_membership,
        product,
        reviewer,
    ):
        record = fill(record_for(product), owner_membership.user)
        url = reverse(
            "products:document",
            args=["compliance", record.pk, "functional_certificate"],
        )

        mine = sign_in(owner_membership.user).get(url)
        assert mine.status_code == HTTPStatus.OK
        assert mine["Content-Disposition"].startswith("attachment")
        assert b"".join(mine.streaming_content) == b"%PDF-1.4 demo"

        theirs = sign_in(reviewer).get(url)
        assert theirs.status_code == HTTPStatus.OK

        outsider = MembershipFactory.create(role=Role.OWNER)
        assert sign_in(outsider.user).get(url).status_code == HTTPStatus.FORBIDDEN

    def test_unknown_kinds_and_fields_are_404(self, sign_in, owner_membership, product):
        record = record_for(product)
        client = sign_in(owner_membership.user)

        assert (
            client.get(
                reverse("products:document", args=["nope", record.pk, "x"]),
            ).status_code
            == HTTPStatus.NOT_FOUND
        )
        assert (
            client.get(
                reverse("products:document", args=["compliance", record.pk, "status"]),
            ).status_code
            == HTTPStatus.NOT_FOUND
        )
        # A field that is set but empty is a 404 too, not a broken stream.
        assert (
            client.get(
                reverse(
                    "products:document",
                    args=["compliance", record.pk, "functional_report"],
                ),
            ).status_code
            == HTTPStatus.NOT_FOUND
        )
