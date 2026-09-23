"""Additive v2 policy storage and stable principals.

Every operation is additive and conservative:

- ``Instance.authorization_version`` starts at 1 for every existing database, so
  no route silently adopts the v2 defaults; only an Owner-confirmed enablement
  writes 2.
- ``AccountProfile`` gains the v2 override fields (empty) and ``policy_version``
  (the vocabulary revision of those fields), so no existing permission changes.
- one ``Principal`` is created per existing account, and every existing audit
  row is backfilled with that stable reference; the user reference becomes
  SET_NULL so history survives a later account deletion.
- ``Study.creator_principal`` stays NULL: a legacy creator is never guessed and
  is instead reported as unknown by the enablement preview.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_principals_and_backfill_audit(apps, schema_editor):
    """One Principal per existing account; audit rows point at the stable id."""
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Principal = apps.get_model('core', 'Principal')
    Audit = apps.get_model('core', 'Audit')
    for user_id in User.objects.order_by('pk').values_list('pk', flat=True):
        principal, _ = Principal.objects.get_or_create(user_id=user_id)
        Audit.objects.filter(actor_id=user_id, actor_principal__isnull=True).update(
            actor_principal_id=principal.pk)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_release_artifact'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='instance',
            name='authorization_version',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='accountprofile',
            name='future_study_actions',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='accountprofile',
            name='platform_overrides',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='accountprofile',
            name='policy_version',
            field=models.PositiveIntegerField(default=2),
        ),
        migrations.AddField(
            model_name='accountprofile',
            name='study_overrides',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.CreateModel(
            name='Principal',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='gep_principal', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddField(
            model_name='study',
            name='creator_principal',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.principal'),
        ),
        migrations.AddField(
            model_name='audit',
            name='actor_principal',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.principal'),
        ),
        migrations.AlterField(
            model_name='audit',
            name='actor',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(create_principals_and_backfill_audit, migrations.RunPython.noop),
    ]
