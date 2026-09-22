"""The catch-all support category gets a code of its own, "others".

It used to be blank, which is also what a ticket filed under nothing holds, so
the portal could only infer it: the inbox filter needed a stand-in value for it,
and the category field could not be required. Every blank ABDM ticket was filed
under Others, the tickets 0009 and 0010 moved there included, so all of them
take the new code. The way back returns them to blank.
"""

from django.db import migrations
from django.db import models

PROGRAM = "abdm"
OTHERS = "others"


def forwards(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    ticket.objects.filter(
        category="",
        product__workspace__experience_type=PROGRAM,
    ).update(category=OTHERS)


def backwards(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    ticket.objects.filter(
        category=OTHERS,
        product__workspace__experience_type=PROGRAM,
    ).update(category="")


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0031_split_phr_into_phases"),
        ("support", "0011_ticketmessage_priority_change"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="ticket",
            name="category",
            field=models.CharField(max_length=100, verbose_name="Category"),
        ),
    ]
