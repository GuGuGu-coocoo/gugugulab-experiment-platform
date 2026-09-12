"""Opt-in browser verification against pytest's disposable live server, never the acceptance instance."""
import os
import subprocess
from pathlib import Path

import pytest
from core.models import Grant

pytestmark=pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER')!='1',reason='Set GEP_T17_BROWSER=1 to run the isolated Chromium check')


@pytest.fixture(scope='session',autouse=True)
def browser_static_urls(django_test_environment):
    # Django's live-server static wrapper requires string URL prefixes.
    from django.conf import settings
    before=(settings.STATIC_URL,settings.MEDIA_URL)
    settings.STATIC_URL='/static/'
    settings.MEDIA_URL='/media/'
    yield
    settings.STATIC_URL,settings.MEDIA_URL=before


def test_actual_download_and_authorized_form_interactions(live_server,setup):
    for action in ['study.view','study.configure','data.export_raw','recruitment.manage']:
        Grant.objects.create(user=setup['owner'],study=setup['study'],action=action)
    env=dict(os.environ,GEP_TEST_BASE=live_server.url,GEP_TEST_STUDY=str(setup['study'].id))
    script=r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=process.env.GEP_TEST_BASE;
 const loginResponse=await page.goto(base+'/login');expect(loginResponse.status()).toBe(200);
 await page.locator('[name=username]').fill('synthetic_owner');
 await page.locator('[name=password]').fill('synthetic-test-password');
 await page.locator('button').click();
 await page.waitForURL(base+'/');
 await page.goto(base+'/studies/'+process.env.GEP_TEST_STUDY);
 await page.locator('#export-form button').click();
 await expect(page.getByText('下载 JSONL',{exact:true})).toBeVisible();
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'· metadata',exact:true}).click()]);
 expect(download.suggestedFilename()).toMatch(/\.metadata\.json$/);
 const stream=await download.createReadStream();let contents='';for await (const chunk of stream) contents+=chunk;
 const metadata=JSON.parse(contents);expect(metadata.format_version).toBe('1');expect(metadata.records).toBeUndefined();
 await Promise.all([page.waitForResponse(r=>r.request().method()==='POST' && r.url().includes('/studies/')),page.locator('[name=state]').selectOption('paused')]);
 await expect(page.locator('[name=state]')).toHaveValue('paused');
 await page.reload();await expect(page.locator('[name=state]')).toHaveValue('paused');
 await page.locator('form').filter({has:page.locator('[name=op][value=configure]')}).locator('button').click();
 await expect(page.getByRole('alert')).toContainText('参与政策已冻结');
 expect(errors).toEqual([]);
 console.log('Chromium: real metadata download, persisted immediate recruitment and inline error passed.');
} finally {await browser.close();}
'''
    result=subprocess.run(['node','--input-type=module','-e',script],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
