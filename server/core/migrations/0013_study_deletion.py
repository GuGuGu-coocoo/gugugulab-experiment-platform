"""Additive study-deletion lifecycle (R00 §D).

Three additive pieces, no backfill: every existing study keeps
``lifecycle='active'`` and no deletion job or tombstone is guessed for it. A
deletion mark is a new explicit action that writes the durable job row, the
irreversible session tombstones and the lifecycle transition in one
transaction; migration only creates the schema.
"""
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_invitation_identity'),
    ]

    operations = [
        migrations.AddField(
            model_name='study',
            name='lifecycle',
            field=models.CharField(default='active', max_length=16),
        ),
        migrations.CreateModel(
            name='StudyDeletion',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('study_uuid', models.UUIDField(unique=True)),
                ('requested_at', models.DateTimeField(auto_now_add=True)),
                ('state', models.CharField(default='marked', max_length=16)),
                ('cursor', models.PositiveIntegerField(default=0)),
                ('file_manifest', models.JSONField(blank=True, default=list)),
                ('counts', models.JSONField(blank=True, default=dict)),
                ('error_code', models.CharField(blank=True, default='', max_length=64)),
                ('principal', models.ForeignKey(blank=True, null=True,
                                                on_delete=django.db.models.deletion.PROTECT,
                                                related_name='+', to='core.principal')),
            ],
        ),
        migrations.CreateModel(
            name='DeletedSession',
            fields=[
                ('session_uuid', models.UUIDField(primary_key=True, serialize=False)),
                ('study_uuid', models.UUIDField(db_index=True)),
                ('release_uuid', models.UUIDField()),
                ('build_uuid', models.UUIDField()),
                ('instance_uuid', models.UUIDField()),
                ('token_hmac', models.CharField(db_index=True, max_length=64)),
                ('proof_hmac', models.CharField(db_index=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
