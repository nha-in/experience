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
