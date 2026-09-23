"""Operation-binding tombstone and bounded preview scan progress (R00 §D).

Two additive fields, no backfill:

* ``DeletedSession.operation_hmac`` stores the dedicated-domain HMAC that binds
  the original operation to the instance/study/release binding, so only a retry
  of that exact operation can ever receive the permanent ``study_deleted``
  refusal; a fresh operation reusing the proof stays ``admission_unavailable``.
* ``StudyDeletion.preview_cursor`` persists the bounded cross-scope preview scan
  position so a killed cleanup resumes without re-scanning and never claims
  ``complete`` while a staged preview still references the deleted study.

Both defaults are empty/NULL on purpose: a tombstone written before this
migration keeps refusing by token/proof only (never by a guessed operation), and
an unfinished job restarts its preview scan from the beginning.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_study_deletion'),
    ]

    operations = [
        migrations.AddField(
            model_name='deletedSession',
            name='operation_hmac',
            field=models.CharField(blank=True, default='', db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name='studyDeletion',
            name='preview_cursor',
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
