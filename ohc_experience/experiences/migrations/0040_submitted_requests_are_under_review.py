"""A submitted request is under review; "new" only meant nobody was assigned.

Whether a reviewer has picked a request up is its assignee, so the requests
still marked new move to under review. Going back leaves them there, which the
old statuses allow too.
"""

from django.db import migrations
from django.db import models


def forwards(apps, schema_editor):
    review = apps.get_model("experiences", "ReviewItem")
    review.objects.filter(status="new").update(status="in_review")


class Migration(migrations.Migration):
    dependencies = [
        ("experiences", "0039_split_nhcx_into_roles"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="reviewitem",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "In progress"),
                    ("in_review", "Under review"),
                    ("query_raised", "Query raised"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="draft",
                max_length=24,
            ),
        ),
    ]
