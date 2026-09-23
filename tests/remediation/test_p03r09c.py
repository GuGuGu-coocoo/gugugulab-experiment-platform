"""P03R09C: the shell's persisted delivery presentation and the deletion terminal.

Three real checks, none of them mocked or skipped:

* the real Godot native shell (real SQLite backend, real HTTP against the
  synthetic fault server) through the failure surface, the legal failure export,
  the same-state refresh, a real local save failure and the deletion terminal,
  plus the durable reopen of a received tombstone and of an unfinished session;
* the real exported Godot Web build in real Chrome, driving the shipped bridge
  and participation panel (the unpatched real bridge must answer both a
  successful flush and an unexpired backoff wait), with a real IndexedDB
  completion-save abort;
* an isolated real Django server whose study is really marked deleted through
  the production deletion service: the legitimate and unknown bindings, the
  front-locked named recovery, and the real client before/after the mark.

Evidence: one fresh session root under
``local_data/phase03_remediation_20260923/p03r09c/<UTC stamp>-<random>/``; no
existing root is reused or overwritten.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'examples' / 'synthetic_experiment'
FAULT_APP = ROOT / 'tests' / 'remediation' / 'delivery_fault_app.py'
HARNESS = ROOT / 'tests' / 'native' / 'remediation_shell_harness.gd'
SPEC = 'tests/browser/remediation_shell.spec.js'
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_p03r09b import FaultServer, _free_port, _godot, _run  # noqa: E402,F401


def test_scientific_task_hash_unchanged():
    """The science task source is byte-identical to its frozen digest."""
    task = (PROJECT / 'task.gd').read_bytes()
    recorded = (ROOT / 'tests' / 'fixtures' / 'scientific_task.sha256').read_text().strip()
    assert hashlib.sha256(task).hexdigest() == recorded, 'the scientific task source changed'


@pytest.fixture
def fault_server():
    server = FaultServer(ROOT)
    try:
        yield server
    finally:
        server.stop()


def _shell_env(storage, url, phase, *, instance, study, build, extra=None):
    env = {'GEP_DELIVERY_FAULT_URL': url, 'GEP_SYNTHETIC_STORAGE': str(storage), 'GEP_SHELL_PHASE': phase,
           'GEP_SHELL_INSTANCE': instance, 'GEP_SHELL_STUDY': study, 'GEP_SHELL_BUILD': build}
    if extra:
        env.update(extra)
    return env


def _http_json(base, path, body, token=None):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    if token:
        request.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode('utf-8'))


def test_real_native_shell_presentation(evidence_root, evidence, fault_server):
    """The real native shell over real SQLite and real HTTP.

    ``main`` proves the retired entry form, the persisted failure surface after
    the first and the 1st/2nd/3rd retry failure, the same-state refresh, the
    legal failure export without secrets, the successful shell retry that hides
    the surface, a real SQLite save failure and the permanent deletion terminal.
    ``reopen_cleaned``/``reopen_pending`` prove the fresh process over the same
    store distinguishes a received tombstone from an unfinished session by the
    persisted summary alone.
    """
    godot = _godot()
    storage = evidence_root / 'native' / 'storage'
    storage.mkdir(parents=True)
    instance, study, build = secrets.token_hex(16), secrets.token_hex(16), secrets.token_hex(16)
    outputs = {}
    for phase in ('main', 'reopen_cleaned', 'reopen_pending'):
        result = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=420,
                      env=_shell_env(storage, fault_server.url, phase, instance=instance, study=study, build=build))
        evidence(f'native_{phase}_stdout.txt', {'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:], 'exit': result.returncode})
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
        assert 'P03R09C_SHELL_VERIFIED' in result.stdout, result.stdout[-4000:] + result.stderr[-2000:]
        assert 'P03R09C_SHELL_FAILED' not in result.stdout, result.stdout[-4000:]
        outputs[phase] = result.stdout

    state = fault_server.client.state()
    evidence('native_fault_state.json', state)
    sessions = state.get('sessions', {})
    cleaned = [entry for entry in sessions.values() if entry['batch_requests'] == 5]
    assert len(cleaned) == 1, sessions
    assert len(cleaned[0]['events']) == 2 and cleaned[0]['completion_requests'] == 1, cleaned[0]
    deleted_match = re.search(r'P03R09C_DELETED (\S+)', outputs['main'])
    pending_match = re.search(r'P03R09C_PENDING (\S+)', outputs['main'])
    assert deleted_match and pending_match, outputs['main'][-2000:]
    deleted = sessions[deleted_match.group(1)]
    assert deleted['batch_requests'] == 1 and deleted['completion_requests'] == 0 and deleted['events'] == [], deleted
    pending = sessions[pending_match.group(1)]
    assert pending['batch_requests'] == 4 and pending['completion_requests'] == 0 and pending['events'] == [], pending


def test_real_chrome_shell(evidence_root, evidence, fault_server):
    """The exported real Godot Web build driven through the shipped shell.

    The export is built from the current sources (pinned Godot 4.7.2) and served
    with the same context injection the real host uses. The spec requires one
    passed, non-skipped test and a failure-free evidence document.
    """
    godot = _godot()
    export = evidence_root / 'web_export'
    export.mkdir()
    (export / 'gec').mkdir()
    for name in ('sdk.js', 'bridge.js', 'inputs.js', 'shell.js'):
        shutil.copy2(ROOT / 'packages' / 'gec_web' / name, export / 'gec' / name)
    built = _run([godot, '--headless', '--path', str(PROJECT), '--export-release', 'Web', str(export / 'index.html')], timeout=300)
    assert built.returncode == 0 and (export / 'index.wasm').is_file(), built.stdout[-3000:] + built.stderr[-2000:]

    context = {
        'config_version': '1', 'protocol_version': 'gep/1', 'sdk_version': '0.1.0', 'purpose': 'synthetic', 'locale': 'zh',
        'api_url': fault_server.url, 'instance_id': secrets.token_hex(16), 'study_id': secrets.token_hex(16),
        'release_id': 'release-1', 'build_id': secrets.token_hex(16), 'mode': 'anonymous',
    }
    chrome = evidence_root / 'chrome'
    chrome.mkdir()
    job_path = chrome / 'job.json'
    job_path.write_text(json.dumps({'package_dir': str(export), 'evidence_dir': str(chrome),
                                    'api_url': context['api_url'], 'context': context}), encoding='utf-8')
    runner = ROOT / 'node_modules' / '.bin' / 'playwright'
    assert runner.exists(), 'the pinned Playwright runner is required (pnpm install --frozen-lockfile)'
    report = _run([str(runner), 'test', SPEC, '--reporter=json'], timeout=900, env={'GEP_REMEDIATION_JOB': str(job_path)})
    document = None
    start = report.stdout.find('{')
    if start >= 0:
        try:
            document = json.loads(report.stdout[start:])
        except ValueError:
            document = None
    assert report.returncode == 0, report.stdout[-4000:] + report.stderr[-2000:]
    assert document is not None, report.stdout[-2000:]
    stats = document.get('stats', {})
    assert stats.get('expected') == 1 and stats.get('unexpected') == 0 and stats.get('skipped') == 0, stats
    evidence('playwright_report.json', document)

    summary = json.loads((chrome / 'summary.json').read_text())
    evidence('chrome_summary.json', summary)
    assert summary['failures'] == [], summary['failures']
    assert len(summary['checks']) >= 35, 'the Chrome evidence is too thin: %d checks' % len(summary['checks'])
    scenarios = summary['scenarios']
    for name in ('failure_surface', 'export', 'received', 'local_save_failure'):
        assert name in scenarios, f'the {name} scenario is missing'


def test_real_deletion_server_client_boundaries(evidence_root, evidence):
    """The real isolated server, really marked deleted, and the real client.

    Before the mark the legitimate probe batch, the named recovery and the
    front-locked refusal are exercised over real HTTP; the mark runs through the
    production deletion service; after the mark the exact tombstone token gets
    ``study_deleted`` while unknown sessions/tokens stay ``session_unavailable``
    and the exact proof keeps ``study_deleted``. The real native client then
    completes a live session before the mark and is permanently stopped by the
    same server after it, keeping the unconfirmed records and exporting them
    without credentials; the access log proves no further upload was attempted.
    """
    if str(ROOT / 'tools') not in sys.path:
        sys.path.insert(0, str(ROOT / 'tools'))
    import phase03_verify_shell as shell_verify

    verify = shell_verify.Verify(evidence_root / 'live')
    verify.init_instance()
    verify.start_server()
    try:
        proof_before, proof_deleted, proof_probe = 'p' * 48, 'q' * 48, 'r' * 48
        token_before, token_deleted, token_probe = secrets.token_hex(16), secrets.token_hex(16), secrets.token_hex(16)
        world_code = (
            "import hashlib, json, os, uuid, django\n"
            "django.setup()\n"
            "from datetime import timedelta\n"
            "from django.contrib.auth.hashers import make_password\n"
            "from django.utils import timezone\n"
            "from core.models import Build, Instance, Participant, Release, Session, Study\n"
            "instance = Instance.objects.get(pk=1)\n"
            "instance.authorization_version = 2\n"
            "instance.save(update_fields=['authorization_version'])\n"
            "study = Study.objects.create(title='Live deletion study', mode='password', recruitment='open', max_sessions=8)\n"
            "descriptor = {'platform': 'godot_web', 'version': 'synthetic-1', 'schemas': {'exp.rt': {'id': 'rt', 'version': '1', "
            "'schema': {'type': 'object', 'properties': {'rt_ms': {'type': 'number'}, 'choice': {'type': 'string'}}, "
            "'required': ['rt_ms', 'choice'], 'additionalProperties': False}}}}\n"
            "build = Build.objects.create(study=study, descriptor=descriptor, digest='d' * 64)\n"
            "release = Release.objects.create(study=study, build=build, approved=True, "
            "config={'purpose': 'synthetic', 'mode': 'password', 'max_sessions': 8})\n"
            "study.current_release = release\n"
            "study.save(update_fields=['current_release'])\n"
            "participant = Participant.objects.create(study=study, code='001', password_hash=make_password('synthetic-password'))\n"
            "def make_session(proof, token):\n"
            "    return Session.objects.create(participant=participant, release=release, operation=uuid.uuid4(), "
            "proof_hash=hashlib.sha256(proof.encode()).hexdigest(), request={'operation_id': str(uuid.uuid4())}, "
            "token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=timezone.now() + timedelta(days=1))\n"
            "before = make_session(os.environ['GEP_LIVE_PROOF_BEFORE'], os.environ['GEP_LIVE_TOKEN_BEFORE'])\n"
            "deleted = make_session(os.environ['GEP_LIVE_PROOF_DELETED'], os.environ['GEP_LIVE_TOKEN_DELETED'])\n"
            "probe = make_session(os.environ['GEP_LIVE_PROOF_PROBE'], os.environ['GEP_LIVE_TOKEN_PROBE'])\n"
            "print('LIVE_WORLD', json.dumps({'instance': str(instance.instance_id), 'study': str(study.id), 'release': str(release.id), "
            "'build': str(build.id), 'before': str(before.id), 'deleted': str(deleted.id), 'probe': str(probe.id)}))\n"
        )
        created = verify.run_python(world_code, extra_env={
            'GEP_LIVE_PROOF_BEFORE': proof_before, 'GEP_LIVE_TOKEN_BEFORE': token_before,
            'GEP_LIVE_PROOF_DELETED': proof_deleted, 'GEP_LIVE_TOKEN_DELETED': token_deleted,
            'GEP_LIVE_PROOF_PROBE': proof_probe, 'GEP_LIVE_TOKEN_PROBE': token_probe})
        match = re.search(r'LIVE_WORLD (\{.*\})', created.stdout)
        assert match, created.stdout + created.stderr
        world = json.loads(match.group(1))
        base = f'http://127.0.0.1:{verify.port}'
        binding = {'instance_id': world['instance'], 'study_id': world['study'], 'release_id': world['release'], 'build_id': world['build']}
        exchanges = []

        def exchange(label, path, body, token=None):
            status, payload = _http_json(base, path, body, token)
            exchanges.append({'label': label, 'status': status, 'payload': payload})
            return status, payload

        # ---- before the mark: the same binding works and front locks refuse --
        status, payload = exchange('named front_locked', '/v1/participant/recovery', {
            'capability': 'recovery_named/v1', 'participant_code': '001', 'password': 'synthetic-password',
            'proof': proof_before, 'front_locked': True, **binding})
        assert status == 403 and payload['code'] == 'front_locked', payload
        status, payload = exchange('named wrong password', '/v1/participant/recovery', {
            'capability': 'recovery_named/v1', 'participant_code': '001', 'password': 'not-the-password',
            'proof': proof_before, 'front_locked': False, **binding})
        assert status == 403 and payload['code'] == 'recovery_denied' and 'token' not in payload, payload
        status, payload = exchange('named legitimate', '/v1/participant/recovery', {
            'capability': 'recovery_named/v1', 'participant_code': '001', 'password': 'synthetic-password',
            'proof': proof_before, 'front_locked': False, **binding})
        assert status == 200 and payload['token'] and payload['session_id'] == world['before'], payload
        status, payload = exchange('named forged proof before', '/v1/participant/recovery', {
            'capability': 'recovery_named/v1', 'participant_code': '001', 'password': 'synthetic-password',
            'proof': 'f' * 48, 'front_locked': False, **binding})
        assert status == 403 and payload['code'] == 'recovery_denied' and 'token' not in payload, payload
        probe_event = {'protocol_version': 'gep/1', 'event_id': str(uuid.uuid4()), 'session_id': world['probe'],
                       'segment_id': str(uuid.uuid4()), 'sequence': 1, 'event_type': 'exp.rt', 'schema_id': 'rt',
                       'schema_version': '1', 'payload': {'rt_ms': 321.5, 'choice': 'left'}}
        probe_batch = {'batch_id': str(uuid.uuid4()), 'events': [probe_event]}
        status, payload = exchange('probe batch before', f"/v1/participant/sessions/{world['probe']}/event-batches", probe_batch, token_probe)
        assert status == 200 and payload['accepted'] == [probe_event['event_id']], payload

        # ---- the real native client completes a live session before the mark --
        godot = _godot()
        live_storage = evidence_root / 'live_native' / 'storage'
        live_storage.mkdir(parents=True)
        shared = {'GEP_SHELL_LIVE_URL': base, 'GEP_SHELL_INSTANCE': world['instance'], 'GEP_SHELL_STUDY': world['study'],
                  'GEP_SHELL_RELEASE': world['release'], 'GEP_SHELL_BUILD': world['build']}
        before_env = dict(shared, GEP_SHELL_PHASE='live_before', GEP_SHELL_SESSION=world['before'], GEP_SHELL_TOKEN=token_before,
                          GEP_SHELL_PROOF=proof_before, GEP_SHELL_EVENT_ID=str(uuid.uuid4()), GEP_SHELL_SEGMENT_ID=str(uuid.uuid4()))
        before = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=300,
                      env={**os.environ, 'GEP_SYNTHETIC_STORAGE': str(live_storage), **before_env})
        evidence('native_live_before_stdout.txt', {'stdout': before.stdout[-8000:], 'stderr': before.stderr[-4000:], 'exit': before.returncode})
        assert before.returncode == 0 and 'P03R09C_SHELL_VERIFIED' in before.stdout, before.stdout[-4000:] + before.stderr[-2000:]

        # ---- the real deletion mark through the production service ----------
        marked = verify.run_python(
            "import django, os\n"
            "django.setup()\n"
            "from core import deletion\n"
            "from core.models import Instance, Study\n"
            "owner = Instance.objects.get(pk=1).owner\n"
            "study = Study.objects.get(pk=os.environ['GEP_LIVE_STUDY'])\n"
            "print('LIVE_MARK', deletion.mark_study_deletion(owner, study, os.environ['GEP_OWNER_PASSWORD'])['state'])\n",
            extra_env={'GEP_LIVE_STUDY': world['study'], 'GEP_OWNER_PASSWORD': verify.owner_password})
        assert 'LIVE_MARK marked' in marked.stdout, marked.stdout + marked.stderr

        # ---- after the mark: the tombstone refuses, unknown stays unknown ---
        status, payload = exchange('probe batch after', f"/v1/participant/sessions/{world['probe']}/event-batches", probe_batch, token_probe)
        assert status == 403 and payload['code'] == 'study_deleted' and payload['retryable'] is False and payload['outcome_unknown'] is False, payload
        status, payload = exchange('unknown session', f"/v1/participant/sessions/{uuid.uuid4()}/event-batches", probe_batch, token_probe)
        assert status == 403 and payload['code'] == 'session_unavailable', payload
        status, payload = exchange('wrong token', f"/v1/participant/sessions/{world['probe']}/event-batches", probe_batch, 'forged-token')
        assert status == 403 and payload['code'] == 'session_unavailable', payload
        status, payload = exchange('recovery deleted proof', '/v1/participant/recovery', {
            'capability': 'recovery_named/v1', 'participant_code': '001', 'password': 'synthetic-password',
            'proof': proof_deleted, 'front_locked': False, **binding})
        assert status == 403 and payload['code'] == 'study_deleted', payload
        status, payload = exchange('recovery forged proof', '/v1/participant/recovery', {
            'capability': 'recovery_named/v1', 'participant_code': '001', 'password': 'synthetic-password',
            'proof': 'f' * 48, 'front_locked': False, **binding})
        assert status == 403 and payload['code'] == 'recovery_unavailable', payload
        serialized = json.dumps(exchanges)
        assert 'Live deletion study' not in serialized
        assert all('title' not in entry['payload'] and 'participant' not in entry['payload'] for entry in exchanges)
        evidence('live_server_exchanges.json', exchanges)

        # ---- the real native client is permanently stopped after the mark ----
        deleted_env = dict(shared, GEP_SHELL_PHASE='live_deleted', GEP_SHELL_SESSION=world['deleted'], GEP_SHELL_TOKEN=token_deleted,
                           GEP_SHELL_PROOF=proof_deleted, GEP_SHELL_EVENT_ID=str(uuid.uuid4()), GEP_SHELL_SEGMENT_ID=str(uuid.uuid4()))
        deleted = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=300,
                       env={**os.environ, 'GEP_SYNTHETIC_STORAGE': str(live_storage), **deleted_env})
        evidence('native_live_deleted_stdout.txt', {'stdout': deleted.stdout[-8000:], 'stderr': deleted.stderr[-4000:], 'exit': deleted.returncode})
        assert deleted.returncode == 0 and 'P03R09C_SHELL_VERIFIED' in deleted.stdout, deleted.stdout[-4000:] + deleted.stderr[-2000:]

        # ---- independent server-side and access-log cross-checks ------------
        database = verify.run_python(
            "import django, json, os\n"
            "django.setup()\n"
            "from core.models import Event, Session, StudyDeletion\n"
            "before = Session.objects.get(pk=os.environ['GEP_LIVE_BEFORE'])\n"
            "deleted = Session.objects.get(pk=os.environ['GEP_LIVE_DELETED'])\n"
            "row = StudyDeletion.objects.get(study_uuid=os.environ['GEP_LIVE_STUDY'])\n"
            "print('LIVE_DB', json.dumps({'before_events': Event.objects.filter(session=before).count(), "
            "'before_completion': bool(before.completion), 'deleted_events': Event.objects.filter(session=deleted).count(), "
            "'deleted_completion': bool(deleted.completion), 'state': row.state}))\n",
            extra_env={'GEP_LIVE_BEFORE': world['before'], 'GEP_LIVE_DELETED': world['deleted'], 'GEP_LIVE_STUDY': world['study']})
        facts = json.loads(re.search(r'LIVE_DB (\{.*\})', database.stdout).group(1))
        evidence('live_database_facts.json', facts)
        assert facts == {'before_events': 1, 'before_completion': True, 'deleted_events': 0, 'deleted_completion': False, 'state': 'marked'}, facts

        log = (verify.root / 'gunicorn.log').read_text()
        counts = {
            'deleted_batches': log.count(f'POST /v1/participant/sessions/{world["deleted"]}/event-batches'),
            'deleted_completions': log.count(f'POST /v1/participant/sessions/{world["deleted"]}/completion'),
            'before_batches': log.count(f'POST /v1/participant/sessions/{world["before"]}/event-batches'),
            'before_completions': log.count(f'POST /v1/participant/sessions/{world["before"]}/completion'),
            'probe_batches': log.count(f'POST /v1/participant/sessions/{world["probe"]}/event-batches'),
        }
        evidence('live_access_log_counts.json', counts)
        assert counts == {'deleted_batches': 1, 'deleted_completions': 0, 'before_batches': 1, 'before_completions': 1, 'probe_batches': 3}, counts
    finally:
        verify.stop_server()
