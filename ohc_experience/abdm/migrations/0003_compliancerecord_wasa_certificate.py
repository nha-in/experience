# Hand-written: the WASA certificate has a life, not a date. wasa_date becomes
# wasa_issued_on and gains a matching expiry. RenameField rather than
# remove-and-add, so the dates already recorded survive as the issue date —
# makemigrations run non-interactively would have dropped the column.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('abdm', '0002_product_form_defaults'),
    ]

    operations = [
        migrations.RenameField(
            model_name='compliancerecord',
            old_name='wasa_date',
            new_name='wasa_issued_on',
        ),
        migrations.AlterField(
            model_name='compliancerecord',
            name='wasa_issued_on',
            field=models.DateField(blank=True, null=True, verbose_name='WASA certificate issued on'),
        ),
        migrations.AddField(
            model_name='compliancerecord',
            name='wasa_valid_until',
            field=models.DateField(blank=True, null=True, verbose_name='WASA valid until'),
        ),
    ]
