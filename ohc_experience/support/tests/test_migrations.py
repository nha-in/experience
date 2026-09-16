import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ohc_experience.support.models import Ticket
from ohc_experience.support.tests.factories import product_for

pytestmark = pytest.mark.django_db

BEFORE = [("support", "0006_rename_awaiting_vendor_to_awaiting_integrator")]
AFTER = [("support", "0007_remove_resolved_status")]


def test_resolved_tickets_close_without_moving_in_the_queue(organisation):
    product = product_for(organisation)
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    try:
        old_ticket = executor.loader.project_state(BEFORE).apps.get_model(
            "support",
            "Ticket",
        )
        tickets = {
            status: old_ticket.objects.create(
                reference=f"TKT-{number}",
                organisation_id=organisation.pk,
                product_id=product.pk,
                subject=f"{status.capitalize()} ticket",
                status=status,
            )
            for number, status in enumerate(("open", "resolved", "closed"), start=1)
        }

        MigrationExecutor(connection).migrate(AFTER)

        migrated = Ticket.objects.in_bulk([ticket.pk for ticket in tickets.values()])
        assert {
            status: migrated[ticket.pk].status for status, ticket in tickets.items()
        } == {"open": "open", "resolved": "closed", "closed": "closed"}
        resolved = tickets["resolved"]
        assert migrated[resolved.pk].updated_at == resolved.updated_at
    finally:
        MigrationExecutor(connection).migrate(AFTER)
