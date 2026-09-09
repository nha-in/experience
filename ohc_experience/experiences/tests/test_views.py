"""The applicant and console screens, driven through the sample experience."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from django.utils.datastructures import MultiValueDict

from ohc_experience.experiences.models import ApplicationAccess
from ohc_experience.experiences.models import ApplicationFormSubmission
from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import QueryStatus
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.services import application_context
from ohc_experience.experiences.services import create_application
from ohc_experience.experiences.services import perform_application_action
from ohc_experience.experiences.services import save_form_submission
from ohc_experience.experiences.tests.sample import SampleExperience
from ohc_experience.organisations.models import Membership
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

ADMIN_EMAIL = "admin@ohc.network"
DRAFT_COMPLETE_TEXT = "3 of 7 currently required forms complete"
FOUR_OF_SEVEN_PERCENT = 57
MULTI_FILE_COUNT = 2
RENEWED_CERTIFICATION_NUMBER = 3
EDITED_REVISION_NUMBER = 2
EDITED_VERSION_COUNT = 2
CURRENT_CERTIFICATION_ATTACHMENT_COUNT = 4
APPENDED_CERTIFICATE_FILE_COUNT = 3
RETAINED_SUPPORTING_FILE_COUNT = 1
HTMX_HEADERS = {"HX-Request": "true"}


def pdf(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4 sample", content_type="application/pdf")


def png(name: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"image", content_type="image/png")


def day(offset: int) -> str:
    return (timezone.localdate() + timedelta(days=offset)).isoformat()


def readiness_data() -> dict[str, str]:
    return {
        "endpoint_url": "https://api.arogya.example.in/gateway",
        "region": "Mumbai, India",
    }


def renewed_certification_data() -> dict[str, object]:
    return {
        "certificate_number": "ISO-DEMO-2026-441",
        "issued_on": day(-3),
        "expires_on": day(365),
        "certificate_documents": [pdf("iso-certificate.pdf"), pdf("iso-scope.pdf")],
        "supporting_documents": [pdf("control-map-2.pdf"), png("audit-cover-2.png")],
    }


def complete(application, user, form_key, data, files=None, mode=None):  # noqa: PLR0913, PLR0917
    definition = SampleExperience.get_form(form_key)
    form = definition.build_form(
        context=application_context(application, user),
        data=data,
        files=files,
        submission_mode=mode,
    )
    assert form.is_valid(), form.errors
    return save_form_submission(
        application=application,
        form_key=form_key,
        form=form,
        user=user,
        submission_mode=mode,
    )


@dataclass
class Workspaces:
    applicant: object
    contributor: object
    admin: object
    draft: ApplicationInstance
    review: ApplicationInstance


@pytest.fixture
def workspaces() -> Workspaces:
    organisation = OrganisationFactory(onboarded=True)
    applicant = UserFactory(email="applicant@example.in", name="Kavya Rao")
    contributor = UserFactory(email="contributor@example.in", name="Arjun Menon")
    admin = UserFactory(email=ADMIN_EMAIL, is_ohc_team=True, is_staff=True)
    Membership.objects.create(
        organisation=organisation,
        user=applicant,
        role=Role.OWNER,
    )
    Membership.objects.create(
        organisation=organisation,
        user=contributor,
        role=Role.DEVELOPER,
    )

    draft = create_application(
        application_type=SampleExperience.key,
        organisation=organisation,
        user=applicant,
    )
    review = create_application(
        application_type=SampleExperience.key,
        organisation=organisation,
        user=applicant,
    )
    for application in (draft, review):
        ApplicationAccess.objects.create(
            application=application,
            user=admin,
            role_key="decision_maker",
            granted_by=admin,
        )

    complete(
        draft,
        applicant,
        "profile",
        {"legal_name": "Arogya Digital Health", "contact_email": applicant.email},
    )
    complete(draft, applicant, "scope", {"channels": ["web", "locker"]})
    complete(draft, applicant, "locker_operations", {"locker_name": "Arogya locker"})
    perform_application_action(
        application=draft,
        action_key="ask_review_team",
        user=applicant,
        cleaned_data={
            "subject": "Which report format is accepted?",
            "message": "Is a scanned PDF acceptable?",
            "related_form": "scope",
        },
    )

    complete(
        review,
        applicant,
        "profile",
        {"legal_name": "Arogya Digital Health", "contact_email": applicant.email},
    )
    complete(review, applicant, "scope", {"channels": ["web"]})
    complete(review, applicant, "readiness", readiness_data())
    complete(
        review,
        applicant,
        "compliance",
        {"assessment_date": day(-30)},
        files=MultiValueDict({"report": [pdf("assessment-report.pdf")]}),
    )
    complete(
        review,
        applicant,
        "certification",
        {
            "certificate_number": "CERT-2025-041",
            "issued_on": day(-760),
            "expires_on": day(-395),
        },
        files=MultiValueDict({"certificate_documents": [pdf("old-certificate.pdf")]}),
    )
    complete(
        review,
        applicant,
        "certification",
        {
            "certificate_number": "CERT-2026-118",
            "issued_on": day(-345),
            "expires_on": day(20),
        },
        files=MultiValueDict(
            {
                "certificate_documents": [pdf("certificate.pdf"), pdf("annexure.pdf")],
                "supporting_documents": [
                    pdf("control-map.pdf"),
                    png("audit-cover.png"),
                ],
            },
        ),
        mode="renew",
    )
    complete(
        review,
        applicant,
        "declaration",
        {"signatory_name": "Dr Kavya Rao", "confirmed": "on"},
    )
    perform_application_action(application=review, action_key="submit", user=applicant)
    perform_application_action(
        application=review,
        action_key="start_review",
        user=admin,
    )
    draft.refresh_from_db()
    review.refresh_from_db()
    return Workspaces(applicant, contributor, admin, draft, review)


def test_applicant_dashboard_detail_and_form_render(client, workspaces):
    client.force_login(workspaces.applicant)
    draft = workspaces.draft.reference

    list_response = client.get(reverse("experiences:list"))
    detail_response = client.get(reverse("experiences:detail", args=[draft]))
    form_response = client.get(reverse("experiences:form", args=[draft, "readiness"]))

    assert list_response.status_code == HTTPStatus.OK
    list_html = list_response.content.decode()
    assert draft in list_html
    assert 'hx-target="#application-results"' in list_html
    assert detail_response.status_code == HTTPStatus.OK
    detail_html = detail_response.content.decode()
    assert "Application forms" in detail_html
    assert DRAFT_COMPLETE_TEXT in detail_html
    assert "Locker operations" in detail_html
    assert "Technical readiness" in detail_html
    assert "Compliance evidence" not in detail_html
    assert "Pending application queries" in detail_html
    assert "Ask review team" in detail_html
    assert form_response.status_code == HTTPStatus.OK
    form_html = form_response.content.decode()
    assert "Production endpoint" in form_html
    assert 'id="application-form"' in form_html
    assert 'hx-encoding="multipart/form-data"' in form_html


def test_list_renders_without_any_registered_experience(
    client,
    monkeypatch,
    owner_membership,
):
    monkeypatch.setattr(registry, "_definitions", {})
    client.force_login(owner_membership.user)

    response = client.get(reverse("experiences:list"))
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert "Start application" not in html


def test_start_creates_a_workspace_and_unknown_types_404(client, workspaces):
    client.force_login(workspaces.applicant)

    unknown = client.get(reverse("experiences:start", args=["nope"]))
    page = client.get(reverse("experiences:start", args=[SampleExperience.key]))
    created = client.post(reverse("experiences:start", args=[SampleExperience.key]))

    assert unknown.status_code == HTTPStatus.NOT_FOUND
    assert page.status_code == HTTPStatus.OK
    assert created.status_code == HTTPStatus.FOUND
    reference = created["Location"].rstrip("/").rsplit("/", 1)[-1]
    assert ApplicationInstance.objects.filter(reference=reference).exists()


def test_hidden_dependency_cannot_be_opened_directly(client, workspaces):
    client.force_login(workspaces.applicant)

    response = client.get(
        reverse("experiences:form", args=[workspaces.draft.reference, "compliance"]),
    )

    assert response.status_code == HTTPStatus.FORBIDDEN


def test_applicant_can_complete_the_next_gated_form(client, workspaces):
    client.force_login(workspaces.applicant)
    draft = workspaces.draft.reference
    url = reverse("experiences:form", args=[draft, "readiness"])

    response = client.post(url, readiness_data())

    assert response.status_code == HTTPStatus.FOUND
    application = ApplicationInstance.objects.get(reference=draft)
    assert application.submissions.filter(form_key="readiness").exists()
    assert application.progress_percent == FOUR_OF_SEVEN_PERCENT
    detail_html = client.get(
        reverse("experiences:detail", args=[draft]),
    ).content.decode()
    assert "Compliance evidence" in detail_html
    assert "Security certification" not in detail_html


def test_htmx_filter_returns_only_application_results(client, workspaces):
    client.force_login(workspaces.applicant)

    response = client.get(
        reverse("experiences:list"),
        {"status": "draft"},
        headers=HTMX_HEADERS,
    )
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert 'id="application-results"' in html
    assert 'id="main-content"' not in html
    assert workspaces.draft.reference in html
    assert workspaces.review.reference not in html


def test_pending_query_filter_and_highlight_for_applicant(client, workspaces):
    client.force_login(workspaces.applicant)

    pending = client.get(
        reverse("experiences:list"),
        {"query_state": "pending"},
        headers=HTMX_HEADERS,
    )
    pending_html = pending.content.decode()

    assert pending.status_code == HTTPStatus.OK
    assert workspaces.draft.reference in pending_html
    assert workspaces.review.reference not in pending_html
    assert "1 pending query" in pending_html
    assert "bg-orange-50/70" in pending_html

    clear_html = client.get(
        reverse("experiences:list"),
        {"query_state": "clear"},
        headers=HTMX_HEADERS,
    ).content.decode()
    assert workspaces.review.reference in clear_html
    assert workspaces.draft.reference not in clear_html


def test_boosted_application_navigation_returns_the_whole_page(client, workspaces):
    client.force_login(workspaces.applicant)

    response = client.get(
        reverse("experiences:list"),
        headers={"HX-Request": "true", "HX-Boosted": "true"},
    )
    html = response.content.decode()

    assert 'id="main-content"' in html
    assert 'id="app-nav"' in html


def test_htmx_form_errors_swap_only_the_form(client, workspaces):
    client.force_login(workspaces.applicant)
    url = reverse("experiences:form", args=[workspaces.draft.reference, "readiness"])

    response = client.post(url, {}, headers=HTMX_HEADERS)
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert 'id="application-form"' in html
    assert 'id="main-content"' not in html
    assert "This field is required" in html


def test_htmx_form_success_redirects_to_the_workspace(client, workspaces):
    client.force_login(workspaces.applicant)
    draft = workspaces.draft.reference
    url = reverse("experiences:form", args=[draft, "readiness"])

    response = client.post(url, readiness_data(), headers=HTMX_HEADERS)

    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse("experiences:detail", args=[draft])


def test_form_revisions_remain_visible_when_editing_is_locked(client, workspaces):
    client.force_login(workspaces.applicant)
    draft = workspaces.draft.reference
    url = reverse("experiences:form", args=[draft, "readiness"])
    first_data = readiness_data()
    updated_data = {**first_data, "region": "Hyderabad, India"}

    first_response = client.post(url, first_data)
    second_response = client.post(url, updated_data)

    assert first_response.status_code == HTTPStatus.FOUND
    assert second_response.status_code == HTTPStatus.FOUND
    application = ApplicationInstance.objects.get(reference=draft)
    versions = application.submissions.filter(form_key="readiness")
    current = versions.get(is_current=True)
    assert versions.count() == EDITED_VERSION_COUNT
    assert current.revision == EDITED_REVISION_NUMBER

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


def test_applicant_can_raise_query_with_htmx(client, workspaces):
    client.force_login(workspaces.applicant)
    draft = workspaces.draft.reference
    url = reverse("experiences:action", args=[draft, "ask_review_team"])

    workspace = client.get(url)
    response = client.post(
        url,
        {
            "subject": "Check partner authorization evidence",
            "related_form": "scope",
            "message": "Would a signed authorization letter be sufficient?",
        },
        headers=HTMX_HEADERS,
    )

    assert workspace.status_code == HTTPStatus.OK
    workspace_html = workspace.content.decode()
    assert "Question or support request" in workspace_html
    assert 'name="due_at"' not in workspace_html
    assert 'hx-post="' in workspace_html
    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse("experiences:detail", args=[draft])

    application = ApplicationInstance.objects.get(reference=draft)
    query = application.query_threads.get(
        subject="Check partner authorization evidence",
    )
    assert application.status == "draft"
    assert query.status == QueryStatus.AWAITING_REVIEWER
    assert query.opened_by == workspaces.applicant


def test_htmx_repeatable_certification_accepts_multiple_file_groups(client, workspaces):
    client.force_login(workspaces.applicant)
    review = workspaces.review.reference
    url = reverse("experiences:form", args=[review, "certification"])

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
    assert "CERT-2026-118" in edit_html
    assert "data-file-upload" in edit_html
    assert "Saved files" in edit_html
    assert "Add files" in edit_html
    for name in (
        "certificate.pdf",
        "annexure.pdf",
        "control-map.pdf",
        "audit-cover.png",
    ):
        assert name in edit_html
    assert edit_html.count("data-existing-file-remove") == (
        CURRENT_CERTIFICATION_ATTACHMENT_COUNT
    )

    response = client.post(url, renewed_certification_data(), headers=HTMX_HEADERS)

    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse("experiences:detail", args=[review])
    application = ApplicationInstance.objects.get(reference=review)
    current = application.submissions.get(form_key="certification", is_current=True)
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
        application.submissions.filter(form_key="certification").count()
        == RENEWED_CERTIFICATION_NUMBER
    )

    detail_html = client.get(
        reverse("experiences:detail", args=[review]),
    ).content.decode()
    assert "3 submissions" in detail_html

    client.force_login(workspaces.admin)
    admin_html = client.get(
        reverse("ohc:application-detail", args=[review]),
    ).content.decode()
    assert "Previous versions" in admin_html
    assert "iso-certificate.pdf" in admin_html
    assert "iso-scope.pdf" in admin_html
    assert "control-map-2.pdf" in admin_html

    attachment = current.attachments.get(original_name="iso-certificate.pdf")
    download = client.get(
        reverse("experiences:attachment", args=[review, attachment.pk]),
    )
    assert download.status_code == HTTPStatus.OK
    assert download["Content-Disposition"].endswith('filename="iso-certificate.pdf"')
    download.close()


def test_attachment_download_is_scoped_to_the_application(client, workspaces):
    application = workspaces.review
    attachment = application.submissions.get(
        form_key="compliance",
    ).attachments.get()
    outsider = UserFactory(email="outsider@example.in")
    Membership.objects.create(
        organisation=OrganisationFactory(onboarded=True),
        user=outsider,
        role=Role.OWNER,
    )

    client.force_login(outsider)
    denied = client.get(
        reverse("experiences:attachment", args=[application.reference, attachment.pk]),
    )
    client.force_login(workspaces.applicant)
    wrong_application = client.get(
        reverse(
            "experiences:attachment",
            args=[workspaces.draft.reference, attachment.pk],
        ),
    )
    allowed = client.get(
        reverse("experiences:attachment", args=[application.reference, attachment.pk]),
    )

    assert denied.status_code == HTTPStatus.NOT_FOUND
    assert wrong_application.status_code == HTTPStatus.NOT_FOUND
    assert allowed.status_code == HTTPStatus.OK
    allowed.close()


def test_htmx_edit_appends_and_removes_files_in_existing_submission(client, workspaces):
    client.force_login(workspaces.applicant)
    review = workspaces.review.reference
    application = workspaces.review
    previous = application.submissions.get(form_key="certification", is_current=True)
    removed = previous.attachments.filter(
        field_key="supporting_documents",
        is_current=True,
    ).first()
    assert removed is not None
    data = renewed_certification_data()
    data.update(
        {
            "submission_mode": "edit",
            "certificate_number": "CERT-2026-118-EDITED",
            "certificate_documents": [pdf("additional-scope.pdf")],
            "remove_files__supporting_documents": str(removed.pk),
        },
    )
    data.pop("supporting_documents")
    url = reverse("experiences:form", args=[review, "certification"])

    response = client.post(url, data, headers=HTMX_HEADERS)

    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse("experiences:detail", args=[review])
    previous.refresh_from_db()
    current = application.submissions.get(form_key="certification", is_current=True)
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


def test_admin_dashboard_review_and_decision_form_render(client, workspaces):
    client.force_login(workspaces.admin)
    review = workspaces.review.reference

    list_response = client.get(reverse("ohc:applications"))
    detail_response = client.get(reverse("ohc:application-detail", args=[review]))
    approve_response = client.get(
        reverse("ohc:application-action", args=[review, "approve"]),
    )

    assert list_response.status_code == HTTPStatus.OK
    assert review in list_response.content.decode()
    assert detail_response.status_code == HTTPStatus.OK
    detail_html = detail_response.content.decode()
    assert "Submitted application pack" in detail_html
    assert "Verify evidence" in detail_html
    assert "100%" in detail_html
    assert "Decision blockers" not in detail_html
    assert approve_response.status_code == HTTPStatus.OK
    approve_html = approve_response.content.decode()
    assert "Client ID" in approve_html
    assert 'id="application-action"' in approve_html
    assert 'hx-post="' in approve_html


def test_htmx_admin_filter_returns_only_application_results(client, workspaces):
    client.force_login(workspaces.admin)

    response = client.get(
        reverse("ohc:applications"),
        {"status": "under_review"},
        headers=HTMX_HEADERS,
    )
    html = response.content.decode()

    assert 'id="application-results"' in html
    assert 'id="ohc-nav"' not in html
    assert workspaces.review.reference in html
    assert workspaces.draft.reference not in html


def test_pending_query_filter_and_highlight_for_admin(client, workspaces):
    client.force_login(workspaces.admin)

    pending = client.get(
        reverse("ohc:applications"),
        {"query_state": "pending"},
        headers=HTMX_HEADERS,
    )
    pending_html = pending.content.decode()

    assert pending.status_code == HTTPStatus.OK
    assert workspaces.draft.reference in pending_html
    assert workspaces.review.reference not in pending_html
    assert "1 pending" in pending_html
    assert "bg-orange-50/70" in pending_html

    clear_html = client.get(
        reverse("ohc:applications"),
        {"query_state": "clear"},
        headers=HTMX_HEADERS,
    ).content.decode()
    assert workspaces.review.reference in clear_html
    assert workspaces.draft.reference not in clear_html


def test_applicant_cannot_use_admin_console_or_approve(client, workspaces):
    client.force_login(workspaces.applicant)
    review = workspaces.review.reference

    console_response = client.get(reverse("ohc:applications"))
    approve_response = client.get(
        reverse("experiences:action", args=[review, "approve"]),
    )
    form_action_response = client.get(
        reverse(
            "experiences:form-action",
            args=[review, "compliance", "verify_evidence"],
        ),
    )
    draft_html = client.get(
        reverse("experiences:detail", args=[workspaces.draft.reference]),
    ).content.decode()

    assert console_response.status_code == HTTPStatus.FORBIDDEN
    assert approve_response.status_code == HTTPStatus.FORBIDDEN
    assert form_action_response.status_code == HTTPStatus.FORBIDDEN
    assert "Withdraw" not in draft_html


def test_other_organisations_cannot_see_the_application(client, workspaces):
    outsider = UserFactory(email="outsider@example.in")
    Membership.objects.create(
        organisation=OrganisationFactory(onboarded=True),
        user=outsider,
        role=Role.OWNER,
    )
    client.force_login(outsider)

    response = client.get(
        reverse("experiences:detail", args=[workspaces.review.reference]),
    )

    assert response.status_code == HTTPStatus.NOT_FOUND


def test_admin_can_run_completed_form_action_with_htmx(client, workspaces):
    client.force_login(workspaces.admin)
    review = workspaces.review.reference
    url = reverse(
        "ohc:application-form-action",
        args=[review, "compliance", "verify_evidence"],
    )

    workspace = client.get(url)
    response = client.post(url, headers=HTMX_HEADERS)
    submission = ApplicationFormSubmission.objects.get(
        application__reference=review,
        form_key="compliance",
    )

    assert workspace.status_code == HTTPStatus.OK
    assert "Verify evidence" in workspace.content.decode()
    assert 'id="application-action"' in workspace.content.decode()
    assert response.status_code == HTTPStatus.OK
    assert response["HX-Redirect"] == reverse("ohc:application-detail", args=[review])
    assert submission.metadata["verified_revision"] == submission.revision

    detail_html = client.get(
        reverse("ohc:application-detail", args=[review]),
    ).content.decode()
    assert "Evidence verified" in detail_html
    assert "Verify evidence" not in detail_html


def test_admin_can_raise_a_query_from_the_review_workspace(client, workspaces):
    client.force_login(workspaces.admin)
    review = workspaces.review.reference
    url = reverse("ohc:application-action", args=[review, "raise_query"])

    response = client.post(
        url,
        {
            "subject": "Confirm the production host scope",
            "related_form": "compliance",
            "message": "Please point us to the section covering this host.",
            "due_at": "",
        },
    )

    assert response.status_code == HTTPStatus.FOUND
    application = ApplicationInstance.objects.get(reference=review)
    assert application.status == "changes_requested"
    assert (
        application.query_threads.get().subject == "Confirm the production host scope"
    )


def test_htmx_query_reply_and_resolution_swap_the_workspace(client, workspaces):
    client.force_login(workspaces.admin)
    review = workspaces.review.reference
    client.post(
        reverse("ohc:application-action", args=[review, "raise_query"]),
        {
            "subject": "Confirm infrastructure scope",
            "related_form": "readiness",
            "message": "Please confirm the listed production hosts.",
            "due_at": "",
        },
    )
    application = ApplicationInstance.objects.get(reference=review)
    thread = application.query_threads.get()
    query_url = reverse("ohc:application-query", args=[review, thread.pk])

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

    client.force_login(workspaces.applicant)
    applicant_view = client.get(reverse("experiences:query", args=[review, thread.pk]))
    assert applicant_view.status_code == HTTPStatus.OK
    assert (
        "Please confirm the listed production hosts." in applicant_view.content.decode()
    )

    client.force_login(workspaces.admin)
    resolve = client.post(
        reverse("ohc:application-query-resolve", args=[review, thread.pk]),
        headers=HTMX_HEADERS,
    )
    resolve_html = resolve.content.decode()
    thread.refresh_from_db()

    assert resolve.status_code == HTTPStatus.OK
    assert thread.status == "resolved"
    assert "This query is resolved" in resolve_html
    assert 'hx-swap-oob="innerHTML"' in resolve_html


def test_htmx_access_update_and_removal_swap_the_access_workspace(client, workspaces):
    client.force_login(workspaces.applicant)
    draft = workspaces.draft.reference
    contributor = workspaces.contributor
    url = reverse("experiences:access", args=[draft])

    response = client.post(
        url,
        {"user": contributor.pk, "role": "applicant_viewer", "direct_permissions": []},
        headers=HTMX_HEADERS,
    )
    html = response.content.decode()

    assert response.status_code == HTTPStatus.OK
    assert 'id="access-workspace"' in html
    assert 'id="flash-messages"' in html
    assert 'hx-swap-oob="innerHTML"' in html
    assert "Applicant viewer" in html
    assert ADMIN_EMAIL not in html
    assert workspaces.draft.access_grants.filter(user=contributor).exists()

    removal = client.post(
        reverse("experiences:access-remove", args=[draft, contributor.pk]),
        headers=HTMX_HEADERS,
    )

    assert removal.status_code == HTTPStatus.OK
    assert 'id="access-workspace"' in removal.content.decode()
    assert not workspaces.draft.access_grants.filter(user=contributor).exists()
