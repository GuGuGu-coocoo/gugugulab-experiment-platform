"""P03R01 evidence: upload reasons, approve location, complete invitation link.

Server-side checks prove the bilingual upload/descriptor/native rejection
mapping (with no absolute server path in any message), that a rejected file
creates no build or release, that the approve POST redirects to the same study's
builds release anchor, and that the account invitation link is built only from
the configured admin origin plus the real request port - never from an
attacker-supplied ``Host`` or ``X-Forwarded-*`` header.

The opt-in real-Chrome check (``GEP_T17_BROWSER=1``, exactly the Required
Verification switch) proves the same behavior in an actual browser against the
pytest disposable live server and its temporary database: a broken ZIP and a
tampered package show their reason in place and create no release, a valid
upload is approved and located at the builds anchor, and the single complete
invitation link with the server-owned non-default port shows no relative
duplicate and opens the controlled admin set-password page.

Random invitation tokens stay in memory for the real activation only: saved
observations carry redacted origin/path/port/status/has_token fields, failure
diagnostics are redacted, and each actual minted token is scanned against the
new evidence files and the captured browser output.
"""
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.test.utils import override_settings

from core.models import AccountInvitation, Audit, Build, Grant, Instance, Release, Study

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = 'synthetic-p03r01-owner-password'
INVITEE_PASSWORD = 'synthetic-p03r01-invitee-password'
LINK_RE = re.compile(r'data-invitation-link="([^"]+)"')
RELATIVE_LINK_RE = re.compile(r'<code>/activate-account\?token=')
TOKEN_PARAM_RE = re.compile(r'token=[A-Za-z0-9_\-]+')
DESCRIPTOR = json.loads((REPO_ROOT / 'examples' / 'synthetic_experiment' / 'descriptor.json').read_text())


def link_of(response):
    match = LINK_RE.search(response.content.decode())
    assert match is not None, 'the one-time invitation must show one complete link'
    return match.group(1)


def token_of(link):
    return parse_qs(urlparse(link).query)['token'][0]


def redacted_link(link):
    """Saved observation: origin/path/port/has_token only, never the token."""
    parsed = urlparse(link)
    token = parse_qs(parsed.query).get('token', [''])
    return {'origin': f'{parsed.scheme}://{parsed.hostname}:{parsed.port}', 'path': parsed.path,
            'port': parsed.port, 'has_token': len(token[0]) > 20}


def redact_text(text):
    """Strip complete token values from diagnostics before they are printed."""
    return TOKEN_PARAM_RE.sub('token=<redacted>', text)


def _descriptor(version, body):
    descriptor = json.loads(json.dumps(DESCRIPTOR))
    descriptor['version'] = version
    descriptor['program_sha256'] = hashlib.sha256(b'web/index.html' + body).hexdigest()
    return descriptor


def _archive(descriptor, body):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(descriptor))
        archive.writestr('web/index.html', body)
    return target.getvalue()


def web_package(version, body=b'<html><head></head><body>P03R01 synthetic package</body></html>'):
    """A valid Web package whose manifest digest matches its real member bytes."""
    return _archive(_descriptor(version, body), body)


def tampered_package(version):
    """A readable archive whose member bytes no longer match the declared digest."""
    return _archive(_descriptor(version, b'<html><body>declared bytes</body></html>'),
                    b'<html><body>different bytes after the manifest</body></html>')


def bad_manifest_package(version, mutate):
    descriptor = _descriptor(version, b'<html></html>')
    mutate(descriptor)
    return _archive(descriptor, b'<html></html>')


@pytest.fixture
def world(db):
    """Owner plus one synthetic study with the grants R01 needs.

    Authorization semantics are stated explicitly and stay the current pre-R02
    (v1) ones: ``authorization_version`` is introduced by R02, so this R01
    fixture deliberately touches no permission rule, Grant default or role.
    """
    owner = get_user_model().objects.create_user('p03r01_owner', password=OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='P03R01 合成研究', mode='id', recruitment='open', max_sessions=5)
    for action in ('study.view', 'study.configure', 'build.upload', 'build.preview', 'release.approve_pilot'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    return {'owner': owner, 'instance': instance, 'study': study}


def owner_client(world):
    client = Client()
    client.force_login(world['owner'])
    return client


def old_release(study, version='p03r01-old'):
    """One pre-existing approved release that the invalid uploads must not touch."""
    build = Build.objects.create(study=study, descriptor=_descriptor(version, b'<html>old</html>'),
                                 digest='a' * 64, package_path='old.zip')
    return Release.objects.create(study=study, build=build, approved=True,
                                  config={'purpose': 'synthetic', 'tag': version})


def invite(client, world, username, host='localhost:8040', server_port='8040', **headers):
    data = {'op': 'invite_account', 'username': username, 'password': OWNER_PASSWORD,
            'revision': str(Instance.objects.get(pk=1).governance_revision)}
    return client.post('/users', data, HTTP_HOST=host, SERVER_PORT=server_port, **headers)


def test_upload_rejections_report_bilingual_reason_and_create_nothing(world, settings, tmp_path, evidence):
    settings.DATA_DIR = tmp_path
    client = owner_client(world)
    study = world['study']
    url = f'/studies/{study.id}'
    old = old_release(study)
    observed = []
    cases = [
        ('broken.zip', b'this is not a zip archive at all', 422, 'invalid_archive', '不是有效的 ZIP'),
        ('tampered.zip', tampered_package('p03r01-tampered'), 422, 'program_digest_mismatch', '程序摘要与描述不一致'),
        ('unknown-field.zip', bad_manifest_package('p03r01-unknown', lambda d: d.update({'unknown_field': 1})),
         422, 'descriptor_fields', '构建描述字段不完整'),
        ('bad-schema.zip', bad_manifest_package('p03r01-schema', lambda d: d['schemas']['exp.rt'].update({'schema': {'type': 42}})),
         400, 'schema_invalid', '不是合法的 JSON Schema'),
    ]
    for name, raw, status, code, text in cases:
        response = client.post(url, {'op': 'upload', 'package': SimpleUploadedFile(name, raw)},
                               HTTP_ACCEPT='application/json')
        assert response.status_code == status, (name, response.content[:400])
        payload = response.json()
        assert payload['code'] == code, (name, payload)
        assert text in payload['error'], (name, payload)
        for leak in ('/Users/', str(tmp_path), str(settings.DATA_DIR)):
            assert leak not in payload['error'], (name, payload['error'])
        observed.append({'file': name, 'status': response.status_code, 'code': payload['code'],
                         'message': payload['error']})

    # The same rejection is English when the interface language is English.
    client.cookies['gep_lang'] = 'en'
    try:
        translated = client.post(url, {'op': 'upload', 'package': SimpleUploadedFile('broken.zip', b'not a zip')},
                                 HTTP_ACCEPT='application/json')
    finally:
        client.cookies.pop('gep_lang', None)
    assert translated.json()['code'] == 'invalid_archive'
    assert 'not a valid ZIP' in translated.json()['error']
    observed.append({'interface': 'en', 'code': translated.json()['code'], 'message': translated.json()['error']})

    # Without JavaScript the same reason is rendered in place in the HTML page.
    html = client.post(url, {'op': 'upload', 'package': SimpleUploadedFile('broken.zip', b'not a zip')},
                       HTTP_ACCEPT='text/html')
    body = html.content.decode()
    assert html.status_code == 422
    assert 'data-error="invalid_archive"' in body and '不是有效的 ZIP' in body
    observed.append({'interface': 'html', 'status': html.status_code, 'code': 'invalid_archive'})

    assert Build.objects.filter(study=study).count() == 1
    assert Release.objects.filter(study=study).count() == 1
    old.refresh_from_db()
    assert old.approved is True and old.config == {'purpose': 'synthetic', 'tag': 'p03r01-old'}
    evidence('upload_rejections.json', observed)


def test_approve_redirects_to_the_same_study_builds_anchor(world, settings, tmp_path, evidence):
    settings.DATA_DIR = tmp_path
    client = owner_client(world)
    study = world['study']
    old = old_release(study)
    raw = web_package('p03r01-web')
    response = client.post(f'/studies/{study.id}', {'op': 'upload', 'package': SimpleUploadedFile('p03r01.zip', raw)})
    assert response.status_code == 302
    build = next(item for item in Build.objects.filter(study=study) if item.descriptor.get('version') == 'p03r01-web')
    response = client.post(f'/studies/{study.id}', {'op': 'approve', 'build_id': str(build.id)})
    assert response.status_code == 302
    release = Release.objects.get(study=study, build=build)
    assert release.approved is True
    assert response['Location'] == f'/studies/{study.id}/builds#release-{release.id}'
    page = client.get(f'/studies/{study.id}/builds', HTTP_ACCEPT='text/html')
    assert page.status_code == 200
    assert f'id="release-{release.id}"' in page.content.decode()
    old.refresh_from_db()
    assert old.approved is True and study.current_release_id is None
    assert Build.objects.filter(study=study).count() == 2
    assert Release.objects.filter(study=study).count() == 2
    evidence('approve_redirect.json', {'location': response['Location'], 'release_id': str(release.id),
                                       'old_release_id': str(old.id), 'old_approved': old.approved})


def test_invitation_link_uses_the_controlled_admin_origin_only(world, evidence, capfd):
    client = owner_client(world)
    observed = {}
    tokens = []

    # The Host header port is untrusted: the link is built from the approved
    # request hostname plus the server-owned SERVER_PORT, never from Host.
    response = invite(client, world, 'p03r01_origin', host='localhost:9999', server_port='8040')
    assert response.status_code == 200
    assert response['Cache-Control'] == 'no-store'
    body = response.content.decode()
    link = link_of(response)
    token = token_of(link)
    tokens.append(token)
    assert link == f'http://localhost:8040/activate-account?token={token}'
    assert ':9999' not in link
    assert token not in str(list(Audit.objects.values()))
    # Exactly one complete clickable link, no relative duplicate.
    assert body.count('data-invitation-link=') == 1
    assert RELATIVE_LINK_RE.search(body) is None
    assert '同一管理主机相对地址' not in body
    observed['derived'] = redacted_link(link)
    assert link not in client.get('/users').content.decode()  # shown once, not on reload
    page = client.get(f'/activate-account?token={token}', HTTP_HOST='localhost:8040', SERVER_PORT='8040')
    assert page.status_code == 200 and '接受账号邀请' in page.content.decode()
    assert page['Cache-Control'] == 'no-store'
    observed['activation_page'] = {'status': page.status_code, 'cache_control': page['Cache-Control']}

    # Forwarded headers must not move the link off the controlled admin entry.
    forwarded = invite(client, world, 'p03r01_forwarded', host='localhost:9999', server_port='8040',
                       HTTP_X_FORWARDED_HOST='evil.example.test', HTTP_X_FORWARDED_PROTO='https')
    assert forwarded.status_code == 200
    forwarded_link = link_of(forwarded)
    tokens.append(token_of(forwarded_link))
    assert forwarded_link.startswith('http://localhost:8040/activate-account?token=')
    assert 'evil.example.test' not in forwarded_link and 'https' not in forwarded_link
    observed['forwarded'] = redacted_link(forwarded_link)

    # A non-admin Host cannot create an invitation or a link; a Host outside
    # ALLOWED_HOSTS is refused before any view runs.
    for host, expected in (('experiment.localhost:8040', 403), ('evil.example.test', 400)):
        response = invite(client, world, 'p03r01_badhost', host=host)
        assert response.status_code == expected, (host, response.status_code)
        assert LINK_RE.search(response.content.decode()) is None
        assert not AccountInvitation.objects.filter(username='p03r01_badhost').exists()
    observed['bad_hosts'] = {'experiment.localhost:8040': 403, 'evil.example.test': 400}

    # An explicitly configured origin wins, keeps its own scheme and port, and
    # is unaffected by the Host header port or the server port.
    with override_settings(ADMIN_ORIGIN='http://admin.localhost:8123'):
        configured = invite(client, world, 'p03r01_configured', host='localhost:9999', server_port='8040')
        assert configured.status_code == 200
        configured_link = link_of(configured)
        tokens.append(token_of(configured_link))
        assert configured_link.startswith('http://admin.localhost:8123/activate-account?token=')
    observed['configured'] = redacted_link(configured_link)

    # A configured origin that is not a controlled admin entry fails closed,
    # before any invitation exists, and the page states the configuration error.
    for broken in ('https://evil.example.test', 'http://admin.localhost:notaport', 'http://admin.localhost:8123/extra'):
        with override_settings(ADMIN_ORIGIN=broken):
            response = invite(client, world, 'p03r01_broken', host='localhost:8040', server_port='8040')
        assert response.status_code == 409, (broken, response.status_code)
        assert '管理地址配置无效' in response.content.decode()
        assert LINK_RE.search(response.content.decode()) is None
        assert not AccountInvitation.objects.filter(username='p03r01_broken').exists()
    observed['broken_configs'] = {'status': 409, 'invitation_created': False}

    # Redaction: the actual minted tokens never appear in saved evidence or in
    # any captured output/failure diagnostic.
    written = evidence('invitation_link.json', observed)
    saved = written.read_text(encoding='utf-8')
    captured = capfd.readouterr()
    assert 'token=' not in saved
    for value in tokens:
        assert value not in saved
        assert value not in captured.out and value not in captured.err
    assert len(tokens) == 3


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')
def test_actual_chrome_upload_approve_and_invitation_journey(live_server, world, settings, tmp_path, evidence, run_chrome_tokens):
    settings.DATA_DIR = tmp_path
    study = world['study']
    old = old_release(study)
    valid = evidence('p03r01-web.zip', web_package('p03r01-web'))
    broken = evidence('p03r01-broken.zip', b'this is not a zip archive at all')
    tampered = evidence('p03r01-tampered.zip', tampered_package('p03r01-tampered'))
    env = dict(os.environ, GEP_TEST_BASE=live_server.url, GEP_STUDY_ID=str(study.id),
               GEP_OWNER_PASSWORD=OWNER_PASSWORD, GEP_WEB_PACKAGE=str(valid),
               GEP_WEB_VERSION='p03r01-web', GEP_BROKEN_PACKAGE=str(broken),
               GEP_TAMPERED_PACKAGE=str(tampered), GEP_OLD_RELEASE=str(old.id))
    result, reported = run_chrome_tokens(SCRIPT, env, timeout=240)
    assert result.returncode == 0, redact_text(result.stdout + result.stderr)
    observations = json.loads(re.search(r'P03R01_OBSERVATIONS (\{.*\})', result.stdout).group(1))
    tokens = [line.strip() for line in reported]
    assert len(tokens) == 2 and all(len(value) > 20 for value in tokens), \
        'the browser journey must hand over its real tokens through the memory channel'

    # The real browser journey: the single complete link on the server-owned
    # non-default port opened the admin page and activated the account.
    base = urlparse(live_server.url)
    expected_origin = f'{base.scheme}://{base.hostname}:{base.port}'
    assert observations['invitation_origin'] == expected_origin
    assert observations['invitation_path'] == '/activate-account'
    assert observations['invitation_port'] == str(base.port)
    assert observations['invitation_has_token'] is True
    assert observations['invitation_port'] not in ('', '80', '443')
    assert observations['link_count'] == 1 and observations['relative_code_count'] == 0
    assert observations['activation_status'] == 200 and observations['activated'] is True
    assert observations['forwarded_origin'] == expected_origin
    assert observations['forwarded_has_token'] is True
    assert observations['bad_zip_reason'] and 'ZIP' in observations['bad_zip_reason']
    assert observations['tampered_reason'] and '摘要' in observations['tampered_reason']
    assert observations['approve_url'].endswith('#release-' + observations['new_release_id'])
    assert observations['page_errors'] == []

    # Redaction: the actual minted tokens never appear in saved evidence,
    # subprocess output or failure diagnostics.
    written = evidence('chrome_journey.json', observations)
    saved = written.read_text(encoding='utf-8')
    assert 'token=' not in result.stdout and 'token=' not in result.stderr
    for value in tokens:
        assert value not in result.stdout and value not in result.stderr
        assert value not in saved
    assert 'token=' not in saved

    # Invalid files created nothing; the valid file created exactly one new
    # build/release and the old approved release is untouched.
    assert Build.objects.filter(study=study).count() == 2
    assert Release.objects.filter(study=study).count() == 2
    new_release = Release.objects.get(pk=uuid.UUID(observations['new_release_id']))
    assert new_release.study_id == study.id and new_release.approved is True
    assert new_release.build.descriptor.get('version') == 'p03r01-web'
    old.refresh_from_db()
    assert old.approved is True and str(old.id) == observations['old_release_id']
    assert study.current_release_id is None
    assert AccountInvitation.objects.filter(username='p03r01_invitee', consumed=True).count() == 1
    assert get_user_model().objects.filter(username='p03r01_invitee').exists()


SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
import {writeSync} from 'node:fs';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, studyId=process.env.GEP_STUDY_ID;
const ownerPassword=process.env.GEP_OWNER_PASSWORD;
const tokenFd=Number(process.env.GEP_TOKEN_FD);
const observations={};
const errors=[];
function handOverToken(token){if(tokenFd&&token)writeSync(tokenFd,token+'\n');}
async function login(page){
  const response=await page.goto(base+'/login');
  expect(response.status()).toBe(200);
  await page.locator('[name=username]').fill('p03r01_owner');
  await page.locator('[name=password]').fill(ownerPassword);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
}
try {
  const context=await browser.newContext();
  const page=await context.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));

  // ---- account invitation: one complete link on the real admin entry ----
  await login(page);
  await page.goto(base+'/users');
  const inviteForm=page.locator('form').filter({has:page.locator('[name=op][value=invite_account]')});
  await inviteForm.locator('[name=username]').fill('p03r01_invitee');
  await inviteForm.locator('[name=password]').fill(ownerPassword);
  await inviteForm.getByRole('button',{name:'生成邀请（本人设置密码）'}).click();
  await page.waitForLoadState('load');
  const invitationLink=await page.locator('[data-one-time-invitation] a[data-invitation-link]').getAttribute('href');
  const parsedLink=new URL(invitationLink);
  const invitationToken=parsedLink.searchParams.get('token');
  handOverToken(invitationToken);
  observations.invitation_origin=parsedLink.origin;
  observations.invitation_path=parsedLink.pathname;
  observations.invitation_port=parsedLink.port;
  observations.invitation_has_token=!!invitationToken&&invitationToken.length>20;
  observations.link_count=await page.locator('[data-one-time-invitation] a[data-invitation-link]').count();
  observations.relative_code_count=await page.locator('[data-one-time-invitation] code').count();
  expect(parsedLink.origin).toBe(new URL(base).origin);
  expect(parsedLink.pathname).toBe('/activate-account');
  expect(parsedLink.host).toBe(new URL(base).host);
  expect(observations.link_count).toBe(1);
  expect(observations.relative_code_count).toBe(0);
  expect(!!invitationToken&&JSON.stringify(observations).includes(invitationToken)).toBe(false);

  const invitee=await browser.newContext();
  const setPage=await invitee.newPage();
  setPage.on('pageerror',e=>errors.push('invitee:'+e.message));
  const openResponse=await setPage.goto(invitationLink);
  expect(openResponse.status()).toBe(200);
  observations.activation_status=openResponse.status();
  await expect(setPage.getByRole('heading',{name:'接受账号邀请'})).toBeVisible();
  await expect(setPage.getByText('临时密码账号不使用此页面')).toBeVisible();
  await setPage.locator('[name=password]').fill('synthetic-p03r01-invitee-password');
  await setPage.locator('[name=confirm]').fill('synthetic-p03r01-invitee-password');
  await setPage.getByRole('button',{name:'激活账号'}).click();
  await expect(setPage.getByText('账号已激活')).toBeVisible();
  observations.activated=true;
  await invitee.close();

  // ---- a forwarded host/proto must not move the link off the admin entry ----
  // The activation above bumped the governance revision, so read the current
  // revision from a freshly loaded page (a stale revision is correctly refused).
  await page.goto(base+'/users');
  const csrf=await page.locator('input[name=csrfmiddlewaretoken]').first().getAttribute('value');
  const revision=await page.locator('form').filter({has:page.locator('[name=op][value=invite_account]')})
    .locator('[name=revision]').inputValue();
  const injected=await page.evaluate(async ({csrf,revision,ownerPassword})=>{
    const body=new URLSearchParams({op:'invite_account',username:'p03r01_forwarded',password:ownerPassword,
      revision:revision,csrfmiddlewaretoken:csrf});
    const response=await fetch('/users',{method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded','X-Forwarded-Host':'evil.example.test','X-Forwarded-Proto':'https'},
      body:body.toString()});
    return {status:response.status,text:await response.text()};
  },{csrf,revision,ownerPassword});
  expect(injected.status).toBe(200);
  const forwardedLink=/data-invitation-link="([^"]+)"/.exec(injected.text);
  expect(forwardedLink).not.toBeNull();
  const parsedForwarded=new URL(forwardedLink[1]);
  const forwardedToken=parsedForwarded.searchParams.get('token');
  handOverToken(forwardedToken);
  observations.forwarded_origin=parsedForwarded.origin;
  observations.forwarded_path=parsedForwarded.pathname;
  observations.forwarded_has_token=!!forwardedToken&&forwardedToken.length>20;
  expect(forwardedLink[1].includes('evil.example.test')).toBe(false);
  expect(parsedForwarded.origin).toBe(new URL(base).origin);
  expect(!!forwardedToken&&JSON.stringify(observations).includes(forwardedToken)).toBe(false);

  // ---- upload rejections stay on the page and name the real reason ----
  await page.goto(base+'/studies/'+studyId+'/builds');
  await expect(page.locator('#package-upload')).toBeVisible();
  const buildsUrl=page.url();
  await page.locator('#package-upload input[name=package]').setInputFiles(process.env.GEP_BROKEN_PACKAGE);
  await page.locator('#package-upload').getByRole('button',{name:'上传并验证'}).click();
  await expect(page.locator('#upload-status')).toContainText('不是有效的 ZIP');
  observations.bad_zip_reason=await page.locator('#upload-status').innerText();
  expect(page.url()).toBe(buildsUrl);
  expect(await page.locator('#package-upload input[name=package]').evaluate(el=>el.files.length)).toBe(1);

  await page.locator('#package-upload input[name=package]').setInputFiles(process.env.GEP_TAMPERED_PACKAGE);
  await page.locator('#package-upload').getByRole('button',{name:'上传并验证'}).click();
  await expect(page.locator('#upload-status')).toContainText('程序摘要与描述不一致');
  observations.tampered_reason=await page.locator('#upload-status').innerText();
  expect(page.url()).toBe(buildsUrl);

  // ---- a valid upload is accepted, then approved at the builds anchor ----
  await page.locator('#package-upload input[name=package]').setInputFiles(process.env.GEP_WEB_PACKAGE);
  await page.locator('#package-upload').getByRole('button',{name:'上传并验证'}).click();
  await expect(page.locator('[data-builds-list]')).toContainText(process.env.GEP_WEB_VERSION,{timeout:20000});
  const buildRow=page.locator('[data-build-row]').filter({hasText:process.env.GEP_WEB_VERSION});
  await buildRow.getByRole('button',{name:'批准合成发行'}).click();
  await page.waitForURL(url=>url.hash.startsWith('#release-'),{timeout:20000});
  observations.approve_url=page.url();
  observations.new_release_id=page.url().split('#release-')[1];
  observations.old_release_id=process.env.GEP_OLD_RELEASE;
  await expect(page.locator('#release-'+observations.new_release_id)).toBeVisible();
  await expect(page.locator('#release-'+observations.new_release_id)).toContainText(process.env.GEP_WEB_VERSION);
  await expect(page.locator('#release-'+process.env.GEP_OLD_RELEASE)).toBeVisible();

  observations.page_errors=errors;
  expect(errors).toEqual([]);
  console.log('P03R01_OBSERVATIONS '+JSON.stringify(observations));
} finally {await browser.close();}
'''
