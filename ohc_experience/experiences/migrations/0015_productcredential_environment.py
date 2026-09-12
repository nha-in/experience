import django.db.models.deletion
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    """Hold the production client ID beside the sandbox credential.

    Every existing row is a sandbox credential and takes the default.
    """

    dependencies = [
        ("experiences", "0014_delete_ticketcontext"),
    ]

    operations = [
        migrations.AddField(
            model_name="productcredential",
            name="environment",
            field=models.CharField(
                choices=[("sandbox", "Sandbox"), ("production", "Production")],
                default="sandbox",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="productcredential",
            name="client_id",
            field=models.CharField(max_length=255, unique=True),
        ),
        migrations.AlterField(
            model_name="productcredential",
            name="gateway_url",
            field=models.URLField(blank=True),
        ),
        migrations.AlterField(
            model_name="productcredential",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="credentials",
                to="experiences.product",
            ),
        ),
        migrations.AlterField(
            model_name="productcredential",
            name="rotation_due",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="productcredential",
            constraint=models.UniqueConstraint(
                fields=("product", "environment"),
                name="unique_credential_per_environment",
            ),
        ),
        migrations.AddConstraint(
            model_name="productcredential",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("environment", "sandbox"),
                    ("encrypted_secret", ""),
                    _connector="OR",
                ),
                name="production_credential_holds_no_secret",
            ),
        ),
        migrations.AddConstraint(
            model_name="productcredential",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("environment", "production"),
                    ("rotation_due__isnull", False),
                    _connector="OR",
                ),
                name="sandbox_credential_has_rotation_due",
            ),
        ),
    ]
