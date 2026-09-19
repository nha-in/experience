"""Rename the sent-back verification status to rejected.

`rejected` was already a declared choice and never set; it now carries what
`sent_back` did, so the organisation's badge follows the review that set it.
"""

from django.db import migrations
from django.db import models


def forwards(apps, schema_editor):
    apps.get_model("organisations", "Organisation").objects.filter(
        verification_status="sent_back",
    ).update(verification_status="rejected")


def backwards(apps, schema_editor):
    apps.get_model("organisations", "Organisation").objects.filter(
        verification_status="rejected",
    ).update(verification_status="sent_back")


class Migration(migrations.Migration):
    dependencies = [
        ("organisations", "0008_alter_organisation_verification_status"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="organisation",
            name="verification_status",
            field=models.CharField(
                choices=[
                    ("pending", "Verification pending"),
                    ("verified", "Verified integrator"),
                    ("rejected", "Verification rejected"),
                    ("withdrawn", "Verification withdrawn"),
                ],
                default="pending",
                max_length=20,
                verbose_name="Verification status",
            ),
        ),
    ]
