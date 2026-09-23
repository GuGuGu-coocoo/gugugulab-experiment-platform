"""T26 server-side evidence: instance roles, account lifecycle, reauth, revision, audit.

Synthetic accounts only; every assertion is an independently stated expectation
about the real database/HTTP behavior, never a copy of implementation output.
"""
import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from core import accounts
from core.access import allowed, conflicts, dominates
from core.models import AccountInvitation, AccountProfile, Audit, Grant, Instance, Study
from core.protocol import Rejected
from core.services import digest

OWNER_PASSWORD = 'synthetic-test-password'  # from tests/conftest.py setup fixture; a pre-existing hash that is never re-set, so it keeps logging in unchanged
# Documented fixture change for U07 (2026-09-23): these two values are *set*
# through /account/password and the activation page, so they must satisfy the
# unified four-class rule; every negative authorization assertion is unchanged.
NEW_PASSWORD = 'Synthetic-new-password-2026'
THIRD_PASSWORD = 'Synthetic-third-password-2026'
SECRET_RE = re.compile(r'data-one-time-secret="1".*?<code>(.*?)</code>', re.S)
INVITE_RE = re.compile(r'data-one-time-invitation="1".*?data-invitation-link="[^"]*/activate-account\?token=([^"&]+)"', re.S)
ACTIVATION_FAILED = '激活失败：邀请无效、已使用、已撤销或已过期。'
PREVIEW_RE = re.compile(r'data-preview-id="([0-9a-fA-F-]{36})"')


def csrf_token(client, path='/login'):
    page = client.get(path)
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode())
    assert match, f'no CSRF token rendered at {path}'
    return match.group(1)


def login(client, username, password, token=None):
    data = {'username': username, 'password': password}
    if token:
        data['csrfmiddlewaretoken'] = token
    return client.post('/login', data)


def owner_client(enforce_csrf=False):
    client = Client(enforce_csrf_checks=True) if enforce_csrf else Client()
    token = csrf_token(client) if enforce_csrf else None
    assert login(client, 'synthetic_owner', OWNER_PASSWORD, token).status_code == 302
    return client, token


def governance(owner, op, expected=200, token=None, **fields):
    data = {'op': op, 'password': OWNER_PASSWORD, 'revision': str(accounts.instance_revision()), **fields}
    if token:
        data['csrfmiddlewaretoken'] = token
    response = owner.post('/users', data)
    assert response.status_code == expected, response.content[:400]
    return response


def current(password):
    return {'password': password, 'revision': str(accounts.instance_revision())}


def create_temp_account(owner, username, token=None):
    response = governance(owner, 'create_temp', username=username, token=token)
    match = SECRET_RE.search(response.content.decode())
    assert match, 'temporary password must be shown once in a no-store response'
    return match.group(1)


def make_admin(owner, username):
    temp = create_temp_account(owner, username)
    governance(owner, 'set_role', username=username, role='admin')
    client = Client()
    assert login(client, username, temp).status_code == 302
    assert client.post('/account/password', {'current': temp, 'new': NEW_PASSWORD, 'confirm': NEW_PASSWORD}).status_code == 302
    return client


def test_owner_governance_and_missing_profile_is_ordinary(setup):
    owner, _ = owner_client()
    page = owner.get('/users')
    assert page.status_code == 200
    body = page.content.decode()
    assert 'data-owner-row="1"' in body and 'Owner 账号只读' in body
    # New 03B GUI contract: capability labels now belong to the study permission
    # matrix (visibility + explicit actions) on /users, not to instance roles.
    assert '实例权限矩阵' in body and '研究可见' in body

    # A user without a profile is ordinary: no instance governance, no study access.
    user = get_user_model().objects.create_user('synthetic_no_profile', password=NEW_PASSWORD)
    ordinary = Client()
    assert login(ordinary, 'synthetic_no_profile', NEW_PASSWORD).status_code == 302
    assert ordinary.get('/users').status_code == 403
    assert ordinary.get(f"/studies/{setup['study'].id}").status_code == 403
    assert accounts.effective_role(user) == 'user'

    # Owner alone appoints Admin; appointment creates no study grants and never moves ownership.
    governance(owner, 'set_role', username='synthetic_no_profile', role='admin')
    profile = AccountProfile.objects.get(user=user)
    assert profile.role == 'admin' and profile.must_change_password is False
    assert not Grant.objects.filter(user=user).exists()
    assert Instance.objects.get(pk=1).owner_id == setup['owner'].pk

    # Admin role still does not imply RAW or any study permission.
    admin = Client()
    assert login(admin, 'synthetic_no_profile', NEW_PASSWORD).status_code == 302
    assert admin.get('/users').status_code == 200
    assert admin.post('/v1/admin/exports', {'study_id': str(setup['study'].id)}, content_type='application/json').status_code == 403
    assert admin.get(f"/studies/{setup['study'].id}").status_code == 403


def test_admin_cannot_manage_owner_or_change_roles(setup):
    owner, _ = owner_client()
    get_user_model().objects.create_user('synthetic_plain', password=NEW_PASSWORD)
    admin = make_admin(owner, 'synthetic_admin_a')

    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'set_role', 'username': 'synthetic_plain', 'role': 'admin'}).status_code == 403
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'reset_password', 'username': setup['owner'].username}).status_code == 403
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'disable', 'username': setup['owner'].username}).status_code == 403
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'enable', 'username': setup['owner'].username}).status_code == 403
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'invite_account', 'username': 'promoted_by_admin', 'role': 'admin'}).status_code == 403
    assert not AccountInvitation.objects.filter(username='promoted_by_admin').exists()
    assert not AccountProfile.objects.filter(user__username='synthetic_plain', role='admin').exists()
    assert get_user_model().objects.get(username=setup['owner'].username).is_active

    # Corrected 03B contract: an Admin may manage a peer Admin's lifecycle when
    # the study takeover guard holds, but appointment/demotion stays Owner-only.
    make_admin(owner, 'synthetic_admin_b')
    response = admin.post('/users', {**current(NEW_PASSWORD), 'op': 'reset_password', 'username': 'synthetic_admin_b'})
    assert response.status_code == 200
    assert SECRET_RE.search(response.content.decode()), 'peer Admin reset must still show one-time secret'
    peer_profile = AccountProfile.objects.get(user__username='synthetic_admin_b')
    assert peer_profile.role == 'admin' and peer_profile.must_change_password is True
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'set_role', 'username': 'synthetic_admin_b', 'role': 'user'}).status_code == 403
    assert AccountProfile.objects.get(user__username='synthetic_admin_b').role == 'admin'

    # Admin cannot undo the Owner's Admin appointment either.
    governance(owner, 'invite_account', username='synthetic_owner_invite', role='admin')
    invitation = AccountInvitation.objects.get(username='synthetic_owner_invite')
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'revoke_invitation', 'invitation_id': str(invitation.id)}).status_code == 403
    invitation.refresh_from_db()
    assert invitation.revoked is False
    governance(owner, 'revoke_invitation', invitation_id=str(invitation.id))
    invitation.refresh_from_db()
    assert invitation.revoked is True


def test_higher_grant_credential_takeover_refused(setup):
    owner, _ = owner_client()
    admin_client = make_admin(owner, 'synthetic_admin_c')
    admin = get_user_model().objects.get(username='synthetic_admin_c')
    target = get_user_model().objects.create_user('synthetic_grant_holder', password=NEW_PASSWORD)
    for action in ('study.view', 'data.export_raw'):
        Grant.objects.create(user=target, study=setup['study'], action=action, delegable=True)

    assert admin_client.post('/users', {**current(NEW_PASSWORD), 'op': 'reset_password', 'username': target.username}).status_code == 403
    assert admin_client.post('/users', {**current(NEW_PASSWORD), 'op': 'disable', 'username': target.username}).status_code == 403
    target.refresh_from_db()
    assert target.check_password(NEW_PASSWORD) and target.is_active
    assert AccountProfile.objects.filter(user=target).count() == 0

    # With dominating delegable permissions the same reset is allowed and audited.
    for action in ('study.view', 'data.export_raw'):
        Grant.objects.create(user=admin, study=setup['study'], action=action, delegable=True)
    response = admin_client.post('/users', {**current(NEW_PASSWORD), 'op': 'reset_password', 'username': target.username})
    assert response.status_code == 200, response.content[:400]
    temp = SECRET_RE.search(response.content.decode()).group(1)
    profile = AccountProfile.objects.get(user=target)
    assert profile.must_change_password is True and profile.auth_version == 2
    target.refresh_from_db()
    assert target.check_password(NEW_PASSWORD) is False and target.check_password(temp) is True
    audit = Audit.objects.get(action='account.temporary_password_reset')
    assert temp not in str(audit.before) + str(audit.after)


def test_takeover_guard_is_study_scoped_and_includes_nondelegable(setup):
    owner, _ = owner_client()
    study_a = setup['study']
    study_b = Study.objects.create(title='Synthetic guard study B')
    admin_client = make_admin(owner, 'synthetic_guard_admin')
    admin = get_user_model().objects.get(username='synthetic_guard_admin')
    target = get_user_model().objects.create_user('synthetic_guard_target', password=NEW_PASSWORD)
    Grant.objects.create(user=target, study=study_b, action='data.export_raw', delegable=True)

    # The same action string on another study is not authority over the target.
    Grant.objects.create(user=admin, study=study_a, action='study.view', delegable=True)
    Grant.objects.create(user=admin, study=study_a, action='data.export_raw', delegable=True)
    assert not dominates(admin, target)
    assert admin_client.post('/users', {**current(NEW_PASSWORD), 'op': 'disable', 'username': target.username}).status_code == 403
    target.refresh_from_db()
    assert target.is_active

    # Adequate same-study authority (view + exact action, both delegable) allows it.
    Grant.objects.create(user=admin, study=study_b, action='study.view', delegable=True)
    Grant.objects.create(user=admin, study=study_b, action='data.export_raw', delegable=True)
    assert dominates(admin, target)
    assert admin_client.post('/users', {**current(NEW_PASSWORD), 'op': 'disable', 'username': target.username}).status_code == 200
    target.refresh_from_db()
    assert target.is_active is False

    # Reactivation restores access, so the guard also applies to enable.
    Grant.objects.filter(user=admin, study=study_b, action='data.export_raw').delete()
    assert not dominates(admin, target)
    assert admin_client.post('/users', {**current(NEW_PASSWORD), 'op': 'enable', 'username': target.username}).status_code == 403
    target.refresh_from_db()
    assert target.is_active is False
    Grant.objects.create(user=admin, study=study_b, action='data.export_raw', delegable=True)
    assert admin_client.post('/users', {**current(NEW_PASSWORD), 'op': 'enable', 'username': target.username}).status_code == 200
    target.refresh_from_db()
    assert target.is_active is True

    # A target nondelegable privilege still blocks takeover until the actor can
    # delegate that exact study-scoped action.
    guarded = get_user_model().objects.create_user('synthetic_nondelegable_target', password=NEW_PASSWORD)
    Grant.objects.create(user=guarded, study=study_a, action='data.export_raw', delegable=False)
    Grant.objects.filter(user=admin, study=study_a, action='data.export_raw').delete()
    assert not dominates(admin, guarded)
    assert admin_client.post('/users', {**current(NEW_PASSWORD), 'op': 'reset_password', 'username': guarded.username}).status_code == 403
    guarded.refresh_from_db()
    assert guarded.check_password(NEW_PASSWORD)
    assert AccountProfile.objects.filter(user=guarded).count() == 0
    Grant.objects.create(user=admin, study=study_a, action='data.export_raw', delegable=True)
    assert dominates(admin, guarded)
    assert admin_client.post('/users', {**current(NEW_PASSWORD), 'op': 'reset_password', 'username': guarded.username}).status_code == 200
    assert AccountProfile.objects.get(user=guarded).must_change_password is True

    # Actor-side visibility prerequisite: a delegable action without study.view
    # on the same study is not authorized authority.
    blind = get_user_model().objects.create_user('synthetic_blind_admin', password=NEW_PASSWORD)
    AccountProfile.objects.create(user=blind, role='admin', must_change_password=False, auth_version=1, revision=0)
    same_study = get_user_model().objects.create_user('synthetic_blind_target', password=NEW_PASSWORD)
    Grant.objects.create(user=blind, study=study_a, action='data.export_raw', delegable=True)
    Grant.objects.create(user=same_study, study=study_a, action='data.export_raw', delegable=True)
    assert not dominates(blind, same_study)
    blind_client = Client()
    assert login(blind_client, 'synthetic_blind_admin', NEW_PASSWORD).status_code == 302
    assert blind_client.post('/users', {**current(NEW_PASSWORD), 'op': 'disable', 'username': same_study.username}).status_code == 403
    same_study.refresh_from_db()
    assert same_study.is_active

    # Owner bypass: the instance Owner dominates regardless of grants.
    privileged = get_user_model().objects.create_user('synthetic_owner_bypass_target', password=NEW_PASSWORD)
    Grant.objects.create(user=privileged, study=study_a, action='data.export_raw', delegable=True)
    assert dominates(setup['owner'], privileged)
    assert owner.post('/users', {**current(OWNER_PASSWORD), 'op': 'disable', 'username': privileged.username}).status_code == 200
    privileged.refresh_from_db()
    assert privileged.is_active is False


def test_stale_actor_credentials_status_and_role_cannot_mutate(setup):
    owner, _ = owner_client()
    target = get_user_model().objects.create_user('synthetic_stale_target', password=NEW_PASSWORD)

    def deny(actor, password, target_id, expected_code):
        revision = Instance.objects.get(pk=1).governance_revision
        audits = Audit.objects.count()
        with pytest.raises(Rejected) as rejected:
            accounts.set_account_active(actor, password, revision, target_id, False)
        assert rejected.value.code == expected_code and rejected.value.status == 403
        target.refresh_from_db()
        assert target.is_active and target.check_password(NEW_PASSWORD)
        assert AccountProfile.objects.filter(user=target).count() == 0
        assert Audit.objects.count() == audits
        assert Instance.objects.get(pk=1).governance_revision == revision

    # (a) password replacement: the stale object still carries the replaced hash.
    make_admin(owner, 'synthetic_stale_a')
    stale = get_user_model().objects.get(username='synthetic_stale_a')
    governance(owner, 'reset_password', username='synthetic_stale_a')
    assert stale.check_password(NEW_PASSWORD)
    deny(stale, NEW_PASSWORD, target.pk, 'reauth_failed')

    # (b) disable: the stale object still claims to be active.
    make_admin(owner, 'synthetic_stale_b')
    stale_b = get_user_model().objects.get(username='synthetic_stale_b')
    governance(owner, 'disable', username='synthetic_stale_b')
    assert stale_b.is_active is True
    deny(stale_b, NEW_PASSWORD, target.pk, 'auth_required')

    # (c) demotion: the stale object still has the Admin profile cached.
    make_admin(owner, 'synthetic_stale_c')
    stale_c = get_user_model().objects.get(username='synthetic_stale_c')
    governance(owner, 'set_role', username='synthetic_stale_c', role='user')
    assert AccountProfile.objects.get(user=stale_c).role == 'user'
    deny(stale_c, NEW_PASSWORD, target.pk, 'forbidden')

    # (d) forced-change reset: even the current temporary password cannot mutate.
    temp = create_temp_account(owner, 'synthetic_stale_d')
    forced = get_user_model().objects.get(username='synthetic_stale_d')
    deny(forced, temp, target.pk, 'password_change_required')

    # Self-service change also reads fresh activity, and cannot reuse the
    # current temporary password to clear the forced-change gate.
    version = AccountProfile.objects.get(user=stale_b).auth_version
    with pytest.raises(Rejected) as rejected:
        accounts.change_own_password(stale_b, NEW_PASSWORD, THIRD_PASSWORD, THIRD_PASSWORD)
    assert rejected.value.code == 'auth_required'
    assert AccountProfile.objects.get(user=stale_b).auth_version == version

    reuse_temp = create_temp_account(owner, 'synthetic_reuse_user')
    reuse = get_user_model().objects.get(username='synthetic_reuse_user')
    reuse_version = AccountProfile.objects.get(user=reuse).auth_version
    with pytest.raises(Rejected) as rejected:
        accounts.change_own_password(reuse, reuse_temp, reuse_temp, reuse_temp)
    assert rejected.value.code == 'password_reused' and rejected.value.status == 409
    reuse.refresh_from_db()
    assert reuse.check_password(reuse_temp)
    reuse_profile = AccountProfile.objects.get(user=reuse)
    assert reuse_profile.must_change_password is True and reuse_profile.auth_version == reuse_version


def test_activation_rechecks_live_issuer_authority(setup):
    owner, _ = owner_client()
    client = Client()

    # Owner invitation succeeds; the token stays single use.
    response = governance(owner, 'invite_account', username='synthetic_live_invitee')
    owner_token = INVITE_RE.search(response.content.decode()).group(1)
    assert client.post('/activate-account', {'token': owner_token, 'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD}).status_code == 200
    assert get_user_model().objects.filter(username='synthetic_live_invitee').exists()
    response = client.post('/activate-account', {'token': owner_token, 'password': THIRD_PASSWORD, 'confirm': THIRD_PASSWORD})
    assert response.status_code == 403 and ACTIVATION_FAILED in response.content.decode()

    def deny_activation(token, username):
        invitation = AccountInvitation.objects.get(username=username)
        revision = Instance.objects.get(pk=1).governance_revision
        audits = Audit.objects.count()
        response = client.post('/activate-account', {'token': token, 'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
        assert response.status_code == 403 and ACTIVATION_FAILED in response.content.decode()
        invitation.refresh_from_db()
        assert invitation.consumed is False
        assert not get_user_model().objects.filter(username=username).exists()
        assert Audit.objects.count() == audits
        assert Instance.objects.get(pk=1).governance_revision == revision

    # A disabled issuer's pending invitation cannot create an account.
    disabled_admin = make_admin(owner, 'synthetic_issuer_disabled')
    response = disabled_admin.post('/users', {**current(NEW_PASSWORD), 'op': 'invite_account', 'username': 'synthetic_disabled_issuer_invitee'})
    assert response.status_code == 200
    disabled_token = INVITE_RE.search(response.content.decode()).group(1)
    governance(owner, 'disable', username='synthetic_issuer_disabled')
    deny_activation(disabled_token, 'synthetic_disabled_issuer_invitee')

    # A demoted issuer loses the authority to issue even ordinary invitations.
    demoted_admin = make_admin(owner, 'synthetic_issuer_demoted')
    response = demoted_admin.post('/users', {**current(NEW_PASSWORD), 'op': 'invite_account', 'username': 'synthetic_demoted_issuer_invitee'})
    assert response.status_code == 200
    demoted_token = INVITE_RE.search(response.content.decode()).group(1)
    governance(owner, 'set_role', username='synthetic_issuer_demoted', role='user')
    deny_activation(demoted_token, 'synthetic_demoted_issuer_invitee')

    # An Admin invitation requires the issuer to still be the instance Owner.
    response = governance(owner, 'invite_account', username='synthetic_lost_owner_invitee', role='admin')
    lost_token = INVITE_RE.search(response.content.decode()).group(1)
    usurper = get_user_model().objects.create_user('synthetic_other_owner_pointer', password=NEW_PASSWORD)
    Instance.objects.filter(pk=1).update(owner_id=usurper.pk)
    deny_activation(lost_token, 'synthetic_lost_owner_invitee')


def test_admin_manages_peer_admin_under_guard_but_never_roles_or_owner(setup):
    owner, _ = owner_client()
    admin = make_admin(owner, 'synthetic_peer_admin_a')
    make_admin(owner, 'synthetic_peer_admin_b')
    peer = get_user_model().objects.get(username='synthetic_peer_admin_b')
    assert AccountProfile.objects.get(user=peer).role == 'admin'

    # Peer Admin lifecycle operations are allowed while the takeover guard holds.
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'disable', 'username': peer.username}).status_code == 200
    peer.refresh_from_db()
    assert peer.is_active is False
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'enable', 'username': peer.username}).status_code == 200
    peer.refresh_from_db()
    assert peer.is_active is True
    response = admin.post('/users', {**current(NEW_PASSWORD), 'op': 'reset_password', 'username': peer.username})
    assert response.status_code == 200
    peer_temp = SECRET_RE.search(response.content.decode()).group(1)
    peer.refresh_from_db()
    assert peer.check_password(peer_temp) and not peer.check_password(NEW_PASSWORD)
    assert AccountProfile.objects.get(user=peer).must_change_password is True

    # Role changes stay Owner-only: no peer demotion, no self/other promotion.
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'set_role', 'username': peer.username, 'role': 'user'}).status_code == 403
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'set_role', 'username': peer.username, 'role': 'admin'}).status_code == 403
    assert AccountProfile.objects.get(user=peer).role == 'admin'

    # The Owner row stays read-only for Admin actors.
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'reset_password', 'username': setup['owner'].username}).status_code == 403
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'disable', 'username': setup['owner'].username}).status_code == 403
    assert admin.post('/users', {**current(NEW_PASSWORD), 'op': 'enable', 'username': setup['owner'].username}).status_code == 403
    setup['owner'].refresh_from_db()
    assert setup['owner'].is_active


def test_temporary_password_forced_change_gate_on_every_endpoint(setup):
    owner, _ = owner_client()
    temp = create_temp_account(owner, 'synthetic_temp_user')
    client = Client()
    assert login(client, 'synthetic_temp_user', temp).status_code == 302

    response = client.get('/')
    assert response.status_code == 302 and response['Location'] == '/account/password'
    response = client.get(f"/studies/{setup['study'].id}")
    assert response.status_code == 302 and response['Location'] == '/account/password'
    response = client.post('/v1/admin/exports', {'study_id': str(setup['study'].id)}, content_type='application/json')
    assert response.status_code == 403 and response.json()['code'] == 'password_change_required'
    response = client.post('/users', {'op': 'create_temp', 'username': 'blocked', 'password': 'x', 'revision': '0'})
    assert response.status_code == 302 and response['Location'] == '/account/password'

    response = client.post('/account/password', {'current': 'wrong-temporary', 'new': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert response.status_code == 403 and '当前密码不正确' in response.content.decode()
    user = get_user_model().objects.get(username='synthetic_temp_user')
    assert user.check_password(temp) and AccountProfile.objects.get(user=user).must_change_password

    assert client.post('/account/password', {'current': temp, 'new': NEW_PASSWORD, 'confirm': NEW_PASSWORD}).status_code == 302
    assert client.get('/').status_code == 200
    profile = AccountProfile.objects.get(user=user)
    assert profile.must_change_password is False and profile.auth_version == 2
    audit = Audit.objects.get(action='account.password_changed')
    assert audit.study is None
    assert temp not in str(audit.before) + str(audit.after) and NEW_PASSWORD not in str(audit.before) + str(audit.after)


def test_password_change_and_admin_reset_invalidate_old_sessions(setup):
    owner, _ = owner_client()
    temp = create_temp_account(owner, 'synthetic_session_user')
    first = Client()
    assert login(first, 'synthetic_session_user', temp).status_code == 302
    assert first.post('/account/password', {'current': temp, 'new': NEW_PASSWORD, 'confirm': NEW_PASSWORD}).status_code == 302
    assert first.get('/').status_code == 200  # the changing session stays valid
    second = Client()
    assert login(second, 'synthetic_session_user', NEW_PASSWORD).status_code == 302
    assert second.get('/').status_code == 200

    response = governance(owner, 'reset_password', username='synthetic_session_user')
    new_temp = SECRET_RE.search(response.content.decode()).group(1)
    for old in (first, second):
        assert old.get('/').status_code == 302 and old.get('/')['Location'] == '/login'
    third = Client()
    assert login(third, 'synthetic_session_user', new_temp).status_code == 302
    assert third.get('/')['Location'] == '/account/password'
    assert third.post('/account/password', {'current': new_temp, 'new': THIRD_PASSWORD, 'confirm': THIRD_PASSWORD}).status_code == 302
    assert third.get('/').status_code == 200

    governance(owner, 'disable', username='synthetic_session_user')
    assert third.get('/').status_code == 302 and third.get('/')['Location'] == '/login'
    fresh = Client()
    assert login(fresh, 'synthetic_session_user', THIRD_PASSWORD).status_code == 200
    assert '_auth_user_id' not in fresh.session
    assert get_user_model().objects.get(username='synthetic_session_user').is_active is False
    assert third.get('/')['Location'] == '/login'


def test_reauth_revision_csrf_and_atomic_audit(setup):
    owner, token = owner_client(enforce_csrf=True)
    token = csrf_token(owner, '/users')
    create_temp_account(owner, 'synthetic_audit_target', token=token)
    target = get_user_model().objects.get(username='synthetic_audit_target')
    revision = str(accounts.instance_revision())

    # Missing CSRF is refused by the framework before any change.
    assert owner.post('/users', {'op': 'disable', 'username': target.username, 'password': OWNER_PASSWORD, 'revision': revision}).status_code == 403
    target.refresh_from_db()
    assert target.is_active

    token = csrf_token(owner, '/users')
    response = owner.post('/users', {'op': 'disable', 'username': target.username, 'password': 'wrong-owner-password', 'revision': revision, 'csrfmiddlewaretoken': token})
    assert response.status_code == 403 and '重新认证失败' in response.content.decode()
    response = owner.post('/users', {'op': 'disable', 'username': target.username, 'password': OWNER_PASSWORD, 'revision': '9999', 'csrfmiddlewaretoken': token})
    assert response.status_code == 409 and '治理版本已变化' in response.content.decode()
    target.refresh_from_db()
    assert target.is_active and AccountProfile.objects.get(user=target).auth_version == 1
    assert Audit.objects.filter(action='account.disabled').count() == 0

    # An audit failure inside the transaction must roll the governance change back.
    original = accounts.audit
    accounts.audit = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('synthetic audit failure'))
    try:
        owner.raise_request_exception = False
        response = owner.post('/users', {'op': 'disable', 'username': target.username, 'password': OWNER_PASSWORD, 'revision': revision, 'csrfmiddlewaretoken': token})
        assert response.status_code == 500
    finally:
        accounts.audit = original
    target.refresh_from_db()
    assert target.is_active and AccountProfile.objects.get(user=target).auth_version == 1
    assert Instance.objects.get(pk=1).governance_revision == int(revision)

    token = csrf_token(owner, '/users')
    response = owner.post('/users', {'op': 'disable', 'username': target.username, 'password': OWNER_PASSWORD, 'revision': revision, 'csrfmiddlewaretoken': token})
    assert response.status_code == 200
    target.refresh_from_db()
    audit = Audit.objects.get(action='account.disabled')
    assert target.is_active is False and audit.study is None
    assert audit.before['is_active'] is True and audit.after['is_active'] is False
    assert Instance.objects.get(pk=1).governance_revision == int(revision) + 1


def test_invitation_activation_replay_expiry_revocation_and_rate_limit(setup):
    owner, _ = owner_client()
    response = governance(owner, 'invite_account', username='synthetic_invitee', role='user')
    token = INVITE_RE.search(response.content.decode()).group(1)
    client = Client()
    assert client.post('/activate-account', {'token': token, 'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD}).status_code == 200
    user = get_user_model().objects.get(username='synthetic_invitee')
    profile = AccountProfile.objects.get(user=user)
    assert user.check_password(NEW_PASSWORD) and profile.role == 'user' and profile.must_change_password is False

    # Single-use: replay cannot replace the password the invitee chose.
    response = client.post('/activate-account', {'token': token, 'password': THIRD_PASSWORD, 'confirm': THIRD_PASSWORD})
    assert response.status_code == 403 and ACTIVATION_FAILED in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD) and not user.check_password(THIRD_PASSWORD)

    response = client.post('/activate-account', {'token': 'synthetic-invalid-token', 'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert response.status_code == 403 and ACTIVATION_FAILED in response.content.decode()

    response = governance(owner, 'invite_account', username='synthetic_expired')
    expired_token = INVITE_RE.search(response.content.decode()).group(1)
    invitation = AccountInvitation.objects.get(username='synthetic_expired')
    from datetime import timedelta
    from django.utils import timezone
    invitation.expires_at = timezone.now() - timedelta(seconds=1)
    invitation.save(update_fields=['expires_at'])
    response = client.post('/activate-account', {'token': expired_token, 'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert response.status_code == 403 and ACTIVATION_FAILED in response.content.decode()
    assert not get_user_model().objects.filter(username='synthetic_expired').exists()

    response = governance(owner, 'invite_account', username='synthetic_revoked')
    revoked_token = INVITE_RE.search(response.content.decode()).group(1)
    invitation = AccountInvitation.objects.get(username='synthetic_revoked')
    governance(owner, 'revoke_invitation', invitation_id=str(invitation.id))
    response = client.post('/activate-account', {'token': revoked_token, 'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert response.status_code == 403 and ACTIVATION_FAILED in response.content.decode()

    # Sixth attempt in the same window is rate limited instead of probed.
    response = client.post('/activate-account', {'token': 'synthetic-invalid-token-2', 'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert response.status_code == 429


def test_activation_never_resets_existing_account_or_enumerates(setup):
    owner, _ = owner_client()
    existing = get_user_model().objects.create_user('synthetic_existing_member', password=NEW_PASSWORD)
    # An invitation issued before the account appeared must not become a takeover path.
    invitation = AccountInvitation.objects.create(issuer=setup['owner'], username=existing.username, role='user',
                                                  token_hash=digest('synthetic-existing-invitation'), expires_at=timezone.now() + timedelta(hours=1))
    assert existing.check_password(NEW_PASSWORD)
    client = Client()
    response = client.post('/activate-account', {'token': 'synthetic-existing-invitation', 'password': THIRD_PASSWORD, 'confirm': THIRD_PASSWORD})
    assert response.status_code == 403 and ACTIVATION_FAILED in response.content.decode()
    existing.refresh_from_db()
    assert existing.check_password(NEW_PASSWORD) and not existing.check_password(THIRD_PASSWORD)
    assert 'synthetic_existing_member' not in response.content.decode()
    invitation.refresh_from_db()
    assert not invitation.consumed


def test_study_view_is_prerequisite_and_owner_reconciliation_is_previewed(setup):
    user = get_user_model().objects.create_user('synthetic_legacy_conflict', password=NEW_PASSWORD)
    Grant.objects.create(user=user, study=setup['study'], action='build.upload', delegable=True)
    assert not allowed(user, setup['study'], 'build.upload')
    assert conflicts() == [(user.pk, setup['study'].pk, ['build.upload'])]

    owner, _ = owner_client()
    page = owner.get('/users').content.decode()
    assert 'synthetic_legacy_conflict' in page and 'build.upload' in page and '授权矛盾预览' in page

    admin = make_admin(owner, 'synthetic_admin_d')
    base = {'password': NEW_PASSWORD, 'revision': str(accounts.instance_revision())}
    assert admin.post('/users', {**base, 'op': 'reconcile', 'choice': 'grant_view'}).status_code == 403
    assert not Grant.objects.filter(user=user, study=setup['study'], action='study.view').exists()

    # Old contract: one POST applied the reconciliation. New contract: the Owner
    # must preview, then confirm with the one-time preview identity.
    response = governance(owner, 'reconcile', choice='grant_view')
    preview = PREVIEW_RE.search(response.content.decode())
    assert preview, 'reconcile now renders a bound preview'
    assert not Grant.objects.filter(user=user, study=setup['study'], action='study.view').exists()
    governance(owner, 'reconcile_commit', preview_id=preview.group(1))
    grant = Grant.objects.get(user=user, study=setup['study'], action='study.view')
    assert grant.delegable is False
    assert allowed(user, setup['study'], 'build.upload')
    audit = Audit.objects.get(action='access.conflict_resolved')
    assert audit.before['choice'] == 'grant_view' and audit.study is None
    assert conflicts() == []

    other = Study.objects.create(title='Synthetic conflict B')
    Grant.objects.create(user=user, study=other, action='data.export_raw')
    response = governance(owner, 'reconcile', choice='remove_conflicting')
    preview = PREVIEW_RE.search(response.content.decode())
    assert preview, 'reconcile now renders a bound preview'
    governance(owner, 'reconcile_commit', preview_id=preview.group(1))
    assert not Grant.objects.filter(user=user, study=other).exists()
    assert Audit.objects.filter(action='access.conflict_resolved').count() == 2


def test_governance_secrets_are_one_time_and_never_audited(setup):
    owner, _ = owner_client()
    response = governance(owner, 'create_temp', username='synthetic_secret_holder')
    temp = SECRET_RE.search(response.content.decode()).group(1)
    assert response['Cache-Control'] == 'no-store'
    response = governance(owner, 'invite_account', username='synthetic_secret_invitee')
    token = INVITE_RE.search(response.content.decode()).group(1)
    assert response['Cache-Control'] == 'no-store'

    records = str(list(Audit.objects.values()))
    assert temp not in records and token not in records
    assert Audit.objects.filter(action='account.created_temporary').count() == 1
    assert Audit.objects.filter(action='account.invite_issued').count() == 1

    page = owner.get('/users').content.decode()
    assert temp not in page and token not in page
    assert AccountInvitation.objects.get(username='synthetic_secret_invitee').token_hash != token
