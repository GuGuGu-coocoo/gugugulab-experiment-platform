"""Transport boundaries for the optional demo delivery tool.

Small synthetic archives exercise copying, source isolation, pinned hashes and
no-overwrite guards. These tests never export Godot or download dependencies;
real game/export acceptance is recorded separately.
"""
import hashlib
import errno
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('moss_duel_delivery', REPO / 'tools/moss_duel_delivery.py')
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    sources = {
        'examples/moss_duel/project.godot': '[application]\nrun/main_scene="res://main.tscn"\n',
        'examples/moss_duel/main.tscn': '[gd_scene format=3]\n',
        'examples/moss_duel/scripts/main.gd': 'extends Node2D\nsignal game_event(payload)\n',
        'examples/moss_duel/export_presets.cfg': '[preset.0]\nplatform="Web"\n',
        'examples/moss_duel/README.md': '[Guide](../../docs/moss_duel_gec.md)\n',
        'examples/moss_duel/THIRD_PARTY_NOTICES.md': 'Kenney assets: CC0.\n',
        'examples/moss_duel/assets/licenses/kenney_rpg_audio_license.txt': 'Creative Commons Zero.\n',
        'examples/moss_duel/assets/licenses/kenney_impact_sounds_license.txt': 'Creative Commons Zero.\n',
        'tests/fixtures/moss_duel_legacy_adapter/gec_entry.gd': 'extends Node\n',
        'tests/fixtures/moss_duel_legacy_adapter/gec_entry.tscn': '[gd_scene format=3]\n',
        'tests/fixtures/moss_duel_legacy_adapter/gec_descriptor.json': '{"version":"moss-duel-1"}\n',
        'tests/fixtures/moss_duel_legacy_adapter/export_presets.cfg': '[preset.0]\nplatform="Web"\n',
        'tests/fixtures/moss_duel_legacy_adapter/package_web.gd': '@tool\nextends EditorScript\n',
        'tests/fixtures/moss_duel_legacy_adapter/package_native.gd': '@tool\nextends EditorScript\n',
        'tests/fixtures/synthetic_experiment/data/experiment_data.gd': 'extends Node\n',
        'tests/fixtures/synthetic_experiment/addons/gec/backend.gd': 'extends Node\n',
        'tests/fixtures/synthetic_experiment/addons/godot-sqlite/gdsqlite.gdextension': '[configuration]\n',
        'docs/moss_duel_gec.md': 'Optional manual integration guide.\n',
        'LICENSE': (REPO / 'LICENSE').read_text(),
        'third_party/godot_sqlite_license.md': (REPO / 'third_party/godot_sqlite_license.md').read_text(),
    }
    sources.update({f'packages/gec_web/{name}.js': '// optional adapter\n'
                    for name in ('sdk', 'bridge', 'inputs', 'shell')})
    for relative, content in sources.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    archive = tmp_path / 'synthetic_sqlite.zip'
    with zipfile.ZipFile(archive, 'w') as writer:
        for platform in ('macos', 'windows'):
            writer.writestr(f'demo/addons/godot-sqlite/bin/libgdsqlite.{platform}.test', b'synthetic library')
        writer.writestr('demo/do_not_distribute.txt', b'unrelated upstream member')
    monkeypatch.setattr(delivery, 'ROOT', root)
    monkeypatch.setattr(delivery, 'SQLITE_SHA', hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(delivery, 'engine_notice', lambda: 'Synthetic engine notice for transport test.\n')
    output = root / 'build' / 'delivery'
    monkeypatch.setattr(sys, 'argv', ['moss_duel_delivery.py', '--output', str(output),
                                    '--sqlite-archive', str(archive)])
    return root, archive, output


def test_prepared_game_and_kit_are_separate_and_licenses_preserved(prepared):
    root, _, output = prepared
    delivery.main()
    with zipfile.ZipFile(output / 'moss_duel_standalone.zip') as game:
        members = game.namelist()
        assert not any(name.startswith(('addons/', 'data/', 'gec_web/')) or name.startswith('gec_')
                       for name in members)
        code = game.read('scripts/main.gd') + game.read('project.godot')
        assert not any(word in code.lower() for word in (b'gec', b'http', b'server', b'credential'))
        assert game.read('LICENSE') == (root / 'LICENSE').read_bytes()
        assert game.read('README.md') == b'[Guide](GEC_GUIDE.md)\n'
        assert game.read('assets/licenses/kenney_rpg_audio_license.txt') == b'Creative Commons Zero.\n'
    with zipfile.ZipFile(output / 'moss_duel_gec_kit.zip') as kit:
        members = kit.namelist()
        assert 'project.godot' not in members and 'scripts/main.gd' not in members
        assert 'gec_entry.gd' in members and 'gec_descriptor.json' in members
        assert any(name.startswith('addons/godot-sqlite/bin/libgdsqlite.windows.') for name in members)
        assert any(name.startswith('addons/godot-sqlite/bin/libgdsqlite.macos.') for name in members)
        assert not any(delivery.secret_member(Path(name)) for name in members)
        assert 'do_not_distribute.txt' not in members
        assert kit.read('SQLITE_LICENSE.md') == (root / 'third_party/godot_sqlite_license.md').read_bytes()
    manifest = json.loads((output / 'delivery_manifest.json').read_text())
    assert set(manifest) == {'moss_duel_standalone.zip', 'moss_duel_gec_kit.zip'}
    for relative, details in manifest.items():
        assert details == {'size': (output / relative).stat().st_size,
                           'sha256': hashlib.sha256((output / relative).read_bytes()).hexdigest()}


@pytest.mark.parametrize('source,member', [
    ('examples/moss_duel', 'connection.json'),
    ('examples/moss_duel', 'addons/gec/backend.gd'),
    ('tests/fixtures/moss_duel_legacy_adapter', '.env'),
    ('tests/fixtures/moss_duel_legacy_adapter', 'private.sqlite3'),
])
def test_contaminated_sources_cannot_produce_distributable_archive(prepared, source, member):
    root, _, output = prepared
    contaminant = root / source / member
    contaminant.parent.mkdir(parents=True, exist_ok=True)
    contaminant.write_text('synthetic sentinel: never distribute')
    with pytest.raises(ValueError, match='Unexpected source member'):
        delivery.main()
    assert not list(output.glob('*.zip'))
    assert contaminant.read_text() == 'synthetic sentinel: never distribute'


def test_wrong_dependency_hash_is_rejected_before_creating_output(prepared):
    _, archive, output = prepared
    archive.write_bytes(archive.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='digest mismatch'):
        delivery.main()
    assert not output.exists()


def test_existing_output_is_never_overwritten(prepared):
    _, _, output = prepared
    output.mkdir(parents=True)
    sentinel = output / 'keep.txt'
    sentinel.write_bytes(b'existing work')
    with pytest.raises(ValueError, match='new directory'):
        delivery.main()
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b'existing work'


def test_output_parent_traversal_cannot_escape_build(prepared, monkeypatch):
    root, archive, _ = prepared
    (root / 'build').mkdir()
    monkeypatch.setattr(sys, 'argv', ['moss_duel_delivery.py', '--output', str(root / 'build/../outside'),
                                    '--sqlite-archive', str(archive)])
    with pytest.raises(ValueError, match='new directory'):
        delivery.main()
    assert not (root / 'outside').exists()


def test_dangling_output_link_is_refused(prepared):
    root, _, output = prepared
    output.parent.mkdir(parents=True)
    missing = root / 'must_not_create'
    output.symlink_to(missing, target_is_directory=True)
    with pytest.raises(ValueError, match='links refused'):
        delivery.main()
    assert output.is_symlink() and not missing.exists()


def test_source_directory_link_is_refused_without_reading_target(tmp_path):
    source, destination, outside = (tmp_path / name for name in ('source', 'destination', 'outside'))
    source.mkdir()
    outside.mkdir()
    (outside / 'unrelated.txt').write_text('not delivery source')
    (source / 'linked').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='Source link refused'):
        delivery.copy_sources(source, destination)
    assert not destination.exists()


def test_zip_does_not_overwrite_existing_target(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'file.txt').write_text('content')
    target = tmp_path / 'existing.zip'
    target.write_bytes(b'existing work')
    with pytest.raises(FileExistsError):
        delivery.zip_tree(source, target)
    assert target.read_bytes() == b'existing work'


def test_zip_refuses_link_instead_of_archiving_outside_bytes(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(b'not delivery source')
    (source / 'linked.txt').symlink_to(outside)
    target = tmp_path / 'delivery.zip'
    with pytest.raises(ValueError, match='Unsafe archive member refused'):
        delivery.zip_tree(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob('*.tmp'))
    assert outside.read_bytes() == b'not delivery source'


def test_zip_write_failure_removes_partial_temporary_without_final(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'file.txt').write_bytes(b'content that cannot be completely written')
    target = tmp_path / 'delivery.zip'
    unrelated = tmp_path / 'unrelated.tmp'
    unrelated.write_bytes(b'keep this')
    original_open = zipfile.ZipFile.open
    wrote = []

    def faulty_open(archive, name, mode='r', pwd=None, *, force_zip64=False):
        member = original_open(archive, name, mode, pwd, force_zip64=force_zip64)
        if mode != 'w':
            return member

        class PartialWriter:
            def __enter__(self):
                member.__enter__()
                return self

            def __exit__(self, *error):
                return member.__exit__(*error)

            def write(self, value):
                member.write(value[:3])
                archive.fp.flush()
                wrote.append(archive.fp.tell() > 0)
                raise OSError(errno.ENOSPC, 'injected write failure after actual partial ZIP I/O')

        return PartialWriter()

    monkeypatch.setattr(zipfile.ZipFile, 'open', faulty_open)
    with pytest.raises(OSError) as rejected:
        delivery.zip_tree(source, target)
    assert rejected.value.errno == errno.ENOSPC and wrote == [True]
    assert not target.exists()
    assert set(tmp_path.iterdir()) == {source, unrelated}
    assert unrelated.read_bytes() == b'keep this'


def test_zip_crc_failure_is_rejected_before_publication(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'file.txt').write_bytes(b'content')
    target = tmp_path / 'delivery.zip'
    original_close = zipfile.ZipFile.close

    def corrupt_closed_archive(archive):
        stream = archive.fp if archive.mode == 'w' else None
        original_close(archive)
        if stream is not None:
            stream.seek(0)
            contents = stream.read()
            crc_offset = contents.index(b'PK\x01\x02') + 16
            stream.seek(crc_offset)
            stream.write(bytes([contents[crc_offset] ^ 1]))
            stream.flush()

    monkeypatch.setattr(zipfile.ZipFile, 'close', corrupt_closed_archive)
    with pytest.raises(ValueError, match='failed CRC'):
        delivery.zip_tree(source, target)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == [source]


def test_zip_late_publication_collision_preserves_external_target(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'file.txt').write_bytes(b'content')
    target = tmp_path / 'delivery.zip'
    original_link = delivery.os.link

    def collide(temporary, final):
        with zipfile.ZipFile(temporary) as archive:
            assert archive.testzip() is None
        final.write_bytes(b'created while packaging')
        original_link(temporary, final)

    monkeypatch.setattr(delivery.os, 'link', collide)
    with pytest.raises(FileExistsError):
        delivery.zip_tree(source, target)
    assert target.read_bytes() == b'created while packaging'
    assert set(tmp_path.iterdir()) == {source, target}


def test_failed_second_archive_never_writes_manifest_or_partial_final(prepared, monkeypatch):
    _, _, output = prepared
    original_write = zipfile.ZipFile.write

    def fail_kit_member(archive, source, member=None, *args, **kwargs):
        original_write(archive, source, member, *args, **kwargs)
        if member == 'gec_entry.gd':
            raise OSError(errno.ENOSPC, 'injected kit write failure')

    monkeypatch.setattr(zipfile.ZipFile, 'write', fail_kit_member)
    with pytest.raises(OSError):
        delivery.main()
    assert not (output / 'delivery_manifest.json').exists()
    assert not (output / 'moss_duel_gec_kit.zip').exists()
    with zipfile.ZipFile(output / 'moss_duel_standalone.zip') as completed:
        assert completed.testzip() is None
    assert not list(output.glob('.*.tmp'))
