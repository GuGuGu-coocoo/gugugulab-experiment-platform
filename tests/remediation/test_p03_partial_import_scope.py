"""Real XLSX uploads use the matrix's bounded study authorization.

Requirements §2.2 permits an authorized study edit while keeping other study
permissions and data intact. Account lifecycle rows still compare the entire
target policy. Preview, password-confirmed commit and replay all re-check their
actual authority; failures never partially apply a workbook.
"""

import io
import re
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from openpyxl import Workbook

from core import accounts, excel, permissions
from core.models import (AccountInvitation, AccountProfile, Audit, Build, Event,
                         Export, Grant, Instance, Participant, PermissionPreview,
                         Principal, Release, Session, Study)
from core.protocol import Rejected


PASSWORD = 'Synthetic-import1!'
VIEW = 'study.view'
CONFIGURE = 'study.configure'
UPLOAD = 'build.upload'


def account(name, *, role='user', studies=None):
    user = get_user_model().objects.create_user(name, password=PASSWORD)
    Principal.objects.create(user=user)
    AccountProfile.objects.create(user=user, role=role, policy_version=2,
                                  study_overrides=studies or {},
                                  future_study_actions=[] if role == 'admin' else None)
    return user


@pytest.fixture
def world(db):
    owner = account('import_scope_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    a = Study.objects.create(title='Import scope A')
    b = Study.objects.create(title='Import scope B')
    c = Study.objects.create(title='Import restricted C')
    admin = account('import_scope_admin', role='admin',
                    studies={str(a.pk): [VIEW, CONFIGURE, 'session.view'],
                             str(b.pk): [VIEW, CONFIGURE, UPLOAD],
                             str(c.pk): []})
    target = account('import_scope_target',
                     studies={str(a.pk): [VIEW], str(b.pk): [VIEW],
                              str(c.pk): [VIEW, 'data.export_raw']})
    Grant.objects.create(user=target, study=c, action='session.recover')
    build = Build.objects.create(study=c, descriptor={'version': 'synthetic'},
                                 digest='d' * 64)
    release = Release.objects.create(study=c, build=build,
                                     config={'mode': 'anonymous'}, approved=True)
    participant = Participant.objects.create(study=c)
    session = Session.objects.create(participant=participant, release=release,
                                     operation=uuid.uuid4(), proof_hash='e' * 64,
                                     request={'source': 'restricted-C'},
                                     token_hash='f' * 64,
                                     expires_at=timezone.now() + timedelta(days=1))
    event = Event.objects.create(session=session, event_id=uuid.uuid4(),
                                 segment_id=uuid.uuid4(), sequence=1,
                                 envelope={'value': 'keep-C'})
    export = Export.objects.create(study=c, snapshot={'event_id': str(event.event_id)})
    client = Client()
    assert client.post('/login', {'username': admin.username,
                                  'password': PASSWORD}).status_code == 302
    return {'owner': owner, 'admin': admin, 'target': target, 'instance': instance,
            'A': a, 'B': b, 'C': c, 'session': session, 'event': event,
            'export': export, 'client': client}


def update_row(world, study='B', actions=(VIEW, UPLOAD)):
    target = world['target']
    revision = AccountProfile.objects.get(user=target).revision
    return (target.username, 'update', str(revision), 'user',
            str(world[study].pk), ';'.join(actions))


def upload(world, rows):
    book = Workbook()
    book.active.append(list(excel.USERS_HEADERS))
    for row in rows:
        book.active.append(list(row))
    stream = io.BytesIO()
    book.save(stream)
    file = SimpleUploadedFile('scope.xlsx', stream.getvalue(),
                              content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response = world['client'].post('/users', {'op': 'import_users_preview', 'file': file})
    assert response.status_code == 200, response.content[:300]
    match = re.search(r'data-preview-id="([0-9a-f-]{36})"', response.content.decode())
    assert match, response.content[:300]
    return response, PermissionPreview.objects.get(pk=match.group(1))


def commit(world, preview):
    return world['client'].post('/users', {'op': 'import_users_commit',
                                          'preview_id': str(preview.pk),
                                          'password': PASSWORD})


def untouched_c(world):
    target, c = world['target'], world['C']
    assert AccountProfile.objects.get(user=target).study_overrides[str(c.pk)] == [VIEW, 'data.export_raw']
    assert list(Grant.objects.filter(user=target, study=c)
                .values_list('action', 'delegable')) == [('session.recover', False)]
    assert Session.objects.get(pk=world['session'].pk).request == {'source': 'restricted-C'}
    assert Event.objects.get(pk=world['event'].pk).envelope == {'value': 'keep-C'}
    assert Export.objects.get(pk=world['export'].pk).snapshot == {
        'event_id': str(world['event'].event_id)}
    assert Build.objects.count() == Release.objects.count() == Session.objects.count() == Event.objects.count() == Export.objects.count() == 1


def test_xlsx_updates_only_authorized_study_and_replays_without_takeover(world):
    target = world['target']
    original_password = target.password
    response, preview = upload(world, [update_row(world)])
    assert preview.errors == []
    assert len(preview.staged['ops']) == 1
    assert PASSWORD not in response.content.decode()
    revision = world['instance'].governance_revision
    assert commit(world, preview).status_code == 200
    profile = AccountProfile.objects.get(user=target)
    assert profile.study_overrides[str(world['B'].pk)] == sorted([VIEW, UPLOAD])
    assert profile.study_overrides[str(world['A'].pk)] == [VIEW]
    assert profile.platform_overrides == {} and profile.future_study_actions is None
    assert profile.revision == 1
    target.refresh_from_db()
    assert target.password == original_password
    untouched_c(world)
    audit = Audit.objects.get(action='permission.import_grants')
    assert audit.actor_principal == Principal.objects.get(user=world['admin'])
    assert audit.target == f'{target.pk}:{world["B"].pk}'
    assert audit.after['actions'][UPLOAD]['granted']
    assert PASSWORD not in str(audit.before) + str(audit.after)
    world['instance'].refresh_from_db()
    assert world['instance'].governance_revision == revision + 1
    assert commit(world, preview).status_code == 200
    assert Audit.objects.filter(action='permission.import_grants').count() == 1
    assert AccountProfile.objects.get(user=target).revision == 1


def test_forged_restricted_c_row_refuses_entire_xlsx_batch(world):
    response, preview = upload(world, [update_row(world), update_row(world, 'C')])
    assert [error['code'] for error in preview.errors] == ['delegation_forbidden']
    assert len(preview.staged['ops']) == 1
    assert world['C'].title not in response.content.decode()
    assert commit(world, preview).status_code == 409
    assert AccountProfile.objects.get(user=world['target']).study_overrides[str(world['B'].pk)] == [VIEW]
    assert Audit.objects.count() == 0
    untouched_c(world)


def test_xlsx_cannot_remove_existing_study_actions_actor_cannot_control(world):
    profile = AccountProfile.objects.get(user=world['target'])
    profile.study_overrides[str(world['B'].pk)] = [VIEW, 'session.recover']
    profile.save(update_fields=['study_overrides'])
    with pytest.raises(Rejected) as error:
        permissions.preview_matrix(world['admin'], {
            'user_id': str(world['target'].pk), 'study_id': str(world['B'].pk),
            'visibility': '1', f'action:{UPLOAD}': '1'})
    assert error.value.code == 'delegation_forbidden'
    _, preview = upload(world, [update_row(world)])
    assert [error['code'] for error in preview.errors] == ['delegation_forbidden']
    assert preview.staged['ops'] == []
    assert commit(world, preview).status_code == 409
    profile.refresh_from_db()
    assert profile.study_overrides[str(world['B'].pk)] == [VIEW, 'session.recover']


def test_xlsx_local_update_still_requires_target_role_platform_authority(world):
    AccountProfile.objects.filter(user=world['admin']).update(
        platform_overrides={'accounts.manage_user': False})
    _, preview = upload(world, [update_row(world)])
    assert [error['code'] for error in preview.errors] == ['forbidden']
    assert preview.staged['ops'] == []
    assert commit(world, preview).status_code == 409
    assert Audit.objects.count() == 0


def test_pending_xlsx_commit_rechecks_live_local_scope_without_revision_bump(world):
    _, preview = upload(world, [update_row(world)])
    profile = AccountProfile.objects.get(user=world['admin'])
    profile.study_overrides[str(world['B'].pk)] = [VIEW, UPLOAD]
    profile.save(update_fields=['study_overrides'])
    # Force the last authority check independently of the revision digest.
    # This changes only this test database, never a saved acceptance instance.
    response = commit(world, preview)
    assert response.status_code == 409
    assert '请重新预览' in response.content.decode()
    assert AccountProfile.objects.get(user=world['target']).study_overrides[str(world['B'].pk)] == [VIEW]
    assert not PermissionPreview.objects.get(pk=preview.pk).consumed
    assert Audit.objects.count() == 0
    untouched_c(world)


@pytest.mark.parametrize('changed', ['governance', 'target_revision'])
def test_pending_xlsx_commit_refuses_changed_revision(world, changed):
    _, preview = upload(world, [update_row(world)])
    if changed == 'governance':
        revoke = permissions.preview_matrix(world['owner'], {
            'user_id': str(world['admin'].pk), 'study_id': str(world['B'].pk),
            'visibility': '1', f'action:{UPLOAD}': '1'})
        permissions.commit_matrix(world['owner'], PASSWORD, str(revoke.pk))
    else:
        profile = AccountProfile.objects.get(user=world['target'])
        profile.revision += 1
        profile.save(update_fields=['revision'])
    assert commit(world, preview).status_code == 409
    assert AccountProfile.objects.get(user=world['target']).study_overrides[str(world['B'].pk)] == [VIEW]
    assert not PermissionPreview.objects.get(pk=preview.pk).consumed
    assert not Audit.objects.filter(action='permission.import_grants').exists()
    untouched_c(world)


def test_consumed_xlsx_replay_refuses_revoked_local_scope(world):
    _, preview = upload(world, [update_row(world)])
    assert commit(world, preview).status_code == 200
    profile = AccountProfile.objects.get(user=world['admin'])
    profile.study_overrides[str(world['B'].pk)] = [VIEW, UPLOAD]
    profile.save(update_fields=['study_overrides'])
    assert commit(world, preview).status_code == 409
    assert Audit.objects.filter(action='permission.import_grants').count() == 1
    assert AccountProfile.objects.get(user=world['target']).revision == 1
    untouched_c(world)


@pytest.mark.parametrize('operation', ['disable', 'enable'])
def test_xlsx_lifecycle_rows_keep_whole_account_guard(world, operation):
    if operation == 'enable':
        get_user_model().objects.filter(pk=world['target'].pk).update(is_active=False)
    target = world['target']
    _, preview = upload(world, [(target.username, operation, '0', 'user', '', '')])
    assert [error['code'] for error in preview.errors] == ['higher_privilege_target']
    assert preview.staged['ops'] == []
    assert commit(world, preview).status_code == 409
    assert get_user_model().objects.get(pk=target.pk).is_active == (operation == 'disable')
    assert Audit.objects.count() == 0
    untouched_c(world)


def test_xlsx_audit_failure_rolls_back_whole_batch_and_preview(world, monkeypatch):
    _, preview = upload(world, [('import_scope_invitee', 'create', '', 'user', '', ''),
                               update_row(world)])
    assert preview.errors == []
    revision = world['instance'].governance_revision
    real_audit = accounts.audit

    def fail_permission_audit(actor, action, target, **kwargs):
        if action == 'permission.import_grants':
            raise RuntimeError('synthetic import audit failure')
        return real_audit(actor, action, target, **kwargs)

    monkeypatch.setattr(accounts, 'audit', fail_permission_audit)
    with pytest.raises(RuntimeError, match='synthetic import audit failure'):
        commit(world, preview)
    profile = AccountProfile.objects.get(user=world['target'])
    assert profile.study_overrides[str(world['B'].pk)] == [VIEW]
    assert profile.revision == 0
    assert not AccountInvitation.objects.filter(username='import_scope_invitee').exists()
    assert Audit.objects.count() == 0
    assert not PermissionPreview.objects.get(pk=preview.pk).consumed
    world['instance'].refresh_from_db()
    assert world['instance'].governance_revision == revision
    untouched_c(world)
