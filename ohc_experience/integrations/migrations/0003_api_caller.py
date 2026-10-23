"""Outside teams that call this portal's API with a key of their own."""

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0002_resource_left_subscribed"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApiCaller",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255, verbose_name="Team")),
                (
                    "key_hash",
                    models.CharField(editable=False, max_length=64, unique=True),
                ),
                ("active", models.BooleanField(default=True, verbose_name="Active")),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created"),
                ),
                (
                    "last_used_at",
                    models.DateTimeField(
                        editable=False,
                        null=True,
                        verbose_name="Last used",
                    ),
                ),
            ],
            options={
                "verbose_name": "API caller",
                "verbose_name_plural": "API callers",
                "ordering": ["name"],
            },
        ),
    ]
