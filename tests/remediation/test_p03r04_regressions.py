"""P03R04 regression evidence for the 2026-09-24 checkpoint boundaries.

Every expected value comes from the R00 §D contract and the fixed synthetic
world imported from ``test_p03r04``; nothing is read from the implementation
under test. The four boundaries the Supervisor probe found failing are
replicated here with fixed expectations, plus the HTTP entry points and the
real multi-process races (the standalone probe adds the pre-check/mark/commit
barrier and the killed-generation spool cleanup):

1. publication policy, current release, roster preview/commit and the
   permission matrix all refuse inside their final transaction after the mark;
2. only the original operation with the exact binding gets ``study_deleted`` -
   a fresh operation, a mismatched release/build or an unknown study stays
   ``admission_unavailable``, before and after the cleanup;
3. the cross-scope preview scan is bounded and resumable, clears every staged
   preview that references the study beyond the first batch and preserves
   unrelated previews;
4. the owned export spool (``<id>.zip`` and ``.<id>.<16 hex>.tmp``) is removed
   while unknown names, other exports and symlink targets survive.
"""
import json
import os
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.utils import timezone

from core import deletion, importers, permissions, publication
from core.models import (AccountProfile, Export, Grant, Instance, PermissionPreview,
                         Study, StudyDeletion)
from core.protocol import Rejected

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures
from test_p03r04 import (OPERATOR_ACTIONS, OPERATOR_PASSWORD, OWNER_PASSWORD,  # noqa: E402
                         assert_refusal, body_bytes, sign_in, world)


def mark(world):
    deletion.mark_study_deletion(world.operator, world.data, OPERATOR_PASSWORD)
    return StudyDeletion.objects.get(study_uuid=world.data.pk)


def run_to_complete(row):
    while StudyDeletion.objects.get(pk=row.pk).state != 'complete':
        deletion.run_cleanup(row.pk)
    return StudyDeletion.objects.get(pk=row.pk)


# --- 1. every study write refuses inside its final transaction ---------------

def test_study_writes_refused_after_mark(world, evidence):
    """Policy, current release, roster preview/commit and the permission matrix
    all refuse with ``study_deleted`` and write nothing once the mark committed,
    and the GUI POST is refused at the HTTP boundary too."""
    operator, owner, data = world.operator, world.owner, world.data
    revision = data.revision
    roster_preview = importers.preview_roster(operator, data, text='r04-regression\n')
    matrix_preview = permissions.preview_matrix(owner, {
        'user_id': str(operator.pk), 'study_id': str(data.pk), 'visibility': '1'})
    assert roster_preview.staged is not None and matrix_preview.staged is not None

    mark(world)

    refused = {}
    with pytest.raises(Rejected) as error:
        publication.update_policy(operator, data, revision, {'public': True})
    refused['update_policy'] = error.value.code
    with pytest.raises(Rejected) as error:
        publication.select_current_release(operator, data, revision, str(world.release.id))
    refused['select_current_release'] = error.value.code
    with pytest.raises(Rejected) as error:
        importers.preview_roster(operator, data, text='r04-after\n')
    refused['preview_roster'] = error.value.code
    with pytest.raises(Rejected) as error:
        importers.commit_roster(operator, OPERATOR_PASSWORD, roster_preview.pk, data.pk)
    refused['commit_roster'] = error.value.code
    with pytest.raises(Rejected) as error:
        permissions.preview_matrix(owner, {'user_id': str(operator.pk),
                                           'study_id': str(data.pk), 'visibility': '1'})
    refused['preview_matrix'] = error.value.code
    with pytest.raises(Rejected) as error:
        permissions.commit_matrix(owner, OWNER_PASSWORD, matrix_preview.pk)
    refused['commit_matrix'] = error.value.code
    assert set(refused.values()) == {'study_deleted'}, refused

    # Nothing was written: policy, release, revision, grants and overrides.
    data.refresh_from_db()
    assert data.public is False and data.recruitment == 'open' and data.revision == revision
    assert data.current_release_id == world.release.id
    assert Grant.objects.filter(study=data, user=operator).count() == len(OPERATOR_ACTIONS)
    assert AccountProfile.objects.get(user=operator).study_overrides == {}
    assert not PermissionPreview.objects.filter(pk=matrix_preview.pk, consumed=True).exists()

    # The HTTP GUI entry renders only the minimal deletion status, and the POST
    # never reaches a study write.
    response = sign_in(operator).post(f'/studies/{data.id}', {'op': 'recruitment', 'state': 'closed'})
    assert response.status_code == 200
    page = response.content.decode()
    assert 'data-deletion-status="marked"' in page and 'data-delete-form="1"' not in page
    data.refresh_from_db()
    assert data.recruitment == 'open' and data.lifecycle == 'deleting'
    evidence('write_entry_points.json', {'refused': refused, 'policy_unchanged': True,
                                         'http_status': response.status_code})


# --- 2. operation-binding admission matrix -----------------------------------

def test_admission_operation_binding_before_and_after_cleanup(world, evidence):
    """Only the original operation + proof + exact instance/study/release/build
    is the permanent stop; a fresh operation, a mismatched release or build and
    an unknown study all stay the same un-enumerable refusal - before and after
    the rows are gone."""
    operator = world.operator
    client = sign_in(operator)
    instance = Instance.objects.get(pk=1)
    session, proof = world.session, 'p' * 48
    operation, release = session.operation, world.release.id
    build = world.build.id
    instance_id = str(instance.instance_id)
    study_id = str(world.data.id)

    def post(operation_id, release_id, build_id=None):
        payload = {'operation_id': str(operation_id), 'instance_id': instance_id, 'study_id': study_id,
                   'release_id': str(release_id), 'proof': proof, 'participant_code': None}
        if build_id is not None:
            payload['build_id'] = str(build_id)
        return client.post('/v1/participant/sessions', payload, content_type='application/json')

    mark(world)
    before = {
        'original': post(operation, release),
        'fresh_operation': post(uuid.uuid4(), release),
        'mismatched_release': post(operation, uuid.uuid4()),
        'mismatched_build': post(operation, release, build_id=uuid.uuid4()),
        'unknown_study': post(uuid.uuid4(), uuid.uuid4()),
    }
    assert_refusal(before['original'], 403, 'study_deleted')
    for key in ('fresh_operation', 'mismatched_release', 'mismatched_build', 'unknown_study'):
        assert_refusal(before[key], 403, 'admission_unavailable')
    # The un-enumerable refusals are byte-identical to a random object.
    assert body_bytes(before['fresh_operation']) == body_bytes(before['unknown_study'])
    assert body_bytes(before['mismatched_release']) == body_bytes(before['unknown_study'])

    row = run_to_complete(StudyDeletion.objects.get(study_uuid=world.data.pk))
    assert row.state == 'complete' and not Study.objects.filter(pk=world.data.pk).exists()
    after = {
        'original': post(operation, release),
        'fresh_operation': post(uuid.uuid4(), release),
        'unknown_study': post(uuid.uuid4(), uuid.uuid4()),
    }
    assert_refusal(after['original'], 403, 'study_deleted')
    assert_refusal(after['fresh_operation'], 403, 'admission_unavailable')
    assert_refusal(after['unknown_study'], 403, 'admission_unavailable')
    assert body_bytes(after['fresh_operation']) == body_bytes(after['unknown_study'])
    evidence('admission_binding.json', {'before': {key: 'refused' for key in before},
                                        'after': {key: 'refused' for key in after},
                                        'original_study_deleted_before_and_after': True})


# --- 3. bounded cross-scope preview scan -------------------------------------

def test_cross_scope_preview_scan_beyond_first_batch(world, evidence):
    """Every staged preview that references the study is cleared in bounded,
    persisted pages (even beyond the first batch), the study-scoped one is
    cleared by scope, and unrelated previews stay untouched."""
    operator, data = world.operator, world.data
    common = dict(actor=operator, kind='permissions', scope='instance', binding='a' * 64,
                  expires_at=timezone.now() + timedelta(minutes=10))
    unrelated = []
    for index in range(5):
        row = PermissionPreview.objects.create(id=uuid.UUID(int=index + 1),
                                               staged={'unrelated': index}, **common)
        unrelated.append((row.pk, {'unrelated': index}))
    targets = []
    for index in range(3):
        row = PermissionPreview.objects.create(
            id=uuid.UUID(int=100 + index),
            staged={'studies': {str(data.pk): ['study.view']}}, **common)
        targets.append(row.pk)
    scoped = PermissionPreview.objects.create(id=uuid.UUID(int=200), scope=str(data.pk),
                                              staged={'private': 'roster'},
                                              **{key: value for key, value in common.items() if key != 'scope'})

    job = mark(world)
    result = deletion.run_cleanup(job.pk, batch_size=1)
    assert result['state'] == 'complete', result
    assert result['files_remaining'] == 0
    for target in targets:
        assert PermissionPreview.objects.get(pk=target).staged is None
    assert PermissionPreview.objects.get(pk=scoped.pk).staged is None
    for key, staged in unrelated:
        assert PermissionPreview.objects.get(pk=key).staged == staged
    # A later preview scan finds nothing left for the deleted study.
    assert deletion._staged_previews_clear(data.pk) is True
    evidence('cross_scope_previews.json', {'cleared': len(targets) + 1,
                                           'unrelated_preserved': len(unrelated),
                                           'state': result['state']})


# --- 4. owned export spool cleanup -------------------------------------------

def test_owned_export_spool_cleaned_unrelated_preserved(world, tmp_path, monkeypatch, evidence):
    """The export ZIP and its interrupted ``.<id>.<16 hex>.tmp`` are removed,
    a planted symlink is unlinked without touching its target, and unknown
    names, other exports and a wrong nonce shape survive."""
    data_root = tmp_path / 'data'
    (data_root / 'exports').mkdir(parents=True)
    monkeypatch.setattr(settings, 'DATA_DIR', data_root)
    item = world.item
    owned_zip = data_root / 'exports' / f'{item.id}.zip'
    owned_zip.write_bytes(b'SYNTHETIC owned export zip')
    owned_tmp = data_root / 'exports' / f'.{item.id}.0123456789abcdef.tmp'
    owned_tmp.write_bytes(b'SYNTHETIC interrupted export bytes')
    other = uuid.uuid4()
    other_zip = data_root / 'exports' / f'{other}.zip'
    other_zip.write_bytes(b'SYNTHETIC other export zip')
    other_tmp = data_root / 'exports' / f'.{other}.0123456789abcdef.tmp'
    other_tmp.write_bytes(b'SYNTHETIC other export tmp')
    unknown = data_root / 'exports' / 'notes.txt'
    unknown.write_bytes(b'SYNTHETIC unknown file')
    malformed = data_root / 'exports' / f'.{item.id}.short.tmp'
    malformed.write_bytes(b'SYNTHETIC wrong nonce shape')
    victim = tmp_path / 'victim.bin'
    victim.write_bytes(b'SYNTHETIC victim bytes that must survive')
    planted = data_root / 'exports' / f'.{item.id}.fedcba9876543210.tmp'
    planted.symlink_to(victim)

    job = mark(world)
    result = deletion.run_cleanup(job.pk, batch_size=2)
    assert result['state'] == 'complete', result
    assert not owned_zip.exists() and not owned_tmp.exists()
    assert not planted.is_symlink() and not planted.exists()
    assert victim.read_bytes() == b'SYNTHETIC victim bytes that must survive'
    assert other_zip.read_bytes() == b'SYNTHETIC other export zip'
    assert other_tmp.read_bytes() == b'SYNTHETIC other export tmp'
    assert unknown.read_bytes() == b'SYNTHETIC unknown file'
    assert malformed.read_bytes() == b'SYNTHETIC wrong nonce shape'
    assert not Export.objects.filter(pk=item.pk).exists()
    evidence('export_spool_cleanup.json', {'owned_zip_removed': True, 'owned_tmp_removed': True,
                                           'symlink_target_kept': True, 'other_export_kept': True,
                                           'unknown_kept': True})


# --- 5. GUI first-read race --------------------------------------------------

def test_gui_post_race_refused_when_mark_commits_after_first_read(world, monkeypatch, evidence):
    """The GUI page read the study while it was active; the mark commits before
    the write transaction. The final lifecycle check refuses and the response is
    the minimal deletion status, never a study write."""
    operator, data = world.operator, world.data
    client = sign_in(operator)
    import core.gui as gui

    stale = Study.objects.get(pk=data.pk)
    mark(world)
    real_filter = gui.Study.objects.filter
    calls = {'count': 0}

    class StaleQuery:
        def first(self):
            return stale

    def filtering(*args, **kwargs):
        calls['count'] += 1
        if calls['count'] == 1:
            return StaleQuery()
        return real_filter(*args, **kwargs)

    monkeypatch.setattr(gui.Study.objects, 'filter', filtering)
    response = client.post(f'/studies/{data.id}', {'op': 'recruitment', 'state': 'closed'})
    assert response.status_code == 200
    page = response.content.decode()
    assert 'data-deletion-status="marked"' in page
    data.refresh_from_db()
    assert data.recruitment == 'open' and data.lifecycle == 'deleting'
    evidence('gui_first_read_race.json', {'status': response.status_code, 'recruitment': data.recruitment,
                                          'lifecycle': data.lifecycle})


# --- 6. real multi-process races ---------------------------------------------

def test_real_multiprocess_barrier_and_killed_generation(evidence_root, evidence):
    """Run the standalone probe with real OS processes: the pre-check/mark/
    commit barrier is refused, a generation killed after its publish leaves no
    spool file once the cleanup ran, and the post-cleanup operation matrix
    holds."""
    probe = Path(__file__).resolve().parent / 'deletion_concurrency_probe.py'
    env = dict(os.environ, GEP_EVIDENCE_DIR=str(evidence_root))
    result = subprocess.run([sys.executable, str(probe)], cwd=str(Path(__file__).resolve().parents[2]),
                            env=env, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report['status'] == 'PASS', report
    barrier = report['barrier_write_vs_mark']
    assert barrier['pre_checked_active'] is True
    assert barrier['update_policy'] == 'study_deleted'
    assert barrier['lifecycle_after'] == 'deleting' and barrier['public_after'] == 0
    assert report['killed_generation_cleanup']['state'] == 'complete'
    assert report['killed_generation_cleanup']['spool_files_left'] == []
    assert report['race_export_vs_delete']['post_cleanup_retry'] == {
        'original': 'study_deleted', 'fresh': 'admission_unavailable'}
    evidence('probe_regressions.json', report)
