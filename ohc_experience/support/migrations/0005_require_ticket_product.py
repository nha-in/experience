import django.db.models.deletion
from django.db import migrations
from django.db import models


def require_product(apps, schema_editor):
    ticket = apps.get_model("support", "Ticket")
    missing = list(
        ticket.objects.filter(product__isnull=True).values_list("reference", flat=True),
    )
    if missing:
        msg = f"Assign a product to these tickets before migrating: {', '.join(missing)}"
        raise RuntimeError(msg)


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0004_ticket_product_track"),
    ]

    operations = [
        migrations.RunPython(require_product, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="ticket",
            name="product",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tickets",
                to="experiences.product",
                verbose_name="Product",
            ),
        ),
    ]
