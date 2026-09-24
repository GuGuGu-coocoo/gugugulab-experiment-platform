"""P03R10: the experiment-side thin defaults wrapper.

Real checks, none mocked or skipped:

* the wrapper contract under a real Godot process over the real independent
  local backend (real SQLite) and a spy backend for the copy-before-await
  timing: default/explicit precedence, payload replacement, declared field and
  provider evaluation, the observed rules, plain-finite-JSON validation, and
  invalid-schema/backpressure errors that leave the raw buffer untouched;
* the real ``--local`` auto run of the unchanged scientific task through the
  modified wrapper, reconciled value for value with the fixed golden;
* the documented developer demo command, run exactly as written;
* the real native backend against a real isolated Django server (fresh
  database, fresh port, real admission/event/completion HTTP), with the two
  scientific structures recorded through the declared defaults and reconciled
  against the server database;
* the real exported Godot Web build in real Chrome with the shipped bridge and
  SDK in the local-only preview, reconciled value for value with the same
  golden and with zero requests to the API origin.

Evidence: one fresh session root under
``local_data/phase03_remediation_20260923/p03r10/<UTC stamp>-<random>/`` and one
fresh frozen build root under ``build/phase03_remediation_20260923/<stamp>/``;
no existing root is reused or overwritten.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / 'examples' / 'synthetic_experiment'
HARNESS = ROOT / 'tests' / 'native' / 'remediation_defaults_harness.gd'
DEMO = ROOT / 'examples' / 'data_defaults_demo' / 'demo.gd'
SPEC = 'tests/browser/remediation_defaults.spec.js'
BUILD_BASE = ROOT / 'build' / 'phase03_remediation_20260923'
DESCRIPTOR = PROJECT / 'descriptor.json'

RT_SCHEMA = {'id': 'rt', 'version': '1'}
INTERACTION_SCHEMA = {'id': 'interaction', 'version': '1'}
RT_ONE = {'trial_id': 't1', 'choice': 'left', 'rt_ms': 321.5, 'response_status': 'responded'}
RT_TWO = {'trial_id': 't2', 'choice': 'right', 'rt_ms': 217.25, 'response_status': 'responded'}
INTERACTION = {'action': 'revise', 'selection': ['shape_a', 'shape_c'], 'confidence': None,
               'nested': {'changes': [{'from': None, 'to': '001'}], 'confirmed': False}}
GOLDEN = [
    ('exp.rt', RT_SCHEMA, RT_ONE, True),
    ('exp.interaction', INTERACTION_SCHEMA, INTERACTION, False),
    ('exp.rt', RT_SCHEMA, RT_TWO, True),
    ('exp.interaction', INTERACTION_SCHEMA, INTERACTION, False),
]
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
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _assert_golden_envelope(record, index, where, source='Godot Time.get_ticks_usec'):
    """One record of the fixed two-structure golden, envelope keys included.

    The real scientific paths measure the key press and must carry the engine
    clock source. The engineering harness records the same structures with the
    fixed synthetic RT fixture, so its records must say ``synthetic fixture``
    instead of claiming a measurement.
    """
    event_type, schema, payload, has_observed = GOLDEN[index]
    assert record['event_type'] == event_type, where
    assert record['schema_id'] == schema['id'] and record['schema_version'] == schema['version'], where
    assert record['payload'] == payload, where
    assert record['sequence'] == index + 1, where
    expected_keys = ENVELOPE_KEYS | ({'observed_time'} if has_observed else set())
    assert set(record) == expected_keys, (where, sorted(record))
    if has_observed:
        observed = record['observed_time']
        assert observed['value'] == payload['rt_ms'], where
        assert observed['unit'] == 'ms' and observed['clock_id'] == 'host_monotonic', where
        assert observed['source'] == source, where


def test_scientific_task_hash_unchanged():
    """The science task source is byte-identical to its frozen digest."""
    task = (PROJECT / 'task.gd').read_bytes()
    recorded = (ROOT / 'tests' / 'fixtures' / 'scientific_task.sha256').read_text().strip()
    assert hashlib.sha256(task).hexdigest() == recorded, 'the scientific task source changed'


def test_wrapper_never_reads_a_clock_random_or_the_scene_tree():
    """The thin wrapper is pure plumbing: no clock, no RNG, no scene traversal."""
    source = (PROJECT / 'data' / 'experiment_data.gd').read_text(encoding='utf-8')
    for pattern in (r'\bTime\.', r'\brandi\b', r'\brandf\b', r'\brandomize\b', r'\bRandomNumberGenerator\b',
                    r'\bget_children\b', r'\bfind_children\b', r'\bget_nodes_in_group\b', r'\bget_tree\b',
                    r'\bowner\b'):
        assert not re.search(pattern, source), f'the wrapper references {pattern}'
    assert re.search(r'func record\(kind: String = "", payload = null, schema: Dictionary = \{\}, observed: Dictionary = \{\}\)',
                     source), 'the four-parameter record signature changed'


def test_wrapper_contract_harness(evidence_root, evidence):
    """The wrapper contract over the real local backend and a copy-timing spy."""
    work = evidence_root / 'unit'
    storage = work / 'storage'
    storage.mkdir(parents=True)
    result = _run([_godot(), '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=300,
                  env={'GEP_SYNTHETIC_STORAGE': str(storage), 'GEP_DEFAULTS_PHASE': 'unit'})
    evidence('unit_stdout.txt', {'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:],
                                 'exit': result.returncode})
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert 'P03R10_VERIFIED' in result.stdout, result.stdout[-4000:]
    facts = _marker(result.stdout, 'P03R10_VERIFIED')

    assert facts['configure_errors'] == {
        'missing': 'invalid_config', 'event_type_empty': 'invalid_config', 'schema_id_empty': 'invalid_schema',
        'schema_version_missing': 'invalid_schema', 'schema_not_dictionary': 'invalid_schema',
        'exclusive': 'invalid_config', 'fields_type': 'invalid_config', 'fields_callable': 'invalid_field',
        'provider_type': 'invalid_provider', 'unknown_key': 'invalid_config'}, facts['configure_errors']
    assert facts['record_errors'] == {
        'invalid_schema': 'invalid_schema', 'unknown_defaults': 'invalid_defaults',
        'missing_default_schema': 'invalid_defaults', 'no_payload_source': 'invalid_payload',
        'payload_array': 'invalid_payload', 'payload_node': 'invalid_value',
        'observed_non_finite': 'invalid_value'}, facts['record_errors']
    assert facts['backpressure'] == {'error': 'not_recording', 'buffer': 64, 'filled': 64}, facts['backpressure']
    assert facts['responsibility'] == {'records_before_commit': 0, 'buffered_before_commit': 7,
                                       'status_before_finish': 'active', 'finish': 'finished_saved',
                                       'post_finish_error': 'not_recording'}, facts['responsibility']
    assert facts['final_state'] == {'state': 'finished_saved', 'remote_status': 'unsupported'}, facts['final_state']

    # Independent re-read of the durable local store: the explicit old-style
    # record is stored verbatim and every stored envelope keeps its exact keys.
    connection = sqlite3.connect(storage / 'local.sqlite')
    try:
        rows = connection.execute('SELECT id, value FROM runs').fetchall()
    finally:
        connection.close()
    assert len(rows) == 1, rows
    value = json.loads(rows[0][1])
    assert value['state'] == 'finished_saved' and value['remote_status'] == 'unsupported', value
    records = value['records']
    assert len(records) >= 64, len(records)
    for record in records:
        expected = ENVELOPE_KEYS | ({'observed_time'} if 'observed_time' in record else set())
        assert set(record) == expected, sorted(record)
    last = records[-1]
    assert last['event_type'] == 'exp.rt' and last['payload'] == RT_ONE, last
    assert last['observed_time'] == {'value': 321.5, 'unit': 'ms', 'clock_id': 'host_monotonic',
                                     'epoch': 'p03r10', 'source': 'synthetic fixture'}, last
    interaction = [record for record in records if record['event_type'] == 'exp.interaction']
    assert interaction and interaction[0]['payload'] == INTERACTION, interaction[:1]
    evidence('unit_facts.json', facts)


def test_real_local_auto_run_matches_golden(evidence_root, evidence):
    """The unchanged scientific task through the modified wrapper, ``--local``."""
    work = evidence_root / 'local_auto'
    storage = work / 'storage'
    results = work / 'results'
    storage.mkdir(parents=True)
    results.mkdir(parents=True)
    result = _run([_godot(), '--headless', '--path', str(PROJECT), '--', '--local', '--synthetic-auto'],
                  timeout=300, env={'GEP_SYNTHETIC_STORAGE': str(storage), 'GEP_SYNTHETIC_RESULTS': str(results)})
    evidence('local_auto_stdout.txt', {'stdout': result.stdout[-4000:], 'stderr': result.stderr[-2000:],
                                       'exit': result.returncode})
    assert result.returncode == 0, result.stdout + result.stderr
    done = next((line for line in result.stdout.splitlines() if line.startswith('SYNTHETIC_DONE ')), None)
    assert done, result.stdout + result.stderr
    status = json.loads(done.split(' ', 1)[1])
    assert status == {'state': 'finished_saved', 'remote_status': 'unsupported'}, status
    assert (storage / 'local.sqlite').is_file(), 'the independent local backend store is missing'
    assert not (storage / 'queue.sqlite').exists(), 'the native backend store must not be used by --local'
    connection = sqlite3.connect(storage / 'local.sqlite')
    try:
        rows = connection.execute('SELECT id, value FROM runs').fetchall()
    finally:
        connection.close()
    assert len(rows) == 1, rows
    value = json.loads(rows[0][1])
    records = value['records']
    assert len(records) == 4, 'the wrapper must not add or drop scientific events: %d' % len(records)
    for index, record in enumerate(records):
        _assert_golden_envelope(record, index, 'local auto record %d' % index)
    assert value['checkpoint'] is None, value['checkpoint']
    assert value['completion'] == {'event_ids': [record['event_id'] for record in records],
                                   'segment_ids': [records[0]['segment_id']]}, value['completion']
    assert len({record['segment_id'] for record in records}) == 1
    evidence('local_auto_records.json', {'status': status, 'records': records})


def test_developer_demo_is_executable(evidence_root, evidence):
    """The documented demo command runs as written and records the golden."""
    work = evidence_root / 'demo'
    storage = work / 'storage'
    results = work / 'results'
    storage.mkdir(parents=True)
    results.mkdir(parents=True)
    result = _run([_godot(), '--headless', '--path', 'examples/synthetic_experiment',
                   '--script', '../data_defaults_demo/demo.gd'], timeout=300,
                  env={'GEP_SYNTHETIC_STORAGE': str(storage), 'GEP_SYNTHETIC_RESULTS': str(results)})
    evidence('demo_stdout.txt', {'stdout': result.stdout[-4000:], 'stderr': result.stderr[-2000:],
                                 'exit': result.returncode})
    assert result.returncode == 0, result.stdout + result.stderr
    marker = next((line for line in result.stdout.splitlines() if line.startswith('DATA_DEFAULTS_DEMO ')), None)
    assert marker, result.stdout + result.stderr
    summary = json.loads(marker.split(' ', 1)[1])
    assert summary['failures'] == [], summary['failures']
    assert summary['state'] == 'finished_saved', summary['state']
    saved = Path(summary['path'])
    assert saved.is_file() and results in saved.parents, summary['path']
    lines = [json.loads(line) for line in saved.read_text(encoding='utf-8').strip().splitlines()]
    assert len(lines) == 3, 'the demo records exactly three events: %d' % len(lines)
    # The demo's first event is the same exp.rt structure with its own explicit
    # host clock value; the second is the provider snapshot; the third keeps the
    # original explicit four-parameter call.
    assert lines[0]['event_type'] == 'exp.rt' and lines[0]['payload'] == RT_ONE, lines[0]
    assert set(lines[0]) == ENVELOPE_KEYS | {'observed_time'}, sorted(lines[0])
    assert lines[0]['observed_time'] == {'value': 321.5, 'unit': 'ms', 'clock_id': 'host_monotonic',
                                         'epoch': 'demo', 'source': 'host trial timer'}, lines[0]['observed_time']
    assert lines[1]['event_type'] == 'exp.interaction' and lines[1]['payload'] == INTERACTION, lines[1]
    assert set(lines[1]) == ENVELOPE_KEYS, sorted(lines[1])
    assert lines[2]['event_type'] == 'exp.special' and lines[2]['payload'] == {'note': 'explicit override'}, lines[2]
    assert set(lines[2]) == ENVELOPE_KEYS, sorted(lines[2])
    assert len(list(results.iterdir())) == 1, 'the demo saves exactly one result document'
    evidence('demo_records.json', {'records': lines, 'path': str(saved)})


def test_real_gep_connection_matches_golden(evidence_root, evidence):
    """The defaults path against a real isolated Django server and database."""
    if str(ROOT / 'tools') not in sys.path:
        sys.path.insert(0, str(ROOT / 'tools'))
    import phase03_verify_shell as shell_verify

    verify = shell_verify.Verify(evidence_root / 'gep' / 'live')
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
            "descriptor = json.loads(os.environ['GEP_R10_DESCRIPTOR'])\n"
            "study = Study.objects.create(title='R10 defaults study', mode='anonymous', recruitment='open', max_sessions=8)\n"
            "build = Build.objects.create(study=study, descriptor=descriptor, digest='d' * 64)\n"
            "release = Release.objects.create(study=study, build=build, approved=True, "
            "config={'purpose': 'synthetic', 'mode': 'anonymous', 'max_sessions': 8})\n"
            "study.current_release = release\n"
            "study.save(update_fields=['current_release'])\n"
            "print('R10_WORLD', json.dumps({'instance': str(instance.instance_id), 'study': str(study.id), "
            "'release': str(release.id), 'build': str(build.id)}))\n"
        )
        created = verify.run_python(world_code, extra_env={'GEP_R10_DESCRIPTOR': DESCRIPTOR.read_text(encoding='utf-8')})
        assert 'R10_WORLD' in created.stdout, created.stdout + created.stderr
        world = json.loads(re.search(r'R10_WORLD (\{.*\})', created.stdout).group(1))

        storage = evidence_root / 'gep' / 'storage'
        storage.mkdir(parents=True)
        result = _run([_godot(), '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=420,
                      env={'GEP_SYNTHETIC_STORAGE': str(storage), 'GEP_DEFAULTS_PHASE': 'gep',
                           'GEP_DEFAULTS_API_URL': f'http://127.0.0.1:{verify.port}',
                           'GEP_DEFAULTS_INSTANCE': world['instance'], 'GEP_DEFAULTS_STUDY': world['study'],
                           'GEP_DEFAULTS_RELEASE': world['release'], 'GEP_DEFAULTS_BUILD': world['build']})
        evidence('gep_stdout.txt', {'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:],
                                    'exit': result.returncode})
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
        assert 'P03R10_VERIFIED' in result.stdout, result.stdout[-4000:]
        facts = _marker(result.stdout, 'P03R10_VERIFIED')
        assert facts['status']['state'] == 'remote_acknowledged', facts['status']
        assert facts['pending'] == 4, facts['pending']
        assert facts['cleaned'] == {'kind': 'cleaned', 'state': 'remote_acknowledged'}, facts['cleaned']

        events = verify.wait_db_events(facts['session_id'], 4, timeout=60)
        evidence('gep_server_events.json', events)
        assert len(events) == 4, 'the server received exactly four events: %d' % len(events)
        ordered = sorted(events, key=lambda event: event['sequence'])
        for index, event in enumerate(ordered):
            _assert_golden_envelope(event, index, 'gep server record %d' % index, source='synthetic fixture')
        # The local queue and the server database reconcile by event id/value.
        local = {record['event_id']: record for record in facts['records']}
        remote = {event['event_id']: event for event in events}
        assert set(local) == set(remote), (sorted(local), sorted(remote))
        for event_id, server_event in remote.items():
            assert local[event_id]['payload'] == server_event['payload'], event_id
            assert local[event_id].get('observed_time') == server_event.get('observed_time'), event_id
            assert local[event_id]['event_type'] == server_event['event_type'], event_id
        evidence('gep_facts.json', facts)
    finally:
        verify.stop_server()


def test_real_web_preview_matches_golden(evidence_root, evidence):
    """The real Web build in real Chrome: the same golden in local-only preview."""
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
    assert summary['api_requests'] == [], 'the local-only preview must not contact the server'
    assert len(summary['checks']) >= 25, 'the Chrome evidence is too thin: %d checks' % len(summary['checks'])
    assert summary['scenarios']['web_preview']['records'] == 4, summary['scenarios']

    lines = [json.loads(line) for line in
             (chrome / 'web-defaults-results.jsonl').read_text(encoding='utf-8').strip().splitlines()]
    assert len(lines) == 4, 'the Web preview recorded exactly four events: %d' % len(lines)
    for index, record in enumerate(lines):
        event_type, schema, payload, has_observed = GOLDEN[index]
        assert record['event_type'] == event_type, 'web record %d' % index
        assert record['schema_id'] == schema['id'] and record['schema_version'] == schema['version'], 'web record %d' % index
        assert record['sequence'] == index + 1, 'web record %d' % index
        expected_keys = ENVELOPE_KEYS | ({'observed_time'} if has_observed else set())
        assert set(record) == expected_keys, (index, sorted(record))
        if has_observed:
            # The RT is the real measured key-press interval; the golden is the
            # deterministic structure plus observed === payload RT.
            assert record['payload']['trial_id'] == payload['trial_id'], 'web record %d' % index
            assert record['payload']['choice'] == payload['choice'], 'web record %d' % index
            assert record['payload']['response_status'] == payload['response_status'], 'web record %d' % index
            assert set(record['payload']) == {'trial_id', 'choice', 'rt_ms', 'response_status'}, record['payload']
            assert isinstance(record['payload']['rt_ms'], (int, float)) and record['payload']['rt_ms'] > 0, record['payload']
            observed = record['observed_time']
            assert observed['value'] == record['payload']['rt_ms'], 'web record %d' % index
            assert observed['unit'] == 'ms' and observed['clock_id'] == 'host_monotonic', observed
            assert observed['source'] == 'Godot Time.get_ticks_usec', observed
        else:
            assert record['payload'] == payload, 'web record %d' % index
    assert len({record['segment_id'] for record in lines}) == 1
    preview = summary['scenarios']['web_preview']
    assert preview['observed'][1] is None and preview['observed'][3] is None, preview
    assert preview['observed'][0] == lines[0]['payload']['rt_ms'], preview
    assert preview['observed'][2] == lines[2]['payload']['rt_ms'], preview
