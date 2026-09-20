"""Opt-in real-Chrome evidence for the 03C publication and stable-entry journeys.

Demonstrates with actual Chrome against the pytest temporary server:

* the researcher publishes the public policy and selects R1 in the study GUI,
  then switches the current release to R2 through the same GUI;
* the participant portal lists the explicitly public study, the stable entry page
  binds the observed release/revision, and the start click carries that binding
  through the platform gate into the application context, where the real GEC
  client admits a session on the observed release;
* a page loaded before the switch is refused at the gate with a refresh page
  instead of starting the superseded materials, and creates no session; reloading
  the entry page binds the new release and admits on R2;
* the helper session created on R1 keeps its context and uploads after the
  switch, and the frozen R1 direct URL still serves the original package with no
  injected binding (legacy direct-release contract);
* the admin session cookie is host-only: it is present for the admin host and
  absent for the experiment host in the same browser context.

Server-side suites own the exhaustive authorization/revision/audit assertions;
this file proves the real browser journey.
"""
import io
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from core.models import Audit, Build, Event, Grant, Release, Session

pytestmark = pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1', reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')

SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const admin=process.env.GEP_ADMIN_BASE;
const experiment=process.env.GEP_EXPERIMENT_BASE;
const studyId=process.env.GEP_STUDY_ID;
const first=process.env.GEP_FIRST_RELEASE;
const second=process.env.GEP_SECOND_RELEASE;
const errors=[];
async function login(page,username,password){
  const response=await page.goto(admin+'/login');
  expect(response.status()).toBe(200);
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(admin+'/');
}
async function createSession(page,releaseId,revision,proof){
  return page.evaluate(async ({studyId,releaseId,revision,proof,instanceId})=>{
    const response=await fetch('/v1/participant/sessions',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({operation_id:crypto.randomUUID(),proof,instance_id:instanceId,study_id:studyId,
                           expected_release_id:releaseId,expected_revision:Number(revision)})});
    return {status:response.status,body:await response.json()};
  },{studyId,releaseId,revision,proof,instanceId:process.env.GEP_INSTANCE_ID});
}
function admissionId(locator){return locator.getAttribute('data-admission').then(raw=>JSON.parse(raw).session_id);}
try {
  const context=await browser.newContext();
  const researcher=await context.newPage();
  researcher.on('pageerror',e=>errors.push('researcher:'+e.message));
  const participant=await context.newPage();
  participant.on('pageerror',e=>errors.push('participant:'+e.message));

  // ---------- researcher: publish, select first release ----------
  await login(researcher,'synthetic_owner',process.env.GEP_OWNER_PASSWORD);
  await researcher.goto(admin+'/studies/'+studyId);
  await expect(researcher.locator('[data-publication-form]')).toBeVisible();
  await researcher.locator('[data-publication-form] [name=public]').check();
  await researcher.locator('[data-publication-form] [name=public_summary]').fill('浏览器公开简介');
  await researcher.locator('[data-publication-form] [name=public_duration]').fill('9 分钟');
  await researcher.locator('[data-publication-form] [name=public_device_requirements]').fill('桌面 Chrome');
  await researcher.locator('[data-publication-form]').getByRole('button',{name:'保存公开政策'}).click();
  await researcher.waitForLoadState('load');
  await researcher.locator('[data-current-release-form] [data-release-option="'+first+'"]').check();
  await researcher.locator('[data-current-release-form]').getByRole('button',{name:'设为当前发行'}).click();
  await researcher.waitForLoadState('load');
  await expect(researcher.locator('[data-current-release]')).toHaveAttribute('data-current-release', first);

  // ---------- participant: portal lists it, entry page observes R1 ----------
  await participant.goto(experiment+'/');
  const portalEntry=participant.locator('[data-study="'+studyId+'"]');
  await expect(portalEntry).toBeVisible();
  await expect(portalEntry).toContainText('浏览器公开简介');
  await expect(portalEntry).toContainText('9 分钟');

  const stalePage=await context.newPage();
  stalePage.on('pageerror',e=>errors.push('stale:'+e.message));
  await stalePage.goto(experiment+'/join/'+studyId);
  await expect(stalePage.locator('body')).toHaveAttribute('data-expected-release', first);
  const staleRevision=await stalePage.locator('body').getAttribute('data-expected-revision');
  await expect(stalePage.locator('#gep-start')).toHaveAttribute('data-start-url',
    experiment+'/run/'+first+'/web/index.html');
  await expect(stalePage.locator('#gep-start')).toHaveAttribute('href',
    experiment+'/run/'+first+'/web/index.html?entry_release='+first+'&entry_revision='+staleRevision);

  // ---------- start click: gate passes the observation, the real GEC client admits R1 ----------
  await participant.goto(experiment+'/join/'+studyId);
  await participant.locator('#gep-start').click();
  await participant.waitForURL(experiment+'/run/'+first+'/web/index.html?entry_release='+first+'&entry_revision='+staleRevision);
  await expect(participant.locator('body')).toContainText('first synthetic package');
  await expect(participant.locator('body')).toHaveAttribute('data-admission', /\{"session_id":"[0-9a-f-]{36}"/);
  const entrySessionId=await admissionId(participant.locator('body'));
  expect(await participant.evaluate(()=>globalThis.GEP_CONTEXT.expected_release_id)).toBe(first);
  expect(await participant.evaluate(()=>globalThis.GEP_CONTEXT.expected_revision)).toBe(Number(staleRevision));

  // A helper session on R1 owns the upload/context assertions across the switch.
  const helper=await createSession(participant,first,staleRevision,'browser-help-'+'x'.repeat(40));
  expect(helper.status).toBe(200);
  expect(helper.body.release_id).toBe(first);
  const helperId=helper.body.session_id;
  const helperToken=helper.body.token;
  const uploadBefore=await participant.evaluate(async ({sessionId,token})=>{
    const response=await fetch('/v1/participant/sessions/'+sessionId+'/event-batches',{method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},
      body:JSON.stringify({batch_id:crypto.randomUUID(),events:[{protocol_version:'gep/1',event_id:crypto.randomUUID(),session_id:sessionId,
        segment_id:crypto.randomUUID(),sequence:1,event_type:'exp.rt',schema_id:'rt',schema_version:'1',payload:{rt_ms:250}}]})});
    return {status:response.status,body:await response.json()};
  },{sessionId:helperId,token:helperToken});
  expect(uploadBefore.status).toBe(200);
  expect(uploadBefore.body.accepted.length).toBe(1);

  // ---------- researcher switches the current release ----------
  await researcher.goto(admin+'/studies/'+studyId);
  await researcher.locator('[data-current-release-form] [data-release-option="'+second+'"]').check();
  await researcher.locator('[data-current-release-form]').getByRole('button',{name:'设为当前发行'}).click();
  await researcher.waitForLoadState('load');
  await expect(researcher.locator('[data-current-release]')).toHaveAttribute('data-current-release', second);

  // ---------- old session keeps context and uploads across the switch ----------
  const contextState=await participant.evaluate(async ({sessionId,token})=>{
    const response=await fetch('/v1/participant/sessions/'+sessionId+'/context',{headers:{'Authorization':'Bearer '+token}});
    return {status:response.status,body:await response.json()};
  },{sessionId:helperId,token:helperToken});
  expect(contextState.status).toBe(200);
  expect(contextState.body.release_id).toBe(first);
  const uploadAfter=await participant.evaluate(async ({sessionId,token})=>{
    const response=await fetch('/v1/participant/sessions/'+sessionId+'/event-batches',{method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},
      body:JSON.stringify({batch_id:crypto.randomUUID(),events:[{protocol_version:'gep/1',event_id:crypto.randomUUID(),session_id:sessionId,
        segment_id:crypto.randomUUID(),sequence:2,event_type:'exp.rt',schema_id:'rt',schema_version:'1',payload:{rt_ms:260}}]})});
    return {status:response.status,body:await response.json()};
  },{sessionId:helperId,token:helperToken});
  expect(uploadAfter.status).toBe(200);
  expect(uploadAfter.body.accepted.length).toBe(1);

  // ---------- the stale entry page is refused at the gate and creates no session ----------
  await stalePage.locator('#gep-start').click();
  await stalePage.waitForURL(experiment+'/run/'+first+'/web/index.html?entry_release='+first+'&entry_revision='+staleRevision);
  await expect(stalePage.locator('body')).toContainText('研究入口已更新');
  const staleHtml=await stalePage.content();
  expect(staleHtml).not.toContain('first synthetic package');
  expect(staleHtml).not.toContain('GEP_CONTEXT');
  expect(await stalePage.locator('body').getAttribute('data-admission')).toBeNull();

  // Release the R1 client's device lock before the refreshed tab starts R2.
  await participant.close();

  // ---------- refresh the entry: the new binding admits on R2 ----------
  await stalePage.goto(experiment+'/join/'+studyId);
  await expect(stalePage.locator('body')).toHaveAttribute('data-expected-release', second);
  const freshRevision=await stalePage.locator('body').getAttribute('data-expected-revision');
  await expect(stalePage.locator('#gep-start')).toHaveAttribute('data-start-url',
    experiment+'/run/'+second+'/web/index.html');
  await expect(stalePage.locator('#gep-start')).toHaveAttribute('href',
    experiment+'/run/'+second+'/web/index.html?entry_release='+second+'&entry_revision='+freshRevision);
  await stalePage.locator('#gep-start').click();
  await stalePage.waitForURL(experiment+'/run/'+second+'/web/index.html?entry_release='+second+'&entry_revision='+freshRevision);
  await expect(stalePage.locator('body')).toContainText('second synthetic package');
  await expect(stalePage.locator('body')).toHaveAttribute('data-admission', /\{"session_id":"[0-9a-f-]{36}"/);
  const freshSessionId=await admissionId(stalePage.locator('body'));
  expect(freshSessionId).not.toBe(entrySessionId);
  expect(await stalePage.evaluate(()=>globalThis.GEP_CONTEXT.expected_release_id)).toBe(second);
  await stalePage.close();

  // ---------- frozen R1 direct URL keeps its legacy contract and serves the original bytes ----------
  const legacy=await context.newPage();
  legacy.on('pageerror',e=>errors.push('legacy:'+e.message));
  await legacy.goto(experiment+'/run/'+first+'/web/index.html');
  await expect(legacy.locator('body')).toContainText('first synthetic package');
  expect(await legacy.evaluate(()=>globalThis.GEP_CONTEXT.expected_release_id)).toBeUndefined();
  await expect(legacy.locator('body')).toHaveAttribute('data-admission', /\{"session_id":"[0-9a-f-]{36}"/);
  const legacySessionId=await admissionId(legacy.locator('body'));
  expect(legacySessionId).not.toBe(entrySessionId);
  expect(legacySessionId).not.toBe(freshSessionId);

  // ---------- two hosts, one browser: the admin cookie is host-only ----------
  const adminCookie=(await context.cookies()).find(c=>c.name==='gep_admin');
  expect(adminCookie.domain).toBe('localhost');
  expect(adminCookie.domain.startsWith('.')).toBe(false);
  await legacy.goto(experiment+'/');
  await expect(legacy.locator('body')).toHaveAttribute('data-authenticated', '0');
  await expect(legacy.locator('[data-publication-form], [data-current-release-form]')).toHaveCount(0);
  await researcher.goto(admin+'/');
  await expect(researcher.locator('body')).toContainText('我的研究');

  expect(errors).toEqual([]);
  console.log('Chrome: publication, R1->R2 switch, gated start click, stale entry refusal, refreshed R2 admission, old-session upload, frozen R1 direct URL and host-only admin cookie passed.');
} finally {await browser.close();}
'''


@pytest.fixture(scope='session', autouse=True)
def browser_static_urls(django_test_environment):
    """The live server wrapper needs configured static URLs (known 500 otherwise)."""
    from django.conf import settings
    before = (settings.STATIC_URL, settings.MEDIA_URL)
    settings.STATIC_URL = '/static/'
    settings.MEDIA_URL = '/media/'
    yield
    settings.STATIC_URL, settings.MEDIA_URL = before


def package_zip(body, expected_version):
    """A synthetic package with the real GEC browser client and an admission driver.

    The driver stands in for the Godot page while using the shipped bridge/SDK, so
    the observed context and the admission request are produced by the real client.
    """
    driver = (b'<script type="module">import "./gec/bridge.js";'
              b'(async()=>{try{await globalThis.GECBridge.call("start","start",JSON.stringify([{expected_version:"'
              + expected_version.encode() + b'"}]));'
              b'document.body.dataset.admission=globalThis.GECBridge.take("start")||JSON.stringify({error:"no_result"});}'
              b'catch(error){document.body.dataset.admission=JSON.stringify({error:String(error)});}})();</script>')
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w') as archive:
        archive.writestr('web/index.html', body + driver)
        client_root = Path(__file__).resolve().parents[1] / 'packages' / 'gec_web'
        # The shipped Web client is sdk.js + bridge.js + inputs.js + shell.js
        # (the shell companion bridge.js imports); the package mirrors the real
        # exporter list in tools/build.py so the bridge can load in Chrome.
        for name in ('sdk.js', 'bridge.js', 'inputs.js', 'shell.js'):
            archive.writestr('web/gec/' + name, (client_root / name).read_bytes())
    return target.getvalue()


def browser_release(study, version, package):
    descriptor = {'platform': 'godot_web', 'version': version,
                  'schemas': {'exp.rt': {'id': 'rt', 'version': '1', 'schema': {
                      'type': 'object', 'properties': {'rt_ms': {'type': 'number'}}, 'required': ['rt_ms'],
                      'additionalProperties': False}}}}
    build = Build.objects.create(study=study, descriptor=descriptor,
                                 digest=(version + '0' * 64)[:64], package_path=package)
    return Release.objects.create(study=study, build=build, approved=True,
                                  config={'purpose': 'synthetic', 'tag': version})


def test_actual_chrome_publication_and_stable_entry(live_server, setup, tmp_path, settings, monkeypatch):
    package_root = tmp_path / 'packages'
    package_root.mkdir()
    (package_root / 'first.zip').write_bytes(package_zip(b'<html><head></head><body>first synthetic package</body></html>', 'v1'))
    (package_root / 'second.zip').write_bytes(package_zip(b'<html><head></head><body>second synthetic package</body></html>', 'v2'))
    settings.DATA_DIR = tmp_path

    study = setup['study']
    study.recruitment = 'open'
    study.save()
    for action in ('study.view', 'study.configure', 'recruitment.manage'):
        Grant.objects.create(user=setup['owner'], study=study, action=action, delegable=True)
    first = browser_release(study, 'v1', 'first.zip')
    second = browser_release(study, 'v2', 'second.zip')

    port = live_server.url.rsplit(':', 1)[1]
    admin_base = live_server.url
    experiment_base = f'http://experiment.localhost:{port}'
    monkeypatch.setenv('GEP_PUBLIC_API', experiment_base)
    env = dict(os.environ, GEP_ADMIN_BASE=admin_base, GEP_EXPERIMENT_BASE=experiment_base,
               GEP_OWNER_PASSWORD='synthetic-test-password', GEP_STUDY_ID=str(study.id),
               GEP_INSTANCE_ID=str(setup['instance'].instance_id),
               GEP_FIRST_RELEASE=str(first.id), GEP_SECOND_RELEASE=str(second.id))
    result = subprocess.run(['node', '--input-type=module', '-e', SCRIPT],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr

    study.refresh_from_db()
    assert study.public is True and study.public_summary == '浏览器公开简介'
    assert study.current_release_id == second.id and study.revision == 3
    # R1: the start click, the helper session and the frozen direct URL each admitted
    # once; the refused stale click created no session.
    assert Session.objects.filter(release=first).count() == 3
    assert Session.objects.filter(release=second).count() == 1
    assert Event.objects.filter(session__release=first).count() == 2
    assert Audit.objects.filter(study=study, action='publication.policy_changed').count() == 1
    assert Audit.objects.filter(study=study, action='publication.current_release_changed').count() == 2
