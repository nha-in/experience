from django.db import migrations
from django.db import models


def copy_entity_types(apps, schema_editor):
    """Take each organisation's type from its latest verification form, else from signup."""
    organisations = apps.get_model("organisations", "Organisation")
    review_items = apps.get_model("experiences", "ReviewItem")
    verifications = review_items.objects.filter(
        kind="organisation_verification",
    ).select_related("form", "selected_submission")
    for verification in verifications:
        submitted = verification.selected_submission.data if verification.selected_submission else {}
        entity_type = submitted.get("entity_type") or verification.form.metadata.get("entity_type", "")
        organisations.objects.filter(pk=verification.organisation_id).update(entity_type=entity_type)


class Migration(migrations.Migration):
    dependencies = [
        ("organisations", "0005_remove_legacy_features"),
        ("experiences", "0017_remove_productworkspace_registration_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="organisation",
            name="entity_type",
            field=models.CharField(blank=True, max_length=32, verbose_name="Type of entity"),
        ),
        migrations.RunPython(copy_entity_types, migrations.RunPython.noop),
    ]
