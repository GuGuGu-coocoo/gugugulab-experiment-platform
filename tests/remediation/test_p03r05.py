"""P03R05 evidence: the v2 permission page, drafts and the confirmation dialog.

Expected side: the confirmed requirements (U01-U04, current_requirements §2.1)
and the R00 contract section A, never the implementation:

- one main row per account with an accurate Owner/Admin/user badge, expandable
  study entries that show the final effective selection and its source, and no
  delegation checkbox anywhere (v2 has no ``delegable`` meaning);
- a study the actor cannot see never leaks its title: only ``hidden_studies``
  count and state are rendered, and the Owner/own rows stay read-only;
- a permission commit is a per-study PATCH: state outside the current filter and
  page, and the target's other studies, are untouched; a stale revision refuses
  the whole commit without a write;
- restoring visibility sends ``study.view`` alone and never revives removed
  child actions;
- the Owner platform switches are a separate, preview-bound entry; an Admin can
  never see or use it, and the three Owner-controlled switches are never passed
  on by an Admin;
- the unified dialog is password-only, and ``GET /users?preview_status=`` is a
  read-only, actor-scoped check used when a submit result is unknown: a pending
  preview proves nothing ran, a consumed preview proves it did (replay stays
  idempotent);
- one real Chrome journey: one row per account, both languages, all three
  themes, keyboard cancel, refusal, double-click and an unknown submit result
  (aborted POST) that is checked through the preview status instead of claiming
  success, with the server-side authorization still in force afterwards.

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r05/<UTC>-<random>/`` root.
"""
import json
import os
import re
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from core import access, accounts
from core.models import (AccountProfile, Audit, Grant, Instance, PermissionPreview,
                         Principal, Study)

REPO_ROOT = Path(__file__).resolve().parents[2]

OWNER_PASSWORD = 'synthetic-p03r05-owner-password'
ADMIN_PASSWORD = 'synthetic-p03r05-admin-password'
USER_PASSWORD = 'synthetic-p03r05-user-password'
VIEW = 'study.view'
ADMIN_SWITCHES = ('accounts.create_admin', 'accounts.manage_admin', 'accounts.delete_admin')
MATRIX_USER_ROW_RE = re.compile(r'data-matrix-user-row="(\d+)"')
PREVIEW_RE = re.compile(r'data-preview-id="([0-9a-fA-F-]{36})"')


# --- synthetic world ---------------------------------------------------------

def make_user(username, password, *, role='user', platform=None, studies=None,
              future=None, must_change=False):
    """One account with an explicit stored v2 policy and stable Principal."""
    user = get_user_model().objects.create_user(username, password=password)
    Principal.objects.create(user=user)
    AccountProfile.objects.create(user=user, role=role, must_change_password=must_change,
                                  policy_version=2, platform_overrides=dict(platform or {}),
                                  study_overrides=dict(studies or {}),
                                  future_study_actions=future)
    return user


def signed_in(username, password):
    client = Client()
    assert client.post('/login', {'username': username, 'password': password}).status_code == 302
    return client


def preview_payload(client, user_id, study_id, *, visibility=False, actions=()):
    data = {'op': 'matrix_preview', 'user_id': str(user_id), 'study_id': str(study_id)}
    if visibility:
        data['visibility'] = '1'
    for action in actions:
        data[f'action:{action}'] = '1'
    return client.post('/users', data)


def preview_id_of(response):
    match = PREVIEW_RE.search(response.content.decode())
    assert match, response.content[:400]
    return match.group(1)


def commit_matrix(client, preview_id, password=OWNER_PASSWORD):
    return client.post('/users', {'op': 'matrix_commit', 'preview_id': preview_id,
                                  'password': password})


def stored(user, study):
    return sorted(AccountProfile.objects.get(user=user).study_overrides.get(str(study.pk), []))


def row_of(body, user_id):
    return re.search(rf'<tr data-matrix-user-row="{user_id}".*?</tr>', body, re.S).group(0)


def panel_of(body, user_id):
    return re.search(rf'<tr data-matrix-studies="{user_id}">.*?</tr>', body, re.S).group(0)


@pytest.fixture
def world(db):
    owner = make_user('p03r05_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    study_a = Study.objects.create(title='P03R05 study A', recruitment='open', max_sessions=5)
    study_b = Study.objects.create(title='P03R05 study B')
    unseen = Study.objects.create(title='P03R05 unseen study')
    AccountProfile.objects.filter(user=owner).update(
        study_overrides={str(study_a.pk): [VIEW, 'study.configure'],
                         str(study_b.pk): [VIEW, 'study.configure']})
    # An Admin restricted away from one study, with an explicit empty override
    # there so its own view really lacks that study.
    admin = make_user('p03r05_admin', ADMIN_PASSWORD, role='admin',
                      platform={'accounts.manage_admin': True},
                      studies={str(unseen.pk): []})
    ordinary = make_user('p03r05_ordinary', USER_PASSWORD,
                         studies={str(study_a.pk): [VIEW, 'session.view'],
                                  str(unseen.pk): [VIEW]})
    # A user whose selection comes from real legacy grant rows, so the page has
    # to name the 'grants' source as well.
    granted = make_user('p03r05_granted', USER_PASSWORD)
    for action in (VIEW, 'session.recover'):
        Grant.objects.create(user=granted, study=study_a, action=action, delegable=False)
    return {'owner': owner, 'instance': instance, 'study_a': study_a, 'study_b': study_b,
            'unseen': unseen, 'admin': admin, 'ordinary': ordinary, 'granted': granted}


# --- one row per account, badges, sources, no delegation ---------------------

def test_v2_matrix_one_row_per_account_badges_sources_and_no_delegation(world):
    owner = world['owner']
    client = signed_in(owner.username, OWNER_PASSWORD)
    body = client.get('/users').content.decode()
    assert 'data-matrix-version="2"' in body
    accounts_seen = list(get_user_model().objects.order_by('id').values_list('pk', flat=True))
    assert MATRIX_USER_ROW_RE.findall(body) == [str(pk) for pk in accounts_seen]
    assert body.count('data-matrix-user-row=') == len(accounts_seen)
    for user_id in accounts_seen:
        assert '<th scope="row" data-matrix-account=' in row_of(body, user_id)
    assert 'Owner' in row_of(body, owner.pk)
    assert '管理员' in row_of(body, world['admin'].pk)
    assert 'data-account-role="admin"' in row_of(body, world['admin'].pk)
    assert '普通成员' in row_of(body, world['ordinary'].pk)
    assert 'data-account-role="user"' in row_of(body, world['ordinary'].pk)

    # v2 never renders the legacy delegation flag as a control.
    matrix_section = body[body.index('<section data-permission-matrix'):
                          body.index('</section>', body.index('<section data-permission-matrix'))]
    assert 'name="delegable:' not in body and 'delegable:' not in matrix_section
    assert '可委派' not in matrix_section and '委派标记' not in matrix_section

    # Each study entry names where its final effective selection comes from.
    assert f'data-matrix-row="{world["ordinary"].pk}-{world["study_a"].pk}"' in body
    assert f'data-matrix-row="{world["granted"].pk}-{world["study_a"].pk}"' in body
    assert 'data-study-source="grants"' in panel_of(body, world['granted'].pk)
    assert '来源：显式授权' in body
    assert f'data-matrix-row="{world["admin"].pk}-{world["unseen"].pk}"' in body
    assert 'data-study-source="override"' in body
    assert f'data-matrix-row="{world["admin"].pk}-{world["study_a"].pk}"' in body
    assert 'data-study-source="role_default"' in body
    assert '来源：角色默认/未来上界' in body
    # The complete v2 catalog is shown, not a stale subset.
    assert 'action:study.delete' in body and 'action:identity_mapping.read' in body

    # English rendering uses the same data attributes with English labels.
    client.cookies['gep_lang'] = 'en'
    english = client.get('/users').content.decode()
    assert 'Source: explicit grants' in english and 'Source: role default / future bound' in english
    assert 'No hidden studies' in english and 'Editable scope' in english
    assert 'name="delegable:' not in english and 'Delegable flag' not in english


def test_v2_matrix_hides_unseen_study_titles_and_keeps_owner_and_self_readonly(world):
    admin, unseen = world['admin'], world['unseen']
    client = signed_in(admin.username, ADMIN_PASSWORD)
    body = client.get('/users').content.decode()
    # The admin cannot see the unseen study: no title, no chip, only a count.
    assert unseen.title not in body
    assert f'data-matrix-row="{world["ordinary"].pk}-{unseen.pk}"' not in body
    ordinary_row = row_of(body, world['ordinary'].pk)
    assert 'data-hidden-studies="1"' in ordinary_row
    assert '不可见研究 1' in ordinary_row
    # The Owner row is read-only for the Admin: no form and no editable input.
    owner_panel = panel_of(body, world['owner'].pk)
    assert 'data-readonly-reason="owner"' in owner_panel
    assert '<form' not in owner_panel and '<input' not in owner_panel
    # The Admin's own row is read-only too.
    own_panel = panel_of(body, admin.pk)
    assert 'data-readonly-reason="self"' in own_panel
    assert '<form' not in own_panel and '<input' not in own_panel
    # Read-only inspection grants nothing: no Owner platform switches and no
    # delete entry for the Owner row.
    assert 'data-platform-switches' not in body
    assert f'data-delete-account="{world["owner"].pk}"' not in body


# --- PATCH semantics, filtered/paged state, stale revisions ------------------

def test_v2_matrix_commit_is_a_patch_keeping_filtered_and_paged_state(world):
    owner, study_a, study_b = world['owner'], world['study_a'], world['study_b']
    ordinary = world['ordinary']
    study_c = Study.objects.create(title='P03R05 study C')
    bulk = [make_user(f'p03r05_bulk_{index:03d}', USER_PASSWORD,
                      studies={str(study_c.pk): [VIEW]}) for index in range(24)]
    before = {user.pk: dict(AccountProfile.objects.get(user=user).study_overrides)
              for user in bulk + [ordinary]}

    client = signed_in(owner.username, OWNER_PASSWORD)
    # The matrix is paginated and a filtered request narrows it further; the
    # commit below is for an account outside that filter and still keeps
    # everything else exactly as it was.
    assert len(MATRIX_USER_ROW_RE.findall(client.get('/users?page=2').content.decode())) > 0
    filtered = client.get('/users?q=p03r05_bulk_001').content.decode()
    assert MATRIX_USER_ROW_RE.findall(filtered) == [str(bulk[1].pk)]
    preview = preview_payload(client, ordinary.pk, study_a.pk, visibility=True,
                              actions=['session.view', 'data.export_raw'])
    assert preview.status_code == 200
    first_pid = preview_id_of(preview)
    response = commit_matrix(client, first_pid)
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-committed-kind="matrix"' in body
    assert f'data-committed-user="{ordinary.pk}"' in body
    assert f'data-committed-study="{study_a.pk}"' in body
    assert stored(ordinary, study_a) == sorted([VIEW, 'session.view', 'data.export_raw'])
    # Replaying the same successful preview (a client retry after an unknown
    # result) returns the stored result and writes nothing again.
    revisions = sorted(AccountProfile.objects.values_list('pk', 'revision'))
    replay = commit_matrix(client, first_pid)
    assert replay.status_code == 200
    assert '权限矩阵已更新' in replay.content.decode()
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 1
    assert sorted(AccountProfile.objects.values_list('pk', 'revision')) == revisions
    # The target's other studies and every account outside the committed entry
    # are untouched.
    assert str(study_b.pk) not in AccountProfile.objects.get(user=ordinary).study_overrides
    for user in bulk:
        assert dict(AccountProfile.objects.get(user=user).study_overrides) == before[user.pk]

    # A stale target revision refuses the whole commit and writes nothing.
    preview = preview_payload(client, ordinary.pk, study_b.pk, visibility=True, actions=['audit.view'])
    assert preview.status_code == 200
    stale_id = preview_id_of(preview)
    profile = AccountProfile.objects.get(user=ordinary)
    profile.revision += 1
    profile.save(update_fields=['revision'])
    refused = commit_matrix(client, stale_id)
    assert refused.status_code == 409
    assert str(study_b.pk) not in AccountProfile.objects.get(user=ordinary).study_overrides
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 1


def test_v2_restore_visibility_sends_view_only_and_refuses_contradictions(world):
    owner, ordinary, study_a = world['owner'], world['ordinary'], world['study_a']
    client = signed_in(owner.username, OWNER_PASSWORD)

    # Hide the study: the submitted empty list removes view and its children.
    hidden = preview_payload(client, ordinary.pk, study_a.pk)
    assert hidden.status_code == 200
    assert commit_matrix(client, preview_id_of(hidden)).status_code == 200
    assert stored(ordinary, study_a) == []

    # Restore visibility: only study.view is sent; the old child action stays
    # removed until it is explicitly checked again.
    restored = preview_payload(client, ordinary.pk, study_a.pk, visibility=True)
    assert restored.status_code == 200
    assert 'study.view' in restored.content.decode()
    assert commit_matrix(client, preview_id_of(restored)).status_code == 200
    assert stored(ordinary, study_a) == [VIEW]

    # Children without visibility stay a contradiction, with no write and no
    # stored preview left behind.
    contradiction = preview_payload(client, ordinary.pk, study_a.pk, actions=['session.view'])
    assert contradiction.status_code == 409
    assert '不能保留子权限' in contradiction.content.decode()
    assert stored(ordinary, study_a) == [VIEW]
    assert PermissionPreview.objects.filter(consumed=False, kind='matrix').count() == 0


def test_v2_readonly_targets_and_own_rows_refuse_writes(world):
    owner, admin, ordinary, study_a = (world['owner'], world['admin'], world['ordinary'],
                                       world['study_a'])
    admin_client = signed_in(admin.username, ADMIN_PASSWORD)
    before = dict(AccountProfile.objects.get(user=admin).study_overrides)
    assert admin_client.post('/users', {'op': 'matrix_preview', 'user_id': str(admin.pk),
                                        'study_id': str(study_a.pk), 'visibility': '1'}
                             ).status_code == 409
    assert admin_client.post('/users', {'op': 'matrix_preview', 'user_id': str(owner.pk),
                                        'study_id': str(study_a.pk), 'visibility': '1'}
                             ).status_code == 403
    assert admin_client.post('/users', {'op': 'matrix_preview', 'user_id': str(ordinary.pk),
                                        'study_id': str(world['unseen'].pk), 'visibility': '1'}
                             ).status_code == 403
    assert dict(AccountProfile.objects.get(user=admin).study_overrides) == before
    # A v2 platform switch is Owner-only: the Admin cannot even preview it.
    assert admin_client.post('/users', {'op': 'platform_preview', 'username': ordinary.username,
                                        'platform:accounts.create_admin': '1'}
                             ).status_code == 403
    assert AccountProfile.objects.get(user=ordinary).platform_overrides == {}


# --- Owner platform switches -------------------------------------------------

def test_owner_platform_switch_ui_is_separate_and_preview_bound(world):
    owner, admin, ordinary = world['owner'], world['admin'], world['ordinary']
    client = signed_in(owner.username, OWNER_PASSWORD)
    body = client.get('/users').content.decode()
    # The switches are their own control, offered for every non-Owner account
    # (Admin and ordinary) and never for the Owner row or own row.
    assert f'data-platform-switches="{admin.pk}"' in body
    assert f'data-platform-switches="{ordinary.pk}"' in body
    assert f'data-platform-switches="{owner.pk}"' not in body
    assert 'data-platform-form="1"' in body
    assert body.count('data-platform-switch=') == 7 * 3
    block = body[body.index(f'data-platform-switches="{admin.pk}"'):]
    block = block[:block.index('</details>')]
    for action in ADMIN_SWITCHES:
        assert f'platform:{action}' in block
    # Preview binds the target and the full resulting map; a wrong password
    # changes nothing, the right one is a single audited commit.
    payload = {'op': 'platform_preview', 'username': admin.username,
               'platform:study.create': '1', 'platform:accounts.view': '1',
               'platform:accounts.create_user': '1', 'platform:accounts.manage_user': '1'}
    for action in ADMIN_SWITCHES:
        payload[f'platform:{action}'] = '1'
    preview = client.post('/users', payload)
    assert preview.status_code == 200
    preview_body = preview.content.decode()
    assert 'data-preview="platform"' in preview_body and 'data-platform-preview="1"' in preview_body
    assert 'accounts.create_admin' in preview_body
    pid = preview_id_of(preview)
    refused = client.post('/users', {'op': 'platform_commit', 'preview_id': pid,
                                     'password': 'wrong-password-2026'})
    assert refused.status_code == 403
    assert AccountProfile.objects.get(user=admin).platform_overrides == {'accounts.manage_admin': True}
    assert Audit.objects.filter(action='permission.platform_changed').count() == 0
    committed = client.post('/users', {'op': 'platform_commit', 'preview_id': pid,
                                       'password': OWNER_PASSWORD})
    assert committed.status_code == 200
    assert AccountProfile.objects.get(user=admin).platform_overrides['accounts.create_admin'] is True
    assert Audit.objects.filter(action='permission.platform_changed').count() == 1
    # The switch is effective for the target and the Admin still cannot pass it
    # on to anybody else.
    assert access.allowed_platform(admin, 'accounts.create_admin') is True
    assert access.allowed_platform(ordinary, 'accounts.create_admin') is False
    admin_preview = signed_in(admin.username, ADMIN_PASSWORD).post(
        '/users', {'op': 'platform_preview', 'username': ordinary.username,
                   'platform:accounts.create_admin': '1'})
    assert admin_preview.status_code == 403


# --- read-only preview status ------------------------------------------------

def test_preview_status_is_read_only_actor_scoped_and_proves_execution(world):
    owner, ordinary, study_a = world['owner'], world['ordinary'], world['study_a']
    client = signed_in(owner.username, OWNER_PASSWORD)
    preview = preview_payload(client, ordinary.pk, study_a.pk, visibility=True, actions=['audit.view'])
    assert preview.status_code == 200
    pid = preview_id_of(preview)
    before = list(AccountProfile.objects.values_list('revision', flat=True))
    audits_before = Audit.objects.count()
    status = client.get(f'/users?preview_status={pid}')
    assert status.status_code == 200
    assert status['Cache-Control'] == 'no-store'
    data = status.json()
    # P03R05R expectation change (confirmed requirement: the status query serves
    # a minimal own-operation state only). R05 returned the stored result object
    # here; that object named the study/account and is exactly what a revoked
    # actor must not be able to read back.
    assert data['state'] == 'pending' and data['kind'] == 'matrix'
    assert 'result' not in data and 'username' not in data
    assert ordinary.username not in status.content.decode()
    assert study_a.title not in status.content.decode()
    # The check itself writes nothing at all.
    assert list(AccountProfile.objects.values_list('revision', flat=True)) == before
    assert Audit.objects.count() == audits_before
    assert PermissionPreview.objects.get(pk=pid).consumed is False
    # After the commit the same query proves the operation ran, still without
    # returning any stored object data.
    assert commit_matrix(client, pid).status_code == 200
    after_response = client.get(f'/users?preview_status={pid}')
    after = after_response.json()
    assert after['state'] == 'consumed'
    assert 'result' not in after
    assert ordinary.username not in after_response.content.decode()
    assert study_a.title not in after_response.content.decode()
    # Another actor never sees this preview, and a malformed/unknown id fails
    # closed instead of leaking anything.
    admin_client = signed_in(world['admin'].username, ADMIN_PASSWORD)
    assert admin_client.get(f'/users?preview_status={pid}').status_code == 404
    assert client.get('/users?preview_status=not-a-uuid').status_code == 404
    assert client.get(f'/users?preview_status={uuid.uuid4()}').status_code == 404
    # Expired previews are reported as expired, not as pending.
    expired = PermissionPreview.objects.create(
        actor=owner, kind='matrix', scope=str(study_a.pk), summary={}, errors=[], binding='a' * 64,
        staged=None, expires_at=timezone.now() - timedelta(minutes=1))
    assert client.get(f'/users?preview_status={expired.pk}').json()['state'] == 'expired'
    # Without accounts.view the query is refused for an ordinary account.
    assert signed_in(ordinary.username, USER_PASSWORD).get(
        f'/users?preview_status={pid}').status_code == 403


# --- version routing of the new interaction ----------------------------------

def test_v2_page_uses_the_dialog_and_delete_trigger(world):
    owner, ordinary = world['owner'], world['ordinary']
    client = signed_in(owner.username, OWNER_PASSWORD)
    body = client.get('/users').content.decode()
    assert 'data-confirm-dialog="1"' in body and 'data-confirm-form="1"' in body
    assert '/static/core/permissions.js' in body
    assert f'data-confirm-open="delete"' in body
    assert f'data-confirm-username="{ordinary.username}"' in body
    assert 'data-confirm-op="delete"' in body
    assert 'data-danger="1"' in body
    # The Owner row has no delete trigger at all.
    assert f'data-delete-account="{owner.pk}"' not in body


def test_v1_page_keeps_its_legacy_controls(db):
    owner = make_user('p03r05_v1_owner', OWNER_PASSWORD)
    AccountProfile.objects.filter(user=owner).update(policy_version=1)
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=1)
    study = Study.objects.create(title='P03R05 v1 study')
    for action in ('study.view', 'study.configure'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    target = get_user_model().objects.create_user('p03r05_v1_target', password=USER_PASSWORD)
    AccountProfile.objects.create(user=target, role='user', policy_version=1)
    body = signed_in(owner.username, OWNER_PASSWORD).get('/users').content.decode()
    assert 'data-matrix-version="1"' in body
    assert 'name="delegable:' in body and '可委派' in body
    assert 'data-confirm-dialog' not in body
    assert '/static/core/permissions.js' not in body
    assert '<details data-delete-account=' in body


# --- real Chrome -------------------------------------------------------------

browser_only = pytest.mark.skipif(
    os.environ.get('GEP_T17_BROWSER') != '1',
    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')


def run_chrome(script, env, observations_key):
    env = dict(os.environ, **env)
    result = subprocess.run(['node', '--input-type=module', '-e', script],
                            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=240)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    match = re.search(rf'{observations_key} ({{.*}})', result.stdout)
    assert match, result.stdout[-2000:] + result.stderr[-2000:]
    return json.loads(match.group(1))


@browser_only
def test_actual_chrome_v2_matrix_one_row_drafts_languages_and_themes(
        live_server, world, evidence, evidence_root):
    env = {'GEP_TEST_BASE': live_server.url, 'GEP_OWNER_PASSWORD': OWNER_PASSWORD,
           'GEP_ORDINARY_PK': str(world['ordinary'].pk),
           'GEP_ACCOUNT_NAME': world['admin'].username,
           'GEP_STUDY_A': str(world['study_a'].pk),
           'GEP_EVIDENCE': str(evidence_root)}
    observations = run_chrome(SCRIPT_MATRIX, env, 'P03R05_MATRIX')
    assert observations['page_errors'] == []
    assert observations['user_rows'] == 4
    assert observations['unique_header'] is True
    assert observations['no_delegation_control'] is True
    assert observations['source_labels'] >= 3
    assert observations['draft_banner_visible'] is True
    assert observations['draft_survived_navigation'] is True
    assert observations['draft_cleared'] is True
    assert observations['collapsed'] is True
    assert observations['scope_select_all_checked'] is True
    assert observations['scope_select_all_skips_invisible'] is True
    assert observations['english'] is True and observations['chinese'] is True
    assert observations['dialog_english_cancel'] is True
    assert observations['themes'] == ['light', 'dark', 'system']
    assert observations['dark_contrast'] is True and observations['light_contrast'] is True
    assert observations['focus_inside'] is True and observations['focus_returned'] is True
    assert observations['escape_sent_no_request'] is True
    evidence('chrome_matrix.json', observations)


SCRIPT_MATRIX = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const ordinaryPk=process.env.GEP_ORDINARY_PK, grantedPk=process.env.GEP_GRANTED_PK;
const studyA=process.env.GEP_STUDY_A, accountPk=process.env.GEP_ACCOUNT_PK;
const evidence=process.env.GEP_EVIDENCE;
const observations={},errors=[];
function luminance(rgb){const parts=rgb.match(/[\d.]+/g).map(Number);const [r,g,b]=parts;return (0.2126*r+0.7152*g+0.0722*b)/255;}
async function login(page,username,password){
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
}
try {
  const ctx=await browser.newContext({viewport:{width:1366,height:900}});
  const page=await ctx.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  await login(page,'p03r05_owner',ownerPassword);
  await page.goto(base+'/users');

  // One main row per account, one row header each, no delegation control.
  observations.user_rows=await page.locator('[data-matrix-user-row]').count();
  observations.unique_header=
    (await page.locator('[data-matrix-account]').count())===observations.user_rows;
  observations.no_delegation_control=(await page.locator('[name^="delegable:"]').count())===0;
  observations.source_labels=await page.locator('[data-study-source]').count();
  observations.hidden_count_shown=(await page.locator('[data-hidden-studies="0"]').count())>0;

  // Drafts survive paging/filtering; the change count is stated; the clear
  // control restores the stored state.
  const entry='form[data-matrix-entry="'+ordinaryPk+'-'+studyA+'"]';
  const child=page.locator(entry+' [name="action:session.view"]');
  await page.locator(entry+' details summary').click();
  await expect(child).toBeChecked();
  await child.uncheck();
  await expect(page.locator('[data-draft-banner]')).toBeVisible();
  observations.draft_banner_visible=true;
  observations.change_count=
    await page.locator('[data-entry-state="'+ordinaryPk+'-'+studyA+'"] [data-change-count]').innerText();
  await page.goto(base+'/users?q='+process.env.GEP_ACCOUNT_NAME);
  await expect(page.locator('[data-draft-banner]')).toBeVisible();
  await page.goto(base+'/users');
  await expect(page.locator(entry+' [name="action:session.view"]')).not.toBeChecked();
  observations.draft_survived_navigation=true;
  await page.locator('[data-draft-clear]').click();
  await expect(page.locator(entry+' [name="action:session.view"]')).toBeChecked();
  observations.draft_cleared=!(await page.locator('[data-draft-banner]').isVisible());

  // Bulk selection only touches the explicitly visible editable scope: the
  // account-wide control skips an entry whose visibility is not checked.
  await page.locator('[data-matrix-toggle="'+ordinaryPk+'"]').click();
  observations.collapsed=!await page.locator('[data-matrix-studies="'+ordinaryPk+'"]').isVisible();
  await page.locator('[data-matrix-toggle="'+ordinaryPk+'"]').click();
  await page.locator(entry+' details summary').click();
  await page.locator(entry+' [data-select-all-study]').check();
  observations.scope_select_all_checked=
    await child.isChecked()
    && await page.locator(entry+' [name="action:audit.view"]').isChecked();
  await page.locator(entry+' [data-select-all-study]').uncheck();
  await page.locator('[data-draft-clear]').click();
  // The account-wide control needs explicit visibility: uncheck it first.
  const visibility=page.locator(entry+' [name=visibility]');
  await visibility.uncheck();
  await page.locator('[data-select-all-user="'+ordinaryPk+'"]').check();
  observations.scope_select_all_skips_invisible=!(await child.isChecked());
  await page.locator('[data-draft-clear]').click();

  // Both languages render the same controls and the dialog labels.
  await page.goto(base+'/prefs?lang=en&next=/users');
  const englishBody=await page.content();
  observations.english=englishBody.includes('Owner platform switches')
    && englishBody.includes('Source: explicit grants');
  await page.locator('[data-delete-account="'+ordinaryPk+'"]').click();
  observations.dialog_english_cancel=
    (await page.locator('dialog[open]').innerText()).includes('Cancel (sends no request)');
  await page.keyboard.press('Escape');
  await page.goto(base+'/prefs?lang=zh&next=/users');
  const chineseBody=await page.content();
  observations.chinese=chineseBody.includes('Owner 平台开关')
    && chineseBody.includes('来源：显式授权');

  // Three themes: the dialog and page follow the stored theme with real values.
  const themes=[];
  for(const theme of ['light','dark','system']){
    await page.goto(base+'/prefs?theme='+theme+'&next=/users');
    themes.push(await page.getAttribute('html','data-theme'));
    const dialogBg=await page.evaluate(()=>getComputedStyle(document.querySelector('dialog')).backgroundColor);
    observations['dialog_'+theme]=dialogBg;
    if(theme==='dark'){observations.dark_contrast=luminance(dialogBg)<0.5;}
    if(theme==='light'){observations.light_contrast=luminance(dialogBg)>0.5;}
    await page.screenshot({path:evidence+'/matrix_v2_'+theme+'.png',fullPage:false});
  }
  observations.themes=themes;
  await page.goto(base+'/prefs?theme=system&next=/users');

  // The dialog takes focus, Escape sends nothing and returns the focus.
  await page.locator('[data-delete-account="'+ordinaryPk+'"]').click();
  observations.focus_inside=await page.evaluate(()=>document.querySelector('dialog').contains(document.activeElement));
  await page.keyboard.press('Escape');
  observations.focus_returned=await page.evaluate(
    id=>document.activeElement===document.querySelector('[data-delete-account="'+id+'"]'),ordinaryPk);
  observations.escape_sent_no_request=(await page.locator('dialog[open]').count())===0;
  expect(await page.locator('[data-account-row="'+ordinaryPk+'"]').count()).toBe(1);
  await page.screenshot({path:evidence+'/dialog_zh_system.png',fullPage:false});
} finally {await browser.close();}
observations.page_errors=errors;
console.log('P03R05_MATRIX '+JSON.stringify(observations));
'''


@browser_only
def test_actual_chrome_dialog_cancel_error_double_click_and_unknown_result(
        live_server, world, evidence, evidence_root):
    env = {'GEP_TEST_BASE': live_server.url, 'GEP_OWNER_PASSWORD': OWNER_PASSWORD,
           'GEP_ADMIN_PK': str(world['admin'].pk),
           'GEP_VICTIM_PK': str(world['granted'].pk),
           'GEP_VICTIM_NAME': world['granted'].username,
           'GEP_EVIDENCE': str(evidence_root)}
    observations = run_chrome(SCRIPT_DIALOG, env, 'P03R05_DIALOG')
    assert observations['page_errors'] == []
    assert observations['focus_inside'] is True
    assert observations['focus_returned'] is True
    assert observations['cancel_sent_no_request'] is True
    assert observations['wrong_password_refused'] is True
    assert observations['victim_still_there'] is True
    assert observations['double_click_single_request'] is True
    assert observations['victim_row_gone'] is True
    assert observations['unknown_state'] == 'pending-checked'
    assert observations['unknown_no_success'] is True
    assert observations['status_queried'] is True
    assert observations['platform_committed'] is True
    evidence('chrome_dialog.json', observations)

    # Server truth after the real browser journey: the victim is permanently
    # deleted exactly once and the platform switch is stored exactly once.
    victim = world['granted']
    assert not get_user_model().objects.filter(pk=victim.pk).exists()
    assert Audit.objects.filter(action='account.deleted').count() == 1
    assert Audit.objects.filter(action='permission.platform_changed').count() == 1
    profile = AccountProfile.objects.get(user=world['admin'])
    assert profile.platform_overrides['accounts.create_admin'] is True
    assert access.allowed_platform(world['admin'], 'accounts.create_admin') is True
    # The dialog never bypasses the server: a forged Owner-only switch still
    # fails for an Admin after the UI journey.
    admin_client = signed_in(world['admin'].username, ADMIN_PASSWORD)
    assert admin_client.post('/users', {'op': 'platform_preview', 'username': world['owner'].username,
                                        'platform:accounts.create_admin': '1'}).status_code == 403
    assert admin_client.post('/users', {'op': 'delete', 'username': world['owner'].username,
                                        'confirm_username': world['owner'].username,
                                        'revision': str(accounts.instance_revision()),
                                        'password': ADMIN_PASSWORD}).status_code == 403


SCRIPT_DIALOG = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const adminPk=process.env.GEP_ADMIN_PK, victimPk=process.env.GEP_VICTIM_PK;
const victimName=process.env.GEP_VICTIM_NAME;
const observations={},errors=[],posts=[],statusRequests=[];
async function login(page,username,password){
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
}
try {
  const ctx=await browser.newContext({viewport:{width:1366,height:900}});
  const page=await ctx.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  page.on('request',r=>{
    if(r.method()==='POST'&&r.url().includes('/users')){posts.push(r.url());}
    if(r.url().includes('preview_status=')){statusRequests.push(r.url());}
  });
  await login(page,'p03r05_owner',ownerPassword);
  await page.goto(base+'/users');

  // 1) Keyboard cancel: Escape closes, sends nothing, returns the focus.
  let before=posts.length;
  await page.locator('[data-delete-account="'+victimPk+'"]').click();
  observations.focus_inside=
    await page.evaluate(()=>document.querySelector('dialog').contains(document.activeElement));
  await page.keyboard.press('Escape');
  observations.focus_returned=await page.evaluate(
    id=>document.activeElement===document.querySelector('[data-delete-account="'+id+'"]'),victimPk);
  observations.cancel_sent_no_request=(posts.length===before);
  expect(await page.locator('dialog[open]').count()).toBe(0);

  // 2) A wrong own password is refused by the server and nothing is deleted.
  await page.locator('[data-delete-account="'+victimPk+'"]').click();
  await page.locator('dialog [name=confirm_username]').fill(victimName);
  await page.locator('dialog [name=password]').fill('not-the-owner-password');
  await page.locator('dialog [data-confirm-submit]').click();
  await page.waitForFunction(()=>document.querySelector('div.error[role=alert]'));
  observations.wrong_password_refused=
    (await page.locator('div.error[role=alert]').first().innerText()).includes('重新认证失败');
  observations.victim_still_there=
    (await page.locator('[data-account-row="'+victimPk+'"]').count())===1;

  // 3) A double click writes once: the second click is locked out client-side.
  before=posts.length;
  await page.locator('[data-delete-account="'+victimPk+'"]').click();
  await page.locator('dialog [name=confirm_username]').fill(victimName);
  await page.locator('dialog [name=password]').fill(ownerPassword);
  await page.evaluate(()=>{
    const button=document.querySelector('dialog [data-confirm-submit]');
    button.click(); button.click();
  });
  await page.waitForFunction(id=>!document.querySelector('[data-account-row="'+id+'"]'),victimPk);
  observations.double_click_single_request=((posts.length-before)===1);
  observations.victim_row_gone=
    (await page.locator('[data-account-row="'+victimPk+'"]').count())===0;

  // 4) An unknown network result is checked through the preview status instead
  //    of claiming success; the retry then commits exactly once.
  await page.locator('[data-platform-switches="'+adminPk+'"] summary').click();
  await page.locator('[data-platform-switches="'+adminPk+'"] [name="platform:accounts.create_admin"][value="1"]').check();
  await page.locator('[data-platform-switches="'+adminPk+'"]').getByRole('button',{name:'预览平台开关'}).click();
  await page.waitForLoadState('load');
  const preview=page.locator('[data-preview="platform"]');
  await expect(preview).toBeVisible();
  await page.route('**/users',route=>route.request().method()==='POST'?route.abort():route.continue());
  await preview.getByRole('button',{name:/确认执行/}).click();
  await page.locator('dialog[open] [name=password]').fill(ownerPassword);
  await page.locator('dialog[open] [data-confirm-submit]').click();
  await page.waitForFunction(()=>{
    const state=document.querySelector('dialog [data-confirm-state]');
    return state&&state.textContent.includes('尚未完成');
  });
  const stateText=await page.locator('dialog [data-confirm-state]').innerText();
  observations.unknown_state=stateText.includes('尚未完成')?'pending-checked':stateText;
  observations.unknown_no_success=(await page.locator('.notice').count())===0;
  observations.status_queried=statusRequests.length>0;
  await page.unroute('**/users');
  await page.locator('dialog [data-confirm-retry]').click();
  await page.waitForFunction(()=>document.querySelector('.notice'));
  observations.platform_committed=
    (await page.locator('.notice').innerText()).includes('平台权限已更新');
  observations.status_requests=statusRequests.length;
} finally {await browser.close();}
observations.page_errors=errors;
console.log('P03R05_DIALOG '+JSON.stringify(observations));
'''
