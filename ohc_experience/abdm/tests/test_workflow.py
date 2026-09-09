# ruff: noqa: PLR2004
import socket
from io import BytesIO
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from PIL import Image

from ohc_experience.abdm.catalog import TRACK_MAP
from ohc_experience.abdm.demo import evidence_data
from ohc_experience.abdm.demo import organisation_data
from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.forms import ExitEvidenceForm
from ohc_experience.abdm.forms import OrganisationForm
from ohc_experience.abdm.forms import ProductRegistrationForm
from ohc_experience.experiences import credentials
from ohc_experience.experiences import uploads
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import Notification
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Organisation
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def pdf(name="test.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n%%EOF", content_type="application/pdf")


def files():
    return MultiValueDict(
        {
            "wasa_certificate": [pdf("wasa.pdf")],
            "functional_certificate": [pdf("certificate.pdf")],
            "functional_report": [pdf("report.pdf")],
        },
    )


@pytest.fixture
def environment(settings, tmp_path, lgd_lookup):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
    settings.MEDIA_ROOT = tmp_path
    cache.clear()
    applicant = UserFactory()
    admin = UserFactory(is_superuser=True, is_staff=True, is_ohc_team=True)
    reviewer = ReviewerFactory(is_ohc_team=True, is_staff=True)
    outsider = UserFactory()
    org = Organisation.objects.create(name="Test health systems")
    Membership.objects.create(organisation=org, user=applicant, role="owner")
    item = services.organisation_review(org, applicant)
    item, form, saved = services.save_review_form(
        item,
        applicant,
        data=organisation_data(),
        files={"supporting_document": pdf()},
        submit=True,
    )
    assert saved, form.errors
    services.assign_review(item, admin, reviewer)
    services.decide(item, reviewer, action="approve", note="Identity verified.")
    org.refresh_from_db()
    workspace, form = services.register_product(org, applicant, data=product_data())
    assert workspace, form.errors
    registration = workspace.product.review_items.get(kind="product_registration")
    services.assign_review(registration, admin, reviewer)
    services.decide(registration, reviewer, action="approve")
    workspace.refresh_from_db()
    return {
        "applicant": applicant,
        "admin": admin,
        "reviewer": reviewer,
        "outsider": outsider,
        "org": org,
        "workspace": workspace,
    }


def milestone(environment, key="m1"):
    return (
        environment["workspace"].product.milestones.get(key=key).application.review_item
    )


def submit(environment, key="m1"):
    item, form, saved = services.save_review_form(
        milestone(environment, key),
        environment["applicant"],
        data=evidence_data(),
        files=files(),
        submit=True,
    )
    assert saved, form.errors
    return item


def approve(environment, key="m1"):
    item = submit(environment, key)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    return services.decide(
        item,
        environment["reviewer"],
        action="approve",
        note="Approved with gateway handoff.",
    )


def test_shared_m1_and_independent_tracks(environment):
    product = environment["workspace"].product
    assert product.milestones.filter(key="m1").count() == 1
    assert TRACK_MAP["PHR"].keys == ("m1", "phr1")
    assert TRACK_MAP["HealthLocker"].keys == ("locker1",)
    assert TRACK_MAP["NHCX"].keys == ("nhcx1",)
    assert services.milestone_locked(milestone(environment, "m2"))
    assert services.milestone_locked(milestone(environment, "phr1"))
    assert not services.milestone_locked(milestone(environment, "locker1"))
    approve(environment)
    assert not services.milestone_locked(milestone(environment, "m2"))
    assert not services.milestone_locked(milestone(environment, "phr1"))
    assert services.milestone_locked(milestone(environment, "m3"))
    assert product.outcomes.filter(outcome_type="milestone_approval").exists()


def test_cannot_forge_locked_or_unregistered_exit(environment):
    with pytest.raises(ValidationError, match="unlocks"):
        submit(environment, "m2")
    environment["workspace"].registration_status = "pending"
    environment["workspace"].save()
    with pytest.raises(ValidationError, match="registration"):
        submit(environment)


def test_only_manually_assigned_reviewers_decide(environment):
    item = submit(environment)
    for actor in [
        environment["applicant"],
        environment["outsider"],
        environment["reviewer"],
    ]:
        with pytest.raises(PermissionDenied):
            services.decide(item, actor, action="approve")
    with pytest.raises(PermissionDenied):
        services.assign_review(item, environment["reviewer"], environment["reviewer"])
    with pytest.raises(ValidationError):
        services.assign_review(item, environment["admin"], environment["applicant"])
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(item, environment["reviewer"], action="approve")
    with pytest.raises(ValidationError):
        services.decide(
            item,
            environment["reviewer"],
            action="send_back",
            note="Late change",
        )


def test_queries_pause_until_all_answered_and_resolved(environment):
    item = submit(environment)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    for field in ["wasa_agency", "functional_report"]:
        services.decide(
            item,
            environment["reviewer"],
            action="query",
            note="Please clarify this field.",
            field_key=field,
        )
    first, second = item.queries.all()
    services.reply_query(first, environment["applicant"], "Confirmed the scope.")
    item.refresh_from_db()
    assert item.status == "query_raised"
    with pytest.raises(ValidationError, match="Withdraw"):
        services.save_review_form(item, environment["applicant"], data=evidence_data())
    services.reply_query(second, environment["applicant"], "See case 14.")
    item.refresh_from_db()
    assert item.status == "in_review"
    with pytest.raises(ValidationError, match="Resolve"):
        services.decide(item, environment["reviewer"], action="approve")
    services.resolve_query(first, environment["reviewer"])
    services.resolve_query(second, environment["reviewer"])
    services.decide(item, environment["reviewer"], action="approve")
    assert item.history.filter(action="Query answered").count() == 2
    assert Notification.objects.filter(subject__contains="query answered").count() == 2


def test_withdraw_and_resubmit_preserves_original_fields_and_files(environment):
    item = submit(environment)
    original = item.selected_submission
    original_data = dict(original.data)
    services.withdraw(item, environment["applicant"])
    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data={**evidence_data(), "wasa_agency": "Updated agency"},
        submit=True,
    )
    assert saved, form.errors
    original.refresh_from_db()
    assert original.data == original_data
    assert item.selected_submission.pk != original.pk
    assert item.selected_submission.revision == 2
    assert item.selected_submission.attachments.count() == original.attachments.count()
    assert item.resubmission_count == 1
    assert item.history.filter(action="Request withdrawn").exists()


def test_reuse_pins_revision_without_mutating_approved_application(environment):
    first = approve(environment)
    pin = first.selected_submission_id
    second = milestone(environment, "m2")
    services.reuse_evidence(second, environment["applicant"])
    second.refresh_from_db()
    assert second.selected_submission_id == pin
    second, form, saved = services.save_review_form(
        second,
        environment["applicant"],
        data={**evidence_data(), "wasa_agency": "Different agency"},
        submit=True,
    )
    assert saved, form.errors
    first.refresh_from_db()
    assert first.selected_submission_id == pin
    assert second.selected_submission.submission_number == 2
    assert second.selected_submission.revision == 1
    assert second.form_id == first.form_id


def test_multiple_evidence_append_remove_keeps_history(environment):
    uploads = files()
    uploads.setlist("supporting_evidence", [pdf("one.pdf"), pdf("two.pdf")])
    item, form, saved = services.save_review_form(
        milestone(environment),
        environment["applicant"],
        data=evidence_data(),
        files=uploads,
        submit=False,
    )
    assert saved, form.errors
    before = item.selected_submission
    removed = before.attachments.get(original_name="one.pdf")
    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data={
            **evidence_data(),
            "remove_files__supporting_evidence": [str(removed.pk)],
        },
        files=MultiValueDict(
            {"supporting_evidence": [pdf("three.pdf"), pdf("four.pdf")]},
        ),
        submit=True,
    )
    assert saved, form.errors
    assert set(
        item.selected_submission.attachments.filter(
            field_key="supporting_evidence",
        ).values_list("original_name", flat=True),
    ) == {"two.pdf", "three.pdf", "four.pdf"}
    assert set(
        before.attachments.filter(field_key="supporting_evidence").values_list(
            "original_name",
            flat=True,
        ),
    ) == {"one.pdf", "two.pdf"}


def test_stale_form_cannot_overwrite_teammate(environment):
    item = milestone(environment)
    services.save_review_form(
        item,
        environment["applicant"],
        data={"wasa_agency": "Draft"},
    )
    with pytest.raises(ValidationError, match="teammate"):
        services.save_review_form(
            item,
            environment["applicant"],
            data=evidence_data(),
            expected_revision="",
        )


def test_approved_track_selection_cannot_be_removed(environment):
    approve(environment)
    registration = environment["workspace"].product.review_items.get(
        kind="product_registration",
    )
    with pytest.raises(ValidationError, match="Approved milestones"):
        services.save_review_form(
            registration,
            environment["applicant"],
            data={**product_data(), "applied_milestones": ["HealthLocker:locker1"]},
            submit=True,
        )


def test_date_and_pdf_validation_and_required_documents():
    form = ExitEvidenceForm(data=evidence_data())
    assert not form.is_valid()
    assert "functional_report" in form.errors
    fake = SimpleUploadedFile(
        "fake.pdf",
        b"<html>not a pdf</html>",
        content_type="application/pdf",
    )
    form = ExitEvidenceForm(
        data=evidence_data(),
        files={"functional_report": fake, "functional_certificate": pdf()},
    )
    assert not form.is_valid()
    assert "Upload a PDF" in str(form.errors)
    data = evidence_data()
    form = ExitEvidenceForm(data={**data, "end_date": "2000-01-01"}, files=files())
    assert not form.is_valid()
    assert "end_date" in form.errors
    assert ExitEvidenceForm(data={"wasa_agency": "Agency"}, draft=True).is_valid()


def test_phr_requires_m1_but_not_m3_and_locker_is_independent():
    assert ProductRegistrationForm(
        data={**product_data(), "applied_milestones": ["PHR:m1", "PHR:phr1"]},
    ).is_valid()
    assert ProductRegistrationForm(
        data={**product_data(), "applied_milestones": ["HealthLocker:locker1"]},
    ).is_valid()
    assert not ProductRegistrationForm(
        data={**product_data(), "applied_milestones": ["PHR:phr1"]},
    ).is_valid()


def test_credentials_encrypted_audited_rate_limited_and_not_in_outcomes(
    environment,
    client,
):
    credential = ProductCredential.objects.get(product=environment["workspace"].product)
    plain = credentials.cipher().decrypt(credential.encrypted_secret.encode()).decode()
    outcome = credential.product.outcomes.get(outcome_type="sandbox_credentials")
    assert plain not in str(outcome.data)
    assert plain not in credential.encrypted_secret
    url = reverse("experiences:credentials", args=[environment["workspace"].reference])
    client.force_login(environment["applicant"])
    response = client.get(url)
    assert response.status_code == 200
    assert plain not in response.content.decode()
    response = client.post(url, {"intent": "reveal"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert plain in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert AuditEvent.objects.filter(action="Client secret revealed").count() == 1
    for _ in range(4):
        credentials.reveal(credential, environment["applicant"])
    with pytest.raises(ValidationError, match="Too many"):
        credentials.reveal(credential, environment["applicant"])
    client.force_login(environment["reviewer"])
    assert client.get(url).status_code == 403
    assert client.post(url, {"intent": "reveal"}).status_code == 403


def test_revoke_removes_secret_and_reveal_permission(environment):
    credential = ProductCredential.objects.get(product=environment["workspace"].product)
    credentials.revoke(credential, environment["applicant"])
    credential.refresh_from_db()
    assert credential.encrypted_secret == ""
    with pytest.raises(ValidationError, match="active"):
        credentials.reveal(credential, environment["applicant"])


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"],
)
def test_callback_rejects_private_dns_results(address):
    with (
        patch(
            "ohc_experience.experiences.credentials.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
        ),
        pytest.raises(ValidationError, match="Private"),
    ):
        credentials.public_callback_target("https://callback.example.org/")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "https://user:pass@example.org",
        "https://example.org:8443",
        "https://example.org/#fragment",
    ],
)
def test_callback_rejects_unsafe_urls(url):
    with pytest.raises(ValidationError):
        credentials.public_callback_target(url)


def test_permission_boundaries_and_all_portal_pages_render(environment, client):
    workspace = environment["workspace"]
    approve(environment)
    item = submit(environment, "m2")
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(
        item,
        environment["reviewer"],
        action="query",
        note="Explain evidence.",
    )
    client.force_login(environment["applicant"])
    paths = [
        reverse("experiences:products"),
        workspace.get_absolute_url(),
        reverse("experiences:organisation"),
        reverse("experiences:product-edit", args=[workspace.reference]),
        reverse("experiences:pending-queries"),
        reverse("experiences:events"),
        reverse("experiences:support"),
        reverse("experiences:submission", args=[item.pk, item.selected_submission_id]),
    ]
    paths += [
        reverse("experiences:track", args=[workspace.reference, code])
        for code in TRACK_MAP
    ]
    for path in paths:
        response = client.get(path, HTTP_HX_REQUEST="true")
        assert response.status_code == 200, path
        assert b'id="portal"' in response.content, path
    assert client.get(reverse("experiences:queue")).status_code == 403
    client.force_login(environment["outsider"])
    assert client.get(workspace.get_absolute_url()).status_code == 404
    file = item.selected_submission.attachments.first()
    assert (
        client.get(reverse("experiences:attachment", args=[file.pk])).status_code == 404
    )
    client.force_login(environment["reviewer"])
    for path in [
        reverse("experiences:assess-dashboard"),
        reverse("experiences:queue"),
        item.get_absolute_url(),
        item.get_absolute_url() + "?action=query&field=wasa_agency",
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
    assert (
        client.get(reverse("experiences:attachment", args=[file.pk])).status_code == 200
    )


def test_historical_schema_is_rendered_after_form_definition_changes(
    environment,
    client,
):
    item = submit(environment)
    snapshot = item.selected_submission
    snapshot.field_schema = [
        {
            "key": "retired_field",
            "label": "Retired audit reference",
            "type": "CharField",
            "choices": [],
        },
    ]
    snapshot.data = {"retired_field": "audit-2025-001"}
    snapshot.save()
    client.force_login(environment["applicant"])
    response = client.get(
        reverse("experiences:submission", args=[item.pk, snapshot.pk]),
    )
    assert b"Retired audit reference" in response.content
    assert b"audit-2025-001" in response.content


def test_audit_events_cannot_be_changed_or_deleted(environment):
    event = AuditEvent.objects.first()
    for operation in [
        lambda: AuditEvent.objects.filter(pk=event.pk).update(action="Changed"),
        lambda: AuditEvent.objects.filter(pk=event.pk).delete(),
        event.save,
        event.delete,
    ]:
        with pytest.raises(ValidationError, match="append-only"):
            operation()


def test_sent_back_draft_retains_reason_and_decision_history(environment, client):
    item = submit(environment)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(
        item,
        environment["reviewer"],
        action="send_back",
        note="Revise scope.",
    )
    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data={"wasa_agency": "Revised agency"},
    )
    assert saved, form.errors
    assert item.status == "sent_back"
    assert item.decision_note == "Revise scope."
    item, form, saved = services.save_review_form(
        item,
        environment["applicant"],
        data=evidence_data(),
        submit=True,
    )
    assert saved, form.errors
    assert item.status == "in_review"
    client.force_login(environment["reviewer"])
    response = client.get(reverse("experiences:assess-dashboard"))
    assert sum(week["sent_back"] for week in response.context["weeks"]) == 1
    assert response.context["median_days"] is not None


def test_track_filter_respects_which_track_applied_for_shared_m1(environment, client):
    item = submit(environment)
    workspace = environment["workspace"]
    workspace.applied_milestones = ["HI-CM:m1"]
    workspace.save()
    client.force_login(environment["reviewer"])
    url = reverse("experiences:queue")
    assert item in client.get(url, {"track": "HI-CM"}).context["page"]
    assert item not in client.get(url, {"track": "PHR"}).context["page"]
    workspace.applied_milestones.append("PHR:m1")
    workspace.save()
    assert item in client.get(url, {"track": "PHR"}).context["page"]


def test_removed_organisation_urls_cannot_bypass_review(environment, client):
    org = environment["org"]
    client.force_login(environment["applicant"])
    for url in ["/settings/organisation/", "/onboarding/"]:
        assert client.post(url, {"name": "Bypassed review"}).status_code == 404
    client.force_login(environment["reviewer"])
    response = client.post(
        f"/ohc/organisations/{org.slug}/verification/",
        {"verification_status": "sent_back"},
    )
    assert response.status_code == 404
    org.refresh_from_db()
    assert org.is_verified
    assert org.name != "Bypassed review"


def test_support_members_cannot_reply_or_withdraw(environment, client):
    supporter = UserFactory()
    Membership.objects.create(
        user=supporter,
        organisation=environment["org"],
        role="support",
    )
    item = submit(environment)
    services.assign_review(item, environment["admin"], environment["reviewer"])
    services.decide(
        item,
        environment["reviewer"],
        action="query",
        note="Clarify scope.",
    )
    client.force_login(supporter)
    response = client.get(
        reverse(
            "experiences:track",
            args=[environment["workspace"].reference, "HI-CM"],
        ),
    )
    assert response.status_code == 200
    assert b"Withdraw request</button>" not in response.content
    assert b"Send reply</button>" not in response.content
    with pytest.raises(PermissionDenied):
        services.withdraw(item, supporter)
    with pytest.raises(PermissionDenied):
        services.reply_query(item.queries.get(), supporter, "Reply")


def test_review_mutation_requires_csrf(environment):
    item = submit(environment)
    client = Client(enforce_csrf_checks=True)
    client.force_login(environment["applicant"])
    assert (
        client.post(reverse("experiences:withdraw", args=[item.pk])).status_code == 403
    )
    item.refresh_from_db()
    assert item.pending


def test_credential_issuance_checks_current_organisation_not_cached_instance(
    environment,
):
    product = environment["workspace"].product
    assert product.organisation.is_verified
    Organisation.objects.filter(pk=product.organisation_id).update(
        verification_status="pending",
    )
    with pytest.raises(ValidationError, match="verified organisation"):
        credentials.issue_credentials(product, environment["applicant"])


def test_pdf_validation_keeps_upload_readable_without_network_access():
    document = pdf()
    with patch("socket.create_connection") as connect:
        uploads.validate_pdf(document)
        connect.assert_not_called()
    assert document.tell() == 0
    assert document.read() == b"%PDF-1.4\n%%EOF"


def test_logo_still_validates_image_type_and_size():
    buffer = BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, format="PNG")
    image = SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")
    field = OrganisationForm().fields["logo"]
    assert field.clean(image)
    image.size = uploads.MAX_UPLOAD_BYTES + 1
    image.seek(0)
    with pytest.raises(ValidationError, match="10 MB"):
        field.clean(image)
    with pytest.raises(ValidationError, match="valid image"):
        field.clean(SimpleUploadedFile("fake.png", b"not an image"))


def test_oversized_document_is_rejected():
    document = pdf()
    document.size = uploads.MAX_UPLOAD_BYTES + 1
    with pytest.raises(ValidationError, match="10 MB"):
        uploads.validate_pdf(document)
