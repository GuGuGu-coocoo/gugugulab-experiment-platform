from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """03D: a device-initiated named recovery has no human actor.

    Existing audit rows keep their actor; only the column becomes optional so a
    verified same-device continuation can be recorded instead of dropped.
    """

    dependencies = [('core', '0007_recovery_code')]

    operations = [
        migrations.AlterField(
            model_name='audit',
            name='actor',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='auth.user'),
        ),
    ]
