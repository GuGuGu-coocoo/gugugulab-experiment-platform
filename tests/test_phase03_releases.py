"""T27/P0303 server-side evidence: publication policy, current release, stable entry.

Every assertion states an independently specified expectation about the real
database, HTTP API, files and audit rows; release availability, roles and portal
visibility are explicit fixture state, never a copy of implementation output.
"""
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.utils import timezone

from core import publication
from core.gui import connection_config
from core.models import (Audit, Build, Event, Export, Grant, Instance, Participant,
                         RecoveryPermit, Release, Session, Study)
from core.protocol import Rejected
from core.services import admit, admit_request, digest, receive, recover

OWNER_PASSWORD = 'synthetic-test-password'


def package_bytes(body):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w') as archive:
        archive.writestr('web/index.html', body)
    return target.getvalue()


FIRST_PACKAGE = package_bytes(b'<html><head></head><body>first synthetic package</body></html>')
SECOND_PACKAGE = package_bytes(b'<html><head></head><body>second synthetic package</body></html>')
DESCRIPTOR = {
    'platform': 'godot_web',
    'schemas': {'exp.rt': {'id': 'rt', 'version': '1', 'schema': {
        'type': 'object', 'properties': {'rt_ms': {'type': 'number'}}, 'required': ['rt_ms'],
        'additionalProperties': False}}},
    'codebook': {'rt_ms': {'unit': 'ms', 'source': 'host'}},
}


def grant(user, study, *actions):
    for action in actions:
        Grant.objects.create(user=user, study=study, action=action, delegable=True)


def make_release(study, version, approved=True, package='package.zip', config=None):
    digest_value = hashlib.sha256((str(study.id) + version).encode()).hexdigest()
    descriptor = dict(DESCRIPTOR, version=version)
    build = Build.objects.create(study=study, descriptor=descriptor, digest=digest_value, package_path=package)
    return Release.objects.create(study=study, build=build, approved=approved,
                                  config=config or {'purpose': 'synthetic', 'mode': study.mode, 'tag': version})


def world(title='Publication synthetic', recruitment='open', mode='anonymous'):
    """Owner + instance + two approved package releases on one study."""
    owner = get_user_model().objects.create_user('publication_owner', password=OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title=title, mode=mode, recruitment=recruitment)
    first = make_release(study, 'v1', package='first.zip')
    second = make_release(study, 'v2', package='second.zip')
    grant(owner, study, 'study.view', 'study.configure', 'recruitment.manage')
    return {'owner': owner, 'instance': instance, 'study': study, 'first': first, 'second': second}


def entry_request(context, release, revision, operation=None, proof='p' * 48, **extra):
    data = {'operation_id': operation or str(uuid.uuid4()), 'proof': proof,
            'instance_id': str(context['instance'].instance_id), 'study_id': str(context['study'].id),
            'expected_release_id': str(release.id), 'expected_revision': revision}
    data.update(extra)
    return data


def post_admission(data, host='experiment.localhost'):
    return Client().post('/v1/participant/sessions', data, content_type='application/json', HTTP_HOST=host)


def event_for(session, sequence=1, rt=321.5):
    return {'protocol_version': 'gep/1', 'event_id': str(uuid.uuid4()), 'session_id': str(session.id),
            'segment_id': str(uuid.uuid4()), 'sequence': sequence, 'event_type': 'exp.rt',
            'schema_id': 'rt', 'schema_version': '1', 'payload': {'rt_ms': rt}}


@pytest.mark.django_db(transaction=True)
def test_publication_migration_defaults_and_preserves_legacy_bindings():
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([('core', '0005_permission_preview')])
        legacy = executor.loader.project_state([('core', '0005_permission_preview')]).apps
        User = legacy.get_model('auth', 'User')
        owner = User.objects.create(username='migration_publication_owner', password='x')
        LegacyInstance = legacy.get_model('core', 'Instance')
        LegacyInstance.objects.create(id=1, instance_id=uuid.uuid4(), owner_id=owner.pk)
        LegacyStudy = legacy.get_model('core', 'Study')
        study = LegacyStudy.objects.create(title='Legacy publication', mode='anonymous', recruitment='open', max_sessions=2)
        LegacyBuild = legacy.get_model('core', 'Build')
        build = LegacyBuild.objects.create(study_id=study.pk, descriptor={'platform': 'godot_web', 'version': 'v1'}, digest='a' * 64)
        LegacyRelease = legacy.get_model('core', 'Release')
        release = LegacyRelease.objects.create(study_id=study.pk, build_id=build.pk, approved=True,
                                              config={'purpose': 'synthetic', 'mode': 'anonymous', 'old': 'byte-structure'})
        LegacyParticipant = legacy.get_model('core', 'Participant')
        participant = LegacyParticipant.objects.create(study_id=study.pk, code='001')
        LegacySession = legacy.get_model('core', 'Session')
        session = LegacySession.objects.create(participant_id=participant.pk, release_id=release.pk, operation=uuid.uuid4(),
                                               proof_hash='p' * 64, request={'operation_id': 'legacy', 'release_id': str(release.pk)},
                                               token_hash='t' * 64, expires_at=timezone.now() + timedelta(days=7))
        LegacyEvent = legacy.get_model('core', 'Event')
        event = LegacyEvent.objects.create(session_id=session.pk, event_id=uuid.uuid4(), segment_id=uuid.uuid4(),
                                           sequence=1, envelope={'payload': {'rt_ms': 321.5}})
        LegacyExport = legacy.get_model('core', 'Export')
        exported = LegacyExport.objects.create(study_id=study.pk, snapshot={'records': [{'record': {'rt_ms': 321.5}}], 'format_version': '1'})
        before = {
            'study': {'id': study.pk, 'title': study.title, 'mode': study.mode, 'recruitment': study.recruitment, 'max_sessions': study.max_sessions},
            'release': {'id': release.pk, 'study_id': str(release.study_id), 'build_id': str(release.build_id),
                        'config': release.config, 'approved': release.approved},
            'session': {'id': session.pk, 'release_id': str(session.release_id), 'operation': str(session.operation),
                        'proof_hash': session.proof_hash, 'token_hash': session.token_hash, 'request': session.request},
            'event': {'id': event.pk, 'session_id': str(event.session_id), 'event_id': str(event.event_id), 'envelope': event.envelope},
            'export': {'id': exported.pk, 'study_id': str(exported.study_id), 'snapshot': exported.snapshot},
        }
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    # New fields default to private with no guessed current release.
    study_after = Study.objects.get(pk=before['study']['id'])
    assert study_after.public is False and study_after.current_release_id is None
    assert study_after.show_closed_summary is False and study_after.revision == 0
    assert study_after.public_summary == '' and study_after.public_duration == '' and study_after.public_device_requirements == ''
    assert (study_after.title, study_after.mode, study_after.recruitment, study_after.max_sessions) == (
        before['study']['title'], before['study']['mode'], before['study']['recruitment'], before['study']['max_sessions'])

    # Legacy release, session, event and export identities and content survive byte for byte.
    release_after = Release.objects.select_related('build').get(pk=before['release']['id'])
    assert release_after.config == before['release']['config'] and release_after.approved is True
    assert str(release_after.study_id) == before['release']['study_id'] and str(release_after.build_id) == before['release']['build_id']
    session_after = Session.objects.get(pk=before['session']['id'])
    assert str(session_after.release_id) == before['session']['release_id']
    assert session_after.request == before['session']['request'] and session_after.proof_hash == before['session']['proof_hash']
    assert session_after.token_hash == before['session']['token_hash'] and str(session_after.operation) == before['session']['operation']
    event_after = Event.objects.get(pk=before['event']['id'])
    assert event_after.envelope == before['event']['envelope'] and str(event_after.session_id) == before['event']['session_id']
    export_after = Export.objects.get(pk=before['export']['id'])
    assert export_after.snapshot == before['export']['snapshot'] and str(export_after.study_id) == before['export']['study_id']

    # The legacy direct-release URL still admits against its frozen binding, not a current release.
    direct = {'operation_id': str(uuid.uuid4()), 'proof': 'q' * 48, 'instance_id': str(Instance.objects.get(pk=1).instance_id),
              'study_id': str(study_after.id), 'release_id': str(release_after.id), 'build_id': str(release_after.build_id)}
    session_new, _ = admit_request(direct)
    assert session_new.release_id == release_after.id and session_new.id != session_after.id


@pytest.mark.django_db
def test_selection_requires_authority_revision_and_same_study_approval():
    context = world()
    owner, study = context['owner'], context['study']
    first, second = context['first'], context['second']
    unapproved = make_release(study, 'v3', approved=False, package='third.zip')
    unpackaged = make_release(study, 'v4', approved=True, package='')
    other_study = Study.objects.create(title='Other publication')
    foreign = make_release(other_study, 'v1', package='foreign.zip')

    # Explicit authority: a study viewer alone cannot select, an anonymous caller never can.
    viewer = get_user_model().objects.create_user('publication_viewer', password=OWNER_PASSWORD)
    grant(viewer, study, 'study.view')
    with pytest.raises(Rejected, match='forbidden'):
        publication.select_current_release(viewer, study, str(study.revision), str(first.id))
    from django.contrib.auth.models import AnonymousUser
    with pytest.raises(Rejected, match='forbidden'):
        publication.select_current_release(AnonymousUser(), study, str(study.revision), str(first.id))

    result = publication.select_current_release(owner, study, str(study.revision), str(first.id))
    study.refresh_from_db()
    assert study.current_release_id == first.id and study.revision == 1 and result['after'] == {'current_release': str(first.id)}
    audit = Audit.objects.get(study=study, action=publication.RELEASE_CHANGED)
    assert audit.before == {'current_release': None} and audit.after == {'current_release': str(first.id)}

    # Same-study, approved and resource-located only.
    with pytest.raises(Rejected, match='revision_conflict'):
        publication.select_current_release(owner, study, '0', str(second.id))
    with pytest.raises(Rejected, match='release_not_found'):
        publication.select_current_release(owner, study, '1', str(foreign.id))
    with pytest.raises(Rejected, match='release_unapproved'):
        publication.select_current_release(owner, study, '1', str(unapproved.id))
    with pytest.raises(Rejected, match='release_unavailable'):
        publication.select_current_release(owner, study, '1', str(unpackaged.id))
    study.refresh_from_db()
    assert study.current_release_id == first.id and study.revision == 1
    assert Audit.objects.filter(study=study, action=publication.RELEASE_CHANGED).count() == 1

    # A stale revision cannot switch and cannot silently clear the current release.
    with pytest.raises(Rejected, match='revision_conflict'):
        publication.select_current_release(owner, study, '0', str(second.id))
    with pytest.raises(Rejected, match='revision_conflict'):
        publication.select_current_release(owner, study, None, '')
    with pytest.raises(Rejected, match='no_change'):
        publication.select_current_release(owner, study, '1', str(first.id))

    cleared = publication.select_current_release(owner, study, '1', '')
    study.refresh_from_db()
    assert cleared['after'] == {'current_release': None} and study.current_release_id is None and study.revision == 2


@pytest.mark.django_db
def test_selection_accepts_recruitment_or_configuration_authority():
    context = world()
    study = context['study']
    recruiter = get_user_model().objects.create_user('publication_recruiter', password=OWNER_PASSWORD)
    configurator = get_user_model().objects.create_user('publication_configurator', password=OWNER_PASSWORD)
    grant(recruiter, study, 'study.view', 'recruitment.manage')
    grant(configurator, study, 'study.view', 'study.configure')

    # Recruitment authority selects the current release but cannot edit the public policy.
    publication.select_current_release(recruiter, study, '0', str(context['first'].id))
    study.refresh_from_db()
    with pytest.raises(Rejected, match='forbidden'):
        publication.update_policy(recruiter, study, str(study.revision), {'public': True})
    # Configuration authority can publish and can also select the current release.
    publication.update_policy(configurator, study, str(study.revision),
                              {'public': True, 'public_summary': 'synthetic summary'})
    study.refresh_from_db()
    publication.select_current_release(configurator, study, str(study.revision), str(context['second'].id))
    study.refresh_from_db()
    assert study.current_release_id == context['second'].id and study.public is True


@pytest.mark.django_db
def test_policy_update_bounds_revision_and_audit():
    context = world()
    owner, study = context['owner'], context['study']
    result = publication.update_policy(owner, study, '0', {
        'public': True, 'public_summary': '合成公开简介', 'public_duration': '约 15 分钟',
        'public_device_requirements': '桌面 Chrome', 'show_closed_summary': True})
    study.refresh_from_db()
    assert study.public is True and study.show_closed_summary is True
    assert study.public_summary == '合成公开简介' and study.public_duration == '约 15 分钟'
    assert study.public_device_requirements == '桌面 Chrome' and study.revision == 1
    audit = Audit.objects.get(study=study, action=publication.POLICY_CHANGED)
    assert audit.before == {'public': False, 'public_summary': '', 'public_duration': '',
                            'public_device_requirements': '', 'show_closed_summary': False}
    assert audit.after['public'] is True and audit.after['public_summary'] == '合成公开简介'
    assert result['revision'] == 1

    # Bounds, staleness and no-op are refused without partial writes.
    with pytest.raises(Rejected, match='policy_field'):
        publication.update_policy(owner, study, '1', {'public': True, 'public_summary': 'x' * 281})
    with pytest.raises(Rejected, match='revision_conflict'):
        publication.update_policy(owner, study, '0', {'public': False})
    with pytest.raises(Rejected, match='no_change'):
        publication.update_policy(owner, study, '1', {
            'public': True, 'public_summary': '合成公开简介', 'public_duration': '约 15 分钟',
            'public_device_requirements': '桌面 Chrome', 'show_closed_summary': True})
    study.refresh_from_db()
    assert study.public is True and study.revision == 1
    assert Audit.objects.filter(study=study, action=publication.POLICY_CHANGED).count() == 1

    viewer = get_user_model().objects.create_user('publication_policy_viewer', password=OWNER_PASSWORD)
    grant(viewer, study, 'study.view')
    with pytest.raises(Rejected, match='forbidden'):
        publication.update_policy(viewer, study, '1', {'public': False})


@pytest.mark.django_db
def test_publication_rejects_deactivated_actor_without_writes():
    """A cached actor object cannot authorize a write after the account row was
    deactivated; the service re-reads the live account inside the transaction."""
    context = world()
    owner, study, first = context['owner'], context['study'], context['first']
    cached = get_user_model().objects.get(pk=owner.pk)
    get_user_model().objects.filter(pk=owner.pk).update(is_active=False)
    with pytest.raises(Rejected, match='forbidden'):
        publication.update_policy(cached, study, '0', {'public': True, 'public_summary': '不应写入'})
    with pytest.raises(Rejected, match='forbidden'):
        publication.select_current_release(cached, study, '0', str(first.id))
    study.refresh_from_db()
    assert study.public is False and study.public_summary == '' and study.current_release_id is None and study.revision == 0
    assert Audit.objects.filter(study=study).count() == 0
    # Reactivation is honored from the live row too: the refusal is the current state, not a cached role.
    get_user_model().objects.filter(pk=owner.pk).update(is_active=True)
    publication.select_current_release(get_user_model().objects.get(pk=owner.pk), study, '0', str(first.id))
    study.refresh_from_db()
    assert study.current_release_id == first.id and study.revision == 1


@pytest.mark.django_db
def test_admit_rechecks_cached_release_state_inside_the_transaction():
    """A release object resolved earlier must not admit under a superseded
    approval or recruitment state; the session write re-reads both rows."""
    context = world()
    study, first = context['study'], context['first']
    cached = Release.objects.select_related('study', 'build').get(pk=first.id)
    assert cached.approved is True and cached.study.recruitment == 'open'

    Study.objects.filter(pk=study.pk).update(recruitment='closed')
    request = entry_request(context, first, study.revision)
    with pytest.raises(Rejected, match='admission_closed'):
        admit(cached, request)
    assert not Session.objects.filter(operation=request['operation_id']).exists()

    Study.objects.filter(pk=study.pk).update(recruitment='open')
    Release.objects.filter(pk=first.pk).update(approved=False)
    request = entry_request(context, first, study.revision)
    with pytest.raises(Rejected, match='admission_closed'):
        admit(cached, request)
    assert not Session.objects.filter(operation=request['operation_id']).exists()

    Release.objects.filter(pk=first.pk).update(approved=True)
    request = entry_request(context, first, study.revision)
    session, token = admit(cached, request)
    assert session.release_id == first.id and token and Session.objects.count() == 1


@pytest.mark.django_db
def test_stable_entry_admission_binds_observed_release_and_fails_stale():
    """The observed release/revision binding is authoritative over a legacy
    ``release_id`` carried in the same request; a superseded binding never admits."""
    context = world(title='Entry API synthetic')
    study, first, second = context['study'], context['first'], context['second']
    publication.select_current_release(context['owner'], study, '0', str(first.id))
    study.refresh_from_db()

    accepted = post_admission(entry_request(context, first, study.revision))
    assert accepted.status_code == 200 and accepted.json()['release_id'] == str(first.id)

    publication.select_current_release(context['owner'], study, str(study.revision), str(second.id))
    study.refresh_from_db()

    # The application context carries release_id and the observed binding; the
    # binding wins, so a superseded page fails closed instead of silently
    # admitting through the frozen direct-release path.
    stale = entry_request(context, first, 1)
    stale['release_id'] = str(first.id)
    failed = post_admission(stale)
    assert failed.status_code == 409 and failed.json()['code'] == 'stale_entry'
    assert not Session.objects.filter(operation=stale['operation_id']).exists()

    # A pure legacy direct-release request keeps its frozen contract on R1.
    legacy = {'operation_id': str(uuid.uuid4()), 'proof': 'l' * 48,
              'instance_id': str(context['instance'].instance_id), 'study_id': str(study.id),
              'release_id': str(first.id), 'build_id': str(first.build_id)}
    frozen = post_admission(legacy)
    assert frozen.status_code == 200 and frozen.json()['release_id'] == str(first.id)

    current = post_admission(entry_request(context, second, study.revision))
    assert current.status_code == 200 and current.json()['release_id'] == str(second.id)


@pytest.mark.django_db
def test_operation_retry_returns_original_session_before_new_current_policy():
    context = world()
    study, first, second = context['study'], context['first'], context['second']
    publication.select_current_release(context['owner'], study, '0', str(first.id))
    study.refresh_from_db()
    request = entry_request(context, first, study.revision)
    session, token = admit_request(request)
    assert session.release_id == first.id

    publication.select_current_release(context['owner'], study, str(study.revision), str(second.id))
    study.refresh_from_db()
    study.recruitment = 'closed'
    study.save()

    replayed, replayed_token = admit_request(dict(request))
    assert replayed.id == session.id and replayed_token == token and Session.objects.count() == 1
    with pytest.raises(Rejected, match='operation_conflict'):
        admit_request(dict(request, proof='q' * 48))
    with pytest.raises(Rejected, match='wrong_binding'):
        admit_request(dict(request, expected_release_id=str(second.id)))
    with pytest.raises(Rejected, match='admission_closed'):
        admit_request(entry_request(context, second, study.revision))


@pytest.mark.django_db
def test_switch_keeps_old_session_uploads_recovery_config_export_and_resources(tmp_path, settings, monkeypatch):
    monkeypatch.setenv('GEP_PUBLIC_API', 'http://experiment.localhost:8123')
    settings.DATA_DIR = tmp_path
    package_root = tmp_path / 'packages'
    package_root.mkdir()
    (package_root / 'first.zip').write_bytes(FIRST_PACKAGE)
    (package_root / 'second.zip').write_bytes(SECOND_PACKAGE)
    context = world()
    study, first, second = context['study'], context['first'], context['second']
    owner = context['owner']
    grant(owner, study, 'data.export_raw', 'session.recover')
    publication.select_current_release(owner, study, '0', str(first.id))
    study.refresh_from_db()
    session, token = admit_request(entry_request(context, first, study.revision))
    receive(session.id, token, {'batch_id': str(uuid.uuid4()), 'events': [event_for(session)]})

    client = Client()
    client.force_login(owner)
    resource_url = f'/run/{first.id}/web/index.html'
    open_url = f'{settings.EXPERIMENT_HOST}'
    resource_before = client.get(resource_url, HTTP_HOST=open_url)
    config_before = client.get(f'/releases/{first.id}/config')
    export_before = client.post('/v1/admin/exports', {'study_id': str(study.id)}, content_type='application/json')
    assert resource_before.status_code == 200 and config_before.status_code == 200 and export_before.status_code == 201
    download_url = f"/v1/admin/exports/{export_before.json()['export_id']}/download"
    download_before = client.get(download_url)
    package_before = hashlib.sha256((package_root / 'first.zip').read_bytes()).hexdigest()
    context_before = dict(connection_config(first))

    publication.select_current_release(owner, study, str(study.revision), str(second.id))
    study.refresh_from_db()
    assert study.current_release_id == second.id

    # Old session keeps its release binding for uploads, recovery and context.
    assert receive(session.id, token, {'batch_id': str(uuid.uuid4()), 'events': [event_for(session, sequence=2)]})['accepted']
    session.refresh_from_db()
    assert session.release_id == first.id
    permit = uuid.uuid4().hex
    RecoveryPermit.objects.create(session=session, issuer=owner, token_hash=digest(permit), expires_at=timezone.now() + timedelta(minutes=15))
    recovered = recover(session.id, 'p' * 48, permit)
    assert recovered['session_id'] == str(session.id) and Session.objects.get(pk=session.pk).release_id == first.id

    # Config, resource bytes, downloads and package bytes are untouched.
    assert client.get(resource_url, HTTP_HOST=open_url).content == resource_before.content
    assert client.get(f'/releases/{first.id}/config').content == config_before.content
    assert client.get(download_url).content == download_before.content
    assert hashlib.sha256((package_root / 'first.zip').read_bytes()).hexdigest() == package_before
    first.refresh_from_db()
    assert connection_config(first) == context_before and first.config == {'purpose': 'synthetic', 'mode': 'anonymous', 'tag': 'v1'}
    assert first.approved is True and first.build.package_path == 'first.zip'
    assert hashlib.sha256((package_root / 'second.zip').read_bytes()).hexdigest() == hashlib.sha256(SECOND_PACKAGE).hexdigest()


def test_concurrent_current_release_switches_compare_revision(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', 'tests/phase03_release_concurrency_probe.py'],
        cwd=root, env={**os.environ, 'GEP_TEST_DB_FILE': str(tmp_path / 'release_concurrency.sqlite3')},
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed' in result.stdout


def test_concurrent_first_admission_crosses_switch_and_close(tmp_path):
    """Real file-database concurrency: a first stable-entry admission crossing an
    open switch/close write must be serializable, never read a cached state."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', 'tests/phase03_admission_concurrency_probe.py'],
        cwd=root, env={**os.environ, 'GEP_TEST_DB_FILE': str(tmp_path / 'admission_concurrency.sqlite3')},
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '4 passed' in result.stdout
