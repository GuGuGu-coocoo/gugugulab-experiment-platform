"""Opt-in browser verification against pytest's disposable live server, never the acceptance instance."""
import json
import os
import subprocess
import zipfile
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


def test_actual_download_and_authorized_form_interactions(live_server,setup,tmp_path):
    for action in ['study.view','study.configure','data.export_raw','identity_mapping.read','recruitment.manage']:
        Grant.objects.create(user=setup['owner'],study=setup['study'],action=action)
    env=dict(os.environ,GEP_TEST_BASE=live_server.url,GEP_TEST_STUDY=str(setup['study'].id),
             GEP_TEST_DOWNLOADS=str(tmp_path))
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
 // Confirmed U09 v2 workflow: the main button creates the fixed snapshot and
 // downloads the overview + details ZIP (exactly participants.csv/events.csv);
 // metadata is a separate link.
 await page.goto(base+'/studies/'+process.env.GEP_TEST_STUDY+'/exports');
 expect(await page.locator('[data-export-view="identified"]').isChecked()).toBe(true);
 const [download]=await Promise.all([page.waitForEvent('download'),page.locator('[data-export-submit]').click()]);
 expect(download.suggestedFilename()).toMatch(/\.zip$/);
 await download.saveAs(process.env.GEP_TEST_DOWNLOADS+'/export.zip');
 await page.waitForSelector('[data-created-format="metadata"]');
 const [metaDownload]=await Promise.all([page.waitForEvent('download'),page.locator('[data-created-format="metadata"]').click()]);
 expect(metaDownload.suggestedFilename()).toMatch(/\.metadata\.json$/);
 await metaDownload.saveAs(process.env.GEP_TEST_DOWNLOADS+'/metadata.json');
 await page.goto(base+'/studies/'+process.env.GEP_TEST_STUDY+'/recruitment');
 await Promise.all([page.waitForResponse(r=>r.request().method()==='POST' && r.url().includes('/studies/')),page.locator('[name=state]').selectOption('paused')]);
 await expect(page.locator('[name=state]')).toHaveValue('paused');
 await page.reload();await expect(page.locator('[name=state]')).toHaveValue('paused');
 await page.goto(base+'/studies/'+process.env.GEP_TEST_STUDY+'/participation');
 await page.locator('form').filter({has:page.locator('[name=op][value=configure]')}).locator('button').click();
 await expect(page.getByRole('alert')).toContainText('参与政策已冻结');
 expect(errors).toEqual([]);
 console.log('Chromium: real v2 ZIP + metadata download, persisted immediate recruitment and inline error passed.');
} finally {await browser.close();}
'''
    result=subprocess.run(['node','--input-type=module','-e',script],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    with zipfile.ZipFile(tmp_path/'export.zip') as archive:
        assert archive.testzip() is None
        assert archive.namelist()==['participants.csv','events.csv']
        header=archive.read('participants.csv').decode('utf-8-sig').splitlines()[0]
        assert header.startswith('研究标题（study_title）,研究 ID（study_id）,被试 UUID（participant_uuid）,名单 ID（participant_code）')
    metadata=json.loads((tmp_path/'metadata.json').read_text(encoding='utf-8'))
    assert metadata['format_version']=='2' and metadata['view']=='identified' and metadata['language']=='zh'
    assert 'records' not in metadata
