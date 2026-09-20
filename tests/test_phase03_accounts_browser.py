"""Opt-in real-Chrome evidence for instance accounts (T26), temporary server only.

Demonstrates: Owner temporary-password create and invite; forced first-login
password change; Owner row read-only for Admin; ordinary users denied /users.
"""
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1', reason='Set GEP_T17_BROWSER=1 to run the isolated Chromium check')

SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE;
const ownerPassword=process.env.GEP_OWNER_PASSWORD;
const adminPassword=process.env.GEP_ADMIN_PASSWORD;
const userPassword=process.env.GEP_USER_PASSWORD;
const inviteePassword=process.env.GEP_INVITEE_PASSWORD;
const errors=[];
async function login(page,username,password){
  const response=await page.goto(base+'/login');
  expect(response.status()).toBe(200);
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录'}).click();
}
try {
  // Owner: create temporary account and invitation, appoint Admin.
  const owner=await browser.newContext();
  const page=await owner.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  await login(page,'synthetic_owner',ownerPassword);
  await page.waitForURL(base+'/');
  let response=await page.goto(base+'/users');
  expect(response.status()).toBe(200);
  await expect(page.locator('[data-owner-row="1"]')).toContainText('Owner 账号只读');

  const createForm=page.locator('form').filter({has:page.locator('[name=op][value=create_temp]')});
  await createForm.locator('[name=username]').fill('browser_temp_user');
  await createForm.locator('[name=password]').fill(ownerPassword);
  await createForm.getByRole('button',{name:'创建临时密码账号'}).click();
  await page.waitForLoadState('load');
  const tempPassword=await page.locator('[data-one-time-secret] code').innerText();
  expect(tempPassword.length).toBeGreaterThan(15);

  const inviteForm=page.locator('form').filter({has:page.locator('[name=op][value=invite_account]')});
  await inviteForm.locator('[name=username]').fill('browser_invitee');
  await inviteForm.locator('[name=password]').fill(ownerPassword);
  await inviteForm.getByRole('button',{name:'生成邀请（本人设置密码）'}).click();
  await page.waitForLoadState('load');
  const invitation=await page.locator('[data-one-time-invitation] code').innerText();
  const inviteToken=invitation.split('token=')[1];
  expect(inviteToken.length).toBeGreaterThan(20);

  await createForm.locator('[name=username]').fill('browser_admin');
  await createForm.locator('[name=password]').fill(ownerPassword);
  await createForm.getByRole('button',{name:'创建临时密码账号'}).click();
  await page.waitForLoadState('load');
  const adminTempPassword=await page.locator('[data-one-time-secret] code').innerText();

  const adminRow=page.locator('[data-account-row]').filter({hasText:'browser_admin'});
  const adminRoleForm=page.locator('form').filter({has:page.locator('[name=op][value=set_role]')}).filter({has:page.locator('[name=username][value="browser_admin"]')});
  await adminRoleForm.locator('select[name=role]').selectOption('admin');
  await adminRoleForm.locator('[name=password]').fill(ownerPassword);
  await adminRoleForm.getByRole('button',{name:'保存角色'}).click();
  await page.waitForLoadState('load');
  await expect(page.locator('[data-account-row]').filter({hasText:'browser_admin'})).toContainText('admin');
  await owner.close();

  // Admin: forced first-login change, then Owner row is read-only.
  const adminContext=await browser.newContext();
  const adminPage=await adminContext.newPage();
  adminPage.on('pageerror',e=>errors.push('admin:'+e.message));
  await login(adminPage,'browser_admin',adminTempPassword);
  await adminPage.waitForURL(base+'/account/password');
  await adminPage.goto(base+'/');
  expect(adminPage.url()).toBe(base+'/account/password');
  await adminPage.locator('[name=current]').fill(adminTempPassword);
  await adminPage.locator('[name=new]').fill(adminPassword);
  await adminPage.locator('[name=confirm]').fill(adminPassword);
  await adminPage.getByRole('button',{name:'保存新密码'}).click();
  await adminPage.waitForURL(base+'/');
  response=await adminPage.goto(base+'/users');
  expect(response.status()).toBe(200);
  const ownerRow=adminPage.locator('[data-owner-row="1"]');
  await expect(ownerRow).toContainText('Owner 账号只读');
  expect(await ownerRow.locator('button, select, input').count()).toBe(0);
  const plainRow=adminPage.locator('[data-account-row]').filter({hasText:'browser_temp_user'});
  expect(await plainRow.locator('button').count()).toBeGreaterThan(0);
  await adminContext.close();

  // Temporary-password user: same forced-change gate, then ordinary denial.
  const tempContext=await browser.newContext();
  const tempPage=await tempContext.newPage();
  tempPage.on('pageerror',e=>errors.push('temp:'+e.message));
  await login(tempPage,'browser_temp_user',tempPassword);
  await tempPage.waitForURL(base+'/account/password');
  await tempPage.locator('[name=current]').fill(tempPassword);
  await tempPage.locator('[name=new]').fill(userPassword);
  await tempPage.locator('[name=confirm]').fill(userPassword);
  await tempPage.getByRole('button',{name:'保存新密码'}).click();
  await tempPage.waitForURL(base+'/');
  response=await tempPage.goto(base+'/users');
  expect(response.status()).toBe(403);
  await tempContext.close();

  // Invited account sets its own password, then is an ordinary user.
  const inviteeContext=await browser.newContext();
  const inviteePage=await inviteeContext.newPage();
  inviteePage.on('pageerror',e=>errors.push('invitee:'+e.message));
  response=await inviteePage.goto(base+'/activate-account?token='+inviteToken);
  expect(response.status()).toBe(200);
  await inviteePage.locator('[name=password]').fill(inviteePassword);
  await inviteePage.locator('[name=confirm]').fill(inviteePassword);
  await inviteePage.getByRole('button',{name:'激活账号'}).click();
  await expect(inviteePage.getByText('已激活')).toBeVisible();
  await login(inviteePage,'browser_invitee',inviteePassword);
  await inviteePage.waitForURL(base+'/');
  response=await inviteePage.goto(base+'/users');
  expect(response.status()).toBe(403);
  await inviteeContext.close();

  expect(errors).toEqual([]);
  console.log('Chrome: temporary password forced change, invitation activation, Owner read-only row and ordinary denial passed.');
} finally {await browser.close();}
'''


@pytest.fixture(scope='session', autouse=True)
def browser_static_urls(django_test_environment):
    from django.conf import settings
    before = (settings.STATIC_URL, settings.MEDIA_URL)
    settings.STATIC_URL = '/static/'
    settings.MEDIA_URL = '/media/'
    yield
    settings.STATIC_URL, settings.MEDIA_URL = before


def test_actual_chrome_instance_account_governance(live_server, setup):
    env = dict(os.environ, GEP_TEST_BASE=live_server.url,
               GEP_OWNER_PASSWORD='synthetic-test-password',
               GEP_ADMIN_PASSWORD='synthetic-browser-admin-password',
               GEP_USER_PASSWORD='synthetic-browser-user-password',
               GEP_INVITEE_PASSWORD='synthetic-browser-invitee-password')
    result = subprocess.run(['node', '--input-type=module', '-e', SCRIPT], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
