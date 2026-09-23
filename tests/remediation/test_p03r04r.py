"""P03R04R evidence: minimal retained audit association, real per-file batch
limits, resumable file-only cleanup and the deletion-module consistency fixes.

Every expected value comes from the R04R contract (``remediation_contracts.md``
§D and the 2026-09-24 guarded review) and the fixed synthetic world imported
from ``test_p03r04``; nothing is read from the implementation under test.

1. every retained action of the deleted study keeps its minimal study UUID and
   its actor/action/time, raw before/after are cleared, and other studies and
   account-scoped audits stay untouched;
2. one batch removes at most ``batch_size`` real files (the export ZIP and every
   interrupted temp file or link is one unit), an unfinished spool stays in the
   persisted manifest, and re-runs advance to completion without a false
   ``complete``; the old exact-name manifest still works and unknown files,
   other exports and symlink targets are never touched;
3. a file-only job (database already clean) continues while files make real
   progress and completes in one run; a mid-spool failure persists ``failed``
   and a re-run is idempotent;
4. the maintenance command rejects illegal ``--batch-size``/``--max-batches``
   with a non-zero exit (real exit code in the standalone probe), and the
   minimal status page never renders the study title.
"""
import json
import sys
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from core import deletion, ui
from core.access import ensure_principal
from core.models import Audit, Export, StudyDeletion

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures
from test_p03r04 import OPERATOR_PASSWORD, sign_in, world  # noqa: E402


def mark(world):
    deletion.mark_study_deletion(world.operator, world.data, OPERATOR_PASSWORD)
    return StudyDeletion.objects.get(study_uuid=world.data.pk)


def run_to_complete(row, batch_size=deletion.DEFAULT_BATCH_SIZE):
    while StudyDeletion.objects.get(pk=row.pk).state != 'complete':
        deletion.run_cleanup(row.pk, batch_size=batch_size)
    return StudyDeletion.objects.get(pk=row.pk)


def file_only_job(row, manifest):
    """The durable file-only state after the database rows are already gone."""
    row.state = 'cleaning'
    row.file_manifest = manifest
    row.save(update_fields=['state', 'file_manifest'])
    return row


def write_spool(root, export_id, temp_count):
    paths = [root / f'{export_id}.zip']
    paths += [root / f'.{export_id}.{index:016x}.tmp' for index in range(temp_count)]
    for path in paths:
        path.write_bytes(b'SYNTHETIC spool bytes')
    return paths


# --- 1. minimal retained audit association -----------------------------------

def test_retained_actions_keep_minimal_study_uuid(world, evidence):
    """Every action of the deleted study keeps its own study UUID, actor
    principal, action, target and time after the foreign key is nulled; raw
    before/after are cleared; other studies and account audits are untouched."""
    operator = world.operator
    principal = ensure_principal(operator)
    actions = [
        ('synthetic.session_review', str(world.session.pk)),
        ('synthetic.participant_review', str(world.data.pk)),
        ('synthetic.build_review', str(world.build.pk)),
    ]
    created = []
    for action, target in actions:
        created.append(Audit.objects.create(
            study=world.data, actor=operator, actor_principal=principal, action=action, target=target,
            before={'private': f'SYNTHETIC_private_before_{action}'},
            after={'private': f'SYNTHETIC_private_after_{action}'}))
    other_study = Audit.objects.create(
        study=world.empty, actor=operator, actor_principal=principal, action='synthetic.other_review',
        target=str(world.empty.pk), before={'public': True}, after={'public': False})
    account_scoped = Audit.objects.create(
        study=None, actor=operator, actor_principal=principal, action='synthetic.account_review',
        target=str(operator.pk), before={'private': 'SYNTHETIC_private_account_before'},
        after={'private': 'SYNTHETIC_private_account_after'})

    job = mark(world)
    # A batch size of one keeps the audit step bounded and resumable too.
    row = run_to_complete(job, batch_size=1)
    assert row.state == 'complete' and row.file_manifest == []

    for entry in created:
        entry.refresh_from_db()
        assert entry.study_id is None, entry.action
        assert entry.study_uuid == world.data.pk, entry.action
        assert entry.before is None and entry.after is None, entry.action
        assert entry.actor_id == operator.pk and entry.actor_principal_id == principal.pk, entry.action
        assert entry.action in dict(actions) and entry.target == dict(actions)[entry.action]
        assert entry.created_at is not None

    mark_audit = Audit.objects.get(action='study.deletion_marked', target=str(world.data.pk))
    assert mark_audit.study_id is None and mark_audit.study_uuid == world.data.pk
    assert mark_audit.before is None and mark_audit.after == {'counts': row.counts}
    assert mark_audit.actor_principal_id == principal.pk

    other_study.refresh_from_db()
    assert other_study.study_id == world.empty.pk and other_study.study_uuid is None
    assert other_study.before == {'public': True} and other_study.after == {'public': False}
    account_scoped.refresh_from_db()
    assert account_scoped.study_id is None and account_scoped.study_uuid is None
    assert account_scoped.before == {'private': 'SYNTHETIC_private_account_before'}
    assert account_scoped.after == {'private': 'SYNTHETIC_private_account_after'}

    # No raw private value of the deleted study survives in any of its rows.
    for entry in Audit.objects.filter(study_uuid=world.data.pk):
        saved = json.dumps(Audit.objects.filter(pk=entry.pk).values().get(), default=str)
        assert 'SYNTHETIC_private' not in saved, saved
    evidence('retained_audit_uuid.json', {'actions': len(created), 'state': row.state,
                                          'other_study_untouched': True, 'account_audit_untouched': True})


# --- 2. real per-file batch limit and resumable spool ------------------------

def test_file_batches_remove_at_most_batch_size_and_resume(world, tmp_path, monkeypatch, evidence):
    """With ``batch_size=1`` one batch really removes at most one file, the
    unfinished spool ownership stays in the persisted manifest, ``max_batches=1``
    never reports complete, and later runs advance to completion while other
    exports and unknown files survive."""
    data_root = tmp_path / 'data'
    root = data_root / 'exports'
    root.mkdir(parents=True)
    monkeypatch.setattr(settings, 'DATA_DIR', data_root)
    row = run_to_complete(mark(world))

    first_export = str(world.item.pk)
    second_export = str(uuid.uuid4())
    owned = write_spool(root, first_export, 3) + write_spool(root, second_export, 2)
    other_export = str(uuid.uuid4())
    unrelated = write_spool(root, other_export, 1)
    unknown = root / 'notes.txt'
    unknown.write_bytes(b'SYNTHETIC unknown file')
    victim = tmp_path / 'victim.bin'
    victim.write_bytes(b'SYNTHETIC victim bytes that must survive')
    planted = root / f'.{first_export}.fedcba9876543210.tmp'
    planted.symlink_to(victim)
    owned.append(planted)

    file_only_job(row, [{'root': 'exports', 'export': first_export},
                        {'root': 'exports', 'export': second_export}])

    first = deletion.run_cleanup(row.pk, batch_size=1, max_batches=1)
    assert first['state'] != 'complete', first
    assert StudyDeletion.objects.get(pk=row.pk).file_manifest, 'unfinished spool ownership was lost'
    removed_first = sum(not (path.is_symlink() or path.exists()) for path in owned)
    assert removed_first <= 1, f'--batch-size 1 removed {removed_first} files'

    second = deletion.run_cleanup(row.pk, batch_size=2, max_batches=1)
    assert second['state'] != 'complete', second
    removed_second = sum(not (path.is_symlink() or path.exists()) for path in owned)
    assert removed_second - removed_first <= 2, f'--batch-size 2 removed {removed_second - removed_first} files'

    previous = removed_second
    result = second
    for _ in range(len(owned) + 2):
        result = deletion.run_cleanup(row.pk, batch_size=1, max_batches=1)
        current = sum(not (path.is_symlink() or path.exists()) for path in owned)
        assert current - previous <= 1, f'--batch-size 1 removed {current - previous} files'
        previous = current
        if result['state'] == 'complete':
            break
    assert result['state'] == 'complete', result
    assert previous == len(owned)
    assert not StudyDeletion.objects.get(pk=row.pk).file_manifest
    assert victim.read_bytes() == b'SYNTHETIC victim bytes that must survive'
    for path in unrelated:
        assert path.read_bytes() == b'SYNTHETIC spool bytes'
    assert unknown.read_bytes() == b'SYNTHETIC unknown file'
    evidence('file_batch_limit.json', {'batch_1_removed': removed_first, 'batch_2_removed': removed_second - removed_first,
                                       'files': len(owned), 'final_state': result['state'],
                                       'unrelated_kept': len(unrelated) + 1})


def test_old_exact_name_manifest_entry_still_works(world, tmp_path, monkeypatch, evidence):
    """The pre-existing exact-name export manifest entry (``{'root': 'exports',
    'name': '<uuid>.zip'}``) is still honored within the same file budget."""
    data_root = tmp_path / 'data'
    root = data_root / 'exports'
    root.mkdir(parents=True)
    monkeypatch.setattr(settings, 'DATA_DIR', data_root)
    row = run_to_complete(mark(world))

    export_id = str(world.item.pk)
    old_style = root / f'{export_id}.zip'
    old_style.write_bytes(b'SYNTHETIC old manifest zip')
    untouched = root / f'.{export_id}.0123456789abcdef.tmp'
    untouched.write_bytes(b'SYNTHETIC tmp is not named by the old entry')
    file_only_job(row, [{'root': 'exports', 'name': f'{export_id}.zip'}])

    result = deletion.run_cleanup(row.pk, batch_size=1)
    assert result['state'] == 'complete', result
    assert not old_style.exists()
    assert untouched.read_bytes() == b'SYNTHETIC tmp is not named by the old entry'
    evidence('old_manifest_compat.json', {'state': result['state'], 'exact_name_removed': True,
                                          'other_file_kept': True})


# --- 3. file-only progress and failure resume --------------------------------

def test_file_only_job_continues_on_real_progress(world, tmp_path, monkeypatch, evidence):
    """A job whose database cursor cannot move still runs to completion while its
    file step really removes files, instead of stopping after one batch."""
    data_root = tmp_path / 'data'
    root = data_root / 'exports'
    root.mkdir(parents=True)
    monkeypatch.setattr(settings, 'DATA_DIR', data_root)
    row = run_to_complete(mark(world))

    owned = write_spool(root, str(world.item.pk), 4)
    file_only_job(row, [{'root': 'exports', 'export': str(world.item.pk)}])
    result = deletion.run_cleanup(row.pk, batch_size=2)
    assert result['state'] == 'complete', result
    assert result['batches'] >= 3, result
    assert not any(path.exists() for path in owned)
    evidence('file_only_progress.json', {'batches': result['batches'], 'state': result['state'],
                                         'files': len(owned)})


def test_file_failure_persists_and_rerun_is_idempotent(world, tmp_path, monkeypatch, evidence):
    """A file removal failure persists ``failed`` with the error code and keeps
    the remaining spool ownership; a later run resumes idempotently and never
    touches another export's files."""
    data_root = tmp_path / 'data'
    root = data_root / 'exports'
    root.mkdir(parents=True)
    monkeypatch.setattr(settings, 'DATA_DIR', data_root)
    row = run_to_complete(mark(world))

    export_id = str(world.item.pk)
    owned = write_spool(root, export_id, 3)
    other_export = str(uuid.uuid4())
    unrelated = write_spool(root, other_export, 1)
    file_only_job(row, [{'root': 'exports', 'export': export_id}])

    real_unlink = deletion._unlink_private
    calls = {'count': 0}

    def flaky_unlink(path):
        calls['count'] += 1
        if calls['count'] == 2:
            raise OSError('synthetic file failure')
        real_unlink(path)

    with monkeypatch.context() as patch:
        patch.setattr(deletion, '_unlink_private', flaky_unlink)
        failed = deletion.run_cleanup(row.pk, batch_size=5)
    assert failed['state'] == 'failed' and failed['error_code'] == 'OSError', failed
    assert failed['files_remaining'] >= 1
    assert sum(not path.exists() for path in owned) == 1, 'a failure must not lose already removed files'
    stored = StudyDeletion.objects.get(pk=row.pk)
    assert stored.state == 'failed' and stored.file_manifest

    result = deletion.run_cleanup(row.pk, batch_size=5)
    assert result['state'] == 'complete' and result['files_remaining'] == 0, result
    assert not any(path.exists() for path in owned)
    for path in unrelated:
        assert path.read_bytes() == b'SYNTHETIC spool bytes'
    evidence('file_failure_resume.json', {'failed': failed['state'], 'error_code': failed['error_code'],
                                          'resumed': result['state'], 'unrelated_kept': len(unrelated)})


# --- 4. command parameters and minimal status page ---------------------------

def test_cleanup_command_rejects_illegal_parameters(world, evidence):
    """Illegal ``--batch-size``/``--max-batches`` values raise instead of
    printing an error and succeeding, and change no job state."""
    row = mark(world)
    for options in ({'batch_size': 0}, {'batch_size': -1}, {'max_batches': -1}):
        with pytest.raises(CommandError):
            call_command('cleanup_deleted_studies', **options)
    stored = StudyDeletion.objects.get(pk=row.pk)
    assert stored.state == 'marked' and stored.cursor == 0
    assert Export.objects.filter(study_id=world.data.pk).exists(), 'illegal parameters must not run any cleanup'
    evidence('illegal_parameters.json', {'batch_size_zero': 'CommandError', 'batch_size_negative': 'CommandError',
                                         'max_batches_negative': 'CommandError', 'state': stored.state})


def test_status_page_hides_study_title_while_study_exists(world, evidence):
    """The minimal deletion status page shows only the durable job facts: no
    study title, even while the Study row still exists."""
    deletion.mark_study_deletion(world.operator, world.data, OPERATOR_PASSWORD)
    for who in (world.operator, world.owner):
        page = sign_in(who).get(f'/studies/{world.data.id}').content.decode()
        assert 'data-deletion-status="marked"' in page
        assert 'data-deletion-state="marked"' in page
        assert ui.tr('zh', 'deletion_status_title') in page
        assert world.data.title not in page, f'the study title leaked to {who.username}'
    evidence('status_page_minimal.json', {'title_hidden': True, 'state': 'marked'})
