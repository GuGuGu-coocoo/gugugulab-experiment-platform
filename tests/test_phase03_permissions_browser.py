"""Opt-in real-Chrome evidence for the preview-bound permission matrix (P0302).

Demonstrates with actual Chrome against the pytest temporary server:
Owner and Admin journeys through matrix preview and own-password confirmation,
a rejected confirmation (wrong own password) with no change, server rejection of
a forged contradictory submission from the browser session, the Owner row being
read-only in the matrix, and an ordinary user denied /users while the existing
password still works.
"""
import os
import subprocess
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from core.models import AccountProfile, Grant

pytestmark = pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1', reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')

SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE;
const errors=[];
async function login(page,username,password){
  const response=await page.goto(base+'/login');
  expect(response.status()).toBe(200);
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
}
try {
  // ---------- Admin: bounded matrix, own row and Owner row read-only ----------
  const adminCtx=await browser.newContext();
  const admin=await adminCtx.newPage();
  admin.on('pageerror',e=>errors.push('admin:'+e.message));
  await login(admin,'browser_matrix_admin',process.env.GEP_ADMIN_PASSWORD);
  let response=await admin.goto(base+'/users');
  expect(response.status()).toBe(200);
  await expect(admin.locator('[data-permission-matrix]')).toBeVisible();
  const ownerMatrixRow=admin.locator(`[data-matrix-row="${process.env.GEP_OWNER_ID}-${process.env.GEP_STUDY_ID}"]`);
  await expect(ownerMatrixRow).toBeVisible();
  expect(await ownerMatrixRow.locator('form, input, button').count()).toBe(0);
  const adminMatrixRow=admin.locator(`[data-matrix-row="${process.env.GEP_ADMIN_ID}-${process.env.GEP_STUDY_ID}"]`);
  await expect(adminMatrixRow).toBeVisible();
  expect(await adminMatrixRow.locator('form, input, button').count()).toBe(0);
  const ordinaryRow=admin.locator(`[data-matrix-row="${process.env.GEP_MATRIX_ROW}"]`);
  const ordinaryForm=ordinaryRow.locator('form[data-visibility-form]');
  expect(await ordinaryForm.locator('[name="action:data.export_raw"]').count()).toBe(0); // not delegable for this Admin
  await ordinaryForm.locator('details').evaluate(el=>{el.open=true});
  await ordinaryForm.locator('[name="action:session.view"]').check();
  await ordinaryForm.getByRole('button',{name:'预览更改'}).click();
  await admin.waitForLoadState('load');
  await expect(admin.locator('[data-preview="matrix"]')).toContainText('session.view');
  await expect(admin.locator('[data-preview="matrix"]')).toContainText('browser_ordinary');
  await admin.locator('[data-preview="matrix"] [name=password]').fill(process.env.GEP_ADMIN_PASSWORD);
  await admin.locator('[data-preview="matrix"]').getByRole('button',{name:/确认执行/}).click();
  await admin.waitForLoadState('load');
  await expect(admin.locator('.notice')).toContainText('权限矩阵已更新');
  await adminCtx.close();

  // ---------- Owner: preview, rejected confirmation, confirmation, forged 409 ----------
  const ownerCtx=await browser.newContext();
  const owner=await ownerCtx.newPage();
  owner.on('pageerror',e=>errors.push('owner:'+e.message));
  await login(owner,'synthetic_owner',process.env.GEP_OWNER_PASSWORD);
  response=await owner.goto(base+'/users');
  expect(response.status()).toBe(200);
  const row=owner.locator(`[data-matrix-row="${process.env.GEP_MATRIX_ROW}"]`);
  await expect(row).toBeVisible();
  const form=row.locator('form[data-visibility-form]');
  await form.locator('details').evaluate(el=>{el.open=true});
  await form.locator('[name=visibility]').check();
  await form.locator('[name="action:data.export_raw"]').check();
  await form.getByRole('button',{name:'预览更改'}).click();
  await owner.waitForLoadState('load');
  const preview=owner.locator('[data-preview="matrix"]');
  await expect(preview).toBeVisible();
  await expect(preview).toContainText('data.export_raw');
  await expect(preview).toContainText(process.env.GEP_ORDINARY_NAME);

  // Rejection: the wrong own password changes nothing and keeps the preview usable.
  await preview.locator('[name=password]').fill('wrong-owner-password-2026');
  await preview.getByRole('button',{name:/确认执行/}).click();
  await owner.waitForLoadState('load');
  await expect(owner.locator('[role=alert]')).toContainText('重新认证失败');
  await expect(owner.locator('[data-preview="matrix"]')).toBeVisible();

  // Confirmation with the Owner's own password.
  await owner.locator('[data-preview="matrix"] [name=password]').fill(process.env.GEP_OWNER_PASSWORD);
  await owner.locator('[data-preview="matrix"]').getByRole('button',{name:/确认执行/}).click();
  await owner.waitForLoadState('load');
  await expect(owner.locator('.notice')).toContainText('权限矩阵已更新');

  // Persisted: visibility and the explicit action are checked after reload.
  await owner.goto(base+'/users');
  const persisted=owner.locator(`[data-matrix-row="${process.env.GEP_MATRIX_ROW}"] form[data-visibility-form]`);
  await expect(persisted.locator('[name=visibility]')).toBeChecked();
  await expect(persisted.locator('[name="action:data.export_raw"]')).toBeChecked();

  // Forged submission from the real browser session: children without visibility.
  const forged=await owner.evaluate(async ({userId,studyId})=>{
    const token=document.querySelector('[name=csrfmiddlewaretoken]').value;
    const body=new URLSearchParams({op:'matrix_preview',user_id:userId,study_id:studyId,'action:data.export_raw':'1'});
    const response=await fetch('/users',{method:'POST',headers:{'X-CSRFToken':token,'Content-Type':'application/x-www-form-urlencoded'},body});
    return response.status;
  },{userId:process.env.GEP_MATRIX_USER_ID,studyId:process.env.GEP_STUDY_ID});
  expect(forged).toBe(409);
  await ownerCtx.close();

  // ---------- Ordinary user: denied /users, existing password unchanged ----------
  const ordinaryCtx=await browser.newContext();
  const ordinary=await ordinaryCtx.newPage();
  ordinary.on('pageerror',e=>errors.push('ordinary:'+e.message));
  await login(ordinary,process.env.GEP_ORDINARY_NAME,process.env.GEP_USER_PASSWORD);
  response=await ordinary.goto(base+'/users');
  expect(response.status()).toBe(403);
  await ordinaryCtx.close();

  expect(errors).toEqual([]);
  console.log('Chrome: matrix preview, wrong/right own-password confirmation, forged 409, read-only Owner row, ordinary denial and unchanged password passed.');
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


def test_actual_chrome_permission_matrix_journeys(live_server, setup):
    User = get_user_model()
    study = setup['study']
    admin = User.objects.create_user('browser_matrix_admin', password='synthetic-browser-admin-password')
    AccountProfile.objects.create(user=admin, role='admin', must_change_password=False, auth_version=1, revision=0)
    ordinary = User.objects.create_user('browser_ordinary', password='synthetic-browser-user-password')
    AccountProfile.objects.create(user=ordinary, role='user', must_change_password=False, auth_version=1, revision=0)
    for action in ('study.view', 'session.view'):
        Grant.objects.create(user=admin, study=study, action=action, delegable=True)
    for action in ('study.view', 'study.configure'):
        Grant.objects.create(user=setup['owner'], study=study, action=action, delegable=True)
    Grant.objects.create(user=ordinary, study=study, action='study.view', delegable=True)

    env = dict(os.environ, GEP_TEST_BASE=live_server.url,
               GEP_OWNER_PASSWORD='synthetic-test-password',
               GEP_ADMIN_PASSWORD='synthetic-browser-admin-password',
               GEP_USER_PASSWORD='synthetic-browser-user-password',
               GEP_STUDY_ID=str(study.id),
               GEP_OWNER_ID=str(setup['owner'].pk),
               GEP_ADMIN_ID=str(admin.pk),
               GEP_MATRIX_USER_ID=str(ordinary.pk),
               GEP_MATRIX_ROW=f'{ordinary.pk}-{study.id}',
               GEP_ORDINARY_NAME=ordinary.username)
    result = subprocess.run(['node', '--input-type=module', '-e', SCRIPT], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr

    ordinary.refresh_from_db()
    assert ordinary.check_password('synthetic-browser-user-password')
    assert sorted(Grant.objects.filter(user=ordinary, study=study).values_list('action', flat=True)) == ['data.export_raw', 'session.view', 'study.view']
