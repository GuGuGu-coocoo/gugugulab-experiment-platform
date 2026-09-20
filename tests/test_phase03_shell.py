"""Phase 03 reusable GEC shell contract tests.

These tests cover the server-side frozen public configuration, the versioned
same-device recovery API the shell consumes, the real headless Godot shell
contract and the packaging boundary that keeps the scientific task unchanged.
They are non-interactive and fail (never skip) when the pinned Godot toolchain is
missing.
"""
import hashlib
import json
import re
import shutil
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import Client

from core.gui import connection_config
from core.models import Build, Grant, Instance, Participant, Release, Session, Study
from core.access import ACTIONS
from core.protocol import Rejected
from core.services import (RECOVERY_CODE_CAPABILITY, RECOVERY_NAMED_CAPABILITY, SHELL_CAPABILITY,
                           issue_recovery_code)

ROOT = Path(__file__).resolve().parents[1]
GODOT = shutil.which('godot')


def _godot() -> str:
    if GODOT is None:
        pytest.fail('godot executable not on PATH; the pinned Godot 4.7.2 toolchain is required')
    return GODOT


def frozen_config(release, mode):
    release.config = {'purpose': 'synthetic', 'mode': mode, 'max_sessions': 3,
                      'offline_policy': 'continue_local', 'recovery': 'trial_boundary_v1'}
    release.save(update_fields=['config'])
    return connection_config(release)


def test_connection_config_freezes_mode_without_credentials(setup):
    for mode in ('anonymous', 'id', 'password'):
        config = frozen_config(setup['release'], mode)
        assert config['mode'] == mode
        assert config['shell_capability'] == SHELL_CAPABILITY
        assert config['purpose'] == 'synthetic'
        assert set(config) <= {'config_version', 'protocol_version', 'sdk_version', 'api_url', 'instance_id', 'study_id',
                               'release_id', 'build_id', 'purpose', 'mode', 'shell_capability'}
        serialized = json.dumps(config)
        assert 'roster' not in serialized and 'participant' not in serialized and 'secret' not in serialized
    assert connection_config(setup['release'])['mode'] == 'password'


def test_legacy_release_keeps_pre_shell_contract(setup):
    """A release approved before the shell has no frozen mode: the shell must
    keep the legacy field set instead of guessing one."""
    config = connection_config(setup['release'])
    assert 'mode' not in config and 'shell_capability' not in config
    assert config['config_version'] == '1' and config['purpose'] == 'synthetic'


def test_config_endpoint_serves_frozen_mode(setup, db):
    owner = setup['owner']
    for action in ACTIONS:
        Grant.objects.get_or_create(user=owner, study=setup['study'], action=action, defaults={'delegable': True})
    frozen_config(setup['release'], 'id')
    client = Client()
    client.force_login(owner)
    response = client.get(f'/releases/{setup["release"].id}/config', HTTP_HOST='admin.localhost')
    assert response.status_code == 200
    config = json.loads(response.content)
    assert config['mode'] == 'id' and config['shell_capability'] == SHELL_CAPABILITY
    assert config['release_id'] == str(setup['release'].id) and config['build_id'] == str(setup['release'].build_id)


def test_recovery_api_requires_versioned_capability(setup, client):
    response = client.post('/v1/participant/recovery', data=json.dumps({'capability': 'recovery_code/v2'}),
                           content_type='application/json', HTTP_HOST='experiment.localhost')
    assert response.status_code == 409
    assert json.loads(response.content)['code'] == 'unsupported_capability'


def test_recovery_code_needs_device_proof_and_binding(setup, db):
    session = setup['session']
    for action in ('study.view', 'session.recover'):
        Grant.objects.get_or_create(user=setup['owner'], study=setup['study'], action=action, defaults={'delegable': True})
    binding = {'instance_id': str(setup['instance'].instance_id), 'study_id': str(setup['study'].id),
               'release_id': str(setup['release'].id), 'build_id': str(setup['release'].build_id)}
    client = Client()
    issued = issue_recovery_code(setup['owner'], session.id)
    assert re.fullmatch(r'\d{6}', issued['code'])
    # Wrong proof, wrong code and unknown binding all deny without disclosing a session.
    for body in ({'capability': RECOVERY_CODE_CAPABILITY, 'code': issued['code'], 'proof': 'q' * 48, **binding},
                 {'capability': RECOVERY_CODE_CAPABILITY, 'code': '000000', 'proof': 'p' * 48, **binding},
                 {'capability': RECOVERY_CODE_CAPABILITY, 'code': issued['code'], 'proof': 'p' * 48,
                  **{**binding, 'study_id': str(uuid.uuid4())}}):
        response = client.post('/v1/participant/recovery', data=json.dumps(body),
                               content_type='application/json', HTTP_HOST='experiment.localhost')
        assert response.status_code == 403
        payload = json.loads(response.content)
        assert payload['code'] == 'recovery_denied' and 'token' not in payload and 'session_id' not in payload
    # The original device proof redeems the code without any public UUID.
    redeemed = client.post('/v1/participant/recovery',
                           data=json.dumps({'capability': RECOVERY_CODE_CAPABILITY, 'code': issued['code'],
                                            'proof': 'p' * 48, **binding}),
                           content_type='application/json', HTTP_HOST='experiment.localhost')
    assert redeemed.status_code == 200
    payload = json.loads(redeemed.content)
    assert payload['session_id'] == str(session.id) and payload['token'] and payload['task_finished'] is False
    assert 'participant_code' not in payload and 'participant_uuid' not in payload
    # A consumed code cannot be replayed.
    replay = client.post('/v1/participant/recovery',
                         data=json.dumps({'capability': RECOVERY_CODE_CAPABILITY, 'code': issued['code'],
                                          'proof': 'p' * 48, **binding}),
                         content_type='application/json', HTTP_HOST='experiment.localhost')
    assert replay.status_code == 403


@pytest.fixture
def password_release(db):
    owner = get_user_model().objects.create_user('shell_owner', password='shell-owner-password')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='Shell password study', mode='password', recruitment='open', max_sessions=2)
    participant = Participant.objects.create(study=study, code='001', password_hash=make_password('synthetic-password'))
    build = Build.objects.create(study=study, descriptor={'platform': 'godot_web', 'version': 'synthetic-1'}, digest='b' * 64)
    release = Release.objects.create(study=study, build=build, approved=True,
                                     config={'purpose': 'synthetic', 'mode': 'password', 'max_sessions': 2})
    session = Session.objects.create(participant=participant, release=release, operation=uuid.uuid4(),
                                     proof_hash=hashlib.sha256(('p' * 48).encode()).hexdigest(),
                                     request={'operation_id': str(uuid.uuid4())},
                                     token_hash=hashlib.sha256(b'token').hexdigest(),
                                     expires_at=timezone.now() + timedelta(days=1))
    return {'owner': owner, 'instance': instance, 'study': study, 'release': release, 'build': build,
            'session': session, 'participant': participant}


def test_named_recovery_refuses_front_locked_and_wrong_password(password_release, client):
    binding = {'instance_id': str(password_release['instance'].instance_id), 'study_id': str(password_release['study'].id),
               'release_id': str(password_release['release'].id), 'build_id': str(password_release['build'].id)}
    base = {'capability': RECOVERY_NAMED_CAPABILITY, 'participant_code': '001', 'proof': 'p' * 48, **binding}
    locked = client.post('/v1/participant/recovery',
                         data=json.dumps({**base, 'password': 'synthetic-password', 'front_locked': True}),
                         content_type='application/json', HTTP_HOST='experiment.localhost')
    assert locked.status_code == 403 and json.loads(locked.content)['code'] == 'front_locked'
    wrong = client.post('/v1/participant/recovery',
                        data=json.dumps({**base, 'password': 'not-the-password', 'front_locked': False}),
                        content_type='application/json', HTTP_HOST='experiment.localhost')
    assert wrong.status_code == 403 and 'token' not in json.loads(wrong.content)
    matched = client.post('/v1/participant/recovery',
                          data=json.dumps({**base, 'password': 'synthetic-password', 'front_locked': False}),
                          content_type='application/json', HTTP_HOST='experiment.localhost')
    assert matched.status_code == 200
    payload = json.loads(matched.content)
    assert payload['session_id'] == str(password_release['session'].id) and payload['token']


# ---------------------------------------------------------------------------
# Frozen release mode vs. later study policy changes (real HTTP admission)
# ---------------------------------------------------------------------------

def admission_world(study_mode='anonymous', frozen=None, roster=True):
    """Owner + one approved release; ``frozen`` is the mode frozen into it.

    The study policy is written first and can be edited afterwards, exactly like
    a researcher policy change after the release was approved.
    """
    owner = get_user_model().objects.create_user(f'shell_admission_{uuid.uuid4().hex[:10]}',
                                                 password='synthetic-test-password')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title=f'Shell frozen {frozen}', mode=study_mode, recruitment='open', max_sessions=4)
    build = Build.objects.create(study=study, descriptor={'platform': 'godot_web', 'version': 'synthetic-1'}, digest='c' * 64)
    config = {'purpose': 'synthetic'}
    if frozen is not None:
        config['mode'] = frozen
    release = Release.objects.create(study=study, build=build, approved=True, config=config)
    if roster:
        Participant.objects.create(study=study, code='001', password_hash=make_password('synthetic-password'))
    return {'owner': owner, 'instance': instance, 'study': study, 'release': release, 'build': build}


def admit_post(world, **extra):
    body = {'operation_id': str(uuid.uuid4()), 'proof': 'a' * 48,
            'instance_id': str(world['instance'].instance_id), 'study_id': str(world['study'].id),
            'release_id': str(world['release'].id), 'build_id': str(world['build'].id)}
    body.update(extra)
    return Client().post('/v1/participant/sessions', body, content_type='application/json',
                         HTTP_HOST='experiment.localhost')


def admitted(response):
    assert response.status_code == 200, response.content
    payload = json.loads(response.content)
    return payload, Session.objects.get(pk=payload['session_id'])


@pytest.mark.django_db
def test_frozen_password_release_keeps_admission_after_study_policy_change():
    """A release approved as password keeps requiring the password even when the
    study is later switched to anonymous; the public config keeps the same mode."""
    world = admission_world(study_mode='password', frozen='password')
    world['study'].mode = 'anonymous'
    world['study'].save(update_fields=['mode'])
    assert connection_config(world['release'])['mode'] == 'password'

    payload, session = admitted(admit_post(world, participant_code='001', password='synthetic-password'))
    assert session.release_id == world['release'].id
    assert Session.objects.get(pk=payload['session_id']).participant.code == '001'

    assert admit_post(world, participant_code='001', password='wrong-password').status_code == 403
    assert admit_post(world, participant_code='001').status_code == 403
    assert admit_post(world).status_code == 403
    # The anonymous-looking request never created a second participant.
    assert Participant.objects.filter(study=world['study'], code__isnull=True).count() == 0


@pytest.mark.django_db
def test_frozen_anonymous_release_ignores_study_roster_policy():
    world = admission_world(study_mode='password', frozen='anonymous')
    world['study'].mode = 'password'
    world['study'].save(update_fields=['mode'])
    assert connection_config(world['release'])['mode'] == 'anonymous'

    refused = admit_post(world, participant_code='001', password='synthetic-password')
    assert refused.status_code == 422 and json.loads(refused.content)['code'] == 'unexpected_code'
    response = admit_post(world)
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert Session.objects.get(pk=payload['session_id']).participant.code is None


@pytest.mark.django_db
def test_frozen_id_release_requires_roster_id_after_study_policy_change():
    world = admission_world(study_mode='anonymous', frozen='id')
    world['study'].mode = 'anonymous'
    world['study'].save(update_fields=['mode'])
    assert connection_config(world['release'])['mode'] == 'id'

    assert admit_post(world).status_code == 403
    assert admit_post(world, participant_code='unknown').status_code == 403
    payload, _session = admitted(admit_post(world, participant_code='001'))
    assert Session.objects.get(pk=payload['session_id']).participant.code == '001'


@pytest.mark.django_db
def test_legacy_release_without_frozen_mode_follows_the_study_policy():
    """Legacy releases keep the pre-shell rule: the study policy decides."""
    world = admission_world(study_mode='password', frozen=None)
    assert 'mode' not in connection_config(world['release'])
    assert admit_post(world, participant_code='001', password='wrong-password').status_code == 403
    admitted(admit_post(world, participant_code='001', password='synthetic-password'))


@pytest.mark.django_db
def test_legacy_anonymous_release_still_refuses_a_roster_id():
    world = admission_world(study_mode='anonymous', frozen=None)
    assert 'mode' not in connection_config(world['release'])
    refused = admit_post(world, participant_code='001')
    assert refused.status_code == 422 and json.loads(refused.content)['code'] == 'unexpected_code'
    response = admit_post(world)
    assert response.status_code == 200
    assert Session.objects.get(pk=json.loads(response.content)['session_id']).participant.code is None


@pytest.mark.django_db
def test_unknown_frozen_mode_fails_closed_in_config_and_admission():
    world = admission_world(study_mode='anonymous', frozen='invite')
    with pytest.raises(Rejected) as rejected:
        connection_config(world['release'])
    assert rejected.value.code == 'unsupported_capability' and rejected.value.status == 409
    response = admit_post(world)
    assert response.status_code == 409 and json.loads(response.content)['code'] == 'unsupported_capability'
    assert Session.objects.filter(release=world['release']).count() == 0

    owner = world['owner']
    for action in ACTIONS:
        Grant.objects.get_or_create(user=owner, study=world['study'], action=action, defaults={'delegable': True})
    client = Client()
    client.force_login(owner)
    endpoint = client.get(f'/releases/{world["release"].id}/config', HTTP_HOST='admin.localhost')
    assert endpoint.status_code == 409 and json.loads(endpoint.content)['code'] == 'unsupported_capability'


@pytest.mark.django_db
def test_public_config_endpoint_keeps_frozen_mode_after_policy_change():
    world = admission_world(study_mode='id', frozen='password')
    world['study'].mode = 'anonymous'
    world['study'].save(update_fields=['mode'])
    owner = world['owner']
    for action in ACTIONS:
        Grant.objects.get_or_create(user=owner, study=world['study'], action=action, defaults={'delegable': True})
    client = Client()
    client.force_login(owner)
    response = client.get(f'/releases/{world["release"].id}/config', HTTP_HOST='admin.localhost')
    assert response.status_code == 200
    config = json.loads(response.content)
    assert config['mode'] == 'password' and config['shell_capability'] == SHELL_CAPABILITY
    serialized = json.dumps(config)
    assert 'password_hash' not in serialized and 'roster' not in serialized and 'participant' not in serialized
