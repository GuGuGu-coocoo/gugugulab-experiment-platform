"""Reader examples remain separate; shared game and vendored SDK do not drift."""
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_gec_addon
import moss_duel_examples


def test_only_two_reader_projects_and_clean_game_has_no_gec():
    assert {path.name for path in (ROOT / 'examples').iterdir() if path.is_dir()} == {'moss_duel', 'moss_duel_gec'}
    clean = ROOT / 'examples/moss_duel'
    assert not any((clean / name).exists() for name in ('addons', 'data', 'gec_web', 'gec_entry.gd', 'connection.json'))
    for script in (clean / 'scripts').glob('*.gd'):
        assert not re.search(r'\bGEC\b|https?://|connection\.json', script.read_text(), re.I)
    assert 'res://main.tscn' in (clean / 'project.godot').read_text()


def test_shared_game_is_identical_and_both_guides_match_wiring():
    clean, integrated = (ROOT / 'examples' / name for name in ('moss_duel', 'moss_duel_gec'))
    for folder in ('scripts', 'assets', 'tests'):
        for source in (clean / folder).rglob('*'):
            if source.is_file():
                assert source.read_bytes() == (integrated / source.relative_to(clean)).read_bytes()
    assert (clean / 'main.tscn').read_bytes() == (integrated / 'main.tscn').read_bytes()
    assert 'res://gec_entry.tscn' in (integrated / 'project.godot').read_text()
    for language in ('', '_en'):
        guide = (ROOT / f'docs/moss_duel_gec{language}.md').read_text()
        script = re.search(r'```gdscript\n(.*?)\n```', guide, re.S).group(1)
        descriptor = re.search(r'```json\n(.*?)\n```', guide, re.S).group(1)
        assert script + '\n' == (integrated / 'gec_entry.gd').read_text()
        assert json.loads(descriptor) == json.loads((integrated / 'gec_descriptor.json').read_text())


def test_sdk_sources_match_connected_example_and_regression_host():
    dependencies = build_gec_addon.source_members()
    for name, content in dependencies.items():
        if name not in ('README.md', 'gec_descriptor.example.json'):
            assert (ROOT / 'examples/moss_duel_gec' / name).read_bytes() == content, name
    for source in (ROOT / 'GEC_SDK/gec_godot/runtime').rglob('*'):
        if source.is_file():
            fixture = ROOT / 'tests/fixtures/synthetic_experiment' / source.relative_to(ROOT / 'GEC_SDK/gec_godot/runtime')
            assert fixture.read_bytes() == source.read_bytes(), source


def test_two_complete_archives_and_existing_output_preserved(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    shutil.copytree(ROOT / 'examples/moss_duel', root / 'examples/moss_duel',
                    ignore=shutil.ignore_patterns('.godot', 'exports'))
    integrated = root / 'examples/moss_duel_gec'
    integrated.mkdir()
    for name in moss_duel_examples.INTEGRATION_FILES:
        shutil.copy2(ROOT / 'examples/moss_duel_gec' / name, integrated / name)
    shutil.copy2(ROOT / 'LICENSE', root / 'LICENSE')
    monkeypatch.setattr(moss_duel_examples, 'ROOT', root)
    # Transport check only; the real pinned native archives are exercised by
    # the maintainer build, not simulated target-system acceptance here.
    dependencies = {name: b'synthetic native dependency' for name in build_gec_addon.SQLITE_MEMBERS}
    monkeypatch.setattr(moss_duel_examples, 'sqlite_members', lambda _: dependencies)
    output = root / 'build/examples'
    result = moss_duel_examples.prepare(output, Path('unused-in-transport-test.zip'))
    assert result['program_version'] == 'moss-duel-3'
    for filename in (result['standalone'], result['gec_connected']):
        with zipfile.ZipFile(output / filename) as archive:
            assert archive.testzip() is None
            assert 'project.godot' in archive.namelist()
            assert 'connection.json' not in archive.namelist()
            assert not any('/.godot/' in name or name.startswith(('.godot/', 'exports/')) for name in archive.namelist())
            assert archive.read('scripts/main.gd') == (ROOT / 'examples/moss_duel/scripts/main.gd').read_bytes()
            readme = archive.read('README.md').decode()
            assert 'blob/main/' in readme
            if filename == result['standalone']:
                assert not any(name.startswith(('addons/', 'gec_', 'package_')) for name in archive.namelist())
            else:
                assert all(name in archive.namelist() for name in dependencies)
                assert 'addons/gec/session.gd' in archive.namelist()
    before = (output / result['standalone']).read_bytes()
    with pytest.raises(ValueError, match='new directory'):
        moss_duel_examples.prepare(output, Path('unused-in-transport-test.zip'))
    assert (output / result['standalone']).read_bytes() == before
