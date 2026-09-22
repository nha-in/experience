"""Drop the credential rotation deadline.

It was issue date plus ninety days, shown on the credentials page and nowhere
enforced: nothing expired a credential or acted when it passed.

The default only serves a rollback, which re-adds the column to existing rows.
"""

from django.db import migrations
from django.db import models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0032_others_support_grant_code"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productcredential",
            name="rotation_due",
            field=models.DateTimeField(default=timezone.now),
        ),
        migrations.RemoveField(
            model_name="productcredential",
            name="rotation_due",
        ),
    ]
