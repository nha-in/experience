"""Identifiers, secrets and the read-only helpers the models carry."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone

from ohc_experience.abdm.crypto import decrypt_secret
from ohc_experience.abdm.crypto import encrypt_secret
from ohc_experience.abdm.models import ComplianceRecord
from ohc_experience.abdm.models import Credential
from ohc_experience.abdm.models import ReviewHistory
from ohc_experience.abdm.models import ReviewItem
from ohc_experience.abdm.references import next_review_reference
from ohc_experience.abdm.references import next_sandbox_id
from ohc_experience.abdm.tests.factories import ComplianceRecordFactory
from ohc_experience.abdm.tests.factories import ProductFactory
from ohc_experience.abdm.tests.factories import ReviewItemFactory
from ohc_experience.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

MISSING_FIELD_COUNT = 8


class TestReferences:
    def test_sandbox_ids_are_sequential_within_a_year(self):
        year = timezone.localdate().year
        first = ProductFactory.create()
        second = ProductFactory.create()

        assert first.sandbox_id == f"SBX-{year}-00001"
        assert second.sandbox_id == f"SBX-{year}-00002"

    def test_sandbox_sequence_is_derived_from_the_highest_id_not_the_count(self):
        year = timezone.localdate().year
        ProductFactory.create(sandbox_id=f"SBX-{year}-00041")

        assert next_sandbox_id() == f"SBX-{year}-00042"

    def test_sandbox_sequence_restarts_each_year(self):
        ProductFactory.create(sandbox_id="SBX-2025-00099")

        assert next_sandbox_id(2026) == "SBX-2026-00001"

    def test_review_references_are_sequential(self):
        year = timezone.localdate().year
        item = ReviewItemFactory.create()

        assert item.reference == f"REV-{year}-00001"
        assert next_review_reference() == f"REV-{year}-00002"


class TestCrypto:
    def test_secret_round_trips(self):
        ciphertext = encrypt_secret("sandbox-value-one")

        assert ciphertext != "sandbox-value-one"
        assert decrypt_secret(ciphertext) == "sandbox-value-one"

    def test_credential_exposes_the_decrypted_secret(self):
        credential = Credential(
            product=ProductFactory.create(),
            client_id="SBX_CLIENT_1",
            gateway_base_url="https://gateway.example.test",
            issued_on=timezone.now(),
            rotation_due=timezone.localdate(),
        )
        credential.set_secret("hunter2hunter2")
        credential.save()

        credential.refresh_from_db()
        assert credential.secret == "hunter2hunter2"  # noqa: S105
        assert "hunter2" not in credential.secret_encrypted


class TestComplianceRecord:
    def test_a_fresh_record_lists_every_missing_field(self):
        record = ComplianceRecordFactory.create()

        assert record.is_complete is False
        assert len(record.missing_fields) == MISSING_FIELD_COUNT
        assert record.missing_fields[0] == "Sandbox testing start"

    def test_a_filled_record_is_complete(self):
        today = timezone.localdate()
        record = ComplianceRecordFactory.create(
            start_date=today,
            end_date=today,
            demo_date=today,
            wasa_agency="Example Auditors",
            wasa_issued_on=today,
            wasa_valid_until=today + timedelta(days=365),
            functional_certificate=ContentFile(b"%PDF-1.4", name="certificate.pdf"),
            functional_report=ContentFile(b"%PDF-1.4", name="report.pdf"),
        )

        assert record.is_complete is True
        assert record.key == "HI-CM:M1"
        assert record.label == "HI-CM M1 · ABHA and identity"

    def test_editable_and_review_states(self):
        record = ComplianceRecordFactory.build(status=ComplianceRecord.Status.LOCKED)
        assert record.is_locked
        assert not record.is_editable

        record.status = ComplianceRecord.Status.IN_PROGRESS
        assert record.is_editable
        assert not record.is_under_review

        record.status = ComplianceRecord.Status.QUERY_RAISED
        assert record.is_under_review

    def test_the_sent_back_banner_only_shows_while_editable(self):
        record = ComplianceRecordFactory.build(
            status=ComplianceRecord.Status.IN_PROGRESS,
            sent_back_on=timezone.now(),
        )
        assert record.was_sent_back is True

        record.status = ComplianceRecord.Status.UNDER_REVIEW
        assert record.was_sent_back is False


class TestReviewItem:
    def test_titles_and_subjects(self):
        item = ReviewItemFactory.create()

        assert item.title == "HI-CM M1 exit request"
        assert item.subject_label == item.product.name
        assert item.is_open
        assert item.age_days == 0

    def test_only_one_verification_item_per_organisation(self):
        item = ReviewItemFactory.create(
            item_type=ReviewItem.Type.ORGANISATION_VERIFICATION,
            compliance=None,
            product=None,
            organisation=ProductFactory.create().organisation,
        )

        with pytest.raises(Exception, match="unique_organisation_verification_item"):
            ReviewItemFactory.create(
                item_type=ReviewItem.Type.ORGANISATION_VERIFICATION,
                compliance=None,
                product=None,
                organisation=item.organisation,
            )


class TestHistoryIsImmutable:
    def test_an_entry_cannot_be_edited(self):
        entry = ReviewHistory.objects.create(
            item=ReviewItemFactory.create(),
            actor=UserFactory.create(),
            kind=ReviewHistory.Kind.SUBMITTED,
            title="Submitted",
        )

        entry.title = "Rewritten"
        with pytest.raises(ValidationError):
            entry.save()
