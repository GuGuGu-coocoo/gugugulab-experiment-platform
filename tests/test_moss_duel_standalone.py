"""The existing game is distributed without a preinstalled integration."""
import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
spec = importlib.util.spec_from_file_location('moss_duel_standalone', ROOT / 'tools/moss_duel_standalone.py')
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def test_independent_source_is_unchanged_and_has_no_glue(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'examples').mkdir()
    import shutil
    shutil.copytree(ROOT / 'examples/moss_duel', root / 'examples/moss_duel',
                    ignore=shutil.ignore_patterns('.godot', 'exports'))
    shutil.copy2(ROOT / 'LICENSE', root / 'LICENSE')
    monkeypatch.setattr(delivery, 'ROOT', root)
    monkeypatch.setattr(delivery, 'engine_notice', lambda: 'Test engine notice\n')
    output = root / 'build' / 'clean_game'
    manifest = delivery.prepare(output)
    with zipfile.ZipFile(output / 'moss_duel_standalone.zip') as archive:
        members = archive.namelist()
        assert not any(name.startswith(('addons/', 'gec_', 'data/', 'package_')) for name in members)
        assert 'GEC_GUIDE.md' not in members and 'connection.json' not in members
        for name in members:
            if name.startswith('scripts/') or name in {'project.godot', 'main.tscn', 'export_presets.cfg'}:
                assert archive.read(name) == (root / 'examples/moss_duel' / name).read_bytes()
        assert archive.read('LICENSE') == (ROOT / 'LICENSE').read_bytes()
        assert archive.testzip() is None
    assert set(manifest) == {'moss_duel_standalone.zip'}
    before = (output / 'moss_duel_standalone.zip').read_bytes()
    with pytest.raises(ValueError, match='new directory'):
        delivery.prepare(output)
    assert (output / 'moss_duel_standalone.zip').read_bytes() == before
