"""The demo seed: complete, idempotent, and removable."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from ohc_experience.abdm.management.commands import seed_abdm_demo as seed
from ohc_experience.abdm.models import ComplianceRecord
from ohc_experience.abdm.models import Product
from ohc_experience.abdm.models import ReviewItem
from ohc_experience.events.models import Event
from ohc_experience.organisations.models import Organisation
from ohc_experience.support.models import Ticket

pytestmark = pytest.mark.django_db


def run(*args) -> str:
    out = StringIO()
    call_command("seed_abdm_demo", *args, stdout=out)
    return out.getvalue()


class TestSeedAbdmDemo:
    def test_seeds_both_personas(self):
        output = run()

        assert "ABDM sandbox demo seeded." in output
        assert seed.OWNER_EMAIL in output
        assert Organisation.objects.filter(slug__in=seed.DEMO_ORG_SLUGS).count() == len(
            seed.ORG_SPECS,
        )
        assert Product.objects.count() == len(seed.PRODUCT_SPECS)
        assert Event.objects.filter(slug__in=seed.DEMO_EVENT_SLUGS).count() == len(
            seed.EVENT_SPECS,
        )
        assert Ticket.objects.count() == len(seed.TICKET_SPECS)

        medibase = Organisation.objects.get(slug=seed.MEDIBASE_SLUG)
        assert medibase.is_verified
        product = Product.objects.get(name=seed.MEDIBASE_PRODUCT)
        assert product.is_registered
        assert product.credential.is_active
        assert product.credential.callback_ok
        statuses = {
            record.milestone_code: record.status
            for record in product.compliance_records.all()
        }
        assert statuses == {
            "M1": ComplianceRecord.Status.APPROVED,
            "M2": ComplianceRecord.Status.UNDER_REVIEW,
            "M3": ComplianceRecord.Status.IN_PROGRESS,
            "M4": ComplianceRecord.Status.LOCKED,
            "PHR1": ComplianceRecord.Status.OPEN,
        }

        open_items = ReviewItem.objects.open()
        assert set(open_items.values_list("status", flat=True)) == {
            ReviewItem.Status.NEW,
            ReviewItem.Status.IN_REVIEW,
            ReviewItem.Status.QUERY_RAISED,
        }
        assert ReviewItem.objects.filter(status=ReviewItem.Status.SENT_BACK).exists()
        pending_organisations = sum(
            1 for spec in seed.ORG_SPECS if spec.verified_days_ago is None
        )
        pending_products = sum(
            1 for spec in seed.PRODUCT_SPECS if spec.registered_days_ago is None
        )
        assert (
            open_items.filter(
                item_type=ReviewItem.Type.ORGANISATION_VERIFICATION,
            ).count()
            == pending_organisations
        )
        assert (
            open_items.filter(
                item_type=ReviewItem.Type.PRODUCT_REGISTRATION,
            ).count()
            == pending_products
        )
        # Ages are real: the oldest open item has been waiting for days.
        assert max(item.age_days for item in open_items) >= 10  # noqa: PLR2004

    def test_running_twice_changes_nothing(self):
        run()
        before = (
            Organisation.objects.count(),
            Product.objects.count(),
            ReviewItem.objects.count(),
            Event.objects.count(),
            Ticket.objects.count(),
        )

        run()

        assert (
            Organisation.objects.count(),
            Product.objects.count(),
            ReviewItem.objects.count(),
            Event.objects.count(),
            Ticket.objects.count(),
        ) == before

    def test_fresh_removes_only_what_it_seeded(self):
        run()
        untouched = Organisation.objects.create(name="Someone Else Pvt Ltd")

        run("--fresh")

        assert Organisation.objects.filter(pk=untouched.pk).exists()
        assert Organisation.objects.filter(slug__in=seed.DEMO_ORG_SLUGS).count() == len(
            seed.ORG_SPECS,
        )
        assert Product.objects.count() == len(seed.PRODUCT_SPECS)
