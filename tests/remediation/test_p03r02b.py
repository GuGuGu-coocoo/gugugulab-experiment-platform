"""P03R02B evidence: v2 policy/principal schema, consistent-copy migration diff
and Owner-confirmed enablement.

The expected side of every check is the R00 contract (2026-09-23, sections A-C)
and explicit synthetic inputs; no expectation is derived from the implementation
under test. The suite covers:

- the additive schema extension: exact versions only (1 or 2), non-boolean state
  flags refused, an extension that keeps every v1 decision unchanged, a
  Principal per existing account, audit backfill and a NULL legacy creator;
- the Owner-bound enablement: read-only per-subject/study/action difference,
  unknown items that need an explicit choice, audit-proven revocations applied
  as explicit exceptions, password/revision/preview binding, invalidation of old
  invitations and previews, audit failure rollback and idempotent refusal;
- the real server entry (``/users`` preview/confirm ops) with no flag that can
  bypass the Owner password or the stored Owner pointer;
- the shared ``run_chrome_tokens`` helper: normal, non-zero, quiet and timeout
  exits all bounded, with only its own descriptors closed.
"""
import importlib
import importlib.util
import os
import re
import subprocess
import threading
import uuid
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.utils import timezone

from core import access, authorization, governance_migration
from core.models import (AccountInvitation, AccountProfile, Audit, Grant,
                         Instance, Invitation, PermissionPreview, Principal,
                         Study)
from core.protocol import Rejected

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = 'synthetic-p03r02b-owner-password'
STUDY_V2 = {
    'study.view', 'study.configure', 'build.upload', 'build.preview',
    'release.approve_pilot', 'recruitment.manage', 'data.export_raw',
    'identity_mapping.read', 'session.view', 'session.recover',
    'audit.view', 'study.delete',
}


def make_user(username, **kwargs):
    return get_user_model().objects.create_user(username, password=OWNER_PASSWORD, **kwargs)


def make_instance(owner, version=1):
    return Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                   authorization_version=version)


def make_study(title):
    return Study.objects.create(title=title)


def grant(user, study, *actions, delegable=False):
    for action in actions:
        Grant.objects.create(user=user, study=study, action=action, delegable=delegable)


def enablement_choices(diff):
    return {item['id']: item['choices'][0] for item in diff['unknown']}


def explicit_v2_choices(diff):
    """The Owner explicitly adopting the fixed v2 sets (Admin studies + future)."""
    choices = enablement_choices(diff)
    for item in diff['unknown']:
        if item['kind'] in ('admin_missing_permissions', 'admin_future_default'):
            choices[item['id']] = governance_migration.CHOICE_ADOPT_V2
    return choices


def preview_for(owner, instance, **overrides):
    diff = governance_migration.read_diff(instance=instance)
    choices = overrides.pop('choices', enablement_choices(diff))
    return governance_migration.preview_enablement(owner, choices), diff


def sign_in(client, user):
    """Test-client login that also satisfies the AccountGate auth_version check."""
    client.force_login(user)
    profile = AccountProfile.objects.filter(user_id=user.pk).first()
    session = client.session  # one bound store: the property returns a fresh one per access
    session['gep_auth_version'] = profile.auth_version if profile is not None else 1
    session.save()


# --- schema extension and exact versions -----------------------------------

def test_schema_defaults_exact_versions_and_v1_never_enlarges(db, evidence):
    owner = make_user('p03r02b_schema_owner')
    instance = make_instance(owner, version=1)
    AccountProfile.objects.create(user=owner, role='user')
    admin = make_user('p03r02b_schema_admin')
    AccountProfile.objects.create(user=admin, role='admin')
    study = make_study('P03R02B schema')
    grant(admin, study, 'study.view', 'data.export_raw')

    # Direct creation is conservative v1; only an explicit value changes it and
    # unknown values are unsupported, not a silent downgrade to v1.
    assert Instance._meta.get_field('authorization_version').default == 1
    assert access.authorization_version(instance) == 1
    assert governance_migration.instance_version(instance) == 1
    assert access.allowed(admin, study, 'data.export_raw') is True
    assert access.allowed(admin, study, 'build.upload') is False  # v1 never enlarges an Admin

    profile = AccountProfile.objects.get(user=admin)
    assert profile.policy_version == 2
    assert profile.platform_overrides == {} and profile.study_overrides == {}
    assert profile.future_study_actions is None
    assert Study.objects.get(pk=study.pk).creator_principal_id is None

    instance.authorization_version = 3
    instance.save(update_fields=['authorization_version'])
    assert access.authorization_version(instance) is None
    assert governance_migration.instance_version(instance) is None
    assert access.allowed(admin, study, 'data.export_raw') is False
    assert access.allowed(owner, study, 'study.view') is False
    assert access.resolve_study_actions(admin, study) == frozenset()
    assert access.resolve_platform_actions(admin) == frozenset()
    assert access.authority_actions(admin, study) == set()
    assert access.exact_policy_version('2') is None
    assert access.exact_policy_version(True) is None
    assert access.exact_policy_version(2) == 2 and access.exact_policy_version(1) == 1
    evidence('exact_versions.json', {
        'default_version': 1, 'stored_three': access.authorization_version(instance),
        'three_grants_nothing': True, 'policy_version_default': profile.policy_version,
        'creator_principal': None})


def test_migration_backfills_principals_and_audit_without_guessing_creator(db, evidence):
    User = get_user_model()
    users = [make_user('p03r02b_backfill_a'), make_user('p03r02b_backfill_b')]
    studies = [make_study('P03R02B backfill A'), make_study('P03R02B backfill B')]
    audits = [Audit.objects.create(actor=user, action='permission.matrix_changed', target=f'{user.pk}:{studies[0].pk}')
              for user in users]
    Audit.objects.create(actor=None, action='recovery.named_redeemed', target=str(studies[1].pk))

    migration = importlib.import_module('core.migrations.0010_policy_principal')
    migration.create_principals_and_backfill_audit(django_apps, None)
    migration.create_principals_and_backfill_audit(django_apps, None)  # idempotent

    principals = {principal.user_id: principal for principal in Principal.objects.all()}
    assert set(principals) == {user.pk for user in users}
    assert len(principals) == len(users)
    for audit in audits:
        audit.refresh_from_db()
        assert audit.actor_principal_id == principals[audit.actor_id].pk
    assert Audit.objects.filter(actor__isnull=True, actor_principal__isnull=True).count() == 1
    assert not Study.objects.exclude(creator_principal__isnull=True).exists()
    evidence('principal_backfill.json', {
        'principals': len(principals), 'backfilled_audits': len(audits),
        'device_audit_without_principal': 1, 'legacy_creator_null': True})


# --- Owner-bound preview and confirm ---------------------------------------

def _scenario():
    """One v1 instance with an Admin (matrix-revoked action + legacy action), a
    user grant set, two studies and old pending invitations/previews."""
    owner = make_user('p03r02b_owner')
    instance = make_instance(owner, version=1)
    AccountProfile.objects.create(user=owner, role='user')
    admin = make_user('p03r02b_admin')
    AccountProfile.objects.create(user=admin, role='admin')
    user = make_user('p03r02b_user')
    AccountProfile.objects.create(user=user, role='user')
    study_a = make_study('P03R02B study A')
    study_b = make_study('P03R02B study B')
    grant(admin, study_a, 'study.view', 'data.export_raw', 'member.manage', delegable=True)
    grant(user, study_a, 'study.view', 'build.preview')
    Audit.objects.create(actor=owner, action='permission.matrix_changed', target=f'{admin.pk}:{study_a.pk}',
                         before={'actions': {'data.export_raw': {'granted': True, 'delegable': True}}},
                         after={'actions': {'data.export_raw': {'granted': False, 'delegable': True}}})
    AccountInvitation.objects.create(issuer=owner, token_hash='a' * 64, username='p03r02b_pending',
                                     role='user', expires_at=timezone.now() + timezone.timedelta(hours=1))
    Invitation.objects.create(study=study_a, issuer=owner, token_hash='b' * 64, username='p03r02b_member',
                              actions=['study.view'], expires_at=timezone.now() + timezone.timedelta(hours=1))
    pending = PermissionPreview.objects.create(actor=owner, kind='matrix', scope='', summary={},
                                               errors=[], binding='c' * 64, base_revision=0, staged={},
                                               expires_at=timezone.now() + timezone.timedelta(minutes=5))
    return owner, instance, admin, user, study_a, study_b, pending


def test_read_only_diff_reports_added_removed_preserved_unknown_and_exceptions(db, evidence):
    owner, instance, admin, user, study_a, study_b, _ = _scenario()
    diff = governance_migration.read_diff(instance=instance)

    by_name = {subject['username']: subject for subject in diff['subjects']}
    admin_a = next(row for row in by_name['p03r02b_admin']['studies'] if row['study'] == str(study_a.pk))
    # P03R02BR: the read-only projection is conservative. It applies the
    # audit-proven export revocation but never assumes that the missing legacy
    # grants accept the v2 Admin default; those need an explicit Owner choice.
    assert set(admin_a['preserved']) == {'study.view'}
    assert set(admin_a['removed']) == {'member.manage', 'data.export_raw'}
    assert set(admin_a['added']) == set()
    admin_b = next(row for row in by_name['p03r02b_admin']['studies'] if row['study'] == str(study_b.pk))
    assert set(admin_b['added']) == set() and set(admin_b['removed']) == set()
    assert set(admin_b['preserved']) == set()  # no v1 grant on B and no assumed v2 default

    user_a = next(row for row in by_name['p03r02b_user']['studies'] if row['study'] == str(study_a.pk))
    assert user_a['added'] == [] and user_a['removed'] == []
    assert set(user_a['preserved']) == {'study.view', 'build.preview'}

    kinds = {(item['kind'], item['action']) for item in diff['unknown']}
    assert ('legacy_creator', None) in kinds
    assert ('legacy_action', 'member.manage') in kinds
    assert ('admin_missing_permissions', None) in kinds
    assert ('admin_future_default', None) in kinds
    assert len([item for item in diff['unknown'] if item['kind'] == 'legacy_creator']) == 2
    missing = {(item['user_id'], item['study']) for item in diff['unknown']
               if item['kind'] == 'admin_missing_permissions'}
    assert missing == {(admin.pk, str(study_a.pk)), (admin.pk, str(study_b.pk))}
    future = [item for item in diff['unknown'] if item['kind'] == 'admin_future_default']
    assert [item['user_id'] for item in future] == [admin.pk]
    assert future[0]['choices'] == ['keep_empty', 'adopt_v2']
    assert [item['action'] for item in diff['exceptions']] == ['data.export_raw']
    assert diff['exceptions'][0]['subject'] == 'p03r02b_admin'
    # P03R02C correction (P03R02BR review): the "before" column uses the *real*
    # v1 route. An Owner without any Grant could not view or manage these
    # studies either, so v2 access there is newly added and never "preserved";
    # the conservative default still adds nothing for the non-Owner accounts.
    assert diff['counts']['study_added'] == 24  # 2 studies x the 12 v2 actions for the grant-less Owner
    owner_rows = by_name['p03r02b_owner']['studies']
    assert all(row['legacy'] == [] and set(row['added']) == STUDY_V2 for row in owner_rows)

    # Platform permissions are mapped to their v1 counterparts instead of being
    # reported as a wholly new catalog (P03R02BR review, adjacent boundaries).
    owner_row = by_name['p03r02b_owner']
    assert owner_row['platform']['added'] == [] and owner_row['platform']['removed'] == []
    assert set(owner_row['platform']['preserved']) == set(access.PLATFORM_V2_ACTIONS)
    admin_row = by_name['p03r02b_admin']
    # P03R02C correction: a v1 Admin could reset/disable a peer Admin per target
    # under the study dominance rule. That conditional capability maps to
    # ``accounts.manage_admin`` and is reported as removed when the v2 default
    # does not carry it; the note records that it is not one-to-one.
    assert admin_row['platform']['added'] == ['study.create']
    assert admin_row['platform']['removed'] == ['accounts.manage_admin']
    assert set(admin_row['platform']['preserved']) == {
        'accounts.view', 'accounts.create_user', 'accounts.manage_user'}
    assert 'peer-Admin' in admin_row['platform_note']
    assert by_name['p03r02b_user']['platform']['added'] == ['study.create']

    # The read-only projection writes nothing: the version, the audit trail and
    # the pending invitations/previews are untouched.
    instance.refresh_from_db()
    assert instance.authorization_version == 1
    assert AccountInvitation.objects.filter(revoked=False).count() == 1
    assert AccountProfile.objects.get(user=admin).study_overrides == {}
    evidence('read_only_diff.json', {'counts': diff['counts'], 'unknown_kinds': sorted(kinds),
                                     'exceptions': diff['exceptions'], 'digest': diff['digest'],
                                     'admin_platform': admin_row['platform']})


def test_preview_requires_owner_and_exact_unknown_choices(db, evidence):
    owner, instance, admin, user, study_a, _, _ = _scenario()
    diff = governance_migration.read_diff(instance=instance)
    choices = enablement_choices(diff)

    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(admin, choices)
    assert info.value.code == 'owner_only'
    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(user, choices)
    assert info.value.code == 'owner_only'

    missing = dict(choices)
    removed_key = sorted(missing)[0]
    missing.pop(removed_key)
    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(owner, missing)
    assert info.value.code == 'unknown_choice_required'
    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(owner, {**choices, 'not-an-item': 'keep_null'})
    assert info.value.code == 'unknown_choice_unknown'
    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(owner, {**choices, removed_key: 'ignore_everything'})
    assert info.value.code == 'unknown_choice_invalid'

    row, _ = preview_for(owner, instance)
    assert row.kind == 'migration_enable'
    assert row.base_revision == instance.governance_revision
    assert row.staged['choices'] == choices
    assert row.binding and row.summary['counts']['unknown'] == len(choices)
    # No permission and no version moved by a preview.
    instance.refresh_from_db()
    assert instance.authorization_version == 1
    assert AccountProfile.objects.get(user=admin).study_overrides == {}
    evidence('owner_bound_preview.json', {'owner_only': True, 'unknown_items': len(choices),
                                          'preview_kind': row.kind, 'digest': row.summary['digest']})


def test_confirm_reauth_revision_binding_and_repeat_refusal(db, evidence):
    owner, instance, admin, user, study_a, _, pending = _scenario()
    row, diff = preview_for(owner, instance)

    with pytest.raises(Rejected) as info:
        governance_migration.confirm_enablement(owner, 'wrong-password', instance.governance_revision, row.pk)
    assert info.value.code == 'reauth_failed'
    with pytest.raises(Rejected) as info:
        governance_migration.confirm_enablement(owner, OWNER_PASSWORD, instance.governance_revision + 1, row.pk)
    assert info.value.code == 'revision_conflict'
    with pytest.raises(Rejected) as info:  # another actor cannot use the Owner's preview
        governance_migration.confirm_enablement(admin, OWNER_PASSWORD, instance.governance_revision, row.pk)
    assert info.value.code == 'owner_only'

    # A concurrent change after the preview refuses the commit (no partial write).
    Grant.objects.create(user=user, study=study_a, action='session.view')
    with pytest.raises(Rejected) as info:
        governance_migration.confirm_enablement(owner, OWNER_PASSWORD, instance.governance_revision, row.pk)
    assert info.value.code == 'preview_stale'
    instance.refresh_from_db()
    assert instance.authorization_version == 1
    Grant.objects.filter(user=user, study=study_a, action='session.view').delete()

    # An expired preview is refused before any write.
    PermissionPreview.objects.filter(pk=row.pk).update(expires_at=timezone.now() - timezone.timedelta(minutes=1))
    with pytest.raises(Rejected) as info:
        governance_migration.confirm_enablement(owner, OWNER_PASSWORD, instance.governance_revision, row.pk)
    assert info.value.code == 'preview_expired'

    # A fresh preview commits exactly once and the repeat never resets anything.
    # The Owner explicitly adopts the fixed v2 sets here (the read-only default
    # would keep the legacy set), so the committed strategy is the adopted one.
    diff = governance_migration.read_diff(instance=instance)
    fresh, _ = preview_for(owner, instance, choices=explicit_v2_choices(diff))
    result = governance_migration.confirm_enablement(owner, OWNER_PASSWORD, instance.governance_revision, fresh.pk)
    instance.refresh_from_db()
    admin_profile = AccountProfile.objects.get(user=admin)
    frozen = admin_profile.study_overrides[str(study_a.pk)]
    assert instance.authorization_version == 2
    assert instance.governance_revision == 1
    assert result['invalidated']['account_invitations_revoked'] == 1
    assert result['invalidated']['study_invitations_revoked'] == 1
    assert result['invalidated']['previews_invalidated'] >= 1  # the pending matrix preview
    assert AccountInvitation.objects.filter(revoked=False).count() == 0
    assert Invitation.objects.filter(revoked=False).count() == 0
    pending.refresh_from_db()
    assert pending.consumed is False and pending.staged is None  # expired, no staged intent
    # The audit-proven revocation stays out of the explicitly adopted v2 set.
    assert 'data.export_raw' not in frozen and 'study.view' in frozen and 'member.manage' not in frozen
    assert set(frozen) == set(STUDY_V2) - {'data.export_raw'}
    assert Audit.objects.filter(action='policy.v2_enabled').count() == 1
    assert Audit.objects.filter(action='policy.v2_override_frozen').count() == 2  # A and B choices
    policy = access.canonical_policy(admin, instance=instance)
    assert access.allowed(admin, study_a, 'build.upload', version=2, policy=policy) is True
    assert access.allowed(admin, study_a, 'data.export_raw', version=2, policy=policy) is False
    assert access.allowed(admin, study_a, 'member.manage', version=2, policy=policy) is False

    with pytest.raises(Rejected) as info:
        governance_migration.confirm_enablement(owner, OWNER_PASSWORD, instance.governance_revision, fresh.pk)
    assert info.value.code == 'already_enabled'
    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(owner, {})
    assert info.value.code == 'already_enabled'
    admin_profile.refresh_from_db()
    assert admin_profile.study_overrides[str(study_a.pk)] == frozen  # exceptions are not reset
    evidence('owner_confirm.json', {'version': instance.authorization_version,
                                    'revision': instance.governance_revision,
                                    'frozen_without_export': 'data.export_raw' not in frozen,
                                    'repeat_refused': 'already_enabled',
                                    'exceptions_preserved': True,
                                    'audit_rows': {'enabled': 1, 'override_frozen': 2}})


def test_audit_failure_rolls_back_everything(db, evidence, monkeypatch):
    owner, instance, admin, user, study_a, _, _ = _scenario()
    row, _ = preview_for(owner, instance)

    def boom(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')

    monkeypatch.setattr(governance_migration, '_audit', boom)
    with pytest.raises(RuntimeError):
        governance_migration.confirm_enablement(owner, OWNER_PASSWORD, instance.governance_revision, row.pk)

    instance.refresh_from_db()
    row.refresh_from_db()
    assert instance.authorization_version == 1 and instance.governance_revision == 0
    assert row.consumed is False and row.staged is not None
    assert AccountInvitation.objects.filter(revoked=False).count() == 1
    assert Invitation.objects.filter(revoked=False).count() == 1
    assert AccountProfile.objects.get(user=admin).study_overrides == {}
    assert Audit.objects.filter(action='policy.v2_enabled').count() == 0
    evidence('audit_rollback.json', {'version': instance.authorization_version, 'revision': instance.governance_revision,
                                     'invitations_valid': 2, 'preview_reusable': True,
                                     'exception_overrides': 0})


# --- stored policy data fails closed ---------------------------------------

def test_canonical_policy_never_accepts_caller_flags_or_malformed_data(db, evidence):
    owner = make_user('p03r02b_policy_owner')
    instance = make_instance(owner, version=1)
    AccountProfile.objects.create(user=owner, role='user')
    admin = make_user('p03r02b_policy_admin')
    profile = AccountProfile.objects.create(user=admin, role='admin',
                                            platform_overrides={'accounts.delete_admin': True})
    study = make_study('P03R02B policy')
    grant(admin, study, 'study.view')

    policy = access.canonical_policy(admin, instance=instance)
    assert policy.is_instance_owner is False  # Owner comes only from Instance.owner
    assert authorization.allows_platform(policy, 'accounts.delete_admin') is True  # stored override is real

    for field in ('is_instance_owner', 'authenticated', 'active', 'must_change_password', 'deleted'):
        for bad in ('yes', 1, 0, None, []):
            with pytest.raises(authorization.PolicyError) as info:
                authorization.SubjectPolicy('admin', **{field: bad})
            assert info.value.code == 'invalid_state_flag'

    AccountProfile.objects.filter(pk=profile.pk).update(policy_version=3)
    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(admin, instance=instance)
    assert info.value.code == 'unsupported_policy_version'
    assert governance_migration.page_state()['state'] == 'invalid'
    with pytest.raises(authorization.PolicyError):
        governance_migration.diff_payload()
    with pytest.raises(authorization.PolicyError):
        governance_migration.preview_enablement(owner, {})

    AccountProfile.objects.filter(pk=profile.pk).update(policy_version=2, role='owner')
    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(admin, instance=instance)
    assert info.value.code == 'unknown_role'
    AccountProfile.objects.filter(pk=profile.pk).update(role='admin',
                                                       platform_overrides={'accounts.superuser': True})
    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(admin, instance=instance)
    assert info.value.code == 'unknown_action'
    AccountProfile.objects.filter(pk=profile.pk).update(
        platform_overrides={}, study_overrides={str(study.pk): ['build.publish']})
    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(admin, instance=instance)
    assert info.value.code == 'unknown_action'
    AccountProfile.objects.filter(pk=profile.pk).update(study_overrides={}, future_study_actions=['study.*'])
    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(admin, instance=instance)
    assert info.value.code == 'unknown_action'

    # A deleted stable principal denies everything even if the user row is live.
    AccountProfile.objects.filter(pk=profile.pk).update(future_study_actions=None)
    Principal.objects.create(user=admin, deleted_at=timezone.now())
    deleted = access.canonical_policy(admin, instance=instance)
    assert deleted.deleted is True
    assert authorization.study_actions(deleted, str(study.pk)) == frozenset()
    evidence('policy_fail_closed.json', {'non_bool_flags': 'invalid_state_flag', 'policy_version_three': True,
                                         'unknown_role': True, 'unknown_action': True,
                                         'deleted_principal_denied': True})


# --- real server entry -----------------------------------------------------

def test_server_owner_entry_preview_confirm_and_no_flag_bypass(db, client, evidence):
    owner, instance, admin, user, study_a, _, _ = _scenario()
    sign_in(client, owner)
    page = client.get('/users')
    assert page.status_code == 200
    html = page.content.decode()
    assert 'data-policy-migration="available"' in html
    assert 'data-policy-diff' not in html  # the full difference is on demand only

    # The read-only difference is an explicit request and writes nothing.
    diff_page = client.post('/users', {'op': 'migration_diff', 'revision': str(instance.governance_revision)})
    assert diff_page.status_code == 200
    diff_html = diff_page.content.decode()
    assert 'data-policy-diff="1"' in diff_html and 'data-migration-unknown=' in diff_html
    assert 'data-migration-unknown-select=' in diff_html and '<option value="" selected>' in diff_html
    instance.refresh_from_db()
    assert instance.authorization_version == 1

    # An ordinary Admin never gets the enablement section or the controls.
    sign_in(client, admin)
    admin_html = client.get('/users').content.decode()
    assert 'data-policy-migration' not in admin_html
    assert client.post('/users', {'op': 'migration_diff', 'revision': '0'}).status_code == 403

    sign_in(client, owner)
    diff = governance_migration.read_diff(instance=instance)
    data = {'op': 'migration_preview', 'revision': str(instance.governance_revision)}
    for item in diff['unknown']:
        data['unknown:' + item['id']] = item['choices'][0]
    response = client.post('/users', data)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'data-preview="migration_enable"' in html
    assert 'data-migration-final-strategy="1"' in html and 'data-final-subject=' in html
    # The conservative choices request no expansion, so the final strategy adds
    # no study action for the legacy Admin.
    admin_row = re.search(r'data-final-subject="%d"[^>]*data-final-study-added="(\d+)"' % admin.pk, html)
    assert admin_row is not None and admin_row.group(1) == '0'
    preview_id = re.search(r'data-preview-id="([0-9a-f-]{36})"', html).group(1)
    assert 'name="op" value="migration_confirm"' in html
    instance.refresh_from_db()
    assert instance.authorization_version == 1

    # An ordinary Admin cannot confirm even with the right password and forged
    # owner-ish flags: the Owner comes only from Instance.owner.
    sign_in(client, admin)
    refused_confirm = client.post('/users', {'op': 'migration_confirm', 'preview_id': preview_id,
                                            'revision': str(instance.governance_revision),
                                            'password': OWNER_PASSWORD, 'owner': '1',
                                            'is_instance_owner': '1'})
    assert refused_confirm.status_code == 403
    instance.refresh_from_db()
    assert instance.authorization_version == 1

    sign_in(client, owner)
    # A wrong password or a stale revision writes nothing.
    bad = client.post('/users', {'op': 'migration_confirm', 'preview_id': preview_id,
                                 'revision': str(instance.governance_revision), 'password': 'nope'})
    assert bad.status_code == 403
    assert '重新认证失败' in bad.content.decode()
    instance.refresh_from_db()
    assert instance.authorization_version == 1

    # The Owner confirms once; the preview identity alone is never enough.
    confirmed = client.post('/users', {'op': 'migration_confirm', 'preview_id': preview_id,
                                       'revision': str(instance.governance_revision),
                                       'password': OWNER_PASSWORD})
    assert confirmed.status_code == 200
    instance.refresh_from_db()
    assert instance.authorization_version == 2
    assert Audit.objects.filter(action='policy.v2_enabled').count() == 1

    sign_in(client, admin)
    refused = client.post('/users', {'op': 'migration_preview', 'revision': '0'})
    assert refused.status_code == 403
    owner.refresh_from_db()
    assert access.is_instance_owner(owner) is True
    evidence('server_entry.json', {'preview_kind': 'migration_enable', 'wrong_password_status': 403,
                                   'confirmed_version': instance.authorization_version,
                                   'admin_preview_status': 403, 'flag_keys_ignored': ['owner', 'is_instance_owner']})


# --- the shared helper must be bounded and leak-free -----------------------

def _bounded(callable_, seconds=30):
    box = {}

    def target():
        try:
            box['value'] = callable_()
        except BaseException as error:  # noqa: BLE001 - recorded and re-raised below
            box['error'] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), 'helper did not return within the outer bound'
    if 'error' in box:
        raise box['error']
    return box.get('value')


WRITE_TOKEN = "import fs from 'node:fs'; fs.writeSync(Number(process.env.GEP_TOKEN_FD), 'tok-abc\\n');"
QUIET_EXIT = 'process.exit(0);'


def test_run_chrome_tokens_normal_nonzero_quiet_and_timeout_are_bounded(evidence, run_chrome_tokens):
    env = dict(os.environ)
    result, tokens = _bounded(lambda: run_chrome_tokens(WRITE_TOKEN, env, timeout=60))
    assert result.returncode == 0 and tokens == ['tok-abc']
    assert 'tok-abc' not in result.stdout and 'tok-abc' not in result.stderr

    nonzero = _bounded(lambda: run_chrome_tokens("import fs from 'node:fs'; "
                                                 "fs.writeSync(Number(process.env.GEP_TOKEN_FD), 'tok-n\\n'); "
                                                 "process.exit(3);", env, timeout=60))
    assert nonzero[0].returncode == 3 and nonzero[1] == ['tok-n']

    quiet = _bounded(lambda: run_chrome_tokens(QUIET_EXIT, env, timeout=60))
    assert quiet[0].returncode == 0 and quiet[1] == []

    def hanging():
        try:
            run_chrome_tokens('setTimeout(() => {}, 30000);', env, timeout=1)
        except subprocess.TimeoutExpired:
            return 'timeout'
        return 'returned'

    assert _bounded(hanging, seconds=30) == 'timeout'

    before = len(os.listdir('/dev/fd')) if os.path.isdir('/dev/fd') else None
    for _ in range(10):
        _bounded(lambda: run_chrome_tokens(QUIET_EXIT, env, timeout=60))
    after = len(os.listdir('/dev/fd')) if os.path.isdir('/dev/fd') else None
    if before is not None:
        assert after <= before + 2, (before, after)
    evidence('run_chrome_tokens.json', {'normal': tokens, 'nonzero': nonzero[0].returncode,
                                        'quiet': quiet[1], 'timeout': 'bounded',
                                        'descriptors_before': before, 'descriptors_after': after})


# --- the migration tool refuses to overwrite or escape ---------------------

def test_migration_tool_root_and_path_safety(evidence_root, evidence):
    spec = importlib.util.spec_from_file_location('remediation_migration',
                                                  REPO_ROOT / 'tools' / 'remediation_migration.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    base = evidence_root / 'roots'
    first = tool.new_unique_root(base)
    second = tool.new_unique_root(base)
    assert first != second and first.is_dir() and second.is_dir()
    assert re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{8}', first.name)
    with pytest.raises(ValueError):
        tool.resolve_within(base, '../escape')
    with pytest.raises(ValueError):
        tool.resolve_within(base, '/etc/passwd')
    inside = tool.resolve_within(base, 'nested/file.json')
    assert base in inside.parents
    evidence('tool_safety.json', {'unique_roots': [first.name, second.name],
                                  'escape_refused': True, 'absolute_refused': True})
