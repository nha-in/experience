from django.db import migrations
from django.db import models


def resolved_to_closed(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    ticket.objects.filter(status="resolved").update(status="closed")


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0006_rename_awaiting_vendor_to_awaiting_integrator"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ticket",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "With NHA team"),
                    ("awaiting_integrator", "Awaiting your reply"),
                    ("closed", "Resolved"),
                ],
                default="open",
                max_length=20,
                verbose_name="Status",
            ),
        ),
        # Resolved tickets would otherwise show up in no inbox tab. They keep
        # their resolved_at, and update() leaves updated_at, so queue order holds.
        # Closed was already a valid status, so reversing has nothing to undo.
        migrations.RunPython(resolved_to_closed, migrations.RunPython.noop),
    ]
