"""Synthetic release-origin, CORS and legacy-queue security regression checks."""
import copy
import json
import os
from pathlib import Path
import subprocess
import uuid
import zipfile

import pytest
from django.core import signing
from django.test import Client

from core.gui import connection_config
from core.models import BrowserOrigin, Build, Event, Grant, Release, Session, Study
from core.origins import isolated_host, isolated_origin, release_url, remember

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def web(setup, settings, tmp_path, monkeypatch):
    settings.DATA_DIR = tmp_path
    monkeypatch.setenv('GEP_PUBLIC_API', 'http://experiment.localhost:8123')
    (tmp_path / 'packages').mkdir()
    with zipfile.ZipFile(tmp_path / 'packages/audit.zip', 'w') as archive:
        archive.writestr('web/index.html', '<html><head></head><body>synthetic isolated program</body></html>')
        archive.writestr('web/gec/sdk.js', (ROOT / 'GEC_SDK/gec_web/sdk.js').read_bytes())
    build = setup['release'].build
    build.descriptor['platform'] = 'godot_web'
    build.package_path = 'audit.zip'
    build.save()
    other_study = Study.objects.create(title='Unrelated synthetic B', recruitment='open')
    other_build = Build.objects.create(study=other_study, descriptor=build.descriptor,
                                      digest='b' * 64, package_path='audit.zip')
    other = Release.objects.create(study=other_study, build=other_build, approved=True,
                                   config={'purpose':'synthetic'})
    return {**setup, 'other':other, 'other_build':other_build}


def test_only_bound_release_and_preview_origins_serve_uploaded_code(web):
    release = web['release']
    client = Client()
    url = f'/run/{release.id}/web/index.html'
    legacy = client.get(url, HTTP_HOST='experiment.localhost')
    assert legacy.status_code == 200 and b'data-legacy-queue' in legacy.content
    assert b'synthetic isolated program' not in legacy.content and b'GEP_CONTEXT' not in legacy.content
    correct = client.get(url, HTTP_HOST=isolated_host('r',release.id))
    assert correct.status_code == 200 and b'GEP_CONTEXT' in correct.content
    assert 'http://experiment.localhost:8123' in correct['Content-Security-Policy']
    assert correct['Origin-Agent-Cluster'] == '?1'
    for host in (isolated_host('r',web['other'].id), isolated_host('p',release.build_id), 'admin.localhost'):
        assert client.get(url, HTTP_HOST=host).status_code == 403
    asset = client.get(f'/run/{release.id}/web/gec/sdk.js', HTTP_HOST='experiment.localhost')
    assert asset.status_code == 302 and asset['Location'].startswith(isolated_origin('r',release.id))
    Grant.objects.create(user=web['owner'], study=web['study'], action='study.view')
    Grant.objects.create(user=web['owner'], study=web['study'], action='build.preview')
    token = signing.dumps({'build':str(release.build_id),'user':web['owner'].pk}, salt='preview')
    preview_path = f'/preview/{token}/web/index.html'
    assert client.get(preview_path, HTTP_HOST='experiment.localhost').status_code == 302
    assert client.get(preview_path, HTTP_HOST=isolated_host('p',release.build_id)).status_code == 200
    assert client.get(preview_path, HTTP_HOST=isolated_host('r',release.id)).status_code == 403


@pytest.mark.parametrize('origin_kind', ['r','p','unrelated','suffix','port','scheme','null','userinfo','path','unapproved'])
def test_cors_preflight_is_exact_origin_not_a_suffix_allowlist(web, origin_kind):
    origin = isolated_origin('r',web['release'].id)
    if origin_kind == 'p': origin = isolated_origin('p',web['release'].build_id)
    if origin_kind == 'unrelated': origin = 'https://attacker.invalid'
    if origin_kind == 'suffix': origin += '.attacker.invalid'
    if origin_kind == 'port': origin = origin.replace(':8123', ':8124')
    if origin_kind == 'scheme': origin = origin.replace('http:', 'https:')
    if origin_kind == 'null': origin = 'null'
    if origin_kind == 'userinfo': origin = origin.replace('://', '://user@')
    if origin_kind == 'path': origin += '/'
    if origin_kind == 'unapproved':
        web['release'].approved = False
        web['release'].save(update_fields=['approved'])
    response = Client().options('/v1/participant/sessions', HTTP_HOST='experiment.localhost', HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST', HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization, content-type')
    assert response.status_code == (204 if origin_kind == 'r' else 403)
    if origin_kind == 'r':
        assert response['Access-Control-Allow-Origin'] == origin and response['Vary'] == 'Origin'
        assert 'Access-Control-Allow-Credentials' not in response
    else:
        assert 'Access-Control-Allow-Origin' not in response


@pytest.mark.parametrize('action', ['status','context','event-batches','completion','recover'])
def test_other_release_origin_denies_even_a_valid_target_token(web, event, action):
    url = f'/v1/participant/sessions/{web["session"].id}/{action}'
    headers = {'HTTP_HOST':'experiment.localhost', 'HTTP_ORIGIN':isolated_origin('r',web['other'].id),
               'HTTP_AUTHORIZATION':'Bearer '+web['token']}
    client = Client()
    response = (client.get(url, **headers) if action in ('status','context') else
                client.post(url, {'batch_id':str(uuid.uuid4()),'events':[event]},
                            content_type='application/json', **headers))
    assert response.status_code == 403 and response.json()['code'] == 'origin_binding'
    assert Event.objects.count() == 0
    web['session'].refresh_from_db()
    assert web['session'].completion is None


def test_admission_and_recovery_bind_origin_before_mutation(web):
    headers = {'HTTP_HOST':'experiment.localhost', 'HTTP_ORIGIN':isolated_origin('r',web['other'].id)}
    count = Session.objects.count()
    for path in ('/v1/participant/sessions','/v1/participant/recovery'):
        response = Client().post(path, {**web['request'],'operation_id':str(uuid.uuid4())},
                                 content_type='application/json', **headers)
        assert response.status_code == 403 and response.json()['code'] == 'origin_binding'
    assert Session.objects.count() == count
    # Native callers and old same-origin uploaders retain the original contract.
    url = f'/v1/participant/sessions/{web["session"].id}/context'
    for origin in (None, 'http://experiment.localhost:8123', isolated_origin('r',web['release'].id)):
        extra = {'HTTP_ORIGIN':origin} if origin else {}
        response = Client().get(url, HTTP_HOST='experiment.localhost',
                                HTTP_AUTHORIZATION='Bearer '+web['token'], **extra)
        assert response.status_code == 200 and response.json()['release_id'] == str(web['release'].id)
    # No CORS grants on administrator endpoints, regardless of a valid runtime.
    response = Client().options('/v1/admin/exports', **headers)
    assert 'Access-Control-Allow-Origin' not in response


def test_certificate_allowlist_is_indexed_and_private_not_arbitrary(web, settings):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, 'gep']
    client = Client()
    for host, expected in ((isolated_host('r',web['release'].id),204),
                           (isolated_host('p',web['release'].build_id),204),
                           ('unknown.example.com',403),(isolated_host('r',uuid.uuid4()),403)):
        response = client.get('/_internal/tls-allow', {'domain':host}, HTTP_HOST='gep:8000')
        assert response.status_code == expected
        assert client.get('/_internal/tls-allow', {'domain':host}, HTTP_HOST='experiment.localhost').status_code == 403
    web['release'].approved = False
    web['release'].save(update_fields=['approved'])
    assert client.get('/_internal/tls-allow', {'domain':isolated_host('r',web['release'].id)},
                      HTTP_HOST='gep:8000').status_code == 403


def test_frozen_config_and_native_api_address_are_not_relabelled(web):
    before = copy.deepcopy(web['release'].config)
    config = connection_config(web['release'])
    assert config['api_url'] == 'http://experiment.localhost:8123'
    assert config['release_id'] == str(web['release'].id)
    assert release_url(web['release']).startswith(isolated_origin('r',web['release'].id))
    web['release'].refresh_from_db()
    assert web['release'].config == before


def test_bad_public_origin_is_a_controlled_refusal(web, monkeypatch):
    monkeypatch.setenv('GEP_PUBLIC_API','https://attacker.invalid')
    response = Client().options('/v1/participant/sessions', HTTP_HOST='experiment.localhost',
        HTTP_ORIGIN='https://attacker.invalid', HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST')
    assert response.status_code == 409 and response.json()['code'] == 'origin_configuration'
    assert 'Access-Control-Allow-Origin' not in response


def test_public_caddy_and_docker_context_include_the_isolation_boundaries():
    config = (ROOT/'deploy/Caddyfile').read_text()
    assert 'ask http://gep:8000/_internal/tls-allow' in config and 'on_demand' in config
    assert config.count('path /_internal/*') == 2 and config.count('respond @internal 404') == 2
    docker = (ROOT/'deploy/Dockerfile').read_text()
    ignore = (ROOT/'.dockerignore').read_text()
    assert 'COPY GEC_SDK/gec_web/sdk.js GEC_SDK/gec_web/sdk.js' in docker
    assert '!GEC_SDK/gec_web/sdk.js' in ignore and '!GEC_SDK/**' not in ignore


def test_pending_domain_configuration_carries_the_same_rules():
    from gep.deployment import domain_files, setup_values
    pending = domain_files('audit.invalid')
    assert 'ask http://gep:8000/_internal/tls-allow' in pending['Caddyfile.pending']
    assert pending['Caddyfile.pending'].count('path /_internal/*') == 2
    assert 'GEP_TRUST_PROXY=1' in pending['domain.env.pending']
    # Leave room for the release/build label, not only the portal hostname.
    values = {'username':'synthetic','password':'Synthetic1!','password_confirm':'Synthetic1!',
              'domain':'.'.join(['a'*60]*4)+'.com'}
    with pytest.raises(ValueError):
        setup_values(values)


@pytest.mark.django_db(transaction=True)
def test_additive_namespace_migration_preserves_existing_data(web, event):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from core.services import receive
    receive(web['session'].id,web['token'],{'batch_id':str(uuid.uuid4()),'events':[event]})
    before = copy.deepcopy(web['release'].config)
    token_hash = web['session'].token_hash
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([('core','0015_audit_study_uuid')])
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    expected = {f'r-{web["release"].id.hex}',f'p-{web["release"].build_id.hex}',
                f'r-{web["other"].id.hex}',f'p-{web["other_build"].id.hex}'}
    assert set(BrowserOrigin.objects.values_list('label',flat=True)) == expected
    assert [field.name for field in BrowserOrigin._meta.fields] == ['label']
    assert Event.objects.get().envelope == event
    web['release'].refresh_from_db()
    web['session'].refresh_from_db()
    assert web['release'].config == before and web['session'].token_hash == token_hash


def test_deleted_origin_retains_only_trusted_queue_and_permanent_refusal(web):
    from core import deletion
    remember('r',web['release'].id)
    web['instance'].authorization_version = 2
    web['instance'].save(update_fields=['authorization_version'])
    deletion.mark_study_deletion(web['owner'],web['study'],'synthetic-test-password')
    job = deletion.deletion_for(web['study'].id)
    headers = {'HTTP_HOST':'experiment.localhost','HTTP_ORIGIN':isolated_origin('r',web['release'].id),
               'HTTP_AUTHORIZATION':'Bearer '+web['token']}
    path = f'/v1/participant/sessions/{web["session"].id}/context'
    for stage in ('marked','cleaned'):
        if stage == 'cleaned':
            while deletion.deletion_for(web['study'].id).state != 'complete':
                deletion.run_cleanup(job.pk)
        refused = Client().get(path, **headers)
        assert refused.status_code == 403 and refused.json()['code'] == 'study_deleted'
        assert refused['Access-Control-Allow-Origin'] == headers['HTTP_ORIGIN']
        forged = Client().get(path, **{**headers,'HTTP_AUTHORIZATION':'Bearer synthetic-wrong'})
        assert forged.status_code == 403 and forged.json()['code'] == 'session_unavailable'
        resumed = Client().post(f'/v1/participant/sessions/{web["session"].id}/recover',
            {'proof':web['request']['proof'],'permit':'synthetic-unused'},content_type='application/json', **headers)
        assert resumed.status_code == 403 and resumed.json()['code'] == 'study_deleted'
        old_url = Client().get(f'/run/{web["release"].id}/web/index.html',
                              HTTP_HOST=isolated_host('r',web['release'].id))
        assert old_url.status_code == 404 and b'data-legacy-queue' in old_url.content
        assert b'GEP_CONTEXT' not in old_url.content and b'synthetic isolated program' not in old_url.content
    assert BrowserOrigin.objects.filter(label=f'r-{web["release"].id.hex}').exists()
    assert Event.objects.count() == 0


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1', reason='Opt-in real Chrome legacy queue check')
def test_real_chrome_legacy_pending_round_trip_without_running_an_experiment(live_server, web, monkeypatch):
    port = live_server.url.rsplit(':',1)[1]
    base = 'http://experiment.localhost:'+port
    monkeypatch.setenv('GEP_PUBLIC_API',base)
    config = connection_config(web['release'])
    script = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
 const page=await browser.newPage();await page.goto(process.env.GEP_OLD_ORIGIN+'/local-data');
 const ids=await page.evaluate(async config=>{
   const {GEC}=await import('/legacy-data/sdk.js');const client=new GEC(config);
   await client.prepare();clearInterval(client.timer);await client.begin();
   const id=client.record('exp.rt',{rt_ms:321.5,choice:'synthetic-legacy'},{id:'rt',version:'1'}).event_id;
   await client.commit();await client.finish();const row=await client.get(client.id);
   client.close();return {event:id,session:row.id,records:row.records.length,pending:row.pending.length};
 },JSON.parse(process.env.GEP_OLD_CONFIG));
 expect(ids.records).toBe(1);expect(ids.pending).toBe(1);
 // Close/reopen the real durable database before using the trusted page.
 await page.reload();await page.locator('#legacy-inspect').click();
 await expect(page.locator('#legacy-count')).toContainText('1 / 1');
 await page.locator('#legacy-upload').click();
 await expect(page.locator('#legacy-count')).toContainText('0 / 0');
 const tombstone=await page.evaluate(async id=>{
   const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('gec-1',1);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
   const row=await new Promise((resolve,reject)=>{const tx=db.transaction('sessions');const r=tx.objectStore('sessions').get(id);tx.oncomplete=()=>resolve(r.result);tx.onerror=()=>reject(tx.error);});
   db.close();return {kind:row.kind,state:row.state,hasRecords:'records' in row,hasProof:'proof' in row};
 },ids.session);
 expect(tombstone).toEqual({kind:'cleaned',state:'remote_acknowledged',hasRecords:false,hasProof:false});
 console.log('Legacy closed/reopened queue received and acknowledged; cleanup followed ACK only.');
} finally {await browser.close();}
'''
    result = subprocess.run(['node','--input-type=module','-e',script], cwd=ROOT, capture_output=True,text=True,
        env={**os.environ,'GEP_OLD_ORIGIN':base,'GEP_OLD_CONFIG':json.dumps(config)},timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    row = Event.objects.get()
    assert row.envelope['payload'] == {'rt_ms':321.5,'choice':'synthetic-legacy'}
    assert row.session.release_id == web['release'].id
    assert row.session.completion is not None
