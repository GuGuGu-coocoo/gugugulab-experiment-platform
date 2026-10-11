"""Generic dependency download boundaries; no game exports or network calls."""
import errno
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import zipfile

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_gec_addon', REPO / 'tools/build_gec_addon.py')
addon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(addon)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    sources = {
        'examples/synthetic_experiment/data/experiment_data.gd': 'extends RefCounted\n',
        'examples/synthetic_experiment/data/local_backend.gd': 'extends Node\n',
        'packages/gec_godot/session.gd': 'extends Node\n# generic fixture\n',
        'packages/gec_godot/package_web.gd': '@tool\nextends EditorScript\n',
        'packages/gec_godot/package_native.gd': '@tool\nextends EditorScript\n',
        'packages/gec_godot/gec_descriptor.example.json': '{"version":"example-1"}\n',
        'examples/synthetic_experiment/addons/godot-sqlite/gdsqlite.gdextension': '[configuration]\n',
        'LICENSE': (REPO / 'LICENSE').read_text(),
        'third_party/godot_sqlite_license.md': (REPO / 'third_party/godot_sqlite_license.md').read_text(),
        'docs/gec_godot.md': 'Generic guide [Host example](moss_duel_gec.md). [Protocol](protocol.md)\n',
    }
    sources.update({f'examples/synthetic_experiment/addons/gec/{name}.gd': 'extends Node\n'
                    for name in ('native_backend', 'web_backend', 'shell')})
    sources.update({f'packages/gec_web/{name}.js': '// generic dependency\n'
                    for name in ('sdk', 'bridge', 'inputs', 'shell')})
    for relative, content in sources.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    notices = root / 'server/core/data/godot_engine_notices.json'
    notices.parent.mkdir(parents=True)
    notices.write_text(json.dumps({
        'engine_version': {'major': 4, 'minor': 7, 'patch': 2},
        'engine_license': 'Synthetic Godot notice used only by this transport test.',
        'third_party_licenses': {'test': 'Synthetic dependency notice.'},
        'third_party_copyright': [{'name': 'synthetic'}],
    }))
    archive = tmp_path / 'synthetic_sqlite.zip'
    with zipfile.ZipFile(archive, 'w') as writer:
        for member in addon.SQLITE_MEMBERS:
            writer.writestr('demo/' + member, b'synthetic library for ' + member.encode())
        writer.writestr('demo/main.tscn', b'unrelated upstream scene')
        writer.writestr('demo/connection.json', b'not a distributable dependency')
    monkeypatch.setattr(addon, 'ROOT', root)
    monkeypatch.setattr(addon, 'SQLITE_SHA', hashlib.sha256(archive.read_bytes()).hexdigest())
    output = root / 'build' / 'download'
    monkeypatch.setattr(sys, 'argv', ['build_gec_addon.py', '--output', str(output),
                                    '--sqlite-archive', str(archive)])
    return root, archive, output


def test_only_generic_allowlisted_dependencies_and_notices_are_shipped(prepared):
    root, _, output = prepared
    # Secrets and project files next to sources must never be collected.
    for name in ('connection.json', '.env', 'main.tscn', 'project.godot', 'export_presets.cfg'):
        (root / 'packages/gec_godot' / name).write_text('private-or-host sentinel')
    addon.main()
    target = output / addon.ARCHIVE_NAME
    with zipfile.ZipFile(target) as reader:
        members = set(reader.namelist())
        assert members == set(addon.source_members()) | set(addon.SQLITE_MEMBERS) | {'content_manifest.json'}
        assert not members.intersection({'connection.json', '.env', 'main.tscn', 'project.godot',
                                         'export_presets.cfg', 'gec_descriptor.json', 'LICENSE',
                                         'GODOT_NOTICES.txt', 'THIRD_PARTY_NOTICES.txt'})
        assert {'GEC_GODOT_NOTICES.txt', 'GEC_THIRD_PARTY_NOTICES.txt'} <= members
        assert not any(name.startswith(('examples/', 'integrations/')) for name in members)
        assert reader.read('GEC_LICENSE.txt') == (root / 'LICENSE').read_bytes()
        assert reader.read('SQLITE_LICENSE.md') == (root / 'third_party/godot_sqlite_license.md').read_bytes()
        assert 'https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/main/docs/protocol.md' in reader.read('README.md').decode()
        assert 'SQLite is in the public domain' in reader.read('SQLITE_NOTICE.txt').decode()
        manifest = json.loads(reader.read('content_manifest.json'))
        assert manifest['component'] == 'gec_godot'
        assert manifest['sdk_version'] == '0.1.0'
        assert set(manifest['files']) == members - {'content_manifest.json'}
        for name, detail in manifest['files'].items():
            content = reader.read(name)
            assert detail == {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
    result = json.loads((output / 'delivery_manifest.json').read_text())
    assert result == {'archive': str(target), 'size': target.stat().st_size,
                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'members': len(members)}


@pytest.mark.parametrize('kind', ['missing', 'empty', 'link'])
def test_missing_empty_or_linked_session_never_creates_output(prepared, kind):
    root, _, output = prepared
    source = root / 'packages/gec_godot/session.gd'
    source.unlink()
    if kind == 'empty':
        source.touch()
    elif kind == 'link':
        source.symlink_to(root / 'packages/gec_godot/package_web.gd')
    with pytest.raises((FileNotFoundError, ValueError)):
        addon.main()
    assert not output.exists()


def test_unpinned_archive_is_rejected_before_output(prepared):
    _, archive, output = prepared
    archive.write_bytes(archive.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='digest mismatch'):
        addon.main()
    assert not output.exists()


@pytest.mark.parametrize('kind', ['missing', 'link'])
def test_missing_or_linked_native_dependency_is_rejected(prepared, monkeypatch, kind):
    _, archive, output = prepared
    with zipfile.ZipFile(archive, 'w') as writer:
        for member in addon.SQLITE_MEMBERS:
            if member == addon.SQLITE_MEMBERS[0]:
                if kind == 'missing':
                    continue
                info = zipfile.ZipInfo('demo/' + member)
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                writer.writestr(info, b'other.dll')
            else:
                writer.writestr('demo/' + member, b'synthetic library')
    monkeypatch.setattr(addon, 'SQLITE_SHA', hashlib.sha256(archive.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='dependency'):
        addon.main()
    assert not output.exists()


def test_existing_output_is_preserved(prepared):
    _, _, output = prepared
    output.mkdir(parents=True)
    keep = output / 'keep.txt'
    keep.write_bytes(b'existing user work')
    with pytest.raises(ValueError, match='NEW directory'):
        addon.main()
    assert list(output.iterdir()) == [keep]
    assert keep.read_bytes() == b'existing user work'


@pytest.mark.parametrize('kind', ['parent_escape', 'parent_link', 'dangling_output'])
def test_output_paths_cannot_escape_or_follow_links(prepared, monkeypatch, tmp_path, kind):
    root, archive, _ = prepared
    (root / 'build').mkdir()
    if kind == 'parent_escape':
        output = root / 'build/../outside'
    elif kind == 'parent_link':
        (root / 'build' / 'alias').symlink_to(tmp_path, target_is_directory=True)
        output = root / 'build/alias/download'
    else:
        output = root / 'build' / 'dangling'
        output.symlink_to(tmp_path / 'missing')
    monkeypatch.setattr(sys, 'argv', ['build_gec_addon.py', '--output', str(output),
                                    '--sqlite-archive', str(archive)])
    with pytest.raises(ValueError, match='directory|links'):
        addon.main()
    assert not (root / 'outside').exists()
    assert not (tmp_path / 'download').exists()
    assert not (tmp_path / 'missing').exists()


def test_source_parent_link_is_refused(prepared):
    root, _, output = prepared
    directory = root / 'packages/gec_web'
    directory.rename(root / 'packages/actual_web')
    directory.symlink_to(root / 'packages/actual_web', target_is_directory=True)
    with pytest.raises(ValueError, match='path links refused'):
        addon.main()
    assert not output.exists()


def test_partial_write_failure_never_publishes_download_or_manifest(prepared, monkeypatch):
    _, _, output = prepared
    original = zipfile.ZipFile.writestr
    def fail_after_bytes(archive, *args, **kwargs):
        original(archive, *args, **kwargs)
        archive.fp.flush()
        assert archive.fp.tell() > 0  # Fault after real ZIP bytes were written.
        raise OSError(errno.ENOSPC, 'synthetic disk full')
    monkeypatch.setattr(zipfile.ZipFile, 'writestr', fail_after_bytes)
    with pytest.raises(OSError, match='disk full'):
        addon.main()
    assert list(output.iterdir()) == []


def test_late_publish_collision_preserves_other_writer(prepared, monkeypatch):
    _, _, output = prepared
    target = output / addon.ARCHIVE_NAME
    original = addon.os.link
    def concurrent_result(source, destination):
        target.write_bytes(b'other writer completed first')
        return original(source, destination)
    monkeypatch.setattr(addon.os, 'link', concurrent_result)
    with pytest.raises(FileExistsError):
        addon.main()
    assert target.read_bytes() == b'other writer completed first'
    assert list(output.iterdir()) == [target]


def test_download_is_deterministic_for_identical_sources(prepared, monkeypatch):
    root, archive, output = prepared
    addon.main()
    second = root / 'build' / 'second'
    monkeypatch.setattr(sys, 'argv', ['build_gec_addon.py', '--output', str(second),
                                    '--sqlite-archive', str(archive)])
    addon.main()
    assert (output / addon.ARCHIVE_NAME).read_bytes() == (second / addon.ARCHIVE_NAME).read_bytes()


def test_wrong_engine_notice_cannot_mislabel_dependency(prepared):
    root, _, output = prepared
    path = root / 'server/core/data/godot_engine_notices.json'
    notices = json.loads(path.read_text())
    notices['engine_version']['patch'] = 1
    path.write_text(json.dumps(notices))
    with pytest.raises(ValueError, match='do not match'):
        addon.main()
    assert not output.exists()


@pytest.mark.parametrize('platform,checks', [('web', 22), ('native', 36)])
def test_generic_editor_helper_real_godot_boundaries(tmp_path, platform, checks):
    godot = shutil.which('godot')
    if not godot:
        pytest.skip('Godot 4.7.2 is required for real editor helper checks')
    project = tmp_path / 'ordinary_project'
    project.mkdir()
    (project / 'project.godot').write_text(
        'config_version=5\n[application]\nconfig/name="Generic packaging harness"\n'
        '[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
    result = subprocess.run([
        godot, '--headless', '--log-file', str(tmp_path / 'packaging.log'),
        '--path', str(project), '--script', str(REPO / f'tests/godot/test_gec_{platform}_package.gd'),
        '--', str(REPO / f'packages/gec_godot/package_{platform}.gd'), str(tmp_path / 'new_fixtures'),
    ], capture_output=True, text=True, timeout=35)
    transcript = result.stdout + result.stderr
    assert result.returncode == 0, transcript
    assert 'SCRIPT ERROR' not in transcript, transcript
    assert f'GEC_{platform.upper()}_PACKAGE_BOUNDARY: {checks} checks, 0 failures' in transcript, transcript
