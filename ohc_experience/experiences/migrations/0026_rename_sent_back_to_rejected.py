"""Rename the sent-back review outcome to rejected.

The decision is the same one: a reviewer returns a request so the integrator
can correct and resubmit it. Only the word changed, so existing reviews and
their audit entries are renamed with it rather than left reading the old term
beside the new one.
"""

from django.db import migrations
from django.db import models


def forwards(apps, schema_editor):
    apps.get_model("experiences", "ReviewItem").objects.filter(
        status="sent_back",
    ).update(status="rejected")
    apps.get_model("experiences", "AuditEvent").objects.filter(
        action="Sent back",
    ).update(action="Rejected")


def backwards(apps, schema_editor):
    apps.get_model("experiences", "ReviewItem").objects.filter(
        status="rejected",
    ).update(status="sent_back")
    apps.get_model("experiences", "AuditEvent").objects.filter(
        action="Rejected",
    ).update(action="Sent back")


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0025_support_grants_by_category"),
    ]

    operations = [
        migrations.AlterField(
            model_name="reviewitem",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "In progress"),
                    ("new", "New"),
                    ("in_review", "Under review"),
                    ("query_raised", "Query raised"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="draft",
                max_length=24,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
