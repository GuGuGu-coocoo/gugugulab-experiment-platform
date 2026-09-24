"""P03R11AR: the corrected R11 evidence chain, negative first.

These tests own the four defects the P03R11A review found, and every fixture
here is a *validator fixture*: each test proves that a defect is refused, and
that a tree without real device facts is refused too. They never claim a device
run, never produce a human result and never stand in for R11W:

* a Windows run is only accepted as a complete evidence tree - report format,
  current program source digest and input set, frozen program/kit/run identity,
  the raw harness document, real device OS/architecture/build facts and
  per-event reconciliation exports re-read with their digests - never as a short
  summary of six PASS strings;
* the program source binding covers the four ``packages/gec_web/*.js`` files the
  Web build really ships plus the Godot project and packagers; an isolated
  source copy proves a Web SDK change invalidates the digest, and a binding that
  recorded different sources before and after its build is refused;
* every binding entry point (the acceptance gate, the shell verifier, the
  package verifier) and the Windows kit manifest refuse a symbolic link at any
  ancestor component with lstat semantics (not ``resolve()``), leaving an
  external canary and old artifact bytes identical and starting nothing;
* the unpacked macOS runtime resources must match the bound archive member by
  member (executable, ``.pck``, dynamic libraries), and the shell verifier
  refuses before any server or program starts when they do not.

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r11ar/<UTC>-<random>/`` root.
"""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import phase03_remediation_acceptance as acceptance  # noqa: E402
import remediation_windows as windows  # noqa: E402

KIT_MODES = ('anonymous', 'id', 'password')
CASE_IDS = ('WN01', 'WN02', 'WN03', 'WN04', 'WN05', 'WN06')


# ------------------------------------------------------------ fixtures

def make_kit(root, *, program_sha256='a' * 64, source_digest=None, instance_id='synthetic-instance'):
    """One minimal but structurally valid frozen kit (same shape as --prepare)."""
    root = Path(root)
    source_digest = source_digest or windows.program_source_digest()
    source_inputs = windows.program_source_inputs()
    (root / 'operator' / 'harness').mkdir(parents=True)
    (root / 'README.md').write_text('# synthetic kit\n', encoding='utf-8')
    (root / 'operator' / 'harness' / 'windows_native_harness.py').write_text(
        '# synthetic harness\n', encoding='utf-8')
    modes = {}
    for mode in KIT_MODES:
        package = root / 'delivery' / mode / f'gep-{mode}.zip'
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_bytes(('zip-' + mode).encode())
        modes[mode] = {'delivery': package.relative_to(root).as_posix(),
                       'package_sha256': windows.sha256_file(package),
                       'artifact_sha256': windows.sha256_file(package),
                       'program_sha256': program_sha256,
                       'study_id': f'study-{mode}', 'release_id': f'release-{mode}',
                       'build_id': f'build-{mode}', 'package_size': package.stat().st_size}
    (root / 'releases.json').write_bytes(windows.canonical_json(
        {'kit_format': windows.KIT_VERSION, 'prepared_by': windows.PREPARED_BY,
         'prepared_for': windows.PREPARED_FOR, 'instance_id': instance_id,
         'api_url': 'http://127.0.0.1:8123', 'program_sha256': program_sha256,
         'program_source_digest': source_digest, 'program_source_inputs': source_inputs,
         'modes': modes}))
    (root / 'operator' / 'runtime.json').write_bytes(windows.canonical_json(
        {'format': windows.RUNTIME_FORMAT, 'instance_id': instance_id, 'port': 8123,
         'api_url': 'http://127.0.0.1:8123',
         'tunnel': {'listen_port': 8223, 'target_port': 8123},
         'studies': {mode: {key: value for key, value in entry.items()
                            if key in ('study_id', 'release_id', 'build_id', 'package_sha256')}
                     for mode, entry in modes.items()},
         'member': {}}))
    members = {}
    for path in sorted(root.rglob('*')):
        if path.is_file() and path != root / 'integrity.json':
            members[path.relative_to(root).as_posix()] = {
                'sha256': windows.sha256_file(path), 'size': path.stat().st_size}
    (root / 'integrity.json').write_bytes(windows.canonical_json(
        {'format': windows.KIT_VERSION, 'program_sha256': program_sha256,
         'program_source_digest': source_digest, 'program_source_inputs': source_inputs,
         'members': members}))
    return root


def make_windows_run_evidence(base, kit, *, program_sha256='a' * 64, source_digest=None,
                              run_id='synthetic-run'):
    """Report + raw harness + bound exports, anchored at ``base`` (validator fixture)."""
    base = Path(base)
    base.mkdir(parents=True)
    source_digest = source_digest or windows.program_source_digest()
    run_root = base / 'windows-run'
    run_root.mkdir()
    cases = []
    for case_id in CASE_IDS:
        evidence = {}
        if case_id in ('WN02', 'WN03', 'WN04'):
            export = run_root / 'evidence' / case_id / 'export.jsonl'
            export.parent.mkdir(parents=True)
            export.write_bytes((case_id + '-export\n').encode())
            evidence = {'export': {'path': f'evidence/{case_id}/export.jsonl',
                                   'sha256': windows.sha256_file(export)}}
        cases.append({'id': case_id, 'title': case_id, 'status': 'PASS',
                      'checks': [{'ok': True, 'label': f'{case_id} synthetic check', 'detail': None}],
                      'evidence': evidence, 'reason': None})
    kit_releases = json.loads((Path(kit) / 'releases.json').read_text(encoding='utf-8'))
    studies = {mode: {key: entry[key] for key in ('study_id', 'release_id', 'build_id', 'package_sha256')
                      if key in entry}
               for mode, entry in kit_releases['modes'].items()}
    harness_path = run_root / 'run.json'
    document = {
        'format': windows.RUN_FORMAT, 'mode': 'run', 'run_id': run_id,
        'host': {'run_id': run_id, 'platform': 'win32', 'processor_architecture': 'AMD64',
                 'windows_build': '10.0.26200', 'console_session': 1, 'interactive': True,
                 'machine': {'Caption': 'Microsoft Windows 11 Pro', 'Version': '10.0.26200',
                             'Arch': '64-bit', 'System': 'x64-based PC'}},
        'binding': {'instance_id': 'synthetic-instance', 'studies': studies},
        'expected': {'program_source_digest': source_digest, 'program_sha256': program_sha256},
        'harness_sha256': windows.sha256_file(kit / 'operator' / 'harness'
                                              / 'windows_native_harness.py'),
        'cases': cases, 'checks_total': sum(len(case['checks']) for case in cases),
        'checks_failed': 0, 'runtime_acceptance': 'PASS'}
    harness_path.write_text(json.dumps(document), encoding='utf-8')
    report = {
        'format': windows.VERIFY_FORMAT, 'verdict': 'ok', 'windows_verified': True,
        'program_source_digest': source_digest, 'program_sha256': program_sha256,
        'kit_instance_id': 'synthetic-instance', 'kit_root': str(kit),
        'kit_integrity_sha256': windows.sha256_file(kit / 'integrity.json'),
        'run_id': run_id, 'harness_path': 'windows-run/run.json',
        'harness_sha256': windows.sha256_file(harness_path),
        'device': {'platform': 'win32', 'architecture': 'AMD64', 'os': 'Microsoft Windows 11 Pro',
                   'os_architecture': '64-bit', 'system_type': 'x64-based PC',
                   'windows_build': '10.0.26200', 'console_session': 1},
        'cases': {case_id: 'PASS' for case_id in CASE_IDS}}
    (base / 'verify_report.json').write_text(json.dumps(report), encoding='utf-8')
    return base


def rewrite_report(evidence, mutate):
    path = Path(evidence) / 'verify_report.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    mutate(report)
    path.write_text(json.dumps(report), encoding='utf-8')
    return report


def rewrite_harness(evidence, mutate):
    path = Path(evidence) / 'windows-run' / 'run.json'
    document = json.loads(path.read_text(encoding='utf-8'))
    mutate(document)
    path.write_text(json.dumps(document), encoding='utf-8')
    rewrite_report(evidence, lambda report: report.update(
        {'harness_sha256': windows.sha256_file(path)}))
    return document


def refresh_harness_digest(evidence):
    path = Path(evidence) / 'windows-run' / 'run.json'
    rewrite_report(evidence, lambda report: report.update(
        {'harness_sha256': windows.sha256_file(path)}))


def validate(evidence, kit, *, program_sha256='a' * 64):
    return windows.validate_windows_run_evidence(
        evidence, expected_source_digest=windows.program_source_digest(),
        expected_program_sha256=program_sha256, kit_root=kit,
        expected_source_inputs=windows.program_source_inputs())


# ---------------------------------------------------- complete Windows run

def test_windows_summary_only_report_is_refused(evidence_root, tmp_path):
    """The review probe: six PASS strings without sources or raw files is not a pass."""
    root = evidence_root / 'summary-only'
    root.mkdir()
    (root / 'verify_report.json').write_text(json.dumps(
        {'verdict': 'ok', 'windows_verified': True, 'cases': {case: 'PASS' for case in CASE_IDS}}),
        encoding='utf-8')
    problems = windows.validate_windows_run_evidence(
        root, expected_source_digest=windows.program_source_digest())
    assert problems, 'a summary-only report must never validate'
    for marker in ('format', 'program sources', 'frozen program', 'kit instance', 'run identity',
                   'device facts', 'raw harness document'):
        assert any(marker in problem for problem in problems), (marker, problems)

    # The aggregate gate refuses it as FAIL, and refuses even earlier without a
    # fresh preparation bound to the current sources.
    kit = make_kit(tmp_path / 'summary-kit')
    fresh = {'status': acceptance.STATUS_PASS, 'program_sha256': 'a' * 64, 'kit_root': str(kit)}
    result = acceptance.run_windows_runtime(root, None, str(root), preparation=fresh)
    assert result['status'] == acceptance.STATUS_FAIL, result
    result = acceptance.run_windows_runtime(root, None, str(root),
                                            preparation={'status': acceptance.STATUS_NOT_RUN})
    assert result['status'] == acceptance.STATUS_FAIL
    assert 'fresh preparation' in result['reason']


def test_windows_run_evidence_is_re_read_and_bound(evidence_root, tmp_path):
    """A complete tree validates; the gate proves it from the files, not a bool."""
    kit = make_kit(tmp_path / 'kit')
    evidence = make_windows_run_evidence(evidence_root / 'complete-run', kit)
    assert validate(evidence, kit) == []

    result = acceptance.run_windows_runtime(
        None, None, str(evidence),
        preparation={'status': acceptance.STATUS_PASS, 'program_sha256': 'a' * 64, 'kit_root': str(kit)})
    assert result['status'] == acceptance.STATUS_PASS, result
    assert result['cases'] == {case: 'PASS' for case in CASE_IDS}

    # Tampering with the raw harness document itself (its own digest no longer
    # matches the report) is refused even though every case still says PASS.
    rewrite_harness(evidence, lambda document: document['cases'][0].update({'status': 'FAIL'}))
    refresh_harness_digest(evidence)
    problems = validate(evidence, kit)
    assert any('did not pass' in problem for problem in problems), problems


def test_windows_run_evidence_refuses_stale_tampered_and_external_evidence(evidence_root, tmp_path):
    """Stale source, wrong program, changed artifact, missing case and an
    external path are all refused; the external canary and the old artifact
    bytes stay identical."""
    kit = make_kit(tmp_path / 'kit')
    canary_dir = tmp_path / 'external'
    canary_dir.mkdir()
    canary = canary_dir / 'canary.bin'
    canary.write_bytes(b'external-canary-bytes')
    old_default = ROOT / 'build' / 'synthetic_web.zip'
    old_before = windows.sha256_file(old_default) if old_default.is_file() else None
    canary_before = canary.read_bytes()

    # A stale program source digest (old evidence from another tree).
    stale = make_windows_run_evidence(evidence_root / 'stale-run', kit)
    rewrite_report(stale, lambda report: report.update({'program_source_digest': 'f' * 64}))
    assert any('stale evidence' in problem for problem in validate(stale, kit))

    # A program digest that is not the fresh preparation's frozen program.
    wrong_program = make_windows_run_evidence(evidence_root / 'wrong-program-run', kit)
    problems = validate(wrong_program, kit, program_sha256='b' * 64)
    assert any('different frozen program' in problem for problem in problems), problems

    # A changed reconciliation export (the recorded digest no longer holds).
    tampered = make_windows_run_evidence(evidence_root / 'tampered-export-run', kit)
    (tampered / 'windows-run' / 'evidence' / 'WN03' / 'export.jsonl').write_bytes(b'changed\n')
    problems = validate(tampered, kit)
    assert any('digest changed' in problem for problem in problems), problems

    # A missing case can never be a pass.
    missing_case = make_windows_run_evidence(evidence_root / 'missing-case-run', kit)
    rewrite_harness(missing_case, lambda document: document.update(
        {'cases': [case for case in document['cases'] if case['id'] != 'WN05']}))
    problems = validate(missing_case, kit)
    assert any('missing case WN05' in problem for problem in problems), problems

    # No device facts (a synthetic tree cannot masquerade as a device run).
    no_device = make_windows_run_evidence(evidence_root / 'no-device-run', kit)
    rewrite_report(no_device, lambda report: report.update(
        {'device': {'platform': 'darwin', 'architecture': 'arm64'}}))
    problems = validate(no_device, kit)
    assert any('Windows host' in problem for problem in problems), problems
    assert any('architecture' in problem for problem in problems), problems

    # An absolute external path with a true digest is refused as an escape.
    external = make_windows_run_evidence(evidence_root / 'external-path-run', kit)
    document = json.loads((external / 'windows-run' / 'run.json').read_text(encoding='utf-8'))
    document['cases'][1]['evidence'] = {'export': {'path': str(canary),
                                                   'sha256': windows.sha256_file(canary)}}
    (external / 'windows-run' / 'run.json').write_text(json.dumps(document), encoding='utf-8')
    refresh_harness_digest(external)
    problems = validate(external, kit)
    assert any('safe relative member' in problem for problem in problems), problems

    # A traversing relative path is refused as well.
    traversal = make_windows_run_evidence(evidence_root / 'traversal-run', kit)
    rewrite_harness(traversal, lambda doc: doc['cases'][1]['evidence'].update(
        {'export': {'path': '../outside.jsonl', 'sha256': 'e' * 64}}))
    problems = validate(traversal, kit)
    assert any('safe relative member' in problem for problem in problems), problems

    # A raw harness document outside this evidence root is refused.
    relocated = make_windows_run_evidence(evidence_root / 'relocated-run', kit)
    outside_document = external / 'windows-run' / 'run.json'
    rewrite_report(relocated, lambda report: report.update(
        {'harness_path': str(outside_document), 'harness_sha256': windows.sha256_file(outside_document)}))
    problems = validate(relocated, kit)
    assert any('safe relative member' in problem for problem in problems), problems

    assert canary.read_bytes() == canary_before
    if old_before is not None:
        assert windows.sha256_file(old_default) == old_before, 'the old default artifact bytes must not change'


# ------------------------------------------------------ source input binding

def test_program_source_binding_includes_web_sdk_and_packager_inputs(tmp_path, evidence_root):
    """The real packaging inputs are bound; a Web SDK change is never invisible."""
    names = [entry['name'] for entry in windows.program_source_inputs()]
    for required in ('packages/gec_web/sdk.js', 'packages/gec_web/bridge.js',
                     'packages/gec_web/inputs.js', 'packages/gec_web/shell.js',
                     'tools/build.py', 'tools/package_build.py', 'tools/windows_template.py'):
        assert required in names, (required, names[:10])
    assert any(name.endswith('project.godot') for name in names), names[:10]

    # An isolated source copy: the real sources are never edited for a test.
    copy = tmp_path / 'source-copy'
    (copy / 'examples').mkdir(parents=True)
    (copy / 'tools').mkdir()
    (copy / 'packages').mkdir()
    shutil.copytree(ROOT / 'examples' / 'synthetic_experiment',
                    copy / 'examples' / 'synthetic_experiment',
                    ignore=shutil.ignore_patterns('.godot', '__pycache__'))
    shutil.copytree(ROOT / 'packages' / 'gec_web', copy / 'packages' / 'gec_web')
    for name in ('tools/build.py', 'tools/package_build.py', 'tools/windows_template.py'):
        shutil.copy2(ROOT / name, copy / name)
    baseline = windows.program_source_digest(copy)
    assert windows.program_source_inputs(copy), 'the isolated copy must list its inputs'

    sdk = copy / 'packages' / 'gec_web' / 'sdk.js'
    sdk_baseline = sdk.read_bytes()
    sdk.write_bytes(sdk_baseline + b'\n// synthetic web sdk change\n')
    web_changed = windows.program_source_digest(copy)
    assert web_changed != baseline, 'a Web SDK change must invalidate the source digest'

    sdk.write_bytes(sdk_baseline)
    project = copy / 'examples' / 'synthetic_experiment' / 'project.godot'
    project.write_bytes(project.read_bytes() + b'\n; synthetic project change\n')
    assert windows.program_source_digest(copy) != baseline, 'a Godot project change must invalidate the digest'

    packager = copy / 'tools' / 'package_build.py'
    packager.write_bytes(packager.read_bytes() + b'\n# synthetic packager change\n')
    assert windows.program_source_digest(copy) != baseline, 'a packager change must invalidate the digest'

    # The real sources were never touched by this test.
    assert windows.program_source_digest() != web_changed


def test_artifact_binding_refuses_unstable_or_foreign_source_inputs(evidence_root, tmp_path):
    """A binding that recorded different sources before/after is refused."""
    root = acceptance.new_unique_root(acceptance.BUILD_BASE)
    (root / 'web').mkdir()
    web = root / 'web' / 'synthetic_web.zip'
    web.write_bytes(b'synthetic-web-bytes')
    native = root / 'native'
    native.mkdir()
    descriptor = native / 'descriptor.json'
    descriptor.write_text('{"platform": "macos_arm64"}', encoding='utf-8')
    binary = native / 'GEP Synthetic Experiment.app' / 'Contents' / 'MacOS' / 'GEP Synthetic Experiment'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'synthetic-native-bytes')
    source = windows.program_source_digest()
    inputs = windows.program_source_inputs()

    def entry(path):
        return {'path': str(path), 'sha256': windows.sha256_file(path), 'size': path.stat().st_size}

    document = {'format': acceptance.ARTIFACT_BINDING_FORMAT, 'verdict': 'ok', 'build_root': str(root),
                'program_source_digest': source, 'program_source_digest_after': source,
                'program_source_inputs': inputs, 'program_source_inputs_after': inputs,
                'artifacts': {'web_zip': entry(web), 'native_zip': entry(web),
                              'native_descriptor': entry(descriptor), 'native_binary': entry(binary)}}
    resolved = acceptance.validate_artifact_binding(document, source, expected_source_inputs=inputs)
    assert resolved['native_binary'] == str(binary.resolve())

    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(dict(document, program_source_digest_after='f' * 64), source,
                                             expected_source_inputs=inputs)
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(dict(document, program_source_inputs_after=[]), source,
                                             expected_source_inputs=inputs)
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(dict(document, program_source_inputs=[]), source,
                                             expected_source_inputs=inputs)
    foreign = [dict(entry, sha256='f' * 64) for entry in inputs]
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(dict(document, program_source_inputs=foreign), source,
                                             expected_source_inputs=inputs)


# ------------------------------------------- ancestor links and canary holes

def test_binding_entry_points_refuse_ancestor_links_with_canary(evidence_root, tmp_path):
    """A parent-directory link never redirects a verifier; nothing starts."""
    external = tmp_path / 'external-canary'
    external.mkdir()
    canary = external / 'canary.bin'
    canary.write_bytes(b'external-canary-bytes')
    old_source = ROOT / 'tools' / 'windows_native_harness.py'
    old_before = windows.sha256_file(old_source)
    canary_before = canary.read_bytes()

    base = acceptance.new_unique_root(acceptance.BUILD_BASE)
    real_root = base / 'real-root'
    real_root.mkdir()
    linked_base = base / 'linked-base'
    linked_base.symlink_to(external, target_is_directory=True)
    inward = base / 'inward'
    inward.symlink_to(real_root, target_is_directory=True)
    root_link = base / 'root-link'
    root_link.symlink_to(real_root, target_is_directory=True)

    # 1. The acceptance gate refuses every ancestor-link shape before it builds
    #    or reads an artifact.
    with pytest.raises(acceptance.GateError):
        acceptance.guard_build_root(linked_base / 'child')
    with pytest.raises(acceptance.GateError):
        acceptance.guard_build_root(root_link)
    with pytest.raises(acceptance.GateError):
        acceptance.guard_build_root(inward / 'child')

    source = windows.program_source_digest()
    inputs = windows.program_source_inputs()
    document = {'format': acceptance.ARTIFACT_BINDING_FORMAT, 'verdict': 'ok',
                'build_root': str(linked_base / 'child'),
                'program_source_digest': source, 'program_source_digest_after': source,
                'program_source_inputs': inputs, 'program_source_inputs_after': inputs,
                'artifacts': {'web_zip': {'path': str(canary), 'sha256': windows.sha256_file(canary),
                                          'size': canary.stat().st_size},
                              'native_zip': {'path': str(canary), 'sha256': windows.sha256_file(canary),
                                             'size': canary.stat().st_size},
                              'native_descriptor': {'path': str(canary), 'sha256': windows.sha256_file(canary),
                                                    'size': canary.stat().st_size},
                              'native_binary': {'path': str(canary), 'sha256': windows.sha256_file(canary),
                                                'size': canary.stat().st_size}}}
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(document, source, expected_source_inputs=inputs)

    # An artifact whose own leaf is real but whose ancestor is a link is refused.
    artifact = real_root / 'artifact.bin'
    artifact.write_bytes(b'inside-artifact')
    linked_artifact = dict(document, build_root=str(base),
                           artifacts={key: {'path': str(inward / 'artifact.bin'),
                                            'sha256': windows.sha256_file(artifact),
                                            'size': artifact.stat().st_size}
                                      for key in ('web_zip', 'native_zip', 'native_descriptor', 'native_binary')})
    with pytest.raises(acceptance.GateError):
        acceptance.validate_artifact_binding(linked_artifact, source, expected_source_inputs=inputs)

    # 2/3. Both verifier CLIs refuse the linked build root before creating an
    #      evidence root or starting an instance/program.
    binding = base / 'linked-binding.json'
    binding.write_text(json.dumps(document), encoding='utf-8')
    for tool, extra in ((ROOT / 'tools' / 'phase03_verify_shell.py',
                         ['--web-zip', str(canary), '--native-zip', str(canary)]),
                        (ROOT / 'tools' / 'phase03_verify_package.py', ['--native-zip', str(canary)])):
        root = evidence_root / f'{tool.stem}-ancestor-link'
        command = [sys.executable, str(tool), '--verify', '--root', str(root), '--binding', str(binding),
                   *extra, '--native-descriptor', str(canary), '--native-binary', str(canary)]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
        assert result.returncode == 2, (tool.name, result.stdout[-800:], result.stderr[-800:])
        assert 'REFUSED' in result.stdout
        assert 'symbolic link' in result.stdout
        assert not root.exists(), 'a refused verifier creates nothing'

    assert canary.read_bytes() == canary_before
    assert windows.sha256_file(old_source) == old_before


def test_kit_manifest_refuses_parent_path_links_with_canary(evidence_root, tmp_path):
    """The frozen kit manifest refuses a link at the root or any parent path."""
    external = tmp_path / 'external-kit-holder'
    external.mkdir()
    canary = external / 'canary.bin'
    canary.write_bytes(b'external-canary-bytes')
    canary_before = canary.read_bytes()
    source = windows.program_source_digest()
    inputs = windows.program_source_inputs()

    base = acceptance.new_unique_root(acceptance.BUILD_BASE)
    good = make_kit(base / 'good-kit')
    assert windows.verify_kit_strict(good, expected_source_digest=source,
                                     expected_source_inputs=inputs) == []

    # An internal parent path link (kit/delivery -> outside) is refused.
    linked = make_kit(base / 'linked-kit')
    shutil.move(str(linked / 'delivery'), str(external / 'delivery'))
    (linked / 'delivery').symlink_to(external / 'delivery', target_is_directory=True)
    problems = windows.verify_kit_strict(linked, expected_source_digest=source)
    assert any('symbolic link' in problem for problem in problems), problems

    # The kit root itself being a link is refused.
    root_link = base / 'root-link'
    root_link.symlink_to(linked, target_is_directory=True)
    problems = windows.verify_kit_strict(root_link, expected_source_digest=source)
    assert any('root is a symbolic link' in problem for problem in problems), problems

    # A parent component that redirects outside the project is refused before a
    # member is read; the redirected kit itself is untouched.
    outside_kit = make_kit(external / 'outside-kit')
    outside_readme = windows.sha256_file(outside_kit / 'README.md')
    outer = base / 'outer'
    outer.mkdir()
    (outer / 'linked-parent').symlink_to(external, target_is_directory=True)
    problems = windows.verify_kit_strict(outer / 'linked-parent' / 'outside-kit',
                                         expected_source_digest=source)
    assert any('root path contains a symbolic link' in problem for problem in problems), problems

    assert canary.read_bytes() == canary_before
    assert windows.sha256_file(external / 'outside-kit' / 'README.md') == outside_readme


# ------------------------------------------- unpacked runtime resources

def test_unpacked_runtime_members_must_match_the_bound_archive(evidence_root):
    """The .pck and dynamic libraries are checked member by member, not only the exe."""
    archive = evidence_root / 'runtime.zip'
    members = {'GEP Synthetic Experiment.app/Contents/MacOS/GEP Synthetic Experiment': b'exe-bytes',
               'GEP Synthetic Experiment.app/Contents/Resources/app.pck': b'pck-bytes',
               'GEP Synthetic Experiment.app/Contents/Frameworks/libgdsqlite.dylib': b'dylib-bytes'}
    with zipfile.ZipFile(archive, 'w') as bundle:
        for name, raw in members.items():
            bundle.writestr(name, raw)
    extracted = evidence_root / 'runtime'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    assert windows.verify_extracted_archive(archive, extracted, label='probe') == []

    pck = extracted / 'GEP Synthetic Experiment.app' / 'Contents' / 'Resources' / 'app.pck'
    original = pck.read_bytes()
    pck.write_bytes(b'changed-pck')
    problems = windows.verify_extracted_archive(archive, extracted, label='probe')
    assert any('bytes changed' in problem and 'app.pck' in problem for problem in problems), problems
    pck.write_bytes(original)

    dylib = extracted / 'GEP Synthetic Experiment.app' / 'Contents' / 'Frameworks' / 'libgdsqlite.dylib'
    dylib.unlink()
    problems = windows.verify_extracted_archive(archive, extracted, label='probe')
    assert any('missing' in problem and 'libgdsqlite.dylib' in problem for problem in problems), problems

    canary = evidence_root / 'external-dylib.bin'
    canary.write_bytes(b'dylib-bytes')
    dylib.symlink_to(canary)
    problems = windows.verify_extracted_archive(archive, extracted, label='probe')
    assert any('symbolic link' in problem for problem in problems), problems
    assert canary.read_bytes() == b'dylib-bytes'


def test_shell_refuses_before_start_when_unpacked_runtime_is_incomplete(evidence_root):
    """A bound shell run with a missing .pck fails before any server or program."""
    fresh = acceptance.new_unique_root(acceptance.BUILD_BASE)
    web = fresh / 'web' / 'synthetic_web.zip'
    web.parent.mkdir()
    web.write_bytes(b'web-bytes')
    native = fresh / 'native'
    native.mkdir()
    native_zip = native / 'synthetic.zip'
    with zipfile.ZipFile(native_zip, 'w') as bundle:
        bundle.writestr('GEP Synthetic Experiment.app/Contents/MacOS/GEP Synthetic Experiment', b'exe')
        bundle.writestr('GEP Synthetic Experiment.app/Contents/Resources/app.pck', b'pck')
    binary = native / 'GEP Synthetic Experiment.app' / 'Contents' / 'MacOS' / 'GEP Synthetic Experiment'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'exe')
    descriptor = native / 'descriptor.json'
    descriptor.write_text('{"platform": "macos_arm64"}', encoding='utf-8')

    def entry(path):
        return {'path': str(path), 'sha256': windows.sha256_file(path), 'size': path.stat().st_size}

    source = windows.program_source_digest()
    inputs = windows.program_source_inputs()
    binding = fresh / 'artifact_binding.json'
    binding.write_text(json.dumps(
        {'format': acceptance.ARTIFACT_BINDING_FORMAT, 'verdict': 'ok', 'build_root': str(fresh),
         'program_source_digest': source, 'program_source_digest_after': source,
         'program_source_inputs': inputs, 'program_source_inputs_after': inputs,
         'artifacts': {'web_zip': entry(web), 'native_zip': entry(native_zip),
                       'native_descriptor': entry(descriptor), 'native_binary': entry(binary)}}),
        encoding='utf-8')
    root = evidence_root / 'shell-incomplete-runtime'
    command = [sys.executable, str(ROOT / 'tools' / 'phase03_verify_shell.py'),
               '--verify', '--root', str(root), '--binding', str(binding),
               '--web-zip', str(web), '--native-zip', str(native_zip),
               '--native-descriptor', str(descriptor), '--native-binary', str(binary)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert result.returncode == 1, (result.stdout[-1200:], result.stderr[-400:])
    assert 'PHASE03_SHELL_VERIFY_FAILED' in result.stdout
    assert '解包 macOS 运行资源与绑定归档逐成员一致' in result.stdout
    assert 'app.pck' in result.stdout
    # No server, database or program was started for the refused run.
    assert not (root / 'data').exists()
    assert not (root / 'gunicorn.log').exists()
