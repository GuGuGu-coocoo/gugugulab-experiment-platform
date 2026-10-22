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
import sqlite3
import uuid
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import Client
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http.request import HttpRequest
from django.db import connection

from core import excel, packages, exports as export_core
from core.models import Build, Event, Export, Grant, Release, Session, Study, Throttle
from core.protocol import Rejected, parse
from core.services import admit, digest, receive

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('schema', [
    {'type': 'string', 'pattern': '(a+)+$'},
    {'type': 'object', 'patternProperties': {'(a+)+$': {'type': 'string'}}},
    {'properties': {'answer': {'pattern': 'anything'}}},
    {'items': {'anyOf': [{'type': 'null'}, {'pattern': 'anything'}]}},
    {'$defs': {'unused': {'pattern': 'anything'}}},
    {'dependentSchemas': {'answer': {'patternProperties': {}}}},
    {'if': {'properties': {'answer': {'pattern': 'anything'}}}, 'then': {'type': 'null'}},
    {'contentSchema': {'pattern': 'anything'}},
])
def test_regex_rules_are_rejected_at_registration(schema):
    with pytest.raises(Rejected, match='schema_regex_unsupported'):
        packages.schema_safe(schema)


def test_data_keys_and_annotations_named_pattern_remain_legal():
    schema = {'type': 'object', 'properties': {'pattern': {'type': 'string'},
                                              'patternProperties': {'type': 'string'}},
              'const': {'pattern': 'literal data', 'patternProperties': 'literal data'},
              'default': {'pattern': 'literal data'}, 'examples': [{'pattern': 'literal data'}]}
    packages.schema_safe(schema)


def test_frozen_regex_build_rejects_upload_and_new_admission_without_mutation(setup, event, monkeypatch):
    build = setup['release'].build
    build.descriptor['schemas']['exp.rt']['schema']['properties']['choice']['pattern'] = '(a+)+$'
    build.save(update_fields=['descriptor'])
    def no_validator(*args, **kwargs):
        pytest.fail('An unsupported regex must be refused before jsonschema is called')
    monkeypatch.setattr('core.services.Draft202012Validator', no_validator)
    with pytest.raises(Rejected, match='schema_regex_unsupported'):
        receive(setup['session'].id, setup['token'], {'batch_id': str(uuid.uuid4()), 'events': [event]})
    with pytest.raises(Rejected, match='schema_regex_unsupported'):
        admit(setup['release'], {**setup['request'], 'operation_id': str(uuid.uuid4())})
    build.refresh_from_db()
    assert build.descriptor['schemas']['exp.rt']['schema']['properties']['choice']['pattern'] == '(a+)+$'
    assert Event.objects.count() == 0 and Session.objects.count() == 1


def test_regex_refusal_rolls_back_an_entire_mixed_batch(setup, event):
    build = setup['release'].build
    build.descriptor['schemas']['unsafe'] = {'id': 'unsafe', 'version': '1',
                                             'schema': {'type': 'string', 'pattern': '(a+)+$'}}
    build.save(update_fields=['descriptor'])
    unsafe = {**event, 'event_id': str(uuid.uuid4()), 'event_type': 'unsafe', 'schema_id': 'unsafe',
              'payload': 'small synthetic value'}
    with pytest.raises(Rejected, match='schema_regex_unsupported'):
        receive(setup['session'].id, setup['token'], {'batch_id': str(uuid.uuid4()), 'events': [event, unsafe]})
    assert Event.objects.count() == 0


def test_admission_rotation_cannot_bypass_source_budget(setup, monkeypatch):
    """A bounded 121-request check, not a load test; no session is created."""
    client = Client()
    monkeypatch.setattr('core.throttle.time.time', lambda: 1800000000)
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


def test_global_admission_budget_precedes_new_client_and_operation_keys(setup, monkeypatch):
    from core.throttle import check
    monkeypatch.setattr('core.throttle.time.time', lambda: 1800000000)
    for _ in range(600):
        check('admit-global', limit=600)
    count = Throttle.objects.count()
    data = {**setup['request'], 'operation_id': str(uuid.uuid4())}
    response = Client().post('/v1/participant/sessions', json.dumps(data), content_type='application/json',
                             HTTP_HOST='experiment.localhost', REMOTE_ADDR='192.0.2.99')
    assert response.status_code == 429 and response['Retry-After'] == '60'
    assert Throttle.objects.count() == count
    assert Session.objects.count() == 1


@pytest.mark.parametrize('path', ['/users', '/studies/11111111-1111-4111-8111-111111111111/builds'])
def test_anonymous_upload_denied_before_csrf_parses_multipart(setup, monkeypatch, path):
    client = Client(enforce_csrf_checks=True)
    client.get('/login')  # An attacker can obtain a CSRF cookie without an account.
    original = HttpRequest._load_post_and_files
    calls = []
    def observed(request):
        calls.append(True)
        return original(request)
    monkeypatch.setattr(HttpRequest, '_load_post_and_files', observed)
    response = client.post(path, {'package': SimpleUploadedFile('audit.zip', b'tiny synthetic input')})
    assert response.status_code == 403
    assert calls == [], 'Unauthenticated requests must not reach multipart parsing'


@pytest.mark.parametrize('compression', [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_web_and_native_refuse_nonstandard_decompressors(compression):
    raw = _zip({'web/index.html': b'<head></head>'}, compression)
    with pytest.raises(Rejected, match='compression_unsupported'):
        packages.validate_package(raw)
    descriptor = {'platform': 'macos_arm64', 'program_sha256': hashlib.sha256(raw).hexdigest()}
    with pytest.raises(Rejected, match='compression_unsupported'):
        packages.native_program_valid(raw, descriptor)


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


def test_owned_token_is_not_proof_of_genuine_task_execution(setup, event):
    """Known trust boundary: schema-valid client claims are not attestation."""
    event['payload'] = {'rt_ms': 0, 'choice': 'synthetic-fabricated-claim'}
    result = receive(setup['session'].id, setup['token'], {'batch_id': str(uuid.uuid4()), 'events': [event]})
    assert result['accepted'] == [event['event_id']]
    assert Event.objects.get().envelope == event


@pytest.mark.django_db(transaction=True)
def test_synthetic_sqlite_backup_retains_events_permissions_and_revocation(setup, event, tmp_path):
    """Logical DB backup only, not a production volume/RPO/RTO claim."""
    receive(setup['session'].id, setup['token'], {'batch_id': str(uuid.uuid4()), 'events': [event]})
    setup['session'].revoked = True
    setup['session'].save(update_fields=['revoked'])
    Grant.objects.create(user=setup['owner'], study=setup['study'], action='study.view')
    target = tmp_path / 'synthetic_backup.sqlite3'
    with sqlite3.connect(target) as backup:
        connection.connection.backup(backup)
    with sqlite3.connect(target.as_uri() + '?mode=ro', uri=True) as restored:
        assert restored.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        envelope = json.loads(restored.execute('SELECT envelope FROM core_event').fetchone()[0])
        assert envelope == event
        assert restored.execute('SELECT revoked FROM core_session WHERE id=?', (setup['session'].pk.hex,)).fetchone() == (1,)
        assert restored.execute('SELECT action FROM core_grant WHERE user_id=?', (setup['owner'].pk,)).fetchone() == ('study.view',)
        assert restored.execute('SELECT instance_id FROM core_instance').fetchone() == (setup['instance'].instance_id.hex,)


def test_researcher_scope_cannot_be_swapped_through_ids(setup):
    member = get_user_model().objects.create_user('synthetic_audit_member')
    for action in ('study.view', 'study.configure', 'build.upload', 'data.export_raw', 'identity_mapping.read'):
        Grant.objects.create(user=member, study=setup['study'], action=action)
    other = Study.objects.create(title='Synthetic private B', recruitment='open')
    build = Build.objects.create(study=other, descriptor=setup['release'].build.descriptor, digest='b' * 64)
    release = Release.objects.create(study=other, build=build, approved=True, config={'purpose': 'synthetic'})
    session, _ = admit(release, {**setup['request'], 'operation_id': str(uuid.uuid4()),
                                 'study_id': str(other.pk), 'release_id': str(release.pk), 'build_id': str(build.pk)})
    # Owner has no implicit legacy grants either. Create this synthetic B
    # snapshot through the authorized member of B, not by bypassing its guard.
    for action in ('study.view', 'data.export_raw'):
        Grant.objects.create(user=setup['owner'], study=other, action=action)
    item = export_core.create_v2_export(setup['owner'], other.pk, view='unmapped', language='en')
    client = Client()
    client.force_login(member)
    for url in (f'/studies/{other.pk}', f'/releases/{release.pk}/config', f'/releases/{release.pk}/artifact',
                f'/v1/admin/exports/{item.pk}/download?format=jsonl',
                f'/v1/admin/exports/{item.pk}/download?format=metadata',
                f'/v1/admin/exports/{item.pk}/download?format=zip'):
        assert client.get(url).status_code == 403
    before = Export.objects.count()
    with pytest.raises(Rejected):
        export_core.create_v2_export(member, setup['study'].pk, view='unmapped', language='en',
                                    session_ids=[str(session.pk)])
    assert Export.objects.count() == before


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
