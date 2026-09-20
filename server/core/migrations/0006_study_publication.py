from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Additive 03C publication fields.

    All existing studies keep private policy and a NULL current release: the
    migration never guesses the first or latest release and never rewrites a
    release config, session binding or exported snapshot.
    """

    dependencies = [('core', '0005_permission_preview')]

    operations = [
        migrations.AddField('study', 'public', models.BooleanField(default=False)),
        migrations.AddField('study', 'public_summary', models.CharField(blank=True, max_length=280)),
        migrations.AddField('study', 'public_duration', models.CharField(blank=True, max_length=80)),
        migrations.AddField('study', 'public_device_requirements', models.CharField(blank=True, max_length=160)),
        migrations.AddField('study', 'show_closed_summary', models.BooleanField(default=False)),
        migrations.AddField('study', 'current_release', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.release')),
        migrations.AddField('study', 'revision', models.PositiveIntegerField(default=0)),
    ]
