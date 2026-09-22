"""A ledger state for a resource revoke could not remove.

The WSO2 wrapper has no call to unsubscribe, so a revoked integrator's gateway
application stays subscribed; the ledger says so instead of failing teardown.
"""

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0033_drop_credential_rotation_due"),
        ("integrations", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="provisionedresource",
            name="integrations_provisioned_resource_state_valid",
        ),
        migrations.AlterField(
            model_name="provisionedresource",
            name="state",
            field=models.CharField(
                choices=[
                    ("ACTIVE", "Active"),
                    ("DISABLED", "Disabled"),
                    ("FAILED", "Failed"),
                    ("ORPHANED", "Orphaned"),
                    ("LEFT_SUBSCRIBED", "Left subscribed"),
                ],
                default="ACTIVE",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="provisionedresource",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "state__in",
                        ["ACTIVE", "DISABLED", "FAILED", "ORPHANED", "LEFT_SUBSCRIBED"],
                    ),
                ),
                name="integrations_provisioned_resource_state_valid",
            ),
        ),
    ]
