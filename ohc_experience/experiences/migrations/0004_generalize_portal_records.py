from django.db import migrations
from django.db import models


def move_content_types(apps, schema_editor):
    content_types = apps.get_model("contenttypes", "ContentType")
    permissions = apps.get_model("auth", "Permission")
    for content_type in content_types.objects.filter(app_label="sandbox"):
        if content_type.model == "sandboxcredential":
            for permission in permissions.objects.filter(content_type=content_type):
                permission.codename = permission.codename.replace("sandboxcredential", "productcredential")
                permission.save(update_fields=["codename"])
            content_type.model = "productcredential"
        content_type.app_label = "experiences"
        content_type.save(update_fields=["app_label", "model"])
    periodic_tasks = apps.get_model("django_celery_beat", "PeriodicTask")
    for task in periodic_tasks.objects.filter(task__startswith="ohc_experience.sandbox.tasks."):
        task.task = task.task.replace("ohc_experience.sandbox.tasks.", "ohc_experience.experiences.tasks.")
        task.save(update_fields=["task"])


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0003_adopt_portal_models"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RenameModel("SandboxCredential", "ProductCredential"),
        migrations.RenameField("productworkspace", "sandbox_id", "reference"),
        migrations.AddField(
            model_name="productworkspace",
            name="experience_type",
            field=models.CharField(max_length=100, default="abdm"),
            preserve_default=False,
        ),
        migrations.RunPython(move_content_types, migrations.RunPython.noop),
    ]
