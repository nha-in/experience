"""PHR1 becomes the first of three PHR phases, and the locker becomes P4.

The PHR track now runs P1 identity and profile, P2 linking and records and P3
subscription flow, in that order; the health locker is P4 and keeps HL1's
freedom to be applied for on its own.

A PHR1 request carries over as P1, the phase it opened with. P2 and P3 are not
invented for it: an approved PHR1 was decided against the old, whole-app
request, and minting approvals here would grant what no reviewer saw. An
integrator applies for the later phases the way any milestone is applied for.
"""

from django.db import migrations

RENAMES = {"phr1": "p1", "locker1": "p4"}
#: `workflows.project_product` writes this title when a request is opened.
TITLES = {
    "phr1": "PHR1 - PHR application flows",
    "locker1": "HL1 - Health locker flows",
    "p1": "P1 - Identity and profile",
    "p4": "P4 - Health locker",
}


def forwards(apps, schema_editor):
    _rename(apps, RENAMES)


def backwards(apps, schema_editor):
    _rename(apps, {new: old for old, new in RENAMES.items()})


def _swap(value, mapping):
    track, _, key = value.partition(":")
    return f"{track}:{mapping[key]}" if key in mapping else value


def _rename(apps, mapping):
    milestone = apps.get_model("experiences", "Milestone")
    workspace = apps.get_model("experiences", "ProductWorkspace")
    rows = list(
        milestone.objects.filter(
            product__workspace__experience_type="abdm",
            key__in=mapping,
        ).select_related("application"),
    )
    for row in rows:
        row.key = mapping[row.key]
        row.save(update_fields=["key"])
        application = row.application
        application.title = TITLES[row.key]
        application.metadata["milestone"] = row.key
        application.save(update_fields=["title", "metadata"])
    for record in workspace.objects.filter(experience_type="abdm"):
        applied = [_swap(value, mapping) for value in record.applied_milestones]
        if applied != record.applied_milestones:
            record.applied_milestones = applied
            record.save(update_fields=["applied_milestones"])
    # The registration form keeps its own copy, which is what reopening it reads.
    submission = apps.get_model("experiences", "FormSubmission")
    submissions = submission.objects.filter(
        form__product__workspace__experience_type="abdm",
    )
    for pk, data in submissions.values_list("pk", "data"):
        values = data.get("applied_milestones") if isinstance(data, dict) else None
        if not isinstance(values, list):
            continue
        applied = [_swap(value, mapping) for value in values]
        if applied != values:
            submission.objects.filter(pk=pk).update(
                data={**data, "applied_milestones": applied},
            )


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0030_drop_callback_probe_readings"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
