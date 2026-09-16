"""M3 (HIU) builds on M1, not M2.

NHA gates M3 on M1 alone — its own sandbox portal says "completed the M1
application before you proceed to M2 or M3", and no published document orders
M3 after M2. Requests created under the old catalog carry a stored dependency
on M2, so they are repointed here; nothing else about them changes.
"""

from django.db import migrations

MILESTONE = "m3"


def forwards(apps, schema_editor):
    _repoint(apps, "m2", "m1")


def backwards(apps, schema_editor):
    _repoint(apps, "m1", "m2")


def _repoint(apps, old_key, new_key):
    milestone = apps.get_model("experiences", "Milestone")
    dependency = apps.get_model("experiences", "ApplicationDependency")
    products = {}
    for product_id, key, application_id in milestone.objects.filter(
        product__workspace__experience_type="abdm",
    ).values_list("product_id", "key", "application_id"):
        products.setdefault(product_id, {})[key] = application_id
    for keys in products.values():
        application_id = keys.get(MILESTONE)
        old_id, new_id = keys.get(old_key), keys.get(new_key)
        if not application_id or not new_id:
            continue
        if old_id:
            dependency.objects.filter(
                application_id=application_id,
                depends_on_id=old_id,
            ).delete()
        dependency.objects.get_or_create(
            application_id=application_id,
            depends_on_id=new_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0017_remove_productworkspace_registration_status"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
