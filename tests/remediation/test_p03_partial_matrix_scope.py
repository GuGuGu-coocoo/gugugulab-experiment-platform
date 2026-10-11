"""A per-study permission edit cannot take over a target's other studies.

Current requirements §2.2 permits only the authorized difference and preserves
other study permissions. Reset, enable, role change, and deletion still require
whole-account takeover. These synthetic rows exercise both boundaries.
"""

import re
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from core import accounts, permissions
from core.models import (AccountProfile, Audit, Build, Event, Export, Grant,
                         Instance, Participant, PermissionPreview, Principal,
                         Release, Session, Study)
from core.protocol import Rejected


OWNER_PASSWORD = 'synthetic-partial-owner-password'
ADMIN_PASSWORD = 'synthetic-partial-admin-password'
VIEW = 'study.view'
CONFIGURE = 'study.configure'


def user(name, password, *, role='user', studies=None, platform=None, active=True):
    account = get_user_model().objects.create_user(name, password=password,
                                                    is_active=active)
    Principal.objects.create(user=account)
    AccountProfile.objects.create(user=account, role=role, policy_version=2,
                                  study_overrides=studies or {},
                                  platform_overrides=platform or {},
                                  future_study_actions=[] if role == 'admin' else None)
    return account


@pytest.fixture
def world(db):
    owner = user('partial_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    study_a = Study.objects.create(title='Partial study A')
    study_b = Study.objects.create(title='Partial study B')
    study_c = Study.objects.create(title='Restricted study C')
    admin = user('partial_admin', ADMIN_PASSWORD, role='admin',
                 studies={str(study_a.pk): [VIEW, CONFIGURE, 'session.view'],
                          str(study_b.pk): [VIEW, CONFIGURE, 'build.upload'],
                          str(study_c.pk): []},
                 platform={'accounts.create_admin': True})
    target = user('partial_target', OWNER_PASSWORD,
                  studies={str(study_a.pk): [VIEW], str(study_b.pk): [VIEW],
                           str(study_c.pk): [VIEW, 'data.export_raw']})
    disabled = user('partial_disabled', OWNER_PASSWORD, active=False,
                    studies={str(study_c.pk): [VIEW, 'data.export_raw']})
    Grant.objects.create(user=target, study=study_c, action='session.recover',
                         delegable=False)
    build = Build.objects.create(study=study_c, descriptor={'version': 'synthetic'},
                                 digest='a' * 64)
    release = Release.objects.create(study=study_c, build=build,
                                     config={'mode': 'anonymous'}, approved=True)
    participant = Participant.objects.create(study=study_c)
    session = Session.objects.create(participant=participant, release=release,
                                     operation=uuid.uuid4(), proof_hash='b' * 64,
                                     request={'source': 'restricted-C'},
                                     token_hash='c' * 64,
                                     expires_at=timezone.now() + timedelta(days=1))
    event = Event.objects.create(session=session, event_id=uuid.uuid4(),
                                 segment_id=uuid.uuid4(), sequence=1,
                                 envelope={'value': 'keep-C'})
    export = Export.objects.create(study=study_c,
                                   snapshot={'event_id': str(event.event_id)})
    return {'owner': owner, 'admin': admin, 'target': target,
            'disabled': disabled, 'instance': instance,
            'A': study_a, 'B': study_b, 'C': study_c,
            'session': session, 'event': event, 'export': export}


def selection(target, study, actions):
    payload = {'user_id': str(target.pk), 'study_id': str(study.pk),
               'visibility': '1'}
    payload.update({f'action:{action}': '1' for action in actions if action != VIEW})
    return payload


def denied(code, operation, status=403):
    with pytest.raises(Rejected) as error:
        operation()
    assert error.value.status == status
    assert error.value.code == code


def logged_in(account, password):
    client = Client()
    assert client.post('/login', {'username': account.username,
                                  'password': password}).status_code == 302
    return client


def preview_id(response):
    match = re.search(r'name="preview_id" value="([0-9a-f-]{36})"',
                      response.content.decode())
    assert match, response.content[:300]
    return match.group(1)


def test_admin_edits_a_and_b_while_c_bindings_remain_unchanged(world):
    admin, target = world['admin'], world['target']
    client = logged_in(admin, ADMIN_PASSWORD)
    body = client.get('/users?q=partial_target').content.decode()
    for key in ('A', 'B'):
        assert f'data-matrix-row="{target.pk}-{world[key].pk}"' in body
        assert f'data-matrix-entry="{target.pk}-{world[key].pk}"' in body
    assert world['C'].title not in body
    assert f'data-matrix-row="{target.pk}-{world["C"].pk}"' not in body

    c_key = str(world['C'].pk)
    profile = AccountProfile.objects.get(user=target)
    c_override = list(profile.study_overrides[c_key])
    c_grants = list(Grant.objects.filter(user=target, study=world['C'])
                    .values_list('action', 'delegable'))
    session_id, event_id, export_id = (world['session'].pk, world['event'].pk,
                                       world['export'].pk)
    for key, action in (('A', 'session.view'), ('B', 'build.upload')):
        payload = {'op': 'matrix_preview',
                   **selection(target, world[key], [VIEW, action])}
        preview = client.post('/users', payload)
        assert preview.status_code == 200, preview.content[:300]
        committed = client.post('/users', {'op': 'matrix_commit',
                                           'preview_id': preview_id(preview),
                                           'password': ADMIN_PASSWORD})
        assert committed.status_code == 200, committed.content[:300]

    profile.refresh_from_db()
    assert sorted(profile.study_overrides[str(world['A'].pk)]) == sorted([VIEW, 'session.view'])
    assert sorted(profile.study_overrides[str(world['B'].pk)]) == sorted([VIEW, 'build.upload'])
    assert profile.study_overrides[c_key] == c_override
    assert list(Grant.objects.filter(user=target, study=world['C'])
                .values_list('action', 'delegable')) == c_grants
    assert Session.objects.get(pk=session_id).request == {'source': 'restricted-C'}
    assert Event.objects.get(pk=event_id).envelope == {'value': 'keep-C'}
    assert Export.objects.get(pk=export_id).snapshot == {'event_id': str(world['event'].event_id)}
    assert Session.objects.count() == Event.objects.count() == Export.objects.count() == 1
    audits = list(Audit.objects.filter(action='permission.matrix_changed')
                  .values_list('after', flat=True))
    assert len(audits) == 2
    assert all(item['policy']['studies'][c_key] == sorted(c_override) for item in audits)

    denied('delegation_forbidden', lambda: permissions.preview_matrix(
        admin, selection(target, world['C'], [VIEW, 'data.export_raw', 'session.view'])))


def test_whole_account_operations_still_reject_restricted_c(world):
    admin, target, disabled = world['admin'], world['target'], world['disabled']
    revision = world['instance'].governance_revision
    operations = (
        lambda: accounts.reset_temporary_password(admin, ADMIN_PASSWORD, revision, target.pk),
        lambda: accounts.set_account_active(admin, ADMIN_PASSWORD, revision, target.pk, False),
        lambda: accounts.set_account_active(admin, ADMIN_PASSWORD, revision, disabled.pk, True),
        lambda: accounts.set_account_role(admin, ADMIN_PASSWORD, revision, target.pk, 'admin'),
        lambda: accounts.delete_account(admin, ADMIN_PASSWORD, revision, target.pk,
                                        target.username),
    )
    for operation in operations:
        denied('higher_privilege_target', operation)
    assert get_user_model().objects.get(pk=target.pk).is_active
    assert not get_user_model().objects.get(pk=disabled.pk).is_active
    assert AccountProfile.objects.get(user=target).role == 'user'
    assert Audit.objects.count() == 0
    world['instance'].refresh_from_db()
    assert world['instance'].governance_revision == revision


def test_owner_self_and_revoked_scope_still_refuse(world):
    admin, owner, target = world['admin'], world['owner'], world['target']
    denied('owner_protected', lambda: permissions.preview_matrix(
        admin, selection(owner, world['A'], [VIEW, 'session.view'])))
    denied('self_target', lambda: permissions.preview_matrix(
        admin, selection(admin, world['B'], [VIEW, 'build.upload'])), status=409)

    staged = permissions.preview_matrix(
        admin, selection(target, world['A'], [VIEW, 'session.view']))
    revoke = permissions.preview_matrix(
        owner, selection(admin, world['A'], [VIEW, 'session.view']))
    permissions.commit_matrix(owner, OWNER_PASSWORD, str(revoke.pk))
    with pytest.raises(Rejected) as error:
        permissions.commit_matrix(admin, ADMIN_PASSWORD, str(staged.pk))
    assert (error.value.code, error.value.status) == ('preview_stale', 409)
    assert AccountProfile.objects.get(user=target).study_overrides[str(world['A'].pk)] == [VIEW]
    denied('delegation_forbidden', lambda: permissions.preview_matrix(
        admin, selection(target, world['A'], [VIEW, 'session.view'])))


def test_partial_commit_audit_failure_rolls_back_everything(world, monkeypatch):
    admin, target, study = world['admin'], world['target'], world['B']
    preview = permissions.preview_matrix(
        admin, selection(target, study, [VIEW, 'build.upload']))
    before = AccountProfile.objects.get(user=target)
    original = (dict(before.study_overrides), before.revision)
    revision = world['instance'].governance_revision
    count = Audit.objects.count()

    def fail_audit(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')

    monkeypatch.setattr(accounts, 'audit', fail_audit)
    with pytest.raises(RuntimeError, match='synthetic audit failure'):
        permissions.commit_matrix(admin, ADMIN_PASSWORD, str(preview.pk))
    after = AccountProfile.objects.get(user=target)
    world['instance'].refresh_from_db()
    assert (after.study_overrides, after.revision) == original
    assert world['instance'].governance_revision == revision
    assert not PermissionPreview.objects.get(pk=preview.pk).consumed
    assert Audit.objects.count() == count
