"""Bounded first-run checks using real initializer/database and WSGI requests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from gep.deployment import setup_values


@pytest.mark.parametrize('change', [
    {'password': 'weak', 'password_confirm': 'weak'},
    {'password_confirm': 'different'},
    {'domain': 'https://example.com/path'},
    {'domain': '127.0.0.1'},
    {'username': 'not a username'},
])
def test_setup_validation(change):
    values = {'username': 'synthetic_owner', 'password': 'Synthetic1!', 'password_confirm': 'Synthetic1!', 'domain': ''}
    with pytest.raises(ValueError):
        setup_values({**values, **change})


@pytest.mark.parametrize('domain', ['', 'Example.COM'])
def test_first_run_real_initialization(tmp_path, domain):
    root = tmp_path / 'volume'
    root.mkdir()
    expected = str(uuid.uuid4())
    env = {**os.environ, 'PYTHONPATH': str(Path('server').resolve()), 'GEP_DATA_DIR': str(root),
           'GEP_EXPECTED_INSTANCE': expected, 'GEP_SETUP_ENABLED': '1', 'GEP_LOCAL_PORT': '8080',
           'GEP_ADMIN_HOST': 'admin.localhost', 'SYNTHETIC_DOMAIN': domain}
    # Isolate Django's process-global settings from pytest-django. All assertions
    # below run against a real initialized SQLite volume, not a mocked service.
    script = r'''
import io, json, os, sqlite3
from urllib.parse import urlencode
from wsgiref.util import setup_testing_defaults
from gep import docker_wsgi as setup

def call(method='GET', path='/setup', data=None, cookie='', host='admin.localhost:8080', origin=None):
    env={};setup_testing_defaults(env)
    raw=urlencode(data or {}).encode()
    env.update(REQUEST_METHOD=method,PATH_INFO=path,HTTP_HOST=host,HTTP_COOKIE=cookie,
               CONTENT_TYPE='application/x-www-form-urlencoded',CONTENT_LENGTH=str(len(raw)))
    env['wsgi.input']=io.BytesIO(raw)
    if origin is not None:env['HTTP_ORIGIN']=origin
    response={}
    def start(status,headers):response.update(status=status,headers=dict(headers))
    response['body']=b''.join(setup.application(env,start)).decode()
    return response

assert call(host='experiment.localhost:8080')['status'].startswith('403')
assert call(path='/login')['headers']['Location']=='/setup'
first=call(); assert first['status']=='200 OK' and '首次设置' in first['body']
cookie=first['headers']['Set-Cookie'].split(';')[0]
values={'csrf':setup.CSRF,'username':'setup_owner','password':'Synthetic1!','password_confirm':'Synthetic1!','domain':os.environ['SYNTHETIC_DOMAIN']}
assert call('POST',data=values)['status'].startswith('403')
assert call('POST',data=values,cookie=cookie,origin='https://evil.example')['status'].startswith('403')
bad=call('POST',data={**values,'password_confirm':'bad'},cookie=cookie)
assert bad['status'].startswith('400') and '不一致' in bad['body']
assert values['password'] not in bad['body']
assert not list(setup.ROOT.iterdir())
done=call('POST',data=values,cookie=cookie,origin='http://admin.localhost:8080')
assert done['status']=='201 Created',done['body']
assert values['password'] not in done['body']
assert setup.ROOT.joinpath('instance').read_text()==os.environ['GEP_EXPECTED_INSTANCE']
assert not setup.ROOT.joinpath('dev_credentials.json').exists()
config=json.loads(setup.ROOT.joinpath('deployment.json').read_text())
assert config['mode']=='local' and config['admin_url']=='http://admin.localhost:8080'
if values['domain']:
    assert config['public_admin_url']=='https://admin.example.com'
    assert config['domain_status']=='pending'
    assert '尚未启用公网' in done['body']
    assert 'experiment.example.com' in setup.ROOT.joinpath('Caddyfile.pending').read_text()
    assert 'GEP_HTTPS=1' in setup.ROOT.joinpath('domain.env.pending').read_text()
else:
    assert config['domain_status']=='not_configured'
    assert not setup.ROOT.joinpath('Caddyfile.pending').exists()
assert call('POST',data=values,cookie=cookie)['status'].startswith('409')
assert call()['headers']['Location']=='/login'
assert call(path='/login')['status']=='200 OK'
from django.contrib.auth import get_user_model
from core.models import Instance
owner=get_user_model().objects.get(username='setup_owner')
assert owner.check_password(values['password'])
assert Instance.objects.get(pk=1).owner_id==owner.pk
assert get_user_model().objects.count()==1
print('first-run inputs, CSRF, real Owner, inactive domain configuration, closed setup, login: PASS')
'''
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout
    assert 'Synthetic1!' not in result.stdout + result.stderr
    # A new process (container restart) still uses the same instance and Owner.
    restart = subprocess.run([sys.executable, '-c',
        "from gep.docker_wsgi import READY,real_app;assert READY;real_app();"
        "from django.contrib.auth import get_user_model;assert get_user_model().objects.count()==1"],
        env=env, capture_output=True, text=True)
    assert restart.returncode == 0, restart.stderr
    # A partial volume may not turn back into an unclaimed setup page.
    (root / 'initializing').write_text('')
    partial = subprocess.run([sys.executable, '-c', 'import gep.docker_wsgi'], env=env, capture_output=True, text=True)
    assert partial.returncode != 0 and 'initialized volume' in partial.stderr


def test_old_volume_remains_usable_without_setup(tmp_path):
    env = {**os.environ, 'PYTHONPATH': str(Path('server').resolve()), 'GEP_DATA_DIR': str(tmp_path)}
    first = subprocess.run([sys.executable, '-m', 'gep.initialize', '--data-dir', str(tmp_path)],
                           env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    env.update(GEP_EXPECTED_INSTANCE=(tmp_path / 'instance').read_text(), GEP_SETUP_ENABLED='0')
    before = (tmp_path / 'dev_credentials.json').read_bytes()
    run = subprocess.run([sys.executable, '-c',
        'from gep.docker_wsgi import READY,real_app;assert READY;real_app()'], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / 'dev_credentials.json').read_bytes() == before
