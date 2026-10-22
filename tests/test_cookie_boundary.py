"""Real Chrome cookie precedence plus real Django synthetic authentication.

HTTPS pages are fulfilled inside a new browser context, without an external
network, a certificate bypass or changing any machine trust store.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core.models import Grant, Study

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1', reason='Opt-in Chrome cookie boundary')
def test_https_child_origin_cannot_replace_the_admin_session(setup, settings):
    env = {**os.environ,'PYTHONPATH':str(ROOT/'server'),'GEP_HTTPS':'1'}
    probe = ('import json; import gep.settings as s; print(json.dumps({'
             '"session":s.SESSION_COOKIE_NAME,"csrf":getattr(s,"CSRF_COOKIE_NAME","csrftoken"),'
             '"secure":s.SESSION_COOKIE_SECURE}))')
    configuration = subprocess.run([sys.executable,'-c',probe], env=env,
                                  capture_output=True,text=True,check=True)
    cookies = json.loads(configuration.stdout)
    settings.SESSION_COOKIE_NAME = cookies['session']
    settings.CSRF_COOKIE_NAME = cookies['csrf']
    settings.SESSION_COOKIE_SECURE = settings.CSRF_COOKIE_SECURE = cookies['secure']
    settings.ADMIN_HOST = 'admin.audit.localhost'
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS,settings.ADMIN_HOST]
    attacker = get_user_model().objects.create_user('synthetic_cookie_attacker')
    other = Study.objects.create(title='Synthetic attacker-only study')
    Grant.objects.create(user=attacker,study=other,action='study.view')
    Grant.objects.create(user=setup['owner'],study=setup['study'],action='study.view')
    victim_client, attacker_client = Client(), Client()
    victim_client.force_login(setup['owner'])
    attacker_client.force_login(attacker)
    script = r'''
import {chromium} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
 const context=await browser.newContext();const page=await context.newPage();
 const name=process.env.GEP_COOKIE_NAME;
 await context.addCookies([{name,value:process.env.GEP_VICTIM_COOKIE,url:'https://admin.audit.localhost/',secure:true,httpOnly:true,sameSite:'Strict'}]);
 await page.route('https://**.audit.localhost/**',route=>route.fulfill({contentType:'text/html',body:'<body>synthetic cookie boundary</body>'}));
 await page.goto('https://r-synthetic.experiment.audit.localhost/');
 await page.evaluate(({name,value})=>{document.cookie=name+'='+value+'; Domain=audit.localhost; Path=/; Secure; SameSite=Strict';},
   {name,value:process.env.GEP_ATTACKER_COOKIE});
 let header='';
 await page.route('https://admin.audit.localhost/**',async route=>{header=(await route.request().allHeaders())['cookie']??'';return route.fulfill({contentType:'text/html',body:'<body>synthetic target</body>'});});
 await page.goto('https://admin.audit.localhost/');
 // In-memory handoff only: the test never prints/persists these synthetic IDs.
 process.stdout.write(JSON.stringify({cookie:header}));
} finally {await browser.close();}
'''
    result = subprocess.run(['node','--input-type=module','-e',script],cwd=ROOT,
        env={**os.environ,'GEP_COOKIE_NAME':cookies['session'],
             'GEP_VICTIM_COOKIE':victim_client.cookies[cookies['session']].value,
             'GEP_ATTACKER_COOKIE':attacker_client.cookies[cookies['session']].value},
        capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, 'The isolated cookie browser did not complete'
    header = json.loads(result.stdout)['cookie']
    response = Client().get('/', HTTP_HOST=settings.ADMIN_HOST, HTTP_COOKIE=header)
    evidence = {'victim_active':response.wsgi_request.user.pk == setup['owner'].pk,
                'attacker_active':response.wsgi_request.user.pk == attacker.pk}
    print(json.dumps(evidence))
    assert evidence == {'victim_active':True,'attacker_active':False}, 'A child-origin cookie replaced the researcher account'
    assert cookies['session'].startswith('__Host-') and cookies['csrf'].startswith('__Host-')
    response = Client().get('/login',HTTP_HOST=settings.ADMIN_HOST,secure=True)
    csrf = response.cookies[cookies['csrf']]
    assert csrf['secure'] and csrf['domain'] == '' and csrf['path'] == '/'
