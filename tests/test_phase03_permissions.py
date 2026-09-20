"""T26/P0302 server evidence: preview-bound instance permission matrix.

Every assertion states an independently specified expectation about the real
database/HTTP behavior: visibility contract, delegable-authority bounds, stale
preview binding (even without a governance revision bump), expiry, replay
idempotency, actor revocation, atomic audit and legacy-route hardening.
"""
import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from core import accounts, permissions
from core.access import ACTIONS, delegable_authority, dominates
from core.models import AccountProfile, Audit, Grant, Instance, Invitation, PermissionPreview, Study
from core.protocol import Rejected

OWNER_PASSWORD = 'synthetic-test-password'
NEW_PASSWORD = 'synthetic-new-password-2026'
THIRD_PASSWORD = 'synthetic-third-password-2026'
SECRET_RE = re.compile(r'data-one-time-secret="1".*?<code>(.*?)</code>', re.S)
PREVIEW_RE = re.compile(r'(?:name="preview_id" value="|data-preview-id=")([0-9a-fA-F-]{36})')


def csrf_token(client, path='/login'):
    page = client.get(path)
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode())
    assert match, f'no CSRF token rendered at {path}'
    return match.group(1)


def login(client, username, password):
    return client.post('/login', {'username': username, 'password': password})


def owner_client():
    client = Client()
    assert login(client, 'synthetic_owner', OWNER_PASSWORD).status_code == 302
    return client


def make_admin(owner, username):
    response = owner.post('/users', {'op': 'create_temp', 'username': username, 'password': OWNER_PASSWORD, 'revision': str(accounts.instance_revision())})
    assert response.status_code == 200
    temp = SECRET_RE.search(response.content.decode()).group(1)
    owner.post('/users', {'op': 'set_role', 'username': username, 'role': 'admin', 'password': OWNER_PASSWORD, 'revision': str(accounts.instance_revision())})
    client = Client()
    assert login(client, username, temp).status_code == 302
    assert client.post('/account/password', {'current': temp, 'new': NEW_PASSWORD, 'confirm': NEW_PASSWORD}).status_code == 302
    return client


def preview_id(response):
    match = PREVIEW_RE.search(response.content.decode())
    assert match, response.content[:400]
    return match.group(1)


def matrix_data(user_id, study_id, visible, actions=(), delegable=(), op='matrix_preview'):
    data = {'op': op, 'user_id': str(user_id), 'study_id': str(study_id)}
    if visible:
        data['visibility'] = '1'
    for action in actions:
        data[f'action:{action}'] = '1'
    for action in delegable:
        data[f'delegable:{action}'] = '1'
    return data


def preview_matrix(client, user_id, study_id, visible, actions=(), delegable=()):
    return client.post('/users', matrix_data(user_id, study_id, visible, actions, delegable))


def commit_matrix(client, preview, password=OWNER_PASSWORD):
    return client.post('/users', {'op': 'matrix_commit', 'preview_id': preview, 'password': password})


def grants_of(user, study):
    return sorted(Grant.objects.filter(user=user, study=study).values_list('action', flat=True))


def test_matrix_visibility_contract_reauth_and_atomic_commit(setup):
    owner = owner_client()
    target = get_user_model().objects.create_user('synthetic_matrix_target', password=NEW_PASSWORD)

    # A contradictory submission (children without visibility) is rejected as a set.
    response = preview_matrix(owner, target.pk, setup['study'].pk, False, ['data.export_raw'])
    assert response.status_code == 409 and '取消研究可见性' in response.content.decode()
    assert grants_of(target, setup['study']) == []

    # Preview shows the redacted change and does not mutate yet.
    response = preview_matrix(owner, target.pk, setup['study'].pk, True, ['data.export_raw'])
    assert response.status_code == 200
    preview = preview_id(response)
    assert grants_of(target, setup['study']) == []
    assert PermissionPreview.objects.filter(pk=preview, consumed=False).exists()

    # Wrong own password: refused with zero mutations and no audit.
    response = commit_matrix(owner, preview, THIRD_PASSWORD)
    assert response.status_code == 403 and '重新认证失败' in response.content.decode()
    assert grants_of(target, setup['study']) == []
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 0

    # Correct password: view + explicit action, audited, revision bumped once.
    revision = accounts.instance_revision()
    response = commit_matrix(owner, preview)
    assert response.status_code == 200, response.content.decode()[:400]
    assert grants_of(target, setup['study']) == ['data.export_raw', 'study.view']
    audit = Audit.objects.get(action='permission.matrix_changed')
    # New 03B contract (P0302R): the audit stores the full boolean state of every
    # affected action (granted + delegable), so a delegability-only change is
    # traceable; it still contains no username, password or token.
    assert audit.before == {'actions': {'data.export_raw': {'granted': False, 'delegable': False},
                                        'study.view': {'granted': False, 'delegable': False}}}
    assert audit.after == {'actions': {'data.export_raw': {'granted': True, 'delegable': False},
                                       'study.view': {'granted': True, 'delegable': False}}}
    assert 'synthetic' not in str(audit.after)
    assert accounts.instance_revision() == revision + 1

    # Unchecking visibility clears children through the same preview/commit contract.
    response = preview_matrix(owner, target.pk, setup['study'].pk, False)
    assert response.status_code == 200
    assert '移除' in response.content.decode()
    response = commit_matrix(owner, preview_id(response))
    assert response.status_code == 200
    assert grants_of(target, setup['study']) == []


def test_admin_matrix_cannot_exceed_effective_and_delegable_authority(setup):
    owner = owner_client()
    admin_client = make_admin(owner, 'synthetic_matrix_admin')
    admin = get_user_model().objects.get(username='synthetic_matrix_admin')
    for action in ('study.view', 'data.export_raw'):
        Grant.objects.create(user=admin, study=setup['study'], action=action, delegable=True)
    target = get_user_model().objects.create_user('synthetic_matrix_managed', password=NEW_PASSWORD)

    # Held and delegable: allowed, and the outcome is authorized after commit.
    response = preview_matrix(admin_client, target.pk, setup['study'].pk, True, ['data.export_raw'], delegable=['data.export_raw'])
    assert response.status_code == 200
    response = commit_matrix(admin_client, preview_id(response), NEW_PASSWORD)
    assert response.status_code == 200
    assert grants_of(target, setup['study']) == ['data.export_raw', 'study.view']

    # Forged request for an action the Admin does not hold: refused as a whole.
    response = preview_matrix(admin_client, target.pk, setup['study'].pk, True, ['data.export_raw', 'build.upload'])
    assert response.status_code == 403 and '没有有效且可委派' in response.content.decode()
    assert grants_of(target, setup['study']) == ['data.export_raw', 'study.view']

    # A study where the Admin has no delegable authority is out of scope.
    other = Study.objects.create(title='Synthetic matrix study B')
    response = preview_matrix(admin_client, target.pk, other.pk, True, ['study.view'])
    assert response.status_code == 403
    assert grants_of(target, other) == []

    # Owner row and self row stay protected for every account-management actor.
    response = preview_matrix(admin_client, setup['owner'].pk, setup['study'].pk, True, ['data.export_raw'])
    assert response.status_code == 403 and 'Owner 账号不可通过账号管理修改' in response.content.decode()
    response = preview_matrix(admin_client, admin.pk, setup['study'].pk, True, ['data.export_raw'])
    assert response.status_code == 409 and '不能对自己的账号' in response.content.decode()
    setup['owner'].refresh_from_db()
    assert Grant.objects.filter(user=setup['owner'], study=setup['study'], action='data.export_raw').count() == 0

    # The Owner may grant the same actions on any study, but never the Owner row.
    response = preview_matrix(owner, target.pk, other.pk, True, ['data.export_raw'])
    assert response.status_code == 200
    assert commit_matrix(owner, preview_id(response)).status_code == 200
    assert grants_of(target, other) == ['data.export_raw', 'study.view']
    assert preview_matrix(owner, setup['owner'].pk, other.pk, True, ['data.export_raw']).status_code == 403


def test_preview_is_bound_to_actor_state_and_target_grants(setup):
    owner = owner_client()
    target = get_user_model().objects.create_user('synthetic_binding_target', password=NEW_PASSWORD)
    Grant.objects.create(user=target, study=setup['study'], action='study.view', delegable=False)

    # Another administrator cannot spend a preview that is not theirs.
    admin_client = make_admin(owner, 'synthetic_binding_admin')
    response = preview_matrix(owner, target.pk, setup['study'].pk, True, ['data.export_raw'])
    foreign = preview_id(response)
    response = commit_matrix(admin_client, foreign, NEW_PASSWORD)
    assert response.status_code == 404 and '预览不存在' in response.content.decode()
    assert grants_of(target, setup['study']) == ['study.view']

    # A legacy writer changes the target grants without bumping the revision; the
    # bound preview must still be rejected with zero mutations.
    Grant.objects.create(user=target, study=setup['study'], action='build.upload', delegable=True)
    revision = accounts.instance_revision()
    response = commit_matrix(owner, foreign)
    assert response.status_code == 409 and '相关账号、研究或权限已变化' in response.content.decode()
    assert grants_of(target, setup['study']) == ['build.upload', 'study.view']
    assert accounts.instance_revision() == revision
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 0

    # Expired preview is refused too.
    response = preview_matrix(owner, target.pk, setup['study'].pk, True, ['data.export_raw'])
    preview = preview_id(response)
    PermissionPreview.objects.filter(pk=preview).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert commit_matrix(owner, preview).status_code == 409
    assert grants_of(target, setup['study']) == ['build.upload', 'study.view']

    # A governance revision change alone also invalidates the bound preview.
    response = preview_matrix(owner, target.pk, setup['study'].pk, True, ['data.export_raw'])
    preview = preview_id(response)
    revision = accounts.instance_revision()
    Instance.objects.filter(pk=1).update(governance_revision=revision + 1)
    assert commit_matrix(owner, preview).status_code == 409
    assert accounts.instance_revision() == revision + 1
    Instance.objects.filter(pk=1).update(governance_revision=revision)

    # Replayed successful commit is idempotent but still respects current authorization.
    response = preview_matrix(owner, target.pk, setup['study'].pk, True, ['data.export_raw'])
    preview = preview_id(response)
    assert commit_matrix(owner, preview).status_code == 200
    audits = Audit.objects.filter(action='permission.matrix_changed').count()
    replay = commit_matrix(owner, preview)
    assert replay.status_code == 200
    assert Audit.objects.filter(action='permission.matrix_changed').count() == audits
    # The re-previewed set was complete for that study, so the legacy-added
    # build.upload was shown as removed and the replay changes nothing further.
    assert grants_of(target, setup['study']) == ['data.export_raw', 'study.view']

    # Once the actor loses authorization (disabled), even the replay is refused.
    admin = get_user_model().objects.get(username='synthetic_binding_admin')
    owner.post('/users', {'op': 'disable', 'username': admin.username, 'password': OWNER_PASSWORD, 'revision': str(accounts.instance_revision())})
    disabled = get_user_model().objects.get(pk=admin.pk)
    audits = Audit.objects.filter(action='permission.matrix_changed').count()
    with pytest.raises(Rejected) as rejected:
        permissions.commit_matrix(disabled, NEW_PASSWORD, preview)
    assert rejected.value.code == 'auth_required' and rejected.value.status == 403
    assert Audit.objects.filter(action='permission.matrix_changed').count() == audits
    assert grants_of(target, setup['study']) == ['data.export_raw', 'study.view']


def test_reconcile_preview_binds_exact_rows_and_owner_only(setup):
    owner = owner_client()
    legacy = get_user_model().objects.create_user('synthetic_reconcile_target', password=NEW_PASSWORD)
    Grant.objects.create(user=legacy, study=setup['study'], action='build.upload', delegable=True)

    admin_client = make_admin(owner, 'synthetic_reconcile_admin')
    response = admin_client.post('/users', {'op': 'reconcile_preview', 'choice': 'grant_view'})
    assert response.status_code == 403
    assert not Grant.objects.filter(user=legacy, study=setup['study'], action='study.view').exists()

    response = owner.post('/users', {'op': 'reconcile_preview', 'choice': 'grant_view'})
    assert response.status_code == 200
    preview = preview_id(response)
    assert '授权矛盾预览' in response.content.decode()
    assert 'synthetic_reconcile_target' in response.content.decode()

    # A legacy writer adds another conflicting grant (no revision bump): stale.
    other_study = Study.objects.create(title='Synthetic reconcile study B')
    Grant.objects.create(user=legacy, study=other_study, action='data.export_raw', delegable=True)
    revision = accounts.instance_revision()
    response = owner.post('/users', {'op': 'reconcile_commit', 'preview_id': preview, 'password': OWNER_PASSWORD})
    assert response.status_code == 409 and '未执行任何更改' in response.content.decode()
    assert not Grant.objects.filter(study=setup['study'], action='study.view').exists()
    assert accounts.instance_revision() == revision

    # Re-preview and commit resolves exactly the rows that were shown.
    response = owner.post('/users', {'op': 'reconcile_preview', 'choice': 'grant_view'})
    preview = preview_id(response)
    response = owner.post('/users', {'op': 'reconcile_commit', 'preview_id': preview, 'password': OWNER_PASSWORD})
    assert response.status_code == 200
    assert Grant.objects.filter(user=legacy, study=setup['study'], action='study.view', delegable=False).exists()
    assert Audit.objects.filter(action='access.conflict_resolved').count() == 2

    # Bad own password and wrong choice change nothing.
    third = Study.objects.create(title='Synthetic reconcile study C')
    Grant.objects.create(user=legacy, study=third, action='build.preview', delegable=True)
    response = owner.post('/users', {'op': 'reconcile_preview', 'choice': 'not_a_choice'})
    assert response.status_code == 422
    response = owner.post('/users', {'op': 'reconcile_preview', 'choice': 'remove_conflicting'})
    preview = preview_id(response)
    response = owner.post('/users', {'op': 'reconcile_commit', 'preview_id': preview, 'password': THIRD_PASSWORD})
    assert response.status_code == 403
    assert Grant.objects.filter(user=legacy, study=third, action='build.preview').exists()


def test_legacy_permission_routes_enforce_revision_visibility_and_authority(setup):
    owner = owner_client()
    study = setup['study']
    callers = get_user_model().objects.create_user('synthetic_legacy_caller', password=NEW_PASSWORD)
    for action in ('study.view', 'data.export_raw', 'build.upload', 'member.manage', 'permission.delegate'):
        Grant.objects.create(user=callers, study=study, action=action, delegable=True)
    client = Client()
    assert login(client, 'synthetic_legacy_caller', NEW_PASSWORD).status_code == 302

    # Missing or stale revision refuses the legacy invite route with zero writes.
    base = {'op': 'invite', 'username': 'legacy_invitee', 'actions': ['study.view', 'data.export_raw']}
    assert client.post(f"/studies/{study.id}", base).status_code == 409
    stale = {**base, 'revision': '9999'}
    assert client.post(f"/studies/{study.id}", stale).status_code == 409
    assert not Invitation.objects.filter(username='legacy_invitee').exists()

    # Children without visibility are refused even on the legacy route.
    revision = str(accounts.instance_revision())
    contradiction = {'op': 'invite', 'username': 'legacy_invitee', 'actions': ['data.export_raw'], 'revision': revision}
    assert client.post(f"/studies/{study.id}", contradiction).status_code == 409
    assert not Invitation.objects.filter(username='legacy_invitee').exists()

    # Actions outside the caller's delegable authority are refused.
    forged = {'op': 'invite', 'username': 'legacy_invitee', 'actions': ['study.view', 'release.approve_pilot'], 'revision': revision}
    assert client.post(f"/studies/{study.id}", forged).status_code == 403
    assert not Invitation.objects.filter(username='legacy_invitee').exists()

    # A valid legacy invite is created and bumps the governance revision.
    valid = {'op': 'invite', 'username': 'legacy_invitee', 'actions': ['study.view', 'data.export_raw'], 'revision': revision}
    response = client.post(f"/studies/{study.id}", valid)
    assert response.status_code == 200 and '邀请密钥' in response.content.decode()
    assert Invitation.objects.filter(username='legacy_invitee', revoked=False).exists()
    assert accounts.instance_revision() == int(revision) + 1

    # revoke_member refuses a target whose grants the caller cannot delegate.
    privileged = get_user_model().objects.create_user('synthetic_legacy_privileged', password=NEW_PASSWORD)
    Grant.objects.create(user=privileged, study=study, action='release.approve_pilot', delegable=False)
    revision = str(accounts.instance_revision())
    response = client.post(f"/studies/{study.id}", {'op': 'revoke_member', 'user_id': str(privileged.pk), 'revision': revision})
    assert response.status_code == 403
    assert Grant.objects.filter(user=privileged, study=study).count() == 1

    # The Owner row can never be revoked through the legacy route.
    response = owner.post(f"/studies/{study.id}", {'op': 'revoke_member', 'user_id': str(setup['owner'].pk), 'revision': revision})
    assert response.status_code == 403
    assert Instance.objects.get(pk=1).owner_id == setup['owner'].pk


def test_audit_failure_rolls_back_matrix_commit(setup):
    owner = owner_client()
    target = get_user_model().objects.create_user('synthetic_rollback_target', password=NEW_PASSWORD)
    Grant.objects.create(user=target, study=setup['study'], action='study.view', delegable=False)
    response = preview_matrix(owner, target.pk, setup['study'].pk, True, ['data.export_raw'])
    preview = preview_id(response)
    revision = accounts.instance_revision()

    original = accounts.audit
    accounts.audit = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('synthetic audit failure'))
    try:
        owner.raise_request_exception = False
        response = commit_matrix(owner, preview)
        assert response.status_code == 500
    finally:
        accounts.audit = original
    assert grants_of(target, setup['study']) == ['study.view']
    assert accounts.instance_revision() == revision
    PermissionPreview.objects.get(pk=preview).refresh_from_db()
    assert PermissionPreview.objects.get(pk=preview).consumed is False


def test_new_visibility_actions_are_explicit_and_never_backfilled(setup):
    assert {'identity_mapping.read', 'session.view'} <= ACTIONS
    user = get_user_model().objects.create_user('synthetic_new_actions', password=NEW_PASSWORD)
    Grant.objects.create(user=user, study=setup['study'], action='study.view', delegable=False)
    assert not Grant.objects.filter(user=user, action__in=('identity_mapping.read', 'session.view')).exists()

    owner = owner_client()
    # The matrix must assign them explicitly; they are not silently inferred.
    response = preview_matrix(owner, user.pk, setup['study'].pk, True, ['session.view'])
    assert response.status_code == 200
    assert commit_matrix(owner, preview_id(response)).status_code == 200
    assert Grant.objects.filter(user=user, study=setup['study'], action='session.view').exists()
    assert not Grant.objects.filter(user=user, action='identity_mapping.read').exists()
    # No migration backfills them for pre-existing grants.
    assert Grant.objects.filter(action='identity_mapping.read').count() == 0
    assert Grant.objects.filter(action='session.view').count() == 1
    # And a delegable mark is explicit too.
    assert Grant.objects.get(user=user, study=setup['study'], action='session.view').delegable is False
    response = preview_matrix(owner, user.pk, setup['study'].pk, True, ['session.view'], delegable=['session.view'])
    assert commit_matrix(owner, preview_id(response)).status_code == 200
    assert Grant.objects.get(user=user, study=setup['study'], action='session.view').delegable is True


def test_consumed_preview_replay_rechecks_live_authority(setup):
    """A consumed preview is idempotent only while its actor still holds the
    exact current authority; revoked delegation, an elevated target, a forced
    password change and a role change all refuse the replay with no new write."""
    owner = owner_client()
    admin_client = make_admin(owner, 'synthetic_replay_admin')
    admin = get_user_model().objects.get(username='synthetic_replay_admin')
    for action in ('study.view', 'data.export_raw'):
        Grant.objects.create(user=admin, study=setup['study'], action=action, delegable=True)
    target = get_user_model().objects.create_user('synthetic_replay_target', password=NEW_PASSWORD)

    response = preview_matrix(admin_client, target.pk, setup['study'].pk, True, ['data.export_raw'], delegable=['data.export_raw'])
    assert response.status_code == 200
    preview = preview_id(response)
    assert commit_matrix(admin_client, preview, NEW_PASSWORD).status_code == 200
    audits = Audit.objects.filter(action='permission.matrix_changed').count()
    assert audits == 1

    # Legitimate replay: same result, no second mutation and no second audit.
    response = commit_matrix(admin_client, preview, NEW_PASSWORD)
    assert response.status_code == 200 and '权限矩阵已更新' in response.content.decode()
    assert Audit.objects.filter(action='permission.matrix_changed').count() == audits

    # The actor keeps the Admin role but loses the delegable authority: the old
    # result must not be readable and nothing may change.
    Grant.objects.filter(user=admin, study=setup['study']).update(delegable=False)
    response = commit_matrix(admin_client, preview, NEW_PASSWORD)
    assert response.status_code == 403 and '权限矩阵已更新' not in response.content.decode()
    assert Audit.objects.filter(action='permission.matrix_changed').count() == audits
    assert grants_of(target, setup['study']) == ['data.export_raw', 'study.view']

    # Restore authority, then raise the target beyond what the actor can delegate.
    Grant.objects.filter(user=admin, study=setup['study']).update(delegable=True)
    other_study = Study.objects.create(title='Synthetic replay study B')
    Grant.objects.create(user=target, study=other_study, action='session.recover', delegable=False)
    response = commit_matrix(admin_client, preview, NEW_PASSWORD)
    assert response.status_code == 403 and '权限矩阵已更新' not in response.content.decode()
    Grant.objects.filter(user=target, study=other_study).delete()

    # Forced password change refuses the replay even with the current password.
    response = owner.post('/users', {'op': 'reset_password', 'username': admin.username, 'password': OWNER_PASSWORD,
                                     'revision': str(accounts.instance_revision())})
    assert response.status_code == 200
    temp = SECRET_RE.search(response.content.decode()).group(1)
    forced = get_user_model().objects.get(pk=admin.pk)
    with pytest.raises(Rejected) as rejected:
        permissions.commit_matrix(forced, temp, preview)
    assert rejected.value.code == 'password_change_required' and rejected.value.status == 403
    assert Audit.objects.filter(action='permission.matrix_changed').count() == audits

    # Demotion refuses the replay as forbidden (obsolete Owner/Admin authority).
    owner.post('/users', {'op': 'set_role', 'username': admin.username, 'role': 'user', 'password': OWNER_PASSWORD,
                          'revision': str(accounts.instance_revision())})
    demoted = get_user_model().objects.get(pk=admin.pk)
    with pytest.raises(Rejected) as rejected:
        permissions.commit_matrix(demoted, NEW_PASSWORD, preview)
    assert rejected.value.code == 'forbidden' and rejected.value.status == 403
    assert Audit.objects.filter(action='permission.matrix_changed').count() == audits
    assert grants_of(target, setup['study']) == ['data.export_raw', 'study.view']


def test_matrix_audit_records_delegable_only_change(setup):
    """Flipping only the delegable flag keeps the same action list but must still
    be fully traceable in the structured audit, without secrets."""
    owner = owner_client()
    target = get_user_model().objects.create_user('synthetic_delegable_target', password=NEW_PASSWORD)
    Grant.objects.create(user=target, study=setup['study'], action='study.view', delegable=False)
    Grant.objects.create(user=target, study=setup['study'], action='data.export_raw', delegable=False)

    response = preview_matrix(owner, target.pk, setup['study'].pk, True, ['data.export_raw'], delegable=['data.export_raw'])
    assert response.status_code == 200
    assert 'data.export_raw' in response.content.decode()
    assert commit_matrix(owner, preview_id(response)).status_code == 200
    assert Grant.objects.get(user=target, study=setup['study'], action='data.export_raw').delegable is True

    audit = Audit.objects.get(action='permission.matrix_changed')
    assert audit.before['actions'] == {'data.export_raw': {'granted': True, 'delegable': False},
                                       'study.view': {'granted': True, 'delegable': False}}
    assert audit.after['actions'] == {'data.export_raw': {'granted': True, 'delegable': True},
                                      'study.view': {'granted': True, 'delegable': False}}
    assert audit.before != audit.after
    assert NEW_PASSWORD not in str(audit.before) + str(audit.after)
    assert 'synthetic_delegable_target' not in str(audit.before) + str(audit.after)


def test_matrix_csrf_is_required_for_preview_and_commit(setup):
    target = get_user_model().objects.create_user('synthetic_csrf_target', password=NEW_PASSWORD)
    client = Client(enforce_csrf_checks=True)
    token = csrf_token(client)
    assert client.post('/login', {'username': 'synthetic_owner', 'password': OWNER_PASSWORD, 'csrfmiddlewaretoken': token}).status_code == 302
    response = client.post('/users', matrix_data(target.pk, setup['study'].pk, True, ['data.export_raw']))
    assert response.status_code == 403
    assert not PermissionPreview.objects.filter(actor__username='synthetic_owner').exists()
    token = csrf_token(client, '/users')
    response = client.post('/users', {**matrix_data(target.pk, setup['study'].pk, True, ['data.export_raw']), 'csrfmiddlewaretoken': token})
    assert response.status_code == 200
    preview = preview_id(response)
    assert client.post('/users', {'op': 'matrix_commit', 'preview_id': preview, 'password': OWNER_PASSWORD}).status_code == 403
    assert grants_of(target, setup['study']) == []
    token = csrf_token(client, '/users')
    response = client.post('/users', {'op': 'matrix_commit', 'preview_id': preview, 'password': OWNER_PASSWORD, 'csrfmiddlewaretoken': token})
    assert response.status_code == 200
    assert grants_of(target, setup['study']) == ['data.export_raw', 'study.view']


def test_concurrent_confirmations_apply_exactly_once(tmp_path):
    """Threaded double-confirmation runs in an isolated subprocess whose test
    database is a file (tests/phase03_concurrency_probe.py); the in-memory
    shared-cache database used by the rest of the suite fails fast on concurrent
    table locks instead of honoring the busy timeout."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', 'tests/phase03_concurrency_probe.py'],
        cwd=root, env={**os.environ, 'GEP_TEST_DB_FILE': str(tmp_path / 'concurrency.sqlite3')},
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed' in result.stdout
