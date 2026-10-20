"""Small synthetic checks for the browser boundary; never use the live instance."""
import os
import re
import subprocess
import zipfile
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory

from core.middleware import ResponseSecurity
from core.models import AccountProfile, Grant, Throttle
from core.throttle import check


def test_nonce_policy_and_host_only_cookies(setup):
    client = Client()
    first = client.get('/login')
    second = client.get('/login')
    nonce = re.search(r"'nonce-([^']+)'", first['Content-Security-Policy']).group(1)
    assert nonce not in second['Content-Security-Policy']
    assert first['X-Frame-Options'] == 'DENY'
    assert first['Referrer-Policy'] == 'same-origin'
    assert first['X-Content-Type-Options'] == 'nosniff'
    assert first['Cache-Control'] == 'no-store'
    assert "script-src 'self' 'nonce-" in first['Content-Security-Policy']
    assert "'unsafe-inline'" not in first['Content-Security-Policy'].split('script-src ')[1].split(';')[0]
    assert "form-action 'self'" in first['Content-Security-Policy']
    assert client.cookies['csrftoken']['domain'] == ''
    assert client.cookies['csrftoken']['samesite'] == 'Strict'
    assert client.post('/login', {'username': 'synthetic_owner', 'password': 'synthetic-test-password'}).status_code == 302
    response = client.get('/users')
    nonce = re.search(r"'nonce-([^']+)'", response['Content-Security-Policy']).group(1)
    assert f'<script nonce="{nonce}">' in response.content.decode()
    assert client.cookies['gep_admin']['httponly']
    assert client.cookies['gep_admin']['domain'] == ''


def test_early_body_and_csrf_refusals_also_have_headers(setup):
    client = Client(enforce_csrf_checks=True)
    responses = [client.post('/login', {'username': 'synthetic_owner', 'password': 'invalid'}),
                 client.generic('POST', '/login', b'{}', content_type='application/json', CONTENT_LENGTH='262145'),
                 client.get('/not-a-route'), client.get('/login', HTTP_HOST='unapproved.invalid')]
    assert [response.status_code for response in responses] == [403, 413, 404, 400]
    for response in responses:
        assert response['Cache-Control'] == 'no-store'
        assert response['Referrer-Policy'] == 'same-origin'
        assert response['X-Content-Type-Options'] == 'nosniff'
        assert response['X-Frame-Options'] == 'DENY'


def test_published_game_csp_and_static_caching_unchanged(setup, settings, tmp_path):
    settings.DATA_DIR = tmp_path
    (tmp_path / 'packages').mkdir()
    with zipfile.ZipFile(tmp_path / 'packages/game.zip', 'w') as archive:
        archive.writestr('web/index.html', '<head></head><body>synthetic</body>')
    build = setup['release'].build
    build.descriptor['platform'] = 'godot_web'
    build.package_path = 'game.zip'
    build.save()
    response = Client().get(f"/run/{setup['release'].id}/web/index.html", HTTP_HOST='experiment.localhost')
    assert response.status_code == 200
    assert "'wasm-unsafe-eval'" in response['Content-Security-Policy']
    assert "'unsafe-inline'" in response['Content-Security-Policy']
    assert "'nonce-" not in response['Content-Security-Policy']
    assert b'globalThis.GEP_CONTEXT' in response.content
    static = Client().get('/static/core/permissions.js')
    assert static.status_code == 200 and static['Cache-Control'] == 'no-cache'
    assert 'Content-Security-Policy' not in static
    static.close()


def test_header_layer_adds_no_database_access_or_changes_to_body(db, django_assert_num_queries):
    request = RequestFactory().get('/v1/participant/sessions/example/status')
    expected = b'{"state":"complete"}'
    with django_assert_num_queries(0):
        response = ResponseSecurity(lambda request: HttpResponse(expected, content_type='application/json'))(request)
    assert response.content == expected and request.csp_nonce == ''


def test_login_limits_cannot_be_bypassed_by_username_or_forwarded_ip(setup, monkeypatch):
    monkeypatch.setattr('core.throttle.time.time', lambda: 1800000000)
    for _ in range(120):
        check('login-client:127.0.0.1', limit=120)
    # The source address is the direct peer, not a caller-supplied XFF header.
    client = Client()
    before = Throttle.objects.count()
    response = client.post('/login', {'username': 'a-different-account', 'password': 'not-used'},
                           HTTP_X_FORWARDED_FOR='192.0.2.123')
    assert response.status_code == 429 and response['Retry-After'] == '60'
    assert '请求过于频繁' in response.content.decode() and response['Content-Type'].startswith('text/html')
    assert '_auth_user_id' not in client.session
    assert Throttle.objects.count() == before
    # Another direct peer is independent; the original per-account limit stays.
    for _ in range(20):
        check('login:192.0.2.5:synthetic_owner')
    response = client.post('/login', {'username': 'synthetic_owner', 'password': 'not-used'}, REMOTE_ADDR='192.0.2.5')
    assert response.status_code == 429
    monkeypatch.setattr('core.throttle.time.time', lambda: 1800000060)
    response = client.post('/login', {'username': 'synthetic_owner', 'password': 'synthetic-test-password'})
    assert response.status_code == 302


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1', reason='Opt-in real Chrome check')
def test_chrome_csp_blocks_unapproved_script_and_allows_existing_ui(live_server, setup, tmp_path):
    setup['instance'].authorization_version = 2
    setup['instance'].save(update_fields=['authorization_version'])
    AccountProfile.objects.create(user=setup['owner'])
    target = get_user_model().objects.create_user('synthetic_security_member')
    AccountProfile.objects.create(user=target, study_overrides={str(setup['study'].id): []})
    for action in ('study.view', 'study.configure', 'recruitment.manage', 'data.export_raw'):
        Grant.objects.create(user=setup['owner'], study=setup['study'], action=action)
    script = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=process.env.GEP_TEST_BASE;
 await page.goto(base+'/login');
 await page.locator('[name=username]').fill('synthetic_owner');
 await page.locator('[name=password]').fill('synthetic-test-password');
 await page.locator('button').click();await page.waitForURL(base+'/');
 await page.goto(base+'/users');
 expect(await page.locator('script[src="/static/core/permissions.js"]').count()).toBe(1);
 await page.evaluate(()=>{window.__blocked=false;const script=document.createElement('script');script.textContent='window.__blocked=true';document.body.append(script);});
 expect(await page.evaluate(()=>window.__blocked)).toBe(false);
 const form=page.locator('[data-matrix-entry="'+process.env.GEP_TEST_TARGET+'-'+process.env.GEP_TEST_STUDY+'"]');
 await form.locator('[name=visibility]').check();
 await form.locator('summary').click();
 await form.locator('[name="action:audit.view"]').check();
 await page.locator('[data-save-permissions-button]').click();
 await page.locator('[data-confirm-dialog] [name=password]').fill('synthetic-test-password');
 await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&r.url()===base+'/users'),page.locator('[data-confirm-submit]').click()]);
 await expect(page.locator('.notice')).toContainText('已保存 1 项权限修改');
 await page.goto(base+'/studies/'+process.env.GEP_TEST_STUDY+'/recruitment');
 await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/studies/')),page.locator('[name=state]').selectOption('paused')]);
 await expect(page.locator('[name=state]')).toHaveValue('paused');
 await page.reload();await expect(page.locator('[name=state]')).toHaveValue('paused');
 await page.screenshot({path:process.env.GEP_TEST_SCREENSHOT});
 expect(errors).toEqual([]);
 console.log('Chrome: injected script blocked; nonce scripts, login, v2 permission save and immediate recruitment remain working.');
} finally {await browser.close();}
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'GEP_TEST_BASE': live_server.url, 'GEP_TEST_STUDY': str(setup['study'].id),
                                 'GEP_TEST_SCREENSHOT': str(tmp_path / 'security.png'), 'GEP_TEST_TARGET': str(target.pk)},
                            text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    profile = AccountProfile.objects.get(user=target)
    assert sorted(profile.study_overrides[str(setup['study'].id)]) == ['audit.view', 'study.view']
    from core.access import allowed
    assert allowed(target, setup['study'], 'audit.view')
    assert not allowed(target, setup['study'], 'data.export_raw')
