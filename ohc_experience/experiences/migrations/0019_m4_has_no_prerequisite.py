"""M4 (HFR Registration) requests no longer depend on M3."""

from django.db import migrations


def forwards(apps, schema_editor):
    dependency = apps.get_model("experiences", "ApplicationDependency")
    dependency.objects.filter(
        application__milestone__key="m4",
        application__milestone__product__workspace__experience_type="abdm",
    ).delete()


def backwards(apps, schema_editor):
    milestone = apps.get_model("experiences", "Milestone")
    dependency = apps.get_model("experiences", "ApplicationDependency")
    abdm = milestone.objects.filter(product__workspace__experience_type="abdm")
    m3_applications = dict(
        abdm.filter(key="m3").values_list("product_id", "application_id"),
    )
    for product_id, application_id in abdm.filter(key="m4").values_list(
        "product_id",
        "application_id",
    ):
        if product_id in m3_applications:
            dependency.objects.get_or_create(
                application_id=application_id,
                depends_on_id=m3_applications[product_id],
            )


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0018_m3_builds_on_m1"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
