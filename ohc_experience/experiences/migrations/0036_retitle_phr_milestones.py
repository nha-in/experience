"""P1 to P4 take the names the documentation gives them.

A request's title is written when it is opened, so the open ones are retitled.
"""

from django.db import migrations

PROGRAM = "abdm"
TITLES = {
    "P1 - Identity and profile": "P1 - Registration and login",
    "P2 - Linking and records": "P2 - Consents Management",
    "P3 - Subscription flow": "P3 - Subscription",
    "P4 - Health locker": "P4 - Locker",
}


def forwards(apps, schema_editor):
    _retitle(apps, TITLES)


def backwards(apps, schema_editor):
    _retitle(apps, {new: old for old, new in TITLES.items()})


def _retitle(apps, titles):
    application = apps.get_model("experiences", "ApplicationInstance")
    for old, new in titles.items():
        application.objects.filter(
            product__workspace__experience_type=PROGRAM,
            title=old,
        ).update(title=new)


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0035_retitle_uhi_requests"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
