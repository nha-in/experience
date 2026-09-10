from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0013_drop_inherited_milestone_selections"),
        ("support", "0004_ticket_product_track"),
    ]

    operations = [
        migrations.DeleteModel(name="TicketContext"),
    ]
