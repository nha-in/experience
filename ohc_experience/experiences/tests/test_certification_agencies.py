# ruff: noqa: PLR2004
import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.urls import reverse

from ohc_experience.experiences.admin import CertificationAgencyForm
from ohc_experience.experiences.models import CertificationAgency
from ohc_experience.experiences.registry import registry
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_agency_names_are_unique_within_a_program():
    CertificationAgency.objects.create(program="abdm", name="Example Audit Agency")
    with transaction.atomic(), pytest.raises(IntegrityError):
        CertificationAgency.objects.create(program="abdm", name="Example Audit Agency")
    CertificationAgency.objects.create(program="another", name="Example Audit Agency")


def test_agency_ordering_and_defaults():
    later = CertificationAgency.objects.create(
        program="abdm",
        name="Example A",
        sort_order=20,
    )
    second = CertificationAgency.objects.create(program="abdm", name="Example Z")
    first = CertificationAgency.objects.create(program="abdm", name="Example B")
    agencies = CertificationAgency.objects.filter(
        pk__in=[later.pk, second.pk, first.pk],
    )
    assert list(agencies) == [first, second, later]
    assert first.is_active
    assert first.sort_order == 0
    assert first.created_at
    assert first.updated_at
    assert str(first) == first.name


def test_agency_requires_a_registered_program():
    agency = CertificationAgency(program="missing", name="Example Audit Agency")
    with pytest.raises(ValidationError, match="Choose a registered program"):
        agency.full_clean()
    agency.program = "abdm"
    agency.full_clean()


def test_admin_form_lists_registered_programs_and_rejects_unknown_programs():
    form = CertificationAgencyForm(
        data={"name": "Example Audit Agency", "program": "missing", "sort_order": 0},
    )
    assert list(form.fields["program"].choices) == [
        (program.key, program.short_name) for program in registry.programs()
    ]
    assert not form.is_valid()
    assert "program" in form.errors


def test_superuser_can_add_edit_and_deactivate_agencies(admin_client):
    response = admin_client.post(
        reverse("admin:experiences_certificationagency_add"),
        {
            "name": "Example Audit Agency",
            "program": "abdm",
            "is_active": "on",
            "sort_order": 0,
            "_save": "Save",
        },
    )
    assert response.status_code == 302
    agency = CertificationAgency.objects.get(name="Example Audit Agency")
    change_url = reverse(
        "admin:experiences_certificationagency_change",
        args=[agency.pk],
    )
    response = admin_client.post(
        change_url,
        {
            "name": "Renamed Audit Agency",
            "program": "abdm",
            "sort_order": 10,
            "_save": "Save",
        },
    )
    assert response.status_code == 302
    agency.refresh_from_db()
    assert agency.name == "Renamed Audit Agency"
    assert agency.sort_order == 10
    assert not agency.is_active
    response = admin_client.post(
        change_url,
        {
            "name": agency.name,
            "program": agency.program,
            "is_active": "on",
            "sort_order": agency.sort_order,
            "_save": "Save",
        },
    )
    assert response.status_code == 302
    agency.refresh_from_db()
    assert agency.is_active


def test_staff_cannot_manage_agencies_even_with_model_permissions(client):
    staff = UserFactory(is_staff=True, is_ohc_team=True)
    staff.user_permissions.add(
        *Permission.objects.filter(content_type__model="certificationagency"),
    )
    client.force_login(staff)
    agency = CertificationAgency.objects.create(program="abdm", name="Example Agency")
    assert (
        client.get(
            reverse("admin:experiences_certificationagency_changelist"),
        ).status_code
        == 403
    )
    assert (
        client.post(
            reverse("admin:experiences_certificationagency_add"),
            {"name": "Unauthorised Agency", "program": "abdm", "sort_order": 0},
        ).status_code
        == 403
    )
    assert (
        client.post(
            reverse("admin:experiences_certificationagency_change", args=[agency.pk]),
            {"name": "Unauthorised Rename", "program": "abdm", "sort_order": 0},
        ).status_code
        == 403
    )
    agency.refresh_from_db()
    assert agency.name == "Example Agency"
    assert not CertificationAgency.objects.filter(name="Unauthorised Agency").exists()


def test_superuser_can_edit_agency_active_status_and_order_in_list(admin_client):
    agency = CertificationAgency.objects.create(
        program="abdm",
        name="Example Agency",
        sort_order=20,
    )
    list_url = reverse("admin:experiences_certificationagency_changelist")
    response = admin_client.post(
        f"{list_url}?q=Example+Agency",
        {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-id": agency.pk,
            "form-0-sort_order": "10",
            "_save": "Save",
        },
    )
    assert response.status_code == 302
    agency.refresh_from_db()
    assert not agency.is_active
    assert agency.sort_order == 10
    assert agency.program == "abdm"
    assert agency.name == "Example Agency"


def test_admin_cannot_delete_agencies(admin_client, admin_user, rf):
    agency = CertificationAgency.objects.create(program="abdm", name="Example Agency")
    request = rf.get("/")
    request.user = admin_user
    model_admin = admin.site.get_model_admin(CertificationAgency)
    assert not model_admin.has_delete_permission(request, agency)
    assert "delete_selected" not in model_admin.get_actions(request)
    response = admin_client.post(
        reverse("admin:experiences_certificationagency_delete", args=[agency.pk]),
        {"post": "yes"},
    )
    assert response.status_code == 403
    assert CertificationAgency.objects.filter(pk=agency.pk).exists()


def test_inactive_superuser_has_no_agency_admin_permissions(rf):
    request = rf.get("/")
    request.user = UserFactory(is_staff=True, is_superuser=True, is_active=False)
    model_admin = admin.site.get_model_admin(CertificationAgency)
    assert not model_admin.has_module_permission(request)
    assert not model_admin.has_view_permission(request)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request)
    assert not model_admin.get_queryset(request).exists()
