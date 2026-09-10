import django.db.models.deletion
from django.db import migrations
from django.db import models


def copy_from_context(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    context = apps.get_model("experiences", "TicketContext")
    for ticket_id, product_id, track in context.objects.values_list(
        "ticket_id",
        "product_id",
        "track",
    ):
        ticket.objects.filter(pk=ticket_id).update(product_id=product_id, track=track)


def copy_to_context(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    context = apps.get_model("experiences", "TicketContext")
    context.objects.bulk_create(
        context(ticket_id=pk, product_id=product_id, track=track)
        for pk, product_id, track in ticket.objects.filter(
            product__isnull=False,
        ).values_list("pk", "product_id", "track")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0013_drop_inherited_milestone_selections"),
        ("support", "0003_rename_from_ohc_team_to_from_nha_team"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tickets",
                to="experiences.product",
                verbose_name="Product",
            ),
        ),
        migrations.AddField(
            model_name="ticket",
            name="track",
            field=models.CharField(blank=True, max_length=100, verbose_name="Track"),
        ),
        migrations.RunPython(copy_from_context, copy_to_context),
    ]
