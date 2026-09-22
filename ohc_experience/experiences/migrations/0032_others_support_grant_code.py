"""A support permission for the catch-all names its new code, "others".

Blank meant Others to support, the way it meant the Others tickets themselves.
Review and events are granted by track, and blank there still means general and
onboarding work, so only support grants move.
"""

from django.db import migrations

PROGRAM = "abdm"
OTHERS = "others"


def forwards(apps, schema_editor):
    grant = apps.get_model("experiences", "AccessGrant")
    grant.objects.filter(program=PROGRAM, area="support", category="").update(
        category=OTHERS,
    )


def backwards(apps, schema_editor):
    grant = apps.get_model("experiences", "AccessGrant")
    grant.objects.filter(program=PROGRAM, area="support", category=OTHERS).update(
        category="",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0031_split_phr_into_phases"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
