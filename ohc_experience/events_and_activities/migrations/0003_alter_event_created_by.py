from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    replaces = [('events', '0003_alter_event_created_by')]

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('events_and_activities', '0002_event_category_event_program'),
    ]

    operations = [
        migrations.AlterField(
            model_name='event',
            name='created_by',
            field=models.ForeignKey(blank=True, limit_choices_to={'is_nha_team': True}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='events_created', to=settings.AUTH_USER_MODEL, verbose_name='Created by'),
        ),
    ]
