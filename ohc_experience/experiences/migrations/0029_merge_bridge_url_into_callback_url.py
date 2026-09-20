"""Collapse `bridge_url` into `callback_url`: legacy's two names for one URL."""

from django.db import migrations
from django.db import models


def forwards(apps, schema_editor):
    credentials = apps.get_model("experiences", "ProductCredential")
    for credential in credentials.objects.filter(callback_url="").exclude(
        bridge_url="",
    ):
        credential.callback_url = credential.bridge_url
        credential.save(update_fields=["callback_url"])


def backwards(apps, schema_editor):
    """Which column a URL came from is not recoverable, so both get it."""
    apps.get_model("experiences", "ProductCredential").objects.exclude(
        callback_url="",
    ).update(bridge_url=models.F("callback_url"))


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0028_uhi_builds_on_m1"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(
            model_name="productcredential",
            name="bridge_url",
        ),
    ]
