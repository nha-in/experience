# ruff: noqa: F811, PLR2004
import pytest
from django.urls import reverse

from ohc_experience.abdm.demo import uhi_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def uhi_url(environment):
    return reverse(
        "experiences:track",
        args=[environment["workspace"].reference, "UHI"],
    )


def uhi_reviewer(client):
    staff = UserFactory(is_nha_team=True, is_staff=True)
    AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="review",
        category="UHI",
        can_read=True,
    )
    client.force_login(staff)
    return staff


def test_uhi_defaults_to_next_open_application_and_respects_explicit_milestone(
    environment,
    client,
):
    client.force_login(environment["applicant"])
    url = uhi_url(environment)
    page = client.get(url)
    assert page.context["tile"]["definition"].key == "m1"
    locked = client.get(url, {"milestone": "uhi1"})
    assert locked.context["locked"]
    assert b'name="uhi_role"' not in locked.content

    approve(environment)

    page = client.get(url)
    assert page.context["tile"]["definition"].key == "uhi1"
    assert b'name="uhi_role"' in page.content
    assert b'name="wasa_certificate"' not in page.content
    explicit = client.get(url, {"milestone": "m1"})
    assert explicit.context["tile"]["definition"].key == "m1"


def test_recorded_uhi_answers_remain_editable_after_invalid_submission(
    environment,
    client,
):
    approve(environment)
    item = submit(environment, "uhi1")
    client.force_login(environment["applicant"])
    url = f"{uhi_url(environment)}?milestone=uhi1"
    page = client.get(url)
    assert page.context["can_edit"]
    assert b'name="uhi_role"' in page.content
    assert b"Participation recorded" in page.content
    assert b"Update participation" in page.content
    assert b'value="draft"' not in page.content
    assert b"Submitting sends this milestone for review" not in page.content

    invalid = client.post(
        url,
        {"intent": "submit", "revision": item.selected_submission_id},
    )
    assert invalid.status_code == 200
    assert invalid.context["can_edit"]
    assert "uhi_role" in invalid.context["form"].errors
    assert b'name="uhi_role"' in invalid.content
    assert b"Update participation" in invalid.content
    assert b'value="draft"' not in invalid.content

    saved = client.post(
        url,
        {
            **uhi_data(),
            "uhi_role": ["hspa"],
            "intent": "submit",
            "revision": item.selected_submission_id,
        },
    )
    assert saved.status_code == 302
    item.refresh_from_db()
    assert item.status == "approved"
    assert item.selected_submission.data["uhi_role"] == ["hspa"]


def test_draft_post_cannot_reopen_recorded_uhi_participation(environment, client):
    approve(environment)
    item = submit(environment, "uhi1")
    client.force_login(environment["applicant"])
    snapshot_id = item.selected_submission_id
    snapshot_count = item.form.submissions.count()
    previous_data = item.selected_submission.data
    decision_time = item.decided_at
    application_decision_time = item.application.decided_at
    event_count = item.history.count()

    page = client.post(
        f"{uhi_url(environment)}?milestone=uhi1",
        {
            **uhi_data(),
            "uhi_role": ["hspa"],
            "intent": "draft",
            "revision": snapshot_id,
        },
    )

    assert page.status_code == 200
    assert (
        b"Submit your updated answers to keep participation recorded." in page.content
    )
    assert b"Update participation" in page.content
    assert b'value="draft"' not in page.content
    item.refresh_from_db()
    assert item.status == item.application.status == "approved"
    assert item.selected_submission_id == snapshot_id
    assert item.selected_submission.data == previous_data
    assert item.form.submissions.count() == snapshot_count
    assert item.decided_at == decision_time
    assert item.application.decided_at == application_decision_time
    assert item.history.count() == event_count


def test_uhi_history_keeps_original_form_title_after_form_upgrade(
    environment,
    client,
):
    m1 = approve(environment)
    item = milestone(environment, "uhi1")
    old = FormSubmission.objects.create(
        form=m1.form,
        origin_application=item.application,
        data={"legacy_note": "Original exit evidence"},
        submitted_by=environment["applicant"],
        submission_number=2,
        is_current=False,
    )
    uhi_reviewer(client)

    page = client.get(
        reverse("experiences:submission", args=[item.pk, old.pk]),
    )

    assert page.status_code == 200
    assert page.context["snapshot"].pk == old.pk
    assert page.context["page_title"].startswith(m1.form.name)
    assert f'<h1 class="ui-hero-title">{m1.form.name}</h1>' in page.content.decode()


def test_upgrade_audit_pin_grants_only_the_exact_historical_submission(
    environment,
    client,
):
    approve(environment)
    legacy = submit(environment, "locker1")
    item = milestone(environment, "uhi1")
    old = legacy.selected_submission
    unrelated = FormSubmission.objects.create(
        form=old.form,
        origin_application=legacy.application,
        data={"legacy_note": "Different private revision"},
        submitted_by=environment["applicant"],
        submission_number=99,
        is_current=False,
    )
    uhi_reviewer(client)
    url = reverse("experiences:submission", args=[item.pk, old.pk])
    assert client.get(url).status_code == 404

    AuditEvent.objects.create(
        item=item,
        product=item.product,
        organisation=item.organisation,
        action="UHI participation form upgraded",
        detail={"submission_id": old.pk},
    )

    assert client.get(url).status_code == 200
    assert (
        client.get(
            reverse("experiences:submission", args=[item.pk, unrelated.pk]),
        ).status_code
        == 404
    )
    client.force_login(environment["outsider"])
    assert client.get(url).status_code == 404
