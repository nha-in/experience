from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0003_user_is_ohc_team'),
    ]

    operations = [
        # Renamed rather than dropped and re-added so existing team members keep
        # the flag.
        migrations.RenameField(
            model_name='user',
            old_name='is_ohc_team',
            new_name='is_nha_team',
        ),
        migrations.AlterField(
            model_name='user',
            name='is_nha_team',
            field=models.BooleanField(default=False, help_text='Works the support queue across all vendors and publishes events. Separate from staff status, which only controls Django admin access.', verbose_name='NHA team member'),
        ),
    ]
