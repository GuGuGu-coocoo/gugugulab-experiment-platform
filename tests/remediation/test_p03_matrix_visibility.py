"""Real Chrome evidence for matrix visibility and lifecycle confirmation.

The requirements are current_requirements §2: invisible studies have no usable
child permissions; restoring visibility never resurrects old actions; bulk
selection stays within the rendered editable scope; page/filter drafts retain
their account and instance scope. Real HTTP previews and commits prove that
these mouse-selected combinations are accepted by the actual server.
"""
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from core.models import AccountInvitation, AccountProfile, Audit, Instance, Principal, Study

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = 'Synthetic-matrix-owner-1!'
ADMIN_PASSWORD = 'Synthetic-matrix-admin-1!'
VIEW = 'study.view'
ADMIN_A = [VIEW, 'study.configure', 'session.view', 'audit.view']
ADMIN_B = [VIEW, 'study.configure', 'session.recover']

browser_only = pytest.mark.skipif(
    os.environ.get('GEP_T17_BROWSER') != '1',
    reason='Set GEP_T17_BROWSER=1 for the real Chrome journey')


def make_user(username, password, *, role='user', studies=None):
    user = get_user_model().objects.create_user(username, password=password)
    Principal.objects.create(user=user)
    AccountProfile.objects.create(user=user, role=role, policy_version=2,
                                  study_overrides=dict(studies or {}))
    return user


@pytest.fixture
def world(db):
    owner = make_user('matrix_visibility_owner', OWNER_PASSWORD)
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    study_a = Study.objects.create(title='Matrix visible A')
    study_b = Study.objects.create(title='Matrix initially invisible B')
    study_c = Study.objects.create(title='Matrix read-only C')
    admin = make_user('matrix_visibility_admin', ADMIN_PASSWORD, role='admin', studies={
        str(study_a.pk): ADMIN_A, str(study_b.pk): ADMIN_B, str(study_c.pk): [VIEW]})
    target = make_user('matrix_visibility_target', 'Synthetic-target-1!', studies={
        str(study_a.pk): [VIEW, 'session.view'], str(study_b.pk): [], str(study_c.pk): []})
    readonly = make_user('matrix_visibility_readonly', 'Synthetic-readonly-1!',
                         studies={str(study_c.pk): [VIEW]})
    # Put the target on the first page, then make a genuine second page.
    for index in range(20):
        make_user(f'matrix_bulk_{index:02}', 'Synthetic-bulk-1!')
    return {'owner': owner, 'admin': admin, 'target': target, 'readonly': readonly,
            'study_a': study_a, 'study_b': study_b, 'study_c': study_c}


def run_chrome(script, env, evidence, name):
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=REPO_ROOT,
                            env=dict(os.environ, **env), capture_output=True, text=True,
                            timeout=180)
    evidence(name + '_stdout.txt', result.stdout.encode())
    evidence(name + '_stderr.txt', result.stderr.encode())
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    found = re.search(r'MATRIX_OBSERVATIONS (\{.*\})', result.stdout)
    assert found, result.stdout
    return json.loads(found.group(1))


@browser_only
def test_chrome_visibility_bulk_and_drafts_use_legal_server_combinations(
        live_server, world, evidence, evidence_root):
    observed = run_chrome(SCRIPT_VISIBILITY, {
        'GEP_TEST_BASE': live_server.url, 'GEP_ADMIN_PASSWORD': ADMIN_PASSWORD,
        'GEP_OWNER_PASSWORD': OWNER_PASSWORD, 'GEP_TARGET_PK': str(world['target'].pk),
        'GEP_READONLY_PK': str(world['readonly'].pk),
        'GEP_STUDY_A': str(world['study_a'].pk), 'GEP_STUDY_B': str(world['study_b'].pk),
        'GEP_STUDY_C': str(world['study_c'].pk), 'GEP_EVIDENCE': str(evidence_root)}, evidence, 'visibility')
    assert observed['page_errors'] == []
    assert observed['invisible_disabled'] and observed['restored_empty']
    assert observed['bulk_scope_legal'] and observed['scope_counts_match_eligible_controls']
    assert observed['drafts_survive_paging']
    assert observed['drafts_account_scoped'] and observed['clear_restores_stored_state']
    assert observed['contradiction_refused'] == 409
    assert observed['forbidden_action_refused'] == 403
    evidence('matrix_visibility.json', observed)
    profile = AccountProfile.objects.get(user=world['target'])
    assert sorted(profile.study_overrides[str(world['study_a'].pk)]) == sorted(ADMIN_A)
    assert profile.study_overrides[str(world['study_b'].pk)] == []
    assert profile.study_overrides[str(world['study_c'].pk)] == []
    assert AccountProfile.objects.get(user=world['readonly']).study_overrides[str(world['study_c'].pk)] == [VIEW]
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 2


SCRIPT_VISIBILITY = r'''
import {chromium, expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, target=process.env.GEP_TARGET_PK;
const studyA=process.env.GEP_STUDY_A, studyB=process.env.GEP_STUDY_B, studyC=process.env.GEP_STUDY_C;
const entryA='form[data-matrix-entry="'+target+'-'+studyA+'"]';
const entryB='form[data-matrix-entry="'+target+'-'+studyB+'"]';
const observed={page_errors:[]};
const ctx=await browser.newContext({viewport:{width:1366,height:900}});
const page=await ctx.newPage();
page.on('pageerror',error=>observed.page_errors.push(error.message));
async function login(username,password){
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/users');
}
async function expand(entry){
  const details=page.locator(entry+' details');
  if(!(await details.evaluate(node=>node.open))) await details.locator('summary').click();
}
async function commit(entry){
  await page.locator(entry+' button').click();
  await expect(page.locator('[data-preview="matrix"]')).toBeVisible();
  await page.locator('[data-preview="matrix"] button').click();
  const dialog=page.locator('[data-confirm-dialog][open]');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[data-confirm-impact]')).toContainText('matrix_visibility_target');
  await expect(dialog.locator('[data-confirm-impact]')).toContainText(entry === entryA ? 'Matrix visible A' : 'Matrix initially invisible B');
  await dialog.locator('[name=password]').fill(process.env.GEP_ADMIN_PASSWORD);
  await dialog.locator('[data-confirm-submit]').click();
  await expect(page.locator('[data-committed-kind="matrix"]')).toBeVisible();
}
async function assertChildren(entry,disabled,checked=false){
  for(const child of await page.locator(entry+' input[data-child="1"]').all()){
    if(disabled) await expect(child).toBeDisabled(); else await expect(child).toBeEnabled();
    if(checked) await expect(child).toBeChecked(); else await expect(child).not.toBeChecked();
  }
}
async function assertScope(studies,actions){
  await expect(page.locator('[data-scope-study-count="'+target+'"]')).toHaveText(String(studies));
  await expect(page.locator('[data-scope-action-count="'+target+'"]')).toHaveText(String(actions));
}
try{
  await login('matrix_visibility_admin',process.env.GEP_ADMIN_PASSWORD);
  await expand(entryA); await expand(entryB);
  await assertChildren(entryB,true);
  await expect(page.locator(entryB+' [data-select-all-study]')).toBeDisabled();
  const readOnly='[data-matrix-row="'+process.env.GEP_READONLY_PK+'-'+studyC+'"]';
  await expect(page.locator(readOnly+' input')).toHaveCount(0);
  await expect(page.locator(entryA+' [name="action:data.export_raw"]')).toHaveCount(0);
  await assertScope(1,3);

  // Clearing visibility immediately clears and locks children, including both
  // bulk controls when no other rendered study is visible and editable.
  await page.locator(entryA+' [name=visibility]').uncheck();
  await assertChildren(entryA,true);
  await expect(page.locator(entryA+' [data-select-all-study]')).toBeDisabled();
  await expect(page.locator('[data-select-all-user="'+target+'"]')).toBeDisabled();
  await assertScope(0,0);
  observed.invisible_disabled=true;
  await page.screenshot({path:process.env.GEP_EVIDENCE+'/invisible_children.png',fullPage:false});
  await commit(entryA);

  // A fresh server page after the commit also starts disabled. Restoring
  // visibility alone must never resurrect the previously stored session.view.
  await expand(entryA);
  await assertChildren(entryA,true);
  await page.locator(entryA+' [name=visibility]').check();
  await assertChildren(entryA,false);
  await assertScope(1,3);
  observed.restored_empty=true;
  await page.locator(entryA+' [data-select-all-study]').check();
  await assertChildren(entryA,false,true);
  await page.locator(entryA+' [data-select-all-study]').uncheck();
  await assertChildren(entryA,false);
  await page.locator(entryA+' [name="action:session.view"]').check();
  expect(await page.locator(entryA+' [data-select-all-study]').evaluate(node=>node.indeterminate)).toBe(true);
  await page.locator('[data-select-all-user="'+target+'"]').check();
  await assertChildren(entryA,false,true);
  await assertChildren(entryB,true);
  await expect(page.locator(readOnly+' input')).toHaveCount(0);
  await assertScope(1,3);
  observed.bulk_scope_legal=true;
  await commit(entryA);

  // Two unsent drafts survive real paging and account-filter navigation.
  await expand(entryA); await expand(entryB);
  await page.locator(entryA+' [name=visibility]').uncheck();
  await page.locator(entryB+' [name=visibility]').check();
  await page.locator(entryB+' [name="action:session.recover"]').check();
  await page.goto(base+'/users?page=2');
  await expect(page.locator(entryA)).toHaveCount(0);
  await expect(page.locator('[data-draft-count]')).toHaveText('2');
  await page.goto(base+'/users?q=matrix_visibility_target');
  await expand(entryA); await expand(entryB);
  await assertChildren(entryA,true);
  await expect(page.locator(entryB+' [name="action:session.recover"]')).toBeEnabled();
  await expect(page.locator(entryB+' [name="action:session.recover"]')).toBeChecked();
  await assertScope(1,2);
  observed.drafts_survive_paging=true;

  // The same tab changes account; the other actor cannot read this draft.
  await page.locator('[data-nav-link=logout]').click();
  await login('matrix_visibility_owner',process.env.GEP_OWNER_PASSWORD);
  await expect(page.locator('[data-draft-banner]')).toBeHidden();
  await expect(page.locator(entryA+' [name=visibility]')).toBeChecked();
  await expect(page.locator(entryB+' [name=visibility]')).not.toBeChecked();
  await page.locator('[data-nav-link=logout]').click();
  await login('matrix_visibility_admin',process.env.GEP_ADMIN_PASSWORD);
  await expect(page.locator(entryA+' [name=visibility]')).not.toBeChecked();
  observed.drafts_account_scoped=true;
  await page.locator('[data-draft-clear]').click();
  await expand(entryA); await expand(entryB);
  await assertChildren(entryA,false,true);
  await assertChildren(entryB,true);
  await expect(page.locator('[data-draft-banner]')).toBeHidden();
  await assertScope(1,3);
  observed.clear_restores_stored_state=true;
  observed.scope_counts_match_eligible_controls=true;

  // UI behavior does not weaken the final authorization or contradictory
  // state rejection. These are real requests, not client-side mocks.
  const refused=await page.evaluate(async args=>{
    const token=document.querySelector('form[data-matrix-entry] [name=csrfmiddlewaretoken]').value;
    const send=async fields=>{
      const body=new URLSearchParams({op:'matrix_preview',user_id:args.target,study_id:args.study,
        csrfmiddlewaretoken:token,...fields});
      return (await fetch('/users',{method:'POST',body})).status;
    };
    return [await send({'action:session.view':'1'}),
      await send({visibility:'1','action:data.export_raw':'1'})];
  },{target,study:studyA});
  [observed.contradiction_refused,observed.forbidden_action_refused]=refused;
  await page.screenshot({path:process.env.GEP_EVIDENCE+'/restored_scope.png',fullPage:false});
} finally {await browser.close();}
console.log('MATRIX_OBSERVATIONS '+JSON.stringify(observed));
'''


@browser_only
def test_chrome_lifecycle_dialog_keeps_role_and_invitation_id_without_password_drafts(
        live_server, world, evidence):
    observed = run_chrome(SCRIPT_LIFECYCLE, {
        'GEP_TEST_BASE': live_server.url, 'GEP_OWNER_PASSWORD': OWNER_PASSWORD}, evidence, 'lifecycle')
    assert observed['page_errors'] == []
    assert observed['invalid_name_stays_on_form'] and observed['cancel_sent_nothing']
    assert observed['invite_role'] == 'admin' and observed['revoke_id_preserved']
    assert observed['source_password_ignored'] and observed['no_password_in_storage']
    evidence('lifecycle_payload.json', observed)
    invite = AccountInvitation.objects.get(username='matrix_dialog_new_admin')
    assert invite.role == 'admin' and invite.revoked is True
    assert not get_user_model().objects.filter(username='matrix_dialog_new_admin').exists()


SCRIPT_LIFECYCLE = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, password=process.env.GEP_OWNER_PASSWORD;
const observed={page_errors:[]};
const page=await browser.newPage({viewport:{width:1366,height:900}});
const posts=[];
page.on('pageerror',error=>observed.page_errors.push(error.message));
page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/users')){
  const raw=request.postData() || '';
  const field=name=>(raw.match(new RegExp('name="'+name+'"\\r?\\n\\r?\\n([^\\r\\n]*)')) || [])[1] || null;
  posts.push({op:field('op'),role:field('role'),invitation_id:field('invitation_id')});
}});
try{
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('matrix_visibility_owner');
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/users');
  const invite='form[data-confirm="password"]:has([name=op][value=invite_account])';
  await page.locator(invite+' button').click();
  await expect(page.locator('[data-confirm-dialog][open]')).toHaveCount(0);
  observed.invalid_name_stays_on_form=true;
  await page.locator(invite+' [name=username]').fill('matrix_dialog_new_admin');
  await page.locator(invite+' [name=role]').selectOption('admin');
  const before=posts.length;
  await page.locator(invite+' button').click();
  await expect(page.locator('[data-confirm-dialog][open]')).toBeVisible();
  await page.locator('[data-confirm-dialog] [data-confirm-cancel]').click();
  expect(posts.length).toBe(before);
  observed.cancel_sent_nothing=true;
  expect(await page.evaluate(()=>document.activeElement===document.querySelector('form[data-confirm="password"]:has([name=op][value=invite_account]) button'))).toBe(true);
  await page.locator(invite+' button').click();
  const dialog=page.locator('[data-confirm-dialog][open]');
  await expect(dialog.locator('[data-confirm-impact]')).toContainText('matrix_dialog_new_admin');
  await expect(dialog.locator('[data-confirm-impact]')).toContainText('admin');
  await dialog.locator('[name=password]').fill(password);
  await dialog.locator('[data-confirm-submit]').click();
  await expect(page.locator('[data-one-time-invitation]')).toBeVisible();
  expect(posts.at(-1).op).toBe('invite_account');
  observed.invite_role=posts.at(-1).role;
  observed.source_password_ignored=await page.evaluate(()=>{
    const source=document.querySelector('form[data-confirm="password"]:has([name=op][value=invite_account]) [name=password]');
    return !source || (source.disabled && source.hidden && source.value==='');
  });
  const revoke='form[data-confirm="password"]:has([name=op][value=revoke_invitation])';
  const id=await page.locator(revoke+' [name=invitation_id]').inputValue();
  await page.locator(revoke+' button').click();
  await expect(page.locator('[data-confirm-dialog][open]')).toBeVisible();
  await page.locator('[data-confirm-dialog][open] [name=password]').fill(password);
  await page.locator('[data-confirm-dialog][open] [data-confirm-submit]').click();
  await expect(page.locator(revoke)).toHaveCount(0);
  observed.revoke_id_preserved=posts.at(-1).op==='revoke_invitation' && posts.at(-1).invitation_id===id;
  observed.no_password_in_storage=await page.evaluate(secret=>
    Object.values(sessionStorage).every(value=>!value.includes(secret)&&!value.includes('password')),
    password);
} finally {await browser.close();}
console.log('MATRIX_OBSERVATIONS '+JSON.stringify(observed));
'''
