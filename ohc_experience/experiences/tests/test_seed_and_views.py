from __future__ import annotations

from http import HTTPStatus
from io import StringIO

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

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
    REVIEW_REFERENCE,
)
from ohc_experience.experiences.models import ApplicationAttachment
from ohc_experience.experiences.models import ApplicationFormSubmission
from ohc_experience.experiences.models import ApplicationInstance
from ohc_experience.experiences.models import ApplicationQueryMessage
from ohc_experience.experiences.models import ApplicationQueryThread
from ohc_experience.experiences.models import QueryStatus

pytestmark = pytest.mark.django_db

DRAFT_SUBMISSION_COUNT = 3
COMPLETE_SUBMISSION_COUNT = 7
DEMO_ATTACHMENT_COUNT = 3
DRAFT_REQUIRED_FORM_COUNT = 8
REVIEW_REQUIRED_FORM_COUNT = 7
DRAFT_PROGRESS_PERCENT = 38
COMPLETE_PROGRESS_PERCENT = 100
FOUR_OF_EIGHT_PERCENT = 50
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

    assert applicant.check_password(DEFAULT_PASSWORD)
    assert admin.check_password(DEFAULT_PASSWORD)
    assert admin.is_ohc_team is True
    assert admin.is_staff is True
    assert EmailAddress.objects.get(user=applicant).verified is True
    assert draft.submissions.count() == DRAFT_SUBMISSION_COUNT
    assert draft.metadata["required_forms"] == DRAFT_REQUIRED_FORM_COUNT
    assert draft.progress_percent == DRAFT_PROGRESS_PERCENT
    assert review.submissions.count() == COMPLETE_SUBMISSION_COUNT
    assert review.metadata["required_forms"] == REVIEW_REQUIRED_FORM_COUNT
    assert review.progress_percent == COMPLETE_PROGRESS_PERCENT
    assert (
        ApplicationAttachment.objects.filter(
            submission__application=review,
            is_current=True,
        ).count()
        == DEMO_ATTACHMENT_COUNT
    )
    assert review.access_grants.get(user=admin).role_key == "decision_maker"
    assert review.metadata["product_version"] == "3.2.0"
    assert review.metadata["milestones"] == ["m1", "m2", "m3"]
    applicant_query = draft.query_threads.get()
    assert applicant_query.status == QueryStatus.AWAITING_REVIEWER
    assert applicant_query.opened_by == applicant
    assert applicant_query.assigned_to is None
    assert APPLICANT_EMAIL in seeded_demo
    assert ADMIN_EMAIL in seeded_demo
    assert DEFAULT_PASSWORD in seeded_demo


def test_seeder_is_idempotent(seeded_demo):
    before = (
        ApplicationInstance.objects.count(),
        ApplicationAttachment.objects.count(),
        ApplicationQueryThread.objects.count(),
        ApplicationQueryMessage.objects.count(),
        get_user_model().objects.count(),
    )

    call_command("seed_experience_demo", stdout=StringIO())

    assert (
        ApplicationInstance.objects.count(),
        ApplicationAttachment.objects.count(),
        ApplicationQueryThread.objects.count(),
        ApplicationQueryMessage.objects.count(),
        get_user_model().objects.count(),
    ) == before


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

    assert list_response.status_code == HTTPStatus.OK
    list_html = list_response.content.decode()
    assert DRAFT_REFERENCE in list_html
    assert 'hx-target="#application-results"' in list_html
    assert detail_response.status_code == HTTPStatus.OK
    detail_html = detail_response.content.decode()
    assert "Application forms" in detail_html
    assert "3 of 8 currently required forms complete" in detail_html
    assert "Health locker operations" in detail_html
    assert "Technical readiness" in detail_html
    assert "Security and privacy" not in detail_html
    assert "Pending application queries" in detail_html
    assert "Ask review team" in detail_html
    assert form_response.status_code == HTTPStatus.OK
    form_html = form_response.content.decode()
    assert "Production gateway callback" in form_html
    assert 'id="application-form"' in form_html
    assert 'hx-encoding="multipart/form-data"' in form_html


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
    assert application.submissions.filter(form_key="technical_readiness").exists()
    assert application.progress_percent == FOUR_OF_EIGHT_PERCENT
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
    submission = ApplicationFormSubmission.objects.get(
        application__reference=REVIEW_REFERENCE,
        form_key="security_compliance",
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
