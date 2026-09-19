"""Support permissions follow the support menu, not the review tracks.

A track that the support menu splits into several categories cannot be granted
for any more, so each holder is given every category that came out of it: the
same reach as before, now expressible more narrowly. UHI and HealthLocker have
no support category, so their support grants would match no ticket that can be
filed; they are removed rather than left to read as access that does nothing.
Review and events grants are untouched — they are still granted by track.
"""

from django.db import migrations

PROGRAM = "abdm"
ABDM = (
    "abdm-m1",
    "abdm-m2",
    "abdm-m3",
    "abdm-m4",
    "abdm-review",
    "abdm-scan-share",
)
NHCX = ("nhcx-auth", "nhcx-workflow", "nhcx-data")
EXPANSIONS = {"HIE-CM": ABDM, "NHCX": NHCX, "PHR": ("phr-app",)}
#: Support grants for tracks the menu does not cover.
DROPPED = ("UHI", "HealthLocker")
COLLAPSE = {
    category: track for track, codes in EXPANSIONS.items() for category in codes
}


def regrant(apps, mapping, dropped=()):
    grant = apps.get_model("experiences", "AccessGrant")
    support = grant.objects.filter(area="support", program=PROGRAM)
    replacements = [
        grant(
            user_id=row.user_id,
            program=row.program,
            area=row.area,
            category=category,
            can_read=row.can_read,
            can_write=row.can_write,
            can_approve=row.can_approve,
        )
        for row in support.filter(category__in=mapping)
        for category in mapping[row.category]
    ]
    support.filter(category__in=[*mapping, *dropped]).delete()
    # A holder of two categories that collapse into one track needs one row.
    grant.objects.bulk_create(replacements, ignore_conflicts=True)


def forwards(apps, schema_editor):
    regrant(apps, EXPANSIONS, DROPPED)


def backwards(apps, schema_editor):
    regrant(apps, {category: (track,) for category, track in COLLAPSE.items()})


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0024_withdrawn_organisation_verifications"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
