import ast
import os
import subprocess
import sys
from http import HTTPStatus
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.urls import reverse

from ohc_experience.experiences import workflows
from ohc_experience.experiences.definitions import MilestoneDefinition
from ohc_experience.experiences.models import AuditEvent
from ohc_experience.experiences.models import FormSubmission
from ohc_experience.experiences.models import ReviewItem
from ohc_experience.experiences.registry import registry
from ohc_experience.experiences.tests.example_program import InspectionEvidence
from ohc_experience.experiences.tests.example_program import SupplierQuality
from ohc_experience.users.tests.factories import ReviewerFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def supplier_program(monkeypatch, settings):
    for attribute in ("_definitions", "_forms", "_programs"):
        monkeypatch.setattr(registry, attribute, dict(getattr(registry, attribute)))
    registry.register_program(SupplierQuality)
    settings.EXPERIENCE_PORTAL = SupplierQuality.key
    return SupplierQuality


@pytest.fixture
def equipment(supplier_program, owner_membership):
    workspace, form = workflows.register_product(
        owner_membership.organisation,
        owner_membership.user,
        data={
            "equipment_name": "Water pump",
            "summary": "Industrial equipment",
            "checks": ["Quality:inspection", "Quality:release"],
        },
    )
    assert workspace, form.errors
    return workspace


def test_non_abdm_lifecycle_reuse_dependencies_queries_and_outcomes(
    equipment,
    owner_membership,
):
    actor = owner_membership.user
    reviewer = ReviewerFactory(is_nha_team=True)
    admin = UserFactory(is_superuser=True)
    assert equipment.reference.startswith("QA-")
    assert equipment.product.outcomes.get(outcome_type="receipt").data["reference"]
    inspection = equipment.product.milestones.get(
        key="inspection",
    ).application.review_item
    release = equipment.product.milestones.get(key="release").application.review_item
    assert inspection.form_id == release.form_id
    assert [row.name for row in workflows.pending_prerequisites(release)] == [
        "INS - Inspection",
    ]
    inspection, form, saved = workflows.save_review_form(
        inspection,
        actor,
        data={"report_reference": "Q-1", "score": 95},
        submit=True,
    )
    assert saved, form.errors
    with pytest.raises(PermissionDenied):
        workflows.decide(inspection, actor, action="approve")
    workflows.assign_review(inspection, admin, reviewer)
    workflows.decide(
        inspection,
        reviewer,
        action="query",
        note="Confirm the result.",
        field_key="score",
    )
    query = inspection.queries.get()
    workflows.reply_query(query, actor, "Confirmed")
    workflows.resolve_query(query, reviewer)
    workflows.decide(inspection, reviewer, action="approve")
    assert equipment.product.outcomes.get(outcome_type="quality_certificate").data == {
        "score": 95,
    }
    release.refresh_from_db()
    assert workflows.pending_prerequisites(release) == []
    workflows.reuse_evidence(release, actor)
    release.refresh_from_db()
    assert release.selected_submission_id == inspection.selected_submission_id
    assert not workflows.can_edit_review(inspection)
    assert AuditEvent.objects.filter(item=inspection, action="Approved").exists()


def test_a_dependant_opens_once_its_prerequisite_is_submitted_and_waits_on_approval(
    equipment,
    owner_membership,
):
    actor = owner_membership.user
    reviewer = ReviewerFactory(is_nha_team=True)
    milestones = equipment.product.milestones
    inspection = milestones.get(key="inspection").application.review_item
    release = milestones.get(key="release").application.review_item
    release_data = {"report_reference": "Q-2", "score": 91}

    for submit in (False, True):
        with pytest.raises(
            ValidationError,
            match=r"REL - Release opens once INS - Inspection is submitted\.",
        ):
            workflows.save_review_form(release, actor, data=release_data, submit=submit)
    inspection, form, saved = workflows.save_review_form(
        inspection,
        actor,
        data={"report_reference": "Q-1", "score": 95},
        submit=True,
    )
    assert saved, form.errors
    release, form, saved = workflows.save_review_form(
        release,
        actor,
        data=release_data,
        submit=True,
    )

    assert saved, form.errors
    assert release.status == ReviewItem.Status.NEW
    assert list(ReviewItem.objects.filter(workflows.waiting_reviews())) == [release]
    assert workflows.pending_dependants(inspection) == [release]
    with pytest.raises(ValidationError, match=r"Withdraw REL - Release first\."):
        workflows.withdraw(inspection, actor)
    for action in ("approve", "reject"):
        with pytest.raises(ValidationError, match="once INS - Inspection is approved"):
            workflows.decide(release, reviewer, action=action, note="Hold.")
    workflows.decide(release, reviewer, action="query", note="Which batch?")
    workflows.decide(inspection, reviewer, action="approve")
    assert not ReviewItem.objects.filter(workflows.waiting_reviews()).exists()
    query = release.queries.get()
    workflows.reply_query(query, actor, "Batch 7")
    workflows.resolve_query(query, reviewer)
    workflows.decide(release, reviewer, action="approve")
    release.refresh_from_db()
    assert release.status == ReviewItem.Status.APPROVED


def test_definition_hook_failure_rolls_back_generic_review(
    equipment,
    owner_membership,
    monkeypatch,
):
    item = equipment.product.milestones.get(key="inspection").application.review_item
    before = FormSubmission.objects.count()

    def reject(*args):
        msg = "Supplier is suspended."
        raise ValidationError(msg)

    monkeypatch.setattr(InspectionEvidence, "on_submit", reject)
    with pytest.raises(ValidationError, match="suspended"):
        workflows.save_review_form(
            item,
            owner_membership.user,
            data={"report_reference": "Q-2", "score": 90},
            submit=True,
        )
    item.refresh_from_db()
    assert item.status == "draft"
    assert item.selected_submission_id is None
    assert FormSubmission.objects.count() == before


def test_generic_portal_renders_other_program(equipment, owner_membership, client):
    client.force_login(owner_membership.user)
    response = client.get(equipment.get_absolute_url())
    assert response.status_code == HTTPStatus.OK
    assert b"Supplier Quality Portal" in response.content
    assert b"ABDM" not in response.content
    assert b"HIE-CM" not in response.content
    receipt = equipment.product.outcomes.get(outcome_type="receipt")
    assert receipt.data["reference"].encode() in response.content
    credential_url = reverse("experiences:credentials", args=[equipment.reference])
    assert credential_url.encode() not in response.content
    assert client.get(credential_url).status_code == HTTPStatus.NOT_FOUND
    response = client.get(
        reverse("experiences:track", args=[equipment.reference, "Quality"]),
    )
    assert response.status_code == HTTPStatus.OK
    assert b"Report reference" in response.content
    assert b"WASA" not in response.content
    assert b"Submit application" in response.content
    assert b"Request for exit" not in response.content


def test_product_registration_links_each_track_to_its_documentation(
    owner_membership,
    client,
):
    client.force_login(owner_membership.user)
    response = client.get(reverse("experiences:product-create"))

    assert response.status_code == HTTPStatus.OK
    assert (
        b"https://abdm-docs.dev.eka.care/docs/hiecm/v3/milestones/m1"
        in response.content
    )
    assert b"Create and verify ABHA identities" in response.content
    assert b'id="info-milestone-m1"' in response.content
    assert b"https://abdm-docs.dev.eka.care/docs/uhi/v1" in response.content
    assert b"https://abdm-docs.dev.eka.care/docs/nhcx/v1" in response.content


def test_catalog_rejects_missing_prerequisites(supplier_program):
    with pytest.raises(ValidationError, match="Select"):
        supplier_program.milestone_keys(["Quality:release"])


def test_catalog_rejects_cycles(supplier_program, monkeypatch):
    monkeypatch.setattr(
        supplier_program,
        "milestones",
        {
            "inspection": MilestoneDefinition("inspection", "I", "Inspect", "release"),
            "release": MilestoneDefinition("release", "R", "Release", "inspection"),
        },
    )
    with pytest.raises(ImproperlyConfigured, match="cycle"):
        supplier_program.validate()


def test_definition_schema_versions_preserve_old_submissions(
    equipment,
    owner_membership,
    monkeypatch,
):
    item = equipment.product.milestones.get(key="inspection").application.review_item
    data = {"report_reference": "Q-3", "score": 90}
    item, form, saved = workflows.save_review_form(
        item,
        owner_membership.user,
        data=data,
    )
    assert saved, form.errors
    original = item.selected_submission
    new_version = InspectionEvidence.schema_version + 1
    monkeypatch.setattr(InspectionEvidence, "schema_version", new_version)
    item, form, saved = workflows.save_review_form(
        item,
        owner_membership.user,
        data=data,
        submit=True,
    )
    assert saved, form.errors
    original.refresh_from_db()
    assert original.schema_version == new_version - 1
    assert item.selected_submission.schema_version == new_version


def test_domain_package_has_no_models_or_endpoints():
    root = Path(__file__).resolve().parents[2]
    assert not apps.is_installed("ohc_experience.abdm")
    assert not apps.is_installed("ohc_experience.sandbox")
    assert not (root / "abdm" / "models.py").exists()
    assert not (root / "abdm" / "views.py").exists()
    assert not (root / "abdm" / "urls.py").exists()
    assert {model._meta.app_label for model in (ReviewItem, AuditEvent)} == {  # noqa: SLF001
        "experiences",
    }


def test_engine_does_not_import_an_implementation():
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(
                name.startswith(("ohc_experience.abdm", "ohc_experience.sandbox"))
                for name in modules
            ), path


def test_django_can_boot_without_importing_abdm():
    code = """
import importlib.abc
import sys
from django.conf import settings
settings.EXPERIENCE_IMPLEMENTATIONS = [
    "ohc_experience.experiences.tests.example_program.SupplierQuality"
]
settings.EXPERIENCE_PORTAL = "supplier_quality"
class BlockABDM(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("ohc_experience.abdm", "ohc_experience.sandbox")):
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockABDM())
import django
django.setup()
from django.core.management import call_command
call_command("check")
"""
    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
