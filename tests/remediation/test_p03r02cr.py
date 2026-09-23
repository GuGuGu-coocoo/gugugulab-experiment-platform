"""P03R02CR evidence: the four R02C review gaps and their regressions.

Expected side: the R00 contract (2026-09-23, sections A/B) and hand-written
per-subject tables, never the implementation under test:

- a complete explicit ``study_overrides`` entry (including ``[]``) fully covers
  the role default, the future bound and the legacy Grant rows for that study,
  so ``allowed``, ``viewable_studies``, ``manageable_study_ids``, the study
  selector, the home cards and every real HTTP entry agree;
- every role holding ``study.create`` creates a study atomically with the full
  finite business set written once (complete override + Grant + Principal) and
  a later revocation never refills from ``creator_principal``;
- stored authorization/policy versions route exactly: 1 -> the unchanged legacy
  boundary, 2 -> the kernel, anything else -> refusal without a write;
- one real Chrome journey (Owner hides a default Admin's study; the Admin's
  home and selector never show it; a restricted Admin creates and enters its own
  study; after revocation it stays gone).

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r02cr/<UTC>-<random>/`` root.
"""
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from core import access, authorization, permissions
from core.models import (AccountProfile, Audit, Grant, Instance, Principal,
                         Study)

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = 'synthetic-p03r02cr-owner-password'
VIEW = 'study.view'
CONFIGURE = 'study.configure'
STUDY_V2 = (
    'study.view', 'study.configure', 'build.upload', 'build.preview',
    'release.approve_pilot', 'recruitment.manage', 'data.export_raw',
    'identity_mapping.read', 'session.view', 'session.recover',
    'audit.view', 'study.delete',
)
ALL = frozenset(STUDY_V2)
EMPTY = frozenset()
# Account states that deny every effective action before any study decision.
DENIED_STATES = {'inactive_admin', 'must_change_admin', 'inactive_grant_user',
                 'must_change_grant_user', 'deleted_grant_user',
                 'unknown_policy_admin'}


# --- synthetic world --------------------------------------------------------

def make_user(username, *, role='user', active=True, must_change=False,
              deleted=False, policy_version=2, future=None, overrides=None,
              platform=None, grants=None):
    """One account with an explicit stored v2 policy and no implicit defaults."""
    user = get_user_model().objects.create_user(username, password=OWNER_PASSWORD)
    if not active:
        user.is_active = False
        user.save(update_fields=['is_active'])
    Principal.objects.create(user=user, deleted_at=timezone.now() if deleted else None)
    AccountProfile.objects.create(
        user=user, role=role, policy_version=policy_version,
        must_change_password=must_change,
        platform_overrides=dict(platform or {}),
        study_overrides={str(getattr(key, 'pk', key)): sorted(value)
                         for key, value in (overrides or {}).items()},
        future_study_actions=future)
    for study, actions in (grants or {}).items():
        for action in actions:
            Grant.objects.create(user=user, study=study, action=action, delegable=False)
    return user


@pytest.fixture
def world(db):
    owner = make_user('p03r02cr_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    override_study = Study.objects.create(title='P03R02CR override study')
    default_study = Study.objects.create(title='P03R02CR default study')
    # Distinctive counts for the visible study only: a hidden card must never
    # render either the title or these numbers.
    from core.models import Participant
    for index in range(3):
        Participant.objects.create(study=override_study, code=f'p03r02cr-hidden-{index}')
    Participant.objects.create(study=default_study, code='p03r02cr-visible-0')
    s, t = override_study, default_study
    users = {
        'owner': owner,
        'default_admin': make_user('p03r02cr_default_admin', role='admin'),
        'empty_future_admin': make_user('p03r02cr_empty_future_admin', role='admin', future=[]),
        'subset_future_admin': make_user('p03r02cr_subset_future_admin', role='admin',
                                         future=[VIEW, CONFIGURE]),
        'hidden_admin': make_user('p03r02cr_hidden_admin', role='admin', overrides={s: []}),
        'view_admin': make_user('p03r02cr_view_admin', role='admin', overrides={s: [VIEW]}),
        'configure_admin': make_user('p03r02cr_configure_admin', role='admin',
                                     overrides={s: [VIEW, CONFIGURE]}),
        'hidden_admin_grants': make_user(
            'p03r02cr_hidden_admin_grants', role='admin', overrides={s: []},
            grants={s: [VIEW, CONFIGURE, 'data.export_raw']}),
        'no_grant_user': make_user('p03r02cr_no_grant_user'),
        'grant_user': make_user('p03r02cr_grant_user',
                                grants={s: [VIEW, CONFIGURE]}),
        'hidden_user_grants': make_user(
            'p03r02cr_hidden_user_grants', overrides={s: []},
            grants={s: [VIEW, CONFIGURE, 'data.export_raw']}),
        'view_user_grants': make_user(
            'p03r02cr_view_user_grants', overrides={s: [VIEW]},
            grants={s: [VIEW, CONFIGURE, 'data.export_raw']}),
        'inactive_admin': make_user('p03r02cr_inactive_admin', role='admin', active=False),
        'must_change_admin': make_user('p03r02cr_must_change_admin', role='admin',
                                       must_change=True),
        'inactive_grant_user': make_user('p03r02cr_inactive_grant_user', active=False,
                                         grants={s: [VIEW]}),
        'must_change_grant_user': make_user('p03r02cr_must_change_grant_user',
                                            must_change=True, grants={s: [VIEW]}),
        'deleted_grant_user': make_user('p03r02cr_deleted_grant_user', deleted=True,
                                        grants={s: [VIEW]}),
        'unknown_policy_admin': make_user('p03r02cr_unknown_policy_admin', role='admin',
                                          policy_version=3),
    }
    return {'instance': instance, 'override': s, 'default': t, 'users': users}


# Hand-written expected effective action sets per subject and study. The Admin
# default is the fixed 12-action template; an explicit override (even ``[]``)
# fully covers it; a user follows explicit grants only.
EXPECTED = {
    'owner': (ALL, ALL),
    'default_admin': (ALL, ALL),
    'empty_future_admin': (EMPTY, EMPTY),
    'subset_future_admin': ({VIEW, CONFIGURE}, {VIEW, CONFIGURE}),
    'hidden_admin': (EMPTY, ALL),
    'view_admin': ({VIEW}, ALL),
    'configure_admin': ({VIEW, CONFIGURE}, ALL),
    'hidden_admin_grants': (EMPTY, ALL),
    'no_grant_user': (EMPTY, EMPTY),
    'grant_user': ({VIEW, CONFIGURE}, EMPTY),
    'hidden_user_grants': (EMPTY, EMPTY),
    'view_user_grants': ({VIEW}, EMPTY),
    'inactive_admin': (EMPTY, EMPTY),
    'must_change_admin': (EMPTY, EMPTY),
    'inactive_grant_user': (EMPTY, EMPTY),
    'must_change_grant_user': (EMPTY, EMPTY),
    'deleted_grant_user': (EMPTY, EMPTY),
    'unknown_policy_admin': (EMPTY, EMPTY),
}


def login(username, password=OWNER_PASSWORD):
    client = Client()
    response = client.post('/login', {'username': username, 'password': password})
    assert response.status_code == 302 and response.url == '/'
    return client


def forced_client(user):
    """A session for a subject whose account state may block a real sign-in."""
    client = Client()
    client.force_login(user)
    profile = AccountProfile.objects.filter(user_id=user.pk).first()
    session = client.session
    session['gep_auth_version'] = profile.auth_version if profile is not None else None
    session.save()
    return client


def preview_id_of(response):
    match = re.search(r'name="preview_id" value="([0-9a-f-]{36})"', response.content.decode('utf-8'))
    assert match, 'the preview page must carry the confirmation preview id'
    return match.group(1)


def matrix_hide(client, target, study):
    """Owner-confirmed revocation of every explicit action on one study."""
    preview = client.post('/users', {'op': 'matrix_preview', 'user_id': str(target.pk),
                                     'study_id': str(study.pk), 'visibility': '0'})
    assert preview.status_code == 200, preview.content[:400]
    instance = Instance.objects.get(pk=1)
    committed = client.post('/users', {'op': 'matrix_commit',
                                       'preview_id': preview_id_of(preview),
                                       'revision': str(instance.governance_revision),
                                       'password': OWNER_PASSWORD})
    assert committed.status_code == 200, committed.content[:400]
    return committed


def stored_overrides(user):
    return {key: sorted(value) for key, value in
            (AccountProfile.objects.get(user=user).study_overrides or {}).items()}


# --- AC1: fixed expectation matrix, list/single consistency, no leak ---------

def test_fixed_expectation_matrix_list_and_single_object_agree(world, evidence):
    observed = []
    for name, (expected_s, expected_t) in EXPECTED.items():
        user = world['users'][name]
        policy = None
        try:
            policy = access.canonical_policy(user)
        except authorization.PolicyError:
            policy = None
        for study, expected in ((world['override'], expected_s), (world['default'], expected_t)):
            for action in STUDY_V2:
                assert access.allowed(user, study, action) == (action in expected), \
                    (name, study.title, action)
            visible = set(access.viewable_studies(user).values_list('pk', flat=True))
            assert (study.pk in visible) == (VIEW in expected), (name, study.title)
            manageable = access.manageable_study_ids(user)
            assert (str(study.pk) in manageable) == ({VIEW, CONFIGURE} <= expected), \
                (name, study.title)
            if policy is not None:
                assert set(access.resolve_study_actions(user, study, policy=policy)) == set(expected), \
                    (name, study.title)
        choices = permissions.configure_studies_page(user)
        titles = {study.title for study in choices['studies']}
        assert (world['override'].title in titles) == ({VIEW, CONFIGURE} <= expected_s), name
        assert (world['default'].title in titles) == ({VIEW, CONFIGURE} <= expected_t), name
        observed.append({'subject': name, 'override_study': sorted(expected_s),
                         'default_study': sorted(expected_t),
                         'selector': sorted(titles)})
    evidence('expectation_matrix.json', {'matrix': observed})


def test_home_and_direct_requests_match_the_fixed_expectations(world, evidence):
    override, default = world['override'], world['default']
    observed = []
    for name, (expected_s, expected_t) in EXPECTED.items():
        user = world['users'][name]
        client = forced_client(user)
        home = client.get('/')
        body = home.content.decode('utf-8', 'replace')
        assert home.status_code in (200, 302), (name, home.status_code)
        assert (override.title in body) == (VIEW in expected_s), (name, home.status_code)
        assert (default.title in body) == (VIEW in expected_t), (name, home.status_code)
        assert (f'data-study-card="{override.pk}"' in body) == (VIEW in expected_s), name
        # The create entry is offered exactly to an active account whose stored
        # platform policy still holds study.create.
        assert ('name="title"' in body) == (name not in DENIED_STATES), name
        page = client.get(f'/studies/{override.pk}')
        reachable = VIEW in expected_s and name not in DENIED_STATES
        assert (page.status_code == 200) == reachable, (name, page.status_code)
        if not reachable:
            assert override.title not in page.content.decode('utf-8', 'replace'), name
        observed.append({'subject': name, 'home': home.status_code, 'study': page.status_code})
    evidence('http_expectations.json', {'rows': observed})


def test_admin_overview_and_selector_hide_the_explicitly_hidden_study(world, evidence):
    actor = world['users']['hidden_admin']
    target = world['users']['view_admin']
    override, default = world['override'], world['default']
    client = forced_client(actor)
    page = client.get('/users')
    assert page.status_code == 200
    body = page.content.decode('utf-8')
    assert override.title not in body
    assert default.title in body
    assert f'data-matrix-row="{target.pk}-{override.pk}"' not in body
    assert f'data-matrix-row="{target.pk}-{default.pk}"' in body
    # The study selector is bounded by the same kernel result: only the study
    # with both view and configure is offered.
    assert 'data-configure-total="1"' in body
    assert f'/users/templates/roster?study={override.pk}' not in body
    assert f'/users/templates/roster?study={default.pk}' in body
    home = client.get('/').content.decode('utf-8')
    assert f'data-study-card="{override.pk}"' not in home and override.title not in home
    assert f'data-study-card="{default.pk}"' in home
    evidence('overview_and_selector.json', {
        'hidden_title_absent': override.title not in body,
        'selector_total': 1,
        'hidden_card_absent': f'data-study-card="{override.pk}"' not in home})


def test_hidden_study_write_and_export_entries_refuse_without_writes(world):
    actor = world['users']['hidden_admin_grants']
    override = world['override']
    client = forced_client(actor)
    before = {
        'mode': Study.objects.get(pk=override.pk).mode,
        'participants': override.participant_set.count(),
        'grants': Grant.objects.count(),
        'audits': Audit.objects.count(),
    }
    assert client.get(f'/studies/{override.pk}').status_code == 403
    assert client.post(f'/studies/{override.pk}', {
        'op': 'configure', 'mode': 'id', 'max_sessions': '2'}).status_code == 403
    assert client.post(f'/studies/{override.pk}', {
        'op': 'roster', 'roster': 'p03r02cr-new-id'}).status_code == 403
    assert client.post('/v1/admin/exports', {'study_id': str(override.pk)},
                       content_type='application/json').status_code == 403
    assert client.get(f'/users/templates/roster?study={override.pk}').status_code == 403
    after = {
        'mode': Study.objects.get(pk=override.pk).mode,
        'participants': override.participant_set.count(),
        'grants': Grant.objects.count(),
        'audits': Audit.objects.count(),
    }
    assert after == before


# --- AC2: atomic creation, one-time permissions, revocation, rollback --------

def test_real_post_creation_writes_one_time_complete_creator_permissions(db):
    owner = make_user('p03r02cr_create_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    existing = Study.objects.create(title='P03R02CR existing study')
    restricted = make_user('p03r02cr_restricted_creator', role='admin', future=[],
                           overrides={existing: []})
    member_creator = make_user('p03r02cr_member_creator')
    owner_client = login(owner.username)

    for creator, title in ((member_creator, 'P03R02CR member created study'),
                           (restricted, 'P03R02CR restricted admin created study')):
        client = forced_client(creator)
        response = client.post('/', {'title': title})
        assert response.status_code == 302, (title, response.content[:400])
        study = Study.objects.get(title=title)
        principal = Principal.objects.get(user=creator)
        assert study.creator_principal_id == principal.pk
        # The complete business set is stored once, in the same transaction, as
        # both the explicit override and the legacy grant rows.
        assert set(stored_overrides(creator)[str(study.pk)]) == set(STUDY_V2)
        assert set(Grant.objects.filter(user=creator, study=study)
                   .values_list('action', flat=True)) == set(STUDY_V2)
        for action in STUDY_V2:
            assert access.allowed(creator, study, action), (title, action)
        assert client.get(f'/studies/{study.pk}').status_code == 200
        assert f'data-study-card="{study.pk}"' in client.get('/').content.decode('utf-8')
        assert Audit.objects.filter(action='study.created', target=str(study.pk)).count() == 1

        # A later Owner revocation wins and is never refilled from the creator
        # fact: the explicit selection becomes empty and direct requests refuse.
        matrix_hide(owner_client, creator, study)
        assert not access.allowed(creator, study, VIEW)
        assert study.pk not in set(access.viewable_studies(creator).values_list('pk', flat=True))
        assert client.get(f'/studies/{study.pk}').status_code == 403
        assert f'data-study-card="{study.pk}"' not in client.get('/').content.decode('utf-8')
        assert str(study.pk) not in access.manageable_study_ids(creator)
        # With the one-time storage removed as well, the creator reference alone
        # still grants nothing.
        Grant.objects.filter(user=creator, study=study).delete()
        profile = AccountProfile.objects.get(user=creator)
        overrides = dict(profile.study_overrides or {})
        overrides.pop(str(study.pk), None)
        profile.study_overrides = overrides
        profile.save(update_fields=['study_overrides'])
        assert not access.allowed(creator, study, VIEW)
        study.refresh_from_db()
        assert study.creator_principal_id == principal.pk


def test_creation_authorization_and_audit_are_inside_one_transaction(db, monkeypatch):
    from core import gui as gui_module

    owner = make_user('p03r02cr_tx_owner')
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    creator = make_user('p03r02cr_tx_creator')
    client = forced_client(creator)

    # The authorization decision is taken from the locked state inside the same
    # atomic block as the study/override/grant/audit writes.
    seen = {}
    real_allowed = gui_module.allowed_platform

    def probe(user, action, **kwargs):
        seen['in_atomic_block'] = transaction.get_connection().in_atomic_block
        return real_allowed(user, action, **kwargs)

    monkeypatch.setattr(gui_module, 'allowed_platform', probe)
    assert client.post('/', {'title': 'P03R02CR in-transaction study'}).status_code == 302
    assert seen.get('in_atomic_block') is True
    monkeypatch.undo()

    # An audit failure after the study row was written rolls everything back.
    rollback_user = make_user('p03r02cr_tx_rollback')
    rollback_client = forced_client(rollback_user)
    before = {'studies': Study.objects.count(), 'grants': Grant.objects.count(),
              'revision': Instance.objects.get(pk=1).governance_revision}

    def boom(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')

    monkeypatch.setattr(gui_module.Audit.objects, 'create', boom)
    with pytest.raises(RuntimeError):
        rollback_client.post('/', {'title': 'P03R02CR rolled back study'})
    monkeypatch.undo()
    assert Study.objects.count() == before['studies']
    assert Grant.objects.count() == before['grants']
    assert Instance.objects.get(pk=1).governance_revision == before['revision']
    assert not Study.objects.filter(title='P03R02CR rolled back study').exists()
    assert Grant.objects.filter(user=rollback_user).count() == 0
    assert stored_overrides(rollback_user) == {}
    assert not Audit.objects.filter(action='study.created',
                                    target__isnull=False,
                                    actor=rollback_user).exists()


# --- AC2: real threaded creation/revocation race -----------------------------

RACE_TITLE = 'P03R02CR race-created study'


def _race_world():
    owner = make_user('p03r02cr_race_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    creator = make_user('p03r02cr_race_creator')
    return owner, instance, creator


def _run_race(evidence):
    """Two real writers on a file database: create the study vs revoke the right.

    SQLite runs every ``transaction.atomic`` block in IMMEDIATE mode, so the two
    writers serialize. The legal outcomes are exactly: creation commits first
    (the study exists with its one-time creator set, then the revocation lands)
    or the revocation commits first (the creation is refused with no row). A
    creation that commits after the revocation was stored would be the bypass.
    """
    owner, instance, creator = _race_world()
    barrier = threading.Barrier(2)
    outcomes = []

    def create_worker():
        from django.db import connections
        connections.close_all()
        barrier.wait()
        try:
            client = forced_client(creator)
            response = client.post('/', {'title': RACE_TITLE})
            outcomes.append(('create', response.status_code))
        except Exception as error:  # noqa: BLE001 - recorded as evidence
            outcomes.append(('create_error', repr(error)))
        finally:
            connections.close_all()

    def revoke_worker():
        from django.db import connections
        connections.close_all()
        barrier.wait()
        try:
            with transaction.atomic():
                profile = AccountProfile.objects.select_for_update().get(user=creator)
                overrides = dict(profile.platform_overrides or {})
                overrides['study.create'] = False
                profile.platform_overrides = overrides
                profile.revision += 1
                profile.save(update_fields=['platform_overrides', 'revision'])
                locked = Instance.objects.select_for_update().get(pk=1)
                locked.governance_revision += 1
                locked.save(update_fields=['governance_revision'])
            outcomes.append(('revoke', 'ok'))
        except Exception as error:  # noqa: BLE001 - recorded as evidence
            outcomes.append(('revoke_error', repr(error)))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=create_worker), threading.Thread(target=revoke_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads), 'a writer never finished'
    assert sorted(kind for kind, _ in outcomes) == ['create', 'revoke'], outcomes
    status = dict(outcomes)
    assert status['revoke'] == 'ok'
    assert status['create'] in (302, 403, 409), outcomes
    exists = Study.objects.filter(title=RACE_TITLE).exists()
    if status['create'] == 302:
        assert exists
        study = Study.objects.get(title=RACE_TITLE)
        assert access.allowed(creator, study, VIEW)
    else:
        assert not exists
    # The revocation is applied in both serializations and is never lost.
    profile = AccountProfile.objects.get(user=creator)
    assert profile.platform_overrides.get('study.create') is False
    evidence('race.json', {'outcomes': outcomes, 'study_exists': exists,
                           'revocation_applied': True})


@pytest.mark.django_db(transaction=True)
def test_creation_and_revocation_race_is_serialized(tmp_path, evidence):
    if os.environ.get('GEP_R02CR_RACE_WORKER') == '1':
        _run_race(evidence)
        return
    node = f'{Path(__file__).resolve()}::test_creation_and_revocation_race_is_serialized'
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', node],
        cwd=REPO_ROOT,
        env={**os.environ, 'GEP_TEST_DB_FILE': str(tmp_path / 'p03r02cr_race.sqlite3'),
             'GEP_R02CR_RACE_WORKER': '1'},
        capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    evidence('race_subprocess.json', {
        'exit': result.returncode,
        'tail': [line for line in result.stdout.strip().splitlines()[-2:] if line]})


# --- AC3: exact version routing on real entries ------------------------------

def _counts():
    return {'studies': Study.objects.count(), 'grants': Grant.objects.count(),
            'users': get_user_model().objects.count(),
            'profiles': AccountProfile.objects.count(), 'audits': Audit.objects.count(),
            'revision': Instance.objects.get(pk=1).governance_revision}


def test_unknown_version_is_refused_across_real_http_entries(db):
    owner = make_user('p03r02cr_unknown_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    member = make_user('p03r02cr_unknown_member')
    study = Study.objects.create(title='P03R02CR unknown-version study')
    # A legacy grant row would be listed by a silent v1 fallback.
    Grant.objects.create(user=member, study=study, action=VIEW, delegable=True)
    owner_client = login(owner.username)
    member_client = forced_client(member)

    # One real v2 preview is consumed before the version becomes unknown, so its
    # replay is part of the negative set.
    preview = owner_client.post('/users', {'op': 'matrix_preview', 'user_id': str(member.pk),
                                           'study_id': str(study.pk), 'visibility': '1',
                                           'action:build.upload': '1'})
    assert preview.status_code == 200
    preview_id = preview_id_of(preview)
    instance.refresh_from_db()
    consumed = owner_client.post('/users', {'op': 'matrix_commit',
                                            'preview_id': preview_id,
                                            'revision': str(instance.governance_revision),
                                            'password': OWNER_PASSWORD})
    assert consumed.status_code == 200

    instance.authorization_version = 3
    instance.save(update_fields=['authorization_version'])
    client = forced_client(owner)
    before = _counts()

    home = client.get('/')
    assert home.status_code == 200
    body = home.content.decode('utf-8')
    assert study.title not in body and 'name="title"' not in body
    assert study.title not in member_client.get('/').content.decode('utf-8')
    refused = client.post('/', {'title': 'P03R02CR unknown-created study'})
    assert refused.status_code in (403, 409)
    assert not Study.objects.filter(title='P03R02CR unknown-created study').exists()

    assert client.get('/users').status_code == 403
    for payload in ({'op': 'matrix_preview', 'user_id': str(member.pk),
                     'study_id': str(study.pk), 'visibility': '1'},
                    {'op': 'matrix_commit', 'preview_id': preview_id,
                     'revision': str(before['revision']), 'password': OWNER_PASSWORD},
                    {'op': 'create_temp', 'username': 'p03r02cr_unknown_temp',
                     'revision': str(before['revision']), 'password': OWNER_PASSWORD},
                    {'op': 'import_users_preview'},
                    {'op': 'platform_preview', 'username': owner.username,
                     'platform:accounts.view': '1'},
                    {'op': 'migration_diff'}):
        response = client.post('/users', payload)
        assert response.status_code in (403, 409), (payload, response.status_code)
    assert client.get(f'/users/templates/users').status_code == 403
    assert client.get(f'/users/templates/roster?study={study.pk}').status_code == 403

    assert client.get(f'/studies/{study.pk}').status_code == 403
    for payload in ({'op': 'configure', 'mode': 'id', 'max_sessions': '2'},
                    {'op': 'roster', 'roster': 'p03r02cr-unknown-id'},
                    {'op': 'recover_code', 'session_id': str(uuid.uuid4())},
                    {'op': 'invite', 'username': 'p03r02cr_unknown_invitee',
                     'actions': [VIEW], 'revision': str(before['revision'])}):
        response = client.post(f'/studies/{study.pk}', payload)
        assert response.status_code in (403, 409), (payload, response.status_code)
    assert client.post('/activate', {'token': 'p03r02cr-unknown-token'}).status_code == 409
    # Documented fixture change for U07 (2026-09-23): the request password must
    # satisfy the unified four-class rule so the unknown stored version is what
    # refuses the activation; the version-refusal assertion is unchanged.
    assert client.post('/activate-account', {'token': 'p03r02cr-unknown-token',
                                             'password': 'Synthetic-unknown-password-2026',
                                             'confirm': 'Synthetic-unknown-password-2026'}
                       ).status_code in (403, 409)
    assert client.post('/v1/admin/exports', {'study_id': str(study.pk)},
                       content_type='application/json').status_code == 403

    after = _counts()
    assert after == before, (before, after)
    assert Study.objects.get(pk=study.pk).mode == 'anonymous'
    assert Instance.objects.get(pk=1).authorization_version == 3
    # The consumed preview was not replayed and the stored selection is unchanged.
    assert set(stored_overrides(member)[str(study.pk)]) == {VIEW, 'build.upload'}
    # A malformed stored value is refused the same way, never read as v1 or v2.
    # The write is raw SQL on purpose: the ORM would coerce or reject the value
    # before it ever reaches storage, and the kernel must fail closed on a row
    # that really is malformed.
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("UPDATE core_instance SET authorization_version = 'future' WHERE id = 1")
    instance.refresh_from_db()
    assert instance.authorization_version == 'future'
    assert access.authorization_version(instance) is None
    malformed = client.post('/', {'title': 'P03R02CR malformed-version study'})
    assert malformed.status_code in (403, 409)
    assert not Study.objects.filter(title='P03R02CR malformed-version study').exists()


def test_v1_boundary_is_unchanged_and_unknown_never_downgrades(db):
    owner = make_user('p03r02cr_v1_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=1)
    AccountProfile.objects.filter(user=owner).update(policy_version=1)
    peer = make_user('p03r02cr_v1_peer', role='admin')
    AccountProfile.objects.filter(user=peer).update(policy_version=1)
    client = login(owner.username)

    created = client.post('/', {'title': 'P03R02CR v1 study'})
    assert created.status_code == 302
    study = Study.objects.get(title='P03R02CR v1 study')
    rows = {row.action: row.delegable for row in Grant.objects.filter(user=owner, study=study)}
    assert set(rows) == set(access.ACTIONS) and all(rows.values())
    assert access.allowed(owner, study, 'member.manage') is True

    # A v1 Admin never creates studies and the legacy invitation entry still works.
    peer_client = login(peer.username)
    assert peer_client.post('/', {'title': 'P03R02CR v1 peer study'}).status_code == 403
    revision = Instance.objects.get(pk=1).governance_revision
    invited = client.post(f'/studies/{study.pk}', {
        'op': 'invite', 'username': 'p03r02cr_v1_invitee', 'actions': [VIEW],
        'revision': str(revision)})
    assert invited.status_code == 200
    assert access.allowed_platform(peer, 'accounts.manage_admin') is True

    # The same stored rows with an unknown version are refused, not downgraded.
    Instance.objects.filter(pk=1).update(authorization_version=0)
    before = _counts()
    assert client.post('/', {'title': 'P03R02CR v1 downgrade study'}).status_code in (403, 409)
    assert client.post(f'/studies/{study.pk}', {
        'op': 'invite', 'username': 'p03r02cr_v1_second', 'actions': [VIEW],
        'revision': str(revision)}).status_code in (403, 409)
    assert _counts() == before


# --- real Chrome: hidden study, restricted creation, revocation -------------

@pytest.fixture
def chrome_world(db):
    owner = make_user('p03r02cr_chrome_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    hidden = Study.objects.create(title='P03R02CR chrome hidden study')
    open_study = Study.objects.create(title='P03R02CR chrome open study')
    default_admin = make_user('p03r02cr_chrome_default_admin', role='admin')
    restricted = make_user('p03r02cr_chrome_restricted_admin', role='admin', future=[],
                           overrides={hidden: [], open_study: []})
    return {'owner': owner, 'instance': instance, 'hidden': hidden,
            'open': open_study, 'default_admin': default_admin, 'restricted': restricted}


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')
def test_actual_chrome_hidden_study_creation_and_revocation_journey(
        live_server, chrome_world, evidence, run_chrome_tokens):
    world = chrome_world
    env = dict(os.environ, GEP_TEST_BASE=live_server.url, GEP_OWNER_PASSWORD=OWNER_PASSWORD,
               GEP_HIDDEN_STUDY=str(world['hidden'].pk), GEP_OPEN_STUDY=str(world['open'].pk),
               GEP_DEFAULT_ADMIN=str(world['default_admin'].pk),
               GEP_RESTRICTED_ADMIN=str(world['restricted'].pk))
    result, reported = run_chrome_tokens(SCRIPT, env, timeout=240)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert reported == []
    observations = json.loads(re.search(r'P03R02CR_OBSERVATIONS (\{.*\})', result.stdout).group(1))
    assert observations['owner_hid_study'] is True
    assert observations['admin_home_hidden_absent'] is True
    assert observations['admin_home_open_present'] is True
    assert observations['admin_selector_hidden_absent'] is True
    assert observations['admin_selector_total'] == 1
    assert observations['restricted_created_and_entered'] is True
    assert observations['revoked_direct_request'] == 403
    assert observations['revoked_home_absent'] is True
    assert observations['page_errors'] == []

    created = Study.objects.get(title='P03R02CR chrome created study')
    assert access.allowed(world['restricted'], created, VIEW) is False
    assert str(created.pk) not in access.manageable_study_ids(world['restricted'])
    assert created.creator_principal_id is not None
    evidence('chrome_journey.json', observations)


SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const hiddenStudy=process.env.GEP_HIDDEN_STUDY, openStudy=process.env.GEP_OPEN_STUDY;
const defaultAdmin=process.env.GEP_DEFAULT_ADMIN, restrictedAdmin=process.env.GEP_RESTRICTED_ADMIN;
const observations={}, errors=[];
async function signIn(username){
  const context=await browser.newContext();
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push(username+':'+e.message));
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(ownerPassword);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  return page;
}
async function hideStudy(page,accountId,studyId){
  const row=page.locator(`[data-matrix-row="${accountId}-${studyId}"]`);
  await expect(row).toBeVisible();
  await row.locator('[name=visibility]').uncheck();
  await row.locator('button').click();
  await page.waitForLoadState('load');
  const preview=page.locator('[data-preview="matrix"]');
  await expect(preview).toBeVisible();
  await preview.locator('[name=password]').fill(ownerPassword);
  await preview.locator('button').click();
  await page.waitForLoadState('load');
}
try {
  const owner=await signIn('p03r02cr_chrome_owner');
  await owner.goto(base+'/users');
  await hideStudy(owner,defaultAdmin,hiddenStudy);
  observations.owner_hid_study = await owner.locator('[data-preview="matrix"]').count() === 0;

  // The default Admin's home and selector never show the hidden study.
  const admin=await signIn('p03r02cr_chrome_default_admin');
  await admin.goto(base+'/');
  observations.admin_home_hidden_absent =
    await admin.locator(`[data-study-card="${hiddenStudy}"]`).count() === 0;
  observations.admin_home_open_present =
    await admin.locator(`[data-study-card="${openStudy}"]`).count() === 1;
  await admin.goto(base+'/users');
  const adminUsers=(await admin.content());
  observations.admin_selector_hidden_absent = !adminUsers.includes('P03R02CR chrome hidden study');
  observations.admin_selector_total =
    Number(await admin.locator('[data-configure-total]').first().getAttribute('data-configure-total'));

  // The restricted Admin creates its own study and enters it normally.
  const restricted=await signIn('p03r02cr_chrome_restricted_admin');
  await restricted.goto(base+'/');
  await restricted.locator('[name=title]').fill('P03R02CR chrome created study');
  await restricted.locator('form:has([name=title]) button').click();
  await restricted.waitForURL(/\/studies\/[0-9a-f-]{36}$/);
  const createdUrl=restricted.url();
  const createdStudy=createdUrl.split('/').pop();
  observations.restricted_created_and_entered =
    (await restricted.locator('h1').innerText()).includes('P03R02CR chrome created study')
    && await restricted.locator('[data-error]').count() === 0;

  // The Owner revokes that study; the creator must not recover it.
  await owner.goto(base+'/users');
  await hideStudy(owner,restrictedAdmin,createdStudy);
  await restricted.goto(base+'/');
  observations.revoked_home_absent =
    await restricted.locator(`[data-study-card="${createdStudy}"]`).count() === 0;
  const refused=await restricted.goto(createdUrl);
  observations.revoked_direct_request=refused.status();
  await restricted.goto(base+'/');
  observations.revoked_home_absent =
    observations.revoked_home_absent
    && await restricted.locator(`[data-study-card="${createdStudy}"]`).count() === 0;

  observations.page_errors=errors;
  expect(errors).toEqual([]);
  console.log('P03R02CR_OBSERVATIONS '+JSON.stringify(observations));
} finally {await browser.close();}
'''
