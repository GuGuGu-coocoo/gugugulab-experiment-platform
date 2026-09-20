"""P0304 server-side evidence: six-digit one-time recovery codes.

Every expectation is asserted against the real database, HTTP route and audit
rows. The six digits are taken from the issuing response only; storage and
throttle keys are checked for the absence of the raw code and proof. The
existing long-permit contract keeps its own tests in ``test_recovery.py``.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import Client
from django.utils import timezone

from core.models import (Audit, Build, Grant, Instance, Participant, RecoveryCode, RecoveryPermit,
                         Release, Session, Study, Throttle)
from core.protocol import Rejected
from core.services import (RECOVERY_CODE_CAPABILITY, RECOVERY_CODE_MAX_ATTEMPTS, RECOVERY_NAMED_CAPABILITY,
                           admit_request, authorize_session, digest, finish, issue_recovery_code, receive,
                           recover, redeem_recovery_code)

DENIAL = {'code': 'recovery_denied', 'retryable': False, 'outcome_unknown': False}


def grant(user, study, *actions):
    for action in actions:
        Grant.objects.get_or_create(user=user, study=study, action=action, defaults={'delegable': True})


def authorized(world):
    grant(world['owner'], world['study'], 'study.view', 'session.recover')
    return world['owner']


def issue(world, issuer=None):
    return issue_recovery_code(issuer or authorized(world), world['session'].id)['code']


def code_data(world, code, proof=None, **extra):
    data = {'capability': RECOVERY_CODE_CAPABILITY, 'code': code,
            'proof': world['request']['proof'],
            'instance_id': str(world['instance'].instance_id),
            'study_id': str(world['study'].id),
            'release_id': str(world['release'].id),
            'build_id': str(world['release'].build_id)}
    if proof is not None:
        data['proof'] = proof
    data.update(extra)
    return data


def post_recovery(data, host='experiment.localhost', remote='10.0.0.1'):
    return Client().post('/v1/participant/recovery', data, content_type='application/json',
                         HTTP_HOST=host, REMOTE_ADDR=remote)


def wrong_proof(attempt):
    return f'{attempt:02d}' + 'q' * 46


def password_world(title='Named synthetic', mode_in_config='password'):
    """Owner + password-mode study whose release config carries the frozen mode."""
    owner = get_user_model().objects.create_user(f'named_recovery_owner_{uuid.uuid4().hex[:10]}',
                                                 password='synthetic-test-password')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title=title, mode='password', recruitment='open', max_sessions=3)
    descriptor = {'schemas': {'exp.rt': {'id': 'rt', 'version': '1', 'schema': {
        'type': 'object', 'properties': {'rt_ms': {'type': 'number'}}, 'required': ['rt_ms'],
        'additionalProperties': False}}}, 'codebook': {'rt_ms': {'unit': 'ms', 'source': 'host'}}}
    build = Build.objects.create(study=study, descriptor=descriptor, digest=uuid.uuid4().hex)
    config = {'purpose': 'synthetic'}
    if mode_in_config is not None:
        config['mode'] = mode_in_config
    release = Release.objects.create(study=study, build=build, config=config, approved=True)
    participant = Participant.objects.create(study=study, code='P001',
                                             password_hash=make_password('correct horse battery'))
    request = {'operation_id': str(uuid.uuid4()), 'proof': 'r' * 48, 'instance_id': str(instance.instance_id),
               'study_id': str(study.id), 'release_id': str(release.id), 'build_id': str(build.id),
               'participant_code': 'P001', 'password': 'correct horse battery'}
    session, token = admit_request(request)
    return {'owner': owner, 'instance': instance, 'study': study, 'release': release, 'build': build,
            'participant': participant, 'session': session, 'token': token, 'request': request}


def named_data(world, **extra):
    data = {'capability': RECOVERY_NAMED_CAPABILITY, 'participant_code': 'P001',
            'password': 'correct horse battery', 'proof': world['request']['proof'],
            'instance_id': str(world['instance'].instance_id), 'study_id': str(world['study'].id),
            'release_id': str(world['release'].id), 'build_id': str(world['release'].build_id)}
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# Issuance
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_issue_requires_current_authority_and_binds_the_ticket(setup):
    owner, study, session = setup['owner'], setup['study'], setup['session']
    with pytest.raises(Rejected, match='forbidden'):
        issue_recovery_code(owner, session.id)
    # session.recover without the study.view prerequisite is still refused.
    grant(owner, study, 'session.recover')
    with pytest.raises(Rejected, match='forbidden'):
        issue_recovery_code(owner, session.id)
    grant(owner, study, 'study.view')

    issued = issue_recovery_code(owner, session.id)
    assert issued['capability'] == RECOVERY_CODE_CAPABILITY
    assert re.fullmatch(r'\d{6}', issued['code'])
    assert issued['attempts_allowed'] == RECOVERY_CODE_MAX_ATTEMPTS
    assert timedelta(minutes=4) < issued['expires_at'] - timezone.now() <= timedelta(minutes=5)

    ticket = RecoveryCode.objects.get(session=session)
    assert ticket.study_id == study.id and ticket.release_id == session.release_id
    assert ticket.issuer_id == owner.pk and ticket.consumed is False and ticket.superseded is False
    assert ticket.attempts == 0 and ticket.last_attempt_at is None
    # The six digits are never an enumerable unsalted hash.
    assert ticket.code_hash != hashlib.sha256(issued['code'].encode()).hexdigest()
    assert re.fullmatch(r'[0-9a-f]{64}', ticket.code_hash)

    # A deactivated or revoked issuer cannot issue; the account row is re-read.
    get_user_model().objects.filter(pk=owner.pk).update(is_active=False)
    with pytest.raises(Rejected, match='forbidden'):
        issue_recovery_code(owner, session.id)
    get_user_model().objects.filter(pk=owner.pk).update(is_active=True)
    session.revoked = True
    session.save(update_fields=['revoked'])
    with pytest.raises(Rejected, match='not_recoverable'):
        issue_recovery_code(owner, session.id)
    assert RecoveryCode.objects.filter(session=session).count() == 1


@pytest.mark.django_db
def test_issue_is_rate_limited_per_study_and_issuer(setup, monkeypatch):
    monkeypatch.setattr('core.throttle.time.time', lambda: 1800000000)
    for _ in range(10):
        issue_recovery_code(authorized(setup), setup['session'].id)
    with pytest.raises(Rejected, match='rate_limited') as rejected:
        issue_recovery_code(authorized(setup), setup['session'].id)
    assert rejected.value.status == 429
    assert RecoveryCode.objects.filter(session=setup['session']).count() == 10


# ---------------------------------------------------------------------------
# Redemption: proof, binding, disclosure and attempt limits
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_redemption_requires_device_proof_and_fixed_binding(setup):
    world = setup
    session = world['session']

    # The code alone cannot restore anything and discloses no session.
    alone = post_recovery({key: value for key, value in code_data(world, issue(world)).items() if key != 'proof'})
    assert alone.status_code == 422
    assert 'session_id' not in alone.json() and str(session.id) not in alone.content.decode()

    mismatches = [
        ('proof', wrong_proof(1)),
        ('study_id', str(Study.objects.create(title='Other study').id)),
        ('release_id', str(uuid.uuid4())),
        ('build_id', str(uuid.uuid4())),
        ('instance_id', str(uuid.uuid4())),
    ]
    baseline = None
    for field, value in mismatches:
        response = post_recovery(code_data(world, issue(world), **{field: value}))
        assert response.status_code == 403, (field, response.content)
        assert response.json() == DENIAL, field
        assert str(session.id) not in response.content.decode()
        assert str(world['instance'].instance_id) not in response.content.decode()
        baseline = baseline or response.json()
        assert response.json() == baseline

    # The original device still redeems the newest code, then a replay is dead.
    final_code = issue(world)
    accepted = post_recovery(code_data(world, final_code))
    assert accepted.status_code == 200
    body = accepted.json()
    assert set(body) == {'capability', 'session_id', 'token', 'task_finished'}
    assert body['capability'] == RECOVERY_CODE_CAPABILITY and body['session_id'] == str(session.id)
    assert body['task_finished'] is False
    authorize_session(Session.objects.get(pk=session.pk), body['token'])
    replay = post_recovery(code_data(world, final_code))
    assert replay.status_code == 403 and replay.json() == DENIAL
    assert RecoveryCode.objects.get(session=session, superseded=False).consumed is True


@pytest.mark.django_db
def test_failed_attempts_commit_and_cap_at_five(setup):
    world = setup
    session = world['session']
    original_expiry = session.expires_at
    code = issue(world)
    data = code_data(world, code)
    for attempt in range(1, RECOVERY_CODE_MAX_ATTEMPTS + 1):
        response = post_recovery(dict(data, proof=wrong_proof(attempt)))
        assert response.status_code == 403 and response.json() == DENIAL
        # Committed even though the request returned a failure.
        assert RecoveryCode.objects.get(session=session).attempts == attempt
    sixth = post_recovery(data)
    assert sixth.status_code == 403 and sixth.json() == DENIAL
    ticket = RecoveryCode.objects.get(session=session)
    assert ticket.attempts == RECOVERY_CODE_MAX_ATTEMPTS and ticket.consumed is False
    session.refresh_from_db()
    assert session.expires_at == original_expiry
    assert not Audit.objects.filter(action='recovery.code_redeemed').exists()


@pytest.mark.django_db
def test_expiry_and_newer_issuance_invalidate_older_codes(setup):
    world = setup
    session = world['session']
    first = issue(world)
    RecoveryCode.objects.filter(session=session).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert post_recovery(code_data(world, first)).status_code == 403

    second = issue(world)
    assert second != first
    live = RecoveryCode.objects.get(session=session, superseded=False)
    old = RecoveryCode.objects.get(session=session, superseded=True)
    assert live.code_hash != old.code_hash
    assert post_recovery(code_data(world, first)).status_code == 403
    assert post_recovery(code_data(world, second)).status_code == 200

    third = issue(world)
    assert RecoveryCode.objects.filter(session=session, superseded=False, consumed=False).count() == 1
    RecoveryCode.objects.filter(session=session, superseded=False).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert post_recovery(code_data(world, third)).status_code == 403


@pytest.mark.django_db
def test_redeem_rechecks_live_issuer_authority(setup):
    world = setup
    owner, study, session = world['owner'], world['study'], world['session']
    code = issue(world)
    Grant.objects.filter(user=owner, study=study, action='session.recover').delete()
    assert post_recovery(code_data(world, code)).status_code == 403
    grant(owner, study, 'session.recover')
    get_user_model().objects.filter(pk=owner.pk).update(is_active=False)
    assert post_recovery(code_data(world, code)).status_code == 403
    get_user_model().objects.filter(pk=owner.pk).update(is_active=True)
    accepted = post_recovery(code_data(world, code))
    assert accepted.status_code == 200 and accepted.json()['session_id'] == str(session.id)


@pytest.mark.django_db
def test_one_abusive_client_is_bounded_without_locking_another_client(setup, monkeypatch):
    """A single client's failed guesses are bounded without locking another
    client that shares the same study."""
    monkeypatch.setattr('core.throttle.time.time', lambda: 1800000000)
    world = setup
    world['session'].expires_at = timezone.now() - timedelta(seconds=1)
    world['session'].save(update_fields=['expires_at'])
    code = issue(world)
    binding = code_data(world, code)
    assert code != '000000'
    unknown = {'capability': RECOVERY_CODE_CAPABILITY, 'code': '000000', 'proof': 'z' * 48,
               'instance_id': binding['instance_id'], 'study_id': binding['study_id'],
               'release_id': binding['release_id'], 'build_id': binding['build_id']}

    for _ in range(20):
        assert post_recovery(unknown, remote='10.0.0.9').status_code == 403
    blocked = post_recovery(unknown, remote='10.0.0.9')
    assert blocked.status_code == 429 and blocked.json()['code'] == 'rate_limited'

    other = post_recovery(binding, remote='10.0.0.10')
    assert other.status_code == 200 and other.json()['session_id'] == str(world['session'].id)


@pytest.mark.django_db
def test_instance_wide_ceiling_bounds_distributed_guessing(setup, monkeypatch):
    """Three bounded clients on three bounded studies still fill one shared
    instance ceiling; then even a legitimate redemption from a fresh client is
    refused until the window moves, which is the global abuse bound."""
    monkeypatch.setattr('core.throttle.time.time', lambda: 1800000000)
    world = setup
    binding = code_data(world, issue(world))

    for index, client in enumerate(('10.0.0.21', '10.0.0.22', '10.0.0.23')):
        study = Study.objects.create(title=f'Distributed abuse {index}')
        body = {'capability': RECOVERY_CODE_CAPABILITY, 'code': '000000', 'proof': f'{index}' + 'z' * 47,
                'instance_id': binding['instance_id'], 'study_id': str(study.id),
                'release_id': binding['release_id'], 'build_id': binding['build_id']}
        for _ in range(20):
            assert post_recovery(body, remote=client).status_code == 403
    # Client and study counters are fresh for this request; only the shared
    # instance ceiling can refuse it.
    fresh = post_recovery(binding, remote='10.0.0.29')
    assert fresh.status_code == 429 and fresh.json()['code'] == 'rate_limited'


@pytest.mark.django_db
def test_no_code_or_proof_is_stored_in_throttle_or_audit(setup):
    world = setup
    code = issue(world)
    proof = 'w' * 48
    for _ in range(2):
        assert post_recovery(code_data(world, code, proof=proof)).status_code == 403

    rows = list(Throttle.objects.all())
    assert rows
    for row in rows:
        assert re.fullmatch(r'[0-9a-f]{64}', row.key)
        assert code not in row.key and proof not in row.key and world['request']['proof'] not in row.key
    ticket = RecoveryCode.objects.get(session=world['session'])
    assert code not in ticket.code_hash and proof not in ticket.code_hash
    issued = Audit.objects.get(action='recovery.code_issued')
    blob = json.dumps({'target': issued.target, 'before': issued.before, 'after': issued.after})
    assert code not in blob and proof not in blob and issued.actor_id == world['owner'].pk
    assert 'expires_at' in issued.after and 'capability' in issued.after


# ---------------------------------------------------------------------------
# Finished data-only compatibility and revoked sessions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_expired_finished_queue_recovers_data_only_with_closed_completion(setup, event):
    world = setup
    session, token = world['session'], world['token']
    declaration = {'event_ids': [event['event_id']], 'segment_ids': [event['segment_id']]}
    assert finish(session.id, token, declaration)['state'] == 'pending'
    session.expires_at = timezone.now() - timedelta(seconds=1)
    session.save(update_fields=['expires_at'])
    code = issue(world)

    accepted = post_recovery(code_data(world, code))
    assert accepted.status_code == 200
    body = accepted.json()
    assert body['task_finished'] is True and set(body) == {'capability', 'session_id', 'token', 'task_finished'}
    session.refresh_from_db()
    assert session.completion == declaration
    assert session.expires_at > timezone.now()

    # The original declared event can still be uploaded, but the closed set is
    # not expanded and no trial is replayed by the server.
    assert receive(session.id, body['token'], {'batch_id': str(uuid.uuid4()), 'events': [event]})['accepted'] == [event['event_id']]
    extra = {**event, 'event_id': str(uuid.uuid4()), 'sequence': 2}
    with pytest.raises(Rejected, match='completion_closed'):
        receive(session.id, body['token'], {'batch_id': str(uuid.uuid4()), 'events': [extra]})
    assert finish(session.id, body['token'], declaration)['state'] == 'complete'
    assert session.completion == declaration


@pytest.mark.django_db
def test_code_cannot_bypass_revocation_and_legacy_permit_coexists(setup):
    world = setup
    session, proof = world['session'], world['request']['proof']
    code = issue(world)
    permit = RecoveryPermit.objects.create(session=session, issuer=world['owner'], token_hash=digest('legacy-permit'),
                                           expires_at=timezone.now() + timedelta(minutes=15))
    legacy = recover(session.id, proof, 'legacy-permit')
    assert legacy['session_id'] == str(session.id)
    permit.refresh_from_db()
    assert permit.consumed is True
    # A consumed long permit does not disturb the separately issued code.
    assert post_recovery(code_data(world, code)).status_code == 200

    # A revoked session stays unrecoverable through the code path.
    session.revoked = True
    session.save(update_fields=['revoked'])
    with pytest.raises(Rejected, match='not_recoverable'):
        issue_recovery_code(authorized(world), session.id)
    with pytest.raises(Rejected, match='recovery_denied'):
        redeem_recovery_code(code_data(world, code), '10.0.0.1')


# ---------------------------------------------------------------------------
# Versioned route
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_recovery_route_is_versioned_and_host_bound(setup):
    world = setup
    code = issue(world)
    data = code_data(world, code)
    client = Client()
    assert client.get('/v1/participant/recovery', HTTP_HOST='experiment.localhost').status_code == 405
    for bad in ({}, {'capability': 'recovery_code/v2'}, {'capability': 'recovery/named'}):
        response = client.post('/v1/participant/recovery', bad, content_type='application/json',
                               HTTP_HOST='experiment.localhost')
        assert response.status_code == 409 and response.json()['code'] == 'unsupported_capability'
    assert client.post('/v1/participant/recovery', data, content_type='application/json',
                       HTTP_HOST='admin.localhost').status_code == 403
    assert post_recovery(data).status_code == 200

    # Missing or malformed fields never reach an implicit code-only lookup.
    missing_binding = {'capability': RECOVERY_CODE_CAPABILITY, 'code': code, 'proof': 'x' * 48}
    malformed = {**data, 'code': '12345', 'proof': 'x' * 48}
    for bad_body in (missing_binding, malformed):
        response = post_recovery(bad_body)
        assert response.status_code == 422 and 'session_id' not in response.json()


# ---------------------------------------------------------------------------
# Named same-device continuation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_named_continuation_requires_proof_and_configured_credentials():
    world = password_world()
    session = world['session']
    session.expires_at = timezone.now() - timedelta(seconds=1)
    session.save(update_fields=['expires_at'])

    # A public participant ID alone restores nothing.
    alone = post_recovery({'capability': RECOVERY_NAMED_CAPABILITY, 'participant_code': 'P001',
                           'study_id': str(world['study'].id), 'instance_id': str(world['instance'].instance_id),
                           'release_id': str(world['release'].id), 'build_id': str(world['release'].build_id)})
    assert alone.status_code == 422 and str(session.id) not in alone.content.decode()

    for field, value in (('password', 'wrong password'), ('proof', 'q' * 48),
                         ('release_id', str(uuid.uuid4())), ('build_id', str(uuid.uuid4()))):
        response = post_recovery(named_data(world, **{field: value}))
        assert response.status_code == 403, (field, response.content)
        assert response.json() == DENIAL, field
        assert str(session.id) not in response.content.decode()

    accepted = post_recovery(named_data(world))
    assert accepted.status_code == 200
    body = accepted.json()
    assert set(body) == {'capability', 'session_id', 'token', 'task_finished'}
    assert body['capability'] == RECOVERY_NAMED_CAPABILITY and body['session_id'] == str(session.id)
    session.refresh_from_db()
    assert session.expires_at > timezone.now()
    audit = Audit.objects.get(action='recovery.named_redeemed')
    assert audit.actor is None and audit.target == str(session.id)


@pytest.mark.django_db
def test_named_continuation_denies_front_lock_inactive_expired_and_revoked():
    world = password_world()
    session, participant = world['session'], world['participant']

    locked = post_recovery(named_data(world, front_locked=True))
    assert locked.status_code == 403 and locked.json()['code'] == 'front_locked'

    participant.active = False
    participant.save(update_fields=['active'])
    assert post_recovery(named_data(world)).status_code == 403
    participant.active = True
    participant.expires_at = timezone.now() - timedelta(seconds=1)
    participant.save(update_fields=['active', 'expires_at'])
    assert post_recovery(named_data(world)).status_code == 403
    participant.expires_at = None
    participant.save(update_fields=['expires_at'])
    session.revoked = True
    session.save(update_fields=['revoked'])
    assert post_recovery(named_data(world)).status_code == 403
    session.revoked = False
    session.save(update_fields=['revoked'])
    assert post_recovery(named_data(world)).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize('mode', ['id', None])
def test_named_continuation_requires_the_frozen_password_mode(mode):
    world = password_world(title=f'Frozen {mode}', mode_in_config=mode)
    response = post_recovery(named_data(world))
    assert response.status_code == 403 and response.json() == DENIAL
    assert not Audit.objects.filter(action='recovery.named_redeemed').exists()


# ---------------------------------------------------------------------------
# Researcher GUI issuance
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_gui_issues_codes_only_for_authorized_issuers(setup):
    world = setup
    owner, study, session = world['owner'], world['study'], world['session']
    grant(owner, study, 'study.view', 'session.recover')
    client = Client()
    client.force_login(owner)
    url = f'/studies/{study.id}'
    page = client.get(url).content.decode()
    assert 'value="recover_code"' in page and '签发六位恢复码' in page

    response = client.post(url, {'op': 'recover_code', 'session_id': str(session.id)})
    assert response.status_code == 200
    match = re.search(r'六位恢复码[^：]*：(\d{6})', response.content.decode())
    assert match is not None
    code = match.group(1)
    ticket = RecoveryCode.objects.get(session=session, superseded=False)
    assert ticket.issuer_id == owner.pk and ticket.attempts == 0
    assert post_recovery(code_data(world, code)).status_code == 200

    # A viewer without session.recover cannot issue and writes no ticket.
    viewer = get_user_model().objects.create_user('recovery_viewer', password='synthetic-test-password')
    grant(viewer, study, 'study.view')
    viewer_client = Client()
    viewer_client.force_login(viewer)
    before = RecoveryCode.objects.count()
    assert viewer_client.post(url, {'op': 'recover_code', 'session_id': str(session.id)}).status_code == 403
    assert RecoveryCode.objects.count() == before


# ---------------------------------------------------------------------------
# Concurrency probe (real file database, threaded)
# ---------------------------------------------------------------------------

def test_concurrent_redemption_consumes_the_code_once(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', 'tests/phase03_recovery_code_concurrency_probe.py'],
        cwd=root, env={**os.environ, 'GEP_TEST_DB_FILE': str(tmp_path / 'recovery_code_concurrency.sqlite3')},
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed' in result.stdout
