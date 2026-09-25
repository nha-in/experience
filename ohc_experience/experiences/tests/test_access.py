# ruff: noqa: F811, PLR2004
import re
from datetime import timedelta

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences import permissions
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import FormAttachment
from ohc_experience.experiences.models import TicketAttachment
from ohc_experience.support.models import Ticket
from ohc_experience.support.models import post_reply
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def grant(user, area="review", category="UHI", *, write=False, approve=False):
    return AccessGrant.objects.create(
        user=user,
        program="abdm",
        area=area,
        category=category,
        can_read=True,
        can_write=write,
        can_approve=approve,
    )


@pytest.fixture
def staff():
    return UserFactory(is_nha_team=True, is_staff=True)


def test_staff_identity_and_model_permissions_grant_no_portal_access(
    staff,
    client,
    environment,
):
    staff.user_permissions.add(*Permission.objects.all())
    client.force_login(staff)
    for route in [
        "assess-dashboard",
        "queue",
        "support",
        "events",
        "home",
        "production-list",
        "production-export",
    ]:
        assert client.get(reverse(f"experiences:{route}")).status_code == 403
    for name in [
        "experiences_accessgrant",
        "experiences_formsubmission",
        "users_user",
        "events_and_activities_event",
    ]:
        assert client.get(reverse(f"admin:{name}_changelist")).status_code == 403
    assert not permissions.visible_reviews(staff).exists()
    assert not permissions.visible_submissions(staff).exists()


def test_review_category_filters_lists_counts_details_downloads_and_history(
    environment,
    staff,
    client,
):
    approve(environment)
    hicm = milestone(environment, "m1")
    # PHR, because P1 is the one milestone that is neither shared with another
    # track, gated behind one, nor recorded without a decision.
    locker = submit(environment, "p1")
    grant(staff, category="PHR")
    client.force_login(staff)
    response = client.get(reverse("experiences:queue"), HTTP_HX_REQUEST="true")
    assert len(response.context["page"]) == 1
    assert response.context["page"][0].reviews == [locker]
    assert response.context["page"][0].matching_reviews == [locker]
    assert [track.code for track in response.context["track_choices"]] == [
        "PHR",
    ]
    response = client.get(reverse("experiences:assess-dashboard"))
    assert response.context["ready_count"] == 1
    assert response.context["approved_month"] == 0
    assert b'id="nav-support"' not in response.content
    assert b'id="nav-events"' not in response.content
    assert client.get(hicm.get_absolute_url()).status_code == 404
    assert client.get(locker.get_absolute_url()).status_code == 200
    assert (
        client.get(
            reverse(
                "experiences:track",
                args=[environment["workspace"].reference, "ABDM"],
            ),
        ).status_code
        == 404
    )
    for item, expected in [(hicm, 404), (locker, 200)]:
        upload = FormAttachment.objects.filter(
            submission=item.selected_submission,
        ).first()
        response = client.get(reverse("experiences:attachment", args=[upload.pk]))
        assert response.status_code == expected
        if response.streaming:
            assert b"".join(response.streaming_content)
        assert (
            client.get(
                reverse(
                    "experiences:submission",
                    args=[locker.pk, item.selected_submission_id],
                ),
            ).status_code
            == expected
        )
    registration = environment["workspace"].product.review_items.get(
        kind="product_registration",
    )
    assert client.get(registration.get_absolute_url()).status_code == 404


def test_nhcx_grant_does_not_allow_uhi_or_hicm(environment, staff, client):
    approve(environment)
    approve(environment, "m2")
    uhi = submit(environment, "uhi1")
    grant(staff, category="NHCX", write=True, approve=True)
    client.force_login(staff)
    assert not permissions.visible_reviews(staff).exists()
    assert client.get(uhi.get_absolute_url()).status_code == 404
    with pytest.raises(ValidationError, match="category"):
        workflows.assign_review(uhi, environment["admin"], staff)
    # Even a stale assignment never grants category access.
    uhi.assignee = staff
    uhi.save(update_fields=["assignee"])
    with pytest.raises(PermissionDenied):
        workflows.decide(uhi, staff, action="approve")


def test_review_write_and_approve_are_independent(environment, staff, client):
    approve(environment)
    item = submit(environment, "p1")
    access = grant(staff, category="PHR", write=True)
    workflows.assign_review(item, environment["admin"], staff)
    client.force_login(staff)
    page = client.get(item.get_absolute_url())
    assert b'value="query"' in page.content
    assert b'value="approve"' not in page.content
    assert b'value="reject"' not in page.content
    for action in ["approve", "reject"]:
        assert (
            client.post(
                item.get_absolute_url(),
                {"action": action, "note": "No"},
            ).status_code
            == 403
        )
    assert (
        client.post(
            item.get_absolute_url(),
            {"action": "query", "note": "Clarify scope"},
        ).status_code
        == 302
    )
    query = item.queries.get()
    workflows.reply_query(query, environment["applicant"], "Confirmed")
    workflows.resolve_query(query, staff)
    access.can_write = False
    access.can_approve = True
    access.save()
    page = client.get(item.get_absolute_url())
    assert b'value="approve"' in page.content
    assert b'value="query"' not in page.content
    assert b"field=scope#decision" not in page.content
    assert (
        client.post(
            item.get_absolute_url(),
            {"action": "query", "note": "Clarify"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            item.get_absolute_url(),
            {"action": "approve", "note": "Evidence accepted."},
        ).status_code
        == 302
    )


def test_an_override_needs_approve_rights_not_write(environment, staff, client):
    """Overriding a recorded request is an approval; writing alone never offers it."""
    submit(environment)
    uhi = submit(environment, "uhi1")
    access = grant(staff, category="UHI", write=True)
    client.force_login(staff)
    override = {"action": "approve", "note": "Approving ahead of M1 for a pilot."}

    page = client.get(uhi.get_absolute_url())
    assert b"Approve and override prerequisites" not in page.content
    assert client.post(uhi.get_absolute_url(), override).status_code == 403

    access.can_write, access.can_approve = False, True
    access.save()

    page = client.get(uhi.get_absolute_url())
    assert b"Approve and override prerequisites" in page.content
    assert client.post(uhi.get_absolute_url(), override).status_code == 302
    uhi.refresh_from_db()
    assert uhi.decided_by == staff


def test_read_only_assignment_and_revocation(environment, staff, client):
    approve(environment)
    approve(environment, "m2")
    item = submit(environment, "uhi1")
    access = grant(staff)
    with pytest.raises(ValidationError):
        workflows.assign_review(item, environment["admin"], staff)
    client.force_login(staff)
    assert b"data-decision-form" not in client.get(item.get_absolute_url()).content
    access.can_approve = True
    access.save()
    workflows.assign_review(item, environment["admin"], staff)
    access.delete()
    with pytest.raises(PermissionDenied):
        workflows.decide(item, staff, action="approve")
    assert client.get(item.get_absolute_url()).status_code == 403


@pytest.fixture
def tickets(environment):
    result = {}
    for category in ["NHCX", "UHI", "ABDM", ""]:
        ticket = Ticket.objects.create(
            organisation=environment["org"],
            product=environment["workspace"].product,
            category=category,
            created_by=environment["applicant"],
            subject=f"{category or 'General'} support request",
        )
        message = post_reply(
            ticket,
            environment["applicant"],
            "Help",
            from_nha_team=False,
        )
        TicketAttachment.objects.create(
            message=message,
            original_name="evidence.pdf",
            file=SimpleUploadedFile(
                "evidence.pdf",
                b"%PDF-1.4\n%%EOF",
                content_type="application/pdf",
            ),
        )
        result[category] = ticket
    return result


@pytest.mark.parametrize("category", ["NHCX", "UHI", "ABDM"])
def test_support_is_separate_and_category_scoped(tickets, category, staff, client):
    grant(staff, area="support", category=category)
    client.force_login(staff)
    response = client.get(reverse("experiences:support"))
    assert list(response.context["tickets"]) == [tickets[category]]
    assert response.context["ticket_total"] == 1
    assert response.context["nav_ticket_count"] == 1
    assert b'id="nav-queue"' not in response.content
    assert b'id="nav-events"' not in response.content
    assert client.get(reverse("experiences:home"))["Location"] == reverse(
        "experiences:support",
    )
    assert client.get(reverse("experiences:queue")).status_code == 403
    for track, ticket in tickets.items():
        expected = 200 if track == category else 404
        url = reverse("experiences:ticket", args=[ticket.reference])
        page = client.get(url)
        assert page.status_code == expected
        assert b'id="reply-form"' not in page.content
        upload = TicketAttachment.objects.get(message__ticket=ticket)
        response = client.get(
            reverse("experiences:ticket-attachment", args=[upload.pk]),
        )
        assert response.status_code == expected
        if response.streaming:
            assert b"".join(response.streaming_content)


def test_uploads_preview_in_the_browser_and_still_download(
    environment,
    tickets,
    client,
):
    upload = submit(environment).selected_submission.attachments.first()
    reply = TicketAttachment.objects.get(message__ticket=tickets["UHI"])
    client.force_login(environment["applicant"])
    for route, file in [("attachment", upload), ("ticket-attachment", reply)]:
        download = client.get(reverse(f"experiences:{route}", args=[file.pk]))
        assert download["Content-Disposition"].startswith("attachment;")
        preview_url = reverse(
            f"experiences:{route}-preview",
            args=[file.pk, file.original_name],
        )
        assert preview_url.endswith(f"/preview/{file.original_name}")
        preview = client.get(preview_url)
        assert preview["Content-Type"] == "application/pdf"
        assert preview["Content-Disposition"] == (
            f'inline; filename="{file.original_name}"'
        )
        assert preview["X-Content-Type-Options"] == "nosniff"
        assert b"".join(preview.streaming_content).startswith(b"%PDF-")
    page = client.get(
        reverse("experiences:ticket", args=[tickets["UHI"].reference]),
    ).content.decode()
    preview_url = reverse(
        "experiences:ticket-attachment-preview",
        args=[reply.pk, reply.original_name],
    )
    download_url = reverse("experiences:ticket-attachment", args=[reply.pk])
    assert re.search(rf'href="{re.escape(preview_url)}"\s+target="_blank"', page)
    assert f'href="{download_url}"' in page
    # Earlier organisation logos were images. Anything a browser could run
    # script from downloads even from a preview link.
    for name, disposition, content_type in [
        ("logo.PNG", "inline", "image/png"),
        ("logo.svg", "attachment", "image/svg+xml"),
        ("notes.html", "attachment", "text/html"),
    ]:
        upload.original_name = name
        upload.save(update_fields=["original_name"])
        response = client.get(
            reverse("experiences:attachment-preview", args=[upload.pk, name]),
        )
        assert response["Content-Disposition"] == f'{disposition}; filename="{name}"'
        assert response["Content-Type"].startswith(content_type)
    client.force_login(environment["outsider"])
    for route, file in [("attachment", upload), ("ticket-attachment", reply)]:
        response = client.get(
            reverse(
                f"experiences:{route}-preview",
                args=[file.pk, file.original_name],
            ),
        )
        assert response.status_code == 404


def test_support_reply_and_close_have_distinct_permissions(tickets, staff, client):
    access = grant(staff, area="support", category="NHCX", write=True)
    url = reverse("experiences:ticket", args=[tickets["NHCX"].reference])
    client.force_login(staff)
    page = client.get(url)
    assert b"Send reply" in page.content
    assert b"Mark as resolved" not in page.content
    assert client.post(url, {"body": "Please retry"}).status_code == 302
    resolution = {"intent": "close", "body": "Retried and it works."}
    assert client.post(url, resolution).status_code == 403
    access.can_write = False
    access.can_approve = True
    access.save()
    page = client.get(url)
    assert b"Send reply" not in page.content
    assert b"Mark as resolved" in page.content
    assert client.post(url, {"body": "Please retry"}).status_code == 403
    assert client.post(url, resolution).status_code == 302


@pytest.fixture
def event(staff):
    return Event.objects.create(
        title="UHI workshop",
        category="UHI",
        created_by=staff,
        starts_at=timezone.now() + timedelta(days=2),
    )


def test_event_creation_and_publication_are_separate(staff, client, event):
    access = grant(staff, area="events", write=True)
    client.force_login(staff)
    add = reverse("admin:events_and_activities_event_add")
    listing = reverse("admin:events_and_activities_event_changelist")
    assert client.get(add).status_code == 200
    assert b"publish_events" not in client.get(listing).content
    fields = {
        "title": "Another UHI event",
        "slug": "another-uhi-event",
        "program": "abdm",
        "category": "UHI",
        "kind": "workshop",
        "starts_at_0": "2027-01-01",
        "starts_at_1": "10:00:00",
        "published_at_0": "2026-01-01",
        "published_at_1": "10:00:00",
    }
    assert client.post(add, {**fields, "category": "NHCX"}).status_code == 200
    assert not Event.objects.filter(slug=fields["slug"]).exists()
    assert client.post(add, fields).status_code == 302
    assert Event.objects.get(slug=fields["slug"]).published_at is None
    assert client.get(reverse("experiences:queue")).status_code == 403
    access.can_write = False
    access.can_approve = True
    access.save()
    assert client.get(add).status_code == 403
    assert (
        client.post(
            listing,
            {"action": "publish_events", "_selected_action": [event.pk]},
        ).status_code
        == 302
    )
    event.refresh_from_db()
    assert event.is_published
    access.can_write = True
    access.can_approve = False
    access.save()
    change = reverse("admin:events_and_activities_event_change", args=[event.pk])
    assert client.post(change, fields).status_code == 403


def test_event_listing_and_actions_do_not_cross_categories(staff, client, event, rf):
    grant(staff, area="events", category="NHCX", write=True, approve=True)
    event.publish()
    event.save()
    own = Event.objects.create(
        title="NHCX workshop",
        category="NHCX",
        starts_at=event.starts_at,
        published_at=timezone.now(),
    )
    client.force_login(staff)
    page = client.get(reverse("experiences:events"))
    assert list(page.context["events"]) == [own]
    assert page.context["nav_event_count"] == 1
    assert (
        client.post(reverse("experiences:event-detail", args=[event.pk])).status_code
        == 404
    )
    request = rf.post("/")
    request.user = staff
    with pytest.raises(PermissionDenied):
        admin.site.get_model_admin(Event).set_publication(
            request,
            Event.objects.filter(pk=event.pk),
            publish=False,
        )
    event.refresh_from_db()
    assert event.is_published


def test_only_superusers_can_assign_permissions(staff, client, admin_client):
    grant(staff, area="events", write=True, approve=True)
    staff.user_permissions.add(*Permission.objects.all())
    client.force_login(staff)
    assert (
        client.post(
            reverse("admin:experiences_accessgrant_add"),
            {
                "user": staff.pk,
                "program": "abdm",
                "area": "review",
                "category": "*",
                "can_read": "on",
                "can_approve": "on",
            },
        ).status_code
        == 403
    )
    assert not permissions.has_area(staff, "review")
    response = admin_client.post(
        reverse("admin:experiences_accessgrant_add"),
        {
            "user": staff.pk,
            "program": "abdm",
            "area": "review",
            "category": "NHCX",
            "can_read": "on",
            "can_approve": "on",
        },
    )
    assert response.status_code == 302
    assert permissions.has_access(staff, "review", "NHCX", "approve")
    assert not permissions.has_access(staff, "review", "UHI")
    response = admin_client.get(reverse("admin:users_user_change", args=[staff.pk]))
    assert b"Portal permissions" in response.content


def test_invalid_grants_and_inactive_accounts_are_denied(staff):
    access = grant(staff, write=True)
    access.can_read = False
    with pytest.raises(ValidationError):
        access.full_clean()
    access.can_read = True
    access.category = "unknown"
    with pytest.raises(ValidationError):
        access.full_clean()
    staff.is_active = False
    staff.is_superuser = True
    staff.save()
    assert not permissions.has_area(staff, "review")
    assert not permissions.visible_reviews(staff).exists()
    assert not permissions.visible_events(staff).exists()


def test_general_and_all_categories_are_explicit(environment, staff):
    general = grant(staff, category="")
    assert set(permissions.visible_reviews(staff).values_list("kind", flat=True)) == {
        "organisation_verification",
        "product_registration",
    }
    general.category = "*"
    general.save()
    assert permissions.visible_reviews(staff).filter(kind="application").exists()
    assert not permissions.has_area(staff, "support")
    assert not permissions.has_area(staff, "events")


def test_reused_pins_remain_visible_without_exposing_source_history(environment, staff):
    """UHI reaches M1 and M2, so M3's evidence is only seen once M2 pins it."""
    submit(environment, "m1")
    source = submit(environment, "m3")
    original = source.selected_submission
    target = milestone(environment, "m2")
    grant(staff, category="UHI")
    assert not permissions.visible_submissions(staff).filter(pk=original.pk).exists()
    workflows.reuse_evidence(target, environment["applicant"])
    target.refresh_from_db()
    assert target.selected_submission_id == original.pk
    assert permissions.visible_submissions(staff).filter(pk=original.pk).exists()
    # Replacing a reused pin must not break links in this application's history.
    target = submit(environment, "m2")
    assert target.selected_submission_id != original.pk
    assert permissions.visible_submissions(staff).filter(pk=original.pk).exists()
    workflows.withdraw(source, environment["applicant"])
    revised_source = submit(environment, "m3")
    assert (
        not permissions.visible_submissions(staff)
        .filter(pk=revised_source.selected_submission_id)
        .exists()
    )
