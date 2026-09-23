"""P03R09A: GEC save feedback, retired entry form and local-test result entry.

Real Godot processes (the exported program, the real shell and the real
independent local backend with real SQLite) plus a real Chrome run of the
exported Web build and the shipped browser modules. Nothing here is mocked or
skipped: a missing engine, browser or export template fails the module, and the
Chrome check requires exactly one passed, non-skipped spec.

Evidence: one fresh session root under
``local_data/phase03_remediation_20260923/p03r09a/<UTC stamp>-<random>/`` and one
fresh frozen build root under
``build/phase03_remediation_20260923/<UTC stamp>-<random>/web/``; no existing
root is reused or overwritten.
"""
import json
import hashlib
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'examples' / 'synthetic_experiment'
BUILD_BASE = ROOT / 'build' / 'phase03_remediation_20260923'
SPEC = 'tests/browser/remediation_finish.spec.js'
EXPECTED_TRIALS = [
    ('exp.rt', 't1', 'left'),
    ('exp.interaction', None, None),
    ('exp.rt', 't2', 'right'),
    ('exp.interaction', None, None),
]


def _godot():
    """The official local engine; missing tooling fails the test, never skips."""
    candidate = os.environ.get('GEP_GODOT_BIN') or shutil.which('godot')
    if candidate is None and Path('/Applications/Godot.app/Contents/MacOS/Godot').is_file():
        candidate = '/Applications/Godot.app/Contents/MacOS/Godot'
    if candidate is None or not Path(candidate).exists():
        pytest.fail('official local Godot engine not found (GEP_GODOT_BIN, PATH or /Applications/Godot.app)')
    return candidate


def _unique_build_root():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
    root = BUILD_BASE / stamp
    root.mkdir(parents=True, exist_ok=False)
    return root


def _free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _run(command, timeout, env=None, cwd=ROOT):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})})


def test_scientific_task_hash_unchanged():
    """The science task source is byte-identical to its frozen digest."""
    task = (PROJECT / 'task.gd').read_bytes()
    recorded = (ROOT / 'tests' / 'fixtures' / 'scientific_task.sha256').read_text().strip()
    assert hashlib.sha256(task).hexdigest() == recorded, 'the scientific task source changed'


def test_real_local_auto_run_finishes_with_durable_records(evidence_root, evidence):
    """The real ``--local`` program: two trials, finished_saved, real SQLite.

    The independent local backend is used (``local.sqlite`` exists, the native
    queue is absent), the finish is only saved with the completion set, and the
    explicit result file is not created by the automatic run.
    """
    work = evidence_root / 'local_auto'
    storage = work / 'storage'
    results = work / 'results'
    storage.mkdir(parents=True)
    results.mkdir(parents=True)
    result = _run([_godot(), '--headless', '--path', str(PROJECT), '--', '--local', '--synthetic-auto'],
                  timeout=300, env={'GEP_SYNTHETIC_STORAGE': str(storage),
                                    'GEP_SYNTHETIC_RESULTS': str(results)})
    assert result.returncode == 0, result.stdout + result.stderr
    done = next((line for line in result.stdout.splitlines() if line.startswith('SYNTHETIC_DONE ')), None)
    assert done, result.stdout + result.stderr
    status = json.loads(done.split(' ', 1)[1])
    assert status['state'] == 'finished_saved', status
    assert status['remote_status'] == 'unsupported', status
    assert 'remote_acknowledged' not in json.dumps(status), status
    assert (storage / 'local.sqlite').is_file(), 'the independent local backend did not write its own store'
    assert not (storage / 'queue.sqlite').exists(), 'the native backend store must not be used by --local'
    connection = sqlite3.connect(storage / 'local.sqlite')
    rows = connection.execute('SELECT id, value FROM runs').fetchall()
    connection.close()
    assert len(rows) == 1, rows
    value = json.loads(rows[0][1])
    assert value['state'] == 'finished_saved' and value['remote_status'] == 'unsupported'
    assert value['checkpoint'] is None
    records = value['records']
    assert len(records) == 4
    assert [(record['event_type'], record['payload'].get('trial_id'), record['payload'].get('choice'))
            for record in records] == EXPECTED_TRIALS
    assert [record['sequence'] for record in records] == [1, 2, 3, 4]
    assert records[0]['payload']['rt_ms'] == 321.5 and records[2]['payload']['rt_ms'] == 217.25
    assert records[0]['observed_time']['value'] == records[0]['payload']['rt_ms']
    assert records[1]['payload']['confidence'] is None
    assert records[1]['payload']['nested'] == {'changes': [{'from': None, 'to': '001'}], 'confirmed': False}
    assert len({record['segment_id'] for record in records}) == 1
    assert value['completion'] == {'event_ids': [record['event_id'] for record in records],
                                   'segment_ids': [records[0]['segment_id']]}
    assert list(results.iterdir()) == [], 'the automatic run must not write a result file'
    evidence('local_auto_status.json', {'status': status, 'records': records, 'completion': value['completion']})


def test_real_native_shell_harness(evidence_root, evidence):
    """The real shell and local backend under a real Godot process.

    Covers the retired entry form, the saved finish, the explicit JSONL save,
    the cancelled save, the failed export and a real disk failure on finish
    (read-only store) that must report ``storage_error`` and keep the records.
    """
    work = evidence_root / 'harness'
    storage = work / 'storage'
    storage_fault = work / 'storage_fault'
    results = work / 'results'
    for directory in (storage, storage_fault, results):
        directory.mkdir(parents=True)
    result = _run([_godot(), '--headless', '--path', str(PROJECT),
                   '--script', str(ROOT / 'tests' / 'native' / 'remediation_finish_harness.gd')],
                  timeout=300, env={'GEP_SYNTHETIC_STORAGE': str(storage),
                                    'GEP_SYNTHETIC_STORAGE_FAULT': str(storage_fault),
                                    'GEP_SYNTHETIC_RESULTS': str(results)})
    assert 'P03R09A_HARNESS_VERIFIED' in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    native_results = results / 'harness-results.jsonl'
    fault_results = results / 'harness-fault-results.jsonl'
    assert native_results.is_file() and fault_results.is_file()
    evidence('native_harness_stdout.txt', {'stdout': result.stdout[-4000:], 'exit': result.returncode})
    assert len(native_results.read_text().strip().splitlines()) == 2
    assert len(fault_results.read_text().strip().splitlines()) == 2


def test_real_chrome_local_preview_finish_download_and_storage_failure(evidence_root, evidence):
    """The real Web build in real Chrome against the shipped browser modules.

    The task driver exports the frozen Web build into a new unique build root,
    serves it with the preview application context, and runs the spec with the
    Playwright runner. One passed, non-skipped test is required; the spec's own
    evidence must show no failed check and zero requests to the API origin.
    """
    build_root = _unique_build_root()
    web = build_root / 'web'
    web.mkdir()
    export = _run([_godot(), '--headless', '--path', str(PROJECT), '--export-release', 'Web',
                   str(web / 'index.html')], timeout=600)
    assert export.returncode == 0, export.stdout + export.stderr
    assert (web / 'index.wasm').is_file() and (web / 'index.pck').is_file(), 'the Web export is incomplete'
    chrome = evidence_root / 'chrome'
    chrome.mkdir()
    context = {
        'config_version': '1', 'protocol_version': 'gep/1', 'sdk_version': '0.1.0', 'purpose': 'synthetic',
        'api_url': f'http://127.0.0.1:{_free_port()}', 'instance_id': secrets.token_hex(16),
        'study_id': secrets.token_hex(16), 'release_id': 'preview', 'build_id': secrets.token_hex(16),
        'preview': True,
    }
    job_path = chrome / 'job.json'
    job_path.write_text(json.dumps({'export_dir': str(web), 'package_dir': str(ROOT / 'packages' / 'gec_web'),
                                    'evidence_dir': str(chrome), 'api_url': context['api_url'],
                                    'context': context}), encoding='utf-8')
    report = _run([str(ROOT / 'node_modules' / '.bin' / 'playwright'), 'test', SPEC, '--reporter=json'],
                  timeout=900, env={'GEP_REMEDIATION_JOB': str(job_path)})
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
    assert summary['failures'] == [], summary['failures']
    assert all(entry['ok'] for entry in summary['checks']), summary['failures']
    assert summary['api_requests'] == [], 'the local-only preview must not contact the server'
    assert len(summary['checks']) >= 25, 'the Chrome evidence is too thin: %d checks' % len(summary['checks'])
    scenarios = summary['scenarios']
    assert scenarios['success']['records'] == 4 and scenarios['success']['declared'] == 4
    assert scenarios['success']['download']['lines'] == 4
    assert scenarios['failed_finish']['retry'] == 'finished_saved'
    assert scenarios['failed_finish']['exportable_lines'] == 4
    assert scenarios['ui_failure']['records'] == 2

    # The two result structures reconcile value for value: both JSONL documents
    # keep the original envelopes, the same non-timing payload values, and the
    # observed time equal to the recorded RT. The native harness stops after the
    # first trial boundary (two records); the Web run records both boundaries.
    native_lines = [json.loads(line) for line in
                    (evidence_root / 'harness' / 'results' / 'harness-results.jsonl').read_text().strip().splitlines()]
    web_lines = [json.loads(line) for line in
                 (chrome / 'web-local-results.jsonl').read_text().strip().splitlines()]
    assert len(native_lines) == 2 and len(web_lines) == 4
    for index, native in enumerate(native_lines):
        browser = web_lines[index]
        assert native['event_type'] == browser['event_type'] == EXPECTED_TRIALS[index][0]
        assert native['schema_id'] == browser['schema_id'] and native['schema_version'] == browser['schema_version']
        assert native['sequence'] == browser['sequence'] == index + 1
        assert native['payload'].get('trial_id') == browser['payload'].get('trial_id') == EXPECTED_TRIALS[index][1]
        assert native['payload'].get('choice') == browser['payload'].get('choice') == EXPECTED_TRIALS[index][2]
        if index == 0:
            assert native['payload']['rt_ms'] == native['observed_time']['value']
        if index == 1:
            assert native['payload'] == browser['payload'], 'the deterministic interaction payload changed'
    for index in (0, 2):
        assert web_lines[index]['payload']['rt_ms'] == web_lines[index]['observed_time']['value']
    assert [(line['event_type'], line['payload'].get('trial_id'), line['payload'].get('choice'))
            for line in web_lines] == EXPECTED_TRIALS
    assert native_lines[0]['payload']['rt_ms'] == 321.5 and native_lines[0]['payload']['trial_id'] == 't1'
