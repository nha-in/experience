# Hand-written: an integrator is routinely several solution types at once, so
# the scalar column becomes a set. Existing values are rewritten as JSON text
# first, otherwise Postgres cannot cast varchar to jsonb and the migration
# would fail on any populated row.

import json

from django.db import migrations
from django.db import models


def to_list(apps, schema_editor):
    workspace = apps.get_model("experiences", "ProductWorkspace")
    for pk, value in workspace.objects.values_list("pk", "solution_type"):
        workspace.objects.filter(pk=pk).update(
            solution_type=json.dumps([value] if value else []),
        )


def to_scalar(apps, schema_editor):
    workspace = apps.get_model("experiences", "ProductWorkspace")
    for pk, value in workspace.objects.values_list("pk", "solution_type"):
        # Reverses in either shape: the field may already be text again.
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = [value]
        workspace.objects.filter(pk=pk).update(solution_type=(value or [""])[0])


class Migration(migrations.Migration):

    dependencies = [
        ("experiences", "0006_accessgrant"),
    ]

    operations = [
        migrations.RunPython(to_list, to_scalar),
        migrations.AlterField(
            model_name="productworkspace",
            name="solution_type",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
