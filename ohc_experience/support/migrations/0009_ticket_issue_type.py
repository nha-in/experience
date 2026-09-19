"""Tickets are filed under support categories, not tracks.

A track answered for a whole program area at once; the support menu names the
milestone or the kind of failure a question is about. Only PHR maps across
cleanly. A ticket filed under a track that split into several categories has
nothing in it that says which one, so it lands in the catch-all rather than
keeping a code no one can be granted for any more.
"""

from django.db import migrations
from django.db import models

PHR = "PHR"
PHR_APP = "phr-app"
#: Track codes tickets used to be filed under, and where they land now.
SPLIT = ("HIE-CM", "NHCX", "UHI", "HealthLocker")
#: Which track each new category came from, for the way back.
TRACKS = {
    "abdm-m1": "HIE-CM",
    "abdm-m2": "HIE-CM",
    "abdm-m3": "HIE-CM",
    "abdm-m4": "HIE-CM",
    "abdm-review": "HIE-CM",
    "abdm-scan-share": "HIE-CM",
    PHR_APP: PHR,
    "nhcx-auth": "NHCX",
    "nhcx-workflow": "NHCX",
    "nhcx-data": "NHCX",
}


def forwards(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    ticket.objects.filter(category=PHR).update(category=PHR_APP)
    ticket.objects.filter(category__in=SPLIT).update(category="")


def backwards(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    for category, track in TRACKS.items():
        ticket.objects.filter(category=category).update(category=track)


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0008_ticket_category_from_track"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="issue_type",
            field=models.CharField(
                blank=True,
                max_length=100,
                verbose_name="Issue type",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
