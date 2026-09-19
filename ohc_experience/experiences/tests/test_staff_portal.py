# ruff: noqa: PLR2004, S105, F811
import json
from datetime import timedelta

import pytest
from allauth.account.models import EmailAddress
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertContains
from pytest_django.asserts import assertNotContains

from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import submit
from ohc_experience.events_and_activities.models import Event
from ohc_experience.experiences import permissions
from ohc_experience.experiences import workflows
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import EventRegistration
from ohc_experience.experiences.staff_forms import StaffForm
from ohc_experience.experiences.staff_forms import staff_revision
from ohc_experience.experiences.staff_services import save_staff
from ohc_experience.experiences.staff_services import set_staff_active
from ohc_experience.organisations.models import Role
from ohc_experience.organisations.tests.factories import MembershipFactory
from ohc_experience.organisations.tests.factories import OrganisationFactory
from ohc_experience.support.models import Ticket
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
PASSWORD = "Portal-Staff-Example-9471"


@pytest.fixture
def superadmin():
    return UserFactory(is_superuser=True, is_staff=True)


@pytest.fixture
def staff():
    return UserFactory(is_nha_team=True, is_staff=False)


def payload(user=None, *, grants=()):
    data = {
        "name": user.name if user else "Test Staff Member",
        "email": user.email if user else "staff@example.test",
        "phone_number": "1234567890",
    }
    if user:
        data["revision"] = staff_revision(user)
    else:
        data.update(password1=PASSWORD, password2=PASSWORD)
    form = StaffForm(user=user)
    for area, category, actions in grants:
        row = next(
            row
            for row in form.permission_rows
            if row["key"] == ("abdm", area, category)
        )
        for action, cell in zip(
            ("read", "write", "approve"),
            row["cells"],
            strict=True,
        ):
            if action in actions:
                data[cell.name] = "on"
    return data


@pytest.mark.parametrize("actor_kind", ["applicant", "staff"])
def test_staff_management_is_superadmin_only(client, staff, actor_kind):
    actor = staff if actor_kind == "staff" else UserFactory()
    client.force_login(actor)
    for route, args in [
        ("staff-list", []),
        ("staff-create", []),
        ("staff-edit", [staff.pk]),
    ]:
        url = reverse(f"experiences:{route}", args=args)
        assert client.get(url).status_code == 403
        assert (
            client.post(url, payload(staff), HTTP_HX_REQUEST="true").status_code == 403
        )
    assert (
        client.post(
            reverse("experiences:staff-archive", args=[staff.pk]),
            {"intent": "archive"},
        ).status_code
        == 403
    )
    with pytest.raises(PermissionDenied):
        save_staff(actor, payload())
    with pytest.raises(PermissionDenied):
        set_staff_active(actor, staff.pk, active=False, revision=staff_revision(staff))


def test_superadmin_can_create_portal_only_staff_and_sign_in(client, superadmin):
    client.force_login(superadmin)
    url = reverse("experiences:staff-create")
    page = client.get(url)
    assert page.status_code == 200
    assert b"Portal permissions" in page.content
    assert b'href="/admin/' not in page.content
    data = payload(grants=[("review", "UHI", ["read", "write"])])
    # Privilege flags and arbitrary user ids are never accepted from the client.
    data.update(
        is_staff="on",
        is_superuser="on",
        is_active="off",
        user=str(superadmin.pk),
    )
    response = client.post(url, data, HTTP_HX_REQUEST="true")
    assert response.status_code == 302
    user = get_user_model().objects.get(email=data["email"])
    assert user.is_nha_team
    assert user.is_active
    assert not user.is_staff
    assert not user.is_superuser
    assert not user.memberships.exists()
    assert user.check_password(PASSWORD)
    assert EmailAddress.objects.get(user=user).verified
    assert permissions.has_access(user, "review", "UHI", "write")
    assert not permissions.has_access(user, "review", "UHI", "approve")
    assert not permissions.has_access(user, "review", "NHCX")
    assert not permissions.has_area(user, "support")
    login = Client()
    response = login.post(
        reverse("account_login"),
        {"login": user.email, "password": PASSWORD},
        follow=True,
    )
    assert response.status_code == 200
    assert response.wsgi_request.user.pk == user.pk
    assert b'id="nav-staff"' not in response.content
    assert login.get(reverse("admin:index")).status_code == 302
    assert PASSWORD not in LogEntry.objects.get(object_id=str(user.pk)).change_message


def test_edit_account_email_password_and_permissions_atomically(
    superadmin,
    staff,
    client,
):
    AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="review",
        category="*",
        can_read=True,
        can_approve=True,
    )
    EmailAddress.objects.create(
        user=staff,
        email=staff.email,
        primary=True,
        verified=True,
    )
    client.force_login(superadmin)
    page = client.get(reverse("experiences:staff-edit", args=[staff.pk]))
    assert page.status_code == 200
    wildcard = next(
        row
        for row in page.context["form"].permission_rows
        if row["key"] == ("abdm", "review", "*")
    )
    assert wildcard["cells"][2].value() is True
    data = payload(staff, grants=[("support", "nhcx-data", ["read", "approve"])])
    data.update(
        name="Updated Member",
        email="new-staff@example.test",
        password1=PASSWORD,
        password2=PASSWORD,
    )
    response = client.post(reverse("experiences:staff-edit", args=[staff.pk]), data)
    assert response.status_code == 302
    staff.refresh_from_db()
    assert staff.name == "Updated Member"
    assert staff.email == data["email"]
    assert staff.check_password(PASSWORD)
    assert list(
        EmailAddress.objects.filter(user=staff).values_list("email", flat=True),
    ) == [data["email"]]
    assert not permissions.has_area(staff, "review")
    assert permissions.has_access(staff, "support", "nhcx-data", "approve")
    # Support is granted by category, so its siblings under NHCX stay shut.
    assert not permissions.has_access(staff, "support", "nhcx-auth")
    audit = json.loads(LogEntry.objects.get(object_id=str(staff.pk)).change_message)
    assert audit[1]["portal"]["permissions_before"][0]["category"] == "*"


def test_wildcard_read_supplies_read_for_a_category_row(superadmin, staff, client):
    # The editor locks the read column under a wildcard read tick, and a locked
    # box sends no value. The category row must still save its approve access.
    client.force_login(superadmin)
    data = payload(
        staff,
        grants=[("review", "*", ["read"]), ("review", "HIE-CM", ["approve"])],
    )
    response = client.post(reverse("experiences:staff-edit", args=[staff.pk]), data)
    assert response.status_code == 302
    assert permissions.has_access(staff, "review", "HIE-CM", "approve")
    assert permissions.has_access(staff, "review", "UHI")
    assert not permissions.has_access(staff, "review", "UHI", "approve")
    assert not permissions.has_area(staff, "support")
    saved = staff.experience_access.get(area="review", category="HIE-CM")
    assert saved.can_read
    assert not saved.can_write


def test_a_row_that_grants_nothing_is_not_saved(superadmin, staff):
    save_staff(
        superadmin,
        payload(staff, grants=[("events", "*", ["read"])]),
        pk=staff.pk,
    )
    assert [grant.category for grant in staff.experience_access.all()] == ["*"]


@pytest.mark.parametrize(
    "invalid",
    [
        "password",
        "mismatch",
        "duplicate",
        "email_address",
        "write_without_read",
        "unknown_permission",
    ],
)
def test_invalid_staff_forms_do_not_create_users_or_grants(superadmin, invalid):
    data = payload()
    if invalid == "password":
        data.update(password1="123", password2="123")
    elif invalid == "mismatch":
        data["password2"] = "different"
    elif invalid == "duplicate":
        UserFactory(email="STAFF@example.test")
    elif invalid == "email_address":
        EmailAddress.objects.create(
            user=UserFactory(),
            email="STAFF@example.test",
            verified=True,
        )
    elif invalid == "write_without_read":
        data = payload(grants=[("review", "UHI", ["write"])])
    else:
        data["access_bad_program_review_NHCX_approve"] = "on"
    count = get_user_model().objects.count()
    user, form = save_staff(superadmin, data)
    assert user is None
    assert form.errors
    assert get_user_model().objects.count() == count
    assert not AccessGrant.objects.exists()


def test_changing_a_staff_number_clears_its_verification(superadmin, staff):
    staff.phone_number = "+919876543210"
    staff.phone_verified = True
    staff.save()

    save_staff(
        superadmin,
        payload(staff) | {"phone_number": "+919876543210"},
        pk=staff.pk,
    )
    staff.refresh_from_db()
    assert staff.phone_verified

    save_staff(superadmin, payload(staff), pk=staff.pk)
    staff.refresh_from_db()
    assert staff.phone_number == "1234567890"
    assert not staff.phone_verified


def test_invalid_edit_preserves_existing_account_and_grants(superadmin, staff):
    grant = AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="review",
        category="UHI",
    )
    old_name = staff.name
    data = payload(staff, grants=[("events", "NHCX", ["approve"])])
    data["name"] = "Must not be saved"
    _, form = save_staff(superadmin, data, pk=staff.pk)
    assert form.errors
    staff.refresh_from_db()
    assert staff.name == old_name
    assert list(staff.experience_access.all()) == [grant]


def test_stale_edits_cannot_overwrite_new_grants(superadmin, staff, client):
    old = payload(staff)
    access = AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="review",
        category="UHI",
    )
    with pytest.raises(ValidationError, match="changed"):
        save_staff(superadmin, old, pk=staff.pk)
    client.force_login(superadmin)
    response = client.post(reverse("experiences:staff-edit", args=[staff.pk]), old)
    assert response.status_code == 200
    assert b"Reload before saving" in response.content
    assert AccessGrant.objects.filter(pk=access.pk).exists()


def test_archive_and_restore_revoke_sessions_but_retain_permissions(
    superadmin,
    staff,
    client,
):
    AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="review",
        category="UHI",
    )
    client.force_login(staff)
    old_session = client.session.session_key
    assert Session.objects.filter(pk=old_session).exists()
    archived = set_staff_active(
        superadmin,
        staff.pk,
        active=False,
        revision=staff_revision(staff),
    )
    assert not archived.is_active
    assert archived.experience_access.count() == 1
    assert not Session.objects.filter(pk=old_session).exists()
    assert not permissions.has_area(archived, "review")
    restored = set_staff_active(
        superadmin,
        staff.pk,
        active=True,
        revision=staff_revision(archived),
    )
    assert permissions.has_area(restored, "review")
    assert client.get(reverse("experiences:queue")).status_code == 302
    assert LogEntry.objects.filter(object_id=str(staff.pk)).count() == 2


def test_archive_is_post_only_and_csrf_protected(superadmin, staff, client):
    client.force_login(superadmin)
    url = reverse("experiences:staff-archive", args=[staff.pk])
    assert client.get(url).status_code == 405
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(superadmin)
    assert (
        csrf_client.post(
            url,
            {"intent": "archive", "revision": staff_revision(staff)},
        ).status_code
        == 403
    )
    assert (
        client.post(
            url,
            {"intent": "archive", "revision": staff_revision(staff)},
        ).status_code
        == 302
    )
    staff.refresh_from_db()
    assert not staff.is_active
    assert (
        client.post(
            url,
            {"intent": "restore", "revision": staff_revision(staff)},
        ).status_code
        == 302
    )
    staff.refresh_from_db()
    assert staff.is_active


def test_superadmins_and_applicants_cannot_be_modified_as_staff(superadmin, client):
    client.force_login(superadmin)
    for target in [
        superadmin,
        UserFactory(is_superuser=True, is_nha_team=True),
        UserFactory(),
    ]:
        assert (
            client.post(
                reverse("experiences:staff-edit", args=[target.pk]),
                payload(target),
            ).status_code
            == 404
        )
        assert (
            client.post(
                reverse("experiences:staff-archive", args=[target.pk]),
                {"intent": "archive"},
            ).status_code
            == 404
        )
        with pytest.raises(PermissionDenied):
            set_staff_active(
                superadmin,
                target.pk,
                active=False,
                revision=staff_revision(target),
            )


def test_staff_directory_search_filters_and_pagination(superadmin, client):
    UserFactory.create_batch(22, is_nha_team=True, name="Searchable")
    archived = UserFactory(is_nha_team=True, is_active=False, name="Archived Person")
    applicant = UserFactory(name="Applicant", is_nha_team=False)
    client.force_login(superadmin)
    url = reverse("experiences:staff-list")
    response = client.get(url, {"q": "Searchable"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert len(response.context["page"]) == 20
    assert response.context["counts"]["active"] == 22
    assert len(client.get(url, {"q": "Searchable", "page": 2}).context["page"]) == 2
    response = client.get(url, {"status": "archived"})
    assert list(response.context["page"]) == [archived]
    assert applicant.email.encode() not in response.content
    assert b'href="/admin/' not in response.content


def test_portal_only_event_staff_can_create_edit_and_publish_separately(staff, client):
    access = AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="events",
        category="UHI",
        can_write=True,
    )
    client.force_login(staff)
    create = reverse("experiences:event-create")
    page = client.get(create)
    assert page.status_code == 200
    assert b'href="/admin/' not in page.content
    data = {
        "title": "Portal event",
        "category": "UHI",
        "kind": "workshop",
        "starts_at": "2027-01-02T10:00",
        "published_at": "2027-01-01T10:00",
    }
    assert client.post(create, {**data, "category": "NHCX"}).status_code == 200
    assert not Event.objects.exists()
    assert client.post(create, data).status_code == 302
    event = Event.objects.get()
    assert event.created_by == staff
    assert not event.is_published
    publish = reverse("experiences:event-publication", args=[event.pk])
    assert client.post(publish, {"intent": "publish"}).status_code == 403
    access.can_approve = True
    access.can_write = False
    access.save()
    assert client.post(publish, {"intent": "publish"}).status_code == 302
    event.refresh_from_db()
    assert event.is_published
    assert client.get(create).status_code == 403
    access.can_write = True
    access.save()
    assert (
        client.post(
            reverse("experiences:event-edit", args=[event.pk]),
            data,
        ).status_code
        == 403
    )
    assert client.post(publish, {"intent": "unpublish"}).status_code == 302
    assert (
        client.post(
            reverse("experiences:event-edit", args=[event.pk]),
            {**data, "title": "Edited event"},
        ).status_code
        == 302
    )


def test_event_manager_cannot_access_other_categories(staff, client):
    AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="events",
        category="NHCX",
        can_write=True,
        can_approve=True,
    )
    event = Event.objects.create(
        title="Other category",
        category="UHI",
        starts_at=timezone.now() + timedelta(days=3),
    )
    client.force_login(staff)
    response = client.get(reverse("experiences:event-manage"))
    assert event not in response.context["page"]
    assert (
        client.get(reverse("experiences:event-edit", args=[event.pk])).status_code
        == 404
    )
    assert (
        client.post(
            reverse("experiences:event-publication", args=[event.pk]),
            {"intent": "publish"},
        ).status_code
        == 404
    )
    assert (
        client.post(reverse("experiences:event-delete", args=[event.pk])).status_code
        == 404
    )


def test_event_editors_delete_drafts_but_not_published_events(staff, client):
    access = AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="events",
        category="UHI",
        can_write=True,
        can_approve=True,
    )
    starts_at = timezone.now() + timedelta(days=3)
    published = Event.objects.create(
        title="Published event",
        category="UHI",
        starts_at=starts_at,
        published_at=timezone.now(),
    )
    draft = Event.objects.create(title="Draft", category="UHI", starts_at=starts_at)
    EventRegistration.objects.create(event=draft, user=UserFactory())
    delete_draft = reverse("experiences:event-delete", args=[draft.pk])
    delete_published = reverse("experiences:event-delete", args=[published.pk])
    client.force_login(staff)
    page = client.get(reverse("experiences:event-manage"))
    # The intent must not depend on the button keeping focus through the confirm.
    assertContains(
        page,
        '<input type="hidden" name="intent" value="unpublish">',
        html=True,
    )
    assertContains(page, f'action="{delete_draft}"')
    assertContains(page, "Delete this event and its 1 registration? This cannot")
    assertNotContains(page, f'action="{delete_published}"')
    assert client.get(delete_draft).status_code == 405
    assert client.post(delete_published).status_code == 403
    access.can_write = False
    access.save()
    assert client.post(delete_draft).status_code == 403
    access.can_write = True
    access.save()
    assert client.post(delete_draft).status_code == 302
    assert list(Event.objects.all()) == [published]
    assert not EventRegistration.objects.exists()
    assert LogEntry.objects.get(change_message="Event deleted").is_deletion()


def test_superadmins_can_delete_published_events(superadmin, client):
    event = Event.objects.create(
        title="Published event",
        starts_at=timezone.now() + timedelta(days=3),
        published_at=timezone.now(),
    )
    client.force_login(superadmin)
    assertContains(
        client.get(reverse("experiences:event-manage")),
        "Delete this event? Integrators will no longer see it. This cannot be undone.",
    )
    assert (
        client.post(reverse("experiences:event-delete", args=[event.pk])).status_code
        == 302
    )
    assert not Event.objects.exists()


def test_event_participants_lists_registrations_and_filters_them(staff, client):
    AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="events",
        category="UHI",
        can_read=True,
    )
    event = Event.objects.create(
        title="Draft workshop",
        category="UHI",
        starts_at=timezone.now() + timedelta(days=3),
    )
    meera = UserFactory(name="Meera Nair", email="meera@integrator.test")
    MembershipFactory(
        user=meera,
        organisation=OrganisationFactory(name="Wellspring Health"),
        role=Role.OWNER,
    )
    EventRegistration.objects.create(event=event, user=meera)
    EventRegistration.objects.create(
        event=event,
        user=UserFactory(name="Rahul Das", email="rahul@other.test"),
    )
    participants = reverse("experiences:event-participants", args=[event.pk])
    client.force_login(staff)
    assertContains(client.get(reverse("experiences:event-manage")), participants)
    page = client.get(participants)
    assertContains(page, "2 participants")
    assertContains(page, "Meera Nair")
    assertContains(page, "Wellspring Health")
    assertContains(page, "Rahul Das")
    rows = {row.user.name: row.membership for row in page.context["page"]}
    # Someone without a membership still shows up, with no organisation.
    assert rows["Rahul Das"] is None
    assert rows["Meera Nair"].organisation.name == "Wellspring Health"
    filtered = client.get(participants, {"q": "wellspring"})
    assertContains(filtered, "1 participant found")
    assertNotContains(filtered, "Rahul Das")
    assertContains(client.get(participants, {"q": "meera@"}), "Meera Nair")


def test_event_participants_stay_within_event_permissions(staff, client):
    grant = AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="events",
        category="NHCX",
        can_read=True,
    )
    event = Event.objects.create(
        title="Other category",
        category="UHI",
        starts_at=timezone.now() + timedelta(days=3),
        published_at=timezone.now(),
    )
    EventRegistration.objects.create(event=event, user=UserFactory())
    participants = reverse("experiences:event-participants", args=[event.pk])
    client.force_login(staff)
    assert client.get(participants).status_code == 404
    assert client.post(participants).status_code == 405
    grant.delete()
    assert client.get(participants).status_code == 403


def test_integrators_cannot_see_event_participants(client):
    event = Event.objects.create(
        title="Published webinar",
        starts_at=timezone.now() + timedelta(days=3),
        published_at=timezone.now(),
    )
    membership = MembershipFactory()
    EventRegistration.objects.create(event=event, user=membership.user)
    client.force_login(membership.user)
    assert (
        client.get(
            reverse("experiences:event-participants", args=[event.pk]),
        ).status_code
        == 403
    )


def test_archive_releases_pending_assignments_and_preserves_evidence(
    environment,
    staff,
):
    AccessGrant.objects.create(
        user=staff,
        program="abdm",
        area="review",
        category="HealthLocker",
        can_approve=True,
    )
    approve(environment)
    item = submit(environment, "locker1")
    submission_id = item.selected_submission_id
    workflows.assign_review(item, environment["admin"], staff)
    ticket = Ticket.objects.create(
        organisation=environment["org"],
        product=environment["workspace"].product,
        created_by=environment["applicant"],
        subject="Pending",
        assignee=staff,
    )
    set_staff_active(
        environment["admin"],
        staff.pk,
        active=False,
        revision=staff_revision(staff),
    )
    item.refresh_from_db()
    ticket.refresh_from_db()
    assert item.assignee is None
    assert ticket.assignee is None
    assert item.selected_submission_id == submission_id
    assert item.history.filter(action="Reviewer unassigned").exists()
