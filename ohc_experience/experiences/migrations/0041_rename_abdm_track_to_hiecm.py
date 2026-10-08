"""The ABDM track is called HIE-CM again, as it was before 0034.

Only the track code moves back. M1 to M4 keep their keys, so a product's applied
milestones are rewritten from "ABDM:m1" to "HIE-CM:m1", and the review and
events grants and the events that name the track follow. Support grants are left
alone: they are held by support category, not by track.
"""

from django.db import migrations

PROGRAM = "abdm"
OLD = "ABDM"
NEW = "HIE-CM"


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

    product_model = apps.get_model("experiences", "Product")
    for product in product_model.objects.filter(experience_type=PROGRAM):
        applied = swap(product.applied_milestones)
        if applied != product.applied_milestones:
            product.applied_milestones = applied
            product.save(update_fields=["applied_milestones"])
    # The registration form keeps its own copy, which is what reopening it reads.
    submission = apps.get_model("experiences", "FormSubmission")
    submissions = submission.objects.filter(form__product__experience_type=PROGRAM)
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
        ("experiences", "0040_submitted_requests_are_under_review"),
        ("events_and_activities", "0005_constraint_error_messages"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
