"""T30/P0307 real-Chrome evidence: modules, two languages, three themes, permissions.

Runs against the pytest disposable live server and its temporary database only
(never the acceptance instance). Prerequisites are required: if Chrome, Playwright
or the live server is missing the checks fail, they do not silently skip. The
GEP_T17_BROWSER guard is the same opt-in switch the Required Verification command
sets; inside the run everything is mandatory.
"""
import os
import socket
import subprocess
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import (AccountProfile, Build, Event, Grant, Instance, Participant,
                         Release, Session, Study)
from core.services import admit, receive

pytestmark = pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                                reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')

OWNER_PASSWORD = 'synthetic-test-password'
PARTICIPANT_CODE = '001'
EVIDENCE_DIR = Path(__file__).resolve().parents[1] / 'local_data' / 'phase03_20260920' / 'p0307' / 'evidence'
ALL_ACTIONS = ('study.view', 'study.configure', 'build.upload', 'build.preview', 'release.approve_pilot',
               'recruitment.manage', 'data.export_raw', 'session.recover', 'member.manage',
               'permission.delegate', 'audit.view', 'identity_mapping.read', 'session.view')


@pytest.fixture(scope='session', autouse=True)
def browser_static_urls(django_test_environment):
    """Django's live-server static wrapper requires string URL prefixes."""
    from django.conf import settings
    before = (settings.STATIC_URL, settings.MEDIA_URL)
    settings.STATIC_URL = '/static/'
    settings.MEDIA_URL = '/media/'
    yield
    settings.STATIC_URL, settings.MEDIA_URL = before


@pytest.fixture
def ui_world(db):
    owner = get_user_model().objects.create_user('ui_owner', password=OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='浏览器模块研究', mode='id', recruitment='open', max_sessions=50)
    build = Build.objects.create(study=study, descriptor={
        'platform': 'godot_web', 'version': 'v1',
        'schemas': {'exp.rt': {'id': 'rt', 'version': '1', 'schema': {
            'type': 'object', 'properties': {'rt_ms': {'type': 'number'}}, 'required': ['rt_ms'],
            'additionalProperties': False}}},
        'codebook': {'rt_ms': {'unit': 'ms', 'source': 'host'}}},
        digest='b' * 64, package_path='web.zip')
    release = Release.objects.create(study=study, build=build, approved=True,
                                     config={'purpose': 'synthetic', 'mode': 'id'})
    for action in ALL_ACTIONS:
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    for code in (PARTICIPANT_CODE, '002', '003', '004'):
        Participant.objects.create(study=study, code=code)
    sessions = {}
    for index in range(20):
        session, token = admit(release, {
            'operation_id': str(uuid.uuid4()), 'proof': f'{index:048d}'.ljust(48, 'x'),
            'instance_id': str(instance.instance_id), 'study_id': str(study.id),
            'release_id': str(release.id), 'build_id': str(build.id), 'participant_code': PARTICIPANT_CODE})
        sessions[f'{PARTICIPANT_CODE}-{index}'] = (session, token)
    complete, complete_token = admit(release, {
        'operation_id': str(uuid.uuid4()), 'proof': 'c' * 48, 'instance_id': str(instance.instance_id),
        'study_id': str(study.id), 'release_id': str(release.id), 'build_id': str(build.id),
        'participant_code': '002'})
    event_id, segment_id = uuid.uuid4(), uuid.uuid4()
    receive(complete.id, complete_token, {'batch_id': str(uuid.uuid4()), 'events': [{
        'protocol_version': 'gep/1', 'event_id': str(event_id), 'session_id': str(complete.id),
        'segment_id': str(segment_id), 'sequence': 1, 'event_type': 'exp.rt', 'schema_id': 'rt',
        'schema_version': '1', 'payload': {'rt_ms': 200}}]})
    from core.services import finish
    finish(complete.id, complete_token, {'event_ids': [str(event_id)], 'segment_ids': [str(segment_id)]})
    pending, pending_token = admit(release, {
        'operation_id': str(uuid.uuid4()), 'proof': 'd' * 48, 'instance_id': str(instance.instance_id),
        'study_id': str(study.id), 'release_id': str(release.id), 'build_id': str(build.id),
        'participant_code': '003'})
    revoked, revoked_token = admit(release, {
        'operation_id': str(uuid.uuid4()), 'proof': 'e' * 48, 'instance_id': str(instance.instance_id),
        'study_id': str(study.id), 'release_id': str(release.id), 'build_id': str(build.id),
        'participant_code': '004'})
    revoked.revoked = True
    revoked.save(update_fields=['revoked'])
    Study.objects.filter(pk=study.pk).update(public=True, public_summary='浏览器公开简介',
                                             public_duration='9 分钟', public_device_requirements='桌面 Chrome',
                                             current_release=release)
    admin = get_user_model().objects.create_user('ui_admin', password=OWNER_PASSWORD)
    AccountProfile.objects.create(user=admin, role='admin')
    ordinary = get_user_model().objects.create_user('ui_user', password=OWNER_PASSWORD)
    Grant.objects.create(user=ordinary, study=study, action='study.view', delegable=False)
    return {'owner': owner, 'instance': instance, 'study': study, 'release': release,
            'sessions': sessions, 'complete': complete, 'pending': pending, 'revoked': revoked,
            'admin': admin, 'ordinary': ordinary}


def experiment_base(live_server):
    """A second hostname for the same live server: prefers the configured name."""
    from urllib.parse import urlparse
    parsed = urlparse(live_server.url)
    port = parsed.port
    for host in ('experiment.localhost', '127.0.0.1'):
        try:
            with socket.create_connection((host, port), timeout=1):
                return f'http://{host}:{port}'
        except OSError:
            continue
    raise AssertionError(f'no experiment hostname reachable on port {port}')


def run_chrome(script, env_extra):
    root = Path(__file__).resolve().parents[1]
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GEP_EVIDENCE_DIR=str(EVIDENCE_DIR), **env_extra)
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr


JS_HELPERS = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const admin=process.env.GEP_ADMIN_BASE, experiment=process.env.GEP_EXPERIMENT_BASE;
const study=process.env.GEP_STUDY_ID, evidence=process.env.GEP_EVIDENCE_DIR, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const errors=[];
function check(ok,label,detail){if(!ok){throw new Error('CHECK FAILED: '+label+' :: '+JSON.stringify(detail));}}
async function login(page,username,password){const response=await page.goto(admin+'/login');expect(response.status()).toBe(200);await page.locator('[name=username]').fill(username);await page.locator('[name=password]').fill(password);await page.getByRole('button',{name:'登录'}).click();await page.waitForURL(admin+'/');}
function rgb(text){const match=/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)/.exec(text||'');return match?[Number(match[1]),Number(match[2]),Number(match[3])]:null;}
function luminance(color){const f=v=>{v/=255;return v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4);};return 0.2126*f(color[0])+0.7152*f(color[1])+0.0722*f(color[2]);}
function ratio(fg,bg){const a=luminance(fg),b=luminance(bg);const hi=Math.max(a,b),lo=Math.min(a,b);return (hi+0.05)/(lo+0.05);}
async function colors(page,selector){return page.locator(selector).first().evaluate(el=>{const bgOf=node=>{for(let cur=node;cur;cur=cur.parentElement){const value=getComputedStyle(cur).backgroundColor;if(value&&value!=='rgba(0, 0, 0, 0)'){return value;}}return 'rgb(255, 255, 255)';};return {ink:getComputedStyle(el).color,bg:bgOf(el)};});}
async function contrast(page,label,selector,minimum){const pair=await colors(page,selector);const value=ratio(rgb(pair.ink),rgb(pair.bg));check(value>=minimum,label+' contrast',{value:+value.toFixed(2),ink:pair.ink,bg:pair.bg});return value;}
async function activeNav(page){return page.evaluate('document.activeElement && document.activeElement.getAttribute("data-nav-link")');}
'''

SCRIPT_LAYOUT = JS_HELPERS + r'''
try {
  const context=await browser.newContext({viewport:{width:1280,height:800}});
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  await login(page,'ui_owner',ownerPassword);
  await expect(page.locator(`[data-study-card="${study}"]`)).toBeVisible();
  check(await page.locator('[data-nav="1"]').count()===1,'one stable sidebar on the dashboard',await page.locator('[data-nav="1"]').count());
  check((await page.locator('[data-current-account]').innerText()).includes('ui_owner'),'current account is shown in the sidebar');

  // Every module is a separate page reached through the stable sidebar.
  await page.goto(admin+'/studies/'+study);
  for(const module of ['participation','builds','recruitment','sessions','exports']){
    await page.locator(`[data-nav-link="${module}"]`).click();
    await page.waitForLoadState('load');
    const marker=await page.locator('main').getAttribute('data-module');
    check(marker===module,'sidebar opens module '+module,marker+' '+page.url());
    await expect(page.locator('[data-nav="1"]')).toBeVisible();
  }

  // Labels stay associated on the participation form.
  await page.locator('[data-nav-link="participation"]').click();
  await page.waitForLoadState('load');
  const unlabeled=await page.evaluate(`(()=>{const out=[];document.querySelectorAll('input,select,textarea').forEach(el=>{if(el.type==='hidden')return;if(el.closest('label'))return;const id=el.id;if(id&&document.querySelector('label[for="'+id+'"]'))return;out.push(el.getAttribute('name')||el.tagName);});return out;})()`);
  check(unlabeled.length===0,'every visible control has an associated label',unlabeled);

  // Two languages x three themes: lang/theme attributes, translated generic UI,
  // and measured text contrast from the real computed colors.
  for(const lang of ['zh','en']){
    for(const theme of ['system','light','dark']){
      await page.goto(admin+`/prefs?lang=${lang}&theme=${theme}&next=/studies/${study}/sessions`);
      check(await page.getAttribute('html','lang')===lang,'html lang is '+lang);
      check(await page.getAttribute('html','data-theme')===theme,'data-theme is '+theme);
      const heading=await page.locator('main h2').first().innerText();
      check(heading.includes(lang==='en'?'Sessions & recovery':'会话与恢复'),'translated heading ('+lang+')',heading);
      check((await page.locator('[data-nav="1"]').innerText()).includes(lang==='en'?'My studies':'我的研究'),'translated navigation ('+lang+')');
      if(theme!=='system'){
        await contrast(page,`body text (${theme})`,'body',4.5);
        await contrast(page,`nav text (${theme})`,'.side a',4.5);
        await contrast(page,`muted text (${theme})`,'p.muted',4.5);
        await contrast(page,`button text (${theme})`,'button',4.5);
      }
    }
  }

  // The system theme follows the real OS preference in both directions.
  await page.emulateMedia({colorScheme:'dark'});
  await page.goto(admin+`/prefs?theme=system&next=/studies/${study}/sessions`);
  const systemDark=(await colors(page,'body')).bg;
  await page.goto(admin+`/prefs?theme=dark&next=/studies/${study}/sessions`);
  const explicitDark=(await colors(page,'body')).bg;
  check(systemDark===explicitDark,'system theme matches the dark palette',{systemDark,explicitDark});
  await page.emulateMedia({colorScheme:'light'});
  await page.goto(admin+`/prefs?theme=system&next=/studies/${study}/sessions`);
  const systemLight=(await colors(page,'body')).bg;
  await page.goto(admin+`/prefs?theme=light&next=/studies/${study}/sessions`);
  const explicitLight=(await colors(page,'body')).bg;
  check(systemLight===explicitLight,'system theme matches the light palette',{systemLight,explicitLight});
  check(systemLight!==systemDark,'light and dark palettes differ');

  // Keyboard: Tab reaches a module link, the focus ring is visible and >=3:1,
  // and Enter follows the focused link.
  await page.goto(admin+'/studies/'+study);
  let reached=false;
  for(let index=0;index<40&&!reached;index++){await page.keyboard.press('Tab');if(await activeNav(page)==='participation'){reached=true;}}
  check(reached,'Tab reaches the participation module link');
  const focus=await page.evaluate(`(()=>{const el=document.activeElement;const cs=getComputedStyle(el);return {style:cs.outlineStyle,width:cs.outlineWidth,color:cs.outlineColor};})()`);
  check(focus.style!=='none'&&parseFloat(focus.width)>=2,'focused link has a visible outline',focus);
  const bodyBg=(await colors(page,'body')).bg;
  check(ratio(rgb(focus.color),rgb(bodyBg))>=3,'focus ring contrast >= 3:1',{ring:focus.color,bg:bodyBg});
  await Promise.all([page.waitForURL(value=>value.pathname.endsWith('/participation')),page.keyboard.press('Enter')]);
  check(page.url().endsWith('/participation'),'Enter follows the focused module link',page.url());

  // A real recovery action publishes an aria-live status message with the code.
  await page.goto(admin+'/studies/'+study+'/sessions');
  await page.locator('[data-session-row] form').filter({has:page.locator('[name=op][value=recover_code]')}).first().locator('button').click();
  await page.waitForLoadState('load');
  const notice=page.locator('[data-notice]');
  await expect(notice).toBeVisible();
  const noticeText=await notice.innerText();
  check(/[0-9]{6}/.test(noticeText),'six-digit recovery notice rendered',noticeText.slice(0,30));
  check(await notice.getAttribute('role')==='status','notice is an aria-live status region');

  // Narrow layout on three real pages, then desktop evidence screenshots.
  await page.setViewportSize({width:390,height:844});
  for(const [name,url] of [['dashboard',admin+'/'],['sessions',admin+'/studies/'+study+'/sessions'],['users',admin+'/users'],['portal',experiment+'/']]){
    await page.goto(url);
    const overflow=await page.evaluate('document.documentElement.scrollWidth-document.documentElement.clientWidth');
    check(overflow<=1,'no horizontal overflow at 390px on '+name,overflow);
    await page.screenshot({path:evidence+'/narrow_'+name+'.png',fullPage:true});
  }
  await page.setViewportSize({width:1366,height:900});
  await page.goto(admin+'/');await page.screenshot({path:evidence+'/desktop_dashboard.png',fullPage:true});
  await page.goto(admin+'/studies/'+study+'/sessions');await page.screenshot({path:evidence+'/desktop_sessions.png',fullPage:true});
  await page.goto(admin+'/users');await page.screenshot({path:evidence+'/desktop_users.png',fullPage:true});
  await page.goto(experiment+'/');await page.screenshot({path:evidence+'/desktop_portal.png',fullPage:true});
  check(errors.length===0,'no uncaught page errors',errors);
  console.log('OK: modules, zh/en, system/light/dark, keyboard focus, measured contrast, narrow layout');
} finally {await browser.close();}
'''


def test_chrome_modules_languages_themes_keyboard_and_narrow_layout(ui_world, live_server):
    run_chrome(SCRIPT_LAYOUT, {'GEP_ADMIN_BASE': live_server.url,
                               'GEP_EXPERIMENT_BASE': experiment_base(live_server),
                               'GEP_STUDY_ID': str(ui_world['study'].id),
                               'GEP_OWNER_PASSWORD': OWNER_PASSWORD})
    for name in ('narrow_dashboard', 'narrow_sessions', 'narrow_users', 'narrow_portal',
                 'desktop_dashboard', 'desktop_sessions', 'desktop_users', 'desktop_portal'):
        path = EVIDENCE_DIR / f'{name}.png'
        assert path.exists() and path.stat().st_size > 2000, f'missing screenshot evidence: {name}'


SCRIPT_PERMISSIONS = JS_HELPERS + r'''
try {
  // Owner: the instance users page is reachable and a real export request with
  // the browser session succeeds (the comparison base for forged requests).
  const ownerContext=await browser.newContext({viewport:{width:1280,height:800}});
  const owner=await ownerContext.newPage();
  owner.on('pageerror',e=>errors.push('owner:'+e.message));
  await login(owner,'ui_owner',ownerPassword);
  check((await owner.locator('[data-nav-link="users"]').count())===1,'Owner sees the instance users link');
  const usersResponse=await owner.goto(admin+'/users');
  check(usersResponse.status()===200,'Owner opens /users',usersResponse.status());
  await expect(owner.locator('[data-permission-matrix]')).toBeVisible();
  // The matrix is searchable and its account column is frozen: measured from
  // the real computed style and real horizontal scroll, not class names.
  const matrixSearch=owner.locator('[data-matrix-search] input[name=q]');
  await matrixSearch.fill('ui_user');
  await owner.locator('[data-matrix-search] button').click();
  await owner.waitForLoadState('load');
  const searchedRows=await owner.locator('[data-matrix-row]').count();
  check(searchedRows===1,'matrix search narrows to the matching account',searchedRows);
  check(await owner.locator('[data-matrix-row]').first().getAttribute('data-matrix-row')===process.env.GEP_ORDINARY_ID+'-'+study,'matrix search keeps the exact account row',await owner.locator('[data-matrix-row]').first().getAttribute('data-matrix-row'));
  await owner.locator('[data-matrix-search] a').click();
  await owner.waitForLoadState('load');
  check(await owner.locator('[data-matrix-row]').count()>=3,'clearing the search restores the matrix',await owner.locator('[data-matrix-row]').count());
  await owner.setViewportSize({width:640,height:800});
  const frozen=await owner.locator('[data-permission-matrix] .matrix-wrap').evaluate(el=>{
    el.scrollLeft=400;
    const cell=el.querySelector('th[scope=row]');
    return {position:getComputedStyle(cell).position,left:cell.getBoundingClientRect().left,
            wrapLeft:el.getBoundingClientRect().left,scrollLeft:el.scrollLeft};
  });
  check(frozen.position==='sticky','matrix account column uses sticky positioning',frozen);
  check(Math.abs(frozen.left-frozen.wrapLeft)<=2,'frozen account column stays visible while the matrix scrolls',frozen);
  await owner.setViewportSize({width:1280,height:800});
  await owner.goto(admin+'/');
  const ownerExport=await owner.evaluate(async studyId=>{
    const token=document.cookie.split('; ').find(v=>v.startsWith('csrftoken='))?.split('=')[1]||'';
    const response=await fetch('/v1/admin/exports',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':token},body:JSON.stringify({study_id:studyId})});
    return response.status;
  },study);
  check(ownerExport===201,'Owner creates a snapshot through the API',ownerExport);
  const adminCookie=(await ownerContext.cookies()).find(c=>c.name==='gep_admin');
  check(adminCookie&&adminCookie.domain==='localhost'&&!adminCookie.domain.startsWith('.'),'admin cookie is host-only',adminCookie);

  // Admin: instance page without study grants.
  const adminContext=await browser.newContext();
  const adminPage=await adminContext.newPage();
  adminPage.on('pageerror',e=>errors.push('admin:'+e.message));
  await login(adminPage,'ui_admin',ownerPassword);
  check((await adminPage.locator('[data-nav-link="users"]').count())===1,'Admin sees the instance users link');
  const adminUsers=await adminPage.goto(admin+'/users');
  check(adminUsers.status()===200,'Admin opens /users',adminUsers.status());
  check((await adminPage.locator('[data-study-card]').count())===0,'Admin without study grants sees no study card');
  // Zero-delegable-scope Admin: the whole-instance inspection is read-only and
  // grants nothing; the Owner row and outside-authority grants carry the
  // read-only marker and no form controls.
  check((await adminPage.content()).includes('实例内所有账号的授权都可以查看'),'Admin sees the read-only scope explanation');
  const adminOwnerRow=adminPage.locator(`[data-matrix-row="${process.env.GEP_OWNER_ID}-${study}"]`);
  await expect(adminOwnerRow).toBeVisible();
  check(await adminOwnerRow.locator('small[data-readonly-reason="owner"]').count()===1,'Admin sees the Owner row marked read-only');
  check(await adminOwnerRow.locator('form,input,button').count()===0,'Admin gets no controls on the Owner row');
  const adminOrdinaryRow=adminPage.locator(`[data-matrix-row="${process.env.GEP_ORDINARY_ID}-${study}"]`);
  await expect(adminOrdinaryRow).toBeVisible();
  check(await adminOrdinaryRow.locator('small[data-readonly-reason="outside"]').count()===1,'Admin sees an outside-authority grant read-only');
  check(await adminOrdinaryRow.locator('form,input,button').count()===0,'read-only outside-authority row offers no controls');
  const adminDeniedSessions=await adminPage.goto(admin+'/studies/'+study+'/sessions');
  check(adminDeniedSessions.status()===403,'zero-scope Admin is denied the sessions module',adminDeniedSessions.status());
  await adminContext.close();

  // Ordinary member: no admin surface, gated modules refused, forged requests 403.
  const userContext=await browser.newContext();
  const userPage=await userContext.newPage();
  userPage.on('pageerror',e=>errors.push('user:'+e.message));
  await login(userPage,'ui_user',ownerPassword);
  check((await userPage.locator('[data-study-card]').count())===1,'member sees the authorized study card');
  check((await userPage.locator('[data-nav-link="users"]').count())===0,'member sees no instance users link');
  check((await userPage.locator('[data-nav-link="exports"]').count())===0,'member sees no exports link');
  const deniedUsers=await userPage.goto(admin+'/users');
  check(deniedUsers.status()===403,'member is denied /users directly',deniedUsers.status());
  const deniedSessions=await userPage.goto(admin+'/studies/'+study+'/sessions');
  check(deniedSessions.status()===403,'member is denied the sessions module',deniedSessions.status());
  const deniedExports=await userPage.goto(admin+'/studies/'+study+'/exports');
  check(deniedExports.status()===403,'member is denied the exports module',deniedExports.status());
  await userPage.goto(admin+'/');
  const forgedExport=await userPage.evaluate(async studyId=>{
    const token=document.cookie.split('; ').find(v=>v.startsWith('csrftoken='))?.split('=')[1]||'';
    const response=await fetch('/v1/admin/exports',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':token},body:JSON.stringify({study_id:studyId})});
    return response.status;
  },study);
  check(forgedExport===403,'member forged snapshot request is refused',forgedExport);
  const forgedRecovery=await userPage.evaluate(async ({studyId,sessionId})=>{
    const token=document.cookie.split('; ').find(v=>v.startsWith('csrftoken='))?.split('=')[1]||'';
    const body=new URLSearchParams({op:'recover_code',session_id:sessionId,csrfmiddlewaretoken:token});
    const response=await fetch('/studies/'+studyId,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});
    return response.status;
  },{studyId:study,sessionId:process.env.GEP_SESSION_ID});
  check(forgedRecovery===403,'member forged recovery request is refused',forgedRecovery);

  // Cross-origin: the experiment portal receives no admin cookie or admin surface.
  const portalResponse=await owner.goto(experiment+'/');
  check(portalResponse.status()===200,'portal loads on the experiment origin',portalResponse.status());
  const setCookie=portalResponse.headers()['set-cookie']||'';
  check(!setCookie.includes('gep_admin'),'experiment response sets no admin cookie',setCookie.slice(0,80));
  check(await owner.evaluate('document.cookie')==='','no cookie is readable on the experiment origin');
  const portalBody=await owner.content();
  check(!portalBody.includes('data-nav')&&!portalBody.includes('ui_owner'),'portal exposes no admin navigation or account name');
  check((await owner.locator('[data-study="'+study+'"]').count())===1,'portal lists the public study on the experiment origin');
  check(errors.length===0,'no uncaught page errors',errors);
  console.log('OK: Owner/Admin/member routes, forged requests 403, host-only admin cookie');
} finally {await browser.close();}
'''


def test_chrome_permission_routes_forged_requests_and_cookie_isolation(ui_world, live_server):
    run_chrome(SCRIPT_PERMISSIONS, {'GEP_ADMIN_BASE': live_server.url,
                                    'GEP_EXPERIMENT_BASE': experiment_base(live_server),
                                    'GEP_STUDY_ID': str(ui_world['study'].id),
                                    'GEP_SESSION_ID': str(ui_world['complete'].id),
                                    'GEP_ORDINARY_ID': str(ui_world['ordinary'].pk),
                                    'GEP_OWNER_ID': str(ui_world['owner'].pk),
                                    'GEP_OWNER_PASSWORD': OWNER_PASSWORD})


SCRIPT_QUERIES_PORTAL = JS_HELPERS + r'''
try {
  const context=await browser.newContext({viewport:{width:1366,height:900}});
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push('queries:'+e.message));
  await login(page,'ui_owner',ownerPassword);

  // Exact roster-code search in the real Chrome form, including leading zeros.
  await page.goto(admin+'/studies/'+study+'/sessions');
  await page.locator('[data-sessions-filter] [name=code]').fill('001');
  await page.locator('[data-sessions-filter] button').click();
  await page.waitForLoadState('load');
  const filtered=await page.locator('[data-session-row]').count();
  check(filtered===20,'exact code search returns the 20 roster-ID sessions',filtered);
  check((await page.locator('[data-sessions-total]').getAttribute('data-sessions-total'))==='23','total sessions unchanged by filters');
  check(/\(UTC/.test(await page.locator('[data-timezone]').getAttribute('data-timezone')),'server timezone is labelled');

  // Status filter on its own, then back to all rows.
  await page.locator('[data-sessions-filter] [name=code]').fill('');
  await page.locator('[data-sessions-filter] select[name=status]').selectOption('complete');
  await page.locator('[data-sessions-filter] button').click();
  await page.waitForLoadState('load');
  check(await page.locator('[data-session-row]').count()===1,'complete filter shows exactly the finished session');
  check((await page.locator('[data-session-row]').getAttribute('data-status'))==='complete','filtered row is complete');
  await page.locator('[data-sessions-filter] select[name=status]').selectOption('');
  await page.locator('[data-sessions-filter] button').click();
  await page.waitForLoadState('load');
  check(await page.locator('[data-session-row]').count()===20,'cleared filters return the first page of 20');
  await page.locator('[data-sessions-pager] a').first().click();
  await page.waitForLoadState('load');
  check(await page.locator('[data-session-row]').count()===3,'second page has the remaining three sessions');

  // Roster search in the participation module.
  await page.goto(admin+'/studies/'+study+'/participation');
  await page.locator('[data-roster-search] input[name=code]').fill('002');
  await page.locator('[data-roster-search] button').click();
  await page.waitForLoadState('load');
  check(await page.locator('[data-roster-row]').count()===1,'roster exact search returns one row');
  check((await page.locator('[data-roster-row] td').first().innerText())==='002','roster row keeps the exact code');

  // Portal on the experiment origin: bilingual, three themes, no admin surface.
  await page.goto(experiment+'/');
  check(await page.getAttribute('html','lang')==='zh','portal defaults to Chinese');
  check((await page.getAttribute('html','data-theme'))==='system','portal defaults to the system theme');
  await expect(page.locator('[data-study="'+study+'"]')).toBeVisible();
  await page.locator('[data-pref-lang=en]').click();
  await page.waitForLoadState('load');
  check(await page.getAttribute('html','lang')==='en','portal switches to English');
  check((await page.locator('h1').first().innerText()).includes('Participating studies'),'portal English heading');
  check((await page.locator('[data-study="'+study+'"]').innerText()).includes('Duration'),'portal English field labels');
  await page.locator('[data-pref-theme=dark]').click();
  await page.waitForLoadState('load');
  check(await page.getAttribute('html','data-theme')==='dark','portal switches to dark');
  const portalDark=(await colors(page,'body')).bg;
  await page.locator('[data-pref-theme=light]').click();
  await page.waitForLoadState('load');
  const portalLight=(await colors(page,'body')).bg;
  check(portalDark!==portalLight,'portal themes change the actual palette',{portalDark,portalLight});
  const portalBody=await page.content();
  check(!portalBody.includes('data-nav')&&!portalBody.includes('ui_owner'),'portal exposes no admin surface');

  // Keyboard focus is visible on the portal too.
  await page.locator('[data-pref-lang=zh]').click();
  await page.waitForLoadState('load');
  let reached=false;
  for(let index=0;index<20&&!reached;index++){await page.keyboard.press('Tab');const style=await page.evaluate('document.activeElement && getComputedStyle(document.activeElement).outlineStyle');if(style&&style!=='none'){reached=true;}}
  check(reached,'a keyboard focus ring exists on the portal');
  check(errors.length===0,'no uncaught page errors',errors);
  console.log('OK: Chrome sessions search/filter/pagination, roster search, portal zh/en and themes');
} finally {await browser.close();}
'''


def test_chrome_session_roster_queries_and_bilingual_portal(ui_world, live_server):
    run_chrome(SCRIPT_QUERIES_PORTAL, {'GEP_ADMIN_BASE': live_server.url,
                                       'GEP_EXPERIMENT_BASE': experiment_base(live_server),
                                       'GEP_STUDY_ID': str(ui_world['study'].id),
                                       'GEP_OWNER_PASSWORD': OWNER_PASSWORD})


SCRIPT_MATRIX_LANGUAGES = JS_HELPERS + r'''
try {
  const context=await browser.newContext({viewport:{width:1366,height:900}});
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push('matrix:'+e.message));
  await login(page,'ui_owner',ownerPassword);

  // ---------- English: matrix controls, a real preview, a real error ----------
  await page.goto(admin+'/prefs?lang=en&theme=system&next=/users');
  check(await page.getAttribute('html','lang')==='en','English preference is active on /users');
  await expect(page.locator('[data-permission-matrix]')).toBeVisible();
  for(const label of ['Instance permission matrix','Search accounts by username','Study visibility: view study overview','Explicit actions (expand)','Delegable','Preview change']){
    check((await page.content()).includes(label),'English matrix label '+label);
  }
  const englishBody=await page.content();
  check(!englishBody.includes('预览更改')&&!englishBody.includes('显式动作（点击展开）'),'English matrix has no Chinese generic controls');

  const englishRow=page.locator(`[data-matrix-row="${process.env.GEP_ORDINARY_ID}-${study}"]`);
  await expect(englishRow).toBeVisible();
  const englishForm=englishRow.locator('form[data-visibility-form]');
  await englishForm.locator('details').evaluate(el=>{el.open=true});
  await englishForm.locator('[name="action:session.view"]').check();
  await englishForm.getByRole('button',{name:'Preview change'}).click();
  await page.waitForLoadState('load');
  const englishPreview=page.locator('[data-preview="matrix"]');
  await expect(englishPreview).toContainText('Change preview (not executed)');
  await expect(englishPreview).toContainText('add session.view');
  await englishPreview.locator('[name=password]').fill('wrong-owner-password-2026');
  await englishPreview.getByRole('button',{name:/^Confirm/}).click();
  await page.waitForLoadState('load');
  await expect(page.locator('[role=alert]')).toContainText('Re-authentication failed');
  await expect(page.locator('[data-preview="matrix"]')).toBeVisible();
  await page.locator('[data-preview="matrix"] [name=password]').fill(ownerPassword);
  await page.locator('[data-preview="matrix"]').getByRole('button',{name:/^Confirm/}).click();
  await page.waitForLoadState('load');
  await expect(page.locator('.notice')).toContainText('Permission matrix updated: ui_user');
  await page.screenshot({path:evidence+'/desktop_users_en.png',fullPage:true});

  // ---------- Chinese: the same controls and lifecycle in Chinese ----------
  await page.goto(admin+'/prefs?lang=zh&theme=system&next=/users');
  check(await page.getAttribute('html','lang')==='zh','Chinese preference is active on /users');
  const chineseBody=await page.content();
  for(const label of ['实例权限矩阵','研究可见：查看研究概况','显式动作（点击展开）','可委派','预览更改']){
    check(chineseBody.includes(label),'Chinese matrix label '+label);
  }
  check(!chineseBody.includes('Instance permission matrix')&&!chineseBody.includes('Preview change'),'Chinese matrix has no English generic controls');
  const chineseRow=page.locator(`[data-matrix-row="${process.env.GEP_ORDINARY_ID}-${study}"]`);
  const chineseForm=chineseRow.locator('form[data-visibility-form]');
  await chineseForm.locator('details').evaluate(el=>{el.open=true});
  await chineseForm.locator('[name="action:identity_mapping.read"]').check();
  await chineseForm.getByRole('button',{name:'预览更改'}).click();
  await page.waitForLoadState('load');
  const chinesePreview=page.locator('[data-preview="matrix"]');
  await expect(chinesePreview).toContainText('更改预览（未执行）');
  await expect(chinesePreview).toContainText('新增 identity_mapping.read');
  await chinesePreview.locator('[name=password]').fill('wrong-owner-password-2026');
  await chinesePreview.getByRole('button',{name:/确认执行/}).click();
  await page.waitForLoadState('load');
  await expect(page.locator('[role=alert]')).toContainText('重新认证失败');
  await page.locator('[data-preview="matrix"] [name=password]').fill(ownerPassword);
  await page.locator('[data-preview="matrix"]').getByRole('button',{name:/确认执行/}).click();
  await page.waitForLoadState('load');
  await expect(page.locator('.notice')).toContainText('权限矩阵已更新：ui_user');
  // Both committed actions persist for the ordinary account.
  await page.goto(admin+'/users');
  const persisted=page.locator(`[data-matrix-row="${process.env.GEP_ORDINARY_ID}-${study}"] form[data-visibility-form]`);
  await persisted.locator('details').evaluate(el=>{el.open=true});
  await expect(persisted.locator('[name="action:session.view"]')).toBeChecked();
  await expect(persisted.locator('[name="action:identity_mapping.read"]')).toBeChecked();
  check(errors.length===0,'no uncaught page errors',errors);
  console.log('OK: English and Chinese matrix controls, previews, reauth errors and commits in real Chrome');
} finally {await browser.close();}
'''


def test_chrome_bilingual_matrix_controls_previews_and_reauth_errors(ui_world, live_server):
    run_chrome(SCRIPT_MATRIX_LANGUAGES, {'GEP_ADMIN_BASE': live_server.url,
                                         'GEP_STUDY_ID': str(ui_world['study'].id),
                                         'GEP_ORDINARY_ID': str(ui_world['ordinary'].pk),
                                         'GEP_OWNER_PASSWORD': OWNER_PASSWORD})
    path = EVIDENCE_DIR / 'desktop_users_en.png'
    assert path.exists() and path.stat().st_size > 2000, 'missing English /users screenshot evidence'
