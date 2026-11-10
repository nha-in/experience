from django.db import migrations


class Migration(migrations.Migration):
    """allauth.mfa left the installed apps; its empty table still references
    users and stops a flush. Its migration records go too, so the app would
    create the table again if it ever came back."""

    dependencies = [
        ("users", "0006_user_phone_verified"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "DROP TABLE IF EXISTS mfa_authenticator",
                "DELETE FROM django_migrations WHERE app = 'mfa'",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
