from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_rename_is_ohc_team_to_is_nha_team"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="is_nha_team",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Works the support queue across all integrators and publishes "
                    "events. Separate from staff status, which only controls "
                    "Django admin access."
                ),
                verbose_name="NHA team member",
            ),
        ),
    ]
