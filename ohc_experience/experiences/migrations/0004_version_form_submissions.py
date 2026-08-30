from django.db import migrations, models


def backfill_field_schema(apps, _schema_editor):
    submission_model = apps.get_model("experiences", "ApplicationFormSubmission")
    for submission in submission_model.objects.iterator():
        submission.field_schema = [
            {
                "key": key,
                "label": key.replace("_", " ").title(),
                "type": "",
                "required": False,
                "choices": [],
            }
            for key in submission.data
        ]
        submission.save(update_fields=["field_schema"])


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0003_alter_applicationformsubmission_options_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="applicationformsubmission",
            options={
                "ordering": ["form_key", "-submission_number", "-revision"],
            },
        ),
        migrations.AddField(
            model_name="applicationformsubmission",
            name="field_schema",
            field=models.JSONField(
                blank=True,
                default=list,
                verbose_name="Field schema",
            ),
        ),
        migrations.RunPython(
            backfill_field_schema,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="applicationformsubmission",
            name="unique_experience_form_submission_number",
        ),
        migrations.AddConstraint(
            model_name="applicationformsubmission",
            constraint=models.UniqueConstraint(
                fields=(
                    "application",
                    "form_key",
                    "submission_number",
                    "revision",
                ),
                name="unique_experience_form_submission_revision",
            ),
        ),
    ]
