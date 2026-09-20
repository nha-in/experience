# ruff: noqa: F811, PLR2004
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import files
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import pdf
from ohc_experience.abdm.wasa import approved_wasa_submission
from ohc_experience.abdm.wasa import current_wasa
from ohc_experience.abdm.wasa import wasa_context
from ohc_experience.experiences import permissions
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def certificate_data(*, expires_in=30, audited_ago=12):
    today = timezone.localdate()
    return {
        "wasa_agency": evidence_data()["wasa_agency"],
        "wasa_date": (today - timedelta(days=audited_ago)).isoformat(),
        "wasa_valid_until": (today + timedelta(days=expires_in)).isoformat(),
    }


def request_milestone(environment, key="m1", *, certificate=None, source=None):
    data = {
        **evidence_data(),
        **(certificate if certificate is not None else certificate_data()),
    }
    uploads = files()
    if source is not None:
        for field in tuple(data):
            if field.startswith("wasa_"):
                del data[field]
        data.update(
            use_product_wasa="on",
            wasa_source_submission=str(source.pk),
        )
        uploads.pop("wasa_certificate")
    item, form, saved = workflows.save_review_form(
        milestone(environment, key),
        environment["applicant"],
        data=data,
        files=uploads,
        submit=True,
    )
    assert saved, form.errors
    return item


def request_renewal(environment, *, certificate=None, name="renewed-wasa.pdf"):
    item = workflows.certification_review(
        environment["workspace"].product,
        environment["applicant"],
    )
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=certificate if certificate is not None else certificate_data(),
        files={"wasa_certificate": pdf(name)},
        submit=True,
    )
    assert saved, form.errors
    return item


def decide(environment, item, action="approve", note="Certificate verified."):
    workflows.assign_review(item, environment["admin"], environment["reviewer"])
    return workflows.decide(
        item,
        environment["reviewer"],
        action=action,
        note=note,
    )


def test_milestone_approval_makes_its_certificate_available_to_product(environment):
    product = environment["workspace"].product
    item = request_milestone(environment)
    assert current_wasa(product) is None

    decide(environment, item)

    approved = current_wasa(product)
    assert approved.outcome_type == "wasa_approval"
    assert int(approved.data["submission_id"]) == item.selected_submission_id
    assert approved.source_application_id == item.application_id
    assert approved.valid_until == timezone.localdate() + timedelta(days=30)
    assert item.selected_submission.valid_until == approved.valid_until
    assert ProductOutcome.objects.filter(
        product=product,
        outcome_type="milestone_approval",
    ).exists()


def test_renewal_reuses_open_request_and_creates_new_cycle_after_approval(environment):
    product = environment["workspace"].product
    first = workflows.certification_review(product, environment["applicant"])
    assert first.application.application_type == "abdm_wasa_review"
    assert (
        first.pk
        == workflows.certification_review(
            product,
            environment["applicant"],
        ).pk
    )

    first = request_renewal(environment)
    original = first.selected_submission
    original_data = dict(original.data)
    assert (
        first.pk
        == workflows.certification_review(
            product,
            environment["applicant"],
        ).pk
    )
    decide(environment, first)

    second = workflows.certification_review(product, environment["applicant"])
    assert second.pk != first.pk
    assert second.application_id != first.application_id
    assert second.form_id == first.form_id
    second = request_renewal(
        environment,
        certificate=certificate_data(expires_in=90, audited_ago=1),
    )
    assert second.selected_submission_id != original.pk
    first.refresh_from_db()
    original.refresh_from_db()
    assert first.selected_submission_id == original.pk
    assert original.data == original_data
    assert original.origin_application_id == first.application_id
    assert second.selected_submission.origin_application_id == second.application_id


def test_pending_and_rejected_renewals_preserve_approved_certificate(environment):
    product = environment["workspace"].product
    first = decide(environment, request_milestone(environment))
    approval = current_wasa(product)
    renewal = request_renewal(
        environment,
        certificate=certificate_data(expires_in=90, audited_ago=1),
    )
    assert current_wasa(product).pk == approval.pk
    assert int(current_wasa(product).data["submission_id"]) == (
        first.selected_submission_id
    )

    decide(environment, renewal, "reject", "Upload the signed certificate.")

    assert current_wasa(product).pk == approval.pk
    assert workflows.certification_review(product, environment["applicant"]).pk == (
        renewal.pk
    )


def test_first_pending_wasa_is_reported_as_a_submission_awaiting_approval(environment):
    product = environment["workspace"].product
    renewal = request_renewal(environment)

    context = wasa_context(product)

    assert context["current"] is None
    assert context["review"].pk == renewal.pk
    assert context["pending_review"].pk == renewal.pk
    assert context["status_label"] == "No approved certificate"


def test_stale_renewal_edit_cannot_overwrite_teammates_revision(environment):
    item = workflows.certification_review(
        environment["workspace"].product,
        environment["applicant"],
    )
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=certificate_data(),
        files={"wasa_certificate": pdf()},
        expected_revision="",
    )
    assert saved, form.errors
    stale_revision = item.selected_submission_id
    item, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=certificate_data(expires_in=90),
        expected_revision=stale_revision,
    )
    assert saved, form.errors
    current_revision = item.selected_submission_id
    count = item.form.submissions.count()

    with pytest.raises(ValidationError, match="teammate"):
        workflows.save_review_form(
            item,
            environment["applicant"],
            data=certificate_data(expires_in=60),
            expected_revision=stale_revision,
            submit=True,
        )

    item.refresh_from_db()
    assert item.selected_submission_id == current_revision
    assert item.form.submissions.count() == count
    assert item.status == "draft"


@pytest.mark.parametrize("intent", ["draft", "submit"])
def test_product_wasa_page_saves_and_renders_result(environment, client, intent):
    product = environment["workspace"].product
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )
    client.force_login(environment["applicant"])
    page = client.get(url)
    assert page.status_code == 200
    assert page.context["can_edit"]
    assert b"data-review-form" in page.content
    response = client.post(
        url,
        {
            **certificate_data(),
            "intent": intent,
            "certification_revision": page.context["certification_revision"],
            "revision": "",
            "wasa_certificate": pdf("product-wasa.pdf"),
        },
    )

    assert response.status_code == 302
    assert response["Location"] == url
    item = product.review_items.get(application__application_type="abdm_wasa_review")
    page = client.get(url)
    assert page.status_code == 200
    assert page.context["item"].pk == item.pk
    assert page.context["can_edit"] == (intent == "draft")
    assert (
        item.selected_submission.attachments.get(
            field_key="wasa_certificate",
        ).original_name
        == "product-wasa.pdf"
    )
    if intent == "draft":
        assert item.status == "draft"
        assert b"data-review-form" in page.content
        assert (
            page.context["form"].initial["wasa_valid_until"]
            == (certificate_data()["wasa_valid_until"])
        )
    else:
        assert item.pending
        assert b"Your submission is with the review team" in page.content
        assert page.context["certification"]["pending_review"].pk == item.pk


@pytest.mark.parametrize("intent", ["draft", "submit"])
def test_stale_renewal_page_cannot_start_another_cycle_after_approval(
    environment,
    client,
    intent,
):
    product = environment["workspace"].product
    url = reverse(
        "experiences:product-certification",
        args=[environment["workspace"].reference],
    )
    client.force_login(environment["applicant"])
    initial_count = product.review_items.count()
    page = client.get(url)
    assert page.status_code == 200
    assert product.review_items.count() == initial_count
    revision = page.context["certification_revision"]
    approved = decide(environment, request_renewal(environment))
    count = product.review_items.count()
    submission_count = FormSubmission.objects.count()

    response = client.post(
        url,
        {
            **certificate_data(expires_in=90),
            "intent": intent,
            "certification_revision": revision,
            "revision": "",
            "wasa_certificate": pdf(),
        },
    )

    assert response.status_code == 200
    assert b"This WASA request has changed" in response.content
    assert product.review_items.count() == count
    assert FormSubmission.objects.count() == submission_count
    assert int(current_wasa(product).data["submission_id"]) == (
        approved.selected_submission_id
    )


def test_rejected_renewal_resubmission_keeps_reviewed_revision(environment):
    first = request_renewal(environment)
    original = first.selected_submission
    original_data = dict(original.data)
    original_file = original.attachments.get(field_key="wasa_certificate")
    decide(environment, first, "reject", "Correct the validity date.")

    revised = request_renewal(
        environment,
        certificate=certificate_data(expires_in=90),
        name="corrected-wasa.pdf",
    )
    assert revised.pk == first.pk
    assert revised.selected_submission_id != original.pk
    assert revised.selected_submission.revision == original.revision + 1
    original.refresh_from_db()
    assert original.data == original_data
    assert original.attachments.get(field_key="wasa_certificate").pk == original_file.pk
    decide(environment, revised)
    approved = current_wasa(environment["workspace"].product)
    assert int(approved.data["submission_id"]) == (revised.selected_submission_id)


def test_milestone_defaults_to_approved_wasa_and_copies_immutable_evidence(environment):
    first = decide(environment, request_milestone(environment))
    source = first.selected_submission
    source_file = source.attachments.get(field_key="wasa_certificate")
    form = workflows.build_form(milestone(environment, "m2"))
    assert form.initial["use_product_wasa"]
    assert int(form.initial["wasa_source_submission"]) == source.pk

    second = request_milestone(environment, "m2", source=source)
    snapshot = second.selected_submission
    copied_file = snapshot.attachments.get(field_key="wasa_certificate")
    assert snapshot.pk != source.pk
    assert int(snapshot.data["wasa_source_submission"]) == source.pk
    for key in ["wasa_agency", "wasa_date", "wasa_valid_until"]:
        assert snapshot.data[key] == source.data[key]
    assert copied_file.pk != source_file.pk
    assert copied_file.original_name == source_file.original_name
    assert copied_file.file.read() == source_file.file.read()
    assert snapshot.attachments.filter(field_key="functional_report").exists()


def test_saved_new_milestone_evidence_does_not_switch_to_product_wasa(environment):
    decide(environment, request_milestone(environment))
    own_certificate = certificate_data(expires_in=60, audited_ago=2)
    second, form, saved = workflows.save_review_form(
        milestone(environment, "m2"),
        environment["applicant"],
        data={**evidence_data(), **own_certificate},
        files=files(),
    )
    assert saved, form.errors
    decide(
        environment,
        request_renewal(
            environment,
            certificate=certificate_data(expires_in=90, audited_ago=1),
        ),
    )

    form = workflows.build_form(second)

    assert not form.initial.get("use_product_wasa")
    assert form.wasa_source is None
    assert second.selected_submission.data["wasa_source_submission"] is None
    assert form.initial["wasa_valid_until"] == own_certificate["wasa_valid_until"]
    assert not form.fields["wasa_date"].disabled


def test_inherited_milestone_evidence_defaults_to_current_certificate(environment):
    first = decide(environment, request_milestone(environment))
    renewal = decide(
        environment,
        request_renewal(
            environment,
            certificate=certificate_data(expires_in=90, audited_ago=1),
        ),
    )
    second = milestone(environment, "m2")
    workflows.reuse_evidence(second, environment["applicant"])
    second.refresh_from_db()
    assert second.selected_submission_id == first.selected_submission_id

    form = workflows.build_form(second)

    assert form.initial["use_product_wasa"]
    assert int(form.initial["wasa_source_submission"]) == renewal.selected_submission_id
    assert (
        form.initial["wasa_valid_until"]
        == (renewal.selected_submission.data["wasa_valid_until"])
    )
    assert not form.initial.get("start_date")
    assert not form.initial.get("end_date")


def test_revoked_current_certificate_does_not_default_to_older_approval(environment):
    product = environment["workspace"].product
    decide(environment, request_milestone(environment))
    renewal = decide(
        environment,
        request_renewal(
            environment,
            certificate=certificate_data(expires_in=90, audited_ago=1),
        ),
    )
    revoked = current_wasa(product)
    revoked.status = "revoked"
    revoked.save(update_fields=["status"])

    context = wasa_context(product)
    form = workflows.build_form(milestone(environment, "m2"))

    assert current_wasa(product).pk == revoked.pk
    assert context["status"] == "revoked"
    assert not form.initial.get("use_product_wasa")
    assert not form.wasa_reuse_available
    assert approved_wasa_submission(product, renewal.selected_submission_id) is None


def test_reused_milestone_cannot_replace_newer_product_certificate(environment):
    product = environment["workspace"].product
    first = decide(environment, request_milestone(environment))
    second = request_milestone(environment, "m2", source=first.selected_submission)
    old_snapshot = dict(second.selected_submission.data)
    renewal = decide(
        environment,
        request_renewal(
            environment,
            certificate=certificate_data(expires_in=90, audited_ago=1),
        ),
    )
    renewal_approval = current_wasa(product)

    decide(environment, second)

    assert current_wasa(product).pk == renewal_approval.pk
    assert int(current_wasa(product).data["submission_id"]) == (
        renewal.selected_submission_id
    )
    assert product.outcomes.filter(outcome_type="wasa_approval").count() == 2
    second.selected_submission.refresh_from_db()
    assert second.selected_submission.data == old_snapshot


def test_saved_milestone_can_explicitly_switch_to_renewal_after_source_expires(
    environment,
):
    first = decide(
        environment,
        request_milestone(environment, certificate=certificate_data(expires_in=0)),
    )
    second = request_milestone(environment, "m2", source=first.selected_submission)
    old_snapshot = second.selected_submission
    old_data = dict(old_snapshot.data)
    workflows.withdraw(second, environment["applicant"])
    renewal = decide(
        environment,
        request_renewal(
            environment,
            certificate=certificate_data(expires_in=90, audited_ago=1),
        ),
    )
    tomorrow = timezone.localdate() + timedelta(days=1)

    with patch("django.utils.timezone.localdate", return_value=tomorrow):
        form = workflows.build_form(second)
        assert int(form.initial["wasa_source_submission"]) == (
            first.selected_submission_id
        )
        assert not form.product_wasa["valid"]
        assert form.latest_product_wasa["source_submission"].pk == (
            renewal.selected_submission_id
        )
        second = request_milestone(
            environment,
            "m2",
            source=renewal.selected_submission,
        )

    assert second.selected_submission_id != old_snapshot.pk
    assert second.selected_submission.data["wasa_source_submission"] == (
        renewal.selected_submission_id
    )
    assert (
        second.selected_submission.data["wasa_valid_until"]
        == (renewal.selected_submission.data["wasa_valid_until"])
    )
    assert (
        second.selected_submission.attachments.get(
            field_key="wasa_certificate",
        ).original_name
        == "renewed-wasa.pdf"
    )
    old_snapshot.refresh_from_db()
    assert old_snapshot.data == old_data
    assert old_snapshot.data["wasa_source_submission"] == first.selected_submission_id


@pytest.mark.parametrize("kind", ["milestone", "renewal"])
def test_expiry_is_required_for_new_certificates(environment, kind):
    data = {**evidence_data(), **certificate_data()}
    data.pop("wasa_valid_until")
    item = (
        milestone(environment)
        if kind == "milestone"
        else workflows.certification_review(
            environment["workspace"].product,
            environment["applicant"],
        )
    )
    _, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=data,
        files=files(),
        submit=True,
    )
    assert not saved
    assert "wasa_valid_until" in form.errors
    assert item.selected_submission_id is None


@pytest.mark.parametrize("expires_in", [-1, -20])
def test_expired_or_inverted_validity_is_rejected(environment, expires_in):
    item = workflows.certification_review(
        environment["workspace"].product,
        environment["applicant"],
    )
    _, form, saved = workflows.save_review_form(
        item,
        environment["applicant"],
        data=certificate_data(expires_in=expires_in),
        files={"wasa_certificate": pdf()},
        submit=True,
    )
    assert not saved
    assert "wasa_valid_until" in form.errors


@pytest.mark.parametrize("reuse", [False, True])
def test_certificate_expiry_is_checked_again_at_milestone_approval(environment, reuse):
    today = timezone.localdate()
    if reuse:
        source = decide(
            environment,
            request_milestone(environment, certificate=certificate_data(expires_in=0)),
        ).selected_submission
        item = request_milestone(environment, "m2", source=source)
    else:
        item = request_milestone(
            environment,
            certificate=certificate_data(expires_in=0),
        )
    workflows.assign_review(item, environment["admin"], environment["reviewer"])

    with (
        patch(
            "django.utils.timezone.localdate",
            return_value=today + timedelta(days=1),
        ),
        pytest.raises(ValidationError, match=r"[Ee]xpir"),
    ):
        workflows.decide(item, environment["reviewer"], action="approve")

    item.refresh_from_db()
    assert item.pending
    assert item.application.status == "under_review"
    assert not ProductOutcome.objects.filter(
        source_application=item.application,
        outcome_type="milestone_approval",
    ).exists()


def test_pending_certificate_cannot_be_forged_as_approved_reuse(environment):
    renewal = request_renewal(environment)
    source = renewal.selected_submission
    count = FormSubmission.objects.count()
    _, form, saved = workflows.save_review_form(
        milestone(environment),
        environment["applicant"],
        data={
            **evidence_data(),
            **certificate_data(),
            "use_product_wasa": "on",
            "wasa_source_submission": str(source.pk),
        },
        files=files(),
        submit=True,
    )
    assert not saved
    assert form.errors
    assert FormSubmission.objects.count() == count


def test_approved_certificate_cannot_be_reused_by_another_product(environment):
    first = decide(environment, request_renewal(environment))
    other, form = workflows.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Another product"),
    )
    assert other, form.errors
    other_item = other.product.milestones.get(key="m1").application.review_item
    _, form, saved = workflows.save_review_form(
        other_item,
        environment["applicant"],
        data={
            **evidence_data(),
            **certificate_data(),
            "use_product_wasa": "on",
            "wasa_source_submission": str(first.selected_submission_id),
        },
        files=files(),
        submit=True,
    )
    assert not saved
    assert form.errors
    assert current_wasa(other.product) is None


def test_expired_certificate_cannot_be_reused_even_with_forged_future_date(environment):
    source = decide(
        environment,
        request_renewal(environment, certificate=certificate_data(expires_in=0)),
    ).selected_submission
    tomorrow = timezone.localdate() + timedelta(days=1)
    with patch("django.utils.timezone.localdate", return_value=tomorrow):
        _, form, saved = workflows.save_review_form(
            milestone(environment),
            environment["applicant"],
            data={
                **evidence_data(),
                **certificate_data(expires_in=90),
                "use_product_wasa": "on",
                "wasa_source_submission": str(source.pk),
            },
            files=files(),
            submit=True,
        )
    assert not saved
    assert form.errors


def test_renewal_review_permission_is_general_and_keeps_track_boundaries(
    environment,
    client,
):
    first = decide(environment, request_milestone(environment))
    renewal = request_renewal(environment)
    general_reviewer = UserFactory(is_staff=True, is_nha_team=True)
    track_reviewer = UserFactory(is_staff=True, is_nha_team=True)
    for reviewer, category in [(general_reviewer, ""), (track_reviewer, "HIE-CM")]:
        AccessGrant.objects.create(
            user=reviewer,
            program="abdm",
            area="review",
            category=category,
            can_read=True,
            can_write=True,
            can_approve=True,
        )
    general_reviews = permissions.visible_reviews(general_reviewer)
    track_reviews = permissions.visible_reviews(track_reviewer)
    assert general_reviews.filter(pk=renewal.pk).exists()
    assert not general_reviews.filter(pk=first.pk).exists()
    assert track_reviews.filter(pk=first.pk).exists()
    assert not track_reviews.filter(pk=renewal.pk).exists()
    with pytest.raises(ValidationError, match="category"):
        workflows.assign_review(renewal, environment["admin"], track_reviewer)
    workflows.assign_review(renewal, environment["admin"], general_reviewer)
    client.force_login(general_reviewer)
    assert client.get(renewal.get_absolute_url()).status_code == 200
    assert client.get(first.get_absolute_url()).status_code == 404
    workflows.decide(
        renewal,
        general_reviewer,
        action="approve",
        note="Renewal certificate verified.",
    )


def test_another_organisations_user_cannot_open_or_submit_renewal(environment, client):
    product = environment["workspace"].product
    item = workflows.certification_review(product, environment["applicant"])
    with pytest.raises(PermissionDenied):
        workflows.certification_review(product, environment["outsider"])
    with pytest.raises(PermissionDenied):
        workflows.save_review_form(
            item,
            environment["outsider"],
            data=certificate_data(),
            files={"wasa_certificate": pdf()},
            submit=True,
        )
    client.force_login(environment["outsider"])
    assert client.get(item.get_absolute_url()).status_code == 403
    assert (
        client.get(
            reverse(
                "experiences:product-certification",
                args=[environment["workspace"].reference],
            ),
        ).status_code
        == 404
    )
