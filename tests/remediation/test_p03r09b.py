"""P03R09B: persistent per-session delivery rounds, ACK progress and safe pause.

Real Godot processes (the real native backend with real SQLite transactions) and
a real Chrome run of the shipped bridge/SDK against real IndexedDB, both talking
real HTTP to the synthetic fault server
(``tests/remediation/delivery_fault_app.py``). Nothing here is mocked or skipped:
a missing engine, browser or runner fails the module, and the Chrome check
requires exactly one passed, non-skipped spec.

Evidence: one fresh session root under
``local_data/phase03_remediation_20260923/p03r09b/<UTC stamp>-<random>/``; no
existing root is reused or overwritten.
"""
import json
import hashlib
import os
import select
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'examples' / 'synthetic_experiment'
FAULT_APP = ROOT / 'tests' / 'remediation' / 'delivery_fault_app.py'
HARNESS = ROOT / 'tests' / 'native' / 'remediation_delivery_harness.gd'
SPEC = 'tests/browser/remediation_delivery.spec.js'


def _godot():
    """The official local engine; missing tooling fails the test, never skips."""
    candidate = os.environ.get('GEP_GODOT_BIN') or shutil.which('godot')
    if candidate is None and Path('/Applications/Godot.app/Contents/MacOS/Godot').is_file():
        candidate = '/Applications/Godot.app/Contents/MacOS/Godot'
    if candidate is None or not Path(candidate).exists():
        pytest.fail('official local Godot engine not found (GEP_GODOT_BIN, PATH or /Applications/Godot.app)')
    return candidate


def _free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _run(command, timeout, env=None, cwd=ROOT):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})})


class FaultClient:
    """The synthetic fault server's control channel (test driver only)."""

    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'

    def _post(self, path, body):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode('utf-8'),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))

    def script(self, sessions):
        return self._post('/control/script', {'sessions': sessions})

    def register(self, session):
        return self._post('/control/session', session)

    def state(self):
        return self._get('/control/state')

    def session(self, session_id):
        return self.state().get('sessions', {}).get(session_id, {})


class FaultServer:
    """A real fault server subprocess on a real loopback socket."""

    def __init__(self, cwd):
        self.process = subprocess.Popen([sys.executable, str(FAULT_APP), '--port', '0'],
                                        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready, _, _ = select.select([self.process.stdout], [], [], 30)
        if not ready:
            self.process.kill()
            pytest.fail('the fault server did not report its port')
        line = self.process.stdout.readline().strip()
        try:
            self.port = int(json.loads(line)['port'])
        except (ValueError, KeyError, TypeError):
            self.process.kill()
            pytest.fail(f'the fault server reported an unusable line: {line!r}')
        self.client = FaultClient(self.port)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=15)


@pytest.fixture
def fault_server():
    server = FaultServer(ROOT)
    try:
        yield server
    finally:
        server.stop()


def test_scientific_task_hash_unchanged():
    """The science task source is byte-identical to its frozen digest."""
    task = (PROJECT / 'task.gd').read_bytes()
    recorded = (ROOT / 'tests' / 'fixtures' / 'scientific_task.sha256').read_text().strip()
    assert hashlib.sha256(task).hexdigest() == recorded, 'the scientific task source changed'


def test_real_native_delivery_rounds(evidence_root, evidence, fault_server):
    """The real native backend, real SQLite and real HTTP.

    Phase ``main`` covers the counting rules, the durable reopen, completion-only
    failures, invalid ACK, multi-session isolation, Retry-After, the 32-attempt
    budget, permanent authorization and study_deleted terminals, authorized
    recovery and a lost ACK. Phase ``kill`` starts a round against a delayed
    response and is killed while it is inflight; phase ``resume`` proves the
    interrupted round is outcome_unknown (never a failure), the counts survive,
    and an old queue record only gains the missing defaults.
    """
    godot = _godot()
    storage = evidence_root / 'native' / 'storage'
    storage.mkdir(parents=True)
    env = {'GEP_DELIVERY_FAULT_URL': fault_server.url, 'GEP_SYNTHETIC_STORAGE': str(storage)}

    main = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)],
                timeout=420, env=env)
    evidence('native_main_stdout.txt', {'stdout': main.stdout[-8000:], 'stderr': main.stderr[-4000:], 'exit': main.returncode})
    assert main.returncode == 0, main.stdout[-4000:] + main.stderr[-2000:]
    assert 'P03R09B_HARNESS_VERIFIED' in main.stdout, main.stdout[-4000:] + main.stderr[-2000:]
    assert 'P03R09B_HARNESS_FAILED' not in main.stdout, main.stdout[-4000:]

    # ---- a real process kill while one round is inflight --------------------
    kill_env = dict(env, GEP_DELIVERY_PHASE='kill')
    kill = subprocess.Popen([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)],
                            cwd=ROOT, env={**os.environ, **kill_env}, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    killed_session = None
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if kill.poll() is not None:
            break
        for session_id, item in fault_server.client.state().get('sessions', {}).items():
            if item.get('in_flight'):
                killed_session = session_id
                break
        if killed_session:
            break
        time.sleep(0.2)
    if killed_session is None:
        kill.kill()
        out, _ = kill.communicate(timeout=30)
        pytest.fail('the kill phase never reached an in-flight round: ' + (out or '')[-2000:])
    kill.kill()
    killed_output, _ = kill.communicate(timeout=30)
    assert 'P03R09B_KILL_NOT_KILLED' not in (killed_output or ''), 'the kill phase was never killed while the round was inflight'
    evidence('native_kill_stdout.txt', {'stdout': (killed_output or '')[-4000:], 'session_id': killed_session})

    # ---- the old-queue/partial fixtures and the resume phase ----------------
    old_session = str(uuid.uuid4())
    old_token = secrets.token_hex(16)
    old_binding = {'session_id': old_session, 'token': old_token, 'instance_id': secrets.token_hex(16),
                   'study_id': secrets.token_hex(16), 'release_id': 'release-1', 'build_id': secrets.token_hex(16),
                   'proof': 'old-proof'}
    registered = fault_server.client.register(old_binding)
    assert registered.get('session_id') == old_session, registered
    partial_session = str(uuid.uuid4())
    partial_token = secrets.token_hex(16)
    partial_binding = {'session_id': partial_session, 'token': partial_token, 'instance_id': secrets.token_hex(16),
                       'study_id': secrets.token_hex(16), 'release_id': 'release-1', 'build_id': secrets.token_hex(16),
                       'proof': 'partial-proof'}
    registered = fault_server.client.register(partial_binding)
    assert registered.get('session_id') == partial_session, registered
    resume_env = dict(env, GEP_DELIVERY_PHASE='resume', GEP_DELIVERY_KILL_SESSION=killed_session,
                      GEP_DELIVERY_OLD_SESSION=old_session, GEP_DELIVERY_OLD_TOKEN=old_token,
                      GEP_DELIVERY_OLD_INSTANCE=old_binding['instance_id'], GEP_DELIVERY_OLD_STUDY=old_binding['study_id'],
                      GEP_DELIVERY_OLD_RELEASE=old_binding['release_id'], GEP_DELIVERY_OLD_BUILD=old_binding['build_id'],
                      GEP_DELIVERY_PARTIAL_SESSION=partial_session, GEP_DELIVERY_PARTIAL_TOKEN=partial_token,
                      GEP_DELIVERY_PARTIAL_INSTANCE=partial_binding['instance_id'], GEP_DELIVERY_PARTIAL_STUDY=partial_binding['study_id'],
                      GEP_DELIVERY_PARTIAL_RELEASE=partial_binding['release_id'], GEP_DELIVERY_PARTIAL_BUILD=partial_binding['build_id'])
    resume = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)],
                  timeout=420, env=resume_env)
    evidence('native_resume_stdout.txt', {'stdout': resume.stdout[-8000:], 'stderr': resume.stderr[-4000:], 'exit': resume.returncode})
    assert resume.returncode == 0, resume.stdout[-4000:] + resume.stderr[-2000:]
    assert 'P03R09B_HARNESS_VERIFIED' in resume.stdout, resume.stdout[-4000:] + resume.stderr[-2000:]

    # ---- a real kill after the ACK commit but before the completion ---------
    kill_ack_env = dict(env, GEP_DELIVERY_PHASE='kill_ack')
    kill_ack = subprocess.Popen([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)],
                                cwd=ROOT, env={**os.environ, **kill_ack_env}, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
    marker = storage / 'kill_ack' / 'kill_ack_session.txt'
    kill_ack_session = None
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if kill_ack.poll() is not None:
            break
        if marker.exists():
            reported = marker.read_text(encoding='utf-8').strip()
            if reported:
                kill_ack_session = reported
                break
        time.sleep(0.2)
    if kill_ack_session is None:
        kill_ack.kill()
        out, _ = kill_ack.communicate(timeout=30)
        pytest.fail('the kill-ack phase never reported its session: ' + (out or '')[-2000:])
    # The completion request proves the batch ACK transaction was already
    # committed; the delayed response keeps the round outstanding for the kill.
    deadline = time.monotonic() + 120
    received = False
    while time.monotonic() < deadline:
        if fault_server.client.session(kill_ack_session).get('completion_requests', 0) >= 1:
            received = True
            break
        time.sleep(0.2)
    if not received:
        kill_ack.kill()
        out, _ = kill_ack.communicate(timeout=30)
        pytest.fail('the kill-ack completion request never reached the server: ' + (out or '')[-2000:])
    kill_ack.kill()
    ack_output, _ = kill_ack.communicate(timeout=30)
    assert 'P03R09B_KILL_ACK_NOT_KILLED' not in (ack_output or ''), 'the kill-ack phase was never killed while the completion was outstanding'
    evidence('native_kill_ack_stdout.txt', {'stdout': (ack_output or '')[-4000:], 'session_id': kill_ack_session})

    resume_ack_env = dict(env, GEP_DELIVERY_PHASE='resume_ack', GEP_DELIVERY_KILL_ACK_SESSION=kill_ack_session)
    resume_ack = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)],
                      timeout=420, env=resume_ack_env)
    evidence('native_resume_ack_stdout.txt', {'stdout': resume_ack.stdout[-8000:], 'stderr': resume_ack.stderr[-4000:], 'exit': resume_ack.returncode})
    assert resume_ack.returncode == 0, resume_ack.stdout[-4000:] + resume_ack.stderr[-2000:]
    assert 'P03R09B_HARNESS_VERIFIED' in resume_ack.stdout, resume_ack.stdout[-4000:] + resume_ack.stderr[-2000:]

    # ---- independent cross-checks of the real HTTP evidence -----------------
    state = fault_server.client.state()
    evidence('native_fault_state.json', state)
    killed = state.get('sessions', {}).get(killed_session, {})
    assert killed.get('batch_requests') == 2, killed
    assert len(killed.get('events', [])) == 2, killed
    completions = [entry.get('response') for entry in state.get('requests', [])
                   if entry.get('kind') == 'completion' and entry.get('session_id') == killed_session]
    assert completions == ['fail', 'complete'], completions
    old = state.get('sessions', {}).get(old_session, {})
    assert old.get('batch_requests') == 1 and len(old.get('events', [])) == 1, old
    partial = state.get('sessions', {}).get(partial_session, {})
    assert partial.get('batch_requests') == 1 and len(partial.get('events', [])) == 1, partial
    ack_after_kill = state.get('sessions', {}).get(kill_ack_session, {})
    assert ack_after_kill.get('batch_requests') == 1, ack_after_kill
    assert len(ack_after_kill.get('events', [])) == 2, ack_after_kill
    ack_completions = [entry.get('response') for entry in state.get('requests', [])
                       if entry.get('kind') == 'completion' and entry.get('session_id') == kill_ack_session]
    assert ack_completions == ['delay', 'complete'], ack_completions
    ackfail_session = None
    for line in main.stdout.splitlines():
        if line.startswith('P03R09B_ACKFAIL '):
            ackfail_session = line.split()[-1]
    assert ackfail_session, 'the real SQLite ACK-failure phase reported no session'
    ackfail = state.get('sessions', {}).get(ackfail_session, {})
    assert ackfail.get('batch_requests') == 2 and len(ackfail.get('events', [])) == 2, ackfail


def test_real_chrome_delivery_rounds(evidence_root, evidence, fault_server):
    """The shipped bridge and SDK in real Chrome against real IndexedDB and HTTP.

    The task driver serves the shipped browser modules with the synthetic fault
    server as the API origin and runs the spec with the Playwright runner. One
    passed, non-skipped test is required; the spec's own evidence must show no
    failed check and the full scenario set.
    """
    context = {
        'config_version': '1', 'protocol_version': 'gep/1', 'sdk_version': '0.1.0', 'purpose': 'synthetic',
        'api_url': fault_server.url, 'instance_id': secrets.token_hex(16),
        'study_id': secrets.token_hex(16), 'release_id': 'release-1', 'build_id': secrets.token_hex(16),
    }
    chrome = evidence_root / 'chrome'
    chrome.mkdir()
    job_path = chrome / 'job.json'
    job_path.write_text(json.dumps({'package_dir': str(ROOT / 'packages' / 'gec_web'),
                                    'evidence_dir': str(chrome), 'api_url': context['api_url'],
                                    'context': context}), encoding='utf-8')
    runner = ROOT / 'node_modules' / '.bin' / 'playwright'
    assert runner.exists(), 'the pinned Playwright runner is required (pnpm install --frozen-lockfile)'
    report = _run([str(runner), 'test', SPEC, '--reporter=json'], timeout=900,
                  env={'GEP_REMEDIATION_JOB': str(job_path)})
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
    assert all(entry['ok'] for entry in summary['checks']), summary['failures']
    assert len(summary['checks']) >= 150, 'the Chrome evidence is too thin: %d checks' % len(summary['checks'])
    scenarios = summary['scenarios']
    for name in ('basic', 'acks', 'storage', 'multi', 'schedule', 'policy', 'refusal', 'terminal',
                 'active_deleted', 'progress_deleted', 'malformed', 'offline', 'oldqueue', 'partialqueue', 'kill_ack'):
        assert name in scenarios, f'the {name} scenario is missing'
    assert scenarios['basic']['retry_failures'] == 3, scenarios['basic']
    assert scenarios['schedule']['attempts'] == 32, scenarios['schedule']
    assert scenarios['terminal']['export_records'] == 2, scenarios['terminal']
    assert scenarios['oldqueue']['attempts'] == 8, scenarios['oldqueue']
    assert scenarios['progress_deleted']['ack_progress'] == 1, scenarios['progress_deleted']
    assert scenarios['kill_ack']['batch_requests'] == 1 and scenarios['kill_ack']['events'] == 2, scenarios['kill_ack']
    fault_state = fault_server.client.state()
    evidence('chrome_fault_state.json', fault_state)
    assert fault_state.get('requests'), 'the Chrome run made no real HTTP request'
