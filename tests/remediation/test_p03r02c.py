"""P03R02C evidence: every permission entry switches to the one v2 kernel under a
real v2 instance, the v1 route keeps its old boundary, and the Owner-confirmed
migration preview agrees with the real v1/v2 routes.

The expected side of every check is the R00 contract (2026-09-23, sections A/B)
and explicit synthetic inputs, never the implementation under test:

- a finite study catalog (12 business actions) and platform catalog (7 actions);
  ``member.manage`` / ``permission.delegate`` have no v2 meaning, unknown
  actions are refused and the legacy ``delegable`` flag never authorizes;
- platform = role default + explicit boolean overrides; a study = the complete
  explicit override, else the Admin bound/template, else the user's grants;
  without ``study.view`` the result is empty;
- an account edit needs the target role's lifecycle capability, the actor's own
  view + configure there, and the whole-account before/after takeover comparison
  over every study, so a B difference can never overwrite A and using an action
  never equals managing it;
- the migration difference/projection must be computed from the *real* v1 route
  (an Owner without grants could not view a study) and the previewed final
  strategy must equal the real v2 route after confirmation;
- the static inventory below maps every direct Grant/delegable/role read or
  write in ``server/`` to the routing test that proves its v2 behavior, so a new
  bypass fails the scan instead of passing unnoticed.
"""
import ast
import re
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from core import access, accounts, authorization, governance_migration, importers, permissions
from core.models import (AccountInvitation, AccountProfile, Audit, Grant,
                         Instance, Invitation, PermissionPreview, Principal, Study)
from core.protocol import Rejected

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = 'synthetic-p03r02c-owner-password'
# Documented fixture change for U07 (2026-09-23): this value is *set* through
# activation, so it must satisfy the unified four-class researcher-password rule
# before the activation-specific refusal is reached; every negative assertion
# (the narrowed issuer bound refuses the activation) is unchanged.
ACTIVATION_PASSWORD = 'Synthetic-new-admin-password-2026'
VIEW = 'study.view'
CONFIGURE = 'study.configure'
STUDY_V2 = (
    'study.view', 'study.configure', 'build.upload', 'build.preview',
    'release.approve_pilot', 'recruitment.manage', 'data.export_raw',
    'identity_mapping.read', 'session.view', 'session.recover',
    'audit.view', 'study.delete',
)
PLATFORM_V2 = (
    'study.create', 'accounts.view', 'accounts.create_user',
    'accounts.manage_user', 'accounts.create_admin', 'accounts.manage_admin',
    'accounts.delete_admin',
)
ADMIN_DEFAULT_PLATFORM = {'study.create', 'accounts.view',
                          'accounts.create_user', 'accounts.manage_user'}


# --- static call inventory --------------------------------------------------

FACT_CALLS = {'effective_role', 'is_account_administrator', 'dominates',
              'delegable_authority', 'manageable_actions'}
# Every file that reads or writes a legacy authorization fact directly, and the
# routing test(s) in this module that prove its v2 behavior. A fact appearing in
# a new file, or a new fact in a listed file, fails the scan.
ROUTING_INVENTORY = {
    'server/core/access.py': {
        'facts': ['Grant.objects', 'delegable', 'delegable_authority',
                  'dominates', 'effective_role'],
        'tests': ['test_v1_route_boundary_is_unchanged_and_v2_is_kernel_only',
                  'test_matrix_preview_real_requests_all_roles_targets_studies',
                  'test_v2_data_and_governance_entries_ignore_caller_policy_facts']},
    'server/core/accounts.py': {
        'facts': ['Grant.objects', 'delegable', 'effective_role'],
        'tests': ['test_activation_revalidates_the_bound_and_the_issuer',
                  'test_legacy_admin_peer_lifecycle_is_removed_not_preserved']},
    'server/core/governance_migration.py': {
        'facts': ['Grant.objects'],
        'tests': ['test_migration_http_journey_agrees_with_real_v1_and_v2_routes']},
    'server/core/gui.py': {
        'facts': ['Grant.objects', 'delegable'],
        'tests': ['test_legacy_member_and_invitation_entries_refuse_on_v2',
                  'test_study_visibility_and_creation_use_stored_policy']},
    'server/core/importers.py': {
        'facts': ['Grant.objects', 'delegable', 'dominates',
                  'manageable_actions'],
        'tests': ['test_excel_import_entry_routes_by_version_v2',
                  'test_excel_import_entry_v1_keeps_the_owner_only_admin_rule']},
    'server/core/permissions.py': {
        'facts': ['Grant.objects', 'delegable', 'delegable_authority',
                  'dominates', 'is_account_administrator', 'manageable_actions'],
        'tests': ['test_matrix_preview_real_requests_all_roles_targets_studies',
                  'test_preview_expiry_and_role_change_are_stale',
                  'test_revoked_scope_rejects_commit_and_replay']},
    'server/core/ui.py': {
        'facts': ['effective_role', 'is_account_administrator'],
        'tests': ['test_ui_role_read_is_display_only']},
}


def scan_facts(path):
    """Direct legacy authorization facts inside one Python module (AST only)."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name in FACT_CALLS:
                found.add(name)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == 'Grant' and node.attr == 'objects':
            found.add('Grant.objects')
        if isinstance(node, ast.Attribute) and node.attr == 'delegable':
            found.add('delegable')
        if isinstance(node, ast.Constant) and node.value == 'delegable':
            found.add('delegable')
        if isinstance(node, ast.keyword) and node.arg == 'delegable':
            found.add('delegable')
    return found


def test_static_inventory_maps_every_legacy_fact_to_a_routing_test(evidence):
    scanned = {}
    for path in sorted((REPO_ROOT / 'server').rglob('*.py')):
        if '__pycache__' in path.parts or 'migrations' in path.parts:
            continue
        facts = scan_facts(path)
        if facts:
            scanned[str(path.relative_to(REPO_ROOT))] = sorted(facts)
    expected = {path: sorted(entry['facts']) for path, entry in ROUTING_INVENTORY.items()}
    assert scanned == expected
    for path, entry in ROUTING_INVENTORY.items():
        assert entry['tests']
        for name in entry['tests']:
            assert callable(globals().get(name)), f'{path} maps to missing test {name}'
    evidence('static_inventory.json', {'inventory': scanned})


# --- shared synthetic world -------------------------------------------------

def make_user(username, *, password=OWNER_PASSWORD):
    user = get_user_model().objects.create_user(username, password=password)
    Principal.objects.create(user=user)
    return user


def add_profile(user, role='user', *, studies=None, future=None, platform=None,
                policy_version=2):
    return AccountProfile.objects.create(
        user=user, role=role, policy_version=policy_version,
        platform_overrides=dict(platform or {}), study_overrides=dict(studies or {}),
        future_study_actions=future)


def login(username, password=OWNER_PASSWORD):
    client = Client()
    response = client.post('/login', {'username': username, 'password': password})
    assert response.status_code == 302 and response.url == '/'
    return client


def preview_id_of(response):
    body = response.content.decode('utf-8')
    match = re.search(r'name="preview_id" value="([0-9a-f-]{36})"', body)
    assert match, 'the preview page must carry the confirmation preview id'
    return match.group(1)


def stored_overrides(user):
    stored = AccountProfile.objects.get(user=user).study_overrides or {}
    return {key: sorted(value) for key, value in stored.items()}


@pytest.fixture
def v2_world(db):
    owner = make_user('p03r02c_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    add_profile(owner)  # the Owner is derived from the instance pointer only
    study_a = Study.objects.create(title='P03R02C study A')
    study_b = Study.objects.create(title='P03R02C study B')
    study_c = Study.objects.create(title='P03R02C study C')
    key_a, key_b = str(study_a.pk), str(study_b.pk)
    peer = make_user('p03r02c_peer_admin')
    add_profile(peer, 'admin',
                studies={key_a: sorted(STUDY_V2), key_b: [VIEW, CONFIGURE, 'build.upload']},
                future=[])
    scoped = make_user('p03r02c_scoped_admin')
    add_profile(scoped, 'admin',
                studies={key_a: [VIEW, CONFIGURE, 'build.upload'], key_b: [VIEW, CONFIGURE]},
                future=[])
    readonly = make_user('p03r02c_readonly_admin')
    add_profile(readonly, 'admin', studies={key_a: [VIEW], key_b: []}, future=[])
    target_admin = make_user('p03r02c_target_admin')
    add_profile(target_admin, 'admin',
                studies={key_a: [VIEW, 'data.export_raw']}, future=[])
    member = make_user('p03r02c_member')
    add_profile(member, 'user', studies={key_a: [VIEW, 'build.upload']})
    export_user = make_user('p03r02c_export_user')
    add_profile(export_user, 'user', studies={key_a: [VIEW, 'data.export_raw']})
    platform_user = make_user('p03r02c_platform_user')
    add_profile(platform_user, 'user', platform={'accounts.view': True})
    default_admin = make_user('p03r02c_default_admin')
    add_profile(default_admin, 'admin')  # fixed v2 template on every study
    return {'owner': owner, 'instance': instance, 'study_a': study_a, 'study_b': study_b,
            'study_c': study_c,
            'peer': peer, 'scoped': scoped, 'readonly': readonly,
            'target_admin': target_admin, 'member': member, 'export_user': export_user,
            'platform_user': platform_user, 'default_admin': default_admin}


def selection_payload(target, study, spec=(), hide=False):
    """A complete matrix selection: the current stored set plus +action/-action."""
    study_id = str(study.pk)
    if hide:
        return {'user_id': str(target.pk), 'study_id': study_id, 'visibility': '0'}
    data = {'user_id': str(target.pk), 'study_id': study_id, 'visibility': '1'}
    current = set(access.configured_study_actions(access.canonical_policy(target), study_id))
    for token in spec:
        if token.startswith('+'):
            current.add(token[1:])
        elif token.startswith('-'):
            current.discard(token[1:])
        else:
            raise AssertionError(token)
    for action in sorted(current):
        if action != VIEW:
            data['action:' + action] = '1'
    return data


def matrix_preview(client, target, study, spec=(), hide=False):
    data = selection_payload(target, study, spec, hide)
    data['op'] = 'matrix_preview'
    return client.post('/users', data)


def matrix_commit(client, preview_id, revision):
    return client.post('/users', {'op': 'matrix_commit', 'preview_id': preview_id,
                                  'revision': str(revision), 'password': OWNER_PASSWORD})


def assert_users_rejected(response, status, code):
    """A /users rejection renders the real page with the mapped error message.

    Rejections raised before the op dispatcher (for example a missing
    ``accounts.view``) still answer as the endpoint's JSON envelope.
    """
    assert response.status_code == status, (status, response.content[:400])
    if response['Content-Type'].startswith('application/json'):
        assert response.json()['code'] == code
    else:
        from core import gui_accounts

        assert response.context['error'] == gui_accounts.message_for(code, 'zh'), \
            response.context['error']


# --- AC1: real v2 requests across roles, targets and studies ----------------

MATRIX_CASES = [
    # (actor, target, study, spec, expected status, expected code)
    ('owner', 'member', 'study_a', ['+session.recover'], 200, None),
    ('owner', 'peer', 'study_a', ['-build.upload'], 200, None),
    ('owner', 'target_admin', 'study_a', ['+build.upload'], 200, None),
    ('owner', 'platform_user', 'study_a', ['+build.upload'], 200, None),
    ('owner', 'member', 'study_b', ['+build.upload'], 200, None),
    ('owner', 'owner', 'study_a', [], 403, 'owner_protected'),
    ('peer', 'peer', 'study_a', ['+build.upload'], 409, 'self_target'),
    ('peer', 'owner', 'study_a', [], 403, 'owner_protected'),
    ('peer', 'member', 'study_a', ['+data.export_raw'], 200, None),
    ('peer', 'target_admin', 'study_a', ['+build.upload'], 403, 'forbidden'),
    ('peer', 'scoped', 'study_a', ['-build.upload'], 403, 'forbidden'),
    ('scoped', 'member', 'study_a', ['+data.export_raw'], 403, 'delegation_forbidden'),
    ('scoped', 'member', 'study_b', ['+build.upload'], 403, 'delegation_forbidden'),
    ('scoped', 'target_admin', 'study_a', ['+build.upload'], 403, 'forbidden'),
    ('readonly', 'member', 'study_a', ['+build.upload'], 403, 'delegation_forbidden'),
    ('readonly', 'target_admin', 'study_a', ['+build.upload'], 403, 'forbidden'),
    ('target_admin', 'member', 'study_a', ['+build.upload'], 403, 'delegation_forbidden'),
    ('platform_user', 'member', 'study_a', ['+build.upload'], 403, 'forbidden'),
    ('member', 'platform_user', 'study_a', ['+build.upload'], 403, 'forbidden'),
    ('default_admin', 'target_admin', 'study_a', ['+build.upload'], 403, 'forbidden'),
]

EXPECTED_OVERRIDES = {
    'peer': {'study_a': sorted(STUDY_V2), 'study_b': [VIEW, CONFIGURE, 'build.upload']},
    'scoped': {'study_a': [VIEW, CONFIGURE, 'build.upload'], 'study_b': [VIEW, CONFIGURE]},
    'readonly': {'study_a': [VIEW], 'study_b': []},
    'target_admin': {'study_a': [VIEW, 'data.export_raw']},
    'member': {'study_a': [VIEW, 'build.upload']},
    'export_user': {'study_a': [VIEW, 'data.export_raw']},
    'owner': {}, 'platform_user': {}, 'default_admin': {},
}


def override_for(world, user, study):
    return stored_overrides(user).get(str(world[study].pk), [])


def assert_world_policy_untouched(world):
    for name, expected in EXPECTED_OVERRIDES.items():
        keyed = {str(world[study].pk): sorted(actions) for study, actions in expected.items()}
        assert stored_overrides(world[name]) == keyed, name
    assert Grant.objects.count() == 0


def test_matrix_preview_real_requests_all_roles_targets_studies(v2_world, evidence):
    clients = {name: login(user.username) for name, user in v2_world.items()
               if name in EXPECTED_OVERRIDES}
    observed = []
    for actor, target, study, spec, status, code in MATRIX_CASES:
        response = matrix_preview(clients[actor], v2_world[target], v2_world[study], spec)
        label = f'{actor}->{target}@{study}{spec}'
        if code is not None:
            assert_users_rejected(response, status, code)
        else:
            assert response.status_code == status, (label, response.status_code, response.content[:400])
            assert b'data-preview="matrix"' in response.content, label
        observed.append({'case': label, 'status': response.status_code, 'code': code})
    # A preview never writes a stored policy or a legacy grant row.
    assert_world_policy_untouched(v2_world)
    evidence('matrix_preview_table.json', {'cases': observed})


def test_matrix_commit_writes_the_complete_override_and_keeps_legacy_rows(v2_world, evidence):
    owner_client = login(v2_world['owner'].username)
    peer_client = login(v2_world['peer'].username)
    member = v2_world['member']
    instance = v2_world['instance']
    # A v2 Owner write stores the complete selected list for exactly one study.
    preview = matrix_preview(owner_client, member, v2_world['study_a'], ['+session.recover'])
    assert preview.status_code == 200
    instance.refresh_from_db()
    committed = matrix_commit(owner_client, preview_id_of(preview), instance.governance_revision)
    assert committed.status_code == 200
    assert stored_overrides(member) == {
        str(v2_world['study_a'].pk): sorted([VIEW, 'build.upload', 'session.recover'])}
    # Legacy grant rows and the legacy flags are not rewritten by a v2 write.
    assert Grant.objects.count() == 0
    audit = Audit.objects.filter(action='permission.matrix_changed').latest('id')
    key_a = str(v2_world['study_a'].pk)
    assert audit.before['policy']['studies'][key_a] == sorted([VIEW, 'build.upload'])
    assert audit.after['policy']['studies'][key_a] == sorted(
        [VIEW, 'build.upload', 'session.recover'])

    # An Admin that holds the study scope and the lifecycle action may still edit
    # an ordinary account.
    preview = matrix_preview(peer_client, member, v2_world['study_a'], ['+data.export_raw'])
    assert preview.status_code == 200
    instance.refresh_from_db()
    committed = matrix_commit(peer_client, preview_id_of(preview), instance.governance_revision)
    assert committed.status_code == 200
    assert stored_overrides(member) == {key_a: sorted(
        [VIEW, 'build.upload', 'session.recover', 'data.export_raw'])}
    assert set(override_for(v2_world, v2_world['peer'], 'study_a')) == set(STUDY_V2)
    evidence('matrix_commit.json', {'member_overrides': stored_overrides(member),
                                    'grants_untouched': Grant.objects.count() == 0})


def test_admin_cannot_write_self_or_owner_and_default_cannot_manage_admins(v2_world):
    peer_client = login(v2_world['peer'].username)
    self_response = matrix_preview(peer_client, v2_world['peer'], v2_world['study_a'],
                                   ['+build.upload'])
    assert_users_rejected(self_response, 409, 'self_target')
    owner_client = login(v2_world['owner'].username)
    for client in (owner_client, peer_client):
        response = matrix_preview(client, v2_world['owner'], v2_world['study_a'],
                                  ['+build.upload'])
        assert_users_rejected(response, 403, 'owner_protected')
    # The v2 Admin default carries no accounts.manage_admin: an Admin target is
    # refused by default, exactly as the contract says (nothing silently kept).
    assert 'accounts.manage_admin' not in ADMIN_DEFAULT_PLATFORM
    default_client = login(v2_world['default_admin'].username)
    response = matrix_preview(default_client, v2_world['target_admin'],
                              v2_world['study_a'], ['+build.upload'])
    assert_users_rejected(response, 403, 'forbidden')
    # A preview-only table entry never bypasses the server side: the same
    # default Admin still cannot reset the peer Admin through the lifecycle op.
    response = default_client.post('/users', {
        'op': 'reset_password', 'username': v2_world['target_admin'].username,
        'revision': str(v2_world['instance'].governance_revision),
        'password': OWNER_PASSWORD})
    assert_users_rejected(response, 403, 'forbidden')


def test_owner_platform_switch_is_owner_only_and_stays_in_scope(v2_world, evidence):
    owner_client = login(v2_world['owner'].username)
    peer_client = login(v2_world['peer'].username)
    instance = v2_world['instance']
    peer = v2_world['peer']
    target_admin = v2_world['target_admin']

    # Only the Owner writes the finite platform switches; an Admin is refused
    # before any write and cannot grant them to itself or anyone else.
    refused = peer_client.post('/users', {'op': 'platform_preview', 'username': peer.username,
                                          'platform:accounts.manage_admin': '1'})
    assert_users_rejected(refused, 403, 'owner_only')
    self_target = owner_client.post('/users', {'op': 'platform_preview',
                                               'username': v2_world['owner'].username,
                                               'platform:accounts.manage_admin': '1'})
    assert_users_rejected(self_target, 403, 'owner_protected')
    unknown = owner_client.post('/users', {'op': 'platform_preview', 'username': peer.username,
                                           'platform:accounts.bogus': '1'})
    assert unknown.status_code == 422

    preview = owner_client.post('/users', {'op': 'platform_preview', 'username': peer.username,
                                           'platform:accounts.manage_admin': '1'})
    assert preview.status_code == 200 and b'data-preview="platform"' in preview.content
    instance.refresh_from_db()
    committed = owner_client.post('/users', {'op': 'platform_commit',
                                             'preview_id': preview_id_of(preview),
                                             'revision': str(instance.governance_revision),
                                             'password': OWNER_PASSWORD})
    assert committed.status_code == 200
    peer_profile = AccountProfile.objects.get(user=peer)
    assert peer_profile.platform_overrides == {'accounts.manage_admin': True}

    # With the switch the peer may maintain the other Admin inside its own study
    # scope and only while the whole-account comparison still passes.
    instance.refresh_from_db()
    allowed = matrix_preview(peer_client, target_admin, v2_world['study_a'], ['+build.upload'])
    assert allowed.status_code == 200
    instance.refresh_from_db()
    committed = matrix_commit(peer_client, preview_id_of(allowed), instance.governance_revision)
    assert committed.status_code == 200
    assert override_for(v2_world, target_admin, 'study_a') == sorted(
        [VIEW, 'data.export_raw', 'build.upload'])
    # The switch is never transferable, and a study where the peer holds nothing
    # stays out of reach.
    still_refused = peer_client.post('/users', {
        'op': 'platform_preview', 'username': peer.username,
        'platform:accounts.create_admin': '1'})
    assert_users_rejected(still_refused, 403, 'owner_only')
    hidden = matrix_preview(peer_client, v2_world['member'], v2_world['study_c'],
                            ['+build.upload'])
    assert_users_rejected(hidden, 403, 'delegation_forbidden')
    evidence('platform_switch.json', {
        'owner_switch_written': peer_profile.platform_overrides,
        'target_admin_actions': override_for(v2_world, target_admin, 'study_a')})


# --- AC2: negative boundaries ----------------------------------------------

def test_readonly_use_only_and_lifecycle_boundaries(v2_world):
    readonly_client = login(v2_world['readonly'].username)
    target_client = login(v2_world['target_admin'].username)
    member = v2_world['member']
    revision = v2_world['instance'].governance_revision
    # A read-only Admin (view only) cannot assign any action on that study.
    response = matrix_preview(readonly_client, member, v2_world['study_a'], ['+build.upload'])
    assert_users_rejected(response, 403, 'delegation_forbidden')
    # Holding an action (data.export_raw) without study.configure is not managing:
    # the holder cannot assign it to others...
    response = matrix_preview(target_client, member, v2_world['study_a'], ['+build.upload'])
    assert_users_rejected(response, 403, 'delegation_forbidden')
    # ... and cannot reset an account that holds a study action they do not
    # dominate.
    response = readonly_client.post('/users', {'op': 'reset_password',
                                               'username': member.username,
                                               'revision': str(revision),
                                               'password': OWNER_PASSWORD})
    assert_users_rejected(response, 403, 'higher_privilege_target')
    response = target_client.post('/users', {'op': 'reset_password',
                                             'username': member.username,
                                             'revision': str(revision),
                                             'password': OWNER_PASSWORD})
    assert_users_rejected(response, 403, 'higher_privilege_target')
    assert stored_overrides(member) == {
        str(v2_world['study_a'].pk): sorted([VIEW, 'build.upload'])}


def test_b_edit_requires_a_dominance_and_never_overwrites_a(v2_world, evidence):
    scoped_client = login(v2_world['scoped'].username)
    peer_client = login(v2_world['peer'].username)
    target = v2_world['export_user']
    study_a, study_b = v2_world['study_a'], v2_world['study_b']
    key_a, key_b = str(study_a.pk), str(study_b.pk)
    instance = v2_world['instance']
    before_a = stored_overrides(target)[key_a]

    # The actor holds a legal scope on B but cannot dominate the target's A
    # permission: the B difference is refused and nothing is written.
    refused = matrix_preview(scoped_client, target, study_b, [])
    assert_users_rejected(refused, 403, 'higher_privilege_target')
    assert key_b not in stored_overrides(target)

    # With full dominance on A the B change is legal and leaves A untouched.
    instance.refresh_from_db()
    allowed = matrix_preview(peer_client, target, study_b, ['+build.upload'])
    assert allowed.status_code == 200
    instance.refresh_from_db()
    committed = matrix_commit(peer_client, preview_id_of(allowed), instance.governance_revision)
    assert committed.status_code == 200
    stored = stored_overrides(target)
    assert stored[key_a] == before_a
    assert stored[key_b] == sorted([VIEW, 'build.upload'])
    audit = Audit.objects.filter(action='permission.matrix_changed').latest('id')
    assert audit.before['policy']['studies'][key_a] == before_a
    assert audit.after['policy']['studies'][key_a] == before_a
    evidence('b_edit.json', {'a_before': before_a, 'a_after': stored[key_a],
                             'b_after': stored[key_b]})


def test_preview_expiry_and_role_change_are_stale(v2_world):
    owner_client = login(v2_world['owner'].username)
    member = v2_world['member']
    study_a = v2_world['study_a']
    key_a = str(study_a.pk)
    instance = v2_world['instance']

    preview = matrix_preview(owner_client, member, study_a, ['+session.recover'])
    assert preview.status_code == 200
    preview_id = preview_id_of(preview)
    PermissionPreview.objects.filter(pk=preview_id).update(
        expires_at=timezone.now() - timedelta(seconds=1))
    instance.refresh_from_db()
    expired = matrix_commit(owner_client, preview_id, instance.governance_revision)
    assert_users_rejected(expired, 409, 'preview_expired')
    assert 'session.recover' not in stored_overrides(member).get(key_a, [])

    # A role change is part of the bound subject state: the same preview is stale.
    preview = matrix_preview(owner_client, member, study_a, ['+session.recover'])
    assert preview.status_code == 200
    preview_id = preview_id_of(preview)
    instance.refresh_from_db()
    role_change = owner_client.post('/users', {'op': 'set_role', 'username': member.username,
                                               'role': 'admin',
                                               'revision': str(instance.governance_revision),
                                               'password': OWNER_PASSWORD})
    assert role_change.status_code == 200
    instance.refresh_from_db()
    stale = matrix_commit(owner_client, preview_id, instance.governance_revision)
    assert_users_rejected(stale, 409, 'preview_stale')
    assert 'session.recover' not in stored_overrides(member).get(key_a, [])


def test_revoked_scope_rejects_commit_and_replay(v2_world):
    owner_client = login(v2_world['owner'].username)
    peer_client = login(v2_world['peer'].username)
    member = v2_world['member']
    study_a = v2_world['study_a']
    key_a = str(study_a.pk)
    instance = v2_world['instance']

    # Revocation of the actor's study scope after the preview: refused, no write.
    preview = matrix_preview(peer_client, member, study_a, ['+data.export_raw'])
    assert preview.status_code == 200
    preview_id = preview_id_of(preview)
    instance.refresh_from_db()
    revoke = matrix_preview(owner_client, v2_world['peer'], study_a, ['-study.configure'])
    assert revoke.status_code == 200
    instance.refresh_from_db()
    assert matrix_commit(owner_client, preview_id_of(revoke),
                         instance.governance_revision).status_code == 200
    assert CONFIGURE not in stored_overrides(v2_world['peer'])[key_a]
    instance.refresh_from_db()
    # The Owner's write bumped the instance revision, so the peer's preview is
    # stale before its authority is even compared: refused, nothing written.
    refused = matrix_commit(peer_client, preview_id, instance.governance_revision)
    assert_users_rejected(refused, 409, 'preview_stale')
    assert 'data.export_raw' not in stored_overrides(member).get(key_a, [])

    # Replay of a consumed preview returns the recorded result and never writes
    # again (the member revision stays).
    replay_preview = matrix_preview(owner_client, member, study_a, ['+session.view'])
    assert replay_preview.status_code == 200
    replay_id = preview_id_of(replay_preview)
    instance.refresh_from_db()
    first = matrix_commit(owner_client, replay_id, instance.governance_revision)
    assert first.status_code == 200
    revision_after_first = AccountProfile.objects.get(user=member).revision
    instance.refresh_from_db()
    second = matrix_commit(owner_client, replay_id, instance.governance_revision)
    assert second.status_code == 200
    assert AccountProfile.objects.get(user=member).revision == revision_after_first
    assert stored_overrides(member)[key_a] == sorted(
        [VIEW, 'build.upload', 'session.view'])

    # A revoked platform authority refuses even the replay of a consumed preview.
    instance.refresh_from_db()
    platform_preview = owner_client.post('/users', {'op': 'platform_preview',
                                                    'username': v2_world['peer'].username,
                                                    'platform:accounts.view': '0'})
    assert platform_preview.status_code == 200
    instance.refresh_from_db()
    assert owner_client.post('/users', {'op': 'platform_commit',
                                        'preview_id': preview_id_of(platform_preview),
                                        'revision': str(instance.governance_revision),
                                        'password': OWNER_PASSWORD}).status_code == 200
    instance.refresh_from_db()
    refused = matrix_commit(peer_client, replay_id, instance.governance_revision)
    assert_users_rejected(refused, 403, 'forbidden')


def test_audit_failure_rolls_back_matrix_and_platform_writes(v2_world, monkeypatch):
    owner = v2_world['owner']
    member = v2_world['member']
    study_a = v2_world['study_a']
    instance = v2_world['instance']
    member_before = stored_overrides(member)
    revision = instance.governance_revision
    row = permissions.preview_matrix(owner, {
        'user_id': str(member.pk), 'study_id': str(study_a.pk), 'visibility': '1',
        'action:session.view': '1'})

    def boom(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')

    monkeypatch.setattr(permissions.accounts, 'audit', boom)
    with pytest.raises(RuntimeError):
        permissions.commit_matrix(owner, OWNER_PASSWORD, row.pk)
    instance.refresh_from_db()
    assert instance.governance_revision == revision
    assert stored_overrides(member) == member_before
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 0

    # The platform switch write rolls back the same way.
    platform_row = permissions.preview_platform(
        owner, {'username': member.username, 'platform:accounts.view': '1'})
    platform_revision = instance.governance_revision
    with pytest.raises(RuntimeError):
        permissions.commit_platform(owner, OWNER_PASSWORD, platform_row.pk)
    monkeypatch.undo()
    instance.refresh_from_db()
    assert instance.governance_revision == platform_revision
    assert AccountProfile.objects.get(user=member).platform_overrides == {}


def test_activation_revalidates_the_bound_and_the_issuer(v2_world):
    owner = v2_world['owner']
    peer = v2_world['peer']
    revision = v2_world['instance'].governance_revision
    # A non-Owner Admin needs the Owner's explicit create_admin switch first.
    with pytest.raises(Rejected) as info:
        accounts.invite_account(peer, OWNER_PASSWORD, revision,
                                'p03r02c_restricted_admin', role='admin')
    assert info.value.code == 'admin_appointment_owner_only'
    profile = AccountProfile.objects.get(user=peer)
    profile.platform_overrides = {'accounts.create_admin': True}
    profile.save(update_fields=['platform_overrides'])
    invitation = accounts.invite_account(peer, OWNER_PASSWORD, revision,
                                        'p03r02c_restricted_admin', role='admin')
    from core.services import digest
    stored = AccountInvitation.objects.get(token_hash=digest(invitation['token']))
    assert stored.bound_policy is not None
    assert set(stored.bound_policy['study_overrides'][str(v2_world['study_a'].pk)]) == set(STUDY_V2)
    assert stored.bound_policy['future_study_actions'] == []

    # Narrowing the issuer's own study policy invalidates the pending bound: the
    # activation re-reads the issuer's *current* upper bound.
    peer_profile = AccountProfile.objects.get(user=peer)
    peer_profile.study_overrides = {str(v2_world['study_a'].pk): [VIEW, CONFIGURE, 'build.upload']}
    peer_profile.save(update_fields=['study_overrides'])
    with pytest.raises(Rejected) as info:
        accounts.activate_account(invitation['token'], ACTIVATION_PASSWORD, ACTIVATION_PASSWORD)
    assert info.value.code == 'activation_failed'
    assert not get_user_model().objects.filter(username='p03r02c_restricted_admin').exists()

    # Restoring the issuer's policy lets exactly that bound activate, and the
    # stored profile carries the finite bound (never the role default).
    peer_profile = AccountProfile.objects.get(user=peer)
    peer_profile.study_overrides = {str(v2_world['study_a'].pk): sorted(STUDY_V2),
                                    str(v2_world['study_b'].pk): [VIEW, CONFIGURE, 'build.upload']}
    peer_profile.save(update_fields=['study_overrides'])
    user = accounts.activate_account(invitation['token'], ACTIVATION_PASSWORD,
                                     ACTIVATION_PASSWORD)
    activated = AccountProfile.objects.get(user=user)
    assert activated.role == 'admin'
    assert set(activated.study_overrides[str(v2_world['study_a'].pk)]) == set(STUDY_V2)
    assert activated.future_study_actions == []


# --- AC3: legacy entries, imports, data routes, display-only reads ----------

def test_v1_route_boundary_is_unchanged_and_v2_is_kernel_only(db):
    owner = make_user('p03r02c_boundary_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=1)
    add_profile(owner, policy_version=1)
    user = make_user('p03r02c_boundary_user')
    add_profile(user, policy_version=1)
    study = Study.objects.create(title='P03R02C boundary study')
    for action in (VIEW, 'data.export_raw', 'member.manage'):
        Grant.objects.create(user=user, study=study, action=action, delegable=True)
    # The undecorated v1 route keeps its explicit-grant contract, including the
    # legacy-only action that has no v2 meaning.
    assert access.allowed(user, study, 'data.export_raw') is True
    assert access.allowed(user, study, 'member.manage') is True
    assert access.allowed(user, study, 'build.upload') is False
    assert access.resolve_study_actions(user, study) == frozenset(
        {VIEW, 'data.export_raw', 'member.manage'})
    # The explicit v2 route fails closed without a canonical policy: it never
    # falls back to the legacy delegable rows.
    assert access.allowed(user, study, 'data.export_raw', version=2) is False
    empty = authorization.SubjectPolicy('user')
    assert access.allowed(user, study, 'data.export_raw', version=2, policy=empty) is False
    # The same database after the switch: the old boundary is still reachable
    # through the explicit v1 route, but the stored instance routes to v2 and the
    # raw grant is not an authorization.
    instance.authorization_version = 2
    instance.save(update_fields=['authorization_version'])
    # The old boundary stays reachable only through the explicit v1 route...
    assert access.allowed(user, study, 'data.export_raw', version=1) is True
    # ... while the stored instance now uses the kernel: the same Grant row is
    # the ordinary user's v2 storage, but the legacy-only action has no v2
    # meaning and the delegable flag itself never widens anything.
    assert access.allowed(user, study, 'data.export_raw') is True
    assert access.allowed(user, study, 'member.manage') is False
    assert access.allowed(user, study, 'permission.delegate') is False
    assert access.allowed(user, study, 'build.upload') is False
    assert access.resolve_study_actions(user, study) == frozenset()  # no canonical policy
    policy = access.canonical_policy(user)
    assert set(access.resolve_study_actions(user, study, policy=policy)) == {
        VIEW, 'data.export_raw'}
    empty_user = make_user('p03r02c_boundary_empty')
    add_profile(empty_user, policy_version=1)
    Grant.objects.create(user=empty_user, study=study, action='build.upload', delegable=True)
    assert access.allowed(empty_user, study, 'build.upload') is False


def test_legacy_member_and_invitation_entries_refuse_on_v2(v2_world):
    owner_client = login(v2_world['owner'].username)
    study_a = v2_world['study_a']
    instance = v2_world['instance']
    grant_rows = Grant.objects.count()
    # The v1 study-member write entries refuse with an explicit upgrade conflict
    # instead of replaying a POST into the v2 storage.
    invite = owner_client.post(f'/studies/{study_a.pk}', {
        'op': 'invite', 'revision': str(instance.governance_revision),
        'username': 'p03r02c_invitee', 'actions': [VIEW]})
    assert invite.status_code == 409 and invite.json()['code'] == 'authorization_upgrade_required'
    revoke = owner_client.post(f'/studies/{study_a.pk}', {
        'op': 'revoke_member', 'revision': str(instance.governance_revision),
        'user_id': str(v2_world['member'].pk)})
    assert revoke.status_code == 409 and revoke.json()['code'] == 'authorization_upgrade_required'
    activate = owner_client.post('/activate', {'token': 'not-a-token'})
    assert activate.status_code == 409 and activate.json()['code'] == 'authorization_upgrade_required'
    assert Grant.objects.count() == grant_rows
    assert Invitation.objects.count() == 0


def test_v1_member_and_invitation_entries_keep_their_old_boundary(db):
    owner = make_user('p03r02c_v1_owner')
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=1)
    add_profile(owner, policy_version=1)
    study = Study.objects.create(title='P03R02C v1 study')
    for action in (VIEW, 'member.manage', 'permission.delegate'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    client = login(owner.username)
    instance = Instance.objects.get(pk=1)
    response = client.post(f'/studies/{study.pk}', {
        'op': 'invite', 'revision': str(instance.governance_revision),
        'username': 'p03r02c_v1_invitee', 'actions': [VIEW]})
    assert response.status_code == 200
    assert Invitation.objects.filter(study=study, consumed=False, revoked=False).count() == 1


def test_excel_import_entry_routes_by_version_v2(v2_world):
    member = v2_world['member']
    admin = v2_world['default_admin']
    study_a = v2_world['study_a']
    # The users-import normalizer is the same real entry the XLSX preview calls:
    # under v2 an Admin default may create ordinary users but never Admins, and
    # the Admin row is refused instead of silently mapped to the old role rule.
    workbook = _users_workbook([
        ['p03r02c_import_user', 'create', '', 'user', '', ''],
        ['p03r02c_import_admin', 'create', '', 'admin', '', ''],
        [member.username, 'update', '0', 'user', str(study_a.pk), 'study.view;build.upload'],
    ])
    rows, ops, errors = importers._normalize_users(admin, workbook)
    codes = {error['code'] for error in errors}
    assert codes == {'admin_appointment_owner_only'}
    assert any(op['operation'] == 'create' and op['role'] == 'user' for op in ops)
    assert any(op['operation'] == 'update' for op in ops)
    # The HTTP entry itself is gated by the stored platform policy.
    member_client = login(member.username)
    assert member_client.post('/users', {'op': 'import_users_preview'}).status_code == 403


def test_excel_import_entry_v1_keeps_the_owner_only_admin_rule(db):
    owner = make_user('p03r02c_import_v1_owner')
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=1)
    add_profile(owner, policy_version=1)
    admin = make_user('p03r02c_import_v1_admin')
    add_profile(admin, 'admin', policy_version=1)
    workbook = _users_workbook([
        ['p03r02c_import_user', 'create', '', 'user', '', ''],
        ['p03r02c_import_admin', 'create', '', 'admin', '', ''],
    ])
    _rows, ops, errors = importers._normalize_users(admin, workbook)
    assert {error['code'] for error in errors} == {'admin_appointment_owner_only'}
    assert [op['role'] for op in ops] == ['user']


def _users_workbook(rows):
    """The same synthetic XLSX the users-import preview consumes."""
    import io

    from openpyxl import Workbook

    from core import excel

    book = Workbook()
    sheet = book.active
    sheet.append(list(excel.USERS_HEADERS))
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_v2_data_and_governance_entries_ignore_caller_policy_facts(v2_world):
    member = v2_world['member']
    platform_user = v2_world['platform_user']
    study_a = v2_world['study_a']
    key_a = str(study_a.pk)
    member_client = login(member.username)
    # A caller-supplied policy/version/override in the request body or form never
    # authorizes: the stored canonical policy is the only source.
    forged = member_client.post('/v1/admin/exports',
                                {'study_id': key_a, 'version': 2,
                                 'policy': {'study_overrides': {key_a: list(STUDY_V2)}},
                                 'study_overrides': {key_a: list(STUDY_V2)}},
                                content_type='application/json')
    assert forged.status_code == 403
    # An imported/stored override does authorize through the same entry.
    profile = AccountProfile.objects.get(user=member)
    profile.study_overrides = {key_a: [VIEW, 'data.export_raw']}
    profile.save(update_fields=['study_overrides'])
    granted = member_client.post('/v1/admin/exports', {'study_id': key_a},
                                 content_type='application/json')
    assert granted.status_code == 201
    # accounts.view alone opens the page but never a write or a lifecycle op.
    platform_client = login(platform_user.username)
    assert platform_client.get('/users').status_code == 200
    assert platform_client.post('/users', {
        'op': 'matrix_preview', 'user_id': str(member.pk), 'study_id': key_a,
        'visibility': '1', 'action:build.upload': '1'}).status_code == 403
    assert platform_client.post('/users', {
        'op': 'create_temp', 'username': 'p03r02c_temp',
        'revision': str(v2_world['instance'].governance_revision),
        'password': OWNER_PASSWORD}).status_code == 403
    # A study-scoped write with forged extra fields is still the stored policy.
    configured = member_client.post(f'/studies/{study_a.pk}', {
        'op': 'configure', 'revision': '0',
        'platform_overrides': {'accounts.delete_admin': True},
        'study_overrides': {key_a: list(STUDY_V2)}})
    assert configured.status_code == 403


def test_study_visibility_and_creation_use_stored_policy(v2_world):
    member_client = login(v2_world['member'].username)
    home = member_client.get('/')
    assert home.status_code == 200
    assert (b'P03R02C study A' in home.content and b'P03R02C study B' not in home.content
            and b'P03R02C study C' not in home.content)
    # The v2 Admin default (no explicit override, no future bound) sees every
    # study; an explicit empty future bound restricts the next Admin.
    default_client = login(v2_world['default_admin'].username)
    default_home = default_client.get('/')
    assert b'P03R02C study A' in default_home.content and b'P03R02C study B' in default_home.content
    assert b'P03R02C study C' in default_home.content
    target_home = login(v2_world['target_admin'].username).get('/')
    assert (b'P03R02C study A' in target_home.content
            and b'P03R02C study B' not in target_home.content)

    # study.create comes from the stored platform policy: the user default holds
    # it, an explicit false override removes it, and the created study is stored
    # with the creator principal and the full business grant set once.
    created = member_client.post('/', {'title': 'P03R02C created by member'})
    assert created.status_code == 302
    study = Study.objects.get(title='P03R02C created by member')
    assert study.creator_principal is not None
    assert sorted(Grant.objects.filter(user=v2_world['member'], study=study)
                  .values_list('action', flat=True)) == sorted(STUDY_V2)
    restrained = make_user('p03r02c_no_create')
    add_profile(restrained, platform={'study.create': False})
    refused = login(restrained.username).post('/', {'title': 'P03R02C refused'})
    assert refused.status_code == 403 and refused.json()['code'] == 'create_forbidden'


def test_ui_role_read_is_display_only(v2_world):
    # ui.context renders navigation from the role string, but every entry it
    # links to is gated by the stored policy: a v2 Admin whose accounts.view was
    # explicitly switched off receives 403 on the page and on a write.
    peer = v2_world['peer']
    profile = AccountProfile.objects.get(user=peer)
    profile.platform_overrides = {'accounts.view': False}
    profile.save(update_fields=['platform_overrides'])
    client = login(peer.username)
    assert client.get('/users').status_code == 403
    assert client.post('/users', {'op': 'migration_diff'}).status_code == 403


# --- migration preview must agree with the real v1 and v2 routes ------------

def v1_world():
    owner = make_user('p03r02c_v1_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=1)
    add_profile(owner, policy_version=1)
    admin = make_user('p03r02c_v1_admin')
    add_profile(admin, 'admin', policy_version=1)
    peer = make_user('p03r02c_v1_peer_admin')
    add_profile(peer, 'admin', policy_version=1)
    member = make_user('p03r02c_v1_member')
    add_profile(member, 'user', policy_version=1)
    study_a = Study.objects.create(title='P03R02C v1 study A')
    study_b = Study.objects.create(title='P03R02C v1 study B')
    for action in (VIEW, 'data.export_raw', 'build.upload'):
        Grant.objects.create(user=admin, study=study_a, action=action, delegable=True)
    for action in (VIEW, 'data.export_raw'):
        Grant.objects.create(user=peer, study=study_a, action=action, delegable=False)
    for action in (VIEW, 'build.preview'):
        Grant.objects.create(user=member, study=study_a, action=action, delegable=False)
    return owner, instance, admin, peer, member, study_a, study_b


def legacy_grants():
    """The complete stored legacy binding: rows, actions and delegable flags."""
    return sorted((row.user_id, str(row.study_id), row.action, row.delegable)
                  for row in Grant.objects.all())


def real_route_sets(user, studies):
    """The undecorated real route result per study (v1 before, v2 after)."""
    return {str(study.pk): sorted(action for action in STUDY_V2
                                  if access.allowed(user, study, action))
            for study in studies}


def test_migration_http_journey_agrees_with_real_v1_and_v2_routes(db, evidence):
    owner, instance, admin, peer, member, study_a, study_b = v1_world()
    studies = [study_a, study_b]
    before = {user.pk: real_route_sets(user, studies) for user in (owner, admin, peer, member)}
    grant_snapshot = legacy_grants()
    # The real v1 platform boundary: the Owner alone controls study creation and
    # Admin appointment; an Admin may view/invite and maintain peers per target.
    assert access.allowed_platform(owner, 'study.create') is True
    assert access.allowed_platform(admin, 'accounts.manage_admin') is True
    assert access.allowed_platform(member, 'accounts.view') is False

    # Pending legacy credentials exist before the switch and are invalidated by
    # the same confirmation transaction.
    pending_invitation = AccountInvitation.objects.create(
        issuer=admin, username='p03r02c_stale_invite', role='user',
        token_hash='0' * 64, expires_at=timezone.now() + timedelta(hours=1))
    stale_preview = permissions.preview_matrix(
        owner, {'user_id': str(member.pk), 'study_id': str(study_a.pk), 'visibility': '1',
                'action:session.view': '1'})

    client = login(owner.username)
    page = client.get('/users')
    assert page.status_code == 200 and b'data-policy-migration="available"' in page.content
    assert client.post('/users', {'op': 'migration_diff'}).status_code == 200
    diff = governance_migration.read_diff(instance=instance)
    # The difference is computed from the real v1 route, including an Owner
    # without grants (never "preserved by the Owner role").
    by_user = {subject['user_id']: subject for subject in diff['subjects']}
    owner_row = by_user[owner.pk]
    assert owner_row['is_owner'] and owner_row['platform']['preserved'] == sorted(PLATFORM_V2)
    owner_a = next(row for row in owner_row['studies'] if row['study'] == str(study_a.pk))
    assert owner_a['legacy'] == [] and owner_a['added'] == sorted(STUDY_V2)
    for user in (admin, peer, member):
        for study_row in by_user[user.pk]['studies']:
            assert study_row['actions'] == before[user.pk][study_row['study']], (user.pk, study_row)

    # Explicit Owner choices: adopt the fixed v2 sets for the legacy Admins and
    # keep every other item conservative; the Owner previews the final strategy.
    choices = governance_migration._conservative_choices(diff['unknown'])
    for item in diff['unknown']:
        if item['kind'] in ('admin_missing_permissions', 'admin_future_default'):
            choices[item['id']] = governance_migration.CHOICE_ADOPT_V2
    post = {'op': 'migration_preview'}
    post.update({'unknown:' + key: value for key, value in choices.items()})
    preview = client.post('/users', post)
    assert preview.status_code == 200 and b'data-migration-final-strategy="1"' in preview.content

    preview_row = PermissionPreview.objects.filter(kind='migration_enable').latest('id')
    final = {subject['user_id']: subject for subject in preview_row.summary['subjects']}
    for owner_study in final[owner.pk]['studies']:
        assert owner_study['actions'] == sorted(STUDY_V2)
    admin_a = next(row for row in final[admin.pk]['studies'] if row['study'] == str(study_a.pk))
    admin_b = next(row for row in final[admin.pk]['studies'] if row['study'] == str(study_b.pk))
    assert admin_a['actions'] == sorted(STUDY_V2) and admin_b['actions'] == sorted(STUDY_V2)
    member_a = next(row for row in final[member.pk]['studies'] if row['study'] == str(study_a.pk))
    assert member_a['actions'] == sorted([VIEW, 'build.preview'])

    instance.refresh_from_db()
    committed = client.post('/users', {'op': 'migration_confirm',
                                       'preview_id': preview_id_of(preview),
                                       'revision': str(instance.governance_revision),
                                       'password': OWNER_PASSWORD})
    assert committed.status_code == 200
    instance.refresh_from_db()
    assert instance.authorization_version == 2

    # After the real switch, every undecorated route re-reads the stored policy
    # and matches exactly the previewed final strategy.
    for user in (owner, admin, peer, member):
        actual = real_route_sets(user, studies)
        for study_row in final[user.pk]['studies']:
            assert actual[study_row['study']] == study_row['actions'], (user.pk, study_row)
    assert access.allowed(owner, study_a, 'study.delete') is True
    assert access.allowed(member, study_a, 'build.preview') is True
    assert Grant.objects.filter(user=member, study=study_a).count() == 2
    # The legacy grant rows (actions and delegable flags) are exactly as stored
    # before the switch: v2 writes overrides, never the old binding.
    assert legacy_grants() == grant_snapshot
    # Real requests after the switch: study visibility, study creation, an
    # account/permission write and a protected data route all read the stored
    # canonical policy.
    member_client = login(member.username)
    member_home = member_client.get('/')
    assert b'P03R02C v1 study A' in member_home.content
    assert b'P03R02C v1 study B' not in member_home.content
    created = member_client.post('/', {'title': 'P03R02C v1 created after switch'})
    assert created.status_code == 302
    new_study = Study.objects.get(title='P03R02C v1 created after switch')
    assert sorted(Grant.objects.filter(user=member, study=new_study)
                  .values_list('action', flat=True)) == sorted(STUDY_V2)
    assert member_client.post('/v1/admin/exports', {'study_id': str(study_a.pk)},
                              content_type='application/json').status_code == 403
    admin_client = login(admin.username)
    assert admin_client.post('/v1/admin/exports', {'study_id': str(study_a.pk)},
                             content_type='application/json').status_code == 201
    member_before = stored_overrides(member)
    owner_client = login(owner.username)
    write_preview = matrix_preview(owner_client, member, study_a, ['+session.view'])
    assert write_preview.status_code == 200
    instance.refresh_from_db()
    assert matrix_commit(owner_client, preview_id_of(write_preview),
                         instance.governance_revision).status_code == 200
    assert stored_overrides(member) != member_before
    assert set(stored_overrides(member)[str(study_a.pk)]) == {
        VIEW, 'build.preview', 'session.view'}

    # Old invitations and pending previews were invalidated in the same
    # transaction; a repeated enablement is refused before any write.
    pending_invitation.refresh_from_db()
    stale_preview.refresh_from_db()
    assert pending_invitation.revoked is True
    assert stale_preview.consumed is False and stale_preview.expires_at <= timezone.now()
    second = login(owner.username).post('/users', {'op': 'migration_preview'})
    assert_users_rejected(second, 409, 'already_enabled')
    evidence('migration_agreement.json', {
        'before': {str(key): value for key, value in before.items()},
        'revision': instance.governance_revision,
        'admin_study_a_final': admin_a['actions'],
        'owner_without_grants_before': before[owner.pk][str(study_a.pk)]})


def test_migration_preview_binds_complete_subject_state(db):
    owner, instance, admin, peer, member, study_a, study_b = v1_world()
    diff = governance_migration.read_diff(instance=instance)
    member_row = next(subject for subject in diff['subjects'] if subject['user_id'] == member.pk)
    # The described state carries the complete relevant facts, not only the
    # projected action sets.
    required = {'role', 'is_owner', 'is_active', 'must_change_password', 'deleted',
                'policy_version', 'auth_version', 'revision', 'principal',
                'principal_deleted_at', 'stored_platform_overrides',
                'stored_study_overrides', 'stored_future_study_actions'}
    assert required <= set(member_row['state'])
    assert member_row['state']['principal'] == str(Principal.objects.get(user=member).pk)
    assert member_row['state']['policy_version'] == 1
    choices = governance_migration._conservative_choices(diff['unknown'])
    row = governance_migration.preview_enablement(owner, choices)
    # Changing a stored subject fact after the preview refuses the confirmation:
    # the binding covers the complete observed state, not just the actions.
    profile = AccountProfile.objects.get(user=member)
    profile.auth_version += 1
    profile.save(update_fields=['auth_version'])
    instance.refresh_from_db()
    with pytest.raises(Rejected) as info:
        governance_migration.confirm_enablement(owner, OWNER_PASSWORD,
                                                instance.governance_revision, row.pk)
    assert info.value.code == 'preview_stale'
    instance.refresh_from_db()
    assert instance.authorization_version == 1
    assert AccountProfile.objects.get(user=member).study_overrides == {}


def test_legacy_admin_peer_lifecycle_is_removed_not_preserved(db, evidence):
    owner, instance, admin, peer, member, study_a, study_b = v1_world()
    revision = instance.governance_revision
    # The real v1 route allows a dominating Admin to reset a peer Admin; the
    # difference must not claim this old capability never existed, and must not
    # silently preserve it either.
    assert access.authorization_version() == 1
    assert access.allowed_platform(admin, 'accounts.manage_admin') is True
    reset = accounts.reset_temporary_password(admin, OWNER_PASSWORD, revision, peer.pk)
    assert reset['temporary_password']
    diff = governance_migration.read_diff(instance=instance)
    admin_row = next(subject for subject in diff['subjects'] if subject['user_id'] == admin.pk)
    assert 'accounts.manage_admin' in admin_row['platform']['removed']
    assert admin_row['platform_note']
    assert 'peer-Admin reset/disable' in admin_row['platform_note']

    # Enable v2 with the explicit fixed v2 study set for the Admins.
    choices = governance_migration._conservative_choices(diff['unknown'])
    for item in diff['unknown']:
        if item['kind'] in ('admin_missing_permissions', 'admin_future_default'):
            choices[item['id']] = governance_migration.CHOICE_ADOPT_V2
    instance.refresh_from_db()
    governance_migration.confirm_enablement(
        owner, OWNER_PASSWORD, instance.governance_revision,
        governance_migration.preview_enablement(owner, choices).pk)
    instance.refresh_from_db()
    assert instance.authorization_version == 2

    # The same real request now refuses without the Owner's explicit switch...
    client = login(admin.username)
    refused = client.post('/users', {'op': 'reset_password', 'username': peer.username,
                                     'revision': str(instance.governance_revision),
                                     'password': OWNER_PASSWORD})
    assert_users_rejected(refused, 403, 'forbidden')
    # ... and the Owner's platform entry grants exactly that switch.
    owner_client = login(owner.username)
    preview = owner_client.post('/users', {'op': 'platform_preview', 'username': admin.username,
                                           'platform:accounts.manage_admin': '1'})
    assert preview.status_code == 200
    instance.refresh_from_db()
    assert owner_client.post('/users', {'op': 'platform_commit',
                                        'preview_id': preview_id_of(preview),
                                        'revision': str(instance.governance_revision),
                                        'password': OWNER_PASSWORD}).status_code == 200
    # With the explicit switch, the real v2 route performs the same peer
    # maintenance the v1 route exercised, through the kernel.
    assert access.allowed_platform(admin, 'accounts.manage_admin') is True
    assert access.takeover_allowed(admin, peer) is True
    instance.refresh_from_db()
    allowed = client.post('/users', {'op': 'reset_password', 'username': peer.username,
                                     'revision': str(instance.governance_revision),
                                     'password': OWNER_PASSWORD})
    assert allowed.status_code == 200
    evidence('legacy_admin_peer.json', {
        'v1_reset_succeeded': bool(reset['temporary_password']),
        'platform_removed': admin_row['platform']['removed'],
        'v2_reset_after_switch': allowed.status_code})
