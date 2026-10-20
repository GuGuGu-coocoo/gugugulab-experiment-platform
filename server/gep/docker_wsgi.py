"""Docker first-run page, replaced by the real application after initialization.

The setup handler imports no Django settings or database. Initialization runs
once in a child process; passwords travel on stdin, never argv or environment.
Existing and partially initialized volumes are never reset by the web page.
Gunicorn must use one worker (multiple request threads are supported).
"""
from html import escape
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from string import Template
import threading
from urllib.parse import parse_qs
import uuid

from .deployment import domain_config, setup_values

ROOT = Path(os.environ.get('GEP_DATA_DIR', '/data')).resolve()
EXPECTED = str(uuid.UUID(os.environ['GEP_EXPECTED_INSTANCE']))
PORT = int(os.environ.get('GEP_LOCAL_PORT', '8080'))
HOST = os.environ.get('GEP_ADMIN_HOST', 'admin.localhost')
TEMPLATES = Path(__file__).resolve().parents[1] / 'core' / 'templates' / 'core'
LOCK = threading.RLock()
CSRF = secrets.token_urlsafe(32)
APP = None


def volume_ready():
    if not ROOT.is_dir():
        raise RuntimeError('Missing data volume; setup cannot replace it')
    if not any(ROOT.iterdir()):
        if os.environ.get('GEP_SETUP_ENABLED') != '1':
            raise RuntimeError('First-run setup is not enabled')
        return False
    from .runtime import configure
    configure()  # Keeps partial/mismatched volumes fail-closed.
    return True


def real_app():
    global APP
    if APP is None:
        with LOCK:
            if APP is None:
                from .wsgi import application
                APP = application
    return APP


READY = volume_ready()


def reply(start_response, status, body='', extra=(), nonce=''):
    headers = [
        ('Content-Type', 'text/html; charset=utf-8'), ('Cache-Control', 'no-store'),
        ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
        ('Referrer-Policy', 'same-origin'),
        ('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; "
         f"script-src 'nonce-{nonce}'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
        *extra,
    ]
    start_response(status, headers)
    return [body.encode('utf-8')]


def page(start_response, message='', values=None, status='200 OK'):
    values = values or {}
    nonce = secrets.token_urlsafe(18)
    body = Template((TEMPLATES / 'setup.html').read_text()).substitute(
        css=(TEMPLATES / 'theme_css.html').read_text(), csrf=CSRF, nonce=nonce,
        message=escape(message), username=escape(values.get('username', ''), quote=True),
        domain=escape(values.get('domain', ''), quote=True))
    return reply(start_response, status, body, [('Set-Cookie',
        f'gep_setup_csrf={CSRF}; Path=/setup; HttpOnly; SameSite=Strict')], nonce)


def application(environ, start_response):
    global READY
    path = environ.get('PATH_INFO', '/')
    method = environ.get('REQUEST_METHOD', 'GET')
    if READY:
        if path == '/setup':
            if method == 'GET':
                return reply(start_response, '303 See Other', extra=[('Location', '/login')])
            return reply(start_response, '409 Conflict', '首次设置已经完成，请登录后台。')
        return real_app()(environ, start_response)
    # Only the known admin origin can create the first account. No forwarded
    # host is used and participant hosts never receive the setup form.
    authority = environ.get('HTTP_HOST', '')
    allowed = {HOST, f'{HOST}:{PORT}', f'{HOST}:8000'}
    if authority not in allowed:
        return reply(start_response, '403 Forbidden', '请通过本机管理地址进行首次设置。')
    if path in ('/', '/login') and method == 'GET':
        return reply(start_response, '303 See Other', extra=[('Location', '/setup')])
    if path != '/setup':
        return reply(start_response, '404 Not Found', '页面不存在。')
    if method == 'GET':
        return page(start_response)
    if method != 'POST':
        return reply(start_response, '405 Method Not Allowed', extra=[('Allow', 'GET, POST')])
    try:
        length = int(environ.get('CONTENT_LENGTH') or '0')
        if not 0 < length <= 32768:
            return reply(start_response, '413 Content Too Large', '输入内容过长。')
        raw = environ['wsgi.input'].read(length).decode('utf-8')
        values = {key: items[-1] for key, items in parse_qs(raw, keep_blank_values=True, max_num_fields=8).items()}
        cookie = SimpleCookie(environ.get('HTTP_COOKIE', ''))
        token = cookie.get('gep_setup_csrf')
        origin = environ.get('HTTP_ORIGIN')
        if (not token or not secrets.compare_digest(token.value, CSRF)
                or not secrets.compare_digest(values.get('csrf', ''), CSRF)
                or (origin and origin != f'http://{HOST}:{PORT}')):
            return page(start_response, '页面已过期，请刷新后重试。', status='403 Forbidden')
        username, password, domain = setup_values(values)
    except (ValueError, UnicodeError) as error:
        return page(start_response, str(error), locals().get('values'), '400 Bad Request')
    with LOCK:
        if READY:
            return reply(start_response, '409 Conflict', '首次设置已经完成，请登录后台。')
        # Re-check the volume immediately before the exclusive initializer.
        # A failed attempt leaves its data intact and is not retried silently.
        if volume_ready():
            return reply(start_response, '409 Conflict', '实例已存在，请重启服务后登录。')
        result = subprocess.run([
            sys.executable, '-m', 'gep.initialize', '--data-dir', str(ROOT), '--instance', EXPECTED, '--setup-stdin',
        ], input=json.dumps({'username': username, 'password': password, 'password_confirm': password, 'domain': domain}),
            capture_output=True, text=True)
        if result.returncode:
            return reply(start_response, '500 Internal Server Error',
                         '设置未完成。现有数据已保留，请联系维护者检查；不要删除数据卷或反复初始化。')
        READY = True
    config = domain_config(domain, PORT)
    domain_note = '' if not domain else (
        '<p>域名配置已保存，尚未启用公网。</p><p>管理后台：' + escape(config['public_admin_url'])
        + '<br>参与门户：' + escape(config['public_participant_url']) + '</p>')
    body = ('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>设置完成 · GEP</title><style>' + (TEMPLATES / 'theme_css.html').read_text()
            + '</style><body><main class="main login-page"><section class="login-card"><h1>设置完成</h1>'
            '<p>Owner 账号已创建，请使用刚才设置的账号和密码登录。</p>' + domain_note
            + '<a class="button" href="/login">进入管理后台</a></section></main></body></html>')
    return reply(start_response, '201 Created', body,
                 [('Set-Cookie', 'gep_setup_csrf=; Path=/setup; Max-Age=0; HttpOnly; SameSite=Strict')])
