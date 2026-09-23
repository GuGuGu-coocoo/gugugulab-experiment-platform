"""P03R02BR evidence: corrected audit evidence, explicit migration choices, the
final-strategy preview, subject-bound canonical policy and WAL-safe copies.

The expected side of every check is the P03R02BR task contract (2026-09-23) and
the original independent probe, never the implementation under test:

- audit state follows the newest complete boolean before/after pair only:
  ``true -> false`` proves a revocation, ``false -> true`` clears it,
  ``true -> true`` stays granted and ``false -> false`` is no evidence.
  Malformed evidence and a legacy ``revoke_member`` row without the structured
  pair are unknown items, never a guessed revocation;
- a legacy Admin with missing Grants never silently receives the v2 default:
  each affected study and the future default need an explicit Owner choice
  (``keep_legacy`` / ``adopt_v2`` / ``keep_empty``), and a blank or malformed
  submission is refused by the backend;
- the preview shows the final strategy after choices and audit exceptions, the
  confirmation applies exactly it, and any change of state makes it stale;
- ``access.canonical_policy`` re-reads the stored subject, profile and Owner
  facts, so a foreign profile or an in-memory Owner edit cannot escalate;
- the migration tool never copies SQLite sidecars; a real WAL source with a
  live writer yields a self-contained copy, and a concurrent commit during the
  copy window refuses it.
"""
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core import access, authorization, governance_migration
from core.models import (AccountProfile, Audit, Grant, Instance, PermissionPreview,
                         Principal, Study)
from core.protocol import Rejected

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = 'synthetic-p03r02br-owner-password'
STUDY_V2 = {
    'study.view', 'study.configure', 'build.upload', 'build.preview',
    'release.approve_pilot', 'recruitment.manage', 'data.export_raw',
    'identity_mapping.read', 'session.view', 'session.recover',
    'audit.view', 'study.delete',
}


def load_tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        'remediation_migration_r02br', REPO_ROOT / 'tools' / 'remediation_migration.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


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


def matrix_row(actor, user, study, action, before, after):
    """One complete ``permission.matrix_changed`` row for (user, study, action)."""
    return Audit.objects.create(
        actor=actor, action='permission.matrix_changed', target=f'{user.pk}:{study.pk}',
        before={'actions': {action: {'granted': before, 'delegable': True}}},
        after={'actions': {action: {'granted': after, 'delegable': True}}})


def conservative_choices(diff):
    return {item['id']: item['choices'][0] for item in diff['unknown']}


def explicit_v2_choices(diff):
    choices = conservative_choices(diff)
    for item in diff['unknown']:
        if item['kind'] in ('admin_missing_permissions', 'admin_future_default'):
            choices[item['id']] = governance_migration.CHOICE_ADOPT_V2
    return choices


def exceptions_of(instance):
    diff = governance_migration.read_diff(instance=instance)
    return {(item['user_id'], item['study'], item['action']) for item in diff['exceptions']}


def enable(owner, instance, choices):
    row = governance_migration.preview_enablement(owner, choices)
    result = governance_migration.confirm_enablement(owner, OWNER_PASSWORD,
                                                     instance.governance_revision, row.pk)
    instance.refresh_from_db()
    return row, result


def world(*, study_count=1, prefix='p03r02br'):
    owner = make_user(f'{prefix}_owner')
    instance = make_instance(owner, version=1)
    AccountProfile.objects.create(user=owner, role='user')
    admin = make_user(f'{prefix}_admin')
    AccountProfile.objects.create(user=admin, role='admin')
    studies = [make_study(f'P03R02BR study {index}') for index in range(study_count)]
    return owner, instance, admin, studies


# --- audit evidence: complete boolean history, newest evidence wins ---------

def test_audit_state_follows_complete_boolean_history(db, evidence):
    owner, instance, admin, (study,) = world()
    grant(admin, study, 'study.view', 'data.export_raw')
    key = (admin.pk, str(study.pk), 'data.export_raw')

    # Initial false -> false is not evidence of a revocation.
    matrix_row(owner, admin, study, 'data.export_raw', False, False)
    assert exceptions_of(instance) == set()
    # true -> false proves one; false -> true clears it again; true -> true is
    # still granted and never an exception.
    matrix_row(owner, admin, study, 'data.export_raw', True, False)
    assert exceptions_of(instance) == {key}
    matrix_row(owner, admin, study, 'data.export_raw', False, True)
    assert exceptions_of(instance) == set()
    matrix_row(owner, admin, study, 'data.export_raw', True, True)
    assert exceptions_of(instance) == set()

    # The newest incomplete pair turns the key into an unknown item instead of
    # guessing a revocation from older history.
    Audit.objects.create(actor=owner, action='permission.matrix_changed',
                         target=f'{admin.pk}:{study.pk}',
                         before={'actions': {'data.export_raw': {'granted': 'yes'}}},
                         after={'actions': {'data.export_raw': {'granted': False}}})
    diff = governance_migration.read_diff(instance=instance)
    unknown = [item for item in diff['unknown']
               if item['kind'] == 'audit_unknown' and item['action'] == 'data.export_raw']
    assert len(unknown) == 1 and unknown[0]['choices'] == ['acknowledge_unknown']
    assert exceptions_of(instance) == set()

    # A row with no usable actions payload at all is a pair-level unknown; it
    # never silently erases or creates a per-action revocation.
    Audit.objects.create(actor=owner, action='permission.matrix_changed',
                         target=f'{admin.pk}:{study.pk}', before=None, after=None)
    diff = governance_migration.read_diff(instance=instance)
    pair_unknown = [item for item in diff['unknown']
                    if item['kind'] == 'audit_unknown' and item['action'] is None]
    assert len(pair_unknown) == 1
    assert pair_unknown[0]['user_id'] == admin.pk and pair_unknown[0]['study'] == str(study.pk)
    assert pair_unknown[0]['choices'] == ['acknowledge_unknown']
    evidence('audit_history.json', {'exceptions_after_regrant': sorted(exceptions_of(instance)),
                                    'malformed_unknown': unknown[0]['id'],
                                    'pair_unknown': pair_unknown[0]['id']})


def test_regrant_survives_the_explicit_adoption(db, evidence):
    # true -> false then false -> true: the action survives as granted.
    owner, instance, admin, (study_a,) = world(prefix='p03r02br_regrant')
    grant(admin, study_a, 'study.view', 'data.export_raw')
    matrix_row(owner, admin, study_a, 'data.export_raw', True, False)
    matrix_row(owner, admin, study_a, 'data.export_raw', False, True)
    _row, _result = enable(owner, instance, explicit_v2_choices(
        governance_migration.read_diff(instance=instance)))
    policy = access.canonical_policy(admin, instance=instance)
    assert access.allowed(admin, study_a, 'data.export_raw', version=2, policy=policy) is True
    frozen_a = AccountProfile.objects.get(user=admin).study_overrides[str(study_a.pk)]
    assert 'data.export_raw' in frozen_a  # the regrant survives the explicit adoption
    evidence('regrant_survives.json', {'frozen': frozen_a})


def test_proven_revocation_is_applied_even_when_the_grant_remains(db, evidence):
    owner_b, instance_b, admin_b, (study_b,) = world(prefix='p03r02br_revoked')
    grant(admin_b, study_b, 'study.view', 'data.export_raw')
    matrix_row(owner_b, admin_b, study_b, 'data.export_raw', True, False)
    _row_b, result_b = enable(owner_b, instance_b, explicit_v2_choices(
        governance_migration.read_diff(instance=instance_b)))
    policy_b = access.canonical_policy(admin_b, instance=instance_b)
    profile_b = AccountProfile.objects.get(user=admin_b)
    frozen_b = profile_b.study_overrides[str(study_b.pk)]
    assert access.allowed(admin_b, study_b, 'data.export_raw', version=2, policy=policy_b) is False
    assert set(frozen_b) == set(STUDY_V2) - {'data.export_raw'}
    assert result_b['overrides'][0]['revoked'] == ['data.export_raw']
    evidence('proven_revocation.json', {'frozen_without_export': 'data.export_raw' not in frozen_b,
                                        'revoked': result_b['overrides'][0]['revoked']})


def test_legacy_revoke_member_without_detail_is_unknown(db, evidence):
    owner, instance, admin, (study,) = world()
    grant(admin, study, 'study.view', 'data.export_raw')
    Audit.objects.create(actor=owner, study=study, action='revoke_member', target=str(study.pk))

    diff = governance_migration.read_diff(instance=instance)
    assert diff['exceptions'] == []
    unknown = [item for item in diff['unknown']
               if item['kind'] == 'audit_unknown' and item['action'] is None]
    assert len(unknown) == 1 and unknown[0]['choices'] == ['acknowledge_unknown']
    assert unknown[0]['study'] == str(study.pk)  # the row never named the user

    choices = conservative_choices(diff)
    missing = dict(choices)
    missing.pop(unknown[0]['id'])
    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(owner, missing)
    assert info.value.code == 'unknown_choice_required'
    row = governance_migration.preview_enablement(owner, choices)
    assert row.staged['choices'][unknown[0]['id']] == 'acknowledge_unknown'
    evidence('revoke_member_unknown.json', {'unknown': unknown[0]['id'],
                                            'exceptions': diff['exceptions']})


def test_conflict_confirmation_evidence_is_ordered_like_matrix_rows(db, evidence):
    owner, instance, admin, (study,) = world()
    grant(admin, study, 'study.view', 'data.export_raw', 'build.upload')
    Audit.objects.create(actor=owner, action='access.conflict_resolved', target=f'{admin.pk}:{study.pk}',
                         before={'actions': ['data.export_raw', 'build.upload'], 'choice': 'remove_conflicting'},
                         after={'actions': ['study.view']})
    assert exceptions_of(instance) == {(admin.pk, str(study.pk), 'data.export_raw'),
                                       (admin.pk, str(study.pk), 'build.upload')}
    # grant_view keeps the actions (complete after-snapshot truth) and clears them.
    Audit.objects.create(actor=owner, action='access.conflict_resolved', target=f'{admin.pk}:{study.pk}',
                         before={'actions': ['data.export_raw'], 'choice': 'grant_view'},
                         after={'actions': ['study.view', 'data.export_raw']})
    assert exceptions_of(instance) == {(admin.pk, str(study.pk), 'build.upload')}
    # A malformed pair is unknown, never a revocation.
    Audit.objects.create(actor=owner, action='access.conflict_resolved', target=f'{admin.pk}:{study.pk}',
                         before={'actions': 'not-a-list'}, after={'actions': ['study.view']})
    diff = governance_migration.read_diff(instance=instance)
    assert all(item['action'] != 'data.export_raw' or item['kind'] == 'audit_unknown'
               for item in diff['unknown'] + diff['exceptions'])
    audit_unknowns = [item for item in diff['unknown'] if item['kind'] == 'audit_unknown']
    assert audit_unknowns and audit_unknowns[0]['choices'] == ['acknowledge_unknown']
    evidence('conflict_evidence.json', {'remaining_exception_actions': [
        item['action'] for item in diff['exceptions']]})


# --- explicit choices for missing Admin permissions and the future default --

def test_missing_admin_permissions_never_default_to_v2(db, evidence):
    owner, instance, admin, (study_a, study_b) = world(study_count=2)
    grant(admin, study_a, 'study.view')
    diff = governance_migration.read_diff(instance=instance)
    missing = {item['study']: item for item in diff['unknown']
               if item['kind'] == 'admin_missing_permissions'}
    assert set(missing) == {str(study_a.pk), str(study_b.pk)}
    assert missing[str(study_a.pk)]['choices'] == ['keep_legacy', 'adopt_v2']
    future = [item for item in diff['unknown'] if item['kind'] == 'admin_future_default']
    assert len(future) == 1 and future[0]['user_id'] == admin.pk
    assert future[0]['choices'] == ['keep_empty', 'adopt_v2']

    choices = conservative_choices(diff)
    # Blank, missing and extra choices are all refused before any write.
    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(owner, {**choices, f'missing:{admin.pk}:{study_a.pk}': ''})
    assert info.value.code == 'unknown_choice_invalid'
    short = dict(choices)
    short.pop(f'missing:{admin.pk}:{study_b.pk}')
    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(owner, short)
    assert info.value.code == 'unknown_choice_required'
    with pytest.raises(Rejected) as info:
        governance_migration.preview_enablement(owner, {**choices, 'not-an-item': 'keep_legacy'})
    assert info.value.code == 'unknown_choice_unknown'
    instance.refresh_from_db()
    assert instance.authorization_version == 1

    row = governance_migration.preview_enablement(owner, choices)
    admin_summary = next(subject for subject in row.summary['subjects'] if subject['user_id'] == admin.pk)
    by_study = {study['study']: study for study in admin_summary['studies']}
    assert set(by_study[str(study_a.pk)]['actions']) == {'study.view'}
    assert by_study[str(study_b.pk)]['actions'] == []
    assert admin_summary['future']['source'] == 'conservative_empty'
    assert admin_summary['future']['write'] == []
    evidence('missing_admin_choices.json', {'missing_studies': sorted(missing),
                                            'conservative_actions_a': by_study[str(study_a.pk)]['actions'],
                                            'future_source': admin_summary['future']['source']})


def test_conservative_future_default_keeps_existing_and_blocks_new_studies(db, evidence):
    owner, instance, admin, (study_a,) = world()
    grant(admin, study_a, 'study.view', 'build.upload')
    diff = governance_migration.read_diff(instance=instance)
    enable(owner, instance, conservative_choices(diff))
    policy = access.canonical_policy(admin, instance=instance)
    profile = AccountProfile.objects.get(user=admin)
    assert profile.future_study_actions == []
    assert access.allowed(admin, study_a, 'study.view', version=2, policy=policy) is True
    assert access.allowed(admin, study_a, 'build.upload', version=2, policy=policy) is True
    new_study = make_study('P03R02BR future study')
    assert access.allowed(admin, new_study, 'study.view', version=2, policy=policy) is False
    assert access.allowed(admin, new_study, 'build.upload', version=2, policy=policy) is False
    evidence('conservative_future.json', {'future_study_actions': profile.future_study_actions,
                                          'existing_kept': True, 'new_study_denied': True})


def test_explicit_adoption_adds_the_fixed_v2_sets(db, evidence):
    owner, instance, admin, (study_a,) = world()
    grant(admin, study_a, 'study.view', 'data.export_raw', 'member.manage')
    matrix_row(owner, admin, study_a, 'data.export_raw', True, False)
    diff = governance_migration.read_diff(instance=instance)
    row, result = enable(owner, instance, explicit_v2_choices(diff))
    admin_summary = next(subject for subject in row.summary['subjects'] if subject['user_id'] == admin.pk)
    study_row = next(item for item in admin_summary['studies'] if item['study'] == str(study_a.pk))
    assert study_row['choice'] == 'adopt_v2'
    assert set(study_row['actions']) == set(STUDY_V2) - {'data.export_raw'}
    policy = access.canonical_policy(admin, instance=instance)
    for action in STUDY_V2 - {'data.export_raw'}:
        assert access.allowed(admin, study_a, action, version=2, policy=policy) is True, action
    assert access.allowed(admin, study_a, 'data.export_raw', version=2, policy=policy) is False
    assert access.allowed(admin, study_a, 'member.manage', version=2, policy=policy) is False
    # Adopting the fixed set leaves the future default at the v2 template
    # (``None``), so later studies are governed by the fixed set as well.
    assert AccountProfile.objects.get(user=admin).future_study_actions is None
    new_study = make_study('P03R02BR adopted future')
    assert access.allowed(admin, new_study, 'build.upload', version=2, policy=policy) is True
    evidence('explicit_adoption.json', {'study_actions': study_row['actions'],
                                        'future_study_actions': None,
                                        'future_study_allowed': True})


def test_preview_final_strategy_is_bound_and_applied_exactly(db, evidence):
    owner, instance, admin, (study_a, study_b) = world(study_count=2)
    grant(admin, study_a, 'study.view', 'data.export_raw')
    user = make_user('p03r02br_member')
    AccountProfile.objects.create(user=user, role='user')
    grant(user, study_a, 'study.view', 'build.preview')
    matrix_row(owner, admin, study_a, 'data.export_raw', True, False)
    principals = {principal.user_id: principal for principal in Principal.objects.all()}

    diff = governance_migration.read_diff(instance=instance)
    choices = explicit_v2_choices(diff)
    row = governance_migration.preview_enablement(owner, choices)
    summary = row.summary
    assert summary['instance_id'] == str(instance.instance_id)
    assert summary['owner']['user_id'] == owner.pk
    assert summary['diff_digest'] == diff['digest']
    assert summary['projection_digest'] == row.staged['projection_digest']
    # The binding digest moves with the choices: a different strategy never
    # reuses the same preview identity.
    alternate = governance_migration.preview_enablement(
        owner, {**choices, f'missing:{admin.pk}:{study_b.pk}': governance_migration.CHOICE_KEEP_LEGACY})
    assert alternate.binding != row.binding

    # A change after the preview refuses the exact-strategy confirmation.
    matrix_row(owner, admin, study_a, 'build.upload', True, False)
    with pytest.raises(Rejected) as info:
        governance_migration.confirm_enablement(owner, OWNER_PASSWORD, instance.governance_revision, row.pk)
    assert info.value.code == 'preview_stale'
    instance.refresh_from_db()
    assert instance.authorization_version == 1

    # A fresh preview applies exactly the strategy it showed.
    fresh_diff = governance_migration.read_diff(instance=instance)
    fresh, result = enable(owner, instance, explicit_v2_choices(fresh_diff))
    assert instance.authorization_version == 2
    expected_admin_a = next(item for item in next(
        subject for subject in fresh.summary['subjects'] if subject['user_id'] == admin.pk)
        ['studies'] if item['study'] == str(study_a.pk))['actions']
    policy_admin = access.canonical_policy(admin, instance=instance)
    actual_admin_a = sorted(action for action in STUDY_V2
                            if access.allowed(admin, study_a, action, version=2, policy=policy_admin))
    assert actual_admin_a == expected_admin_a
    user_row = next(subject for subject in fresh.summary['subjects'] if subject['user_id'] == user.pk)
    user_study = next(item for item in user_row['studies'] if item['study'] == str(study_a.pk))
    policy_user = access.canonical_policy(user, instance=instance)
    assert sorted(action for action in STUDY_V2
                  if access.allowed(user, study_a, action, version=2, policy=policy_user)) == user_study['actions']
    assert result['overrides']
    assert AccountProfile.objects.get(user=admin).future_study_actions is None
    evidence('final_strategy.json', {'projection_digest': row.summary['projection_digest'],
                                     'choices': len(choices), 'overrides': len(result['overrides']),
                                     'future_study_actions': None,
                                     'principals': {str(key): str(value.pk) for key, value in principals.items()}})


def test_audit_failure_rolls_the_frozen_strategy_back(db, evidence, monkeypatch):
    owner, instance, admin, (study_a,) = world()
    grant(admin, study_a, 'study.view', 'data.export_raw')
    matrix_row(owner, admin, study_a, 'data.export_raw', True, False)
    diff = governance_migration.read_diff(instance=instance)
    row = governance_migration.preview_enablement(owner, explicit_v2_choices(diff))

    calls = {'count': 0}
    original = governance_migration._audit

    def boom(*args, **kwargs):
        calls['count'] += 1
        if calls['count'] >= 2:  # the policy.v2_enabled row fails after the freeze
            raise RuntimeError('synthetic audit failure')
        return original(*args, **kwargs)

    monkeypatch.setattr(governance_migration, '_audit', boom)
    with pytest.raises(RuntimeError):
        governance_migration.confirm_enablement(owner, OWNER_PASSWORD, instance.governance_revision, row.pk)
    instance.refresh_from_db()
    row.refresh_from_db()
    assert instance.authorization_version == 1 and instance.governance_revision == 0
    assert AccountProfile.objects.get(user=admin).study_overrides == {}
    assert row.consumed is False and row.staged is not None
    assert Audit.objects.filter(action='policy.v2_enabled').count() == 0
    assert Audit.objects.filter(action='policy.v2_override_frozen').count() == 0
    evidence('audit_rollback.json', {'version': instance.authorization_version,
                                     'preview_reusable': row.staged is not None,
                                     'frozen_overrides': 0})


# --- canonical policy is bound to stored subject facts ----------------------

def test_canonical_policy_rejects_foreign_profile_and_forged_owner(db, evidence):
    owner, instance, admin, (study,) = world()
    ordinary = make_user('p03r02br_ordinary')
    ordinary_profile = AccountProfile.objects.create(user=ordinary, role='user')
    admin_profile = AccountProfile.objects.get(user=admin)
    grant(admin, study, 'study.view')

    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(ordinary, instance=instance, profile=admin_profile)
    assert info.value.code == 'profile_subject_mismatch'
    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(ordinary, instance=instance,
                                profile=AccountProfile(user_id=ordinary.pk, role='admin', pk=admin_profile.pk))
    assert info.value.code == 'profile_subject_mismatch'

    # Modified in-memory rows are not authorization facts: the stored values win.
    admin_profile.role = 'user'
    admin_profile.study_overrides = {}
    assert access.canonical_policy(admin, instance=instance).role == 'admin'
    ordinary_profile.role = 'admin'
    assert access.canonical_policy(ordinary, instance=instance).role == 'user'

    # A forged in-memory Owner is re-read from storage in both directions.
    forged = Instance.objects.get(pk=instance.pk)
    forged.owner_id = admin.pk
    assert access.canonical_policy(admin, instance=forged).is_instance_owner is False
    assert access.canonical_policy(owner, instance=forged).is_instance_owner is True
    owner.is_active = False
    assert access.canonical_policy(owner, instance=instance).active is True

    ghost = get_user_model()(username='p03r02br_ghost')
    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(ghost, instance=instance)
    assert info.value.code == 'auth_required'
    evidence('canonical_binding.json', {'foreign_profile': 'profile_subject_mismatch',
                                        'forged_owner': False, 'ghost': 'auth_required'})


# --- WAL: sidecar-free self-contained copies, concurrent change refusal -----

def _wal_source(root, name='wal_source'):
    source = root / name
    source.mkdir(parents=True, exist_ok=True)
    database = source / 'gep.sqlite3'
    connection = sqlite3.connect(database)
    connection.execute('CREATE TABLE gep_throttle (key TEXT PRIMARY KEY, window INTEGER, count INTEGER)')
    connection.execute("INSERT INTO gep_throttle VALUES ('seed', 1, 1)")
    connection.commit()
    connection.close()
    return source, database








# --- real Chrome: blank refusal, explicit choice, final strategy, confirm ---



SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const observations={choice_values:{}};
const errors=[];
try {
  const owner=await browser.newContext();
  const page=await owner.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('p03r02br_owner');
  await page.locator('[name=password]').fill(ownerPassword);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/users');
  await page.locator('[data-migration-diff-form] button').click();
  await page.waitForLoadState('load');
  await expect(page.locator('[data-migration-form]')).toBeVisible();

  // 1) A blank choice must be refused by the backend, with no preview created.
  await page.locator('[data-migration-form] button').click();
  await page.waitForLoadState('load');
  observations.blank_refused = await page.locator('div.error').first().isVisible();
  observations.preview_after_blank = await page.locator('[data-preview="migration_enable"]').count();
  expect(observations.blank_refused).toBe(true);
  expect(observations.preview_after_blank).toBe(0);

  // The rejected submission renders the error page without the difference, so
  // the Owner re-opens it before making explicit choices.
  await page.locator('[data-migration-diff-form] button').click();
  await page.waitForLoadState('load');
  await expect(page.locator('[data-migration-form]')).toBeVisible();

  // 2) Explicit Owner choices: adopt the fixed v2 set for the legacy Admin,
  //    every other item keeps its first (conservative) value.
  const selects=page.locator('[data-migration-unknown-select]');
  const count=await selects.count();
  observations.unknown_items=count;
  expect(count).toBeGreaterThan(0);
  for(let index=0;index<count;index+=1){
    const select=selects.nth(index);
    const name=await select.getAttribute('name');
    const item=name.slice('unknown:'.length);
    if(item.startsWith('missing:')||item.startsWith('future:')){
      await select.selectOption('adopt_v2');
      observations.choice_values[item.startsWith('future:')?'future':'missing']='adopt_v2';
    }else{
      const value=await select.locator('option').nth(1).getAttribute('value');
      await select.selectOption(value);
      observations.choice_values[item.split(':')[0]]=value;
    }
  }
  await page.locator('[data-migration-form] button').click();
  await page.waitForLoadState('load');

  // 3) The preview shows the final strategy and the confirmation controls.
  await expect(page.locator('[data-migration-final-strategy="1"]')).toBeVisible();
  observations.final_visible = true;
  observations.digest_shown = await page.locator('[data-migration-preview-digest]').count() > 0;
  const adminRow = page.locator('[data-final-subject]').filter({hasText:'p03r02br_admin'});
  observations.admin_final_study_added = Number(await adminRow.getAttribute('data-final-study-added'));
  const confirmForm = page.locator('form').filter({has:page.locator('[name=op][value=migration_confirm]')});
  await confirmForm.locator('[name=password]').fill('not-the-owner-password');
  await confirmForm.getByRole('button').click();
  await page.waitForLoadState('load');
  observations.wrong_password_refused = await page.locator('div.error').first().isVisible();

  // 4) The Owner's own password confirms exactly the previewed strategy.
  const retryForm = page.locator('form').filter({has:page.locator('[name=op][value=migration_confirm]')});
  await retryForm.locator('[name=password]').fill(ownerPassword);
  await retryForm.getByRole('button').click();
  await page.waitForLoadState('load');
  observations.confirmed = await page.locator('[data-migration-enabled="1"]').count() > 0;
  observations.enabled_state_visible = await page.locator('[data-policy-migration="enabled"]').count() > 0;
  observations.page_errors=errors;
  expect(errors).toEqual([]);
  console.log('P03R02BR_OBSERVATIONS '+JSON.stringify(observations));
} finally {await browser.close();}
'''


@pytest.fixture
def world_scenario(db):
    owner, instance, admin, (study_a, study_b) = world(study_count=2)
    grant(admin, study_a, 'study.view', 'data.export_raw', 'member.manage')
    matrix_row(owner, admin, study_a, 'data.export_raw', True, False)
    return owner, instance, admin, study_a, study_b
