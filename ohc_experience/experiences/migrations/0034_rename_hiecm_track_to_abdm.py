"""The HIE-CM track is renamed ABDM, which is what the portal now calls it.

Only the track code moves. M1 to M4 keep their keys, so a product's applied
milestones are rewritten from "HIE-CM:m1" to "ABDM:m1", and the review and
events grants that name the track follow. Support grants are left alone: they
are held by support category, not by track.
"""

from django.db import migrations

PROGRAM = "abdm"
OLD = "HIE-CM"
NEW = "ABDM"


def forwards(apps, schema_editor):
    _rename(apps, OLD, NEW)


def backwards(apps, schema_editor):
    _rename(apps, NEW, OLD)


def _rename(apps, old, new):
    prefix = f"{old}:"

    def swap(values):
        return [
            f"{new}:{value[len(prefix) :]}" if value.startswith(prefix) else value
            for value in values
        ]

    workspace = apps.get_model("experiences", "ProductWorkspace")
    for record in workspace.objects.filter(experience_type=PROGRAM):
        applied = swap(record.applied_milestones)
        if applied != record.applied_milestones:
            record.applied_milestones = applied
            record.save(update_fields=["applied_milestones"])
    # The registration form keeps its own copy, which is what reopening it reads.
    submission = apps.get_model("experiences", "FormSubmission")
    submissions = submission.objects.filter(
        form__product__workspace__experience_type=PROGRAM,
    )
    for pk, data in submissions.values_list("pk", "data"):
        values = data.get("applied_milestones") if isinstance(data, dict) else None
        if not isinstance(values, list):
            continue
        applied = swap(values)
        if applied != values:
            submission.objects.filter(pk=pk).update(
                data={**data, "applied_milestones": applied},
            )
    apps.get_model("experiences", "AccessGrant").objects.filter(
        program=PROGRAM,
        area__in=["review", "events"],
        category=old,
    ).update(category=new)
    apps.get_model("events_and_activities", "Event").objects.filter(
        program=PROGRAM,
        category=old,
    ).update(category=new)


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0033_drop_credential_rotation_due"),
        (
            "events_and_activities",
            "0004_rename_events_even_publish_26dca1_idx_events_and__publish_78801a_idx_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
