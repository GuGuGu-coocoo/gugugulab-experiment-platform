"""2026-10-22 defensive audit: synthetic databases and temporary archives only.

The opt-in shared-origin test documents an OPEN finding; its passing result is
evidence of the risk, not a claim that the browser isolation has been fixed.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import uuid
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import Client

from core import excel, packages
from core.models import Build, Event, Grant, Release, Session, Study, Throttle
from core.protocol import Rejected, parse
from core.services import admit, digest, receive

ROOT = Path(__file__).resolve().parents[1]


def test_admission_rotation_cannot_bypass_source_budget(setup):
    """A bounded 121-request check, not a load test; no session is created."""
    client = Client()
    for _ in range(120):
        data = {**setup['request'], 'operation_id': str(uuid.uuid4()), 'release_id': str(uuid.uuid4())}
        response = client.post('/v1/participant/sessions', json.dumps(data), content_type='application/json',
                               HTTP_HOST='experiment.localhost', REMOTE_ADDR='192.0.2.40')
        assert response.status_code == 403
    data['operation_id'] = str(uuid.uuid4())
    response = client.post('/v1/participant/sessions', json.dumps(data), content_type='application/json',
                           HTTP_HOST='experiment.localhost', REMOTE_ADDR='192.0.2.40',
                           HTTP_X_FORWARDED_FOR='192.0.2.41')
    assert response.status_code == 429
    assert response.json()['code'] == 'rate_limited'
    assert Session.objects.count() == 1


@pytest.mark.parametrize('body', ['null', '[]', '"text"', '9' * 400])
@pytest.mark.parametrize('url', ['/v1/participant/sessions', '/v1/participant/recovery'])
def test_nonobject_and_large_integer_requests_are_controlled_refusals(setup, body, url):
    response = Client(raise_request_exception=False).post(url, body, content_type='application/json',
                                                         HTTP_HOST='experiment.localhost')
    assert 400 <= response.status_code < 500
    assert response['Content-Type'].startswith('application/json')
    assert b'Traceback' not in response.content
    assert Session.objects.count() == 1


def _zip(members, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return output.getvalue()


@pytest.mark.parametrize('oversized_name', ['_rels/.rels', 'later.bin'])
def test_xlsx_checks_complete_budget_before_parsing_relationships(monkeypatch, oversized_name):
    # Only the central-directory size is forged. The actual input is tiny; no
    # large decompression or resource-exhaustion payload is constructed.
    members = {'_rels/.rels': b'<Relationships/>', 'later.bin': b'placeholder'}
    raw = bytearray(_zip(members))
    position = 0
    while True:
        position = raw.index(b'PK\x01\x02', position)
        name_length = struct.unpack_from('<H', raw, position + 28)[0]
        name = raw[position + 46:position + 46 + name_length].decode()
        if name == oversized_name:
            struct.pack_into('<I', raw, position + 24, excel.MAX_EXPANDED + 1)
            break
        position += 46 + name_length
    parsed = []
    monkeypatch.setattr(excel, '_has_external_relationship', lambda *args: parsed.append(True) or False)
    with pytest.raises(Rejected):
        excel._archive(bytes(raw))
    assert parsed == [], 'XML parsing must not precede whole-archive budget validation'


@pytest.mark.parametrize('compression', [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_xlsx_refuses_nonstandard_decompressors_before_xml(compression):
    with pytest.raises(Rejected, match='compression_unsupported'):
        excel._archive(_zip({'_rels/.rels': b'<Relationships/>'}, compression))


@pytest.mark.parametrize('platform', ['macos_arm64', 'windows_x64'])
def test_native_archive_budget_precedes_payload_read(platform, monkeypatch):
    raw = _zip({'a': b'small'})
    reads = []
    monkeypatch.setattr(packages, '_read_native_payload', lambda *args: reads.append(True) or {})
    monkeypatch.setattr(packages, 'MAX_NATIVE_EXPANDED', 1)
    descriptor = {'platform': platform, 'program_sha256': hashlib.sha256(raw).hexdigest()}
    with pytest.raises(Rejected):
        packages.native_program_valid(raw, descriptor)
    assert reads == []


@pytest.mark.parametrize('action', ['status', 'context', 'event-batches', 'completion'])
def test_session_uuid_never_substitutes_for_bearer_authorization(setup, event, action):
    other = {**setup['request'], 'operation_id': str(uuid.uuid4()), 'proof': 'different-synthetic-proof-' * 2}
    _, token = admit(setup['release'], other)
    client = Client()
    url = f"/v1/participant/sessions/{setup['session'].id}/{action}"
    fields = {'HTTP_HOST': 'experiment.localhost', 'HTTP_AUTHORIZATION': 'Bearer ' + token}
    if action in ('status', 'context'):
        response = client.get(url, **fields)
    else:
        data = ({'batch_id': str(uuid.uuid4()), 'events': [event]} if action == 'event-batches'
                else {'event_ids': [], 'segment_ids': []})
        response = client.post(url, json.dumps(data), content_type='application/json', **fields)
    assert response.status_code == 401
    assert Event.objects.count() == 0
    setup['session'].refresh_from_db()
    assert setup['session'].completion is None


def test_envelope_session_substitution_rolls_back(setup, event):
    wrong = copy.deepcopy(event)
    wrong['session_id'] = str(uuid.uuid4())
    with pytest.raises(Rejected, match='event_binding'):
        receive(setup['session'].id, setup['token'], {'batch_id': str(uuid.uuid4()), 'events': [wrong]})
    assert Event.objects.count() == 0


def _web_package(schemas):
    members = {'web/index.html': b'<html><head></head><body>synthetic audit</body></html>'}
    members['web/gec/sdk.js'] = (ROOT / 'GEC_SDK/gec_web/sdk.js').read_bytes()
    program = hashlib.sha256()
    for name, value in sorted(members.items()):
        program.update(name.encode())
        program.update(value)
    descriptor = {'platform': 'godot_web', 'version': 'audit-synthetic-1', 'host_version': '4.7.2',
                  'sdk_version': '0.1.0', 'protocol_version': 'gep/1', 'schemas': schemas,
                  'codebook': {'rt_ms': {'unit': 'ms', 'source': 'synthetic audit'}},
                  'program_sha256': program.hexdigest()}
    members['manifest.json'] = json.dumps(descriptor).encode()
    raw = _zip(members)
    validated, sha = packages.validate_package(raw)
    assert validated == descriptor
    return raw, descriptor, sha


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1', reason='Opt-in isolated real Chrome boundary demonstration')
def test_open_finding_preview_can_read_another_study_local_queue(live_server, setup, settings, tmp_path, monkeypatch):
    """OPEN HIGH: proves shared-origin access; no genuine participant data."""
    settings.DATA_DIR = tmp_path
    (tmp_path / 'packages').mkdir()
    port = live_server.url.rsplit(':', 1)[1]
    base = 'http://experiment.localhost:' + port
    monkeypatch.setenv('GEP_PUBLIC_API', base)
    raw, descriptor, sha = _web_package(setup['release'].build.descriptor['schemas'])
    (tmp_path / 'packages' / (sha + '.zip')).write_bytes(raw)
    victim_build = setup['release'].build
    victim_build.descriptor, victim_build.package_path = descriptor, sha + '.zip'
    victim_build.save()
    other_study = Study.objects.create(title='Synthetic unrelated study', recruitment='open')
    attacker = get_user_model().objects.create_user('synthetic_audit_uploader')
    for action in ('study.view', 'build.upload', 'build.preview'):
        Grant.objects.create(user=attacker, study=other_study, action=action)
    other_build = Build.objects.create(study=other_study, descriptor=descriptor, digest=sha, package_path=sha + '.zip')
    preview_token = signing.dumps({'build': str(other_build.id), 'user': attacker.pk}, salt='preview')
    assert not Grant.objects.filter(user=attacker, study=setup['study']).exists()
    script = r'''
import {chromium, expect} from '@playwright/test';
const browser = await chromium.launch({channel:'chrome',headless:true});
try {
 const context = await browser.newContext();
 const page = await context.newPage();
 await page.goto(process.env.GEP_AUDIT_VICTIM);
 await page.evaluate(async () => {
   const {GEC} = await import('./gec/sdk.js');
   const client = new GEC(globalThis.GEP_CONTEXT);
   await client.prepare(); await client.begin();
   client.record('exp.rt',{rt_ms:321.5,choice:'synthetic-left'},{id:'rt',version:'1'});
   await client.commit(); client.close();
 });
 await page.goto(process.env.GEP_AUDIT_PREVIEW);
 const evidence = await page.evaluate(async () => {
   const db = await new Promise((resolve,reject) => {const r=indexedDB.open('gec-1',1); r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
   const rows = await new Promise((resolve,reject) => {const r=db.transaction('sessions').objectStore('sessions').getAll();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
   db.close();
   const victim = rows.find(s=>s.kind==='session' && s.config.study_id!==globalThis.GEP_CONTEXT.study_id);
   const response = await fetch('/v1/participant/sessions/'+victim.id+'/context',{headers:{Authorization:'Bearer '+victim.context.token},credentials:'omit'});
   return {crossStudyRead:victim.records[0].payload.rt_ms===321.5,
           proofExposed:typeof victim.proof==='string',tokenAccepted:response.status===200,
           preview:globalThis.GEP_CONTEXT.preview===true};
 });
 expect(evidence).toEqual({crossStudyRead:true,proofExposed:true,tokenAccepted:true,preview:true});
 console.log(JSON.stringify(evidence));
} finally {await browser.close();}
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT,
                            env={**os.environ, 'GEP_AUDIT_VICTIM': f'{base}/run/{setup["release"].id}/web/index.html',
                                 'GEP_AUDIT_PREVIEW': f'{base}/preview/{preview_token}/web/index.html'},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    print('OPEN FINDING, NOT A FIX: ' + result.stdout.strip())
