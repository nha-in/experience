from django.db import migrations
from django.db import models


def to_integrator(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    ticket.objects.filter(status="awaiting_vendor").update(status="awaiting_integrator")


def to_vendor(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    ticket.objects.filter(status="awaiting_integrator").update(status="awaiting_vendor")


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0005_require_ticket_product"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ticket",
            name="status",
            field=models.CharField(
                choices=[
                    ("open", "Open"),
                    ("awaiting_integrator", "Awaiting your reply"),
                    ("resolved", "Resolved"),
                    ("closed", "Closed"),
                ],
                default="open",
                max_length=20,
                verbose_name="Status",
            ),
        ),
        # Stored tickets still say awaiting_vendor and would drop out of every
        # open-ticket filter. update() leaves updated_at, so queue order holds.
        migrations.RunPython(to_integrator, to_vendor),
    ]
