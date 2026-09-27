"""P03R11W: the explicit Windows round selection, the hardened remote contract,
the real-viewing evidence re-read and the aggregate device/viewing binding.

These are real, deterministic checks of the round's own control surfaces - no
network and no device are needed here:

* the explicit persistent round selection is written/read only at its exact
  path (never by scanning a "latest" directory), refuses missing, malformed,
  incomplete and stale documents, and is written mode 0600;
* ``--verify`` without ``--kit-root`` and without a valid selection refuses
  before any SSH/scp process exists, and a structurally invalid kit is refused
  even earlier;
* every remotely executed PowerShell script escapes single quotes by doubling
  them, never uses ``-Force`` to overwrite an existing root, sets
  ``$ErrorActionPreference='Stop'`` and propagates the child exit code;
* the aggregate preparation step validates exactly the explicitly selected
  preparation (a newer decoy is never chosen, a mismatched selection digest is
  a failure) and a stale selected preparation stays NOT_RUN;
* the real-viewing evidence is re-read from its raw files (device result, CSV
  bytes, expected document) and every tampering variant is refused;
* ``--verify-windows`` without explicit configuration reports BLOCKED and never
  starts a subprocess;
* the aggregate gate never accepts the local machine or the local file
  preparation as a substitute for the real Windows run or the real viewing,
  and requires both to pass together with the local matrix.

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r11w/<UTC>-<random>/`` root.
"""
import csv
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import phase03_remediation_acceptance as acceptance  # noqa: E402
import remediation_spreadsheet as spreadsheet  # noqa: E402
import remediation_windows as windows  # noqa: E402
import windows_native_harness as native_harness  # noqa: E402

INSTANCE_ID = '11111111-2222-3333-4444-555555555555'
PROGRAM_SHA = 'a' * 64
OTHER_SHA = 'b' * 64
STALE_SHA = 'c' * 64


# ------------------------------------------------------------- fixtures

def module_members(root, manifest_name):
    members = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and path.name != manifest_name:
            members[path.relative_to(root).as_posix()] = {
                'sha256': windows.sha256_file(path), 'size': path.stat().st_size}
    return members


def make_preparation(root, *, program_sha=PROGRAM_SHA, source_digest=None, inputs=None):
    """A synthetic preparation that passes the strict kit/human checks.

    Structurally exact on purpose: every strict check the real gate performs
    (member manifests, three mode records, delivery digests, source binding)
    is satisfiable here, so a failing assertion is about the code under test
    and not about a loose fixture.
    """
    root = Path(root)
    source_digest = source_digest or windows.program_source_digest()
    inputs = windows.program_source_inputs() if inputs is None else inputs
    kit = root / 'kit'
    (kit / 'operator' / 'harness').mkdir(parents=True)
    (kit / 'README.md').write_text('# synthetic kit\n', encoding='utf-8')
    (kit / 'operator' / 'harness' / 'windows_native_harness.py').write_text(
        '# synthetic harness\n', encoding='utf-8')
    (kit / 'operator' / 'runtime.json').write_bytes(windows.canonical_json(
        {'format': windows.RUNTIME_FORMAT, 'instance_id': INSTANCE_ID, 'port': 8123}))
    modes = {}
    for mode in windows.MODES:
        delivery = f'delivery/{mode}/gep-{mode}.zip'
        package = kit / delivery
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_bytes(f'synthetic-{mode}-package'.encode('utf-8'))
        modes[mode] = {'delivery': delivery, 'package_sha256': windows.sha256_file(package),
                       'program_sha256': program_sha, 'release_id': f'release-{mode}',
                       'study_id': f'study-{mode}', 'build_id': f'build-{mode}'}
    (kit / 'releases.json').write_bytes(windows.canonical_json(
        {'kit_format': windows.KIT_VERSION, 'prepared_by': windows.PREPARED_BY,
         'instance_id': INSTANCE_ID, 'program_sha256': program_sha,
         'program_source_digest': source_digest, 'program_source_inputs': inputs, 'modes': modes}))
    (kit / 'integrity.json').write_bytes(windows.canonical_json(
        {'format': windows.KIT_VERSION, 'program_sha256': program_sha,
         'program_source_digest': source_digest, 'program_source_inputs': inputs,
         'members': module_members(kit, 'integrity.json')}))
    human = root / 'human'
    human.mkdir()
    (human / 'README.md').write_text('# synthetic human package\n', encoding='utf-8')
    (human / 'sha256.json').write_bytes(windows.canonical_json(
        {'format': windows.HUMAN_FORMAT, 'program_source_digest': source_digest,
         'members': module_members(human, 'sha256.json')}))
    private = root / 'private'
    private.mkdir()
    (private / 'runtime_accounts.json').write_text(json.dumps(
        {'format': windows.ACCOUNTS_FORMAT, 'instance_id': INSTANCE_ID,
         'member': {'username': 'synthetic_member', 'password': 'synthetic_password'}}),
        encoding='utf-8')
    (root / 'prepare_report.json').write_bytes(windows.canonical_json(
        {'verdict': 'ok', 'build': {'program_sha256': program_sha, 'program_source_digest': source_digest,
                                    'program_source_digest_after': source_digest,
                                    'program_source_inputs': inputs}}))
    return kit


def selection_for(root, program_sha=PROGRAM_SHA):
    root = Path(root)
    return {'format': windows.ROUND_SELECTION_FORMAT, 'task': 'p03r11w',
            'prepare_root': str(root), 'kit_root': str(root / 'kit'),
            'accounts': str(root / 'private' / 'runtime_accounts.json'),
            'instance_id': INSTANCE_ID, 'program_sha256': program_sha,
            'program_source_digest': windows.program_source_digest(),
            'connection': {'host': '', 'host_key_alias': '', 'windows_root': ''}}


def synthetic_viewing_pair():
    """A tiny pair with the exact shapes the viewing expectations parse."""
    participants = io.StringIO()
    writer = csv.writer(participants, lineterminator='\r\n')
    writer.writerow(['study_title', 'participant_uuid', 'participant_code'])
    writer.writerow(['text:Synthetic spreadsheet study\n第二行', 'uuid-1', 'text:001'])
    writer.writerow(['text:Synthetic spreadsheet study\n第二行', 'uuid-null', ''])
    events = io.StringIO()
    event_writer = csv.writer(events, lineterminator='\r\n')
    event_writer.writerow(['study_title', 'record_json'])
    event_writer.writerow(['text:Synthetic spreadsheet study\n第二行',
                           'json:' + json.dumps({'payload': {'formula': '=1+1', 'text': 'line1\nline2',
                                                             'blob': 'a' * spreadsheet.LARGE_CELL_CHARS}})])
    return {'participants_bytes': ('\ufeff' + participants.getvalue()).encode('utf-8'),
            'events_bytes': ('\ufeff' + events.getvalue()).encode('utf-8'),
            'null_participant_uuid': 'uuid-null', 'participant_ids': {'uuid-1', 'uuid-null'},
            'csv_dir': None, 'owner': None, 'study': None}


def write_viewing_evidence(root, *, run_id='run-r11w-1'):
    """One complete, valid --verify-windows evidence root; returns (root, expected, device)."""
    root = Path(root)
    (root / 'csv').mkdir(parents=True)
    pair = synthetic_viewing_pair()
    expected = spreadsheet.expected_viewing_document(pair, run_id)
    (root / 'csv' / 'participants.csv').write_bytes(pair['participants_bytes'])
    (root / 'csv' / 'events.csv').write_bytes(pair['events_bytes'])
    (root / 'expected.json').write_text(json.dumps(expected, ensure_ascii=False), encoding='utf-8')
    device = {
        'format': spreadsheet.SPREADSHEET_VIEW_FORMAT, 'run_id': run_id,
        'excel_version': '16.0', 'excel_build': '17932', 'owned_excel_pid': 4242, 'stage': 'done',
        'checks': {name: True for name in spreadsheet.VIEW_CHECKS},
        'observed': {'recovered_001': '001', 'recovered_formula': '=1+1',
                     'recovered_title': 'Synthetic spreadsheet study\n第二行',
                     'title_newline_style': 'lf',
                     'record_json_chars': expected['events']['record_json_chars'],
                     'payload_blob_chars': spreadsheet.LARGE_CELL_CHARS,
                     'null_code_is_empty': True},
        'files': {'participants_sha256': expected['csv']['participants']['sha256'],
                  'events_sha256': expected['csv']['events']['sha256']},
        'error': ''}
    (root / 'viewing_result.json').write_text(json.dumps(device, ensure_ascii=False), encoding='utf-8')
    summary = {'task': 'p03r11w-windows-spreadsheet', 'status': 'PASS', 'run_id': run_id,
               'excel_version': '16.0', 'problems': [], 'windows_verified': True,
               'human_acceptance': 'NOT_RUN', 'viewing_status': 'REAL_VIEWING_PASS',
               'evidence_root': str(root)}
    (root / 'spreadsheet_windows.json').write_text(json.dumps(summary, ensure_ascii=False), encoding='utf-8')
    return root, expected, device


def local_resolver_evidence():
    """All local selectors green; external classes supplied by the caller."""
    nodes = {}
    checks = {'boundary': {}, 'shell': {}, 'package': {}}
    browser = {}
    for _requirement, (_text, clauses) in acceptance.SUBREQUIREMENTS.items():
        for _label, selectors in clauses:
            for selector in selectors:
                if selector.startswith(acceptance.EXTERNAL_SELECTOR_PREFIXES):
                    continue
                source, _, key = selector.partition(':')
                if source == 'pytest':
                    nodes[key] = 'passed'
                elif source in ('boundary', 'shell', 'package'):
                    checks[source][key] = True
                elif source == 'browser':
                    browser[key] = 'passed'
    evidence = {
        'remediation': {'nodes': dict(nodes)}, 'legacy': {'nodes': {}},
        'boundary': {'checks': checks['boundary']}, 'shell': {'checks': checks['shell']},
        'package': {'checks': checks['package']}, 'browser': {'tests': browser},
        'docs': {'internal_paths': [], 'private_addresses': [], 'human_wording': []}}
    evidence['windows_preparation'] = {'status': acceptance.STATUS_PASS}
    evidence['windows_runtime'] = {'status': acceptance.STATUS_PASS, 'cases': {
        case: acceptance.STATUS_PASS for case in windows.WINDOWS_CASES}}
    evidence['spreadsheet_windows'] = {'status': acceptance.STATUS_PASS,
                                       'checks': {key: True for key in (
                                           'real_software_version', 'text_001_literal',
                                           'formula_not_evaluated', 'newline_in_one_cell',
                                           'null_empty_cell', 'large_cell_intact', 'prefix_recovery')}}
    return evidence


def device_steps(windows_status=acceptance.STATUS_PASS, spreadsheet_status=acceptance.STATUS_PASS):
    steps = {name: acceptance.STATUS_PASS for name in
             ('artifacts', 'boundary', 'shell', 'package', 'browser', 'remediation', 'legacy',
              'docs', 'new_suite', 'windows_preparation')}
    steps['windows_runtime'] = windows_status
    steps['spreadsheet_windows'] = spreadsheet_status
    return steps


# ------------------------------------------------------- explicit selection

def test_round_selection_is_explicit_and_stale_is_refused(evidence_root, tmp_path, monkeypatch):
    selection_path = evidence_root / 'selection.json'
    monkeypatch.setenv(windows.ROUND_SELECTION_ENV, str(selection_path))
    root = tmp_path / 'prepared'
    make_preparation(root)
    document = selection_for(root)
    written = windows.write_round_selection(document)
    assert written == selection_path
    assert (written.stat().st_mode & 0o777) == 0o600
    loaded, problems = windows.load_round_selection()
    assert problems == [] and loaded['kit_root'] == document['kit_root']

    # A missing file is an explicit refusal, never a directory scan.
    monkeypatch.setenv(windows.ROUND_SELECTION_ENV, str(evidence_root / 'no-such.json'))
    loaded, problems = windows.load_round_selection()
    assert loaded is None and any('missing' in problem for problem in problems)
    monkeypatch.setenv(windows.ROUND_SELECTION_ENV, str(selection_path))

    # A stale source binding and an incomplete kit are refused.
    stale = dict(document, program_source_digest=STALE_SHA)
    windows.write_round_selection(stale)
    loaded, problems = windows.load_round_selection()
    assert loaded is not None and any('stale' in problem for problem in problems)
    incomplete = dict(document, kit_root=str(tmp_path / 'gone' / 'kit'))
    windows.write_round_selection(incomplete)
    loaded, problems = windows.load_round_selection()
    assert any('missing or incomplete' in problem for problem in problems)

    # Malformed JSON is refused, never silently ignored.
    selection_path.write_text('{not json', encoding='utf-8')
    loaded, problems = windows.load_round_selection()
    assert loaded is None and any('unreadable' in problem for problem in problems)

    # The default path is one fixed explicit file under the project-ignored
    # local_data tree; it is never a glob or a "latest" directory guess.
    monkeypatch.delenv(windows.ROUND_SELECTION_ENV, raising=False)
    default_path = windows.round_selection_path()
    assert default_path.name == 'round_selection.json'
    assert 'p03r11w' in default_path.parts
    assert not any(char in str(default_path) for char in '*?')


def test_verify_without_kit_or_selection_refuses_without_network(evidence_root, tmp_path,
                                                                 monkeypatch, capsys):
    monkeypatch.setenv(windows.ROUND_SELECTION_ENV, str(tmp_path / 'missing-selection.json'))
    for name in ('GEP_TEST_SSH_HOST', 'GEP_TEST_SSH_HOST_KEY_ALIAS', 'GEP_TEST_WINDOWS_ROOT',
                 'GEP_TEST_WINDOWS_ACCOUNTS', 'GEP_TEST_WINDOWS_PYTHON'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(windows, 'EVIDENCE_BASE', evidence_root)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('no SSH/scp subprocess may start without a selection or a kit')

    monkeypatch.setattr(windows, '_ssh_run', forbidden)
    monkeypatch.setattr(windows, '_scp_copy', forbidden)
    code = windows.main(['--verify'])
    assert code == 2
    assert 'kit-root or a valid explicit round selection' in capsys.readouterr().err

    # An explicitly selected but structurally invalid kit is refused before any
    # platform branch and before any network call.
    bad_kit = tmp_path / 'bad-kit'
    bad_kit.mkdir()
    (bad_kit / 'integrity.json').write_text('{}', encoding='utf-8')
    (bad_kit / 'releases.json').write_text('{}', encoding='utf-8')
    code = windows.main(['--verify', '--kit-root', str(bad_kit)])
    assert code == 1
    assert list(evidence_root.rglob('verify_report.json'))


# --------------------------------------------------------- remote contract

def test_remote_scripts_are_single_quote_safe_and_never_force():
    quoted = windows.ps_literal("C:\\a'b\\c")
    assert quoted == "'C:\\a''b\\c'"
    hostile = "C:\\runs\\it's a root"
    root_script = windows.remote_root_script(hostile)
    assert "$ErrorActionPreference='Stop'" in root_script
    assert 'Test-Path -LiteralPath' in root_script
    assert 'GEP_REMOTE_ROOT_EXISTS' in root_script
    assert "Write-Output 'GEP_REMOTE_ROOT_EXISTS'; exit 17" in root_script
    assert "'C:\\runs\\it''s a root'" in root_script
    assert '-Force' not in root_script
    expand = windows.remote_expand_script('C:\\a\\bundle.zip', hostile, 'C:\\a\\run')
    assert 'Expand-Archive -LiteralPath' in expand and '-Force' not in expand
    harness = windows.remote_harness_script("C:\\py'thon\\python.exe", 'C:\\kit\\h.py', 'C:\\kit',
                                            'C:\\kit\\runtime.json', 'C:\\priv\\accounts.json',
                                            'C:\\run', 'C:\\run\\run.json')
    assert "C:\\py''thon\\python.exe" in harness
    assert 'GEP_HARNESS_EXIT=' in harness and 'exit $code' in harness
    assert '-Force' not in harness
    compress = windows.remote_compress_script('C:\\run', 'C:\\run.zip')
    assert 'Test-Path -LiteralPath' in compress and 'exit 18' in compress
    assert 'Compress-Archive' in compress and '-Force' not in compress
    # The scp remote argument is never a backslash path (the local scp parses
    # a backslash as an escape and a download would silently miss).
    assert windows.scp_remote_path('C:\\a\\b') == 'C:/a/b'
    assert windows.remote_evidence_name(Path('/evidence/round-one/windows-runtime')) != \
           windows.remote_evidence_name(Path('/evidence/round-two/windows-runtime'))
    assert windows.remote_evidence_name(Path('/evidence/round-two/windows-runtime')) == \
           'round-two-windows-runtime'
    with pytest.raises(windows.KitError):
        windows.remote_evidence_name(Path('/evidence/unsafe;name/windows-runtime'))


def test_fetched_run_archive_is_unpacked_with_backslash_members(evidence_root):
    """Compress-Archive writes ``evidence\\WN01\\x`` names; POSIX zip would flatten."""
    scratch = evidence_root / 'archive'
    scratch.mkdir()
    good = scratch / 'good.zip'
    with zipfile.ZipFile(good, 'w') as archive:
        archive.writestr('run.json', '{"format":"gep-windows-run/v1"}')
        archive.writestr('evidence\\WN01\\export.jsonl', 'line-1\n')
        archive.writestr('evidence\\WN01\\nested\\out.txt', 'nested')
        archive.writestr('evidence\\WN02\\', '')
    target = scratch / 'good-out'
    written = windows.unpack_windows_run_archive(good, target)
    assert written == 3
    assert (target / 'run.json').read_text(encoding='utf-8') == '{"format":"gep-windows-run/v1"}'
    assert (target / 'evidence' / 'WN01' / 'export.jsonl').read_text(encoding='utf-8') == 'line-1\n'
    assert (target / 'evidence' / 'WN01' / 'nested' / 'out.txt').read_text(encoding='utf-8') == 'nested'
    assert not any('\\' in path.name for path in target.rglob('*'))

    for member in ('..\\escape.txt', 'evidence\\..\\..\\escape.txt', '/absolute.txt', 'C:\\abs.txt'):
        archive_path = scratch / 'bad.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            archive.writestr(member, 'x')
        with pytest.raises(windows.KitError):
            windows.unpack_windows_run_archive(archive_path, scratch / 'bad-out')
        assert not (scratch / 'escape.txt').exists()

    empty = scratch / 'empty.zip'
    with zipfile.ZipFile(empty, 'w'):
        pass
    with pytest.raises(windows.KitError):
        windows.unpack_windows_run_archive(empty, scratch / 'empty-out')

    # A later invalid member or a duplicate normalized Windows/POSIX name must
    # not leave the first member behind as seemingly usable run evidence.
    for names in (('run.json', '..\\escape.txt'),
                  ('evidence\\WN01\\x.txt', 'evidence/WN01/x.txt'),
                  ('run.json', 'run.json\\nested.txt')):
        archive_path = scratch / 'bad-sequence.zip'
        with zipfile.ZipFile(archive_path, 'w') as archive:
            for name in names:
                archive.writestr(name, 'synthetic')
        output = scratch / 'bad-sequence-out'
        with pytest.raises(windows.KitError):
            windows.unpack_windows_run_archive(archive_path, output)
        assert not output.exists()


def test_extract_refuses_symlinked_run_boundary_before_removing_existing_content(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    boundary = tmp_path / 'run-link'
    boundary.symlink_to(outside, target_is_directory=True)
    target = outside / 'GEP 原生测试 中文 anonymous synthetic'
    target.mkdir()
    canary = target / 'canary.bin'
    canary.write_bytes(b'preserve-me')
    harness = SimpleNamespace(run_root=boundary, run_id='synthetic')
    with pytest.raises(native_harness.HarnessError):
        native_harness.Harness.extract(harness, tmp_path / 'unused.zip', 'anonymous')
    assert canary.read_bytes() == b'preserve-me'


# -------------------------------------------------- selected preparation

def test_reselection_keeps_the_already_selected_connection(evidence_root, tmp_path, monkeypatch):
    """A selection rewrite never drops connection values it already carries."""
    selection_path = evidence_root / 'inherit-selection.json'
    monkeypatch.setenv(windows.ROUND_SELECTION_ENV, str(selection_path))
    for name in ('GEP_TEST_SSH_HOST', 'GEP_TEST_SSH_HOST_KEY_ALIAS', 'GEP_TEST_WINDOWS_ROOT',
                 'GEP_TEST_WINDOWS_PYTHON'):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / 'prepared-inherit'
    make_preparation(root)
    existing = dict(selection_for(root), connection={'host': 'win-alias', 'host_key_alias': '192.0.2.7',
                                                     'windows_root': 'C:\\acceptance', 'python': ''})
    windows.write_round_selection(existing)
    build = {'program_sha256': PROGRAM_SHA, 'program_source_digest': windows.program_source_digest()}
    accounts = root / 'private' / 'runtime_accounts.json'
    document = windows.selection_from_preparation(root, build, accounts)
    assert document['connection'] == existing['connection']

    # A configured environment still wins; the missing keys are inherited.
    monkeypatch.setenv('GEP_TEST_SSH_HOST', 'env-host')
    document = windows.selection_from_preparation(root, build, accounts)
    assert document['connection']['host'] == 'env-host'
    assert document['connection']['windows_root'] == 'C:\\acceptance'


def test_selected_preparation_is_validated_exactly_not_scanned(evidence_root, tmp_path):
    base = evidence_root / 'prepare-base'
    base.mkdir()
    selected = base / 'selected'
    make_preparation(selected, program_sha=PROGRAM_SHA)
    # A newer, structurally valid decoy that a "latest directory" scan would
    # have picked; the explicit selection must ignore it.
    make_preparation(base / 'decoy-newer', program_sha=OTHER_SHA)

    report = acceptance.windows_preparation(selection=selection_for(selected))
    assert report['status'] == acceptance.STATUS_PASS, report
    assert report['program_sha256'] == PROGRAM_SHA
    assert Path(report['prepare_root']) == selected
    assert report['selection'] == 'explicit'

    mismatched = acceptance.windows_preparation(selection=selection_for(selected, program_sha=OTHER_SHA))
    assert mismatched['status'] == acceptance.STATUS_FAIL
    assert any('selection program digest' in problem for problem in mismatched['problems'])

    stale_root = base / 'stale'
    make_preparation(stale_root, source_digest=STALE_SHA)
    stale = acceptance.windows_preparation(
        selection=dict(selection_for(stale_root), program_source_digest=STALE_SHA))
    assert stale['status'] == acceptance.STATUS_NOT_RUN
    assert any('stale selection' in (stale.get('reason') or '') for _ in [0])

    missing = acceptance.windows_preparation(selection={'kit_root': str(base / 'nope' / 'kit')})
    assert missing['status'] == acceptance.STATUS_NOT_RUN


def test_real_gate_refuses_missing_selection_even_with_matching_historical_kit(evidence_root):
    prepared = evidence_root / 'historical-preparation'
    make_preparation(prepared)
    # A structurally valid historical kit exists. The required real-device gate
    # cannot infer that it is this round's selected target.
    refused = acceptance.selected_windows_preparation(
        True, None, ['the explicit round selection is missing'], None)
    assert refused['status'] == acceptance.STATUS_NOT_RUN
    assert 'explicit round selection' in refused['reason']

    selected = acceptance.selected_windows_preparation(
        True, selection_for(prepared), [], None)
    assert selected['status'] == acceptance.STATUS_PASS
    assert Path(selected['prepare_root']) == prepared


# ------------------------------------------------------ real viewing gate

def test_windows_viewing_evidence_is_re_read_and_tampering_refused(evidence_root):
    root, expected, device = write_viewing_evidence(evidence_root / 'viewing-ok')
    problems, document = spreadsheet.validate_spreadsheet_windows_evidence(root)
    assert problems == [] and document['status'] == 'PASS'
    assert device['checks'] == {name: True for name in spreadsheet.VIEW_CHECKS}

    # A failed device check is refused even when the summary still says PASS.
    tampered = evidence_root / 'viewing-bad-check'
    write_viewing_evidence(tampered)
    device = json.loads((tampered / 'viewing_result.json').read_text(encoding='utf-8'))
    device['checks']['text_formula_literal'] = False
    (tampered / 'viewing_result.json').write_text(json.dumps(device), encoding='utf-8')
    problems, _ = spreadsheet.validate_spreadsheet_windows_evidence(tampered)
    assert any('text_formula_literal' in problem for problem in problems)

    # A swapped run id, a mismatched CSV digest, a failed summary and a
    # human "pass" all stay explicit problems.
    swapped = evidence_root / 'viewing-swapped'
    write_viewing_evidence(swapped)
    device = json.loads((swapped / 'viewing_result.json').read_text(encoding='utf-8'))
    device['run_id'] = 'another-run'
    (swapped / 'viewing_result.json').write_text(json.dumps(device), encoding='utf-8')
    problems, _ = spreadsheet.validate_spreadsheet_windows_evidence(swapped)
    assert any('belong to this run' in problem for problem in problems)

    digest = evidence_root / 'viewing-digest'
    write_viewing_evidence(digest)
    device = json.loads((digest / 'viewing_result.json').read_text(encoding='utf-8'))
    device['files']['participants_sha256'] = 'd' * 64
    (digest / 'viewing_result.json').write_text(json.dumps(device), encoding='utf-8')
    problems, _ = spreadsheet.validate_spreadsheet_windows_evidence(digest)
    assert any('participants.csv bytes' in problem for problem in problems)

    failed = evidence_root / 'viewing-failed-summary'
    write_viewing_evidence(failed)
    summary = json.loads((failed / 'spreadsheet_windows.json').read_text(encoding='utf-8'))
    summary['status'] = 'FAIL'
    summary['windows_verified'] = False
    (failed / 'spreadsheet_windows.json').write_text(json.dumps(summary), encoding='utf-8')
    problems, _ = spreadsheet.validate_spreadsheet_windows_evidence(failed)
    assert any('status' in problem for problem in problems)

    human = evidence_root / 'viewing-human-pass'
    write_viewing_evidence(human)
    summary = json.loads((human / 'spreadsheet_windows.json').read_text(encoding='utf-8'))
    summary['human_acceptance'] = 'PASS'
    (human / 'spreadsheet_windows.json').write_text(json.dumps(summary), encoding='utf-8')
    problems, _ = spreadsheet.validate_spreadsheet_windows_evidence(human)
    assert any('human round NOT_RUN' in problem for problem in problems)

    missing = evidence_root / 'viewing-missing-result'
    missing.mkdir()
    problems, _ = spreadsheet.validate_spreadsheet_windows_evidence(missing)
    assert problems


def test_windows_viewing_refuses_without_explicit_configuration(evidence_root, tmp_path, monkeypatch):
    monkeypatch.setenv(windows.ROUND_SELECTION_ENV, str(tmp_path / 'no-selection.json'))
    for name in ('GEP_TEST_SSH_HOST', 'GEP_TEST_SSH_HOST_KEY_ALIAS', 'GEP_TEST_WINDOWS_ROOT',
                 'GEP_TEST_WINDOWS_PYTHON'):
        monkeypatch.delenv(name, raising=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('no subprocess may start without explicit configuration')

    monkeypatch.setattr(windows, '_ssh_run', forbidden)
    monkeypatch.setattr(windows, '_scp_copy', forbidden)
    monkeypatch.setattr(spreadsheet, 'prepare_csv_pair', lambda _root: synthetic_viewing_pair())
    monkeypatch.setattr(spreadsheet, 'validate_pair', lambda *args, **kwargs: spreadsheet.Report())
    target = evidence_root / 'blocked-viewing'
    code, document = spreadsheet.verify_windows(evidence_dir=target)
    assert code == 1
    assert document['status'] == 'BLOCKED' and document['windows_verified'] is False
    assert document['human_acceptance'] == 'NOT_RUN'
    assert (target / 'spreadsheet_windows.json').is_file()


# ------------------------------------------------------- aggregate binding

def test_aggregate_never_substitutes_local_for_device_or_viewing():
    evidence = local_resolver_evidence()
    matrix = acceptance.requirement_matrix(evidence)
    assert matrix['T29']['local_status'] == acceptance.STATUS_PASS
    assert matrix['T29']['status'] == acceptance.STATUS_PASS

    verdict, reason = acceptance.overall_status(matrix, device_steps(), {}, require_windows=True)
    assert verdict == acceptance.STATUS_PASS, reason

    # The device run is mandatory: a local-only pass can never stand in.
    verdict, reason = acceptance.overall_status(
        matrix, device_steps(windows_status=acceptance.STATUS_NOT_RUN), {}, require_windows=True)
    assert verdict == acceptance.STATUS_FAIL
    assert 'never substitutes the local gate' in reason

    # The real spreadsheet viewing is mandatory too.
    verdict, reason = acceptance.overall_status(
        matrix, device_steps(spreadsheet_status=acceptance.STATUS_NOT_RUN), {}, require_windows=True)
    assert verdict == acceptance.STATUS_FAIL
    assert 'spreadsheet viewing' in reason and 'never accepts the local file preparation' in reason

    # Removing exactly the viewing evidence from the matrix flips only that.
    lacking = local_resolver_evidence()
    lacking['spreadsheet_windows'] = {'status': acceptance.STATUS_NOT_RUN, 'checks': {}}
    lacking_matrix = acceptance.requirement_matrix(lacking)
    assert lacking_matrix['T29']['status'] == acceptance.STATUS_NOT_RUN
    assert lacking_matrix['T29']['local_status'] == acceptance.STATUS_PASS
    assert lacking_matrix['T28']['status'] == acceptance.STATUS_PASS
