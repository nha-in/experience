from django.db import migrations
from django.db import models
from django.db.models import F


def track_to_category(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    ticket.objects.update(category=F("track"))


def category_to_track(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    ticket.objects.update(track=F("category"))


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0007_remove_resolved_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ticket",
            name="category",
            field=models.CharField(blank=True, max_length=100, verbose_name="Category"),
        ),
        migrations.RunPython(track_to_category, category_to_track),
        migrations.RemoveField(
            model_name="ticket",
            name="track",
        ),
    ]
