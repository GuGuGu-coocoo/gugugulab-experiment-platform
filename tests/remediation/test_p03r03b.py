"""P03R03B evidence: permanent account deletion with a stable audit subject.

The expected side is the R00 contract (2026-09-23, section C), the confirmed
product requirements (U05, current_requirements §3.1) and explicit synthetic
inputs, never the implementation under test:

- deletion is irreversible and only for non-Owner targets; the Owner row, self
  targeting and out-of-scope targets are refused with zero writes, and an Admin
  needs the target role's exact v2 platform switches plus a whole-account
  takeover over the target's stored policy projected to the empty policy;
- one atomic transaction removes the login account, password/authentication data
  and permissions, invalidates live login sessions, pending account/study
  invitations bound to the username, unconsumed previews (including previews by
  other actors that operate on the target) and the target's own issued account/
  study invitations and recovery permits/codes; created studies, participants,
  sessions, events and the audit history are preserved;
- the stable Principal keeps its UUID and only gains ``deleted_at``; a later
  account with the same username gets a new Principal and inherits nothing from
  the old account, and older audits render as ``已删除账号 <uuid>``;
- an audit failure rolls the whole deletion, credential invalidation and session
  removal back;
- one real Chrome journey: the red confirmation entry with the actor password
  and typed username, Owner/self/scope refusals and the resulting list.

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r03b/<UTC>-<random>/`` root.
"""
import json
import os
import re
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session as DjangoSession
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from core import access, accounts, excel, gui_accounts, services
from core.models import (AccountInvitation, AccountProfile, Audit, Build, Event,
                         Grant, Instance, Invitation, Participant,
                         PermissionPreview, Principal, RecoveryCode,
                         RecoveryPermit, Release, Session, Study)
from core.protocol import Rejected
from core.services import digest

OWNER_PASSWORD = 'synthetic-p03r03b-owner-password'
USER_PASSWORD = 'synthetic-p03r03b-user-password'
ADMIN_PASSWORD = 'synthetic-p03r03b-admin-password'
# Four-class values for every *newly set* password (the U07 rule runs before the
# token check, so the negative assertions stay about the revoked invitation).
ACTIVATION_PASSWORD = 'Synthetic-p03r03b-activation-2026!'
APPLICATION_PASSWORD = 'Synthetic-p03r03b-application-2026!'
SECRET_RE = re.compile(r'data-one-time-secret="1".*?<code>(.*?)</code>', re.S)
PRINCIPAL_RE = re.compile(r'data-deleted-principal="([0-9a-fA-F-]{36})"')
PREVIEW_RE = re.compile(r'(?:name="preview_id" value="|data-preview-id=")([0-9a-fA-F-]{36})')
INVITE_TOKEN_RE = re.compile(r'/activate-account\?token=([A-Za-z0-9_\-]+)')
KEY_RE = re.compile(r'邀请密钥（请通过可信渠道交付）：([A-Za-z0-9_\-]+)')
ACTIVATION_FAILED = '激活失败：邀请无效、已使用、已撤销或已过期。'


# --- synthetic world ---------------------------------------------------------

def make_user(username, password, *, role='user', platform=None, studies=None, future=None):
    """One account with an explicit stored v2 policy and stable Principal."""
    user = get_user_model().objects.create_user(username, password=password)
    Principal.objects.create(user=user)
    AccountProfile.objects.create(user=user, role=role, must_change_password=False,
                                  policy_version=2, platform_overrides=dict(platform or {}),
                                  study_overrides=dict(studies or {}),
                                  future_study_actions=future)
    return user


def login(client, username, password):
    return client.post('/login', {'username': username, 'password': password})


def signed_in(username, password):
    client = Client()
    assert login(client, username, password).status_code == 302
    return client


def delete_payload(username, confirm, *, password=OWNER_PASSWORD, revision=None):
    return {'op': 'delete', 'username': username, 'confirm_username': confirm,
            'password': password, 'revision': str(revision if revision is not None
                                                  else accounts.instance_revision())}


def make_session(study, proof='proof'):
    """One admitted synthetic session so target-side data can be checked."""
    build = Build.objects.create(study=study, descriptor={}, digest='b' * 64)
    release = Release.objects.create(study=study, build=build, config={'purpose': 'synthetic'},
                                     approved=True)
    participant = Participant.objects.create(study=study, code='P03R03B-001',
                                             password_hash='', active=True)
    session = Session.objects.create(participant=participant, release=release,
                                     operation=uuid.uuid4(), proof_hash=digest(proof),
                                     request={}, token_hash=digest('token'),
                                     expires_at=timezone.now() + timedelta(days=1))
    Event.objects.create(session=session, event_id=uuid.uuid4(), segment_id=uuid.uuid4(),
                         sequence=1, envelope={'synthetic': True})
    return build, release, participant, session


def users_workbook(*rows):
    """One real bounded XLSX users workbook (the same headers as the template)."""
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'users'
    sheet.append(list(excel.USERS_HEADERS))
    for row in rows:
        sheet.append(list(row))
    import io
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def upload_users(client, raw):
    upload = SimpleUploadedFile('users.xlsx', raw, content_type=(
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
    return client.post('/users', {'op': 'import_users_preview', 'file': upload})


@pytest.fixture
def world(db):
    owner = make_user('p03r03b_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    study = Study.objects.create(title='P03R03B study A', recruitment='open', max_sessions=5)
    target = make_user('p03r03b_target', USER_PASSWORD,
                       studies={str(study.pk): ['study.view', 'data.export_raw']})
    plain = make_user('p03r03b_plain', USER_PASSWORD)
    return {'owner': owner, 'instance': instance, 'study': study,
            'target': target, 'plain': plain}


# --- full invalidation and preservation --------------------------------------

def test_permanent_delete_invalidates_credentials_and_keeps_history(world, evidence):
    owner, instance, study, target = (world['owner'], world['instance'],
                                      world['study'], world['target'])
    build, release, participant, session = make_session(study)
    old_principal = Principal.objects.get(user_id=target.pk)

    # The target is signed in with a live session and has acted before.
    target_client = signed_in(target.username, USER_PASSWORD)
    assert target_client.get('/').status_code == 200
    session_key = target_client.session.session_key
    assert DjangoSession.objects.filter(session_key=session_key).exists()

    # The target issued credentials of its own, and other actors hold pending
    # invitations and previews bound to its username/still operating on it.
    AccountInvitation.objects.create(issuer=target, username='p03r03b_invitee', role='user',
                                     token_hash=digest('issued-account-token'),
                                     expires_at=timezone.now() + timedelta(hours=1))
    Invitation.objects.create(study=study, issuer=target, username='p03r03b_member',
                              actions=['study.view'], token_hash=digest('issued-study-token'),
                              expires_at=timezone.now() + timedelta(hours=1))
    stale_account = AccountInvitation.objects.create(
        issuer=owner, username=target.username, role='user',
        token_hash=digest('stale-account-token'), expires_at=timezone.now() + timedelta(hours=1))
    stale_study = Invitation.objects.create(
        study=study, issuer=owner, username=target.username, actions=['study.view'],
        token_hash=digest('stale-study-token'), expires_at=timezone.now() + timedelta(hours=1))
    own_preview = PermissionPreview.objects.create(
        actor=target, kind='matrix', scope=str(study.pk), summary={}, errors=[],
        binding='a' * 64, staged={'user_id': target.pk}, expires_at=timezone.now() + timedelta(minutes=10))
    other_preview = PermissionPreview.objects.create(
        actor=owner, kind='matrix', scope=str(study.pk),
        summary={'username': target.username, 'user_id': target.pk}, errors=[],
        binding='b' * 64, staged={'user_id': target.pk, 'study_id': str(study.pk)},
        expires_at=timezone.now() + timedelta(minutes=10))

    permit_token = 'synthetic-p03r03b-permit-token'
    RecoveryPermit.objects.create(session=session, issuer=target,
                                  token_hash=digest(permit_token),
                                  expires_at=timezone.now() + timedelta(minutes=15))
    code = '246810'
    RecoveryCode.objects.create(session=session, study=study, release=release, issuer=target,
                                code_hash=services.recovery_code_digest(code),
                                expires_at=timezone.now() + timedelta(minutes=5))
    accounts.audit(target, 'account.invite_issued', 'p03r03b_invitee',
                   after={'username': 'p03r03b_invitee'})
    old_audit = Audit.objects.get(action='account.invite_issued', actor_id=target.pk)

    counts_before = (Study.objects.count(), Participant.objects.count(),
                     Session.objects.count(), Event.objects.count(),
                     Build.objects.count(), Release.objects.count())
    revision = accounts.instance_revision()

    owner_client = signed_in(owner.username, OWNER_PASSWORD)
    response = owner_client.post('/users', delete_payload(target.username, target.username))
    assert response.status_code == 200, response.content[:400]
    body = response.content.decode()
    assert '已永久删除账号' in body
    match = PRINCIPAL_RE.search(body)
    assert match and match.group(1) == str(old_principal.pk), 'the notice names the stable subject'

    # The login account, profile and grants are gone; the Principal stays.
    assert not get_user_model().objects.filter(pk=target.pk).exists()
    assert AccountProfile.objects.filter(user_id=target.pk).count() == 0
    assert Grant.objects.filter(user_id=target.pk).count() == 0
    old_principal.refresh_from_db()
    assert old_principal.deleted_at is not None and old_principal.user_id is None

    # Old login: the Django session row is removed and the cookie is anonymous.
    assert not DjangoSession.objects.filter(session_key=session_key).exists()
    assert target_client.get('/')['Location'] == '/login'
    fresh = Client()
    assert login(fresh, target.username, USER_PASSWORD).status_code == 200
    assert '_auth_user_id' not in fresh.session

    # Pending invitations bound to the released username can never activate.
    stale_account.refresh_from_db()
    stale_study.refresh_from_db()
    assert stale_account.revoked is True and stale_study.revoked is True
    activation = fresh.post('/activate-account', {'token': 'stale-account-token',
                                                  'password': ACTIVATION_PASSWORD,
                                                  'confirm': ACTIVATION_PASSWORD})
    assert activation.status_code == 403 and ACTIVATION_FAILED in activation.content.decode()
    assert not get_user_model().objects.filter(username='p03r03b_invitee').exists()

    # The target's own issued account/study invitations and recovery tickets
    # are removed; the stored token/code cannot be redeemed any more.
    assert not AccountInvitation.objects.filter(issuer_id=target.pk).exists()
    assert not Invitation.objects.filter(issuer_id=target.pk).exists()
    assert not RecoveryPermit.objects.filter(issuer_id=target.pk).exists()
    assert not RecoveryCode.objects.filter(issuer_id=target.pk).exists()
    with pytest.raises(RecoveryPermit.DoesNotExist):
        services.recover(session.pk, 'proof', permit_token)
    expiry_before = Session.objects.get(pk=session.pk).expires_at
    with pytest.raises(Rejected) as denied:
        services.redeem_recovery_code({
            'capability': services.RECOVERY_CODE_CAPABILITY,
            'instance_id': str(instance.instance_id), 'study_id': str(study.pk),
            'release_id': str(release.pk), 'build_id': str(build.pk),
            'proof': 'p' * 48, 'code': code})
    assert denied.value.code == 'recovery_denied' and denied.value.status == 403
    assert Session.objects.get(pk=session.pk).expires_at == expiry_before

    # Previews: the target's own row is removed, a preview that operates on it
    # is expired with its staged intent cleared and can no longer commit.
    assert not PermissionPreview.objects.filter(pk=own_preview.pk).exists()
    other_preview.refresh_from_db()
    assert other_preview.staged is None and other_preview.expires_at <= timezone.now()
    commit = owner_client.post('/users', {'op': 'matrix_commit', 'preview_id': str(other_preview.pk),
                                          'password': OWNER_PASSWORD,
                                          'revision': str(accounts.instance_revision())})
    assert commit.status_code == 409
    assert commit.context['error'] == gui_accounts.message_for('preview_expired')

    # Studies, participants, sessions, events and earlier audits survive.
    assert (Study.objects.count(), Participant.objects.count(), Session.objects.count(),
            Event.objects.count(), Build.objects.count(), Release.objects.count()) == counts_before
    assert Study.objects.filter(pk=study.pk).exists()
    old_audit.refresh_from_db()
    assert old_audit.actor_id is None and old_audit.actor_principal_id == old_principal.pk
    assert accounts.audit_actor_display(old_audit) == f'已删除账号 {old_principal.pk}'
    deleted_audit = Audit.objects.get(action='account.deleted')
    assert deleted_audit.actor_id == owner.pk
    assert deleted_audit.after['principal'] == str(old_principal.pk)

    # The username can be registered again: a new Principal, no inheritance.
    created = owner_client.post('/users', {'op': 'create_temp', 'username': target.username,
                                           'password': OWNER_PASSWORD,
                                           'revision': str(accounts.instance_revision())})
    assert created.status_code == 200
    temporary = SECRET_RE.search(created.content.decode()).group(1)
    new_user = get_user_model().objects.get(username=target.username)
    new_principal = Principal.objects.get(user=new_user)
    assert new_principal.pk != old_principal.pk and new_principal.deleted_at is None
    assert not Grant.objects.filter(user=new_user).exists()
    assert access.canonical_policy(new_user).study_overrides == {}
    assert login(Client(), target.username, temporary).status_code == 302

    # The page lists the new account and still renders the old audit as the
    # stable deleted subject, never as the reused username.
    page = owner_client.get('/users').content.decode()
    assert 'data-account-audit' in page
    assert f'data-account-row="{new_user.pk}"' in page
    assert f'data-account-row="{target.pk}"' not in page
    assert f'data-audit-actor="已删除账号 {old_principal.pk}"' in page
    assert 'data-deleted-principal=' not in page

    evidence('delete_full.json', {
        'principal': str(old_principal.pk), 'new_principal': str(new_principal.pk),
        'sessions_removed': True, 'stale_invitations_revoked': True,
        'target_issuances_removed': True, 'previews_invalidated': True,
        'counts_preserved': counts_before, 'audit_label': accounts.audit_actor_display(old_audit),
        'revision_before': revision})


# --- refusals leave everything unchanged --------------------------------------

def test_delete_refusals_write_nothing(world):
    owner, study, target = world['owner'], world['study'], world['target']
    peer = make_user('p03r03b_peer_admin', USER_PASSWORD, role='admin')
    default_admin = make_user('p03r03b_admin_default', ADMIN_PASSWORD, role='admin')
    admin_client = signed_in(default_admin.username, ADMIN_PASSWORD)
    owner_client = signed_in(owner.username, OWNER_PASSWORD)
    revision = accounts.instance_revision()
    stored_scope = dict(access.canonical_policy(target).study_overrides)

    def unchanged():
        target.refresh_from_db()
        assert target.is_active and target.check_password(USER_PASSWORD)
        assert AccountProfile.objects.filter(user=target).exists()
        principal = Principal.objects.get(user_id=target.pk)
        assert principal.deleted_at is None and principal.user_id == target.pk
        assert dict(access.canonical_policy(target).study_overrides) == stored_scope
        assert Audit.objects.filter(action='account.deleted').count() == 0
        assert accounts.instance_revision() == revision
        assert login(Client(), target.username, USER_PASSWORD).status_code == 302

    # Wrong actor password / stale revision.
    wrong = owner_client.post('/users', delete_payload(target.username, target.username,
                                                       password='not-the-owner-password',
                                                       revision=revision))
    assert wrong.status_code == 403 and '重新认证失败' in wrong.content.decode()
    unchanged()
    stale = owner_client.post('/users', delete_payload(target.username, target.username,
                                                       revision=revision + 1))
    assert stale.status_code == 409 and '治理版本已变化' in stale.content.decode()
    unchanged()

    # Nobody deletes the Owner and nobody deletes itself through this route.
    owner_attack = admin_client.post('/users', delete_payload(owner.username, owner.username,
                                                              password=ADMIN_PASSWORD, revision=revision))
    assert owner_attack.status_code == 403
    assert owner_attack.context['error'] == gui_accounts.message_for('owner_protected')
    self_attack = owner_client.post('/users', delete_payload(owner.username, owner.username))
    assert self_attack.status_code == 409
    assert self_attack.context['error'] == gui_accounts.message_for('self_target')
    owner.refresh_from_db()
    assert owner.is_active and Instance.objects.get(pk=1).owner_id == owner.pk
    unchanged()

    # Confirmation mismatch and a missing confirmation are refusals, not deletes.
    mismatch = owner_client.post('/users', delete_payload(target.username, 'p03r03b_other'))
    assert mismatch.status_code == 409
    assert mismatch.context['error'] == gui_accounts.message_for('delete_confirm_mismatch')
    unchanged()
    missing = owner_client.post('/users', delete_payload(target.username, ''))
    assert missing.status_code == 409
    unchanged()

    # An Admin without the Admin deletion switches cannot delete an Admin peer.
    refused = admin_client.post('/users', delete_payload(peer.username, peer.username,
                                                         password=ADMIN_PASSWORD, revision=revision))
    assert refused.status_code == 403
    assert get_user_model().objects.filter(pk=peer.pk).exists()
    assert Audit.objects.filter(action='account.deleted').count() == 0

    # The Admin's default role cannot delete the Owner through this route.
    refused = admin_client.post('/users', delete_payload(owner.username, owner.username,
                                                         password=ADMIN_PASSWORD, revision=revision))
    assert refused.status_code == 403
    owner.refresh_from_db()
    assert owner.is_active
    unchanged()
    assert Study.objects.filter(pk=study.pk).exists()


# --- v2 Admin switches and whole-scope dominance ------------------------------

def test_v2_admin_switches_and_whole_scope_dominance(world):
    owner, study, target, plain = (world['owner'], world['study'], world['target'],
                                   world['plain'])
    # A restricted Admin (empty future bound) dominates an account with no
    # study scope, but not one holding actions anywhere.
    restricted = make_user('p03r03b_admin_restricted', ADMIN_PASSWORD, role='admin', future=[])
    restricted_client = signed_in(restricted.username, ADMIN_PASSWORD)
    response = restricted_client.post('/users', delete_payload(plain.username, plain.username,
                                                               password=ADMIN_PASSWORD))
    assert response.status_code == 200, response.content[:400]
    assert not get_user_model().objects.filter(pk=plain.pk).exists()

    refused = restricted_client.post('/users', delete_payload(target.username, target.username,
                                                              password=ADMIN_PASSWORD))
    assert refused.status_code == 403
    assert refused.context['error'] == gui_accounts.message_for('higher_privilege_target')
    assert get_user_model().objects.filter(pk=target.pk).exists()
    assert dict(access.canonical_policy(target).study_overrides)

    # Without both Owner-controlled Admin switches an unrestricted Admin can
    # never delete an Admin peer (it lacks accounts.manage_admin/delete_admin).
    unrestricted = make_user('p03r03b_admin_unrestricted', ADMIN_PASSWORD, role='admin')
    unrestricted_client = signed_in(unrestricted.username, ADMIN_PASSWORD)
    peer = make_user('p03r03b_peer_admin', USER_PASSWORD, role='admin')
    refused = unrestricted_client.post('/users', delete_payload(peer.username, peer.username,
                                                                password=ADMIN_PASSWORD))
    assert refused.status_code == 403
    assert get_user_model().objects.filter(pk=peer.pk).exists()

    # Both switches still require whole-scope dominance: a restricted-Admin
    # actor with the switches cannot dominate the unrestricted Admin default.
    AccountProfile.objects.filter(user=restricted).update(
        platform_overrides={'accounts.manage_admin': True, 'accounts.delete_admin': True})
    wide_peer = make_user('p03r03b_wide_peer', USER_PASSWORD, role='admin')
    refused = restricted_client.post('/users', delete_payload(wide_peer.username,
                                                              wide_peer.username,
                                                              password=ADMIN_PASSWORD))
    assert refused.status_code == 403
    assert refused.context['error'] == gui_accounts.message_for('higher_privilege_target')
    assert get_user_model().objects.filter(pk=wide_peer.pk).exists()

    # With the switches and the same full scope, the Admin deletion is legal.
    AccountProfile.objects.filter(user=unrestricted).update(
        platform_overrides={'accounts.manage_admin': True, 'accounts.delete_admin': True})
    response = unrestricted_client.post('/users', delete_payload(wide_peer.username,
                                                                 wide_peer.username,
                                                                 password=ADMIN_PASSWORD))
    assert response.status_code == 200, response.content[:400]
    assert not get_user_model().objects.filter(pk=wide_peer.pk).exists()

    # A non-Owner can never remove an Owner-controlled switch from any target:
    # the whole-account comparison projects the empty policy after deletion.
    switch_holder = make_user('p03r03b_switch_holder', USER_PASSWORD, role='admin',
                              platform={'accounts.create_admin': True})
    switch_refused = unrestricted_client.post('/users', delete_payload(switch_holder.username,
                                                                       switch_holder.username,
                                                                       password=ADMIN_PASSWORD))
    assert switch_refused.status_code == 403
    assert get_user_model().objects.filter(pk=switch_holder.pk).exists()
    switch_holder.refresh_from_db()
    assert switch_holder.is_active

    # The Owner deletes a peer Admin holding any switch: the fixed Owner bypass.
    owner_client = signed_in(owner.username, OWNER_PASSWORD)
    response = owner_client.post('/users', delete_payload(switch_holder.username,
                                                          switch_holder.username))
    assert response.status_code == 200, response.content[:400]
    assert not get_user_model().objects.filter(pk=switch_holder.pk).exists()
    assert Study.objects.filter(pk=study.pk).exists()


# --- audit failure is a full rollback -----------------------------------------

def test_audit_failure_rolls_back_deletion_and_invalidations(world):
    owner, study, target = world['owner'], world['study'], world['target']
    old_principal = Principal.objects.get(user_id=target.pk)
    target_client = signed_in(target.username, USER_PASSWORD)
    session_key = target_client.session.session_key
    stale_account = AccountInvitation.objects.create(
        issuer=owner, username=target.username, role='user',
        token_hash=digest('rollback-account-token'), expires_at=timezone.now() + timedelta(hours=1))
    own_preview = PermissionPreview.objects.create(
        actor=target, kind='matrix', scope=str(study.pk), summary={}, errors=[],
        binding='c' * 64, staged={'user_id': target.pk},
        expires_at=timezone.now() + timedelta(minutes=10))
    scope_before = dict(access.canonical_policy(target).study_overrides)
    # A historical row without the stable subject is part of the same rollback.
    legacy = Audit.objects.create(study=study, actor=target,
                                  action='publication.policy_changed', target=str(study.pk))

    original = accounts.audit
    accounts.audit = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('synthetic audit failure'))
    try:
        owner_client = signed_in(owner.username, OWNER_PASSWORD)
        owner_client.raise_request_exception = False
        response = owner_client.post('/users', delete_payload(target.username, target.username))
        assert response.status_code == 500
    finally:
        accounts.audit = original

    # Everything the transaction touched is still exactly there.
    target.refresh_from_db()
    assert target.is_active and target.check_password(USER_PASSWORD)
    old_principal.refresh_from_db()
    assert old_principal.deleted_at is None and old_principal.user_id == target.pk
    assert dict(access.canonical_policy(target).study_overrides) == scope_before
    stale_account.refresh_from_db()
    assert stale_account.revoked is False
    assert PermissionPreview.objects.filter(pk=own_preview.pk).exists()
    assert DjangoSession.objects.filter(session_key=session_key).exists()
    assert login(Client(), target.username, USER_PASSWORD).status_code == 302
    assert Audit.objects.filter(action='account.deleted').count() == 0
    legacy.refresh_from_db()
    assert legacy.actor_id == target.pk and legacy.actor_principal_id is None


# --- v1 keeps its old boundary ------------------------------------------------

def test_delete_keeps_protect_boundaries_and_never_wide_cascades(world):
    """The deletion removes credential rows itself; PROTECT is not weakened."""
    from django.db.models import PROTECT, SET_NULL
    from django.db.models.deletion import ProtectedError
    study = world['study']
    make_session(study)
    assert Audit._meta.get_field('actor').remote_field.on_delete is SET_NULL
    assert Audit._meta.get_field('actor_principal').remote_field.on_delete is PROTECT
    for model in (AccountInvitation, Invitation, RecoveryPermit, RecoveryCode):
        assert model._meta.get_field('issuer').remote_field.on_delete is PROTECT
    assert PermissionPreview._meta.get_field('actor').remote_field.on_delete is PROTECT
    assert Principal._meta.get_field('user').remote_field.on_delete is SET_NULL
    # Deleting a study with participant data still refuses to cascade.
    with pytest.raises(ProtectedError):
        study.delete()
    assert Study.objects.filter(pk=study.pk).exists()


def test_delete_profile_less_and_inactive_accounts_use_ordinary_role(world):
    owner = world['owner']
    orphan = get_user_model().objects.create_user('p03r03b_no_profile', password=USER_PASSWORD)
    disabled = make_user('p03r03b_disabled_target', USER_PASSWORD)
    disabled_principal = Principal.objects.get(user=disabled)
    disabled.is_active = False
    disabled.save(update_fields=['is_active'])
    owner_client = signed_in(owner.username, OWNER_PASSWORD)
    orphan_principals = set(Principal.objects.values_list('pk', flat=True))

    assert owner_client.post('/users', delete_payload(orphan.username, orphan.username)).status_code == 200
    assert not get_user_model().objects.filter(pk=orphan.pk).exists()
    # The stable subject is created and marked deleted even without a profile.
    created = Principal.objects.exclude(pk__in=orphan_principals)
    assert created.count() == 1
    orphan_principal = created.get()
    assert orphan_principal.deleted_at is not None and orphan_principal.user_id is None

    assert owner_client.post('/users', delete_payload(disabled.username, disabled.username)).status_code == 200
    assert not get_user_model().objects.filter(pk=disabled.pk).exists()
    disabled_principal.refresh_from_db()
    assert disabled_principal.deleted_at is not None and disabled_principal.user_id is None


def test_v1_delete_boundary_stays_owner_only_for_admin_targets(db):
    owner = make_user('p03r03b_v1_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)  # version 1
    assert access.authorization_version(instance) == 1
    study = Study.objects.create(title='P03R03B v1 study')
    admin = make_user('p03r03b_v1_admin', ADMIN_PASSWORD, role='admin')
    peer = make_user('p03r03b_v1_peer', USER_PASSWORD, role='admin')
    plain = make_user('p03r03b_v1_plain', USER_PASSWORD)
    holder = make_user('p03r03b_v1_holder', USER_PASSWORD)
    Grant.objects.create(user=holder, study=study, action='study.view', delegable=True)
    admin_client = signed_in(admin.username, ADMIN_PASSWORD)

    # A v1 Admin can delete an ordinary account without study scope ...
    assert admin_client.post('/users', delete_payload(plain.username, plain.username,
                                                      password=ADMIN_PASSWORD)).status_code == 200
    assert not get_user_model().objects.filter(pk=plain.pk).exists()

    # ... but never an Admin target (accounts.delete_admin never existed for a
    # v1 Admin) and never a target whose grants it cannot dominate.
    refused = admin_client.post('/users', delete_payload(peer.username, peer.username,
                                                         password=ADMIN_PASSWORD))
    assert refused.status_code == 403
    assert get_user_model().objects.filter(pk=peer.pk).exists()
    refused = admin_client.post('/users', delete_payload(holder.username, holder.username,
                                                         password=ADMIN_PASSWORD))
    assert refused.status_code == 403
    assert Grant.objects.filter(user=holder).exists()

    # The Owner deletes an Admin target and keeps the study.
    owner_client = signed_in(owner.username, OWNER_PASSWORD)
    assert owner_client.post('/users', delete_payload(peer.username, peer.username)).status_code == 200
    assert not get_user_model().objects.filter(pk=peer.pk).exists()
    assert Study.objects.filter(pk=study.pk).exists()
    assert access.authorization_version(instance) == 1


# --- stable audit across every human-actor path -------------------------------

def test_stable_audit_survives_real_study_and_recovery_paths(world):
    """Real study creation/modification and recovery issuance/redemption.

    Every audit row written by a *different* module (the gui study routes and the
    services recovery path) must name the actor's stable Principal after the
    account is deleted, and a historical row written without one is rebound from
    the exact actor foreign key inside the deletion transaction.
    """
    owner, instance = world['owner'], world['instance']
    actor = make_user('p03r03b_audit_actor', USER_PASSWORD)
    principal = Principal.objects.get(user=actor)
    client = signed_in(actor.username, USER_PASSWORD)

    # 1) The real POST / creates a study; the creator receives the full v2 action
    #    set, so the same account can configure it through the real route.
    assert client.post('/', {'title': 'P03R03B audit study'}).status_code == 302
    study = Study.objects.get(title='P03R03B audit study')
    created = Audit.objects.get(action='study.created', study=study)
    assert created.actor_id == actor.pk and created.actor_principal_id == principal.pk
    assert client.post(f'/studies/{study.pk}', {'op': 'configure', 'mode': 'anonymous',
                                                'max_sessions': '2'}).status_code == 302
    configured = Audit.objects.get(action='configure', study=study)
    assert configured.actor_id == actor.pk and configured.actor_principal_id == principal.pk

    # 2) Real recovery issuance and redemption write their own audits.
    build, release, participant, session = make_session(study, proof='p' * 48)
    issued = services.issue_recovery_code(actor, session.pk)
    redeemed = services.redeem_recovery_code({
        'capability': services.RECOVERY_CODE_CAPABILITY,
        'instance_id': str(instance.instance_id), 'study_id': str(study.pk),
        'release_id': str(release.pk), 'build_id': str(build.pk),
        'proof': 'p' * 48, 'code': issued['code']})
    assert redeemed['session_id'] == str(session.pk)
    code_issued = Audit.objects.get(action='recovery.code_issued', study=study)
    code_redeemed = Audit.objects.get(action='recovery.code_redeemed', study=study)
    assert code_issued.actor_principal_id == principal.pk
    assert code_redeemed.actor_principal_id == principal.pk

    # 3) A historical row written by a path that stored only the user reference
    #    (for example publication.py, outside the remediation set) is rebound
    #    from the exact actor FK, never from a username guess.
    legacy = Audit.objects.create(study=study, actor=actor, action='publication.policy_changed',
                                  target=str(study.pk))
    assert legacy.actor_principal_id is None

    owner_client = signed_in(owner.username, OWNER_PASSWORD)
    response = owner_client.post('/users', delete_payload(actor.username, actor.username))
    assert response.status_code == 200, response.content[:400]
    assert not get_user_model().objects.filter(pk=actor.pk).exists()

    for entry in (created, configured, code_issued, code_redeemed, legacy):
        entry.refresh_from_db()
        assert entry.actor_id is None
        assert entry.actor_principal_id == principal.pk
        assert accounts.audit_actor_display(entry) == f'已删除账号 {principal.pk}'
    deleted = Audit.objects.get(action='account.deleted')
    assert deleted.after['audits_rebound'] >= 1
    assert Study.objects.filter(pk=study.pk).exists()


# --- invitation identity: stable subject and application identity -------------

def test_study_invitation_binds_the_stable_subject_and_never_reuses_a_username(world):
    """The confirmed invitation identity contract on the legacy study route.

    A version-2 invitation for an existing account binds that account's stable
    Principal and still accepts through the subject's own login. A pending one is
    revoked at deletion and can never attach to the reused username, and a
    version-2 new-account application never adopts an account that appeared
    through another path.
    """
    owner, study, plain = world['owner'], world['study'], world['plain']
    instance = Instance.objects.get(pk=1)
    instance.authorization_version = 1
    instance.save(update_fields=['authorization_version'])
    for action in ('study.view', 'member.manage', 'permission.delegate'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    owner_client = signed_in(owner.username, OWNER_PASSWORD)

    def issue(username):
        response = owner_client.post(f'/studies/{study.pk}', {
            'op': 'invite', 'revision': str(accounts.instance_revision()),
            'username': username, 'actions': ['study.view']})
        assert response.status_code == 200, response.content[:400]
        token = KEY_RE.search(response.content.decode()).group(1)
        return Invitation.objects.get(study=study, username=username), token

    # 1) An existing account's invitation binds its Principal and still accepts
    #    through that subject's own login.
    target = make_user('p03r03b_bound_target', USER_PASSWORD)
    target_principal = Principal.objects.get(user=target)
    invitation, token = issue(target.username)
    assert invitation.identity_version == 2
    assert invitation.principal_id == target_principal.pk
    accepted = signed_in(target.username, USER_PASSWORD).post('/activate', {'token': token})
    assert accepted.status_code == 302
    assert Grant.objects.filter(user=target, study=study, action='study.view').exists()

    # 2) A pending invitation for another existing account is revoked by deletion
    #    and cannot attach to the reused username, even if the row were stale.
    pending, pending_token = issue(plain.username)
    plain_principal = Principal.objects.get(user=plain)
    assert pending.principal_id == plain_principal.pk
    assert owner_client.post('/users', delete_payload(plain.username, plain.username)).status_code == 200
    pending.refresh_from_db()
    assert pending.revoked is True
    rebuilt = owner_client.post('/users', {'op': 'invite_account', 'username': plain.username,
                                           'password': OWNER_PASSWORD,
                                           'revision': str(accounts.instance_revision())})
    assert rebuilt.status_code == 200
    rebuilt_token = INVITE_TOKEN_RE.search(rebuilt.content.decode()).group(1)
    assert Client().post('/activate-account', {'token': rebuilt_token,
                                               'password': APPLICATION_PASSWORD,
                                               'confirm': APPLICATION_PASSWORD}).status_code == 200
    new_user = get_user_model().objects.get(username=plain.username)
    new_principal = Principal.objects.get(user=new_user)
    assert new_principal.pk != pending.principal_id
    Invitation.objects.filter(pk=pending.pk).update(revoked=False)
    refused = signed_in(plain.username, APPLICATION_PASSWORD).post('/activate', {'token': pending_token})
    assert refused.status_code == 403 and refused.json()['code'] == 'invitation_inactive'
    assert not Grant.objects.filter(user=new_user, study=study).exists()

    # 3) A version-2 new-account application is identified by its own UUID: it
    #    creates the account it was issued for, but never adopts an account that
    #    appeared through another application first.
    free_invitation, free_token = issue('p03r03b_free_member')
    assert free_invitation.identity_version == 2 and free_invitation.principal_id is None
    other = owner_client.post('/users', {'op': 'invite_account', 'username': 'p03r03b_free_member',
                                         'password': OWNER_PASSWORD,
                                         'revision': str(accounts.instance_revision())})
    assert other.status_code == 200
    other_token = INVITE_TOKEN_RE.search(other.content.decode()).group(1)
    assert Client().post('/activate-account', {'token': other_token,
                                               'password': APPLICATION_PASSWORD,
                                               'confirm': APPLICATION_PASSWORD}).status_code == 200
    refused = signed_in('p03r03b_free_member', APPLICATION_PASSWORD).post('/activate', {'token': free_token})
    assert refused.status_code == 403 and refused.json()['code'] == 'invitation_inactive'
    assert not Grant.objects.filter(user__username='p03r03b_free_member', study=study).exists()

    # The real new-account application still works for a free username.
    application, application_token = issue('p03r03b_new_member')
    assert application.principal_id is None
    accepted = Client().post('/activate', {'token': application_token,
                                           'password': ACTIVATION_PASSWORD})
    assert accepted.status_code == 302
    newcomer = get_user_model().objects.get(username='p03r03b_new_member')
    assert Principal.objects.filter(user=newcomer).exists()
    assert Grant.objects.filter(user=newcomer, study=study, action='study.view').exists()


def test_account_application_identity_is_recorded_and_replayed_once(world):
    """The account invitation UUID is the independent application identity.

    Issuance names it in the audit, activation records it with the created
    account and consumes exactly that row, a replay opens nothing, and a later
    same-name application is a different UUID with a fresh Principal.
    """
    owner = world['owner']
    owner_client = signed_in(owner.username, OWNER_PASSWORD)

    def invite(username):
        response = owner_client.post('/users', {'op': 'invite_account', 'username': username,
                                                'password': OWNER_PASSWORD,
                                                'revision': str(accounts.instance_revision())})
        assert response.status_code == 200, response.content[:400]
        token = INVITE_TOKEN_RE.search(response.content.decode()).group(1)
        return AccountInvitation.objects.get(username=username, consumed=False), token

    invitation, token = invite('p03r03b_application')
    owner_principal = Principal.objects.get(user=owner)
    issued = Audit.objects.get(action='account.invite_issued', target=str(invitation.pk))
    assert issued.actor_principal_id == owner_principal.pk

    activated = Client().post('/activate-account', {'token': token,
                                                    'password': APPLICATION_PASSWORD,
                                                    'confirm': APPLICATION_PASSWORD})
    assert activated.status_code == 200 and '账号已激活' in activated.content.decode()
    account = get_user_model().objects.get(username='p03r03b_application')
    principal = Principal.objects.get(user=account)
    activation = Audit.objects.get(action='account.invitation_activated', target=str(account.pk))
    assert activation.after['application'] == str(invitation.pk)
    assert activation.after['principal'] == str(principal.pk)

    # Replay of the consumed application is refused and creates nothing.
    replay = Client().post('/activate-account', {'token': token, 'password': APPLICATION_PASSWORD,
                                                 'confirm': APPLICATION_PASSWORD})
    assert replay.status_code == 403 and '激活失败' in replay.content.decode()
    assert get_user_model().objects.filter(username='p03r03b_application').count() == 1

    # After deletion the consumed application can never activate again, and a
    # new same-name application is a different UUID with a fresh subject.
    assert owner_client.post('/users', delete_payload(account.username, account.username)).status_code == 200
    stale = Client().post('/activate-account', {'token': token, 'password': APPLICATION_PASSWORD,
                                                'confirm': APPLICATION_PASSWORD})
    assert stale.status_code == 403 and '激活失败' in stale.content.decode()
    second, second_token = invite('p03r03b_application')
    assert second.pk != invitation.pk and second.consumed is False
    again = Client().post('/activate-account', {'token': second_token,
                                                'password': APPLICATION_PASSWORD,
                                                'confirm': APPLICATION_PASSWORD})
    assert again.status_code == 200
    rebuilt = get_user_model().objects.get(username='p03r03b_application')
    rebuilt_principal = Principal.objects.get(user=rebuilt)
    assert rebuilt_principal.pk != principal.pk
    assert not Grant.objects.filter(user=rebuilt).exists()
    assert access.canonical_policy(rebuilt).study_overrides == {}
    assert Principal.objects.filter(pk=principal.pk, deleted_at__isnull=False).exists()


def test_bulk_import_preview_does_not_survive_a_deleted_username(world):
    """A staged bulk application is expired by the deletion of its target.

    The same-name competition order is covered too: a staged bulk invitation for
    a free username never adopts an account that appeared through another
    application first.
    """
    owner, target = world['owner'], world['target']
    profile = AccountProfile.objects.get(user=target)
    owner_client = signed_in(owner.username, OWNER_PASSWORD)

    # 1) A valid staged bulk operation on the target is expired by its deletion;
    #    the preview can never apply its private intent to a reused username.
    staged = upload_users(owner_client, users_workbook(
        (target.username, 'disable', str(profile.revision), '', '', '')))
    assert staged.status_code == 200, staged.content[:400]
    preview_id = PREVIEW_RE.search(staged.content.decode()).group(1)
    assert owner_client.post('/users', delete_payload(target.username, target.username)).status_code == 200
    commit = owner_client.post('/users', {'op': 'import_users_commit', 'preview_id': preview_id,
                                          'password': OWNER_PASSWORD})
    assert commit.status_code == 409
    assert commit.context['error'] == gui_accounts.message_for('preview_expired')
    assert not get_user_model().objects.filter(username=target.username).exists()

    # 2) A staged bulk invitation for a free username is refused once another
    #    application created that account; it never mints a second invitation
    #    that could attach to it.
    staged = upload_users(owner_client, users_workbook(
        ('p03r03b_bulk_member', 'create', '', 'user', '', '')))
    assert staged.status_code == 200, staged.content[:400]
    preview_id = PREVIEW_RE.search(staged.content.decode()).group(1)
    created = owner_client.post('/users', {'op': 'create_temp', 'username': 'p03r03b_bulk_member',
                                           'password': OWNER_PASSWORD,
                                           'revision': str(accounts.instance_revision())})
    assert created.status_code == 200
    commit = owner_client.post('/users', {'op': 'import_users_commit', 'preview_id': preview_id,
                                          'password': OWNER_PASSWORD})
    assert commit.status_code == 409
    assert commit.context['error'] == gui_accounts.message_for('preview_stale')
    assert not AccountInvitation.objects.filter(username='p03r03b_bulk_member').exists()
    assert get_user_model().objects.filter(username='p03r03b_bulk_member').count() == 1


# --- real Chrome: red confirmation entry, refusals and the resulting list -----

@pytest.fixture
def browser_world(db):
    owner = make_user('p03r03b_owner', OWNER_PASSWORD)
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    study = Study.objects.create(title='P03R03B browser study')
    victim = make_user('p03r03b_browser_victim', USER_PASSWORD)
    victim_principal = Principal.objects.get(user_id=victim.pk)
    plain = make_user('p03r03b_browser_plain', USER_PASSWORD)
    admin = make_user('p03r03b_browser_admin', ADMIN_PASSWORD, role='admin', future=[])
    peer = make_user('p03r03b_browser_peer', USER_PASSWORD, role='admin', future=[])
    # The victim acted before: its audit row must render as a deleted subject.
    accounts.audit(victim, 'account.invite_issued', 'p03r03b_browser_invitee',
                   after={'username': 'p03r03b_browser_invitee'})
    return {'owner': owner, 'study': study, 'victim': victim, 'victim_principal': victim_principal,
            'plain': plain, 'admin': admin, 'peer': peer}


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')
def test_actual_chrome_delete_confirmation_red_entry_and_refusals(
        live_server, browser_world, evidence, run_chrome_tokens):
    env = dict(os.environ, GEP_TEST_BASE=live_server.url, GEP_OWNER_PASSWORD=OWNER_PASSWORD,
               GEP_ADMIN_PASSWORD=ADMIN_PASSWORD,
               GEP_VICTIM=str(browser_world['victim'].pk),
               GEP_PLAIN=str(browser_world['plain'].pk),
               GEP_PEER=str(browser_world['peer'].pk),
               GEP_ADMIN_PK=str(browser_world['admin'].pk))
    result, reported = run_chrome_tokens(SCRIPT, env, timeout=240)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert reported == []
    observations = json.loads(re.search(r'P03R03B_OBSERVATIONS (\{.*\})', result.stdout).group(1))
    assert observations['red_danger'] is True
    assert observations['owner_row_no_delete'] is True
    assert observations['wrong_confirm_refused'] is True
    assert observations['wrong_password_refused'] is True
    assert observations['deleted_notice'] is True
    assert observations['victim_row_gone'] is True
    assert observations['audit_stable_subject'] is True
    assert observations['audit_actor'] == f"已删除账号 {browser_world['victim_principal'].pk}"
    assert observations['admin_self_no_delete'] is True
    assert observations['admin_peer_no_delete'] is True
    assert observations['peer_refusal_status'] == 403
    assert observations['peer_refused_and_alive'] is True
    assert observations['admin_plain_deleted'] is True
    assert observations['page_errors'] == []

    victim = browser_world['victim']
    assert not get_user_model().objects.filter(pk=victim.pk).exists()
    assert not get_user_model().objects.filter(pk=browser_world['plain'].pk).exists()
    assert get_user_model().objects.filter(pk=browser_world['peer'].pk).exists()
    evidence('chrome_journey.json', observations)


SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE;
const ownerPassword=process.env.GEP_OWNER_PASSWORD, adminPassword=process.env.GEP_ADMIN_PASSWORD;
const victimId=process.env.GEP_VICTIM, plainId=process.env.GEP_PLAIN, peerId=process.env.GEP_PEER;
const adminId=process.env.GEP_ADMIN_PK;
const observations={};
const errors=[];
async function login(page,username,password){
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
}
function redDominant(rgb){
  const parts=rgb.match(/\d+/g).map(Number);
  return parts[0]>parts[1]&&parts[0]>parts[2];
}
async function attemptDelete(page,rowId,confirm,password){
  const entry=page.locator('[data-account-row="'+rowId+'"] [data-delete-account]');
  if(!await entry.evaluate(el=>el.open)){await entry.locator('summary').click();}
  const form=entry.locator('[data-delete-form="1"]');
  await form.locator('[name=confirm_username]').fill(confirm);
  await form.locator('[name=password]').fill(password);
  await form.getByRole('button',{name:'永久删除账号'}).click();
  await page.waitForLoadState('load');
}
try {
  const owner=await browser.newContext();
  const page=await owner.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  await login(page,'p03r03b_owner',ownerPassword);
  await page.goto(base+'/users');

  // 1) The red permanent-delete entry exists for a deletable account only.
  const victimRow=page.locator('[data-account-row="'+victimId+'"]');
  await expect(victimRow).toBeVisible();
  const deleteEntry=victimRow.locator('[data-delete-account]');
  expect(await deleteEntry.count()).toBe(1);
  await deleteEntry.locator('summary').click();
  const dangerButton=deleteEntry.locator('button[data-danger="1"]');
  await expect(dangerButton).toBeVisible();
  const dangerColor=await dangerButton.evaluate(el=>getComputedStyle(el).color);
  observations.red_danger=redDominant(dangerColor);
  observations.danger_color=dangerColor;
  observations.owner_row_no_delete=(await page.locator('[data-owner-row="1"] [data-delete-account]').count())===0;

  // 2) The confirmation needs the exact username and the actor password.
  await attemptDelete(page,victimId,'p03r03b_browser_wrong',ownerPassword);
  observations.wrong_confirm_refused=await page.locator('div.error').first().isVisible();
  expect(await page.locator('[data-account-row="'+victimId+'"]').count()).toBe(1);

  await attemptDelete(page,victimId,'p03r03b_browser_victim','not-the-owner-password');
  const reauthError=await page.locator('div.error').first().innerText();
  observations.wrong_password_refused=reauthError.includes('重新认证失败');
  expect(await page.locator('[data-account-row="'+victimId+'"]').count()).toBe(1);

  // 3) The legal deletion removes the row and names the stable subject.
  await attemptDelete(page,victimId,'p03r03b_browser_victim',ownerPassword);
  observations.deleted_notice=(await page.locator('[data-deleted-principal]').count())===1;
  observations.victim_row_gone=(await page.locator('[data-account-row="'+victimId+'"]').count())===0;
  const auditRow=page.locator('[data-audit-row="account.invite_issued"]').first();
  observations.audit_actor=await auditRow.getAttribute('data-audit-actor');
  observations.audit_stable_subject=observations.audit_actor.startsWith('已删除账号');

  // 4) Admin: own row and a peer Admin have no delete entry; a forged peer
  //    deletion is refused by the server and the peer stays.
  const adminContext=await browser.newContext();
  const adminPage=await adminContext.newPage();
  adminPage.on('pageerror',e=>errors.push('admin:'+e.message));
  await login(adminPage,'p03r03b_browser_admin',adminPassword);
  await adminPage.goto(base+'/users');
  observations.admin_self_no_delete=
    (await adminPage.locator('[data-account-row="'+adminId+'"] [data-delete-account]').count())===0;
  observations.admin_peer_no_delete=
    (await adminPage.locator('[data-account-row="'+peerId+'"] [data-delete-account]').count())===0;
  const peerRefusal=await adminPage.evaluate(async (password)=>{
    const cookie=document.cookie.split('; ').find(c=>c.startsWith('csrftoken='));
    const revision=(document.querySelector('[data-account-row] [name=revision]')||{}).value||'';
    const body=new URLSearchParams({op:'delete',username:'p03r03b_browser_peer',
      confirm_username:'p03r03b_browser_peer',password:password,revision:revision});
    const response=await fetch('/users',{method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded',
               'X-CSRFToken':cookie?cookie.split('=')[1]:''},
      body:body.toString()});
    const html=await response.text();
    return {status:response.status,refused:html.includes('没有实例治理权限')};
  },adminPassword);
  observations.peer_refusal_status=peerRefusal.status;
  observations.peer_refused_and_alive=peerRefusal.refused===true
    &&(await adminPage.locator('[data-account-row="'+peerId+'"]').count())===1;

  // 5) The Admin deletes a plain account through the same red entry.
  const plainEntry=adminPage.locator('[data-account-row="'+plainId+'"] [data-delete-account]');
  expect(await plainEntry.count()).toBe(1);
  await attemptDelete(adminPage,plainId,'p03r03b_browser_plain',adminPassword);
  observations.admin_plain_deleted=(await adminPage.locator('[data-account-row="'+plainId+'"]').count())===0;
} finally {await browser.close();}
observations.page_errors=errors;
console.log('P03R03B_OBSERVATIONS '+JSON.stringify(observations));
'''
