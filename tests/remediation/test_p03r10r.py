"""P03R10R: bounded value guard, real Web defaults path, real server refusal.

Real checks, none mocked or skipped:

* the P03R10R ``cycles`` harness phase on a real Godot process and the real
  independent local backend (real SQLite): direct/indirect/mixed container
  cycles, the public ``MAX_VALUE_DEPTH`` boundary, shared-but-acyclic values,
  guarded schema configuration, bounded details without payload echo, and
  record/commit/finish recovery after a refusal and after a real SQLite
  refusal, independently re-read from the durable store;
* the ``gep_negative`` harness phase against a real isolated Django server and
  fresh database: a well-shaped but unregistered schema id/version is accepted
  by the client shape check, refused by the real server, stored in no raw event
  row, and the original local record identity/pending data stay;
* the ``web_demo_bootstrap.gd`` demo inside a test-only copy of the real
  project, exported to real Web and driven in real Chrome: the real GDScript
  wrapper (not JavaScript) performs configure_defaults, fields/provider short
  records, the explicit four-parameter override and post-record source
  mutations, and the same golden is reconciled against real IndexedDB and the
  downloaded JSONL; the direct cycle and over-deep input add no pseudo event;
* the scientific task source stays byte-identical to its frozen digest.

Evidence: one fresh session root under
``local_data/phase03_remediation_20260923/p03r10r/<UTC stamp>-<random>/`` and
one fresh test-only project copy under
``build/phase03_remediation_20260923/<stamp>/``; no existing root is reused or
overwritten and the original project files are never modified.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'examples' / 'synthetic_experiment'
HARNESS = ROOT / 'tests' / 'native' / 'remediation_defaults_harness.gd'
DEMO_BOOTSTRAP = ROOT / 'examples' / 'data_defaults_demo' / 'web_demo_bootstrap.gd'
SPEC = 'tests/browser/remediation_defaults.spec.js'
PACKAGES = ROOT / 'packages' / 'gec_web'
BUILD_BASE = ROOT / 'build' / 'phase03_remediation_20260923'
DESCRIPTOR = PROJECT / 'descriptor.json'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'scientific_task.sha256'

INTERACTION = {'action': 'revise', 'selection': ['shape_a', 'shape_c'], 'confidence': None,
               'nested': {'changes': [{'from': None, 'to': '001'}], 'confirmed': False}}
CYCLE_KEYS = ['direct_dictionary', 'indirect_dictionary', 'direct_array', 'indirect_array',
              'mixed', 'deep', 'observed']
ENVELOPE_KEYS = {'protocol_version', 'event_id', 'session_id', 'segment_id', 'sequence', 'event_type',
                 'schema_id', 'schema_version', 'payload'}


def _godot():
    """The official local engine; missing tooling fails the test, never skips."""
    candidate = os.environ.get('GEP_GODOT_BIN') or shutil.which('godot')
    if candidate is None and Path('/Applications/Godot.app/Contents/MacOS/Godot').is_file():
        candidate = '/Applications/Godot.app/Contents/MacOS/Godot'
    if candidate is None or not Path(candidate).exists():
        pytest.fail('official local Godot engine not found (GEP_GODOT_BIN, PATH or /Applications/Godot.app)')
    return candidate


def _run(command, timeout, env=None, cwd=ROOT):
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})})


def _marker(stdout, name):
    match = re.search(r'%s (\{.*\})' % re.escape(name), stdout)
    assert match, stdout[-4000:]
    return json.loads(match.group(1))


def _unique_build_root():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
    root = BUILD_BASE / stamp
    root.mkdir(parents=True, exist_ok=False)
    return root


def _free_port():
    import socket
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _task_digest():
    return hashlib.sha256((PROJECT / 'task.gd').read_bytes()).hexdigest()


def _stored_session(storage):
    """One real local store read (``local.sqlite`` rows) for the cycle phase."""
    connection = sqlite3.connect(f'file:{storage / "local.sqlite"}?mode=ro', uri=True, timeout=20)
    try:
        rows = connection.execute('SELECT id, value FROM runs').fetchall()
    finally:
        connection.close()
    assert len(rows) == 1, rows
    return json.loads(rows[0][1])


def _container_depth(payload):
    """Container nesting of the deep-chain payload (root Dictionary = 1)."""
    levels, cursor = 1, payload
    while isinstance(cursor.get('next'), dict):
        levels, cursor = levels + 1, cursor['next']
    return levels


def test_scientific_task_hash_unchanged():
    """The science task source is byte-identical to its frozen digest."""
    recorded = FIXTURE.read_text().strip()
    assert _task_digest() == recorded, 'the scientific task source changed'


def test_cycle_guard_harness(evidence_root, evidence):
    """The bounded guard and real-SQLite recovery over a real Godot process."""
    work = evidence_root / 'cycles'
    storage = work / 'storage'
    storage.mkdir(parents=True)
    result = _run([_godot(), '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=300,
                  env={'GEP_SYNTHETIC_STORAGE': str(storage), 'GEP_DEFAULTS_PHASE': 'cycles'})
    evidence('cycles_stdout.txt', {'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:],
                                   'exit': result.returncode})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert 'SCRIPT ERROR' not in result.stderr, result.stderr[-2000:]
    assert 'Stack overflow' not in result.stderr, result.stderr[-2000:]
    assert 'P03R10R_VERIFIED' in result.stdout, result.stdout[-4000:]
    facts = _marker(result.stdout, 'P03R10R_VERIFIED')

    assert facts['max_value_depth'] == 64, facts
    assert facts['cycle_errors'] == {key: 'invalid_value' for key in CYCLE_KEYS}, facts['cycle_errors']
    assert facts['depth'] == {'max': 64, 'at_boundary': True, 'boundary_plus_one': 'invalid_value'}, facts['depth']
    assert facts['shared_leaf'] == [1, 2, {'note': 'shared'}], facts['shared_leaf']
    assert facts['schema_guards'] == {'cycle': 'invalid_value', 'deep': 'invalid_value',
                                      'old_defaults_kept': True}, facts['schema_guards']
    assert facts['detail_bounds']['echoes_payload'] is False, facts['detail_bounds']
    assert isinstance(facts['detail_bounds']['length'], int) and facts['detail_bounds']['length'] <= 160, facts['detail_bounds']
    assert facts['recovery']['state'] == 'finished_saved' and facts['recovery']['buffer'] == 0, facts['recovery']

    # Independent re-read of the durable store: the refusals left the backend
    # usable, the shared payload kept its pre-mutation copy, the boundary-deep
    # payload was persisted exactly once, and the last record is the t9 copy.
    value = _stored_session(storage)
    assert value['state'] == 'finished_saved', value
    records = value['records']
    assert len(records) == facts['recovery']['records'], (len(records), facts['recovery'])
    assert records[-1]['event_id'] == facts['recovery']['last_event_id'], records[-1]
    assert records[-1]['payload'] == facts['recovery']['stored_last'] == {
        'trial_id': 't9', 'choice': 'right', 'rt_ms': 99.25, 'response_status': 'responded'}, records[-1]
    by_type = {}
    for record in records:
        by_type.setdefault(record['event_type'], []).append(record)
    assert [record['event_type'] for record in records] == ['exp.deep', 'exp.shared', 'exp.rt', 'exp.rt', 'exp.rt']
    assert _container_depth(by_type['exp.deep'][0]['payload']) == 64, by_type['exp.deep'][0]['payload']
    assert by_type['exp.shared'][0]['payload'] == facts['shared_golden'], by_type['exp.shared'][0]['payload']
    for record in records:
        expected = ENVELOPE_KEYS | ({'observed_time'} if 'observed_time' in record else set())
        assert set(record) == expected, sorted(record)
    evidence('cycles_facts.json', facts)
    evidence('cycles_records.json', {'records': records})


def test_real_gep_rejects_unregistered_schema(evidence_root, evidence):
    """A well-shaped unregistered id/version is refused by the real server."""
    if str(ROOT / 'tools') not in sys.path:
        sys.path.insert(0, str(ROOT / 'tools'))
    import phase03_verify_shell as shell_verify

    verify = shell_verify.Verify(evidence_root / 'gep_negative' / 'live')
    verify.init_instance()
    verify.start_server()
    try:
        world_code = (
            "import json, os, django\n"
            "django.setup()\n"
            "from core.models import Build, Instance, Release, Study\n"
            "instance = Instance.objects.get(pk=1)\n"
            "instance.authorization_version = 2\n"
            "instance.save(update_fields=['authorization_version'])\n"
            "descriptor = json.loads(os.environ['GEP_R10R_DESCRIPTOR'])\n"
            "study = Study.objects.create(title='R10R unregistered schema study', mode='anonymous', "
            "recruitment='open', max_sessions=8)\n"
            "build = Build.objects.create(study=study, descriptor=descriptor, digest='e' * 64)\n"
            "release = Release.objects.create(study=study, build=build, approved=True, "
            "config={'purpose': 'synthetic', 'mode': 'anonymous', 'max_sessions': 8})\n"
            "study.current_release = release\n"
            "study.save(update_fields=['current_release'])\n"
            "print('R10R_WORLD', json.dumps({'instance': str(instance.instance_id), 'study': str(study.id), "
            "'release': str(release.id), 'build': str(build.id)}))\n"
        )
        created = verify.run_python(world_code, extra_env={'GEP_R10R_DESCRIPTOR': DESCRIPTOR.read_text(encoding='utf-8')})
        assert 'R10R_WORLD' in created.stdout, created.stdout + created.stderr
        world = json.loads(re.search(r'R10R_WORLD (\{.*\})', created.stdout).group(1))

        storage = evidence_root / 'gep_negative' / 'storage'
        storage.mkdir(parents=True)
        result = _run([_godot(), '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=420,
                      env={'GEP_SYNTHETIC_STORAGE': str(storage), 'GEP_DEFAULTS_PHASE': 'gep_negative',
                           'GEP_DEFAULTS_API_URL': f'http://127.0.0.1:{verify.port}',
                           'GEP_DEFAULTS_INSTANCE': world['instance'], 'GEP_DEFAULTS_STUDY': world['study'],
                           'GEP_DEFAULTS_RELEASE': world['release'], 'GEP_DEFAULTS_BUILD': world['build']})
        evidence('gep_negative_stdout.txt', {'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:],
                                             'exit': result.returncode})
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
        assert 'SCRIPT ERROR' not in result.stderr, result.stderr[-2000:]
        assert 'P03R10R_VERIFIED' in result.stdout, result.stdout[-4000:]
        facts = _marker(result.stdout, 'P03R10R_VERIFIED')

        # The client-side shape check accepted the unregistered pair; the real
        # server refusal is persisted as a permanent schema-binding failure.
        assert 'error' not in facts['recorded'] and facts['recorded']['state'] == 'buffered', facts['recorded']
        assert facts['kept']['schema_id'] == 'rt' and facts['kept']['schema_version'] == '9', facts['kept']
        assert facts['verdict']['kind'] == 'schema_binding', facts['verdict']
        assert facts['verdict']['paused'] is True, facts['verdict']
        assert facts['kept']['records'] == 1 and facts['kept']['pending'] == 1, facts['kept']
        assert facts['kept']['complete_ack'] is False, facts['kept']

        # The raw server table has no event for this session: the refused batch
        # never entered RAW, and the completion never became a receipt.
        events = verify.db_events(facts['session_id'])
        evidence('gep_negative_server_events.json', events)
        assert events == [], events

        # The original record identity and pending data are still in the real
        # local SQLite queue, byte-for-byte as recorded.
        connection = sqlite3.connect(f'file:{storage / "queue.sqlite"}?mode=ro', uri=True, timeout=20)
        try:
            rows = connection.execute('SELECT value FROM sessions WHERE id=?', [facts['session_id']]).fetchall()
        finally:
            connection.close()
        assert len(rows) == 1, rows
        session = json.loads(rows[0][0])
        assert len(session['records']) == 1 and session['pending'] == [facts['kept']['event_id']], session
        kept = session['records'][0]
        assert kept['event_id'] == facts['kept']['event_id'], kept
        assert kept['schema_id'] == 'rt' and kept['schema_version'] == '9', kept
        assert kept['payload'] == facts['kept']['payload'], kept
        assert kept['payload'] == {'trial_id': 't1', 'choice': 'left', 'rt_ms': 321.5,
                                   'response_status': 'responded'}, kept
        assert kept['observed_time']['value'] == 321.5 and kept['observed_time']['unit'] == 'ms', kept
        assert session.get('complete_ack') is None, session
        evidence('gep_negative_facts.json', facts)
        evidence('gep_negative_local_session.json', session)
    finally:
        verify.stop_server()


def test_real_web_defaults_demo(evidence_root, evidence):
    """The test-only copy runs the defaults demo in real Web and reconciles it."""
    # The original science task stays byte-identical before and after the copy;
    # the copy itself is only built up from the real project files.
    recorded_digest = FIXTURE.read_text().strip()
    assert _task_digest() == recorded_digest, 'the scientific task source changed before the Web run'

    build_root = _unique_build_root()
    copy = build_root / 'project'
    shutil.copytree(PROJECT, copy, ignore=shutil.ignore_patterns('.godot'))
    templates = copy / '.godot' / 'templates'
    templates.mkdir(parents=True)
    for name in ('web_nothreads_debug.zip', 'web_nothreads_release.zip'):
        source = PROJECT / '.godot' / 'templates' / name
        assert source.is_file(), f'the pinned Web template is missing: {source}'
        shutil.copy2(source, templates / name)
    # The test-only copy replaces exactly one script: its entry bootstrap runs
    # the defaults demo. The copy's task.gd stays byte-identical and unused.
    shutil.copy2(DEMO_BOOTSTRAP, copy / 'bootstrap.gd')
    assert hashlib.sha256((copy / 'task.gd').read_bytes()).hexdigest() == recorded_digest, \
        'the test-only copy changed the scientific task bytes'

    web = build_root / 'web'
    web.mkdir()
    # The test-only bootstrap must parse and analyze before the expensive
    # export/browser run; a parse error would otherwise only surface in Chrome.
    checked = _run([_godot(), '--headless', '--path', str(copy), '--check-only',
                    '--script', 'res://bootstrap.gd'], timeout=120)
    evidence('web_demo_check.txt', {'stdout': checked.stdout[-4000:], 'stderr': checked.stderr[-4000:],
                                    'exit': checked.returncode})
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert 'SCRIPT ERROR' not in checked.stderr and 'Parse Error' not in checked.stderr, checked.stderr[-2000:]
    export = _run([_godot(), '--headless', '--path', str(copy), '--export-release', 'Web',
                   str(web / 'index.html')], timeout=600)
    evidence('web_demo_export.txt', {'stdout': export.stdout[-4000:], 'stderr': export.stderr[-4000:],
                                     'exit': export.returncode, 'project': str(copy)})
    assert export.returncode == 0, export.stdout + export.stderr
    assert (web / 'index.wasm').is_file() and (web / 'index.pck').is_file(), 'the Web export is incomplete'

    chrome = evidence_root / 'chrome_demo'
    chrome.mkdir()
    context = {
        'config_version': '1', 'protocol_version': 'gep/1', 'sdk_version': '0.1.0', 'purpose': 'synthetic',
        'api_url': f'http://127.0.0.1:{_free_port()}', 'instance_id': secrets.token_hex(16),
        'study_id': secrets.token_hex(16), 'release_id': 'preview', 'build_id': secrets.token_hex(16),
        'preview': True,
    }
    job_path = chrome / 'job.json'
    job_path.write_text(json.dumps({'export_dir': str(web), 'package_dir': str(PACKAGES),
                                    'evidence_dir': str(chrome), 'api_url': context['api_url'],
                                    'context': context, 'demo': True}), encoding='utf-8')
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
    evidence('web_demo_playwright_report.json', document)

    summary = json.loads((chrome / 'summary.json').read_text())
    evidence('web_demo_chrome_summary.json', summary)
    assert summary['failures'] == [], summary['failures']
    assert all(entry['ok'] for entry in summary['checks']), summary['failures']
    assert summary['api_requests'] == [], 'the local-only preview must not contact the server'
    scenario = summary['scenarios']['web_defaults_demo']
    assert scenario['records'] == 4, scenario
    assert scenario['demo']['failures'] == [], scenario['demo']['failures']
    assert scenario['demo']['cycle']['error'] == 'invalid_value', scenario['demo']['cycle']
    assert scenario['demo']['deep']['error'] == 'invalid_value', scenario['demo']['deep']
    assert scenario['observed'] == [321.5, None, 217.25, None], scenario['observed']

    lines = [json.loads(line) for line in
             (chrome / 'web-defaults-demo-results.jsonl').read_text(encoding='utf-8').strip().splitlines()]
    assert len(lines) == 4, 'the demo recorded exactly four events: %d' % len(lines)
    expected_payloads = [
        {'trial_id': 't1', 'choice': 'left', 'rt_ms': 321.5, 'response_status': 'responded'},
        INTERACTION,
        {'trial_id': 't2', 'choice': 'right', 'rt_ms': 217.25, 'response_status': 'responded'},
        INTERACTION,
    ]
    assert [line['payload'] for line in lines] == expected_payloads, [line['payload'] for line in lines]
    for index, line in enumerate(lines):
        assert line['sequence'] == index + 1, line
        expected = ENVELOPE_KEYS | ({'observed_time'} if index in (0, 2) else set())
        assert set(line) == expected, (index, sorted(line))
    assert lines[0]['observed_time']['value'] == 321.5 and lines[0]['observed_time']['unit'] == 'ms', lines[0]
    assert lines[2]['observed_time']['value'] == 217.25, lines[2]
    assert 'observed_time' not in lines[1] and 'observed_time' not in lines[3], lines

    # The copy is a test-only artifact; the original science task is unchanged.
    assert _task_digest() == recorded_digest, 'the scientific task source changed during the Web run'
    evidence('web_demo_records.json', {'records': lines})
