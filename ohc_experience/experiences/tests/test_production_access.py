# ruff: noqa: F811, PLR2004
import csv
import io
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import connection
from django.db import transaction
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from django.utils import timezone

from ohc_experience.abdm.demo import product_data
from ohc_experience.abdm.tests.test_workflow import approve
from ohc_experience.abdm.tests.test_workflow import environment  # noqa: F401
from ohc_experience.abdm.tests.test_workflow import milestone
from ohc_experience.experiences import credentials
from ohc_experience.experiences import production
from ohc_experience.experiences import workflows as services
from ohc_experience.experiences.models import AccessGrant
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import Notification
from ohc_experience.experiences.models import ProductCredential
from ohc_experience.experiences.registry import get_program
from ohc_experience.experiences.tasks import monitor_callbacks
from ohc_experience.integrations.credentials import publish_credential
from ohc_experience.organisations.models import Organisation
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


def sandbox_of(product):
    return ProductCredential.objects.get(product=product, environment="sandbox")


def record(environment, client_id, *, expected="", actor=None):
    return production.record(
        product_of(environment),
        actor or environment["reviewer"],
        client_id=client_id,
        expected=expected,
    )


def production_mail():
    return Notification.objects.filter(subject__startswith=PRODUCTION_MAIL)


def test_throttling_lets_requests_through_when_the_cache_is_down():
    """Production's Redis ignores connection errors, answering None."""
    with (
        patch("ohc_experience.experiences.credentials.cache.add", return_value=None),
        patch("ohc_experience.experiences.credentials.cache.incr", return_value=None),
    ):
        assert credentials.throttle("outage", 1)
    with (
        patch("ohc_experience.experiences.credentials.cache.add", return_value=False),
        patch(
            "ohc_experience.experiences.credentials.cache.incr",
            side_effect=ValueError,
        ),
    ):
        assert credentials.throttle("expired", 1)


def test_existing_credentials_become_sandbox_rows():
    previous = [("experiences", "0014_delete_ticketcontext")]
    target = [("experiences", "0015_productcredential_environment")]
    # Users and organisations are untouched by the migration; only the
    # experiences tables go back to their earlier shape.
    owner = UserFactory()
    organisation = Organisation.objects.create(name="Existing organisation")
    executor = MigrationExecutor(connection)
    executor.migrate(previous)
    try:
        apps = executor.loader.project_state(previous).apps
        product = apps.get_model("experiences", "Product").objects.create(
            organisation_id=organisation.pk,
            name="Existing product",
            slug="existing-product",
            product_type="hmis",
            description="Issued before production IDs were recorded.",
            created_by_id=owner.pk,
        )
        sealed = credentials.cipher().encrypt(b"sandbox secret").decode()
        existing = apps.get_model("experiences", "ProductCredential").objects.create(
            product=product,
            client_id="SBX_EXISTING",
            encrypted_secret=sealed,
            gateway_url="https://gateway.example.test",
            rotation_due=timezone.now(),
        )
        # PostgreSQL won't alter a table while the new rows' deferred foreign-key
        # checks are pending in this transaction, so run them now.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        MigrationExecutor(connection).migrate(target)
        credential = ProductCredential.objects.get(pk=existing.pk)
        assert credential.environment == "sandbox"
        assert credential.encrypted_secret == sealed
        assert credential.rotation_due is not None
    finally:
        MigrationExecutor(connection).migrate(target)


def test_recording_waits_for_an_approved_exit(environment):
    with pytest.raises(ValidationError, match="approved milestone exit"):
        record(environment, "PROD-1")
    approve(environment)
    credential = record(environment, "PROD-1")
    assert credential.environment == "production"
    assert credential.status == "active"
    assert credential.encrypted_secret == ""
    assert credential.rotation_due is None
    assert production.current(product_of(environment)) == credential
    assert sandbox_of(product_of(environment)).client_id != "PROD-1"


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
    assert production.current(product_of(environment)).client_id == "PROD-1"


def test_record_change_and_remove_are_audited_and_mailed(environment, settings):
    settings.SITE_BASE_URL = "https://sandbox.example.in/"
    approve(environment)
    product = product_of(environment)
    record(environment, "PROD-1")
    record(environment, "PROD-1", expected="PROD-1")
    record(environment, "PROD-2", expected="PROD-1")
    production.remove(product, environment["reviewer"], expected="PROD-2")
    assert production.current(product) is None
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


def second_product(environment):
    """Another registered product, with no credentials of its own."""
    workspace, form = services.register_product(
        environment["org"],
        environment["applicant"],
        data=product_data("Second product"),
    )
    assert workspace, form.errors
    ProductCredential.objects.filter(product=workspace.product).delete()
    return workspace.product


def test_rejected_client_ids(environment):
    approve(environment)
    product = product_of(environment)
    record(environment, "PROD-1")
    with pytest.raises(ValidationError, match="changed after you opened it"):
        record(environment, "PROD-2", expected="")
    with pytest.raises(ValidationError, match="already in use"):
        record(
            environment,
            sandbox_of(product).client_id.lower(),
            expected="PROD-1",
        )
    for bad in ["ab", "has space", "-leading", "x" * 256]:
        with pytest.raises(ValidationError):
            record(environment, bad, expected="PROD-1")
    ProductCredential.objects.create(
        product=second_product(environment),
        environment="production",
        client_id="PROD-OTHER",
        encrypted_secret="",
    )
    with pytest.raises(ValidationError, match="already in use"):
        record(environment, "prod-other", expected="PROD-1")
    assert production.current(product).client_id == "PROD-1"


def test_an_exact_clash_past_the_check_becomes_a_form_error(environment):
    approve(environment)
    sandbox_id = sandbox_of(product_of(environment)).client_id
    with (
        patch.object(production, "_in_use", return_value=False),
        pytest.raises(ValidationError, match="already in use"),
    ):
        record(environment, sandbox_id)
    # The failed insert rolled back to its savepoint; the transaction still works.
    assert production.current(product_of(environment)) is None


def test_database_rules(environment):
    approve(environment)
    product = product_of(environment)
    record(environment, "PROD-1")
    other = second_product(environment)
    attempts = {
        "one production row per product": {
            "product": product,
            "environment": "production",
            "client_id": "PROD-2",
        },
        "sandbox rows rotate": {
            "product": other,
            "environment": "sandbox",
            "client_id": "SBX-2",
            "encrypted_secret": "sealed",
        },
        "production rows hold no secret": {
            "product": other,
            "environment": "production",
            "client_id": "PROD-3",
            "encrypted_secret": "held",
        },
        "client IDs are unique across environments": {
            "product": other,
            "environment": "production",
            "client_id": "PROD-1",
        },
    }

    def accepted(fields):
        try:
            with transaction.atomic():
                ProductCredential.objects.create(**fields)
        except IntegrityError:
            return False
        return True

    assert [rule for rule, fields in attempts.items() if accepted(fields)] == []


def test_sandbox_code_leaves_production_rows_alone(environment):
    approve(environment)
    product = product_of(environment)
    applicant = environment["applicant"]
    row = record(environment, "PROD-1")
    for action in [
        lambda: credentials.reveal(row, applicant),
        lambda: credentials.rotate(row, applicant),
        lambda: credentials.revoke(row, applicant),
        lambda: credentials.save_urls(
            row,
            applicant,
            {"callback_url": "https://example.org/cb", "bridge_url": ""},
        ),
        lambda: credentials.check_callback(row),
    ]:
        with pytest.raises(ValidationError, match="Only sandbox credentials"):
            action()
    ProductCredential.objects.filter(product=product).update(
        callback_url="https://example.org/cb",
    )
    with patch("ohc_experience.experiences.tasks.check_callback") as checked:
        monitor_callbacks()
    assert [call.args[0].environment for call in checked.call_args_list] == [
        "sandbox",
    ]
    # Re-provisioning without a sandbox row makes a new one beside production.
    ProductCredential.objects.filter(product=product, environment="sandbox").delete()
    publish_credential(product)
    assert sandbox_of(product).encrypted_secret
    row.refresh_from_db()
    assert (row.client_id, row.environment, row.encrypted_secret) == (
        "PROD-1",
        "production",
        "",
    )


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
    assert production.current(product_of(environment)) is None
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
    assert production.current(product_of(environment)).client_id == "PROD-9"
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
    assert production.current(product_of(environment)) is None
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
        sandbox_of(product).client_id,
        product.organisation.name[:8],
        environment["workspace"].reference,
    ]:
        response = client.get(url, {"tab": "all", "q": query})
        assert [row.pk for row in response.context["rows"]] == [product.pk]
    assert not client.get(url, {"tab": "all", "q": "nothing"}).context["rows"]
    product.name = "=HYPERLINK(1)"
    product.save()
    response = client.get(reverse("experiences:production-export"), {"tab": "all"})
    assert response["Content-Type"] == "text/csv; charset=utf-8"
    assert response["Content-Disposition"].startswith("attachment;")
    content = response.content.decode()
    assert content.startswith("﻿")
    header, row = list(csv.reader(io.StringIO(content.removeprefix("﻿"))))
    assert tuple(header) == production.CSV_HEADER
    values = dict(zip(header, row, strict=True))
    assert values["Product"] == "'=HYPERLINK(1)"
    assert values["Production client ID"] == "PROD-1"
    assert values["Approved milestones"] == "M1"
    assert values["Owner email"] == environment["applicant"].email
    assert values["Recorded by"] == environment["reviewer"].email
    response = client.get(reverse("experiences:production-export"))
    assert len(list(csv.reader(io.StringIO(response.content.decode())))) == 1


def test_integrator_and_reviewer_pages(environment, client):
    reference = environment["workspace"].reference
    credentials_url = reverse("experiences:credentials", args=[reference])
    notices = get_program().credentials
    client.force_login(environment["applicant"])
    assert (
        notices.production_ineligible_notice
        in client.get(
            credentials_url,
        ).content.decode()
    )
    approve(environment)
    assert (
        notices.production_pending_notice
        in client.get(
            credentials_url,
        ).content.decode()
    )
    record(environment, "PROD-1")
    content = client.get(credentials_url).content.decode()
    assert "PROD-1" in content
    assert notices.production_notice in content
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
    monkeypatch.setattr(get_program().credentials, "record_production_access", False)
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
