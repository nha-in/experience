from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from io import StringIO

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from ohc_experience.experiences.management.commands.seed_experience_demo import (
    ADMIN_EMAIL,
)
from ohc_experience.experiences.management.commands.seed_experience_demo import (
    APPLICANT_EMAIL,
)
from ohc_experience.experiences.management.commands.seed_experience_demo import (
    CONTRIBUTOR_EMAIL,
)
from ohc_experience.experiences.management.commands.seed_experience_demo import (
    DEFAULT_PASSWORD,
)
from ohc_experience.experiences.management.commands.seed_experience_demo import (
    DRAFT_REFERENCE,
)
from ohc_experience.experiences.management.commands.seed_experience_demo import (
    PRODUCT_NAME,
)
from ohc_experience.experiences.management.commands.seed_experience_demo import (
    PRODUCT_SLUG,
)
from ohc_experience.experiences.management.commands.seed_experience_demo import (
    REVIEW_REFERENCE,
)
from ohc_experience.experiences.models import ApplicationAccess
from ohc_experience.experiences.models import ApplicationDependency
from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import ApplicationQueryMessage
from ohc_experience.experiences.models import ApplicationQueryThread
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductOutcome
from ohc_experience.experiences.models import ProductType
from ohc_experience.experiences.models import QueryStatus

pytestmark = pytest.mark.django_db

DRAFT_SUBMISSION_COUNT = 3
REVIEW_SELECTED_SUBMISSION_COUNT = 10
SEEDED_CERTIFICATION_HISTORY_COUNT = 2
DEMO_ATTACHMENT_COUNT = 11
DRAFT_REQUIRED_FORM_COUNT = 11
REVIEW_REQUIRED_FORM_COUNT = 10
DRAFT_PROGRESS_PERCENT = 27
COMPLETE_PROGRESS_PERCENT = 100
FOUR_OF_ELEVEN_PERCENT = 36
MULTI_FILE_COUNT = 2
RENEWED_CERTIFICATION_NUMBER = 3
EDITED_REVISION_NUMBER = 2
EDITED_VERSION_COUNT = 2
CURRENT_CERTIFICATION_ATTACHMENT_COUNT = 4
APPENDED_CERTIFICATE_FILE_COUNT = 3
RETAINED_SUPPORTING_FILE_COUNT = 1
SEEDED_APPLICATION_COUNT = 2
HTMX_HEADERS = {"HX-Request": "true"}


def technical_readiness_data() -> dict[str, str]:
    return {
        "production_callback_url": "https://abdm.example.in/gateway",
        "health_check_url": "https://abdm.example.in/health",
        "public_key_url": "https://abdm.example.in/jwks.json",
        "outbound_ip_addresses": "203.0.113.44",
        "hosting_region": "Mumbai, India",
        "uptime_commitment": "99.90",
        "technical_contact_email": "ops@example.in",
        "incident_contact_phone": "+91 90000 00000",
        "callback_idempotency": "on",
        "secrets_confirmation": "on",
    }


def renewed_certification_data() -> dict[str, object]:
    return {
        "certification_type": "iso_27001",
        "certification_name": "ISO 27001 certification",
        "issuing_body": "Example Assurance Body",
        "certificate_number": "ISO-DEMO-2026-441",
        "issued_on": (timezone.localdate() - timedelta(days=3)).isoformat(),
        "expires_on": (timezone.localdate() + timedelta(days=365)).isoformat(),
        "scope_summary": "ABDM production services and supporting cloud controls.",
        "certificate_documents": [
            SimpleUploadedFile(
                "iso-certificate.pdf",
                b"certificate",
                content_type="application/pdf",
            ),
            SimpleUploadedFile(
                "iso-scope.pdf",
                b"scope",
                content_type="application/pdf",
            ),
        ],
        "supporting_documents": [
            SimpleUploadedFile(
                "control-map.pdf",
                b"controls",
                content_type="application/pdf",
            ),
            SimpleUploadedFile(
                "audit-cover.png",
                b"image",
                content_type="image/png",
            ),
        ],
    }


def product_form_data(*, name="Care Coordination Suite") -> dict[str, object]:
    return {
        "name": name,
        "product_type": ProductType.HEALTH_SERVICES,
        "description": (
            "Coordinates referrals, care navigation, and longitudinal follow-up."
        ),
        "website": "https://care.example.in",
        "current_facility_count": 12,
        "deployment_regions": "Karnataka\nTamil Nadu",
    }


def selected_submission(application, form_key):
    return (
        application.form_uses.select_related("selected_submission")
        .get(
            form_key=form_key,
        )
        .selected_submission
    )


def form_history(application, form_key):
    return application.form_uses.get(form_key=form_key).form.submissions.all()


def selected_submission_count(application):
    return application.form_uses.filter(selected_submission__isnull=False).count()


@pytest.fixture
def seeded_demo():
    output = StringIO()
    call_command("seed_experience_demo", stdout=output)
    return output.getvalue()


def test_seeder_creates_working_accounts_and_both_workflows(seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    admin = get_user_model().objects.get(email=ADMIN_EMAIL)
    draft = ApplicationInstance.objects.get(reference=DRAFT_REFERENCE)
    review = ApplicationInstance.objects.get(reference=REVIEW_REFERENCE)
    product = Product.objects.get(slug=PRODUCT_SLUG)

    assert applicant.check_password(DEFAULT_PASSWORD)
    assert admin.check_password(DEFAULT_PASSWORD)
    assert admin.is_ohc_team is True
    assert admin.is_staff is True
    assert EmailAddress.objects.get(user=applicant).verified is True
    assert product.name == PRODUCT_NAME
    assert product.product_type == ProductType.HMIS
    assert draft.product == product
    assert review.product == product
    assert list(draft.dependencies.all()) == [review]
    assert selected_submission_count(draft) == DRAFT_SUBMISSION_COUNT
    assert draft.metadata["required_forms"] == DRAFT_REQUIRED_FORM_COUNT
    assert draft.progress_percent == DRAFT_PROGRESS_PERCENT
    assert selected_submission_count(review) == REVIEW_SELECTED_SUBMISSION_COUNT
    draft_profile = selected_submission(draft, "organisation_profile")
    review_profile = selected_submission(review, "organisation_profile")
    assert review_profile == draft_profile
    assert review.form_uses.get(form_key="organisation_profile").is_reused
    assert review.metadata["required_forms"] == REVIEW_REQUIRED_FORM_COUNT
    assert review.progress_percent == COMPLETE_PROGRESS_PERCENT
    assert (
        FormAttachment.objects.filter(
            submission__form__application_uses__application=review,
            is_current=True,
        )
        .distinct()
        .count()
        == DEMO_ATTACHMENT_COUNT
    )
    assert review.access_grants.get(user=admin).role_key == "decision_maker"
    assert review.metadata["product_version"] == "3.2.0"
    assert review.metadata["milestones"] == ["m1", "m2", "m3"]
    assert review.metadata["protocols"] == ["nhcx", "uhi"]
    assert review.metadata["nhcx_participant_id"] == "NHCX-AROGYA-032"
    assert review.metadata["uhi_subscriber_id"] == "uhi.arogya.example.com"
    certifications = form_history(review, "security_certification").order_by(
        "submission_number",
    )
    assert certifications.count() == SEEDED_CERTIFICATION_HISTORY_COUNT
    assert list(certifications.values_list("submission_number", flat=True)) == [1, 2]
    assert certifications.get(is_current=True).valid_until == (
        timezone.localdate() + timedelta(days=20)
    )
    assert (
        ProductOutcome.objects.filter(
            product=product,
            outcome_type="sandbox_credentials",
        ).count()
        == SEEDED_APPLICATION_COUNT
    )
    applicant_query = draft.query_threads.get()
    assert applicant_query.status == QueryStatus.AWAITING_REVIEWER
    assert applicant_query.opened_by == applicant
    assert applicant_query.assigned_to is None
    assert APPLICANT_EMAIL in seeded_demo
    assert ADMIN_EMAIL in seeded_demo
    assert DEFAULT_PASSWORD in seeded_demo
    assert f"/products/{PRODUCT_SLUG}/" in seeded_demo


def test_seeder_is_idempotent(seeded_demo):
    before = (
        Product.objects.count(),
        ApplicationInstance.objects.count(),
        ApplicationDependency.objects.count(),
        FormAttachment.objects.count(),
        ApplicationQueryThread.objects.count(),
        ApplicationQueryMessage.objects.count(),
        get_user_model().objects.count(),
    )

    call_command("seed_experience_demo", stdout=StringIO())

    assert (
        Product.objects.count(),
        ApplicationInstance.objects.count(),
        ApplicationDependency.objects.count(),
        FormAttachment.objects.count(),
        ApplicationQueryThread.objects.count(),
        ApplicationQueryMessage.objects.count(),
        get_user_model().objects.count(),
    ) == before


def test_product_workspace_lists_related_applications(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)

    list_response = client.get(reverse("products:list"))
    detail_response = client.get(reverse("products:detail", args=[PRODUCT_SLUG]))
    detail_html = detail_response.content.decode()

    assert list_response.status_code == HTTPStatus.OK
    assert PRODUCT_NAME in list_response.content.decode()
    assert detail_response.status_code == HTTPStatus.OK
    assert DRAFT_REFERENCE in detail_html
    assert REVIEW_REFERENCE in detail_html
    assert "Start ABDM production access" in detail_html
    assert "Prerequisites" in detail_html
    assert "Product outcomes" in detail_html
    assert "ABDM sandbox credentials" in detail_html
    assert "Shared forms" in detail_html


def test_product_create_edit_and_application_start_use_htmx(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    review = ApplicationInstance.objects.get(reference=REVIEW_REFERENCE)
    client.force_login(applicant)

    create_response = client.post(
        reverse("products:create"),
        product_form_data(),
        headers=HTMX_HEADERS,
    )
    product = Product.objects.get(name="Care Coordination Suite")

    assert create_response.status_code == HTTPStatus.OK
    assert create_response["HX-Redirect"] == product.get_absolute_url()
    assert product.created_by == applicant

    edit_response = client.post(
        reverse("products:edit", args=[product.slug]),
        product_form_data(name="Care Coordination Suite Pro"),
        headers=HTMX_HEADERS,
    )
    product.refresh_from_db()

    assert edit_response.status_code == HTTPStatus.OK
    assert edit_response["HX-Redirect"] == product.get_absolute_url()
    assert product.name == "Care Coordination Suite Pro"
    assert product.slug == "care-coordination-suite"

    start_url = reverse(
        "products:start-application",
        args=[PRODUCT_SLUG, "abdm_production_access"],
    )
    start_page = client.get(start_url)
    start_response = client.post(
        start_url,
        {"dependencies": [review.pk]},
        headers=HTMX_HEADERS,
    )
    application = (
        ApplicationInstance.objects.filter(product=review.product)
        .exclude(reference__in=[DRAFT_REFERENCE, REVIEW_REFERENCE])
        .get()
    )

    assert start_page.status_code == HTTPStatus.OK
    assert REVIEW_REFERENCE in start_page.content.decode()
    assert start_response.status_code == HTTPStatus.OK
    assert start_response["HX-Redirect"] == reverse(
        "experiences:detail",
        args=[application.reference],
    )
    assert list(application.dependencies.all()) == [review]


def test_applicant_dashboard_detail_and_form_render(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)

    list_response = client.get(reverse("experiences:list"))
    detail_response = client.get(
        reverse("experiences:detail", args=[DRAFT_REFERENCE]),
    )
    form_response = client.get(
        reverse(
            "experiences:form",
            args=[DRAFT_REFERENCE, "technical_readiness"],
        ),
    )
    nhcx_response = client.get(
        reverse(
            "experiences:form",
            args=[DRAFT_REFERENCE, "nhcx_integration"],
        ),
    )
    uhi_response = client.get(
        reverse(
            "experiences:form",
            args=[DRAFT_REFERENCE, "uhi_integration"],
        ),
    )

    assert list_response.status_code == HTTPStatus.OK
    list_html = list_response.content.decode()
    assert DRAFT_REFERENCE in list_html
    assert 'hx-target="#application-results"' in list_html
    assert detail_response.status_code == HTTPStatus.OK
    detail_html = detail_response.content.decode()
    assert "Forms used by this application" in detail_html
    assert "3 of 11 currently required forms complete" in detail_html
    assert "NHCX claims exchange" in detail_html
    assert "UHI service network" in detail_html
    assert "Health locker operations" in detail_html
    assert "Technical readiness" in detail_html
    assert "Security and privacy" not in detail_html
    assert "Pending application queries" in detail_html
    assert "Application prerequisites" in detail_html
    assert REVIEW_REFERENCE in detail_html
    assert "cannot be submitted until every prerequisite is approved" in detail_html
    assert "Ask review team" in detail_html
    assert form_response.status_code == HTTPStatus.OK
    form_html = form_response.content.decode()
    assert "Production gateway callback" in form_html
    assert 'id="application-form"' in form_html
    assert 'hx-encoding="multipart/form-data"' in form_html
    assert nhcx_response.status_code == HTTPStatus.OK
    assert "NHCX participant roles" in nhcx_response.content.decode()
    assert "NHCX conformance and test evidence" in nhcx_response.content.decode()
    assert uhi_response.status_code == HTTPStatus.OK
    assert "UHI participant role" in uhi_response.content.decode()
    assert "UHI conformance and test evidence" in uhi_response.content.decode()


def test_hidden_dependency_cannot_be_opened_directly(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)

    response = client.get(
        reverse(
            "experiences:form",
            args=[DRAFT_REFERENCE, "security_compliance"],
        ),
    )

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_applicant_can_complete_the_next_gated_form(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)
    url = reverse(
        "experiences:form",
        args=[DRAFT_REFERENCE, "technical_readiness"],
    )

    response = client.post(
        url,
        technical_readiness_data(),
    )

    assert response.status_code == HTTPStatus.FOUND
    application = ApplicationInstance.objects.get(reference=DRAFT_REFERENCE)
    submission = selected_submission(application, "technical_readiness")
    assert submission is not None
    assert submission.origin_application == application
    assert application.progress_percent == FOUR_OF_ELEVEN_PERCENT
    detail_html = client.get(
        reverse("experiences:detail", args=[DRAFT_REFERENCE]),
    ).content.decode()
    assert "Security and privacy" in detail_html
    assert "Conformance evidence" not in detail_html


def test_htmx_filter_returns_only_application_results(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)

    response = client.get(
        reverse("experiences:list"),
        {"status": "draft"},
        headers=HTMX_HEADERS,
    )
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert 'id="application-results"' in html
    assert 'id="main-content"' not in html
    assert DRAFT_REFERENCE in html
    assert REVIEW_REFERENCE not in html


def test_pending_query_filter_and_highlight_for_applicant(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)

    pending = client.get(
        reverse("experiences:list"),
        {"query_state": "pending"},
        headers=HTMX_HEADERS,
    )
    pending_html = pending.content.decode()

    assert pending.status_code == HTTPStatus.OK
    assert DRAFT_REFERENCE in pending_html
    assert REVIEW_REFERENCE not in pending_html
    assert "1 pending query" in pending_html
    assert "bg-orange-50/70" in pending_html

    clear_html = client.get(
        reverse("experiences:list"),
        {"query_state": "clear"},
        headers=HTMX_HEADERS,
    ).content.decode()
    assert REVIEW_REFERENCE in clear_html
    assert DRAFT_REFERENCE not in clear_html


def test_boosted_application_navigation_returns_the_whole_page(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)

    response = client.get(
        reverse("experiences:list"),
        headers={"HX-Request": "true", "HX-Boosted": "true"},
    )
    html = response.content.decode()

    assert 'id="main-content"' in html
    assert 'id="app-nav"' in html


def test_htmx_form_errors_swap_only_the_form(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)
    url = reverse(
        "experiences:form",
        args=[DRAFT_REFERENCE, "technical_readiness"],
    )

    response = client.post(url, {}, headers=HTMX_HEADERS)
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert 'id="application-form"' in html
    assert 'id="main-content"' not in html
    assert "This field is required" in html


def test_htmx_form_success_redirects_to_the_workspace(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)
    url = reverse(
        "experiences:form",
        args=[DRAFT_REFERENCE, "technical_readiness"],
    )

    response = client.post(
        url,
        technical_readiness_data(),
        headers=HTMX_HEADERS,
    )

    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse(
        "experiences:detail",
        args=[DRAFT_REFERENCE],
    )


def test_form_revisions_remain_visible_when_editing_is_locked(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)
    url = reverse(
        "experiences:form",
        args=[DRAFT_REFERENCE, "technical_readiness"],
    )
    first_data = technical_readiness_data()
    updated_data = {**first_data, "hosting_region": "Hyderabad, India"}

    first_response = client.post(url, first_data)
    second_response = client.post(url, updated_data)

    assert first_response.status_code == HTTPStatus.FOUND
    assert second_response.status_code == HTTPStatus.FOUND
    application = ApplicationInstance.objects.get(reference=DRAFT_REFERENCE)
    versions = form_history(application, "technical_readiness").filter(
        origin_application=application,
    )
    current = selected_submission(application, "technical_readiness")
    assert versions.count() == EDITED_VERSION_COUNT
    assert current == versions.get(is_current=True)

    application.status = "submitted"
    application.save(update_fields=["status", "updated_at"])
    workspace = client.get(url)
    workspace_html = workspace.content.decode()

    assert workspace.status_code == HTTPStatus.OK
    assert 'id="application-form"' not in workspace_html
    assert "Saved form" in workspace_html
    assert "Version history" in workspace_html
    assert "Mumbai, India" in workspace_html
    assert "Hyderabad, India" in workspace_html


def test_applicant_can_raise_query_with_htmx(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)
    url = reverse(
        "experiences:action",
        args=[DRAFT_REFERENCE, "ask_review_team"],
    )

    workspace = client.get(url)
    response = client.post(
        url,
        {
            "subject": "Check partner authorization evidence",
            "related_form": "integration_scope",
            "message": "Would a signed authorization letter be sufficient?",
        },
        headers=HTMX_HEADERS,
    )

    assert workspace.status_code == HTTPStatus.OK
    workspace_html = workspace.content.decode()
    assert "Question or support request" in workspace_html
    assert "Due date" not in workspace_html
    assert 'hx-post="' in workspace_html
    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse(
        "experiences:detail",
        args=[DRAFT_REFERENCE],
    )

    application = ApplicationInstance.objects.get(reference=DRAFT_REFERENCE)
    query = application.query_threads.get(
        subject="Check partner authorization evidence",
    )
    assert application.status == "draft"
    assert query.status == QueryStatus.AWAITING_REVIEWER
    assert query.opened_by == applicant


def test_htmx_repeatable_certification_accepts_multiple_file_groups(
    client,
    seeded_demo,
    settings,
    tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)
    url = reverse(
        "experiences:form",
        args=[REVIEW_REFERENCE, "security_certification"],
    )

    workspace = client.get(url)
    workspace_html = workspace.content.decode()
    assert workspace.status_code == HTTPStatus.OK
    assert "Submission 2" in workspace_html
    assert "Version history" in workspace_html
    assert 'name="submission_mode" value="renew"' in workspace_html
    assert workspace_html.count("multiple") >= MULTI_FILE_COUNT

    edit_workspace = client.get(url, {"mode": "edit"})
    edit_html = edit_workspace.content.decode()
    assert edit_workspace.status_code == HTTPStatus.OK
    assert 'name="submission_mode" value="edit"' in edit_html
    assert "CERTIN-DEMO-2026-118" in edit_html
    assert "data-file-upload" in edit_html
    assert "Saved files" in edit_html
    assert "Add files" in edit_html
    assert "demo-security-certificate.pdf" in edit_html
    assert "demo-security-assessment-annexure.pdf" in edit_html
    assert "demo-remediation-closure.pdf" in edit_html
    assert "demo-scope-confirmation.pdf" in edit_html
    assert (
        edit_html.count("data-existing-file-remove")
        == CURRENT_CERTIFICATION_ATTACHMENT_COUNT
    )

    response = client.post(
        url,
        renewed_certification_data(),
        headers=HTMX_HEADERS,
    )

    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse(
        "experiences:detail",
        args=[REVIEW_REFERENCE],
    )
    application = ApplicationInstance.objects.get(reference=REVIEW_REFERENCE)
    current = selected_submission(application, "security_certification")
    assert application.status == "under_review"
    assert current.submission_number == RENEWED_CERTIFICATION_NUMBER
    assert (
        current.attachments.filter(field_key="certificate_documents").count()
        == MULTI_FILE_COUNT
    )
    assert (
        current.attachments.filter(field_key="supporting_documents").count()
        == MULTI_FILE_COUNT
    )
    assert (
        form_history(application, "security_certification").count()
        == RENEWED_CERTIFICATION_NUMBER
    )

    detail_html = client.get(
        reverse("experiences:detail", args=[REVIEW_REFERENCE]),
    ).content.decode()
    assert "3 submissions" in detail_html

    client.force_login(get_user_model().objects.get(email=ADMIN_EMAIL))
    admin_html = client.get(
        reverse("ohc:application-detail", args=[REVIEW_REFERENCE]),
    ).content.decode()
    assert "Previous versions" in admin_html
    assert "iso-certificate.pdf" in admin_html
    assert "iso-scope.pdf" in admin_html
    assert "control-map.pdf" in admin_html

    attachment = current.attachments.get(original_name="iso-certificate.pdf")
    download = client.get(
        reverse(
            "experiences:attachment",
            args=[REVIEW_REFERENCE, attachment.pk],
        ),
    )
    assert download.status_code == HTTPStatus.OK
    assert download["Content-Disposition"].endswith('filename="iso-certificate.pdf"')
    download.close()


def test_htmx_edit_appends_and_removes_files_in_existing_submission(
    client,
    seeded_demo,
    settings,
    tmp_path,
):
    settings.MEDIA_ROOT = tmp_path
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)
    application = ApplicationInstance.objects.get(reference=REVIEW_REFERENCE)
    previous = selected_submission(application, "security_certification")
    removed = previous.attachments.filter(
        field_key="supporting_documents",
        is_current=True,
    ).first()
    assert removed is not None
    data = renewed_certification_data()
    data.update(
        {
            "submission_mode": "edit",
            "certificate_number": "CERTIN-DEMO-2026-118-EDITED",
            "certificate_documents": [
                SimpleUploadedFile(
                    "additional-scope.pdf",
                    b"additional scope",
                    content_type="application/pdf",
                ),
            ],
            "remove_files__supporting_documents": str(removed.pk),
        },
    )
    data.pop("supporting_documents")
    url = reverse(
        "experiences:form",
        args=[REVIEW_REFERENCE, "security_certification"],
    )

    response = client.post(url, data, headers=HTMX_HEADERS)

    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse(
        "experiences:detail",
        args=[REVIEW_REFERENCE],
    )
    previous.refresh_from_db()
    current = selected_submission(application, "security_certification")
    assert previous.is_current is False
    assert current.submission_number == previous.submission_number
    assert current.revision == EDITED_REVISION_NUMBER
    assert (
        current.attachments.filter(
            field_key="certificate_documents",
            is_current=True,
        ).count()
        == APPENDED_CERTIFICATE_FILE_COUNT
    )
    assert (
        current.attachments.filter(
            field_key="supporting_documents",
            is_current=True,
        ).count()
        == RETAINED_SUPPORTING_FILE_COUNT
    )
    assert current.attachments.filter(
        original_name="additional-scope.pdf",
        is_current=True,
    ).exists()
    assert not current.attachments.filter(
        original_name=removed.original_name,
        is_current=True,
    ).exists()
    assert previous.attachments.filter(pk=removed.pk, is_current=True).exists()


def test_admin_dashboard_review_and_decision_form_render(client, seeded_demo):
    admin = get_user_model().objects.get(email=ADMIN_EMAIL)
    client.force_login(admin)

    list_response = client.get(reverse("ohc:applications"))
    detail_response = client.get(
        reverse("ohc:application-detail", args=[REVIEW_REFERENCE]),
    )
    approve_response = client.get(
        reverse("ohc:application-action", args=[REVIEW_REFERENCE, "approve"]),
    )

    assert list_response.status_code == HTTPStatus.OK
    assert REVIEW_REFERENCE in list_response.content.decode()
    assert detail_response.status_code == HTTPStatus.OK
    detail_html = detail_response.content.decode()
    assert "Submitted application pack" in detail_html
    assert "Verify security evidence" in detail_html
    assert "100%" in detail_html
    assert "Decision blockers" not in detail_html
    assert approve_response.status_code == HTTPStatus.OK
    approve_html = approve_response.content.decode()
    assert "Production client ID" in approve_html
    assert 'id="application-action"' in approve_html
    assert 'hx-post="' in approve_html


def test_admin_console_shows_unassigned_applications_as_read_only(
    client,
    seeded_demo,
):
    admin = get_user_model().objects.get(email=ADMIN_EMAIL)
    application = ApplicationInstance.objects.get(reference=REVIEW_REFERENCE)
    ApplicationAccess.objects.filter(application=application, user=admin).delete()
    client.force_login(admin)

    list_response = client.get(reverse("ohc:applications"))
    detail_response = client.get(
        reverse("ohc:application-detail", args=[REVIEW_REFERENCE]),
    )
    approve_response = client.get(
        reverse("ohc:application-action", args=[REVIEW_REFERENCE, "approve"]),
    )

    assert list_response.status_code == HTTPStatus.OK
    assert list_response.context["total_count"] == SEEDED_APPLICATION_COUNT
    assert REVIEW_REFERENCE in list_response.content.decode()
    assert detail_response.status_code == HTTPStatus.OK
    assert "You are review observer" in detail_response.content.decode()
    assert approve_response.status_code == HTTPStatus.FORBIDDEN


def test_htmx_admin_filter_returns_only_application_results(client, seeded_demo):
    admin = get_user_model().objects.get(email=ADMIN_EMAIL)
    client.force_login(admin)

    response = client.get(
        reverse("ohc:applications"),
        {"status": "under_review"},
        headers=HTMX_HEADERS,
    )
    html = response.content.decode()

    assert 'id="application-results"' in html
    assert 'id="ohc-nav"' not in html
    assert REVIEW_REFERENCE in html
    assert DRAFT_REFERENCE not in html


def test_pending_query_filter_and_highlight_for_admin(client, seeded_demo):
    admin = get_user_model().objects.get(email=ADMIN_EMAIL)
    client.force_login(admin)

    pending = client.get(
        reverse("ohc:applications"),
        {"query_state": "pending"},
        headers=HTMX_HEADERS,
    )
    pending_html = pending.content.decode()

    assert pending.status_code == HTTPStatus.OK
    assert DRAFT_REFERENCE in pending_html
    assert REVIEW_REFERENCE not in pending_html
    assert "1 pending" in pending_html
    assert "bg-orange-50/70" in pending_html

    clear_html = client.get(
        reverse("ohc:applications"),
        {"query_state": "clear"},
        headers=HTMX_HEADERS,
    ).content.decode()
    assert REVIEW_REFERENCE in clear_html
    assert DRAFT_REFERENCE not in clear_html


def test_applicant_cannot_use_admin_console_or_approve(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    client.force_login(applicant)

    console_response = client.get(reverse("ohc:applications"))
    approve_response = client.get(
        reverse("experiences:action", args=[REVIEW_REFERENCE, "approve"]),
    )
    form_action_response = client.get(
        reverse(
            "experiences:form-action",
            args=[REVIEW_REFERENCE, "security_compliance", "verify_evidence"],
        ),
    )
    draft_html = client.get(
        reverse("experiences:detail", args=[DRAFT_REFERENCE]),
    ).content.decode()

    assert console_response.status_code == HTTPStatus.FORBIDDEN
    assert approve_response.status_code == HTTPStatus.FORBIDDEN
    assert form_action_response.status_code == HTTPStatus.FORBIDDEN
    assert "Withdraw" not in draft_html


def test_admin_can_run_completed_form_action_with_htmx(client, seeded_demo):
    admin = get_user_model().objects.get(email=ADMIN_EMAIL)
    client.force_login(admin)
    url = reverse(
        "ohc:application-form-action",
        args=[REVIEW_REFERENCE, "security_compliance", "verify_evidence"],
    )

    workspace = client.get(url)
    response = client.post(url, headers=HTMX_HEADERS)
    submission = FormSubmission.objects.get(
        form__application_uses__application__reference=REVIEW_REFERENCE,
        form__application_uses__form_key="security_compliance",
        is_current=True,
    )

    assert workspace.status_code == HTTPStatus.OK
    assert "Verify security evidence" in workspace.content.decode()
    assert 'id="application-action"' in workspace.content.decode()
    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse(
        "ohc:application-detail",
        args=[REVIEW_REFERENCE],
    )
    assert submission.metadata["verified_revision"] == submission.revision

    detail_html = client.get(
        reverse("ohc:application-detail", args=[REVIEW_REFERENCE]),
    ).content.decode()
    assert "Evidence verified" in detail_html
    assert "Verify security evidence" not in detail_html


def test_admin_can_raise_a_query_from_the_review_workspace(client, seeded_demo):
    admin = get_user_model().objects.get(email=ADMIN_EMAIL)
    client.force_login(admin)
    url = reverse(
        "ohc:application-action",
        args=[REVIEW_REFERENCE, "raise_query"],
    )

    response = client.post(
        url,
        {
            "subject": "Confirm the callback host scope",
            "related_form": "security_compliance",
            "message": "Please point us to the assessment section covering this host.",
            "due_at": "",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    application = ApplicationInstance.objects.get(reference=REVIEW_REFERENCE)
    assert application.status == "changes_requested"
    assert application.query_threads.get().subject == "Confirm the callback host scope"


def test_htmx_query_reply_and_resolution_swap_the_workspace(client, seeded_demo):
    admin = get_user_model().objects.get(email=ADMIN_EMAIL)
    client.force_login(admin)
    action_url = reverse(
        "ohc:application-action",
        args=[REVIEW_REFERENCE, "raise_query"],
    )
    client.post(
        action_url,
        {
            "subject": "Confirm infrastructure scope",
            "related_form": "technical_readiness",
            "message": "Please confirm the listed production hosts.",
            "due_at": "",
        },
    )
    application = ApplicationInstance.objects.get(reference=REVIEW_REFERENCE)
    thread = application.query_threads.get()
    query_url = reverse(
        "ohc:application-query",
        args=[REVIEW_REFERENCE, thread.pk],
    )

    reply = client.post(
        query_url,
        {"body": "The production hosts are confirmed."},
        headers=HTMX_HEADERS,
    )
    reply_html = reply.content.decode()

    assert reply.status_code == HTTPStatus.OK
    assert 'id="query-workspace"' in reply_html
    assert 'id="query-header" hx-swap-oob="outerHTML"' in reply_html
    assert "The production hosts are confirmed." in reply_html

    resolve = client.post(
        reverse(
            "ohc:application-query-resolve",
            args=[REVIEW_REFERENCE, thread.pk],
        ),
        headers=HTMX_HEADERS,
    )
    resolve_html = resolve.content.decode()
    thread.refresh_from_db()

    assert resolve.status_code == HTTPStatus.OK
    assert thread.status == "resolved"
    assert "This query is resolved" in resolve_html
    assert 'hx-swap-oob="innerHTML"' in resolve_html


def test_htmx_access_update_swaps_the_access_workspace(client, seeded_demo):
    applicant = get_user_model().objects.get(email=APPLICANT_EMAIL)
    contributor = get_user_model().objects.get(email=CONTRIBUTOR_EMAIL)
    client.force_login(applicant)
    url = reverse("experiences:access", args=[DRAFT_REFERENCE])

    response = client.post(
        url,
        {
            "user": contributor.pk,
            "role": "applicant_viewer",
            "direct_permissions": [],
        },
        headers=HTMX_HEADERS,
    )
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert 'id="access-workspace"' in html
    assert 'id="flash-messages"' in html
    assert 'hx-swap-oob="innerHTML"' in html
    assert "Applicant viewer" in html
    assert ADMIN_EMAIL not in html
