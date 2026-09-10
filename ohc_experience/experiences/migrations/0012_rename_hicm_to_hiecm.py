from django.db import migrations

OLD = "HI-CM"
NEW = "HIE-CM"


def rename(apps, old, new):
    prefix, replacement = f"{old}:", f"{new}:"

    def swap(values):
        return [
            replacement + value[len(prefix) :]
            if isinstance(value, str) and value.startswith(prefix)
            else value
            for value in values
        ]

    workspace = apps.get_model("experiences", "ProductWorkspace")
    for pk, values in workspace.objects.values_list("pk", "applied_milestones"):
        updated = swap(values or [])
        if updated != (values or []):
            workspace.objects.filter(pk=pk).update(applied_milestones=updated)

    submission = apps.get_model("experiences", "FormSubmission")
    for pk, data in submission.objects.values_list("pk", "data"):
        if not isinstance(data, dict) or not isinstance(
            data.get("applied_milestones"), list
        ):
            continue
        updated = swap(data["applied_milestones"])
        if updated != data["applied_milestones"]:
            submission.objects.filter(pk=pk).update(
                data={**data, "applied_milestones": updated},
            )

    apps.get_model("experiences", "AccessGrant").objects.filter(
        category=old,
    ).update(category=new)
    apps.get_model("experiences", "TicketContext").objects.filter(
        track=old,
    ).update(track=new)


def forwards(apps, schema_editor):
    rename(apps, OLD, NEW)


def backwards(apps, schema_editor):
    rename(apps, NEW, OLD)


class Migration(migrations.Migration):

    dependencies = [
        ("experiences", "0011_upgrade_uhi_participation"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
