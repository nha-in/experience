import django.db.models.functions.text
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    """Record the production client ID staff enter once an exit is approved."""

    dependencies = [
        ("experiences", "0014_delete_ticketcontext"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="production_client_id",
            field=models.CharField(
                blank=True,
                max_length=255,
                verbose_name="Production client ID",
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="production_recorded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("production_client_id"),
                condition=models.Q(("production_client_id", ""), _negated=True),
                name="unique_production_client_id",
            ),
        ),
    ]
