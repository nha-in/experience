"""UHI1 requests build on M2, which builds on M1, so UHI needs both."""

from django.db import migrations


def forwards(apps, schema_editor):
    _repoint(apps, "m1", "m2")


def backwards(apps, schema_editor):
    _repoint(apps, "m2", "m1")


def _repoint(apps, old_key, new_key):
    milestone = apps.get_model("experiences", "Milestone")
    dependency = apps.get_model("experiences", "ApplicationDependency")
    products = {}
    for product_id, key, application_id in milestone.objects.filter(
        product__workspace__experience_type="abdm",
    ).values_list("product_id", "key", "application_id"):
        products.setdefault(product_id, {})[key] = application_id
    for keys in products.values():
        uhi_id, old_id, new_id = keys.get("uhi1"), keys.get(old_key), keys.get(new_key)
        if not uhi_id or not new_id:
            continue
        dependency.objects.filter(application_id=uhi_id, depends_on_id=old_id).delete()
        dependency.objects.get_or_create(application_id=uhi_id, depends_on_id=new_id)


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0020_remove_product_product_type"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
