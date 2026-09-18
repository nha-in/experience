"""The date the gateway team issued the production credentials.

Existing rows only know when this portal saved the ID, so that date stands in
until staff correct it on the product's production details.
"""

from django.db import migrations
from django.db import models
from django.utils import timezone


def forwards(apps, schema_editor):
    product = apps.get_model("experiences", "Product")
    for pk, recorded_at in product.objects.exclude(
        production_recorded_at=None,
    ).values_list("pk", "production_recorded_at"):
        product.objects.filter(pk=pk).update(
            production_issued_on=timezone.localtime(recorded_at).date(),
        )


def backwards(apps, schema_editor):
    """The column goes with the field; nothing else held this date."""


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0021_uhi_builds_on_m2"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="production_issued_on",
            field=models.DateField(
                blank=True,
                null=True,
                verbose_name="Production issue date",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
