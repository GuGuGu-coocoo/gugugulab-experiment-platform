"""P03R05R evidence: the guarded-review corrections after R05.

Expected side: the confirmed P03R05R requirements (guarded review of
``docs/internal/completed/reports/p03r05_review.md`` plus the R00 contract
section A), never the implementation under test:

- the running application itself serves the fixed public permission script
  through a small whitelist route (no test-only static handler, no directory
  serving); a real HTTP service (subprocess ``runserver``, unique temporary
  database) proves the asset, its MIME type and a real dialog commit;
- ``GET /users?preview_status=`` serves only the actor's own minimal operation
  state: no stored result, no study/account name, no identity, no token.
  Revoked, downgraded, disabled, must-change, cross-actor and unknown-kind
  queries fail closed;
- the v2 matrix commit goes through the same password-only dialog as delete and
  platform switches (v1 keeps the inline path); cancel/Escape send nothing and
  return the focus to the real trigger button; an in-flight submit owns the
  lock, so Escape/close/re-open cannot unlock it or start a second request;
- an unknown submit result (fetch failure or unreadable response body) is
  checked through the read-only status query instead of claiming success, and
  the pending wording never claims the original request did not run;
- drafts are scoped to the instance plus the actor's stable subject, survive
  paging/filtering and a governance revision change (marked as needing a fresh
  preview, never silently applied or dropped), a successful commit removes only
  its own entry, and a revoked/read-only entry can never be restored to the UI
  or submitted.

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r05r/<UTC>-<random>/`` root.
"""
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core.models import (AccountProfile, Audit, Instance, PermissionPreview,
                         Principal, Study)

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = 'synthetic-p03r05r-owner-password'
ADMIN_PASSWORD = 'synthetic-p03r05r-admin-password'
USER_PASSWORD = 'synthetic-p03r05r-user-password'
REAL_OWNER_PASSWORD = 'synthetic-p03r05r-real-owner-password'
REAL_USER_PASSWORD = 'synthetic-p03r05r-real-user-password'
VIEW = 'study.view'
MATRIX_USER_ROW_RE = re.compile(r'data-matrix-user-row="(\d+)"')
PREVIEW_RE = re.compile(r'data-preview-id="([0-9a-fA-F-]{36})"')

browser_only = pytest.mark.skipif(
    os.environ.get('GEP_T17_BROWSER') != '1',
    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')


# --- synthetic world ---------------------------------------------------------

def make_user(username, password, *, role='user', platform=None, studies=None):
    user = get_user_model().objects.create_user(username, password=password)
    Principal.objects.create(user=user)
    AccountProfile.objects.create(user=user, role=role, policy_version=2,
                                  platform_overrides=dict(platform or {}),
                                  study_overrides=dict(studies or {}))
    return user


def signed_in(username, password):
    client = Client()
    assert client.post('/login', {'username': username, 'password': password}).status_code == 302
    return client


@pytest.fixture
def world(db):
    owner = make_user('p03r05r_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    study_a = Study.objects.create(title='P03R05R study A', recruitment='open', max_sessions=5)
    study_b = Study.objects.create(title='P03R05R study B')
    AccountProfile.objects.filter(user=owner).update(
        study_overrides={str(study_a.pk): [VIEW, 'study.configure'],
                         str(study_b.pk): [VIEW, 'study.configure']})
    admin = make_user('p03r05r_admin', ADMIN_PASSWORD, role='admin',
                      platform={'accounts.manage_user': True},
                      studies={str(study_a.pk): [VIEW, 'study.configure'],
                               str(study_b.pk): [VIEW, 'study.configure']})
    target = make_user('p03r05r_target', USER_PASSWORD,
                       studies={str(study_a.pk): [VIEW], str(study_b.pk): [VIEW]})
    return {'owner': owner, 'instance': instance, 'study_a': study_a, 'study_b': study_b,
            'admin': admin, 'target': target}


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


def run_chrome(script, env, observations_key, timeout=300):
    result = subprocess.run(['node', '--input-type=module', '-e', script],
                            cwd=REPO_ROOT, env=dict(os.environ, **env),
                            capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    match = re.search(rf'{observations_key} ({{.*}})', result.stdout)
    assert match, result.stdout[-2000:] + result.stderr[-2000:]
    return json.loads(match.group(1))


# --- the real application route serves the fixed asset -----------------------

def test_application_route_serves_the_whitelisted_script_only(db):
    client = Client()
    response = client.get('/static/core/permissions.js')
    assert response.status_code == 200
    assert 'javascript' in response['Content-Type']
    assert response['X-GEP-Asset'] == 'core/permissions.js'
    body = b''.join(response.streaming_content).decode()
    assert 'data-confirm-dialog' in body
    # The whitelist is exact: no other path, no traversal and no other method.
    assert client.get('/static/AGENTS.md').status_code == 404
    assert client.get('/static/core/../../AGENTS.md').status_code == 404
    assert client.get('/static/core/permissions.js/extra').status_code == 404
    assert client.get('/static/').status_code == 404
    assert client.post('/static/core/permissions.js').status_code == 405


def test_v2_matrix_preview_commit_uses_the_dialog_and_v1_keeps_inline(db):
    owner = make_user('p03r05r_dialog_owner', OWNER_PASSWORD)
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    study = Study.objects.create(title='P03R05R dialog study')
    AccountProfile.objects.filter(user=owner).update(
        study_overrides={str(study.pk): [VIEW, 'study.configure']})
    target = make_user('p03r05r_dialog_target', USER_PASSWORD,
                       studies={str(study.pk): [VIEW]})
    client = signed_in(owner.username, OWNER_PASSWORD)
    preview = preview_payload(client, target.pk, study.pk, visibility=True, actions=['session.view'])
    assert preview.status_code == 200
    body = preview.content.decode()
    assert 'data-preview="matrix"' in body
    section = body[body.index('<section data-preview="matrix"'):]
    form = section[section.index('<form'):section.index('</form>')]
    assert 'data-confirm="password"' in form
    # The v1 page keeps the inline password confirmation and never loads the
    # dialog script at all.
    AccountProfile.objects.filter(user=owner).update(policy_version=1)
    Instance.objects.filter(pk=1).update(authorization_version=1)
    v1_body = preview_payload(client, target.pk, study.pk, visibility=True).content.decode()
    assert 'data-preview="matrix"' in v1_body
    v1_section = v1_body[v1_body.index('<section data-preview="matrix"'):]
    v1_form = v1_section[v1_section.index('<form'):v1_section.index('</form>')]
    assert 'data-confirm="password"' not in v1_form
    assert 'data-confirm-dialog' not in v1_body
    assert '/static/core/permissions.js' not in v1_body


# --- minimal preview status --------------------------------------------------

def test_preview_status_is_minimal_and_fails_closed(world):
    owner, admin, target, study_a = world['owner'], world['admin'], world['target'], world['study_a']
    client = signed_in(owner.username, OWNER_PASSWORD)
    preview = preview_payload(client, target.pk, study_a.pk, visibility=True, actions=['audit.view'])
    assert preview.status_code == 200
    pid = preview_id_of(preview)
    response = client.get(f'/users?preview_status={pid}')
    assert response.status_code == 200
    body = response.content.decode()
    data = response.json()
    assert data == {'preview_id': pid, 'kind': 'matrix', 'state': 'pending',
                    'expires_at': data['expires_at']}
    assert target.username not in body and study_a.title not in body

    # A committed preview reports the state without any stored object data; the
    # status query itself writes nothing.
    assert client.post('/users', {'op': 'matrix_commit', 'preview_id': pid,
                                  'password': OWNER_PASSWORD}).status_code == 200
    after = client.get(f'/users?preview_status={pid}')
    assert after.json()['state'] == 'consumed'
    assert 'result' not in after.json()
    assert target.username not in after.content.decode()
    assert study_a.title not in after.content.decode()

    # Cross-actor, unknown kind, malformed id and a revoked query all fail
    # closed instead of returning an old object.
    admin_client = signed_in(admin.username, ADMIN_PASSWORD)
    assert admin_client.get(f'/users?preview_status={pid}').status_code == 404
    reconcile = PermissionPreview.objects.create(
        actor=owner, kind='reconcile', scope='', summary={}, errors=[], binding='b' * 64,
        staged=None, expires_at=after.json()['expires_at'])
    assert client.get(f'/users?preview_status={reconcile.pk}').status_code == 404
    assert client.get('/users?preview_status=not-a-uuid').status_code == 404
    # An actor without accounts.view cannot use the query at all.
    assert signed_in(target.username, USER_PASSWORD).get(
        f'/users?preview_status={pid}').status_code == 403
    # A must-change account is stopped by the same gate as every permission
    # entry (the account gate redirects it to the password page), and disabling
    # the actor revokes the query. Neither can read the old state.
    AccountProfile.objects.filter(user=owner).update(must_change_password=True)
    forced = client.get(f'/users?preview_status={pid}')
    assert forced.status_code == 302 and forced.url == '/account/password'
    AccountProfile.objects.filter(user=owner).update(must_change_password=False)
    owner.is_active = False
    owner.save(update_fields=['is_active'])
    denied = client.get(f'/users?preview_status={pid}')
    assert denied.status_code == 302 and denied.url == '/login'


def test_refused_commit_does_not_rerender_a_revoked_preview(world):
    """The error page of a refused commit must not become a second leak path.

    A recoverable refusal (a mistyped own password) still shows the actor their
    pending preview so they can retry; once the current authority for that exact
    operation is gone, the same refused commit must not re-render the stored
    account or study names.
    """
    admin, target, study_a = world['admin'], world['target'], world['study_a']
    client = signed_in(admin.username, ADMIN_PASSWORD)
    preview = preview_payload(client, target.pk, study_a.pk, visibility=True,
                              actions=['study.configure'])
    assert preview.status_code == 200
    pid = preview_id_of(preview)
    refused = client.post('/users', {'op': 'matrix_commit', 'preview_id': pid,
                                     'password': 'wrong-password-2026'})
    assert refused.status_code == 403
    body = refused.content.decode()
    assert '重新认证失败' in body and 'data-preview="matrix"' in body
    assert study_a.title in body
    # Revoke the Admin's own visibility of the study: the same refusal (wrong
    # and then correct password) no longer re-renders the stored preview, and
    # the study title is gone from the whole page (the Admin cannot see it).
    profile = AccountProfile.objects.get(user=admin)
    profile.study_overrides[str(study_a.pk)] = []
    profile.revision += 1
    profile.save(update_fields=['study_overrides', 'revision'])
    for password in ('wrong-password-2026', ADMIN_PASSWORD):
        after = client.post('/users', {'op': 'matrix_commit', 'preview_id': pid,
                                       'password': password})
        assert after.status_code == 403
        body = after.content.decode()
        assert 'data-preview="matrix"' not in body
        assert study_a.title not in body
    assert AccountProfile.objects.get(user=target).study_overrides[str(study_a.pk)] == [VIEW]


def test_draft_scope_is_instance_and_subject_bound(world):
    owner = world['owner']
    body = signed_in(owner.username, OWNER_PASSWORD).get('/users').content.decode()
    scope = re.search(r'data-draft-scope="([^"]+)"', body).group(1)
    principal = Principal.objects.get(user=owner)
    assert scope == f'{world["instance"].instance_id}:{principal.pk}'
    # The v2 page marks every read-only entry as locked, so a client can drop a
    # revoked entry's draft instead of restoring it.
    admin_client = signed_in(world['admin'].username, ADMIN_PASSWORD)
    admin_body = admin_client.get('/users').content.decode()
    assert f'data-locked-entry="{world["owner"].pk}-{world["study_a"].pk}"' in admin_body


# --- real Chrome: the matrix dialog, the in-flight lock and the focus --------

@browser_only
def test_actual_chrome_matrix_dialog_cancel_refusal_and_inflight_lock(
        live_server, world, evidence, evidence_root):
    env = {'GEP_TEST_BASE': live_server.url, 'GEP_OWNER_PASSWORD': OWNER_PASSWORD,
           'GEP_TARGET_PK': str(world['target'].pk), 'GEP_STUDY_A': str(world['study_a'].pk),
           'GEP_EVIDENCE': str(evidence_root)}
    observations = run_chrome(SCRIPT_MATRIX_DIALOG, env, 'P03R05R_MATRIX')
    assert observations['page_errors'] == []
    assert observations['asset_marker'] == 'core/permissions.js'
    assert observations['preview_shows_diff'] is True
    assert observations['dialog_opened'] is True
    assert observations['focus_inside'] is True
    assert observations['escape_sent_no_request'] is True
    assert observations['focus_returned_to_trigger'] is True
    assert observations['wrong_password_refused'] is True
    assert observations['preview_still_usable'] is True
    assert observations['inflight_escape_kept_lock'] is True
    assert observations['inflight_reopen_refused'] is True
    assert observations['single_commit'] is True
    assert observations['stored_after_commit'] is True
    evidence('chrome_matrix_dialog.json', observations)
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 1


SCRIPT_MATRIX_DIALOG = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const targetPk=process.env.GEP_TARGET_PK, studyA=process.env.GEP_STUDY_A;
const observations={},errors=[],posts=[],asset=[];
async function login(page){
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('p03r05r_owner');
  await page.locator('[name=password]').fill(ownerPassword);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
}
try {
  const ctx=await browser.newContext({viewport:{width:1366,height:900}});
  const page=await ctx.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/users')){posts.push(r.url());}});
  page.on('response',r=>{if(r.url().includes('/static/core/permissions.js')){asset.push([r.status(),r.headers()['x-gep-asset']||'']);}});
  await login(page);
  await page.goto(base+'/users');
  await page.waitForLoadState('load');
  observations.asset_marker=asset.length?asset[0][1]:'missing';
  expect(asset.length&&asset[0][0]).toBe(200);

  const entry='form[data-matrix-entry="'+targetPk+'-'+studyA+'"]';
  await page.locator(entry+' details summary').click();
  await page.locator(entry+' [name="action:session.view"]').check();
  await page.locator(entry+' button').click();
  await page.waitForLoadState('load');
  const preview=page.locator('[data-preview="matrix"]');
  await expect(preview).toBeVisible();
  observations.preview_shows_diff=(await preview.innerText()).includes('session.view');
  const confirmButton=preview.getByRole('button',{name:/确认执行/});

  // 1) The matrix commit opens the unified dialog; Escape sends nothing and
  //    returns the focus to the real preview-confirm button.
  let before=posts.length;
  await confirmButton.click();
  const dialog=page.locator('[data-confirm-dialog][open]');
  await expect(dialog).toBeVisible();
  observations.dialog_opened=true;
  observations.dialog_op=(await dialog.locator('[name=op]').inputValue());
  observations.focus_inside=await page.evaluate(
    ()=>document.querySelector('[data-confirm-dialog]').contains(document.activeElement));
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-confirm-dialog][open]')).toHaveCount(0);
  observations.escape_sent_no_request=(posts.length===before);
  observations.focus_returned_to_trigger=await page.evaluate(
    ()=>document.activeElement===document.querySelector('[data-preview="matrix"] button'));

  // 2) A wrong own password is refused by the server (still-valid preview is
  //    shown again); the real password then commits exactly once.
  await confirmButton.click();
  await page.locator('[data-confirm-dialog][open] [name=password]').fill('wrong-password-2026');
  await page.locator('[data-confirm-dialog][open] [data-confirm-submit]').click();
  await page.waitForFunction(()=>document.querySelector('div.error[role=alert]'));
  observations.wrong_password_refused=
    (await page.locator('div.error[role=alert]').first().innerText()).includes('重新认证失败');
  observations.preview_still_usable=(await page.locator('[data-preview="matrix"]').count())===1;

  // 3) An in-flight submit owns the lock: a delayed POST plus Escape and a
  //    delete-trigger click must not unlock it or start a second request.
  await page.route('**/users',async route=>{
    if(route.request().method()==='POST'){await new Promise(r=>setTimeout(r,1500));}
    await route.continue();
  });
  before=posts.length;
  await page.locator('[data-preview="matrix"] button').click();
  await page.locator('[data-confirm-dialog][open] [name=password]').fill(ownerPassword);
  await page.locator('[data-confirm-dialog][open] [data-confirm-submit]').click();
  await page.waitForTimeout(300);
  await page.keyboard.press('Escape');
  await page.locator('[data-account-row="'+targetPk+'"] [data-delete-account]').click({force:true});
  observations.inflight_escape_kept_lock=(await page.locator('[data-confirm-dialog][open]').count())===1;
  observations.inflight_reopen_refused=
    (await page.locator('[data-confirm-dialog][open] [name=op]').inputValue())==='matrix_commit';
  await page.waitForFunction(()=>document.querySelector('.notice'));
  observations.single_commit=((posts.length-before)===1);
  observations.notice=(await page.locator('.notice').innerText()).includes('权限矩阵已更新');
  await page.unroute('**/users');
  await page.goto(base+'/users');
  await page.locator(entry+' details summary').click();
  observations.stored_after_commit=await page.locator(entry+' [name="action:session.view"]').isChecked();
  await page.screenshot({path:process.env.GEP_EVIDENCE+'/matrix_dialog_zh.png',fullPage:false});
} finally {await browser.close();}
observations.page_errors=errors;
console.log('P03R05R_MATRIX '+JSON.stringify(observations));
'''


# --- real Chrome: unknown results never fake success -------------------------

@browser_only
def test_actual_chrome_unknown_results_check_status_instead_of_success(
        live_server, world, evidence, evidence_root):
    env = {'GEP_TEST_BASE': live_server.url, 'GEP_OWNER_PASSWORD': OWNER_PASSWORD,
           'GEP_TARGET_PK': str(world['target'].pk), 'GEP_STUDY_A': str(world['study_a'].pk),
           'GEP_EVIDENCE': str(evidence_root)}
    observations = run_chrome(SCRIPT_UNKNOWN, env, 'P03R05R_UNKNOWN')
    assert observations['page_errors'] == []
    assert observations['ack_lost_committed_state'] is True, observations
    assert observations['ack_lost_no_success_before_check'] is True
    assert observations['ack_lost_status_queried'] is True
    assert observations['body_failure_pending_state'] is True
    assert observations['pending_wording_no_claim'] is True
    assert observations['body_failure_no_success'] is True
    assert observations['retry_committed_once'] is True
    evidence('chrome_unknown.json', observations)
    # Server truth: exactly one commit per operation (the ACK-lost request and
    # the retry each wrote once), and no second write from the retry.
    profile = AccountProfile.objects.get(user=world['target'])
    assert sorted(profile.study_overrides[str(world['study_a'].pk)]) == sorted(
        [VIEW, 'audit.view', 'data.export_raw'])
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 2


SCRIPT_UNKNOWN = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const targetPk=process.env.GEP_TARGET_PK, studyA=process.env.GEP_STUDY_A;
const observations={},errors=[],posts=[],statusRequests=[];
const entry='form[data-matrix-entry="'+process.env.GEP_TARGET_PK+'-'+process.env.GEP_STUDY_A+'"]';
async function login(page){
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('p03r05r_owner');
  await page.locator('[name=password]').fill(ownerPassword);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
}
async function openPreview(page,action){
  await page.goto(base+'/users');
  await page.locator(entry+' details summary').click();
  await page.locator(entry+' [name="action:'+action+'"]').check();
  await page.locator(entry+' button').click();
  await page.waitForLoadState('load');
  await expect(page.locator('[data-preview="matrix"]')).toBeVisible();
  await page.locator('[data-preview="matrix"] button').click();
  await expect(page.locator('[data-confirm-dialog][open]')).toBeVisible();
  await page.locator('[data-confirm-dialog][open] [name=password]').fill(ownerPassword);
}
try {
  const ctx=await browser.newContext({viewport:{width:1366,height:900}});
  const page=await ctx.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  page.on('request',r=>{
    if(r.method()==='POST'&&r.url().includes('/users')){posts.push(r.url());}
    if(r.url().includes('preview_status=')){statusRequests.push(r.url());}
  });
  await login(page);

  // 1) The server really committed but the client never received the answer:
  //    the real POST runs to completion and its response is then discarded, so
  //    the page sees a network failure. The dialog must ask the read-only
  //    status, show "already executed" and refresh - never show a success
  //    notice on the stale page.
  await openPreview(page,'audit.view');
  await page.evaluate(()=>{
    const real=window.fetch.bind(window);
    window.__gepRealFetch=real;
    window.fetch=(url,options)=>{
      if(options&&options.method==='POST'){
        return real(url,options).then(()=>{throw new TypeError('synthetic lost acknowledgement');});
      }
      return real(url,options);
    };
  });
  let before=posts.length;
  await page.locator('[data-confirm-dialog][open] [data-confirm-submit]').click();
  let committedSeen='';
  try {
    await page.waitForFunction(()=>{
      const state=document.querySelector('[data-confirm-dialog] [data-confirm-state]');
      return state&&state.textContent.includes('已执行');
    },{timeout:8000});
  } catch (error) {
    committedSeen=await page.locator('[data-confirm-dialog] [data-confirm-state]')
      .innerText().catch(()=>'no-state-element');
  }
  observations.ack_lost_committed_state=(committedSeen==='');
  observations.ack_lost_debug_state=committedSeen;
  observations.ack_lost_debug_posts=posts.length-before;
  observations.ack_lost_debug_status=statusRequests.length;
  observations.ack_lost_no_success_before_check=(await page.locator('.notice').count())===0;
  // The client refreshes to the real server state after the consumed answer.
  await expect(page.locator(entry+' [name="action:audit.view"]')).toBeChecked();
  observations.ack_lost_status_queried=statusRequests.length>0;
  expect(posts.length-before).toBe(1);

  // 2) The response headers arrived but reading the body failed. The client
  //    must run the same read-only check: the server never saw this request, so
  //    the honest answer is "not finished at query time", with no success claim
  //    and a retry that still writes exactly once.
  await openPreview(page,'data.export_raw');
  await page.evaluate(()=>{
    const real=window.fetch.bind(window);
    window.__gepRealFetch=real;
    window.fetch=(url,options)=>{
      if(options&&options.method==='POST'){
        const stream=new ReadableStream({start(controller){controller.error(new Error('synthetic body failure'));}});
        return Promise.resolve(new Response(stream,{status:200,headers:{'Content-Type':'text/html'}}));
      }
      return real(url,options);
    };
  });
  before=posts.length;
  const statusBefore=statusRequests.length;
  await page.locator('[data-confirm-dialog][open] [data-confirm-submit]').click();
  await page.waitForFunction(()=>{
    const state=document.querySelector('[data-confirm-dialog] [data-confirm-state]');
    return state&&state.textContent.includes('尚未完成');
  });
  observations.body_failure_pending_state=true;
  const stateText=await page.locator('[data-confirm-dialog] [data-confirm-state]').innerText();
  observations.pending_wording_no_claim=!stateText.includes('尚未执行');
  observations.body_failure_no_success=(await page.locator('.notice').count())===0;
  observations.body_failure_status_queried=(statusRequests.length>statusBefore);
  observations.pending_posts=(posts.length-before);
  await page.evaluate(()=>{window.fetch=window.__gepRealFetch;});
  await page.locator('[data-confirm-dialog] [data-confirm-retry]').click();
  await page.waitForFunction(()=>document.querySelector('.notice'));
  observations.retry_committed_once=(posts.length-before)===1;
  expect((await page.locator('.notice').innerText()).includes('权限矩阵已更新')).toBe(true);
  await page.screenshot({path:process.env.GEP_EVIDENCE+'/unknown_zh.png',fullPage:false});
} finally {await browser.close();}
observations.page_errors=errors;
console.log('P03R05R_UNKNOWN '+JSON.stringify(observations));
'''


# --- real Chrome: drafts across commits, accounts and revocation -------------

@browser_only
def test_actual_chrome_drafts_commit_scope_isolation_and_revocation(
        live_server, world, evidence, evidence_root):
    env = {'GEP_TEST_BASE': live_server.url, 'GEP_OWNER_PASSWORD': OWNER_PASSWORD,
           'GEP_ADMIN_PASSWORD': ADMIN_PASSWORD, 'GEP_TARGET_PK': str(world['target'].pk),
           'GEP_STUDY_A': str(world['study_a'].pk), 'GEP_STUDY_B': str(world['study_b'].pk),
           'GEP_ADMIN_PK': str(world['admin'].pk), 'GEP_EVIDENCE': str(evidence_root)}
    observations = run_chrome(SCRIPT_DRAFTS, env, 'P03R05R_DRAFTS')
    assert observations['page_errors'] == []
    assert observations['two_drafts'] is True
    assert observations['drafts_survive_filter'] is True
    assert observations['committed_one'] is True
    assert observations['other_draft_kept'] is True
    assert observations['other_draft_marked_stale'] is True
    assert observations['other_account_no_draft'] is True
    assert observations['same_actor_draft_back'] is True
    assert observations['revoked_entry_locked'] is True
    assert observations['revoked_draft_dropped'] is True
    assert observations['revoked_forge_refused'] == 403
    evidence('chrome_drafts.json', observations)
    # Server truth after the browser journey: exactly the one committed entry
    # changed; the revoked draft was never submitted.
    profile = AccountProfile.objects.get(user=world['target'])
    assert profile.study_overrides[str(world['study_a'].pk)] == []
    assert sorted(profile.study_overrides[str(world['study_b'].pk)]) == [VIEW]
    assert AccountProfile.objects.get(user=world['admin']).platform_overrides[
        'accounts.manage_user'] is False
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 1


SCRIPT_DRAFTS = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const adminPassword=process.env.GEP_ADMIN_PASSWORD, adminPk=process.env.GEP_ADMIN_PK;
const targetPk=process.env.GEP_TARGET_PK, studyA=process.env.GEP_STUDY_A, studyB=process.env.GEP_STUDY_B;
const observations={},errors=[];
async function login(page,username,password){
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
}
async function logout(page){
  await page.locator('[data-nav-link="logout"]').click();
  await page.waitForURL(base+'/login');
}
try {
  const ctx=await browser.newContext({viewport:{width:1366,height:900}});
  const page=await ctx.newPage();
  page.on('pageerror',e=>errors.push('journey:'+e.message));
  const entryA='form[data-matrix-entry="'+targetPk+'-'+studyA+'"]';
  const entryB='form[data-matrix-entry="'+targetPk+'-'+studyB+'"]';

  // Admin: two drafts on two studies, kept across a filter navigation.
  await login(page,'p03r05r_admin',adminPassword);
  await page.goto(base+'/users');
  await page.locator(entryA+' details summary').click();
  await page.locator(entryA+' [name=visibility]').uncheck();
  await page.locator(entryB+' details summary').click();
  await page.locator(entryB+' [name="action:study.configure"]').check();
  observations.two_drafts=await page.locator('[data-draft-banner]').isVisible();
  await page.goto(base+'/users?q=p03r05r_target');
  observations.drafts_survive_filter=
    await page.locator('[data-draft-banner]').isVisible()
    && await page.locator(entryB+' [name="action:study.configure"]').isChecked();
  await page.goto(base+'/users');

  // Commit exactly the first draft through the unified dialog.
  await page.locator(entryA+' details summary').click();
  await page.locator(entryA+' button').click();
  await page.waitForLoadState('load');
  await page.locator('[data-preview="matrix"] button').click();
  await page.locator('[data-confirm-dialog][open] [name=password]').fill(adminPassword);
  await page.locator('[data-confirm-dialog][open] [data-confirm-submit]').click();
  await page.waitForFunction(()=>document.querySelector('.notice'));
  observations.committed_one=
    (await page.locator('[data-committed-kind="matrix"]').count())===1
    && (await page.locator('.notice').innerText()).includes('权限矩阵已更新');

  // The other draft survives the revision change, is still checked and is
  // explicitly marked as needing a fresh preview.
  await page.locator(entryB+' details summary').click();
  observations.other_draft_kept=await page.locator(entryB+' [name="action:study.configure"]').isChecked();
  observations.other_draft_marked_stale=
    (await page.locator(entryB+'[data-draft-stale="1"]').count())===1
    && (await page.locator('[data-draft-banner]').innerText()).includes('旧治理版本');

  // Same tab, another account: the previous actor's draft is never read.
  await logout(page);
  await login(page,'p03r05r_owner',ownerPassword);
  await page.goto(base+'/users');
  observations.other_account_no_draft=
    !(await page.locator('[data-draft-banner]').isVisible())
    && !(await page.locator(entryB+' [name="action:study.configure"]').isChecked());
  await logout(page);
  await login(page,'p03r05r_admin',adminPassword);
  await page.goto(base+'/users');
  await page.locator(entryB+' details summary').click();
  observations.same_actor_draft_back=
    (await page.locator('[data-draft-banner]').isVisible())
    && await page.locator(entryB+' [name="action:study.configure"]').isChecked();

  // The Owner revokes the Admin's lifecycle capability in a second context.
  const ownerCtx=await browser.newContext();
  const owner=await ownerCtx.newPage();
  owner.on('pageerror',e=>errors.push('owner:'+e.message));
  await login(owner,'p03r05r_owner',ownerPassword);
  await owner.goto(base+'/users');
  await owner.locator('[data-platform-switches="'+adminPk+'"] summary').click();
  await owner.locator('[data-platform-switches="'+adminPk+'"] [name="platform:accounts.manage_user"][value="0"]').check();
  await owner.locator('[data-platform-switches="'+adminPk+'"]').getByRole('button',{name:'预览平台开关'}).click();
  await owner.waitForLoadState('load');
  await owner.locator('[data-preview="platform"] button').click();
  await owner.locator('[data-confirm-dialog][open] [name=password]').fill(ownerPassword);
  await owner.locator('[data-confirm-dialog][open] [data-confirm-submit]').click();
  await owner.waitForFunction(()=>document.querySelector('.notice'));
  expect((await owner.locator('.notice').innerText()).includes('平台权限已更新')).toBe(true);
  await ownerCtx.close();

  // Back in the Admin's tab: the revoked entry is read-only, its draft is
  // dropped and can never be restored to the UI or submitted.
  await page.reload();
  observations.revoked_entry_locked=
    (await page.locator('[data-locked-entry="'+targetPk+'-'+studyB+'"]').count())===1
    && (await page.locator(entryB).count())===0;
  observations.revoked_draft_dropped=!(await page.locator('[data-draft-banner]').isVisible());
  observations.revoked_forge_refused=await page.evaluate(async ({userId,studyId})=>{
    const token=document.querySelector('[name=csrfmiddlewaretoken]').value;
    const body=new URLSearchParams({op:'matrix_preview',user_id:String(userId),study_id:String(studyId),visibility:'1','action:session.view':'1'});
    const response=await fetch('/users',{method:'POST',headers:{'X-CSRFToken':token,'Content-Type':'application/x-www-form-urlencoded'},body});
    return response.status;
  },{userId:targetPk,studyId:studyB});
  await page.screenshot({path:process.env.GEP_EVIDENCE+'/drafts_revoked.png',fullPage:false});
} finally {await browser.close();}
observations.page_errors=errors;
console.log('P03R05R_DRAFTS '+JSON.stringify(observations));
'''


# --- real HTTP service (subprocess runserver) + real Chrome ------------------

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _wait_for_http(url, timeout=45):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return response.status, response.headers, response.read()
        except (urllib.error.URLError, ConnectionError, OSError) as error:
            last = error
            time.sleep(0.25)
    raise AssertionError(f'real application service did not answer {url}: {last!r}')


SEED_SCRIPT = '''
import uuid
from django.contrib.auth import get_user_model
from core.models import AccountProfile, Instance, Principal, Study
User = get_user_model()
owner = User.objects.create_user('p03r05r_real_owner', password='%s')
Principal.objects.create(user=owner)
AccountProfile.objects.create(user=owner, role='admin', policy_version=2)
instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
study = Study.objects.create(title='P03R05R real service study', recruitment='open', max_sessions=3)
AccountProfile.objects.filter(user=owner).update(study_overrides={str(study.pk): ['study.view', 'study.configure']})
target = User.objects.create_user('p03r05r_real_target', password='%s')
Principal.objects.create(user=target)
AccountProfile.objects.create(user=target, role='user', policy_version=2, study_overrides={str(study.pk): ['study.view']})
print('SEEDED', target.pk, study.pk)
''' % (REAL_OWNER_PASSWORD, REAL_USER_PASSWORD)


@browser_only
def test_real_application_http_service_serves_asset_and_dialog(
        evidence, evidence_root):
    """A real subprocess HTTP service, unique temp database, real Chrome.

    This is deliberately not the pytest ``live_server`` wrapper: the running
    application must serve its own asset route and the browser must complete a
    real permission change through the unified dialog against it.
    """
    root = evidence_root / 'real_service'
    root.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, GEP_DATA_DIR=str(root), GEP_SECRET_KEY='synthetic-p03r05r-real-service-key',
               PYTHONUNBUFFERED='1')
    env.pop('DJANGO_SETTINGS_MODULE', None)
    migrate = subprocess.run([sys.executable, 'server/manage.py', 'migrate', '--noinput'],
                             cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=240)
    assert migrate.returncode == 0, migrate.stdout[-2000:] + migrate.stderr[-2000:]
    seeded = subprocess.run([sys.executable, 'server/manage.py', 'shell'],
                            cwd=REPO_ROOT, env=env, input=SEED_SCRIPT,
                            capture_output=True, text=True, timeout=120)
    assert seeded.returncode == 0, seeded.stdout[-2000:] + seeded.stderr[-2000:]
    match = re.search(r'SEEDED (\d+) ([0-9a-f-]{36})', seeded.stdout)
    assert match, seeded.stdout[-2000:] + seeded.stderr[-2000:]
    target_pk, study_id = match.group(1), match.group(2)

    port = _free_port()
    log_path = root / 'runserver.log'
    log = log_path.open('w')
    server = subprocess.Popen([sys.executable, 'server/manage.py', 'runserver',
                               f'localhost:{port}', '--noreload'],
                              cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f'http://localhost:{port}'
    try:
        status, headers, body = _wait_for_http(base + '/static/core/permissions.js')
        assert status == 200
        assert 'javascript' in headers.get('Content-Type', '')
        assert headers.get('X-GEP-Asset') == 'core/permissions.js'
        assert b'data-confirm-dialog' in body
        # The real service rejects unknown assets and traversal with 404.
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(base + '/static/AGENTS.md', timeout=5)
        assert denied.value.code == 404
        observations = run_chrome(
            SCRIPT_REAL_SERVICE,
            {'GEP_TEST_BASE': base, 'GEP_OWNER_PASSWORD': REAL_OWNER_PASSWORD,
             'GEP_TARGET_PK': target_pk, 'GEP_STUDY_ID': study_id,
             'GEP_EVIDENCE': str(evidence_root)},
            'P03R05R_REAL', timeout=300)
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        log.close()
    assert observations['page_errors'] == []
    assert observations['asset_status'] == 200
    assert observations['asset_marker'] == 'core/permissions.js'
    assert observations['dialog_opened'] is True
    assert observations['committed'] is True
    evidence('chrome_real_service.json', observations)
    # The real service really wrote the change into its own temporary database.
    connection = sqlite3.connect(root / 'gep.sqlite3')
    try:
        stored = connection.execute(
            'SELECT study_overrides FROM core_accountprofile WHERE user_id = ?',
            (int(target_pk),)).fetchone()[0]
    finally:
        connection.close()
    assert sorted(json.loads(stored)[study_id]) == sorted([VIEW, 'session.view'])


SCRIPT_REAL_SERVICE = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const targetPk=process.env.GEP_TARGET_PK, studyId=process.env.GEP_STUDY_ID;
const observations={},errors=[],assets=[];
try {
  const ctx=await browser.newContext({viewport:{width:1366,height:900}});
  const page=await ctx.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  page.on('response',r=>{if(r.url().includes('/static/core/permissions.js')){assets.push([r.status(),r.headers()['x-gep-asset']||'']);}});
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('p03r05r_real_owner');
  await page.locator('[name=password]').fill(ownerPassword);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/users');
  await page.waitForLoadState('load');
  observations.asset_status=assets.length?assets[0][0]:0;
  observations.asset_marker=assets.length?assets[0][1]:'missing';
  const entry='form[data-matrix-entry="'+targetPk+'-'+studyId+'"]';
  await page.locator(entry+' details summary').click();
  await page.locator(entry+' [name="action:session.view"]').check();
  await page.locator(entry+' button').click();
  await page.waitForLoadState('load');
  await expect(page.locator('[data-preview="matrix"]')).toBeVisible();
  await page.locator('[data-preview="matrix"] button').click();
  await expect(page.locator('[data-confirm-dialog][open]')).toBeVisible();
  observations.dialog_opened=true;
  await page.locator('[data-confirm-dialog][open] [name=password]').fill(ownerPassword);
  await page.locator('[data-confirm-dialog][open] [data-confirm-submit]').click();
  await page.waitForFunction(()=>document.querySelector('.notice'));
  observations.committed=(await page.locator('.notice').innerText()).includes('权限矩阵已更新');
  await page.screenshot({path:process.env.GEP_EVIDENCE+'/real_service_zh.png',fullPage:false});
} finally {await browser.close();}
observations.page_errors=errors;
console.log('P03R05R_REAL '+JSON.stringify(observations));
'''
