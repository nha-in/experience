"""The support menu splits PHR App into four, and answers for UHI again.

"PHR App" answered for the whole app at once; the menu now names the phase a
question is about, so each holder is given all four phases: the same reach as
before, expressible more narrowly.

UHI grants were removed when the menu had no category to put them under. It has
two now, and the locker became a phase of the PHR app, so a grant for either
track that has since been made by hand is expanded rather than left matching
nothing. The grants 0025 already deleted are gone and are not invented back
here: nothing records who held them.
"""

from django.db import migrations

PROGRAM = "abdm"
PHR_APP = "phr-app"
PHASES = ("phr-app-p1", "phr-app-p2", "phr-app-p3", "phr-app-p4")
LOCKER_PHASE = "phr-app-p4"
UHI = ("uhi-integration", "uhi-service")
EXPANSIONS = {
    PHR_APP: PHASES,
    "UHI": UHI,
    "HealthLocker": (LOCKER_PHASE,),
}
#: Where each category came from, for the way back. A locker grant that became
#: the fourth phase collapses with the other phases, into the app it is part of.
COLLAPSE = {
    **{phase: PHR_APP for phase in PHASES},
    **{category: "UHI" for category in UHI},
}


def regrant(apps, mapping):
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
    support.filter(category__in=mapping).delete()
    # A holder of several categories that collapse into one needs one row.
    grant.objects.bulk_create(replacements, ignore_conflicts=True)


def forwards(apps, schema_editor):
    regrant(apps, EXPANSIONS)


def backwards(apps, schema_editor):
    regrant(apps, {category: (source,) for category, source in COLLAPSE.items()})


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0026_rename_sent_back_to_rejected"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
