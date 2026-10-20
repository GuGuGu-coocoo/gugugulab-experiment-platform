import os
from pathlib import Path
import subprocess
import sys

import pytest
from django.test import Client


def test_proxy_trust_is_opt_in():
    for https, trust, enabled in [('0', '0', False), ('1', '0', False), ('0', '1', False), ('1', '1', True)]:
        env = {**os.environ, 'PYTHONPATH': str(Path('server').resolve()), 'GEP_HTTPS': https, 'GEP_TRUST_PROXY': trust}
        code = ('import gep.settings as s;'
                f'assert bool(s.SECURE_PROXY_SSL_HEADER) is {enabled!r};'
                'assert not getattr(s,"USE_X_FORWARDED_HOST",False)')
        result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


@pytest.mark.django_db
def test_https_proxy_login_keeps_csrf_and_cookie_boundaries(setup, settings):
    settings.ADMIN_HOST = 'admin.example.com'
    settings.ALLOWED_HOSTS = ['admin.example.com', 'experiment.example.com']
    settings.SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    settings.SESSION_COOKIE_SECURE = settings.CSRF_COOKIE_SECURE = True
    client = Client(enforce_csrf_checks=True)
    headers = {'HTTP_HOST': 'admin.example.com', 'HTTP_X_FORWARDED_PROTO': 'https'}
    login = client.get('/login', **headers)
    assert login.status_code == 200
    assert login.cookies['csrftoken']['secure']
    form = {'username': setup['owner'].username, 'password': 'synthetic-test-password',
            'csrfmiddlewaretoken': client.cookies['csrftoken'].value}
    cross_origin = client.post('/login', form, HTTP_ORIGIN='https://experiment.example.com', **headers)
    assert cross_origin.status_code == 403
    allowed = client.post('/login', form, HTTP_ORIGIN='https://admin.example.com', **headers)
    assert allowed.status_code == 302 and allowed['Location'] == '/'
    assert allowed.cookies['gep_admin']['secure']
    assert not allowed.cookies['gep_admin']['domain']
    assert client.get('/', **headers).status_code == 200
