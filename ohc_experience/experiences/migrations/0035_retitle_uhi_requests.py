"""UHI1 is shown as UHI, the name NHA uses for the milestone.

Only its code moves. The key stays "uhi1", so selections, grants and links are
untouched. What does carry the code is the title each UHI request was opened
with, which `workflows.project_product` writes as "<code> - <name>".
"""

from django.db import migrations

PROGRAM = "abdm"
KEY = "uhi1"
OLD = "UHI1 - UHI participation"
NEW = "UHI - UHI participation"


def forwards(apps, schema_editor):
    _retitle(apps, OLD, NEW)


def backwards(apps, schema_editor):
    _retitle(apps, NEW, OLD)


def _retitle(apps, old, new):
    milestone = apps.get_model("experiences", "Milestone")
    requests = milestone.objects.filter(
        product__workspace__experience_type=PROGRAM,
        key=KEY,
        application__title=old,
    ).values("application_id")
    apps.get_model("experiences", "ApplicationInstance").objects.filter(
        pk__in=requests,
    ).update(title=new)


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0034_rename_hiecm_track_to_abdm"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
