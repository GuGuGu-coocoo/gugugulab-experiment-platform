"""P03R09AR: local result export guards, save feedback and data-only retirement.

Real Godot processes (the real shell, the real independent local backend, real
SQLite and real file IO) plus a real Chrome run of the shipped bridge, SDK and
participation panel against real IndexedDB. Nothing here is mocked or skipped:
a missing engine, browser or Playwright runner fails the module, and the Chrome
check requires exactly one passed, non-skipped spec.

Evidence: one fresh session root under
``local_data/phase03_remediation_20260923/p03r09ar/<UTC stamp>-<random>/``; no
existing root is reused or overwritten.
"""
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'examples' / 'synthetic_experiment'
SPEC = 'tests/browser/remediation_finish_guards.spec.js'


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


def test_real_native_finish_guards_harness(evidence_root, evidence):
    """The real shell, local backend, SQLite and file IO under real Godot.

    Covers the save feedback without a finished local test, the chosen and
    default result directories, the cancelled save, a real unopenable target and
    a real read-only directory failure, an injected post-open write failure that
    must preserve the old target (injection clearly separated from the real disk
    faults), and the data-only retirement with its failure/cancel boundaries.
    """
    work = evidence_root / 'native_guards'
    storage = work / 'storage'
    storage_fault = work / 'storage_fault'
    results = work / 'results'
    for directory in (storage, storage_fault, results):
        directory.mkdir(parents=True)
    result = _run([_godot(), '--headless', '--path', str(PROJECT),
                   '--script', str(ROOT / 'tests' / 'native' / 'remediation_finish_guards_harness.gd')],
                  timeout=300, env={'GEP_SYNTHETIC_STORAGE': str(storage),
                                    'GEP_SYNTHETIC_STORAGE_FAULT': str(storage_fault),
                                    'GEP_SYNTHETIC_RESULTS': str(results)})
    assert 'P03R09AR_HARNESS_VERIFIED' in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    evidence('native_guards_stdout.txt', {'stdout': result.stdout[-4000:], 'exit': result.returncode})

    # Real SQLite success: one finished run with both durable writes.
    connection = sqlite3.connect(storage / 'local.sqlite')
    rows = connection.execute('SELECT id, value FROM runs').fetchall()
    connection.close()
    assert len(rows) == 1, rows
    value = json.loads(rows[0][1])
    assert value['state'] == 'finished_saved' and value['remote_status'] == 'unsupported', value['state']
    assert len(value['records']) == 2 and value['completion']['event_ids'] == [record['event_id'] for record in value['records']]
    assert value['checkpoint'] is None

    # Real file outcomes: the chosen and default files hold the full JSONL, the
    # injected failure left no temporary file behind, and the real failures
    # created nothing.
    chosen_lines = (results / 'chosen' / 'partial.jsonl').read_text().strip().splitlines()
    assert len(chosen_lines) == 2 and json.loads(chosen_lines[0])['payload']['trial_id'] == 't1'
    default_files = [name for name in os.listdir(results) if name.startswith('local-results-')]
    assert len(default_files) == 1, default_files
    assert len((results / default_files[0]).read_text().strip().splitlines()) == 2
    preserved = (results / 'preserved.jsonl').read_text()
    assert 'OLD-CONTENT' not in preserved and len(preserved.strip().splitlines()) == 2
    assert not any('.part-' in name for name in os.listdir(results)), 'a temporary file was left behind'
    assert not (results / 'readonly' / 'blocked.jsonl').exists()
    assert not (results / 'no-such-directory').exists()


def test_real_chrome_bridge_export_guard_and_data_only(evidence_root, evidence):
    """The shipped bridge/SDK/panel in real Chrome against real IndexedDB.

    The task driver serves the shipped browser modules with a local-preview
    application context and runs the spec with the Playwright runner. One
    passed, non-skipped test is required; the spec's own evidence must show no
    failed check, exactly one download (the legal one), zero requests to the API
    origin and the data-only retirement checks.
    """
    context = {
        'config_version': '1', 'protocol_version': 'gep/1', 'sdk_version': '0.1.0', 'purpose': 'synthetic',
        'api_url': f'http://127.0.0.1:{_free_port()}', 'instance_id': secrets.token_hex(16),
        'study_id': secrets.token_hex(16), 'release_id': 'preview', 'build_id': secrets.token_hex(16),
        'preview': True,
    }
    chrome = evidence_root / 'chrome'
    chrome.mkdir()
    job_path = chrome / 'job.json'
    job_path.write_text(json.dumps({'package_dir': str(ROOT / 'packages' / 'gec_web'),
                                    'evidence_dir': str(chrome), 'api_url': context['api_url'],
                                    'context': context}), encoding='utf-8')
    runner = ROOT / 'node_modules' / '.bin' / 'playwright'
    assert runner.exists(), 'the pinned Playwright runner is required (pnpm install --frozen-lockfile)'
    report = _run([str(runner), 'test', SPEC, '--reporter=json'], timeout=600,
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
    assert summary['failures'] == [], summary['failures']
    assert all(entry['ok'] for entry in summary['checks']), summary['failures']
    assert summary['api_requests'] == [], 'the local-only preview must not contact the server'
    assert summary['downloads'] == 1, 'only the legal download may produce a file: %s' % summary['downloads']
    assert len(summary['checks']) >= 20, 'the Chrome evidence is too thin: %d checks' % len(summary['checks'])
    scenarios = summary['scenarios']
    assert scenarios['success']['records'] == 2 and scenarios['success']['downloads'] == 1
    assert scenarios['locked']['error'] == 'results_export_unavailable' and scenarios['locked']['downloads'] == 1
    assert scenarios['remote_kind']['error'] == 'results_export_unavailable' and scenarios['remote_kind']['downloads'] == 1
    assert scenarios['data_only']['state'] == 'data_only' and scenarios['data_only']['records'] == 2
    assert scenarios['data_only']['download_error'] == 'results_export_unavailable'
    assert scenarios['data_only']['downloads'] == 1
    assert scenarios['panel']['actions_after'] == scenarios['panel']['actions_before']
