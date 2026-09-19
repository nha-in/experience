"""Tickets filed under "PHR App" name no phase of it.

The category split into four phases, and a ticket filed under the whole app has
nothing in it that says which one. It lands in the catch-all rather than keeping
a code no one can be granted for any more, the way tickets on a split track did
when support stopped being filed by track.

There is no way back: the catch-all holds tickets that were never PHR App ones,
so reversing cannot tell them apart.
"""

from django.db import migrations

PHR_APP = "phr-app"
OTHERS = ""


def forwards(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    ticket.objects.filter(category=PHR_APP).update(category=OTHERS)


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0009_ticket_issue_type"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
