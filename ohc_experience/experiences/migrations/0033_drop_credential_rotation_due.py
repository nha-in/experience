"""Drop the credential rotation deadline.

It was issue date plus ninety days, shown on the credentials page and nowhere
enforced: nothing expired a credential or acted when it passed.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0032_others_support_grant_code"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="productcredential",
            name="rotation_due",
        ),
    ]
