"""P03R04 evidence: the complete server-side study deletion lifecycle.

Every expected value comes from the R00 §D contract and the fixed synthetic
world built below, never from the implementation under test:

* the overview confirmation shows real counts and an export-first link only
  when the study really has data/releases, and always requires the actor's own
  password; an empty study never prompts for a download;
* once the mark commits, admission, event upload, completion, status/context,
  both recovery paths, export creation/download and every public/admin object
  path refuse; the original token/proof gets ``study_deleted`` and every other
  binding gets the same un-enumerable refusal as a random object;
* the durable cleanup job is re-entrant: it persists state/cursor/manifest,
  resumes after a failure or a killed process, only writes ``complete`` after
  the database rows and private files are verified gone, removes shared bytes
  only with the last reference and never follows a planted symlink;
* the tombstones keep only UUIDs and server-secret HMACs; the minimal audit
  counts survive and no re-issuable credential does.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from core import deletion, exports, services
from core.models import (AccountProfile, Audit, Build, DeletedSession, Event, Export,
                         Grant, Instance, Invitation, Participant, RecoveryCode,
                         RecoveryPermit, Release, Session, Study, StudyDeletion)

OWNER_PASSWORD = 'Synthetic-p03r04-owner-password'
OPERATOR_PASSWORD = 'Synthetic-p03r04-operator-password'
OPERATOR_ACTIONS = ('study.view', 'study.configure', 'build.upload', 'build.preview',
                    'release.approve_pilot', 'recruitment.manage', 'data.export_raw',
                    'identity_mapping.read', 'session.view', 'session.recover', 'study.delete')


def make_user(name, password):
    return get_user_model().objects.create_user(name, password=password)


def sign_in(who):
    client = Client()
    client.force_login(who)
    profile = AccountProfile.objects.filter(user_id=who.pk).first()
    session = client.session
    session['gep_auth_version'] = profile.auth_version if profile is not None else 1
    session.save()
    return client


def grant(who, study, *actions):
    Grant.objects.bulk_create([Grant(user=who, study=study, action=action) for action in actions])


def body_bytes(response):
    if getattr(response, 'streaming', False):
        return b''.join(response.streaming_content)
    return response.content


def event_envelope(session, sequence=1, payload=None):
    return {'protocol_version': 'gep/1', 'event_id': str(uuid.uuid4()), 'session_id': str(session.id),
            'segment_id': str(uuid.uuid4()), 'sequence': sequence, 'event_type': 'exp.rt',
            'schema_id': 'rt', 'schema_version': '1',
            'payload': {'rt_ms': 123.5} if payload is None else payload}


class World:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __getitem__(self, key):
        return self.__dict__[key]


def build_study(title, recruitment='open'):
    study = Study.objects.create(title=title, recruitment=recruitment, max_sessions=10)
    build = Build.objects.create(study=study, digest=hashlib.sha256(title.encode()).hexdigest(),
                                 descriptor={'platform': 'godot_web', 'version': '1.0',
                                             'schemas': {'exp.rt': {'id': 'rt', 'version': '1',
                                                                    'schema': {'type': 'object'}}}})
    release = Release.objects.create(study=study, build=build, config={'mode': 'anonymous'}, approved=True)
    study.current_release = release
    study.save(update_fields=['current_release'])
    return study, build, release


def admit(study, release, proof='p' * 48, operation=None, participant_code=None):
    instance = Instance.objects.get(pk=1)
    payload = {'operation_id': str(operation or uuid.uuid4()), 'instance_id': str(instance.instance_id),
               'study_id': str(study.id), 'release_id': str(release.id), 'proof': proof,
               'participant_code': participant_code}
    return services.admit_request(payload)


def send_events(session, token, count=1, start=1):
    batch = {'batch_id': str(uuid.uuid4()),
             'events': [event_envelope(session, sequence=start + offset) for offset in range(count)]}
    return services.receive(session.id, token, batch)


@pytest.fixture
def world(db):
    """A v2 instance with an empty study and a data study, both delegated."""
    owner = make_user('p03r04_owner', OWNER_PASSWORD)
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    AccountProfile.objects.create(user=owner, role='user', policy_version=2)
    operator = make_user('p03r04_operator', OPERATOR_PASSWORD)
    AccountProfile.objects.create(user=operator, role='user', policy_version=2)

    empty = Study.objects.create(title='R04 empty study', recruitment='open', max_sessions=5)
    data, build, release = build_study('R04 data study')
    for study in (empty, data):
        grant(operator, study, *OPERATOR_ACTIONS)
        grant(owner, study, 'study.view')

    session, token = admit(data, release)
    send_events(session, token, count=2)
    session.completion = {'event_ids': [str(e.event_id) for e in Event.objects.filter(session=session)],
                          'segment_ids': [str(e.segment_id) for e in Event.objects.filter(session=session)]}
    session.save(update_fields=['completion'])
    item = exports.create_v2_export(operator, data.id, view='unmapped', language='zh')
    permit = 'permit-' + uuid.uuid4().hex
    RecoveryPermit.objects.create(session=session, issuer=operator, token_hash=services.digest(permit),
                                  expires_at=timezone.now() + timedelta(minutes=15))
    code = services.issue_recovery_code(operator, session.id)
    return World(owner=owner, operator=operator, empty=empty, data=data, build=build, release=release,
                 session=session, token=token, item=item, permit=permit, code=code,
                 admit=admit, send_events=send_events)


# --- 1. confirmation UI -----------------------------------------------------

def test_delete_confirmation_ui_empty_and_data_studies(world, evidence):
    """The overview block: empty study -> password only; data study -> real
    counts + export-first link; wrong password changes nothing; the mark shows
    only marked until the explicit cleanup really completes."""
    operator, owner = world.operator, world.owner
    operator_client = sign_in(operator)

    empty_page = operator_client.get(f'/studies/{world.empty.id}').content.decode()
    assert 'data-study-deletion="1"' in empty_page
    assert 'data-delete-password="1"' in empty_page and 'data-delete-submit="1"' in empty_page
    assert 'data-deletion-counts="1"' not in empty_page
    assert 'data-deletion-export-prompt="1"' not in empty_page
    assert 'data-deletion-export-link="1"' not in empty_page

    data_page = operator_client.get(f'/studies/{world.data.id}').content.decode()
    assert 'data-deletion-counts="1"' in data_page
    assert f'{ui_sessions()} {1}' in data_page
    assert f'{ui_participants()} {1}' in data_page
    assert 'data-deletion-export-prompt="1"' in data_page
    assert f'href="/studies/{world.data.id}/exports"' in data_page
    # The delete entry is not offered without the study.delete action.
    bystander = make_user('p03r04_bystander', OWNER_PASSWORD)
    AccountProfile.objects.create(user=bystander, role='user', policy_version=2)
    grant(bystander, world.data, 'study.view')
    assert 'data-study-deletion="1"' not in sign_in(bystander).get(f'/studies/{world.data.id}').content.decode()

    # A wrong password refuses with no write at all.
    refused = operator_client.post(f'/studies/{world.data.id}',
                                   {'op': 'delete_study', 'password': 'not-the-password'})
    assert refused.status_code == 403
    assert b'reauth_failed' in refused.content
    world.data.refresh_from_db()
    assert world.data.lifecycle == 'active' and not StudyDeletion.objects.exists()
    assert not DeletedSession.objects.exists()

    # The real mark: redirect to the status page that only shows marked.
    marked = operator_client.post(f'/studies/{world.data.id}',
                                  {'op': 'delete_study', 'password': OPERATOR_PASSWORD})
    assert marked.status_code == 302
    status_page = operator_client.get(f'/studies/{world.data.id}').content.decode()
    assert 'data-deletion-status="marked"' in status_page
    assert 'data-deletion-state="marked"' in status_page
    assert 'data-delete-form="1"' not in status_page
    world.data.refresh_from_db()
    assert world.data.lifecycle == 'deleting'
    row = StudyDeletion.objects.get(study_uuid=world.data.id)
    assert row.state == 'marked' and row.counts['sessions'] == 1 and row.counts['events'] == 2
    assert DeletedSession.objects.count() == 1

    # The status page is only for the requester/Owner; a former viewer gets 404.
    assert operator_client.get(f'/studies/{world.empty.id}').status_code == 200
    bystander_client = sign_in(bystander)
    assert bystander_client.get(f'/studies/{world.data.id}').status_code == 404
    assert sign_in(owner).get(f'/studies/{world.data.id}').status_code == 200

    # A repeated mark is refused and never creates a second job.
    again = operator_client.post(f'/studies/{world.data.id}',
                                 {'op': 'delete_study', 'password': OPERATOR_PASSWORD})
    assert again.status_code == 200 and 'data-deletion-status="marked"' in again.content.decode()
    assert StudyDeletion.objects.count() == 1

    evidence('confirmation_ui.json', {'empty_export_prompt': False, 'data_counts': row.counts,
                                      'wrong_password_status': refused.status_code,
                                      'marked_state': row.state, 'jobs': StudyDeletion.objects.count()})


def ui_sessions():
    from core import ui
    return ui.tr('zh', 'home_card_sessions')


def ui_participants():
    from core import ui
    return ui.tr('zh', 'home_card_participants')


# --- 2. every entry point after the mark ------------------------------------

def assert_refusal(response, status, code):
    body = json.loads(body_bytes(response))
    assert response.status_code == status, body
    assert body['code'] == code
    assert body['retryable'] is False and body['outcome_unknown'] is False


def test_marked_and_deleted_study_refuses_every_entry_point(world, evidence):
    """Marked: no write and no read leaks; deleted: the same refusals persist
    after the rows are gone, with study_deleted only for the original secrets."""
    operator, owner = world.operator, world.owner
    client = sign_in(operator)
    instance = Instance.objects.get(pk=1)
    session, token, item = world.session, world.token, world.item
    operation = session.operation
    proof = 'p' * 48
    binding = {'instance_id': str(instance.instance_id), 'study_id': str(world.data.id),
               'release_id': str(world.release.id), 'build_id': str(world.build.id)}

    deletion.mark_study_deletion(operator, world.data, OPERATOR_PASSWORD)

    # Admission: the original operation+proof is the permanent stop; a fresh
    # operation that reuses the same proof is NOT the original operation and
    # gets the same un-enumerable refusal as a random study (2026-09-24
    # checkpoint correction: the previous ``study_deleted`` expectation here
    # contradicted the R00 §D operation-binding contract).
    original = client.post('/v1/participant/sessions', {
        'operation_id': str(operation), 'instance_id': binding['instance_id'], 'study_id': binding['study_id'],
        'release_id': binding['release_id'], 'proof': proof, 'participant_code': None},
        content_type='application/json')
    assert_refusal(original, 403, 'study_deleted')
    fresh_operation = client.post('/v1/participant/sessions', {
        'operation_id': str(uuid.uuid4()), 'instance_id': binding['instance_id'], 'study_id': binding['study_id'],
        'release_id': binding['release_id'], 'proof': proof, 'participant_code': None},
        content_type='application/json')
    assert_refusal(fresh_operation, 403, 'admission_unavailable')
    mismatched_release = client.post('/v1/participant/sessions', {
        'operation_id': str(operation), 'instance_id': binding['instance_id'], 'study_id': binding['study_id'],
        'release_id': str(uuid.uuid4()), 'proof': proof, 'participant_code': None},
        content_type='application/json')
    assert_refusal(mismatched_release, 403, 'admission_unavailable')
    mismatched_build = client.post('/v1/participant/sessions', {
        'operation_id': str(operation), 'instance_id': binding['instance_id'], 'study_id': binding['study_id'],
        'release_id': binding['release_id'], 'build_id': str(uuid.uuid4()), 'proof': proof,
        'participant_code': None},
        content_type='application/json')
    assert_refusal(mismatched_build, 403, 'admission_unavailable')
    forged = client.post('/v1/participant/sessions', {
        'operation_id': str(uuid.uuid4()), 'instance_id': binding['instance_id'], 'study_id': binding['study_id'],
        'release_id': binding['release_id'], 'proof': 'z' * 48, 'participant_code': None},
        content_type='application/json')
    random_study = client.post('/v1/participant/sessions', {
        'operation_id': str(uuid.uuid4()), 'instance_id': binding['instance_id'], 'study_id': str(uuid.uuid4()),
        'release_id': str(uuid.uuid4()), 'proof': 'z' * 48, 'participant_code': None},
        content_type='application/json')
    assert_refusal(forged, 403, 'admission_unavailable')
    assert_refusal(random_study, 403, 'admission_unavailable')
    assert body_bytes(forged) == body_bytes(random_study)

    # Event upload / completion / status / context.
    batch = {'batch_id': str(uuid.uuid4()), 'events': [event_envelope(session, sequence=3)]}
    assert_refusal(client.post(f'/v1/participant/sessions/{session.id}/event-batches', batch,
                               content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}'), 403, 'study_deleted')
    assert_refusal(client.post(f'/v1/participant/sessions/{session.id}/completion',
                               {'event_ids': [], 'segment_ids': []},
                               content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}'), 403, 'study_deleted')
    assert_refusal(client.get(f'/v1/participant/sessions/{session.id}/status',
                              HTTP_AUTHORIZATION=f'Bearer {token}'), 403, 'study_deleted')
    assert_refusal(client.get(f'/v1/participant/sessions/{session.id}/context',
                              HTTP_AUTHORIZATION=f'Bearer {token}'), 403, 'study_deleted')
    wrong_token = client.get(f'/v1/participant/sessions/{session.id}/status',
                             HTTP_AUTHORIZATION='Bearer ' + 'q' * 43)
    random_session = client.get(f'/v1/participant/sessions/{uuid.uuid4()}/status',
                                HTTP_AUTHORIZATION='Bearer ' + 'q' * 43)
    assert_refusal(wrong_token, 403, 'session_unavailable')
    assert_refusal(random_session, 403, 'session_unavailable')
    assert body_bytes(wrong_token) == body_bytes(random_session)
    wrong_batch = client.post(f'/v1/participant/sessions/{session.id}/event-batches', batch,
                              content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + 'q' * 43)
    assert_refusal(wrong_batch, 403, 'session_unavailable')

    # Old recover path: exact proof+binding is the permanent stop, anything
    # else the same recovery_unavailable.
    old_recover = client.post(f'/v1/participant/sessions/{session.id}/recover',
                              {'proof': proof, 'permit': world.permit}, content_type='application/json')
    forged_recover = client.post(f'/v1/participant/sessions/{session.id}/recover',
                                 {'proof': 'z' * 48, 'permit': world.permit}, content_type='application/json')
    assert_refusal(old_recover, 403, 'study_deleted')
    assert_refusal(forged_recover, 403, 'recovery_unavailable')

    # New recovery: same rule; a short code or a forged proof redeems nothing.
    code_payload = {'capability': services.RECOVERY_CODE_CAPABILITY, 'code': world.code['code'],
                    'proof': proof, **binding}
    assert_refusal(client.post('/v1/participant/recovery', code_payload,
                               content_type='application/json'), 403, 'study_deleted')
    forged_code = client.post('/v1/participant/recovery',
                              {**code_payload, 'proof': 'z' * 48}, content_type='application/json')
    assert_refusal(forged_code, 403, 'recovery_unavailable')

    # Export creation and every download format: gone for everyone.
    created = client.post('/v1/admin/exports', {'study_id': str(world.data.id), 'format_version': '2',
                                                'view': 'unmapped', 'language': 'zh'},
                          content_type='application/json')
    assert_refusal(created, 404, 'study_unavailable')
    for fmt in ('jsonl', 'metadata', 'zip'):
        download = client.get(f'/v1/admin/exports/{item.id}/download?format={fmt}')
        assert_refusal(download, 404, 'export_unavailable')
    assert body_bytes(client.get(f'/v1/admin/exports/{item.id}/download')).find(b'PK') == -1

    # Public join/run/preview and admin object requests: 404 like a random id.
    assert client.get(f'/join/{world.data.id}', HTTP_HOST='experiment.localhost').status_code == 404
    assert client.get(f'/run/{world.release.id}/web/index.html', HTTP_HOST='experiment.localhost').status_code == 404
    assert client.get(f'/run/{uuid.uuid4()}/web/index.html', HTTP_HOST='experiment.localhost').status_code == 404
    assert client.get(f'/releases/{world.release.id}/artifact').status_code == 404
    assert client.get(f'/releases/{world.release.id}/config').status_code == 404
    from django.core import signing
    token_preview = signing.dumps({'build': str(world.build.id), 'user': operator.pk}, salt='preview')
    assert client.get(f'/preview/{token_preview}/web/index.html', HTTP_HOST='experiment.localhost').status_code == 404

    # Cleanup to completion, then the refusals persist with the rows gone.
    while StudyDeletion.objects.get(study_uuid=world.data.id).state != 'complete':
        deletion.run_cleanup(StudyDeletion.objects.get(study_uuid=world.data.id).pk)
    assert not Study.objects.filter(pk=world.data.id).exists()
    assert not Session.objects.filter(pk=session.id).exists()
    assert DeletedSession.objects.filter(session_uuid=session.id).exists()
    after = client.post('/v1/participant/sessions', {
        'operation_id': str(operation), 'instance_id': binding['instance_id'], 'study_id': binding['study_id'],
        'release_id': binding['release_id'], 'proof': proof, 'participant_code': None},
        content_type='application/json')
    assert_refusal(after, 403, 'study_deleted')
    assert_refusal(client.get(f'/v1/participant/sessions/{session.id}/status',
                              HTTP_AUTHORIZATION=f'Bearer {token}'), 403, 'study_deleted')
    assert_refusal(client.get(f'/v1/admin/exports/{item.id}/download?format=zip'), 404, 'export_unavailable')
    assert client.get(f'/join/{world.data.id}', HTTP_HOST='experiment.localhost').status_code == 404
    # The requester keeps a minimal completed status page; others 404.
    status_page = client.get(f'/studies/{world.data.id}').content.decode()
    assert 'data-deletion-status="complete"' in status_page
    assert world.data.title not in status_page

    evidence('entry_point_refusals.json', {
        'admission_study_deleted': True, 'admission_unavailable': True,
        'session_deleted': True, 'session_unavailable': True, 'recovery_study_deleted': True,
        'recovery_unavailable': True, 'export_404': True, 'public_404': True,
        'complete_state': 'complete'})


# --- 3. cleanup removes dependencies/files, keeps shared bytes and audit -----

def test_cleanup_clears_dependencies_files_shared_bytes_and_audit(world, tmp_path, monkeypatch, evidence):
    """The durable job: DB dependencies and exclusive files go, shared bytes
    stay with the last reference, the audit is minimized and the tombstone keeps
    no recoverable secret."""
    data_root = tmp_path / 'data'
    for name in ('packages', 'artifacts', 'exports'):
        (data_root / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, 'DATA_DIR', data_root)
    operator = world.operator

    # A's build shares its package bytes with a kept study's build.
    shared_package = 'shared-package-digest.zip'
    (data_root / 'packages' / shared_package).write_bytes(b'shared program bytes')
    world.build.package_path = shared_package
    world.build.save(update_fields=['package_path'])
    keep, keep_build, keep_release = build_study('R04 kept study')
    grant(operator, keep, *OPERATOR_ACTIONS)
    keep_build.package_path = shared_package
    keep_build.save(update_fields=['package_path'])

    # A's release shares its artifact bytes with the kept study's release.
    shared_artifact = 'f' * 64 + '.zip'
    (data_root / 'artifacts' / shared_artifact).write_bytes(b'shared artifact bytes')
    world.release.artifact_path = shared_artifact
    world.release.artifact_digest = 'f' * 64
    world.release.artifact_size = len(b'shared artifact bytes')
    world.release.save()
    keep_release.artifact_path = shared_artifact
    keep_release.artifact_digest = 'f' * 64
    keep_release.artifact_size = len(b'shared artifact bytes')
    keep_release.save()

    # Exclusive export ZIPs for the deleted study and the kept study.
    (data_root / 'exports' / f'{world.item.id}.zip').write_bytes(b'A export zip')
    keep_item = exports.create_v2_export(operator, keep.id, view='unmapped', language='zh')
    (data_root / 'exports' / f'{keep_item.id}.zip').write_bytes(b'keep export zip')

    Invitation.objects.create(study=world.data, issuer=operator, username='r04_invite',
                              actions=['study.view'], token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                              expires_at=timezone.now() + timedelta(hours=1))
    Audit.objects.create(study=world.data, actor=operator, action='study.created',
                         target=str(world.data.id), before={'public': True}, after={'public': False})
    Audit.objects.create(study=keep, actor=operator, action='study.created',
                         target=str(keep.id), before={'public': True}, after={'public': False})

    deletion.mark_study_deletion(operator, world.data, OPERATOR_PASSWORD)
    row = StudyDeletion.objects.get(study_uuid=world.data.id)
    deletion.run_cleanup(row.pk)
    row.refresh_from_db()

    assert row.state == 'complete' and row.file_manifest == []
    assert row.counts['sessions'] == 1 and row.counts['events'] == 2
    assert not Study.objects.filter(pk=world.data.id).exists()
    for queryset in (Session.objects.filter(release__study_id=world.data.id),
                     Participant.objects.filter(study_id=world.data.id),
                     Event.objects.filter(session__release__study_id=world.data.id),
                     Export.objects.filter(study_id=world.data.id),
                     Release.objects.filter(study_id=world.data.id),
                     Build.objects.filter(study_id=world.data.id),
                     Grant.objects.filter(study_id=world.data.id),
                     Invitation.objects.filter(study_id=world.data.id),
                     RecoveryCode.objects.filter(study_id=world.data.id),
                     RecoveryPermit.objects.filter(session__release__study_id=world.data.id)):
        assert not queryset.exists(), queryset.model.__name__

    # The kept study and every row it owns are untouched, including the shared
    # bytes that still have a live reference.
    assert Study.objects.filter(pk=keep.id).exists()
    assert Build.objects.filter(pk=keep_build.id).exists()
    assert Release.objects.filter(pk=keep_release.id).exists()
    assert Export.objects.filter(pk=keep_item.id).exists()
    assert (data_root / 'packages' / shared_package).exists()
    assert (data_root / 'artifacts' / shared_artifact).exists()
    assert not (data_root / 'exports' / f'{world.item.id}.zip').exists()
    assert (data_root / 'exports' / f'{keep_item.id}.zip').exists()

    # Audit: the study link is nulled and raw before/after stripped, but the
    # action rows survive; only the mark row keeps its minimal counts.
    old_audit = Audit.objects.get(action='study.created', target=str(world.data.id))
    assert old_audit.study_id is None and old_audit.before is None and old_audit.after is None
    mark_audit = Audit.objects.get(action='study.deletion_marked', target=str(world.data.id))
    assert mark_audit.study_id is None and mark_audit.before is None and mark_audit.after == {'counts': row.counts}
    keep_audit = Audit.objects.get(action='study.created', target=str(keep.id))
    assert keep_audit.study_id == keep.id and keep_audit.before == {'public': True}

    # The tombstone keeps only the UUIDs and the domain-separated HMACs; the
    # raw token/proof (or even their plain digests) are not stored.
    tomb = DeletedSession.objects.get(session_uuid=world.session.id)
    assert tomb.token_hmac == deletion._server_hmac(deletion.TOKEN_DOMAIN, world.session.token_hash)
    assert tomb.proof_hmac == deletion._server_hmac(deletion.PROOF_DOMAIN, world.session.proof_hash)
    assert tomb.token_hmac != world.token
    assert tomb.token_hmac != hashlib.sha256(world.token.encode()).hexdigest()
    assert tomb.proof_hmac != 'p' * 48
    assert tomb.proof_hmac != hashlib.sha256(('p' * 48).encode()).hexdigest()

    # A complete job is never re-claimed, so there is no false second cleanup.
    assert deletion.cleanup_pending(study_uuid=world.data.id) == []
    evidence('cleanup_dependencies.json', {'state': row.state, 'counts': row.counts,
                                           'shared_package_kept': True, 'shared_artifact_kept': True,
                                           'exclusive_export_removed': True, 'tombstone_hmac': True})


# --- 4. failure/resume and symlink safety -----------------------------------

def test_cleanup_failure_resumes_and_never_follows_symlinks(world, tmp_path, monkeypatch, evidence):
    """A file failure persists ``failed`` with the cursor and never claims
    complete; a planted symlink is unlinked without touching its target and a
    symlinked private root is refused instead of followed."""
    data_root = tmp_path / 'data'
    for name in ('packages', 'artifacts', 'exports'):
        (data_root / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, 'DATA_DIR', data_root)
    export_zip = data_root / 'exports' / f'{world.item.id}.zip'
    export_zip.write_bytes(b'A export zip')

    deletion.mark_study_deletion(world.operator, world.data, OPERATOR_PASSWORD)
    row = StudyDeletion.objects.get(study_uuid=world.data.id)

    def failing_delete(path):
        raise OSError('synthetic file failure')

    with monkeypatch.context() as patch:
        patch.setattr(deletion, '_unlink_private', failing_delete)
        result = deletion.run_cleanup(row.pk, batch_size=5)
    assert result['state'] == 'failed' and result['error_code'] == 'OSError'
    assert result['files_remaining'] >= 1
    row.refresh_from_db()
    assert row.state == 'failed' and row.cursor < len(deletion.CLEANUP_STEPS)
    assert not Export.objects.filter(pk=world.item.id).exists()
    assert export_zip.exists()

    # The next run resumes from the persisted manifest and really completes.
    result = deletion.run_cleanup(row.pk)
    assert result['state'] == 'complete' and result['files_remaining'] == 0
    assert not export_zip.exists()

    # A planted symlink at the export path: the link is removed, the target
    # outside the private root keeps its bytes.
    victim = tmp_path / 'victim.zip'
    victim.write_bytes(b'victim bytes that must survive')
    second, second_build, second_release = build_study('R04 symlink study')
    grant(world.operator, second, *OPERATOR_ACTIONS)
    second_session, _second_token = admit(second, second_release, proof='s' * 48)
    second_item = exports.create_v2_export(world.operator, second.id, view='unmapped', language='zh')
    link_path = data_root / 'exports' / f'{second_item.id}.zip'
    link_path.symlink_to(victim)
    deletion.mark_study_deletion(world.operator, second, OPERATOR_PASSWORD)
    second_row = StudyDeletion.objects.get(study_uuid=second.id)
    result = deletion.run_cleanup(second_row.pk)
    assert result['state'] == 'complete'
    assert victim.read_bytes() == b'victim bytes that must survive'
    assert not link_path.is_symlink() and not link_path.exists()

    # A symlinked private root is refused, never followed: the outside target
    # is untouched and the job stays failed with the documented code.
    third, _third_build, third_release = build_study('R04 root symlink study')
    grant(world.operator, third, *OPERATOR_ACTIONS)
    admit(third, third_release, proof='t' * 48)
    third_item = exports.create_v2_export(world.operator, third.id, view='unmapped', language='zh')
    outside_root = tmp_path / 'outside-exports'
    outside_root.mkdir()
    (outside_root / f'{third_item.id}.zip').write_bytes(b'outside bytes')
    shutil.rmtree(data_root / 'exports')
    (data_root / 'exports').symlink_to(outside_root)
    deletion.mark_study_deletion(world.operator, third, OPERATOR_PASSWORD)
    third_row = StudyDeletion.objects.get(study_uuid=third.id)
    result = deletion.run_cleanup(third_row.pk)
    assert result['state'] == 'failed' and result['error_code'] == 'unsafe_file_root'
    assert (outside_root / f'{third_item.id}.zip').read_bytes() == b'outside bytes'
    # Remove the planted root symlink and the same job resumes to completion.
    (data_root / 'exports').unlink()
    (data_root / 'exports').mkdir()
    result = deletion.run_cleanup(third_row.pk)
    assert result['state'] == 'complete'
    assert (outside_root / f'{third_item.id}.zip').read_bytes() == b'outside bytes'
    evidence('cleanup_failure_resume.json', {'failed_state': 'failed', 'resumed_state': 'complete',
                                             'symlink_target_kept': True, 'root_symlink_refused': True})


# --- 5. mark rollback and bounded resume ------------------------------------

def test_mark_audit_failure_rolls_back_and_bounded_cleanup_resumes(world, monkeypatch, evidence):
    """An audit failure rolls the whole mark back (no job, no tombstone, still
    active); a bounded cleanup run persists its cursor and only a later run
    completes, so a killed process can never leave a false complete."""
    class FailingAudit:
        class objects:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError('synthetic audit failure')

    with monkeypatch.context() as patch:
        patch.setattr(deletion, 'Audit', FailingAudit)
        with pytest.raises(RuntimeError):
            deletion.mark_study_deletion(world.operator, world.data, OPERATOR_PASSWORD)
    world.data.refresh_from_db()
    assert world.data.lifecycle == 'active'
    assert not StudyDeletion.objects.exists()
    assert not DeletedSession.objects.exists()
    assert not Audit.objects.filter(action='study.deletion_marked').exists()

    # A real mark, then a database-step failure rolls the whole batch back (no
    # partial delete) and persists only the failed state.
    deletion.mark_study_deletion(world.operator, world.data, OPERATOR_PASSWORD)
    row = StudyDeletion.objects.get(study_uuid=world.data.id)

    def failing_events(study_uuid, manifest, budget):
        raise ValueError('synthetic database failure')

    with monkeypatch.context() as patch:
        patch.setitem(deletion._STEP_FUNCTIONS, 'events', failing_events)
        failed = deletion.run_cleanup(row.pk, batch_size=200)
    assert failed['state'] == 'failed' and failed['error_code'] == 'ValueError'
    row.refresh_from_db()
    assert row.cursor == 0
    assert RecoveryCode.objects.filter(study_id=world.data.id).exists()
    assert Event.objects.filter(session__release__study_id=world.data.id).exists()
    assert Study.objects.filter(pk=world.data.id).exists()

    # A bounded run advances the persisted cursor without claiming completion.
    bounded = deletion.run_cleanup(row.pk, batch_size=1, max_batches=2)
    assert bounded['state'] == 'cleaning'
    # Some bounded progress is persisted, but the study is nowhere near done.
    assert not RecoveryCode.objects.filter(study_id=world.data.id).exists()
    assert Study.objects.filter(pk=world.data.id).exists()
    assert Event.objects.filter(session__release__study_id=world.data.id).exists()

    # A fresh full run resumes from the cursor and completes.
    result = deletion.run_cleanup(row.pk)
    assert result['state'] == 'complete'
    assert not Study.objects.filter(pk=world.data.id).exists()
    evidence('mark_rollback_resume.json', {'rolled_back': True, 'bounded_state': bounded['state'],
                                           'bounded_cursor': bounded['cursor'], 'final': result['state']})


# --- 6. real multi-process concurrency probe --------------------------------

def test_real_multiprocess_deletion_concurrency_probe(evidence_root, evidence):
    """Run the standalone probe with real OS processes: write-vs-mark, export
    generation-vs-delete and SIGKILL-then-resume all pass their invariants."""
    probe = Path(__file__).resolve().parent / 'deletion_concurrency_probe.py'
    env = dict(os.environ, GEP_EVIDENCE_DIR=str(evidence_root))
    result = subprocess.run([sys.executable, str(probe)], cwd=str(Path(__file__).resolve().parents[2]),
                            env=env, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report['status'] == 'PASS', report
    assert report['race_write_vs_mark']['post_mark_admit'] == 'study_deleted'
    assert report['race_write_vs_mark']['post_mark_fresh_admit'] == 'admission_unavailable'
    assert report['race_write_vs_mark']['post_mark_receive'] == 'study_deleted'
    assert report['race_export_vs_delete']['leftover_file'] is False
    assert report['race_export_vs_delete']['http_status'] == 404
    assert report['kill_resume']['state_after_kill'] != 'complete'
    assert report['kill_resume']['after_resume']['state'] == 'complete'
    # R04R: the file step is bounded by real removals, a SIGKILL in the middle
    # keeps the unfinished ownership, and illegal parameters exit non-zero.
    assert report['file_kill_resume']['state_after_kill'] != 'complete'
    assert report['file_kill_resume']['files_left_at_kill'] >= 1
    assert report['file_kill_resume']['after_resume'] == {'state': 'complete', 'owned_files_left': 0,
                                                          'unrelated_files_kept': 3}
    assert report['illegal_parameters'] and all(code != 0 for code in report['illegal_parameters'].values())
    evidence('probe_result.json', report)


# --- 7. real Chrome confirmation flow ---------------------------------------

@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')
def test_real_chrome_delete_confirmation_and_status(world, live_server, evidence_root, evidence):
    """A real Chrome session sees the empty/data difference, is refused with a
    wrong password, marks with the right one and later sees the real complete
    state only after the explicit cleanup ran."""
    operator, empty, data = world.operator, world.empty, world.data
    root = Path(__file__).resolve().parents[2]
    evidence_dir = evidence_root
    script = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_ADMIN_BASE, empty=process.env.GEP_EMPTY_ID, data=process.env.GEP_DATA_ID;
try {
  const page=await browser.newPage();
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('p03r04_operator');
  await page.locator('[name=password]').fill(process.env.GEP_OPERATOR_PASSWORD);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  const emptyResponse=await page.goto(base+'/studies/'+empty);
  expect(emptyResponse.status()).toBe(200);
  expect(await page.locator('[data-study-deletion]').count()).toBe(1);
  expect(await page.locator('[data-delete-password]').count()).toBe(1);
  expect(await page.locator('[data-deletion-counts]').count()).toBe(0);
  expect(await page.locator('[data-deletion-export-prompt]').count()).toBe(0);
  const dataResponse=await page.goto(base+'/studies/'+data);
  expect(dataResponse.status()).toBe(200);
  expect(await page.locator('[data-deletion-counts]').count()).toBe(1);
  expect(await page.locator('[data-deletion-export-prompt]').count()).toBe(1);
  const exportHref=await page.locator('[data-deletion-export-link]').getAttribute('href');
  if(!exportHref.endsWith('/exports')){throw new Error('export link wrong: '+exportHref);}
  await page.locator('[data-delete-password]').fill('definitely-wrong-password');
  await page.locator('[data-delete-submit]').click();
  await page.waitForSelector('[data-error="reauth_failed"]');
  expect(await page.locator('[data-module-error]').count()).toBe(1);
  expect(await page.locator('[data-deletion-status]').count()).toBe(0);
  await page.goto(base+'/studies/'+data);
  await page.locator('[data-delete-password]').fill(process.env.GEP_OPERATOR_PASSWORD);
  await page.locator('[data-delete-submit]').click();
  await page.waitForSelector('[data-deletion-status="marked"]');
  expect(await page.locator('[data-delete-form]').count()).toBe(0);
  if(errors.length){throw new Error('page errors: '+JSON.stringify(errors));}
  console.log('OK: delete confirmation and marked status');
} finally {await browser.close();}
'''
    env = dict(os.environ, GEP_ADMIN_BASE=live_server.url, GEP_EMPTY_ID=str(empty.id),
               GEP_DATA_ID=str(data.id), GEP_OPERATOR_PASSWORD=OPERATOR_PASSWORD,
               GEP_EVIDENCE_DIR=str(evidence_dir))
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr

    # The explicit cleanup really completes, and only then the page says so.
    row = StudyDeletion.objects.get(study_uuid=data.id)
    while StudyDeletion.objects.get(pk=row.pk).state != 'complete':
        deletion.run_cleanup(row.pk)
    reload_script = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_ADMIN_BASE, data=process.env.GEP_DATA_ID;
try {
  const page=await browser.newPage();
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('p03r04_operator');
  await page.locator('[name=password]').fill(process.env.GEP_OPERATOR_PASSWORD);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/studies/'+data);
  await page.waitForSelector('[data-deletion-status="complete"]');
  const state=(await page.locator('[data-deletion-state]').innerText()).trim();
  if(state!=='complete'){throw new Error('unexpected state: '+state);}
  console.log('OK: completed status after cleanup');
} finally {await browser.close();}
'''
    result = subprocess.run(['node', '--input-type=module', '-e', reload_script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence('chrome_delete.json', {'confirmation': True, 'wrong_password_refused': True,
                                    'marked': True, 'complete': True, 'browser': 'chrome'})
