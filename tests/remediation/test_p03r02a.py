"""P03R02A evidence: the v2 finite authorization kernel and its single resolver.

The expected side of every check is the fixed v2 catalog and role default from
the R00 contract (2026-09-23, section A) plus explicit synthetic inputs; no
expectation is derived from the implementation under test.

Server-side checks prove:

- the fixed Owner / Admin / user / restricted-Admin table across studies A and
  B and a study with no explicit override (the "future" default);
- unknown actions and wildcards are never granted, and submitted or stored
  selections are validated (invisible sub-actions are refused on submission and
  fail closed when stored);
- a complete override wins over grants, a grant set that loses an action never
  regains it, and the kernel has no creator / legacy delegation input;
- a study is editable only with the actor's own ``study.view`` and
  ``study.configure``, a read-only Admin can neither delegate nor reset higher
  privilege targets on A, a legal difference within B is allowed, and an edit
  on B cannot silently overwrite the target's A permission;
- a whole-account takeover dominates the target's stored platform and study
  permissions before and after the change, including the future default and a
  user's ``study.create``, and Owner-controlled switches never move through an
  Admin;
- Owner comes only from ``Instance.owner`` (never a role string, staff or
  superuser flag), the legacy instance stays at version 1 without expanding,
  and exactly one server module (``access.py``) reaches the v2 kernel.
"""
import dataclasses
import re
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from core import access, authorization
from core.models import AccountProfile, Grant, Instance, Study

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = 'synthetic-p03r02a-owner-password'

STUDY_V2 = {
    'study.view', 'study.configure', 'build.upload', 'build.preview',
    'release.approve_pilot', 'recruitment.manage', 'data.export_raw',
    'identity_mapping.read', 'session.view', 'session.recover',
    'audit.view', 'study.delete',
}
PLATFORM_V2 = {
    'study.create', 'accounts.view', 'accounts.create_user',
    'accounts.manage_user', 'accounts.create_admin', 'accounts.manage_admin',
    'accounts.delete_admin',
}
USER_PLATFORM = {'study.create'}
ADMIN_PLATFORM = {'study.create', 'accounts.view', 'accounts.create_user', 'accounts.manage_user'}
OWNER_SWITCHES = {'accounts.create_admin', 'accounts.manage_admin', 'accounts.delete_admin'}


def make_user(username, **kwargs):
    return get_user_model().objects.create_user(username, password=OWNER_PASSWORD, **kwargs)


def test_fixed_v2_catalog_and_role_defaults():
    assert set(authorization.STUDY_ACTIONS) == STUDY_V2
    assert set(authorization.PLATFORM_ACTIONS) == PLATFORM_V2
    assert set(authorization.ROLE_PLATFORM_DEFAULTS['user']) == USER_PLATFORM
    assert set(authorization.ROLE_PLATFORM_DEFAULTS['admin']) == ADMIN_PLATFORM
    assert set(authorization.ROLE_STUDY_TEMPLATE['user']) == set()
    assert set(authorization.ROLE_STUDY_TEMPLATE['admin']) == STUDY_V2
    assert set(authorization.OWNER_PLATFORM_SWITCHES) == OWNER_SWITCHES
    assert set(authorization.LEGACY_ONLY_ACTIONS) == {'member.manage', 'permission.delegate'}
    assert not set(authorization.LEGACY_ONLY_ACTIONS) & STUDY_V2


def test_account_state_gate_denies_everything():
    denied_states = ({'authenticated': False}, {'active': False},
                     {'must_change_password': True}, {'deleted': True})
    for state in denied_states:
        policy = authorization.SubjectPolicy('admin', **state)
        assert authorization.platform_actions(policy) == frozenset(), state
        assert authorization.study_actions(policy, str(uuid.uuid4())) == frozenset(), state
        assert authorization.default_study_actions(policy) == frozenset(), state
    owner = authorization.SubjectPolicy('admin', is_instance_owner=True, active=False)
    assert authorization.platform_actions(owner) == frozenset()


def test_fixed_owner_admin_user_restricted_admin_table(db, evidence):
    owner = make_user('p03r02a_owner')
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    admin = make_user('p03r02a_admin')
    AccountProfile.objects.create(user=admin, role='admin')
    user = make_user('p03r02a_user')
    restricted = make_user('p03r02a_restricted')
    AccountProfile.objects.create(user=restricted, role='admin')
    study_a = Study.objects.create(title='P03R02A A')
    study_b = Study.objects.create(title='P03R02A B')
    a, b = str(study_a.id), str(study_b.id)
    future = str(uuid.uuid4())

    rows = [
        ('owner', owner, authorization.SubjectPolicy('admin', is_instance_owner=True),
         PLATFORM_V2, STUDY_V2, STUDY_V2, STUDY_V2),
        ('admin', admin, authorization.SubjectPolicy('admin'),
         ADMIN_PLATFORM, STUDY_V2, STUDY_V2, STUDY_V2),
        ('user', user,
         authorization.SubjectPolicy('user', grants={a: ['study.view', 'data.export_raw']}),
         USER_PLATFORM, {'study.view', 'data.export_raw'}, set(), set()),
        ('restricted admin', restricted,
         authorization.SubjectPolicy(
             'admin', platform_overrides={'accounts.view': False},
             study_overrides={a: [], b: ['study.view', 'study.configure']},
             future_study_actions=['study.view']),
         ADMIN_PLATFORM - {'accounts.view'}, set(), {'study.view', 'study.configure'}, {'study.view'}),
    ]

    observed = []
    for name, account, policy, platform, actions_a, actions_b, actions_future in rows:
        assert set(authorization.platform_actions(policy)) == platform, name
        assert set(authorization.study_actions(policy, a)) == actions_a, name
        assert set(authorization.study_actions(policy, b)) == actions_b, name
        assert set(authorization.study_actions(policy, future)) == actions_future, name
        # The single resolver returns the same answer for an enabled v2 instance.
        assert set(access.resolve_platform_actions(account, version=2, policy=policy)) == platform, name
        assert set(access.resolve_study_actions(account, study_a, version=2, policy=policy)) == actions_a, name
        assert set(access.resolve_study_actions(account, study_b, version=2, policy=policy)) == actions_b, name
        observed.append({'subject': name, 'platform': sorted(platform), 'study_a': sorted(actions_a),
                         'study_b': sorted(actions_b), 'future_study': sorted(actions_future)})

    # Visibility gates every study source in v2.
    no_view = authorization.SubjectPolicy('user', grants={b: ['data.export_raw']})
    assert authorization.study_actions(no_view, b) == frozenset()
    assert access.allowed(user, study_b, 'data.export_raw', version=2, policy=no_view) is False
    view_only = authorization.SubjectPolicy('user', grants={a: ['study.view']})
    assert access.allowed(user, study_a, 'study.view', version=2, policy=view_only) is True
    assert access.allowed(user, study_a, 'data.export_raw', version=2, policy=view_only) is False
    assert access.allowed(user, study_a, 'member.manage', version=2, policy=view_only) is False
    evidence('effective_table.json', {'rows': observed, 'view_prerequisite': True})


def test_unknown_actions_and_selection_validation_are_refused():
    study = str(uuid.uuid4())
    known = authorization.SubjectPolicy('admin')
    for action in ('member.manage', 'permission.delegate', 'build.publish', 'study.*', '*'):
        assert authorization.allows(known, study, action) is False, action
        assert authorization.allows_platform(known, action) is False, action

    for bad in ({study: ['build.publish']}, {study: ['*']}):
        with pytest.raises(authorization.PolicyError) as info:
            authorization.SubjectPolicy('admin', study_overrides=bad)
        assert info.value.code == 'unknown_action'
    with pytest.raises(authorization.PolicyError) as info:
        authorization.SubjectPolicy('admin', platform_overrides={'accounts.superuser': True})
    assert info.value.code == 'unknown_action'
    with pytest.raises(authorization.PolicyError) as info:
        authorization.SubjectPolicy('admin', platform_overrides={'accounts.view': 'yes'})
    assert info.value.code == 'invalid_override_value'
    with pytest.raises(authorization.PolicyError) as info:
        authorization.SubjectPolicy('admin', future_study_actions=['study.configure'])
    assert info.value.code == 'invisible_subaction'
    with pytest.raises(authorization.PolicyError) as info:
        authorization.SubjectPolicy('admin', study_overrides={'not-a-uuid': ['study.view']})
    assert info.value.code == 'invalid_study'
    for bad in ('study.view', 7, [42], {study: {'study.view': True}}):
        with pytest.raises(authorization.PolicyError):
            authorization.SubjectPolicy('admin', study_overrides={study: bad})

    # Submission validation: hiding a study sends an explicit empty list, and
    # reviving it sends study.view alone; sub-actions without view are refused.
    assert authorization.validate_selection([]) == frozenset()
    assert authorization.validate_selection(['study.view']) == {'study.view'}
    with pytest.raises(authorization.PolicyError) as info:
        authorization.validate_selection(['study.configure'])
    assert info.value.code == 'invisible_subaction'
    # A stored contradictory list is not trusted: the computation fails closed.
    stored = authorization.SubjectPolicy('admin', study_overrides={study: ['study.configure']})
    assert authorization.study_actions(stored, study) == frozenset()


def test_complete_override_and_revocation_are_never_restored(db, evidence):
    user = make_user('p03r02a_history')
    study = Study.objects.create(title='P03R02A history')
    key = str(study.id)
    full = sorted(STUDY_V2)
    for action in full:
        Grant.objects.create(user=user, study=study, action=action)

    def grants_from_rows():
        return {key: [row.action for row in Grant.objects.filter(user=user, study=study)]}

    policy = authorization.SubjectPolicy('user', grants=grants_from_rows())
    assert set(authorization.study_actions(policy, key)) == STUDY_V2

    # The Owner revokes configuration; re-resolving never re-grants it from a
    # creator fact or a legacy delegable column.
    Grant.objects.filter(user=user, study=study, action='study.configure').delete()
    revoked = authorization.SubjectPolicy('user', grants=grants_from_rows())
    assert set(authorization.study_actions(revoked, key)) == STUDY_V2 - {'study.configure'}
    assert access.allowed(user, study, 'study.configure', version=2, policy=revoked) is False
    assert access.allowed(user, study, 'study.view', version=2, policy=revoked) is True

    # A complete override wins over grants and over the fixed Admin template.
    override = authorization.SubjectPolicy(
        'admin', study_overrides={key: ['study.view', 'build.upload']},
        grants={key: ['study.view', 'data.export_raw']})
    assert set(authorization.study_actions(override, key)) == {'study.view', 'build.upload'}
    assert authorization.allows(override, key, 'data.export_raw') is False

    # Reviving visibility sends only view and never revives old sub-actions.
    revived = authorization.SubjectPolicy(
        'admin', study_overrides={key: authorization.validate_selection(['study.view'])})
    assert set(authorization.study_actions(revived, key)) == {'study.view'}

    names = {item.name for item in dataclasses.fields(authorization.SubjectPolicy)}
    assert {'creator', 'creator_principal', 'delegable', 'member.manage', 'permission.delegate'} & names == set()
    evidence('override_and_revocation.json', {
        'full': sorted(STUDY_V2), 'after_revocation': sorted(STUDY_V2 - {'study.configure'}),
        'override_result': ['study.view', 'build.upload'], 'revived_result': ['study.view'],
        'kernel_inputs': sorted(names)})


def test_takeover_dominates_before_after_and_future_defaults(evidence):
    a = str(uuid.uuid4())
    restricted = authorization.SubjectPolicy(
        'admin', future_study_actions=['study.view', 'study.configure'],
        study_overrides={a: ['study.view', 'study.configure']})
    bounded_target = authorization.SubjectPolicy(
        'admin', future_study_actions=['study.view', 'study.configure'],
        study_overrides={a: ['study.view', 'study.configure']})
    assert authorization.can_take_over(restricted, bounded_target, bounded_target, [a]) is True

    # The same per-study A result, but the target's override-less default is the
    # full Admin template: the future default alone blocks the takeover.
    full_default = authorization.SubjectPolicy('admin',
                                               study_overrides={a: ['study.view', 'study.configure']})
    assert set(authorization.configured_study_actions(full_default, a)) == {'study.view', 'study.configure'}
    assert authorization.can_take_over(restricted, full_default, full_default, [a]) is False

    # Role change: promotion is allowed only while the after-state future
    # default also stays inside the actor's bound.
    plain_user = authorization.SubjectPolicy('user')
    promoted_bounded = authorization.SubjectPolicy('admin', future_study_actions=['study.view', 'study.configure'])
    promoted_full = authorization.SubjectPolicy('admin')
    assert authorization.can_take_over(restricted, plain_user, promoted_bounded, []) is True
    assert authorization.can_take_over(restricted, plain_user, promoted_full, []) is False

    # Platform permissions count too, including a user's study.create.
    no_create = authorization.SubjectPolicy('admin', platform_overrides={'study.create': False})
    assert authorization.can_take_over(no_create, plain_user, plain_user, []) is False
    assert authorization.can_take_over(
        authorization.SubjectPolicy('admin', is_instance_owner=True), plain_user, plain_user, []) is True

    # A disabled account may use nothing, but its stored permissions still
    # count: comparing the ineffective (empty) sets would wrongly pass.
    weak = authorization.SubjectPolicy(
        'admin', future_study_actions=['study.view', 'study.configure'],
        study_overrides={a: ['study.view', 'study.configure']})
    inactive_high = authorization.SubjectPolicy(
        'admin', active=False, future_study_actions=['study.view', 'study.configure'],
        study_overrides={a: ['study.view', 'study.configure', 'data.export_raw']})
    assert authorization.study_actions(inactive_high, a) == frozenset()
    assert set(authorization.configured_study_actions(inactive_high, a)) == {
        'study.view', 'study.configure', 'data.export_raw'}
    assert authorization.can_take_over(weak, inactive_high, inactive_high, [a]) is False
    inactive_bounded = authorization.SubjectPolicy(
        'admin', active=False, future_study_actions=['study.view', 'study.configure'])
    assert authorization.can_take_over(weak, inactive_bounded, inactive_bounded, []) is True
    evidence('takeover_future_defaults.json', {
        'bounded_takeover': True, 'full_default_blocked': True, 'promoted_bounded': True,
        'promoted_full_blocked': True, 'user_study_create_counted': True,
        'inactive_stored_permissions_counted': True})


def test_read_only_admin_cannot_delegate_or_reset_and_b_difference_is_allowed(evidence):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    actor = authorization.SubjectPolicy('admin', study_overrides={
        a: ['study.view', 'data.export_raw'],
        b: ['study.view', 'study.configure', 'build.upload', 'build.preview'],
    })
    assert authorization.can_manage_study(actor, a) is False
    assert authorization.can_manage_study(actor, b) is True
    assert authorization.assignable_study_actions(actor, a) == frozenset()
    assert set(authorization.assignable_study_actions(actor, b)) == {
        'study.view', 'study.configure', 'build.upload', 'build.preview'}

    # Cannot grant a user configure on A, and cannot reset a target holding A
    # configure (or any A action) to loop back around that boundary.
    target_configure = authorization.SubjectPolicy('user', grants={a: ['study.view', 'study.configure']})
    target_view_export = authorization.SubjectPolicy('user', grants={a: ['study.view', 'data.export_raw']})
    assert authorization.can_take_over(actor, target_configure, target_configure, [a]) is False
    assert authorization.can_take_over(actor, target_view_export, target_view_export, [a]) is False

    # B legal difference within the actor's range is allowed; an edit on B must
    # not silently overwrite the target's A permission.
    target_b_before = authorization.SubjectPolicy('user', grants={b: ['study.view', 'build.upload']})
    target_b_after = authorization.SubjectPolicy('user', grants={b: ['study.view', 'build.preview']})
    assert authorization.can_take_over(actor, target_b_before, target_b_after, [b]) is True
    mixed = authorization.SubjectPolicy('user', grants={
        a: ['study.view', 'study.configure'], b: ['study.view']})
    assert authorization.can_take_over(actor, mixed, target_b_after, [a, b]) is False
    evidence('read_only_admin.json', {
        'assignable_a': [], 'assignable_b': sorted(authorization.assignable_study_actions(actor, b)),
        'reset_configure_target': False, 'reset_view_export_target': False,
        'b_difference_allowed': True, 'cross_study_overwrite_refused': True})


def test_admin_cannot_transfer_owner_controlled_switches():
    admin = authorization.SubjectPolicy('admin', platform_overrides={
        'accounts.manage_admin': True, 'accounts.delete_admin': True})
    assert {'accounts.manage_admin', 'accounts.delete_admin'} <= set(authorization.platform_actions(admin))
    assert authorization.OWNER_PLATFORM_SWITCHES & authorization.assignable_platform_actions(admin) == frozenset()
    target_user = authorization.SubjectPolicy('user')
    granted = authorization.SubjectPolicy('user', platform_overrides={'accounts.manage_admin': True})
    assert authorization.can_take_over(admin, target_user, granted, []) is False
    assert authorization.can_take_over(admin, granted, target_user, []) is False
    owner = authorization.SubjectPolicy('admin', is_instance_owner=True)
    assert authorization.can_take_over(owner, target_user, granted, []) is True
    assert set(authorization.assignable_platform_actions(owner)) == PLATFORM_V2


def test_owner_is_only_instance_owner_and_never_a_role(db, evidence):
    with pytest.raises(authorization.PolicyError) as info:
        authorization.SubjectPolicy('owner')
    assert info.value.code == 'unknown_role'
    owner = make_user('p03r02a_true_owner')
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    staff = make_user('p03r02a_staff', is_staff=True, is_superuser=True)
    AccountProfile.objects.create(user=staff, role='admin')
    assert access.is_instance_owner(owner) is True
    assert access.is_instance_owner(staff) is False
    assert access.is_account_administrator(staff) is True
    default_admin = authorization.SubjectPolicy('admin')
    assert set(authorization.platform_actions(default_admin)) == ADMIN_PLATFORM
    assert set(authorization.platform_actions(default_admin)) & OWNER_SWITCHES == set()
    evidence('owner_forgery.json', {
        'role_owner_rejected': 'unknown_role', 'true_owner': access.is_instance_owner(owner),
        'staff_superuser_owner': access.is_instance_owner(staff),
        'default_admin_switches': sorted(set(authorization.platform_actions(default_admin)) & OWNER_SWITCHES)})


def test_legacy_instance_does_not_expand_and_the_kernel_has_one_entry(db, evidence):
    owner = make_user('p03r02a_legacy_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    admin = make_user('p03r02a_legacy_admin')
    AccountProfile.objects.create(user=admin, role='admin')
    user = make_user('p03r02a_legacy_user')
    study = Study.objects.create(title='P03R02A legacy')
    assert access.authorization_version(instance) == 1
    for account in (owner, admin, user):
        assert access.allowed(account, study, 'study.view') is False
        assert access.resolve_study_actions(account, study) == frozenset()
    assert access.authority_actions(admin, study) == set()

    # An injected v2 policy changes nothing while the stored marker is v1.
    v2_owner = authorization.SubjectPolicy('admin', is_instance_owner=True)
    assert access.allowed(owner, study, 'study.view', policy=v2_owner) is False
    assert access.resolve_study_actions(owner, study, policy=v2_owner) == frozenset()
    assert access.resolve_platform_actions(owner, policy=v2_owner) == frozenset()
    # An explicit v2 route needs the canonical policy; without one it fails closed.
    assert access.allowed(owner, study, 'study.view', version=2, policy=v2_owner) is True
    assert access.allowed(owner, study, 'study.view', version=2) is False
    assert access.resolve_study_actions(owner, study, version=2) == frozenset()

    # v2 never reads the legacy delegable column to let anything through, while
    # the same row still decides the legacy path.
    Grant.objects.create(user=admin, study=study, action='study.view', delegable=True)
    hidden = authorization.SubjectPolicy('admin', study_overrides={str(study.id): []})
    assert access.allowed(admin, study, 'study.view', version=2, policy=hidden) is False
    assert access.allowed(admin, study, 'study.view') is True

    importers = []
    for path in sorted((REPO_ROOT / 'server').rglob('*.py')):
        text = path.read_text(encoding='utf-8')
        if re.search(r'(?m)^\s*(?:from|import)\b[^\n]*\bauthorization\b', text):
            importers.append(str(path.relative_to(REPO_ROOT)))
    assert importers == ['server/core/access.py']
    kernel = (REPO_ROOT / 'server' / 'core' / 'authorization.py').read_text(encoding='utf-8')
    assert not re.search(r'(?m)^\s*(?:from|import)\b[^\n]*(?:django|models)\b', kernel)
    evidence('legacy_and_single_entry.json', {
        'instance_version': access.authorization_version(instance), 'kernel_importers': importers,
        'v1_expanded': False, 'v2_without_policy': False, 'legacy_delegable_reached_v2': False})
