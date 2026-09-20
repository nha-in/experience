"""Drop the reachability probe"s readings.

The probe ran from our own task, which has no internet egress, so every result
was the same failure whatever the integrator"s endpoint did.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0029_merge_bridge_url_into_callback_url"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="productcredential",
            name="consecutive_failures",
        ),
        migrations.RemoveField(
            model_name="productcredential",
            name="last_checked_at",
        ),
        migrations.RemoveField(
            model_name="productcredential",
            name="last_error",
        ),
        migrations.RemoveField(
            model_name="productcredential",
            name="last_latency_ms",
        ),
        migrations.RemoveField(
            model_name="productcredential",
            name="last_status",
        ),
    ]
