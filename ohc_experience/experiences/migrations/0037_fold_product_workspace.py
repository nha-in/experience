"""`ProductWorkspace` folds into `Product`.

Every product has had exactly one workspace since registration started
creating both, so its five columns belong on the product. A product found
without one gets a reference minted the way registration mints them.
"""

from django.db import migrations
from django.db import models
from django.utils import timezone


def forwards(apps, schema_editor):
    Product = apps.get_model("experiences", "Product")
    ProductWorkspace = apps.get_model("experiences", "ProductWorkspace")
    for workspace in ProductWorkspace.objects.all():
        Product.objects.filter(pk=workspace.product_id).update(
            reference=workspace.reference,
            experience_type=workspace.experience_type,
            solution_type=workspace.solution_type,
            applied_milestones=workspace.applied_milestones,
            registered_at=workspace.registered_at,
        )
    year = timezone.localdate().year
    for product in Product.objects.filter(reference__isnull=True):
        product.reference = f"PRD-{year}-{product.pk:05d}"
        product.save(update_fields=["reference"])


def backwards(apps, schema_editor):
    Product = apps.get_model("experiences", "Product")
    ProductWorkspace = apps.get_model("experiences", "ProductWorkspace")
    for product in Product.objects.all():
        ProductWorkspace.objects.create(
            product_id=product.pk,
            reference=product.reference,
            experience_type=product.experience_type,
            solution_type=product.solution_type,
            applied_milestones=product.applied_milestones,
            registered_at=product.registered_at,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0036_retitle_phr_milestones"),
        # Data migrations elsewhere still read the workspace through its product.
        ("organisations", "0009_rename_sent_back_to_rejected"),
        ("support", "0012_others_category_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="reference",
            field=models.CharField(max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="experience_type",
            field=models.CharField(default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="product",
            name="solution_type",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="product",
            name="applied_milestones",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="product",
            name="registered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="product",
            name="reference",
            field=models.CharField(blank=True, max_length=32, null=True, unique=True),
        ),
        migrations.DeleteModel(name="ProductWorkspace"),
    ]
