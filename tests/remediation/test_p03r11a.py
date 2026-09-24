"""P03R11A: the affected-integration orchestration and the R11 tool boundaries.

Real checks, none mocked into a pass:

* the R11 boundary rehearsal (explicit 0011 source -> SQLite-backup copy ->
  latest schema) really runs as a subprocess, keeps the source unchanged and
  records every identity/deletion fact through real entry points;
* the coverage matrix resolves every subclause on its own named evidence: the
  recovery-code, cleanup and checkpoint clauses stop passing when exactly their
  own evidence is removed, even though every unrelated selector stays green,
  and the T28/T23/T24 mappings are checked not to bind invitation/activation,
  deletion-file or cycle-value lookalikes;
* the local gate marks a missing, failed or skipped named selector NOT_RUN/FAIL
  - never PASS - and the external Windows runtime stays an explicit NOT_RUN
  class while every local requirement is still bound;
* the strict frozen-manifest verification refuses an empty, missing, tampered,
  extra, traversing or symlinked member and leaves an external canary byte
  identical, and the preparation gate fails closed on such a manifest or an
  unreadable report;
* the artifact binding refuses the historical build/build-native defaults, a
  stale program-source digest and tampered bytes, and both verifiers refuse an
  unbound or partial explicit invocation before they start anything;
* the Mac orchestration refuses to claim any Windows result without a real SSH
  configuration, and that refusal happens only after the deterministic kit
  validation has completed;
* every new tool refuses an existing, outside or symlinked evidence root before
  writing and exits non-zero with a readable result;
* the integration tools that prepare or copy a program refuse a symlinked or
  reparse-point input and leave the source and an external canary byte
  identical (R09CR regression: R11W only starts after this holds).

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r11a/<UTC>-<random>/`` root; the
boundary rehearsal writes its own fresh numbered root below it.
"""
import importlib.util
import json
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import phase03_remediation_acceptance as acceptance  # noqa: E402
import remediation_gec as gec  # noqa: E402


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------- matrix helpers

def selectors_of(requirement):
    return [selector for _label, selectors in acceptance.SUBREQUIREMENTS[requirement][1]
            for selector in selectors]


def clause_of(requirement, selector):
    for index, (label, selectors) in enumerate(acceptance.SUBREQUIREMENTS[requirement][1]):
        if selector in selectors:
            return index, label
    raise AssertionError(f'{selector} is not bound to {requirement}')


def synthetic_resolver_evidence():
    """Resolver input built from the declared selectors, all green locally.

    This only exercises the resolver mechanics; the mapping itself is asserted
    separately by the shape and removal tests below, so a wrongly mapped clause
    cannot be "proved" by this fixture alone. The external classes stay NOT_RUN.
    """
    nodes = {}
    checks = {'boundary': {}, 'shell': {}, 'package': {}}
    browser = {}
    for requirement, (_text, clauses) in acceptance.SUBREQUIREMENTS.items():
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
    return {
        'remediation': {'nodes': dict(nodes)},
        'legacy': {'nodes': {}},
        'boundary': {'checks': checks['boundary']},
        'shell': {'checks': checks['shell']},
        'package': {'checks': checks['package']},
        'browser': {'tests': browser},
        'docs': {'internal_paths': [], 'private_addresses': [], 'human_wording': []},
        'windows_preparation': {'status': acceptance.STATUS_NOT_RUN,
                                'reason': 'no fresh preparation bound to the current program source digest'},
        'windows_runtime': {'status': acceptance.STATUS_NOT_RUN, 'cases': {},
                            'reason': 'external Windows x64 runtime; R11W owns the real run'},
    }


def all_steps(status=acceptance.STATUS_PASS):
    return {'boundary': status, 'shell': status, 'package': status, 'browser': status,
            'remediation': status, 'legacy': status, 'docs': status, 'new_suite': status,
            'windows_runtime': acceptance.STATUS_NOT_RUN}


# ---------------------------------------------------------------- matrix

def test_orchestrator_matrix_marks_missing_selector_not_run():
    evidence = synthetic_resolver_evidence()
    node = 'tests/remediation/test_p03r09cr.py::test_real_persisted_retry_counts_and_progress_reset'
    del evidence['remediation']['nodes'][node]
    matrix = acceptance.requirement_matrix(evidence)
    assert matrix['T06']['local_status'] == acceptance.STATUS_NOT_RUN
    assert matrix['T06']['status'] == acceptance.STATUS_NOT_RUN
    # An unrelated requirement stays green, so the flip is clause-local.
    assert matrix['T30']['local_status'] == acceptance.STATUS_PASS


def test_orchestrator_matrix_marks_failed_and_skipped_selectors_never_pass():
    failed = synthetic_resolver_evidence()
    node = 'tests/remediation/test_p03r02c.py::test_matrix_preview_real_requests_all_roles_targets_studies'
    failed['remediation']['nodes'][node] = 'failed'
    assert acceptance.requirement_matrix(failed)['T26']['local_status'] == acceptance.STATUS_FAIL

    skipped = synthetic_resolver_evidence()
    node = 'tests/remediation/test_p03r10.py::test_real_local_auto_run_matches_golden'
    skipped['remediation']['nodes'][node] = 'skipped'
    matrix = acceptance.requirement_matrix(skipped)
    assert matrix['T19']['local_status'] == acceptance.STATUS_NOT_RUN
    assert matrix['T19']['status'] != acceptance.STATUS_PASS


def test_recovery_code_clause_never_passes_without_its_evidence():
    """T28 must bind the real recovery-code security evidence.

    The clause that speaks about brute force, device proof and concurrent
    consumption must name the recovery-code module; invitation/activation and
    finish-display lookalikes may not stand in. Removing exactly one recovery
    selector makes the clause - and T28 - not pass while every unrelated
    selector stays green.
    """
    t28 = selectors_of('T28')
    security = []
    for label, selectors in acceptance.SUBREQUIREMENTS['T28'][1]:
        if label != '撤销与旧长许可兼容（真实壳）':
            security.extend(selectors)
    assert security, 'T28 security clauses must carry evidence'
    for selector in security:
        assert selector.startswith('pytest:tests/test_phase03_recovery_codes.py::') \
            or selector == 'pytest:tests/test_phase03_shell.py::test_recovery_code_needs_device_proof_and_binding', selector
    for lookalike in ('invitation', 'activation', 'shell_presentation', 'study_invitation'):
        assert not any(lookalike in selector for selector in t28), lookalike

    node = 'tests/test_phase03_recovery_codes.py::test_failed_attempts_commit_and_cap_at_five'
    assert f'pytest:{node}' in t28
    evidence = synthetic_resolver_evidence()
    assert evidence['remediation']['nodes'].get(node) == 'passed', 'the recovery suite runs freshly as legacy evidence'
    index, _label = clause_of('T28', f'pytest:{node}')
    del evidence['remediation']['nodes'][node]
    matrix = acceptance.requirement_matrix(evidence)
    assert matrix['T28']['clauses'][index]['status'] != acceptance.STATUS_PASS
    assert matrix['T28']['local_status'] != acceptance.STATUS_PASS
    assert matrix['T30']['local_status'] == acceptance.STATUS_PASS, 'the removal must stay clause-local'


def test_cleanup_and_checkpoint_clauses_never_pass_without_their_evidence():
    """T23/T24 must bind the checkpoint/cleanup evidence, not lookalikes."""
    t23 = selectors_of('T23')
    assert 'shell:native checkpoint bound to the first trial' in t23
    assert 'pytest:tests/remediation/test_p03r09ar.py::test_real_native_finish_guards_harness' in t23
    assert not any('test_cycle_guard_harness' in selector for selector in t23), \
        'the cycle-value guard is not checkpoint evidence'

    t24 = selectors_of('T24')
    cleanup = [selector for selector in t24 if selector.startswith('browser:native_cleanup.spec.js::')]
    assert len(cleanup) == 5, cleanup
    assert not any('test_p03r04r.py' in selector for selector in t24), \
        'server study-deletion file cleanup is not GEC queue cleanup evidence'

    evidence = synthetic_resolver_evidence()
    evidence['shell']['checks']['native checkpoint bound to the first trial'] = True
    index, _label = clause_of('T23', 'shell:native checkpoint bound to the first trial')
    del evidence['shell']['checks']['native checkpoint bound to the first trial']
    matrix = acceptance.requirement_matrix(evidence)
    assert matrix['T23']['clauses'][index]['status'] != acceptance.STATUS_PASS
    assert matrix['T23']['local_status'] != acceptance.STATUS_PASS

    evidence = synthetic_resolver_evidence()
    title = 'native_cleanup.spec.js::native durable completion and cleanup: cleanup_before_commit'
    evidence['browser']['tests'][title] = 'passed'
    index, _label = clause_of('T24', f'browser:{title}')
    del evidence['browser']['tests'][title]
    matrix = acceptance.requirement_matrix(evidence)
    assert matrix['T24']['clauses'][index]['status'] != acceptance.STATUS_PASS
    assert matrix['T24']['local_status'] != acceptance.STATUS_PASS


def test_orchestrator_passes_with_external_windows_runtime_still_not_run():
    """A full local matrix passes; the real Windows run stays the only open class."""
    matrix = acceptance.requirement_matrix(synthetic_resolver_evidence())
    verdict, reason = acceptance.overall_status(matrix, all_steps())
    assert verdict == acceptance.STATUS_PASS, reason
    assert 'external' in reason
    assert matrix['T20']['status'] == acceptance.STATUS_NOT_RUN
    assert matrix['T20']['local_status'] == acceptance.STATUS_PASS
    assert matrix['T29']['status'] == acceptance.STATUS_NOT_RUN


def test_aggregate_verify_is_not_an_alias_of_the_local_gate():
    """--verify without a real Windows run can never pass."""
    matrix = acceptance.requirement_matrix(synthetic_resolver_evidence())
    verdict, reason = acceptance.overall_status(matrix, all_steps(), require_windows=True)
    assert verdict == acceptance.STATUS_FAIL
    assert 'Windows' in reason or 'windows' in reason


def test_orchestrator_fails_on_a_local_step_failure_and_on_a_missing_local_requirement():
    matrix = acceptance.requirement_matrix(synthetic_resolver_evidence())
    steps = all_steps()
    steps['legacy'] = acceptance.STATUS_FAIL
    verdict, reason = acceptance.overall_status(matrix, steps)
    assert verdict == acceptance.STATUS_FAIL and 'legacy' in reason

    missing = synthetic_resolver_evidence()
    del missing['remediation']['nodes']['tests/remediation/test_p03r08.py::test_real_chrome_downloads_the_zip_and_matches_the_independent_golden']
    verdict, _reason = acceptance.overall_status(acceptance.requirement_matrix(missing), all_steps())
    assert verdict == acceptance.STATUS_FAIL, 'a missing local selector must never pass'


def test_orchestrator_boundary_check_false_is_fail():
    evidence = synthetic_resolver_evidence()
    evidence['boundary']['checks']['boundary_source_unchanged'] = False
    matrix = acceptance.requirement_matrix(evidence)
    assert matrix['T27']['local_status'] == acceptance.STATUS_FAIL


# ------------------------------------------------------------- root guards

def test_orchestrator_refuses_an_existing_or_outside_root(tmp_path, evidence_root):
    existing = evidence_root / 'already-here'
    existing.mkdir()
    with pytest.raises(acceptance.GateError):
        acceptance.guard_evidence_root(existing)
    with pytest.raises(acceptance.GateError):
        acceptance.guard_evidence_root(tmp_path / 'outside-project')
    code = acceptance.main(['--verify-local', '--evidence-root', str(existing)])
    assert code == 2
    assert existing.is_dir() and not any(existing.iterdir()), 'a refused run writes nothing'


def test_orchestrator_refuses_a_symlinked_evidence_component(evidence_root):
    link = evidence_root / 'link'
    target = evidence_root / 'real'
    target.mkdir()
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(acceptance.GateError):
        acceptance.guard_evidence_root(link / 'run')
    assert not (target / 'run').exists()


def test_boundary_evidence_root_guard_refuses_existing_and_symlink(evidence_root, tmp_path):
    migration = load_module('remediation_migration_boundary_guard', 'tools/remediation_migration.py')
    existing = evidence_root / 'boundary-existing'
    existing.mkdir()
    with pytest.raises(migration.RehearsalError):
        migration.guard_evidence_root(existing)
    with pytest.raises(migration.RehearsalError):
        migration.guard_evidence_root(tmp_path / 'outside')
    link = evidence_root / 'boundary-link'
    link.symlink_to(existing, target_is_directory=True)
    with pytest.raises(migration.RehearsalError):
        migration.guard_evidence_root(link / 'run')
    assert migration.main(['--verify-boundary', '--evidence-root', str(existing)]) == 2


def test_windows_prepare_refuses_an_existing_or_outside_root(evidence_root, tmp_path):
    windows = load_module('remediation_windows_guard', 'tools/remediation_windows.py')
    existing = evidence_root / 'windows-existing'
    existing.mkdir()
    with pytest.raises(windows.KitError):
        windows.guard_evidence_root(existing)
    with pytest.raises(windows.KitError):
        windows.guard_evidence_root(tmp_path / 'outside-windows')
    code = windows.main(['--prepare', '--evidence-root', str(existing)])
    assert code != 0
    assert not any(existing.iterdir()), 'a refused preparation writes nothing'
    # A missing kit is refused before any platform branch is considered.
    code, report = windows.verify_kit(kit_root=None)
    assert code == 2 and report.get('reason') == 'kit_root_missing'


# ------------------------------------------------- tool boundary regression

def test_integration_tools_reject_symlinked_inputs_and_keep_canary_bytes(tmp_path):
    """A link or reparse point never redirects a copy or a run; bytes stay put."""
    source = tmp_path / 'source-package'
    (source / 'packages').mkdir(parents=True)
    (source / 'packages' / 'build.bin').write_bytes(b'synthetic-package-bytes')
    outside = tmp_path / 'external-canary'
    outside.mkdir()
    canary = outside / 'canary.bin'
    canary.write_bytes(b'external-canary-bytes')
    canary_before = canary.read_bytes()

    (source / 'escape.bin').symlink_to(canary)
    with pytest.raises(gec.VerificationError):
        gec.reject_unsafe_tree(source, 'source package')
    run_root = tmp_path / 'run-root'
    run_root.mkdir()
    with pytest.raises(gec.VerificationError):
        gec.copy_package_for_run(source, run_root)
    assert not (run_root / 'package_copy').exists(), 'a refused copy creates nothing'
    assert canary.read_bytes() == canary_before

    # A clean source still copies byte-identically (no false positive).
    (source / 'escape.bin').unlink()
    copy = gec.copy_package_for_run(source, run_root)
    assert (copy / 'packages' / 'build.bin').read_bytes() == b'synthetic-package-bytes'
    assert canary.read_bytes() == canary_before

    # The Windows program selector refuses a link even when the suffix matches.
    program_root = tmp_path / 'program-root'
    program_root.mkdir()
    (program_root / 'entry.exe').symlink_to(canary)
    with pytest.raises(gec.VerificationError):
        gec.resolve_windows_program(program_root, 'entry.exe')
    assert canary.read_bytes() == canary_before


# --------------------------------------------- this-round artifact binding

def artifact_entry(windows, path):
    return {'path': str(path), 'sha256': windows.sha256_file(path), 'size': path.stat().st_size}


def test_artifact_binding_refuses_legacy_defaults_and_stale_or_tampered_bytes():
    """The gate binds this round's unique build root, never the old defaults.

    A valid binding resolves; the historical build/ and build/native roots, a
    real old default artifact named by a fresh root, a stale program-source
    digest and tampered bytes are all refused, so an unbound or substituted
    artifact can never stand in for the current sources.
    """
    windows = load_module('remediation_windows_artifact_binding', 'tools/remediation_windows.py')
    source = windows.program_source_digest()
    fresh = acceptance.new_unique_root(acceptance.BUILD_BASE)
    (fresh / 'web').mkdir()
    web = fresh / 'web' / 'synthetic_web.zip'
    web.write_bytes(b'synthetic-web-bytes')
    native = fresh / 'native'
    native.mkdir()
    descriptor = native / 'descriptor.json'
    descriptor.write_text('{"platform": "macos_arm64"}', encoding='utf-8')
    binary = native / 'GEP Synthetic Experiment.app' / 'Contents' / 'MacOS' / 'GEP Synthetic Experiment'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'synthetic-native-bytes')
    document = {'format': acceptance.ARTIFACT_BINDING_FORMAT, 'verdict': 'ok', 'build_root': str(fresh),
                'program_source_digest': source,
                'program_source_digest_after': source,
                'program_source_inputs': windows.program_source_inputs(),
                'program_source_inputs_after': windows.program_source_inputs(),
                'artifacts': {'web_zip': artifact_entry(windows, web),
                              'native_zip': artifact_entry(windows, web),
                              'native_descriptor': artifact_entry(windows, descriptor),
                              'native_binary': artifact_entry(windows, binary)}}
    resolved = acceptance.validate_artifact_binding(document, source)
    assert resolved['web_zip'] == str(web.resolve()) and resolved['native_binary'] == str(binary.resolve())

    # The historical default roots are refused, even with valid digests.
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(dict(document, build_root=str(ROOT / 'build' / 'native')), source)
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(dict(document, build_root=str(ROOT / 'build')), source)

    # A real old default artifact (its recorded digest is genuine) still cannot
    # be bound to the fresh root: it is refused as outside the build root.
    old_default = ROOT / 'build' / 'synthetic_web.zip'
    outside = dict(document, artifacts={**document['artifacts'], 'web_zip': artifact_entry(windows, old_default)})
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(outside, source)

    # A stale program-source digest is refused instead of inherited.
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(dict(document, program_source_digest='f' * 64), source)

    # Tampered bytes are refused.
    web.write_bytes(b'other-bytes')
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(document, source)


def test_verifiers_refuse_unbound_or_partial_explicit_artifacts(evidence_root):
    """A missing/partial/legacy binding stops both verifiers before they start."""
    old_defaults = {
        'web_zip': ROOT / 'build' / 'synthetic_web.zip',
        'native_zip': ROOT / 'build' / 'native' / 'synthetic.zip',
        'native_descriptor': ROOT / 'build' / 'native' / 'descriptor.json',
        'native_binary': ROOT / 'build' / 'native' / 'GEP Synthetic Experiment.app' / 'Contents' / 'MacOS'
                         / 'GEP Synthetic Experiment',
    }
    partial_root = evidence_root / 'shell-partial-refusal'
    partial = subprocess.run([sys.executable, str(ROOT / 'tools' / 'phase03_verify_shell.py'),
                              '--verify', '--root', str(partial_root), '--web-zip', str(old_defaults['web_zip'])],
                             cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert partial.returncode == 2, partial.stdout[-800:]
    assert 'REFUSED' in partial.stdout
    assert not partial_root.exists(), 'a refused verifier creates nothing'

    binding = evidence_root / 'legacy-default-binding.json'
    binding.write_text(json.dumps({'format': acceptance.ARTIFACT_BINDING_FORMAT, 'verdict': 'ok',
                                   'build_root': str(ROOT / 'build' / 'native'),
                                   'program_source_digest': 'f' * 64,
                                   'artifacts': {key: {'path': str(path), 'sha256': 'f' * 64, 'size': 0}
                                                 for key, path in old_defaults.items()}}), encoding='utf-8')
    for tool, explicit in ((ROOT / 'tools' / 'phase03_verify_shell.py', ['--web-zip', str(old_defaults['web_zip'])]),
                           (ROOT / 'tools' / 'phase03_verify_package.py',
                            ['--native-zip', str(old_defaults['native_zip'])])):
        root = evidence_root / f'{tool.stem}-legacy-refusal'
        command = [sys.executable, str(tool), '--verify', '--root', str(root), '--binding', str(binding),
                   *explicit, '--native-descriptor', str(old_defaults['native_descriptor']),
                   '--native-binary', str(old_defaults['native_binary'])]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
        assert result.returncode == 2, (tool.name, result.stdout[-800:], result.stderr[-800:])
        assert 'REFUSED' in result.stdout
        assert not root.exists(), 'a refused verifier creates nothing'


def test_orchestrator_passes_bound_artifacts_to_the_tools(tmp_path):
    """The gate hands each verifier exactly the binding and its own artifacts."""
    artifacts = {'binding_path': str(tmp_path / 'artifact_binding.json'),
                 'web_zip': str(tmp_path / 'web.zip'), 'native_zip': str(tmp_path / 'native.zip'),
                 'native_descriptor': str(tmp_path / 'descriptor.json'),
                 'native_binary': str(tmp_path / 'binary')}
    shell = acceptance.verifier_command('shell', acceptance.SHELL_VERIFIER, tmp_path / 'shell', artifacts)
    package = acceptance.verifier_command('package', acceptance.PACKAGE_VERIFIER, tmp_path / 'package', artifacts)
    assert '--binding' in shell and '--web-zip' in shell and '--native-zip' in shell
    assert '--binding' in package and '--native-zip' in package and '--web-zip' not in package
    for command in (shell, package):
        assert '--native-descriptor' in command and '--native-binary' in command
    legacy = acceptance.verifier_command('shell', acceptance.SHELL_VERIFIER, tmp_path / 'legacy', None)
    assert '--binding' not in legacy and legacy[-2:] == ['--root', str(tmp_path / 'legacy')]


# --------------------------------------------- strict frozen member manifests

def make_accounts(root, instance_id='synthetic-instance'):
    path = root / 'runtime_accounts.json'
    path.write_text(json.dumps({'format': 'gep-windows-accounts/v1', 'instance_id': instance_id,
                                'member': {'username': 'synthetic-member', 'password': 'synthetic-password'}}),
                    encoding='utf-8')
    return path


def make_kit(root, source_digest, program_sha256='a' * 64):
    """One minimal but structurally valid frozen kit for validator tests."""
    windows = load_module('remediation_windows_strict_kit', 'tools/remediation_windows.py')
    source_inputs = windows.program_source_inputs()
    (root / 'operator' / 'harness').mkdir(parents=True)
    (root / 'README.md').write_text('# synthetic kit\n', encoding='utf-8')
    (root / 'operator' / 'harness' / 'windows_native_harness.py').write_text(
        '# synthetic harness\n', encoding='utf-8')
    modes = {}
    for mode in ('anonymous', 'id', 'password'):
        package = root / 'delivery' / mode / f'gep-{mode}.zip'
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_bytes(('zip-' + mode).encode())
        modes[mode] = {'delivery': package.relative_to(root).as_posix(),
                       'package_sha256': windows.sha256_file(package),
                       'program_sha256': program_sha256, 'study_id': 'study', 'release_id': mode,
                       'build_id': 'build', 'package_size': package.stat().st_size}
    (root / 'releases.json').write_bytes(windows.canonical_json(
        {'kit_format': windows.KIT_VERSION, 'prepared_by': windows.PREPARED_BY,
         'prepared_for': windows.PREPARED_FOR, 'program_sha256': program_sha256,
         'program_source_digest': source_digest, 'program_source_inputs': source_inputs, 'modes': modes}))
    (root / 'operator' / 'runtime.json').write_bytes(windows.canonical_json(
        {'format': windows.RUNTIME_FORMAT, 'instance_id': 'synthetic-instance', 'port': 8123,
         'api_url': 'http://127.0.0.1:8123',
         'tunnel': {'listen_port': 8223, 'target_port': 8123}, 'studies': {}, 'member': {}}))
    members = {}
    for path in sorted(root.rglob('*')):
        if path.is_file() and path != root / 'integrity.json':
            members[path.relative_to(root).as_posix()] = {
                'sha256': windows.sha256_file(path), 'size': path.stat().st_size}
    (root / 'integrity.json').write_bytes(windows.canonical_json(
        {'format': windows.KIT_VERSION, 'program_sha256': program_sha256,
         'program_source_digest': source_digest, 'program_source_inputs': source_inputs, 'members': members}))
    return root


def test_strict_manifest_refuses_empty_missing_tampered_extra_and_unsafe_members(tmp_path):
    windows = load_module('remediation_windows_strict_manifest', 'tools/remediation_windows.py')
    canary = tmp_path / 'external-canary.bin'
    canary.write_bytes(b'external-canary')
    before = canary.read_bytes()
    source = 'b' * 64

    # A valid kit passes the strict check (no false positive).
    good = make_kit(tmp_path / 'good-kit', source)
    assert windows.verify_kit_strict(good, expected_source_digest=source) == []

    # An empty manifest is refused, never a zero-iteration pass.
    empty = make_kit(tmp_path / 'empty-kit', source)
    (empty / 'integrity.json').write_bytes(windows.canonical_json(
        {'format': windows.KIT_VERSION, 'program_sha256': 'a' * 64,
         'program_source_digest': source, 'members': {}}))
    problems = windows.verify_kit_strict(empty, expected_source_digest=source)
    assert any('declares no members' in problem for problem in problems), problems

    # A missing declared member is refused.
    missing = make_kit(tmp_path / 'missing-kit', source)
    (missing / 'README.md').unlink()
    problems = windows.verify_kit_strict(missing, expected_source_digest=source)
    assert any('member is missing' in problem for problem in problems), problems

    # A tampered member is refused.
    tampered = make_kit(tmp_path / 'tampered-kit', source)
    (tampered / 'README.md').write_text('# changed\n', encoding='utf-8')
    problems = windows.verify_kit_strict(tampered, expected_source_digest=source)
    assert any('digest changed' in problem or 'size changed' in problem for problem in problems), problems

    # An extra executable (any undeclared file) is refused.
    extra = make_kit(tmp_path / 'extra-kit', source)
    (extra / 'extra.exe').write_bytes(b'MZ-synthetic')
    problems = windows.verify_kit_strict(extra, expected_source_digest=source)
    assert any('undeclared files' in problem for problem in problems), problems

    # A traversing or absolute member name is refused before any file is read.
    traversal = make_kit(tmp_path / 'traversal-kit', source)
    document = json.loads((traversal / 'integrity.json').read_text(encoding='utf-8'))
    document['members']['../escape.bin'] = {'sha256': 'c' * 64, 'size': 1}
    (traversal / 'integrity.json').write_bytes(windows.canonical_json(document))
    problems = windows.verify_kit_strict(traversal, expected_source_digest=source)
    assert any('unsafe member name' in problem for problem in problems), problems

    # A symlinked member is refused with lstat semantics.
    linked = make_kit(tmp_path / 'linked-kit', source)
    (linked / 'README.md').unlink()
    (linked / 'README.md').symlink_to(canary)
    problems = windows.verify_kit_strict(linked, expected_source_digest=source)
    assert any('symbolic link' in problem for problem in problems), problems

    # A stale source binding is refused instead of inherited.
    stale = make_kit(tmp_path / 'stale-kit', 'd' * 64)
    problems = windows.verify_kit_strict(stale, expected_source_digest=source)
    assert any('stale preparation' in problem for problem in problems), problems

    assert canary.read_bytes() == before


def test_windows_verify_refuses_before_any_start_without_ssh_configuration(evidence_root, tmp_path,
                                                                           monkeypatch):
    """The Mac branch refuses explicitly; a bad kit is refused even earlier."""
    windows = load_module('remediation_windows_ssh_guard', 'tools/remediation_windows.py')
    source = windows.program_source_digest()
    for name in ('GEP_TEST_SSH_HOST', 'GEP_TEST_SSH_HOST_KEY_ALIAS', 'GEP_TEST_WINDOWS_ROOT',
                 'GEP_TEST_WINDOWS_ACCOUNTS', 'GEP_TEST_WINDOWS_PYTHON'):
        monkeypatch.delenv(name, raising=False)

    # A structurally invalid kit is refused as a kit problem, before the SSH
    # configuration is even considered, and nothing is started.
    bad_kit = make_kit(evidence_root / 'bad-kit', source)
    (bad_kit / 'integrity.json').write_bytes(windows.canonical_json(
        {'format': windows.KIT_VERSION, 'program_sha256': 'a' * 64,
         'program_source_digest': source, 'members': {}}))
    accounts = make_accounts(evidence_root)
    code, report = windows.verify_kit(bad_kit, evidence_root=evidence_root / 'verify-bad',
                                      accounts_path=accounts)
    assert code == 1 and report.get('problems'), report
    assert any('declares no members' in problem for problem in report['problems'])
    assert report.get('windows_verified') is False

    # A valid kit without an SSH configuration is refused with an exact reason;
    # no local command is ever reported as a Windows pass.
    good_kit = make_kit(evidence_root / 'good-kit', source)
    code, report = windows.verify_kit(good_kit, evidence_root=evidence_root / 'verify-nossh',
                                      accounts_path=accounts)
    assert code == 2 and report.get('reason') == 'ssh_not_configured', report
    assert report.get('windows_verified') is False
    assert not (evidence_root / 'verify-nossh' / 'windows-run' / 'run.json').exists()


def test_windows_preparation_fails_closed_on_empty_manifest_and_unreadable_report(evidence_root):
    """The preparation gate never inherits a shrunken manifest or bad JSON."""
    windows = load_module('remediation_windows_prepare_strict', 'tools/remediation_windows.py')
    source = windows.program_source_digest()
    base = evidence_root / 'windows-base'

    stale = base / 'stale' / 'prepare_report.json'
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({'verdict': 'ok', 'build': {'program_source_digest': 'e' * 64,
                                                             'program_sha256': 'a' * 64}}),
                     encoding='utf-8')
    assert acceptance.windows_preparation(base)['status'] == acceptance.STATUS_NOT_RUN

    broken = base / 'broken' / 'prepare_report.json'
    broken.parent.mkdir(parents=True)
    broken.write_text('{not json', encoding='utf-8')
    assert acceptance.windows_preparation(base)['status'] == acceptance.STATUS_FAIL

    shrunken = base / 'shrunken'
    make_kit(shrunken / 'kit', source, program_sha256='a' * 64)
    (shrunken / 'kit' / 'integrity.json').write_bytes(windows.canonical_json(
        {'format': windows.KIT_VERSION, 'program_sha256': 'a' * 64,
         'program_source_digest': source, 'members': {}}))
    human = shrunken / 'human'
    human.mkdir()
    (human / 'README.md').write_text('# synthetic\n', encoding='utf-8')
    (human / 'sha256.json').write_bytes(windows.canonical_json(
        {'format': 'gep-human-test-package/v1', 'program_source_digest': source, 'members': {}}))
    (shrunken / 'prepare_report.json').write_text(json.dumps(
        {'verdict': 'ok', 'build': {'program_source_digest': source, 'program_sha256': 'a' * 64}}),
        encoding='utf-8')
    report = acceptance.windows_preparation(base)
    assert report['status'] == acceptance.STATUS_FAIL, report
    assert any('declares no members' in problem for problem in report['problems']), report


# ------------------------------------------------------------ real rehearsal

def test_boundary_rehearsal_runs_real_and_keeps_source_unchanged(evidence_root):
    """The mandatory verify-local boundary gate really runs and passes."""
    fresh = evidence_root / f'boundary-{secrets.token_hex(4)}'
    result = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'remediation_migration.py'),
         '--verify-boundary', '--evidence-root', str(fresh)],
        cwd=ROOT, capture_output=True, text=True, timeout=900)
    log = evidence_root / 'boundary_run.log'
    log.write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
    assert result.returncode == 0, (result.stdout[-2000:], result.stderr[-2000:])
    report = json.loads((fresh / 'report.json').read_text(encoding='utf-8'))
    assert report['verdict'] == 'ok'
    assert report['checks'] and all(report['checks'].values()), report['checks']
    facts = report['boundary']
    assert facts['old_rows_preserved'] is True and facts['old_row_count'] >= 4
    assert facts['legacy_existing_activated'] is True and facts['legacy_new_application_activated'] is True
    assert facts['v2_existing_bound'] is True and facts['v2_new_application_unbound'] is True
    assert facts['v2_activation'] is True and facts['v2_activation_replay_refused'] is True
    assert facts['deletion_completed'] is True and facts['deleted_principal_marked'] is True
    assert facts['bound_invitation_revoked'] is True and facts['consumed_row_untouched'] is True
    assert facts['audit_kept_and_rebound'] is True and facts['references_kept'] is True
    assert report['checks']['boundary_source_unchanged'] is True
    # A second run must use a brand-new root: the first root is refused.
    again = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'remediation_migration.py'),
         '--verify-boundary', '--evidence-root', str(fresh)],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert again.returncode == 2, again.stdout[-500:]
    assert (fresh / 'report.json').is_file(), 'the failed attempt kept its files'


def test_new_gate_module_requires_its_flag(evidence_root):
    with pytest.raises(SystemExit):
        acceptance.main([])
    with pytest.raises(SystemExit):
        acceptance.main(['--verify-local', '--verify'])
