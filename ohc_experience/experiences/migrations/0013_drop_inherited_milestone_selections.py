from django.db import migrations

OWNER = "HIE-CM:m1"
INHERITED = {"UHI:m1", "NHCX:m1", "PHR:m1"}


def drop_inherited(values):
    """M1 is chosen on HIE-CM alone; other tracks show it as a prerequisite."""
    if not INHERITED & set(values):
        return values
    kept = [value for value in values if value not in INHERITED]
    return kept if OWNER in kept else [OWNER, *kept]


def forwards(apps, schema_editor):
    workspace = apps.get_model("experiences", "ProductWorkspace")
    for pk, values in workspace.objects.values_list("pk", "applied_milestones"):
        updated = drop_inherited(values or [])
        if updated != (values or []):
            workspace.objects.filter(pk=pk).update(applied_milestones=updated)

    submission = apps.get_model("experiences", "FormSubmission")
    for pk, data in submission.objects.values_list("pk", "data"):
        values = data.get("applied_milestones") if isinstance(data, dict) else None
        if not isinstance(values, list):
            continue
        updated = drop_inherited(values)
        if updated != values:
            submission.objects.filter(pk=pk).update(
                data={**data, "applied_milestones": updated},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0012_rename_hicm_to_hiecm"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
