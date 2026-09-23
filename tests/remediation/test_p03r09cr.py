"""P03R09CR: atomic failure export, read-only Windows verification input and a
bounded cross-platform fault-server handshake.

Real checks, none mocked or skipped:

* the real Godot native shell exports through its real SQLite store, and an
  external ``RLIMIT_FSIZE`` really truncates the write: the export must report an
  error, keep the previous target bytes, keep the durable queue row unchanged and
  leave no temporary file; the next unrestricted export must succeed with the
  unchanged queue;
* the real GUI save-dialog callback and the automation entry share that one
  atomic path (success, blocked target, cancel); a real receipt or front-locked
  row with stale failure counters offers no failure export; and the persisted
  retry counter is displayed exactly (initial, progress reset, 1/2/3);
* ``tools/remediation_gec.py`` refuses to guess the expected Windows program,
  copies the read-only source into one unique run copy, and its ``FaultServer``
  handshake is bounded for a real success, a silent child, an invalid ready line
  and an early exit, always reaping the owned child. On this platform
  ``--verify-windows`` returns the non-Windows refusal instead of a claimed pass.

Evidence: one fresh session root under
``local_data/phase03_remediation_20260923/p03r09cr/<UTC stamp>-<random>/``; the
read-only sources and every earlier failure root are never touched.
"""
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

try:
    import resource
except ImportError:  # pragma: no cover - the Windows host never runs this probe
    resource = None

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'examples' / 'synthetic_experiment'
HARNESS = ROOT / 'tests' / 'native' / 'remediation_shell_harness.gd'
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'tools'))
import remediation_gec  # noqa: E402

from test_p03r09b import _godot, _run  # noqa: E402,F401


def _harness_env(storage, phase, **extra):
    env = {'GEP_SYNTHETIC_STORAGE': str(storage),
           'GEP_SHELL_INSTANCE': secrets.token_hex(16), 'GEP_SHELL_STUDY': secrets.token_hex(16),
           'GEP_SHELL_BUILD': secrets.token_hex(16), 'GEP_SHELL_PHASE': phase}
    env.update(extra)
    return env


def _marker(stdout, name):
    match = re.search(r'%s (\{.*\})' % re.escape(name), stdout)
    assert match, stdout[-4000:]
    return json.loads(match.group(1))


def _assert_reaped(error, timeout=10.0):
    """The owned fault-server child must be gone after a refused handshake."""
    match = re.search(r'pid (\d+)', str(error))
    assert match, str(error)
    pid = int(match.group(1))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            break
        time.sleep(0.1)
    pytest.fail(f'the owned fault-server child was not reaped: pid {pid}')


@pytest.fixture
def fault_server():
    server = remediation_gec.FaultServer(ROOT)
    try:
        yield server
    finally:
        server.stop()


def test_scientific_task_hash_unchanged():
    """The science task source is byte-identical to its frozen digest."""
    import hashlib
    task = (PROJECT / 'task.gd').read_bytes()
    recorded = (ROOT / 'tests' / 'fixtures' / 'scientific_task.sha256').read_text().strip()
    assert hashlib.sha256(task).hexdigest() == recorded, 'the scientific task source changed'


def test_real_restricted_write_never_reports_success(evidence_root, evidence):
    """A real file-size limit must not become a reported export.

    The parent sets ``RLIMIT_FSIZE`` for the constrained Godot child (a real
    write failure after a successful open), exactly like the review probe; the
    document is far larger than the limit. The export must fail without touching
    the existing target, the durable row must stay byte-identical, and the next
    unrestricted export must reproduce the control document.
    """
    if resource is None:
        pytest.skip('RLIMIT_FSIZE is a POSIX facility; the real evidence runs on macOS/Unix')
    godot = _godot()
    storage = evidence_root / 'export' / 'storage'
    storage.mkdir(parents=True)
    shared = {'GEP_SHELL_INSTANCE': secrets.token_hex(16), 'GEP_SHELL_STUDY': secrets.token_hex(16),
              'GEP_SHELL_BUILD': secrets.token_hex(16), 'GEP_DELIVERY_FAULT_URL': 'http://127.0.0.1:9'}

    control = evidence_root / 'export' / 'control.json'
    prepared = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=300,
                    env=_harness_env(storage, 'export_prepare', GEP_SHELL_EXPORT_TARGET=str(control),
                                     GEP_SHELL_EXPORT_PAYLOAD='262144', **shared))
    evidence('export_prepare_stdout.txt', {'stdout': prepared.stdout[-8000:], 'stderr': prepared.stderr[-4000:], 'exit': prepared.returncode})
    assert prepared.returncode == 0 and 'P03R09C_EXPORT_PREPARED' in prepared.stdout, prepared.stdout[-4000:] + prepared.stderr[-2000:]
    facts = _marker(prepared.stdout, 'P03R09C_EXPORT_PREPARED')
    assert facts['bytes'] > 262144, facts
    session_id = facts['session_id']

    original = b'{"original_recovery_export":true}\n'
    limited_target = evidence_root / 'export' / 'existing.json'
    limited_target.write_bytes(original)

    def constrained_child():
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))

    limited = subprocess.run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)],
                             env={**os.environ, **_harness_env(storage, 'export_limited', GEP_SHELL_EXPORT_TARGET=str(limited_target),
                                                               GEP_SHELL_SESSION=session_id, **shared)},
                             capture_output=True, text=True, timeout=300, preexec_fn=constrained_child)
    evidence('export_limited_stdout.txt', {'stdout': limited.stdout[-8000:], 'stderr': limited.stderr[-4000:], 'exit': limited.returncode})
    assert limited.returncode == 0 and 'P03R09C_EXPORT_RESULT' in limited.stdout, limited.stdout[-4000:] + limited.stderr[-2000:]
    observed = _marker(limited.stdout, 'P03R09C_EXPORT_RESULT')
    assert observed['result'].get('error') and observed['result'].get('state') != 'exported', observed
    assert observed['target_unchanged'] is True, observed
    assert observed['leftovers'] == [], observed
    assert limited_target.read_bytes() == original, 'the restricted export rewrote the existing target'
    assert observed['queue_sha'] == facts['queue_sha'], 'the failed export changed the durable queue'

    recovered_target = evidence_root / 'export' / 'recovered.json'
    recovered = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=300,
                     env=_harness_env(storage, 'export_recover', GEP_SHELL_EXPORT_TARGET=str(recovered_target),
                                      GEP_SHELL_SESSION=session_id, **shared))
    evidence('export_recover_stdout.txt', {'stdout': recovered.stdout[-8000:], 'stderr': recovered.stderr[-4000:], 'exit': recovered.returncode})
    assert recovered.returncode == 0 and 'P03R09C_EXPORT_RECOVERED' in recovered.stdout, recovered.stdout[-4000:] + recovered.stderr[-2000:]
    recovered_facts = _marker(recovered.stdout, 'P03R09C_EXPORT_RECOVERED')
    assert recovered_facts['queue_sha'] == facts['queue_sha'] == observed['queue_sha'], recovered_facts
    assert recovered_target.read_bytes() == control.read_bytes(), 'a later export did not reproduce the control document'
    text = recovered_target.read_text(encoding='utf-8')
    assert 'SYNTHETIC-TOKEN-MUST-NOT-BE-EXPORTED' not in text, 'the export leaked the session token'
    assert 'SYNTHETIC-PROOF-MUST-NOT-BE-EXPORTED' not in text, 'the export leaked the device proof'
    assert list(evidence_root.rglob('*.part-*')) == [], 'a temporary export file was left behind'


def test_real_gui_export_uses_the_same_atomic_path(evidence_root, evidence):
    """The real FileDialog callback and the automation entry write identically."""
    godot = _godot()
    storage = evidence_root / 'gui' / 'storage'
    storage.mkdir(parents=True)
    result = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=300,
                  env=_harness_env(storage, 'export_gui', GEP_DELIVERY_FAULT_URL='http://127.0.0.1:9'))
    evidence('export_gui_stdout.txt', {'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:], 'exit': result.returncode})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert 'P03R09C_EXPORT_GUI' in result.stdout and 'P03R09C_SHELL_VERIFIED' in result.stdout, result.stdout[-4000:]
    assert 'P03R09C_SHELL_FAILED' not in result.stdout, result.stdout[-4000:]


def test_real_receipt_and_front_locked_offer_no_failure_export(evidence_root, evidence):
    """Stale counters must not beat a stored receipt or the front lock."""
    godot = _godot()
    storage = evidence_root / 'surface' / 'storage'
    storage.mkdir(parents=True)
    result = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=300,
                  env=_harness_env(storage, 'receipt_surface', GEP_DELIVERY_FAULT_URL='http://127.0.0.1:9'))
    evidence('receipt_surface_stdout.txt', {'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:], 'exit': result.returncode})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert 'P03R09C_RECEIPT_SURFACE' in result.stdout and 'P03R09C_SHELL_VERIFIED' in result.stdout, result.stdout[-4000:]
    assert 'P03R09C_SHELL_FAILED' not in result.stdout, result.stdout[-4000:]


def test_real_persisted_retry_counts_and_progress_reset(evidence_root, evidence, fault_server):
    """The displayed count always equals the persisted ``retry_failures``."""
    godot = _godot()
    storage = evidence_root / 'counting' / 'storage'
    storage.mkdir(parents=True)
    result = _run([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=300,
                  env=_harness_env(storage, 'counting', GEP_DELIVERY_FAULT_URL=fault_server.url))
    evidence('counting_stdout.txt', {'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:], 'exit': result.returncode})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert 'P03R09C_COUNTING' in result.stdout and 'P03R09C_SHELL_VERIFIED' in result.stdout, result.stdout[-4000:]
    assert 'P03R09C_SHELL_FAILED' not in result.stdout, result.stdout[-4000:]


def test_windows_program_selection_never_guesses(evidence_root):
    """Explicit or manifest program only; ambiguous or unsafe choices fail."""
    source = evidence_root / 'windows_source'
    source.mkdir()
    (source / 'experiment.exe').write_bytes(b'MZ experiment')
    (source / 'console.exe').write_bytes(b'MZ console')
    (source / 'helper.exe').write_bytes(b'MZ helper')
    (source / 'readme.txt').write_text('not a program')
    with pytest.raises(remediation_gec.VerificationError, match='no --program'):
        remediation_gec.resolve_windows_program(source, None)
    resolved = remediation_gec.resolve_windows_program(source, 'console.exe')
    assert resolved == (source / 'console.exe').resolve(), resolved
    (source / 'gec_package_manifest.json').write_text(json.dumps({'program': 'experiment.exe'}), encoding='utf-8')
    assert remediation_gec.resolve_windows_program(source, None) == (source / 'experiment.exe').resolve()
    (source / 'gec_package_manifest.json').write_text(json.dumps({'programs': ['experiment.exe', 'helper.exe']}), encoding='utf-8')
    with pytest.raises(remediation_gec.VerificationError, match='exactly one'):
        remediation_gec.resolve_windows_program(source, None)
    with pytest.raises(remediation_gec.VerificationError, match='outside the package root'):
        remediation_gec.resolve_windows_program(source, '../escape.exe')
    with pytest.raises(remediation_gec.VerificationError, match='not a Windows executable'):
        remediation_gec.resolve_windows_program(source, 'readme.txt')
    with pytest.raises(remediation_gec.VerificationError, match='does not exist'):
        remediation_gec.resolve_windows_program(source, 'missing.exe')


def test_windows_run_copy_is_unique_and_source_stays_read_only(evidence_root):
    """The source package is hashed input; only the unique copy is written."""
    source = evidence_root / 'readonly_source'
    source.mkdir()
    (source / 'app.exe').write_bytes(b'MZ app')
    (source / 'data.pck').write_bytes(b'PACK bytes')
    before = remediation_gec.package_digests(source)
    assert sorted(before) == ['app.exe', 'data.pck']
    first_run = evidence_root / 'run_one'
    first_run.mkdir()
    first = remediation_gec.copy_package_for_run(source, first_run)
    assert remediation_gec.package_digests(first) == before
    (first / 'connection.json').write_text('{"synthetic": true}', encoding='utf-8')
    assert remediation_gec.package_digests(source) == before, 'the read-only source changed'
    changed = sorted(name for name, digest in remediation_gec.package_digests(first).items() if before.get(name) != digest)
    assert changed == ['connection.json'], changed
    second_run = evidence_root / 'run_two'
    second_run.mkdir()
    second = remediation_gec.copy_package_for_run(source, second_run)
    assert second != first and remediation_gec.package_digests(second) == before


def test_verify_windows_refuses_off_platform(evidence_root, capsys):
    """A local non-Windows run must never claim a Windows pass."""
    if sys.platform.startswith('win'):
        pytest.skip('the non-Windows guard only exists off Windows; R11 runs the real host')
    status = remediation_gec.verify_windows(str(evidence_root), None, None)
    assert status == 2, status
    assert 'must run on Windows' in capsys.readouterr().out


def test_fault_server_handshake_is_bounded_and_reaps(evidence_root, evidence):
    """Real success and real refusal paths, all bounded and without leftovers."""
    # A real success: the shipped fault app reports its real port over real HTTP.
    server = remediation_gec.FaultServer(ROOT)
    try:
        assert server.port > 0 and server.url.startswith('http://127.0.0.1:')
        assert isinstance(server.state().get('sessions'), dict)
    finally:
        server.stop()
    assert server.process.poll() is not None, 'the owned fault server was not reaped'
    evidence('fault_server_success.json', {'port': server.port, 'exit': server.process.poll()})

    silent = evidence_root / 'silent_fault.py'
    silent.write_text('import time\ntime.sleep(120)\n', encoding='utf-8')
    started = time.monotonic()
    with pytest.raises(remediation_gec.VerificationError, match='did not report its port') as refusal:
        remediation_gec.FaultServer(ROOT, timeout=1.0, command=[sys.executable, str(silent)])
    elapsed = time.monotonic() - started
    assert elapsed < 15, elapsed
    _assert_reaped(refusal.value)

    invalid = evidence_root / 'invalid_fault.py'
    invalid.write_text("print('not-json', flush=True)\nimport time\ntime.sleep(120)\n", encoding='utf-8')
    with pytest.raises(remediation_gec.VerificationError, match='unusable line') as invalid_refusal:
        remediation_gec.FaultServer(ROOT, timeout=5.0, command=[sys.executable, str(invalid)])
    _assert_reaped(invalid_refusal.value)

    with pytest.raises(remediation_gec.VerificationError, match='exited before reporting its port') as exit_refusal:
        remediation_gec.FaultServer(ROOT, timeout=5.0, command=[sys.executable, '-c', 'pass'])
    _assert_reaped(exit_refusal.value)
    evidence('fault_server_refusals.json', {'timeout_seconds': round(elapsed, 3), 'cases': ['timeout', 'invalid line', 'early exit']})

    source = (ROOT / 'tools' / 'remediation_gec.py').read_text(encoding='utf-8')
    assert not re.search(r'\bselect\.select\b', source), 'the tool still waits on a process pipe with select'
    assert not re.search(r'^import select$', source, re.M), 'the tool still imports select'
