"""Minimal retained study association for audit rows (R00 §D).

One additive nullable field, no backfill: the deletion cleanup fills
``Audit.study_uuid`` in the same bounded transaction that nulls the study
foreign key, so every retained action keeps its own study association. A row
already detached before this migration keeps NULL (unknown) instead of a value
guessed from its target or a reused study name.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_deletion_operation_binding'),
    ]

    operations = [
        migrations.AddField(
            model_name='audit',
            name='study_uuid',
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
