from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("organisations", "0006_organisation_entity_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="organisation",
            name="verification_status",
            field=models.CharField(
                choices=[
                    ("pending", "Verification pending"),
                    ("verified", "Verified integrator"),
                    ("rejected", "Verification rejected"),
                    ("sent_back", "Sent back"),
                ],
                default="pending",
                max_length=20,
                verbose_name="Verification status",
            ),
        ),
    ]
