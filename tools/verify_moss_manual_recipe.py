"""Apply the published manual recipe only in a disposable engineering copy.

This is not the user's manual acceptance. It starts with two independent ZIPs,
copies the documentation's actual code blocks, and leaves both archives and
the original game untouched. Output is never distributed as a prewired kit.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import zipfile
from pathlib import Path

from moss_duel_delivery import TEMPLATES_SHA, digest, verified

ROOT = Path(__file__).resolve().parents[1]
SCENE = '''[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://gec_entry.gd" id="1"]
[ext_resource type="PackedScene" path="res://main.tscn" id="2"]

[node name="GECEntry" type="Node"]
script = ExtResource("1")

[node name="Game" parent="." instance=ExtResource("2")]
'''
WEB_EXCLUDE = 'addons/godot-sqlite/*,connection.json,gec_descriptor*.json,package_web.gd,package_native.gd,gec_web/*,exports/*'
NATIVE_EXCLUDE = 'connection.json,gec_descriptor*.json,package_web.gd,package_native.gd,gec_web/*,exports/*'
DEPENDENCIES = ('addons/', 'gec_web/', 'package_web.gd', 'package_native.gd',
                'SQLITE_LICENSE.md', 'SQLITE_NOTICE.txt', 'GEC_LICENSE.txt', 'GEC_GODOT_NOTICES.txt', 'GEC_THIRD_PARTY_NOTICES.txt')


def extract(archive: Path, destination: Path, select=lambda _: True):
    with zipfile.ZipFile(archive) as reader:
        for item in reader.infolist():
            if item.is_dir() or not select(item.filename):
                continue
            relative = Path(item.filename)
            if relative.is_absolute() or '..' in relative.parts or '\\' in item.filename:
                raise ValueError('Unexpected archive path')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(reader.read(item))


def apply_recipe(game: Path, addon: Path, output: Path):
    if any(path.is_symlink() for path in (output.absolute(), *output.absolute().parents)):
        raise ValueError('Use an ordinary new output path')
    output = output.resolve()
    if ROOT / 'build' not in output.parents or output.exists():
        raise ValueError('Use a new directory below repository build/')
    output.mkdir(parents=True)
    project = output / 'integrated_verification_copy'
    project.mkdir()
    extract(game, project)
    original = {path.relative_to(project).as_posix(): digest(path) for path in project.rglob('*') if path.is_file()}
    if any(name.startswith(('addons/', 'gec_', 'data/', 'package_')) for name in original):
        raise ValueError('Manual exercise requires a clean independent game')
    extract(addon, project, lambda name: name.startswith(DEPENDENCIES))
    guide = (ROOT / 'docs/moss_duel_gec.md').read_text()
    script = re.search(r'```gdscript\n(.*?)\n```', guide, re.S).group(1) + '\n'
    descriptor_text = re.search(r'```json\n(.*?)\n```', guide, re.S).group(1) + '\n'
    json.loads(descriptor_text)
    (project / 'gec_entry.gd').write_text(script)
    (project / 'gec_entry.tscn').write_text(SCENE)
    (project / 'gec_descriptor.json').write_text(descriptor_text)
    settings = project / 'project.godot'
    settings.write_text(settings.read_text().replace('res://main.tscn', 'res://gec_entry.tscn'))
    presets = project / 'export_presets.cfg'
    text = presets.read_text()
    for index, excludes in [(0, WEB_EXCLUDE), (1, NATIVE_EXCLUDE), (2, NATIVE_EXCLUDE)]:
        pattern = rf'(\[preset\.{index}\]\n.*?exclude_filter=")([^"]*)(")'
        text, count = re.subn(pattern, lambda match: match[1] + match[2] + ',' + excludes + match[3], text, count=1, flags=re.S)
        if count != 1:
            raise ValueError('Missing expected independent game export preset')
    text = text.replace('[preset.0.options]', '[preset.0.options]\nhtml/head_include="<script type=\\"module\\" src=\\"gec/bridge.js\\"></script>"')
    presets.write_text(text)
    unchanged = [name for name in original if name not in {'project.godot', 'export_presets.cfg'}]
    assert all(digest(project / name) == original[name] for name in unchanged), 'Original game was changed'
    report = {'human_manual_acceptance': 'NOT_RUN', 'purpose': 'Published recipe engineering verification only',
              'game_sha256': digest(game), 'addon_sha256': digest(addon),
              'guide_sha256': digest(ROOT / 'docs/moss_duel_gec.md'),
              'copied_script_sha256': digest(project / 'gec_entry.gd'),
              'unchanged_game_members': len(unchanged), 'exports': {}}
    (output / 'recipe_evidence.json').write_text(json.dumps(report, indent=2) + '\n')
    return project, report


def export(project: Path, output: Path, templates: Path, report: dict):
    if not subprocess.check_output(['godot', '--version'], text=True).startswith('4.7.2.stable.'):
        raise ValueError('Godot 4.7.2 required')
    with verified(templates, TEMPLATES_SHA) as reader:
        local = project / '.godot/templates'
        local.mkdir(parents=True)
        for name in ('web_nothreads_debug.zip', 'web_nothreads_release.zip', 'macos.zip', 'windows_release_x86_64.exe'):
            (local / name).write_bytes(reader.read('templates/' + name))
    presets = project / 'export_presets.cfg'
    text = presets.read_text()
    overrides = {
        0: 'custom_template/debug="res://.godot/templates/web_nothreads_debug.zip"\ncustom_template/release="res://.godot/templates/web_nothreads_release.zip"',
        1: 'custom_template/debug="res://.godot/templates/macos.zip"\ncustom_template/release="res://.godot/templates/macos.zip"',
        2: 'custom_template/release="res://.godot/templates/windows_release_x86_64.exe"',
    }
    for index, options in overrides.items():
        text = text.replace(f'[preset.{index}.options]', f'[preset.{index}.options]\n{options}')
    presets.write_text(text)

    def run(command, name):
        with (output / name).open('w') as stream:
            return subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=180)

    command = ['godot', '--headless', '--path', str(project), '--editor', '--import']
    imported = run(command, 'import.log')
    log = (output / 'import.log').read_text()
    if imported.returncode == -6 and not any(error in log for error in ('SCRIPT ERROR', 'Parse Error', 'Failed to load script')):
        report['cold_import_retry'] = True
        imported = run(command, 'import_retry.log')
    imported.check_returncode()
    for preset, relative in [('Web', 'web/index.html'), ('macOS', 'native/moss_duel.zip'), ('Windows', 'windows/Moss Duel/Moss Duel.exe')]:
        target = project / 'exports' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        result = run(['godot', '--headless', '--path', str(project), '--export-release', preset, str(target)], f'export_{preset}.log')
        report['exports'][preset] = {'exit_code': result.returncode, 'sha256': digest(target) if target.exists() else None}
        (output / 'recipe_evidence.json').write_text(json.dumps(report, indent=2) + '\n')
        result.check_returncode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', type=Path, required=True)
    parser.add_argument('--gec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--templates-archive', type=Path)
    args = parser.parse_args()
    project, report = apply_recipe(args.game, args.gec, args.output)
    if args.templates_archive:
        export(project, args.output.resolve(), args.templates_archive, report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
