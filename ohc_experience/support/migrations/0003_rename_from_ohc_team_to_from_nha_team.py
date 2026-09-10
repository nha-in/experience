from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('support', '0002_remove_legacy_features'),
    ]

    operations = [
        migrations.RenameField(
            model_name='ticketmessage',
            old_name='from_ohc_team',
            new_name='from_nha_team',
        ),
        migrations.AlterField(
            model_name='ticket',
            name='assignee',
            field=models.ForeignKey(blank=True, limit_choices_to={'is_nha_team': True}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tickets_assigned', to=settings.AUTH_USER_MODEL, verbose_name='Assignee'),
        ),
    ]
