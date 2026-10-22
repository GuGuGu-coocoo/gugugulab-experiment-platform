"""Add public routing labels without rewriting any study/session/record data."""
from django.db import migrations, models


def register_existing(apps, schema_editor):
    Origin = apps.get_model('core', 'BrowserOrigin')
    Build = apps.get_model('core', 'Build')
    Release = apps.get_model('core', 'Release')
    database = schema_editor.connection.alias
    sources = (
        ('p', Build.objects.using(database).filter(descriptor__platform='godot_web')),
        ('r', Release.objects.using(database).filter(approved=True, build__descriptor__platform='godot_web')),
    )
    for kind, rows in sources:
        batch = []
        for identifier in rows.values_list('pk', flat=True).iterator(chunk_size=500):
            batch.append(Origin(label=f'{kind}-{identifier.hex}'))
            if len(batch) == 500:
                Origin.objects.using(database).bulk_create(batch, ignore_conflicts=True)
                batch = []
        if batch:
            Origin.objects.using(database).bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [('core', '0015_audit_study_uuid')]
    operations = [
        migrations.CreateModel(name='BrowserOrigin', fields=[
            ('label', models.CharField(max_length=34, primary_key=True, serialize=False)),
        ]),
        migrations.RunPython(register_existing, migrations.RunPython.noop),
    ]
