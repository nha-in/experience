# ruff: noqa: F811, PLR2004
import csv
import io

import pytest
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import transaction
from django.urls import reverse

from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.experiences import production
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import Notification
from ohc_experience.experiences.models import Product
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.registry import get_program
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PRODUCTION_MAIL = "ABDM: production client ID"


def staff(category="", *, approver=False, **fields):
    user = UserFactory(is_nha_team=True, is_staff=True, **fields)
    AccessGrant.objects.create(
        user=user,
        program="abdm",
        area="review",
        category=category,
        can_read=True,
        can_write=approver,
        can_approve=approver,
    )
    return user


def product_of(environment):
    return environment["workspace"].product


def saved_id(environment):
    """The production client ID as stored, whatever a copy in memory says."""
    return Product.objects.values_list("production_client_id", flat=True).get(
        pk=product_of(environment).pk,
    )


def sandbox_id(product):
    return ProductCredential.objects.get(product=product).client_id


def record(environment, client_id, *, expected="", actor=None):
    production.record(
        product_of(environment),
        actor or environment["reviewer"],
        client_id=client_id,
        expected=expected,
    )


def production_mail():
    return Notification.objects.filter(subject__startswith=PRODUCTION_MAIL)


def second_product(environment):
    """Another registered product in the same organisation."""
    workspace, form = services.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Second product"),
    )
    assert workspace, form.errors
    return workspace.product


def test_recording_waits_for_an_approved_exit(environment):
    product = product_of(environment)
    with pytest.raises(ValidationError, match="approved milestone exit"):
        record(environment, "PROD-1")
    approve(environment)
    record(environment, "PROD-1")
    assert saved_id(environment) == "PROD-1"
    # The product passed in is updated too, so saving it later keeps the ID.
    assert product.production_client_id == "PROD-1"
    assert product.production_recorded_at is not None


def test_only_onboarding_approvers_record(environment):
    approve(environment)
    program = get_program()
    allowed = [
        environment["admin"],
        environment["reviewer"],
        staff(approver=True),
    ]
    denied = [
        staff(),
        staff("HIE-CM", approver=True),
        environment["applicant"],
        UserFactory(is_superuser=True, is_staff=True, is_active=False),
    ]
    assert all(production.can_manage(user, program) for user in allowed)
    assert not any(production.can_manage(user, program) for user in denied)
    for user in denied:
        with pytest.raises(PermissionDenied):
            record(environment, "PROD-1", actor=user)
    record(environment, "PROD-1", actor=allowed[-1])
    assert saved_id(environment) == "PROD-1"


def test_record_change_and_remove_are_audited_and_mailed(environment, settings):
    settings.SITE_BASE_URL = "https://sandbox.example.in/"
    approve(environment)
    product = product_of(environment)
    record(environment, "PROD-1")
    record(environment, "PROD-1", expected="PROD-1")
    record(environment, "PROD-2", expected="PROD-1")
    production.remove(product, environment["reviewer"], expected="PROD-2")
    product.refresh_from_db()
    assert (product.production_client_id, product.production_recorded_at) == ("", None)
    events = AuditEvent.objects.filter(product=product, action__in=production.ACTIONS)
    assert [(event.action, event.detail) for event in events.order_by("pk")] == [
        (production.RECORDED, {"before": "", "after": "PROD-1"}),
        (production.CHANGED, {"before": "PROD-1", "after": "PROD-2"}),
        (production.REMOVED, {"before": "PROD-2"}),
    ]
    # Record and change are mailed to every active member; saving the same ID
    # again and removing it are not.
    mails = production_mail().order_by("pk")
    assert [mail.subject for mail in mails] == [
        f"{PRODUCTION_MAIL} recorded",
        f"{PRODUCTION_MAIL} updated",
    ]
    assert {mail.recipient for mail in mails} == {environment["applicant"].email}
    for mail in mails:
        assert "https://sandbox.example.in/products/" in mail.body
        assert environment["workspace"].reference in mail.body
        assert "PROD-" not in mail.body


def test_rejected_client_ids(environment):
    approve(environment)
    product = product_of(environment)
    record(environment, "PROD-1")
    with pytest.raises(ValidationError, match="changed after you opened it"):
        record(environment, "PROD-2", expected="")
    with pytest.raises(ValidationError, match="already in use"):
        record(environment, sandbox_id(product).lower(), expected="PROD-1")
    for bad in ["ab", "has space", "-leading", "x" * 256]:
        with pytest.raises(ValidationError):
            record(environment, bad, expected="PROD-1")
    other = second_product(environment)
    Product.objects.filter(pk=other.pk).update(production_client_id="PROD-OTHER")
    # The unique index turns this one away; the transaction carries on.
    with pytest.raises(ValidationError, match="already in use"):
        record(environment, "prod-other", expected="PROD-1")
    assert saved_id(environment) == "PROD-1"
    assert product.production_client_id == "PROD-1"


def test_production_client_ids_are_unique_whatever_their_case(environment):
    first = product_of(environment)
    # Both products were saved without an ID, so blanks never clash.
    second = second_product(environment)
    Product.objects.filter(pk=first.pk).update(production_client_id="PROD-1")
    for clash in ["PROD-1", "prod-1"]:
        with pytest.raises(IntegrityError), transaction.atomic():
            Product.objects.filter(pk=second.pk).update(production_client_id=clash)


def test_staff_pages_need_general_review_access(environment, client):
    approve(environment)
    reference = environment["workspace"].reference
    list_url = reverse("experiences:production-list")
    export_url = reverse("experiences:production-export")
    detail_url = reverse("experiences:production-detail", args=[reference])
    for url in [list_url, export_url, detail_url]:
        assert client.get(url).status_code == 302
    for user in [environment["applicant"], staff("HIE-CM", approver=True)]:
        client.force_login(user)
        for url in [list_url, export_url, detail_url]:
            assert client.get(url).status_code == 403
        assert client.post(detail_url, {"intent": "save"}).status_code == 403
    client.force_login(staff("HIE-CM", approver=True))
    assert (
        b'id="nav-production"' not in client.get(reverse("experiences:queue")).content
    )
    reader = staff()
    client.force_login(reader)
    response = client.get(list_url)
    assert response.status_code == 200
    assert b'id="nav-production"' in response.content
    assert client.get(export_url).status_code == 200
    response = client.get(detail_url)
    assert response.status_code == 200
    assert b'name="intent" value="save"' not in response.content
    assert (
        client.post(
            detail_url,
            {"intent": "save", "client_id": "PROD-1", "expected": ""},
        ).status_code
        == 403
    )
    assert saved_id(environment) == ""
    assert (
        client.get(reverse("experiences:production-detail", args=["SBX-0"])).status_code
        == 404
    )


def test_detail_page_records_and_removes(environment, client):
    approve(environment)
    reference = environment["workspace"].reference
    url = reverse("experiences:production-detail", args=[reference])
    client.force_login(staff(approver=True))
    response = client.get(url)
    assert response.context["eligible"]
    assert [milestone.key for milestone in response.context["exits"]] == ["m1"]
    response = client.post(
        url,
        {"intent": "save", "client_id": " PROD-9 ", "expected": ""},
        follow=True,
    )
    assert b"Production client ID saved." in response.content
    assert saved_id(environment) == "PROD-9"
    response = client.post(
        url,
        {"intent": "save", "client_id": "PROD-10", "expected": ""},
    )
    assert response.status_code == 200
    assert "changed after you opened it" in response.content.decode()
    assert b"PROD-9" in response.content
    response = client.post(url, {"intent": "archive", "expected": "PROD-9"})
    assert b"Choose a valid action." in response.content
    response = client.post(url, {"intent": "remove", "expected": "PROD-9"}, follow=True)
    assert b"Production client ID removed." in response.content
    assert saved_id(environment) == ""
    assert [event.action for event in response.context["history"]] == [
        production.REMOVED,
        production.RECORDED,
    ]


def test_list_tabs_search_and_csv(environment, client):
    approve(environment)
    product = product_of(environment)
    client.force_login(staff())
    url = reverse("experiences:production-list")
    response = client.get(url)
    assert response.context["counts"] == {"awaiting": 1, "recorded": 0, "all": 1}
    assert [row.pk for row in response.context["rows"]] == [product.pk]
    assert response.context["rows"][0].approved_codes == ["M1"]
    assert b'hx-boost="false"' in response.content
    record(environment, "PROD-1")
    response = client.get(url, {"tab": "recorded"})
    assert response.context["counts"] == {"awaiting": 0, "recorded": 1, "all": 1}
    for query in [
        "prod-1",
        sandbox_id(product),
        product.organisation.name[:8],
        environment["workspace"].reference,
    ]:
        response = client.get(url, {"tab": "all", "q": query})
        assert [row.pk for row in response.context["rows"]] == [product.pk]
    assert not client.get(url, {"tab": "all", "q": "nothing"}).context["rows"]
    Product.objects.filter(pk=product.pk).update(name="=HYPERLINK(1)")
    response = client.get(reverse("experiences:production-export"), {"tab": "all"})
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"].startswith("attachment;")
    content = response.content.decode()
    assert content.startswith("﻿")
    header, row = list(csv.reader(io.StringIO(content.removeprefix("﻿"))))
    assert tuple(header) == production.CSV_HEADER
    values = dict(zip(header, row, strict=True))
    assert values["Product"] == "'=HYPERLINK(1)"
    assert values["Sandbox client ID"] == sandbox_id(product)
    assert values["Production client ID"] == "PROD-1"
    assert values["Recorded on"]
    assert values["Approved milestones"] == "M1"
    assert values["Owner email"] == environment["applicant"].email
    assert values["Recorded by"] == environment["reviewer"].email
    response = client.get(reverse("experiences:production-export"))
    assert len(list(csv.reader(io.StringIO(response.content.decode())))) == 1


def test_integrator_and_reviewer_pages(environment, client):
    reference = environment["workspace"].reference
    credentials_url = reverse("experiences:credentials", args=[reference])
    copy = get_program().production_credentials
    client.force_login(environment["applicant"])
    assert copy.unavailable_notice in client.get(credentials_url).content.decode()
    approve(environment)
    assert copy.pending_notice in client.get(credentials_url).content.decode()
    record(environment, "PROD-1")
    content = client.get(credentials_url).content.decode()
    assert "PROD-1" in content
    assert copy.usage_notice in content
    content = client.get(
        reverse("experiences:overview", args=[reference]),
    ).content.decode()
    assert "PROD-1" in content
    assert "/assess/production/" not in content
    client.force_login(environment["reviewer"])
    content = client.get(
        reverse("experiences:review", args=[milestone(environment).pk]),
    ).content.decode()
    assert "Production client ID" in content
    assert "PROD-1" in content
    assert reverse("experiences:production-detail", args=[reference]) in content


def test_programs_that_do_not_record_production_ids(environment, client, monkeypatch):
    approve(environment)
    monkeypatch.setattr(get_program(), "production_credentials", None)
    assert production.state(product_of(environment)) is None
    with pytest.raises(PermissionDenied):
        record(environment, "PROD-1")
    client.force_login(environment["reviewer"])
    assert client.get(reverse("experiences:production-list")).status_code == 404
    assert (
        b'id="nav-production"' not in client.get(reverse("experiences:queue")).content
    )
    client.force_login(environment["applicant"])
    content = client.get(
        reverse("experiences:credentials", args=[environment["workspace"].reference]),
    ).content.decode()
    assert 'id="production-card"' not in content
