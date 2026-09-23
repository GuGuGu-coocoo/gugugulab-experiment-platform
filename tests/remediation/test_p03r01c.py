"""P03R01C evidence: origin port, one complete link, preflight and redaction.

Fixed synthetic inputs and independently stated expectations prove:

- an untrusted ``Host`` port can never override the server-owned ``SERVER_PORT``
  used for the derived admin origin, while an explicitly configured
  ``ADMIN_ORIGIN`` wins and keeps its own scheme/port;
- malformed configurations (scheme, credentials, path/query/fragment, empty
  host, invalid/out-of-range/empty port, ``urlparse`` and ``parsed.port``
  errors) are controlled 409 rejections that create no invitation and print no
  link;
- a bulk users import validates the origin before it commits: an invalid
  configuration creates no account or invitation, consumes no preview, and the
  same preview still commits once the configuration is valid;
- the single invitation and the bulk import each render exactly one complete
  clickable ``data-invitation-link`` and no relative duplicate; the complete
  link activates the invitee and a replay is refused;
- the actual minted tokens never appear in the new evidence files, in captured
  output or in browser subprocess stdout/stderr.

The instance fixture stays at the legacy v1 authorization semantics that
``access.authorization_version`` derives today (the R02B storage field does not
exist yet); this task touches no permission, role or password rule.

The opt-in real-Chrome check (``GEP_T17_BROWSER=1``, the Required Verification
switch) proves the one-link UI and the activation in an actual browser and hands
its real token back through an anonymous pipe for the independent scan.
"""
import io
import json
import os
import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.test.utils import override_settings
from openpyxl import Workbook

from core import access, excel
from core.models import AccountInvitation, Grant, Instance, Study

OWNER_PASSWORD = 'synthetic-p03r01c-owner-password'
INVITEE_PASSWORD = 'synthetic-p03r01c-invitee-password'
LINK_RE = re.compile(r'data-invitation-link="([^"]+)"')
RELATIVE_LINK_RE = re.compile(r'<code>/activate-account\?token=')
PREVIEW_RE = re.compile(r'(?:name="preview_id" value="|data-preview-id=")([0-9a-fA-F-]{36})')
TOKEN_PARAM_RE = re.compile(r'token=[A-Za-z0-9_\-]+')


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


def users_workbook(*rows):
    book = Workbook()
    sheet = book.active
    sheet.title = 'data'
    sheet.append(list(excel.USERS_HEADERS))
    for row in rows:
        sheet.append(list(row))
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


@pytest.fixture
def world(db):
    """Owner plus one synthetic study; explicit legacy v1 authorization."""
    owner = get_user_model().objects.create_user('p03r01c_owner', password=OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    assert access.authorization_version(instance) == 1
    study = Study.objects.create(title='P03R01C 合成研究', mode='id', recruitment='open', max_sessions=5)
    for action in ('study.view', 'study.configure'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    return {'owner': owner, 'instance': instance, 'study': study}


def owner_client(world):
    client = Client()
    client.force_login(world['owner'])
    return client


def invite(client, world, username, host='admin.localhost:9999', server_port='8123'):
    data = {'op': 'invite_account', 'username': username, 'password': OWNER_PASSWORD,
            'revision': str(Instance.objects.get(pk=1).governance_revision)}
    return client.post('/users', data, HTTP_HOST=host, SERVER_PORT=server_port)


def upload_users(client, raw, **headers):
    upload = SimpleUploadedFile('users.xlsx', raw,
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return client.post('/users', {'op': 'import_users_preview', 'file': upload}, **headers)


def preview_of(response):
    match = PREVIEW_RE.search(response.content.decode())
    assert match is not None, response.content[:400]
    return match.group(1)


def commit_users(client, preview, password=OWNER_PASSWORD, **headers):
    return client.post('/users', {'op': 'import_users_commit', 'preview_id': preview,
                                  'password': password}, **headers)


def test_derived_origin_uses_server_port_not_the_host_port(world):
    client = owner_client(world)

    # Host port 9999 cannot override SERVER_PORT 8123; the link still opens the
    # controlled activation page with the real token.
    response = invite(client, world, 'p03r01c_derived')
    assert response.status_code == 200
    link = link_of(response)
    assert link.startswith('http://admin.localhost:8123/activate-account?token=')
    assert ':9999' not in link
    token = token_of(link)
    page = client.get(f'/activate-account?token={token}', HTTP_HOST='admin.localhost:8123', SERVER_PORT='8123')
    assert page.status_code == 200 and '接受账号邀请' in page.content.decode()

    # A Host outside the approved admin entries is refused before any link or
    # invitation exists, whatever port it claims.
    rejected = invite(client, world, 'p03r01c_wrong_host', host='experiment.localhost:9999', server_port='8123')
    assert rejected.status_code == 403
    assert LINK_RE.search(rejected.content.decode()) is None
    assert not AccountInvitation.objects.filter(username='p03r01c_wrong_host').exists()

    # An unusable server-owned port fails closed instead of producing a broken
    # link: nothing is created and the page states the configuration error.
    for index, port in enumerate(('0', '65536', 'not-a-port', '')):
        username = f'p03r01c_bad_port_{index}'
        response = invite(client, world, username, host='admin.localhost:9999', server_port=port)
        assert response.status_code == 409, (port, response.status_code)
        assert '管理地址配置无效' in response.content.decode()
        assert LINK_RE.search(response.content.decode()) is None
        assert not AccountInvitation.objects.filter(username=username).exists()


def test_explicit_origin_wins_over_host_and_server_ports(world):
    client = owner_client(world)
    cases = (('http://admin.localhost:8123', 'http://admin.localhost:8123/'),
             ('https://localhost:9443', 'https://localhost:9443/'),
             ('https://admin.localhost', 'https://admin.localhost/'))
    for index, (configured, expected) in enumerate(cases):
        username = f'p03r01c_configured_{index}'
        with override_settings(ADMIN_ORIGIN=configured):
            response = invite(client, world, username, host='admin.localhost:9999', server_port='9999')
        assert response.status_code == 200, (configured, response.status_code)
        link = link_of(response)
        assert link.startswith(expected + 'activate-account?token='), (configured, link)
        assert ':9999' not in link
        assert AccountInvitation.objects.filter(username=username, consumed=False).count() == 1


def test_malformed_configuration_is_a_controlled_rejection(world):
    client = owner_client(world)
    broken = ('ftp://admin.localhost:8123', 'https://evil.example.test',
              'http://user@admin.localhost:8123', 'http://user:pass@admin.localhost:8123',
              'http://admin.localhost:8123/extra', 'http://admin.localhost:8123?q=1',
              'http://admin.localhost:8123#frag', 'http://admin.localhost:0',
              'http://admin.localhost:65536', 'http://admin.localhost:notaport',
              'http://admin.localhost:', 'http://', 'http://:8123', 'http://[::1')
    for index, value in enumerate(broken):
        username = f'p03r01c_broken_{index}'
        with override_settings(ADMIN_ORIGIN=value):
            response = invite(client, world, username)
        assert response.status_code == 409, (value, response.status_code)
        assert '管理地址配置无效' in response.content.decode(), value
        assert LINK_RE.search(response.content.decode()) is None, value
        assert not AccountInvitation.objects.filter(username=username).exists(), value


def test_bulk_commit_validates_origin_before_consuming_the_preview(world):
    client = owner_client(world)
    response = upload_users(client, users_workbook(('p03r01c_bulk', 'create', '', 'user', '', '')))
    assert response.status_code == 200
    preview = preview_of(response)

    # Invalid origin: no invitation, no account, no preview consumption.
    with override_settings(ADMIN_ORIGIN='http://admin.localhost:notaport'):
        failed = commit_users(client, preview)
    assert failed.status_code == 409
    assert '管理地址配置无效' in failed.content.decode()
    assert LINK_RE.search(failed.content.decode()) is None
    assert not AccountInvitation.objects.filter(username='p03r01c_bulk').exists()
    assert not get_user_model().objects.filter(username='p03r01c_bulk').exists()

    # The same preview still commits once the configuration is valid.
    with override_settings(ADMIN_ORIGIN='http://admin.localhost:8123'):
        committed = commit_users(client, preview)
    assert committed.status_code == 200
    body = committed.content.decode()
    assert body.count('data-invitation-link=') == 1
    assert RELATIVE_LINK_RE.search(body) is None
    link = link_of(committed)
    assert link.startswith('http://admin.localhost:8123/activate-account?token=')
    assert AccountInvitation.objects.filter(username='p03r01c_bulk', consumed=False).count() == 1

    # Replaying the committed preview is idempotent and shows no token again.
    replay = commit_users(client, preview)
    assert replay.status_code == 200
    assert AccountInvitation.objects.filter(username='p03r01c_bulk').count() == 1
    assert 'data-invitation-link=' not in replay.content.decode()


def test_actual_tokens_never_reach_new_evidence_or_captured_output(world, evidence, capfd):
    client = owner_client(world)
    single = invite(client, world, 'p03r01c_redact_single')
    assert single.status_code == 200
    single_token = token_of(link_of(single))

    preview = preview_of(upload_users(client, users_workbook(('p03r01c_redact_bulk', 'create', '', 'user', '', ''))))
    bulk = commit_users(client, preview)
    assert bulk.status_code == 200
    bulk_token = token_of(link_of(bulk))

    written = evidence('redaction.json', {
        'single': redacted_link(link_of(single)),
        'bulk': redacted_link(link_of(bulk)),
        'statuses': {'single': single.status_code, 'bulk': bulk.status_code},
        'shown_once': True})
    saved = written.read_text(encoding='utf-8')
    captured = capfd.readouterr()
    assert 'token=' not in saved
    for value in (single_token, bulk_token):
        assert value not in saved
        assert value not in captured.out and value not in captured.err

    # The tokens stay usable for the real activation (memory only), and the
    # replay of a consumed token is still refused.
    page = client.get(f'/activate-account?token={single_token}', HTTP_HOST='admin.localhost:8123', SERVER_PORT='8123')
    assert page.status_code == 200
    activated = client.post('/activate-account',
                            {'token': single_token, 'password': INVITEE_PASSWORD, 'confirm': INVITEE_PASSWORD},
                            HTTP_HOST='admin.localhost:8123', SERVER_PORT='8123')
    assert activated.status_code == 200 and '账号已激活' in activated.content.decode()
    replay = client.post('/activate-account',
                         {'token': single_token, 'password': INVITEE_PASSWORD, 'confirm': INVITEE_PASSWORD},
                         HTTP_HOST='admin.localhost:8123', SERVER_PORT='8123')
    assert replay.status_code == 403 and '激活失败' in replay.content.decode()
    assert AccountInvitation.objects.get(username='p03r01c_redact_single').consumed is True


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')
def test_actual_chrome_single_link_and_redacted_output(live_server, world, evidence, run_chrome_tokens):
    env = dict(os.environ, GEP_TEST_BASE=live_server.url, GEP_OWNER_PASSWORD=OWNER_PASSWORD)
    result, reported = run_chrome_tokens(SCRIPT, env, timeout=120)
    assert result.returncode == 0, redact_text(result.stdout + result.stderr)
    observations = json.loads(re.search(r'P03R01C_OBSERVATIONS (\{.*\})', result.stdout).group(1))
    tokens = [line.strip() for line in reported]
    assert len(tokens) == 1 and len(tokens[0]) > 20, \
        'the browser journey must hand over its real token through the memory channel'

    base = urlparse(live_server.url)
    assert observations['origin'] == f'{base.scheme}://{base.hostname}:{base.port}'
    assert observations['path'] == '/activate-account'
    assert observations['port'] == str(base.port)
    assert observations['port'] not in ('', '80', '443')
    assert observations['link_count'] == 1 and observations['relative_code_count'] == 0
    assert observations['has_token'] is True
    assert observations['activation_status'] == 200 and observations['activated'] is True
    assert observations['page_errors'] == []

    # Redaction: the actual minted token never appears in saved evidence,
    # subprocess output or failure diagnostics.
    written = evidence('chrome_single_link.json', observations)
    saved = written.read_text(encoding='utf-8')
    assert 'token=' not in result.stdout and 'token=' not in result.stderr and 'token=' not in saved
    for value in tokens:
        assert value not in result.stdout and value not in result.stderr
        assert value not in saved

    assert AccountInvitation.objects.filter(username='p03r01c_invitee', consumed=True).count() == 1
    assert get_user_model().objects.filter(username='p03r01c_invitee').exists()


SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
import {writeSync} from 'node:fs';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const tokenFd=Number(process.env.GEP_TOKEN_FD);
const observations={};
const errors=[];
try {
  const owner=await browser.newContext();
  const page=await owner.newPage();
  page.on('pageerror',e=>errors.push('owner:'+e.message));
  const loginResponse=await page.goto(base+'/login');
  expect(loginResponse.status()).toBe(200);
  await page.locator('[name=username]').fill('p03r01c_owner');
  await page.locator('[name=password]').fill(ownerPassword);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/users');
  const inviteForm=page.locator('form').filter({has:page.locator('[name=op][value=invite_account]')});
  await inviteForm.locator('[name=username]').fill('p03r01c_invitee');
  await inviteForm.locator('[name=password]').fill(ownerPassword);
  await inviteForm.getByRole('button',{name:'生成邀请（本人设置密码）'}).click();
  await page.waitForLoadState('load');

  const link=await page.locator('[data-one-time-invitation] a[data-invitation-link]').getAttribute('href');
  const parsed=new URL(link);
  const token=parsed.searchParams.get('token');
  if(tokenFd&&token)writeSync(tokenFd,token+'\n');
  observations.origin=parsed.origin;
  observations.path=parsed.pathname;
  observations.port=parsed.port;
  observations.has_token=!!token&&token.length>20;
  observations.link_count=await page.locator('[data-one-time-invitation] a[data-invitation-link]').count();
  observations.relative_code_count=await page.locator('[data-one-time-invitation] code').count();
  expect(parsed.origin).toBe(new URL(base).origin);
  expect(observations.link_count).toBe(1);
  expect(observations.relative_code_count).toBe(0);
  expect(!!token&&JSON.stringify(observations).includes(token)).toBe(false);
  await owner.close();

  const invitee=await browser.newContext();
  const setPage=await invitee.newPage();
  setPage.on('pageerror',e=>errors.push('invitee:'+e.message));
  const openResponse=await setPage.goto(link);
  expect(openResponse.status()).toBe(200);
  observations.activation_status=openResponse.status();
  await expect(setPage.getByRole('heading',{name:'接受账号邀请'})).toBeVisible();
  await setPage.locator('[name=password]').fill('synthetic-p03r01c-invitee-password');
  await setPage.locator('[name=confirm]').fill('synthetic-p03r01c-invitee-password');
  await setPage.getByRole('button',{name:'激活账号'}).click();
  await expect(setPage.getByText('账号已激活')).toBeVisible();
  observations.activated=true;
  await invitee.close();

  observations.page_errors=errors;
  expect(errors).toEqual([]);
  console.log('P03R01C_OBSERVATIONS '+JSON.stringify(observations));
} finally {await browser.close();}
'''
