"""Organisations whose verification request was withdrawn read as pending.

Only withdrawing returns a submitted verification to draft while the
organisation waits: an approved one being edited stays verified, and one sent
back keeps that status.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    organisation = apps.get_model("organisations", "Organisation")
    review = apps.get_model("experiences", "ReviewItem")
    organisation.objects.filter(
        verification_status="pending",
        pk__in=review.objects.filter(
            kind="organisation_verification",
            status="draft",
            submitted_at__isnull=False,
        ).values("organisation_id"),
    ).update(verification_status="withdrawn")


def backwards(apps, schema_editor):
    apps.get_model("organisations", "Organisation").objects.filter(
        verification_status="withdrawn",
    ).update(verification_status="pending")


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0021_uhi_builds_on_m2"),
        ("organisations", "0008_alter_organisation_verification_status"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
