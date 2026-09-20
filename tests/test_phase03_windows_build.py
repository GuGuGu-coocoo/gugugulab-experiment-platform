"""03D Windows build gates: real subprocess exit codes and pinned template provenance.

The Windows verifier must never accept a crashed Godot subprocess because its
output files exist. These tests inject subprocess failures -- an export that
writes plausible files and then exits non-zero, an engine-notice export that
writes valid JSON and then exits non-zero -- and prove the verifier fails. They
also prove a corrupt or mismatched export template is rejected before any copy
or export, and that the build tool and the verifier share the same pinned check.
The real cross-build is exercised by
``tools/phase03_verify_windows_package.py --verify``; the fault injection here
covers only the error branches.
"""
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import windows_template  # noqa: E402  (import after the tools path is set)
import phase03_verify_windows_package as verifier  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completed(command, exit_code, stdout='', stderr=''):
    return subprocess.CompletedProcess(command, exit_code, stdout, stderr)


def verify_instance(tmp_path):
    instance = verifier.Verify(tmp_path / 'root')
    instance.godot = '/fake/godot'
    instance.root.mkdir(parents=True, exist_ok=True)
    return instance


# ------------------------------------------------------------- template pin

def test_pinned_official_windows_template_is_the_recorded_digest():
    assert windows_template.OFFICIAL_WINDOWS_TEMPLATE.name == 'windows_release_x86_64.exe'
    assert windows_template.OFFICIAL_WINDOWS_TEMPLATE.size == 109268480
    assert windows_template.OFFICIAL_WINDOWS_TEMPLATE.sha256 == \
        'd34d36f3be1a6c49c56525ae86469b92e4f417ddf0b43cf00dd80c385c4b0562'


def test_corrupt_template_of_the_pinned_size_is_rejected(tmp_path):
    payload = b'x' * 4096
    pin = windows_template.TemplatePin('windows_release_x86_64.exe', '0' * 64, len(payload))
    path = tmp_path / pin.name
    path.write_bytes(payload)
    with pytest.raises(windows_template.TemplateError, match='does not match the pinned official digest'):
        windows_template.verify_windows_template(path, pin)


def test_template_with_the_wrong_size_is_rejected_before_hashing(tmp_path):
    path = tmp_path / 'windows_release_x86_64.exe'
    path.write_bytes(b'not the official template')
    with pytest.raises(windows_template.TemplateError, match='pinned official template is'):
        windows_template.verify_windows_template(path)


def test_mismatched_override_template_is_rejected_not_skipped(tmp_path, monkeypatch):
    override = tmp_path / 'override'
    override.mkdir()
    (override / 'windows_release_x86_64.exe').write_bytes(b'wrong bytes')
    monkeypatch.setenv('GEP_GODOT_TEMPLATES', str(override))
    with pytest.raises(windows_template.TemplateError, match='pinned official template is'):
        windows_template.find_windows_template()


def test_override_without_the_template_falls_back_to_a_verified_directory(tmp_path, monkeypatch):
    payload = b'synthetic official bytes'
    pin = windows_template.TemplatePin('windows_release_x86_64.exe', hashlib.sha256(payload).hexdigest(),
                                       len(payload))
    standard = tmp_path / 'standard'
    standard.mkdir()
    (standard / pin.name).write_bytes(payload)
    monkeypatch.setattr(windows_template, 'template_directories',
                        lambda env=None: [tmp_path / 'missing-override', standard])
    assert windows_template.find_windows_template(pin) == standard / pin.name


def test_build_tool_and_verifier_share_the_pinned_template_check():
    build = load_module('gep_build_tool_shared_check', TOOLS / 'build.py')
    assert build.find_windows_template is windows_template.find_windows_template
    assert build.verify_windows_template is windows_template.verify_windows_template
    assert verifier.find_windows_template is windows_template.find_windows_template
    assert verifier.verify_windows_template is windows_template.verify_windows_template


def test_build_refuses_mismatched_override_before_copy_and_export(tmp_path, monkeypatch):
    build = load_module('gep_build_tool_refusal', TOOLS / 'build.py')
    project = tmp_path / 'project'
    (project / '.godot').mkdir(parents=True)
    (tmp_path / 'packages' / 'gec_web').mkdir(parents=True)
    for name in ('sdk.js', 'bridge.js', 'inputs.js', 'shell.js'):
        (tmp_path / 'packages' / 'gec_web' / name).write_text('// synthetic client file')
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    monkeypatch.setattr(build, 'PROJECT', project)
    monkeypatch.setattr(build, 'TEMPLATES', project / '.godot' / 'templates')
    monkeypatch.setattr(build, 'TARGETS', {'windows': ('Windows', tmp_path / 'out.exe')})
    override = tmp_path / 'override'
    override.mkdir()
    (override / 'windows_release_x86_64.exe').write_bytes(b'wrong bytes')
    monkeypatch.setenv('GEP_GODOT_TEMPLATES', str(override))
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: '4.7.2.stable.official.test\n')
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda command, *args, **kwargs: calls.append(command)
                        or completed(command, 0))
    monkeypatch.setattr(sys, 'argv', ['build.py', 'windows'])
    with pytest.raises(SystemExit, match='pinned official template is'):
        build.main()
    assert not (build.TEMPLATES / 'windows_release_x86_64.exe').exists()
    assert all('--export-release' not in call for call in calls)


# -------------------------------------------------------- subprocess gates

def test_notice_export_with_valid_json_but_nonzero_exit_is_rejected(tmp_path, monkeypatch):
    frozen = tmp_path / 'frozen.json'
    frozen.write_text('{"engine_version": {"string": "4.7.2-stable (official)"}}')
    monkeypatch.setattr(verifier, 'ENGINE_NOTICES', frozen)
    instance = verify_instance(tmp_path)

    def fake_run(command, **kwargs):
        Path(kwargs['env']['GEP_NOTICE_OUTPUT']).write_text(frozen.read_text())
        return completed(command, 3, 'aborted', 'crash backtrace')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    with pytest.raises(verifier.VerificationError, match='子进程成功退出'):
        instance.verify_notice_provenance()
    assert (instance.root / 'engine_notices_export.json').is_file()


def test_notice_export_with_matching_json_and_zero_exit_passes(tmp_path, monkeypatch):
    frozen = tmp_path / 'frozen.json'
    frozen.write_text('{"engine_version": {"string": "4.7.2-stable (official)"}}')
    monkeypatch.setattr(verifier, 'ENGINE_NOTICES', frozen)
    instance = verify_instance(tmp_path)

    def fake_run(command, **kwargs):
        Path(kwargs['env']['GEP_NOTICE_OUTPUT']).write_text(frozen.read_text())
        return completed(command, 0, 'Godot Engine v4.7.2', '')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    instance.verify_notice_provenance()
    assert instance.godot_runs[-1]['exit'] == 0


def test_cold_cache_import_must_exit_zero_after_one_retry(tmp_path, monkeypatch):
    instance = verify_instance(tmp_path)
    exits = iter([-6, 0])
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: completed(command, next(exits)))
    instance.warm_import_cache()
    assert [run['exit'] for run in instance.godot_runs] == [-6, 0]


def test_two_failed_imports_stop_the_build(tmp_path, monkeypatch):
    instance = verify_instance(tmp_path)
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: completed(command, -6))
    with pytest.raises(verifier.VerificationError, match='导入重试'):
        instance.warm_import_cache()
    assert len(instance.godot_runs) == 2


def test_export_retry_is_limited_to_one_and_its_result_is_not_assumed(tmp_path, monkeypatch):
    instance = verify_instance(tmp_path)
    export_exits = iter([-6, 1])
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if '--export-release' in command:
            return completed(command, next(export_exits), 'crash', '')
        return completed(command, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    result = instance.export_release(tmp_path / 'out.exe')
    assert result.returncode == 1
    assert sum('--export-release' in call for call in calls) == 2
    assert sum('--import' in call for call in calls) == 1


def test_cross_build_rejects_nonzero_export_even_when_outputs_exist(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    program_root = tmp_path / 'program'
    monkeypatch.setattr(verifier, 'PROJECT', project)
    monkeypatch.setattr(verifier, 'PROGRAM_ROOT', program_root)
    monkeypatch.setattr(verifier, 'PROGRAM_ARCHIVE', tmp_path / 'archive.zip')
    monkeypatch.setattr(verifier, 'DESCRIPTOR', tmp_path / 'descriptor.json')
    monkeypatch.setattr(verifier, 'TEMPLATES', project / '.godot' / 'templates')
    template = tmp_path / 'windows_release_x86_64.exe'
    template.write_bytes(b'official template placeholder')
    monkeypatch.setattr(verifier, 'verify_windows_template', lambda path: 'f' * 64)
    monkeypatch.setattr(subprocess, 'check_output', lambda *args, **kwargs: '4.7.2.stable.official.test\n')
    export_calls = []

    def fake_run(command, **kwargs):
        if '--export-release' in command:
            export_calls.append(command)
            program_root.mkdir(parents=True, exist_ok=True)
            (program_root / verifier.ENTRY).write_bytes(b'MZ plausible output')
            return completed(command, 134, 'crash', 'aborted after writing files')
        return completed(command, 0)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    instance = verify_instance(tmp_path)
    instance.template = template
    with pytest.raises(verifier.VerificationError, match='导出子进程成功退出'):
        instance.cross_build()
    assert (program_root / verifier.ENTRY).is_file()
    assert len(export_calls) == 2
    assert not (tmp_path / 'archive.zip').exists()


def test_cross_build_rejects_a_mismatched_template_before_export(tmp_path, monkeypatch):
    instance = verify_instance(tmp_path)
    template = tmp_path / 'windows_release_x86_64.exe'
    template.write_bytes(b'wrong bytes')
    instance.template = template
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: calls.append(command)
                        or completed(command, 0))
    with pytest.raises(verifier.VerificationError, match='pinned official template is'):
        instance.cross_build()
    assert calls == []
