"""Legacy preassembled Moss Duel integration reference; optionally export both.

Not the manual integration exercise. Use moss_duel_standalone.py for the clean
game and build_gec_addon.py for the independent generic GEC download.
Use a NEW --output directory: existing work is never overwritten. Dependencies
come only from the exact official archives locked below; binaries stay ignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
SQLITE_SHA = '26966044757cf86a223a8027f8bc88c49c289ab047dcf8138bb591d7632e580e'
TEMPLATES_SHA = 'f298490b8d44d934be425a5a65a51bf15f422428b229a06a6e11d9ffea248011'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verified(path, expected):
    if digest(path) != expected:
        raise ValueError(f'Official archive digest mismatch: {path.name}')
    return zipfile.ZipFile(path)


def game_member(path):
    name = path.as_posix()
    return (name in {'project.godot', 'main.tscn', 'export_presets.cfg', 'README.md', 'THIRD_PARTY_NOTICES.md', 'assets/asset_manifest.json'}
            or path.parts[0] in {'scripts', 'tests'} and path.suffix in {'.gd', '.uid'}
            or path.parent.as_posix() == 'assets/audio' and (name.endswith('.ogg') or name.endswith('.ogg.import'))
            or name in {'assets/licenses/kenney_rpg_audio_license.txt', 'assets/licenses/kenney_impact_sounds_license.txt'})


def secret_member(path):
    return path.name == 'connection.json' or path.name.startswith('.env') or any(suffix in path.name for suffix in ('.sqlite', '.db', 'credentials'))


def copy_sources(source, target, allow=None):
    if any(p.is_symlink() for p in (source, *source.parents)):
        raise ValueError('Source path links refused')
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if any(p in {'.godot', 'exports', '__pycache__', '.DS_Store'} for p in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f'Source link refused: {path}')
        if not path.is_file():
            continue
        if secret_member(path) or (allow is not None and not allow(relative)):
            raise ValueError(f'Unexpected source member refused: {relative}')
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def zip_tree(source, target):
    if any(p.is_symlink() for p in (source, *source.parents)):
        raise ValueError('Archive source path links refused')
    if target.exists() or target.is_symlink():
        raise FileExistsError(f'Archive target already exists: {target.name}')
    if any(p.is_symlink() for p in target.parents):
        raise ValueError('Archive target path links refused')
    fd, temporary_name = tempfile.mkstemp(prefix=f'.{target.name}.', suffix='.tmp', dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'w+b') as stream:
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(source.rglob('*')):
                    if path.is_symlink() or secret_member(path):
                        raise ValueError(f'Unsafe archive member refused: {path.name}')
                    if path.is_file() and '.godot' not in path.relative_to(source).parts:
                        archive.write(path, path.relative_to(source).as_posix())
            stream.flush()
            os.fsync(stream.fileno())
        with zipfile.ZipFile(temporary) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValueError(f'Completed archive failed CRC: {bad_member}')
        # Same-directory hard-link creation publishes the verified bytes in one
        # operation and fails if any file, directory or link now owns the name.
        # rename()/replace() would overwrite a target created during packaging.
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def engine_notice():
    from core.notices import engine_notices, _copyright_block, _license_block
    document = engine_notices()
    return ('Godot 4.7.2 and bundled dependencies\n\n' + document['engine_license'] + '\n' +
            _copyright_block(document['third_party_copyright']) + '\n' + _license_block(document['third_party_licenses']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sqlite-archive', type=Path, required=True)
    parser.add_argument('--templates-archive', type=Path)
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args()
    raw_output = args.output.absolute()
    if any(p.is_symlink() for p in (raw_output, *raw_output.parents)):
        raise ValueError('Output path links refused')
    output = raw_output.resolve()
    # Only disposable repository build output; no arbitrary user-tree copies.
    if ROOT / 'build' not in output.parents or output.exists():
        raise ValueError('Use a new directory below repository build/')
    with verified(args.sqlite_archive, SQLITE_SHA) as archive:
        runtime = {name.removeprefix('demo/'): archive.read(name) for name in archive.namelist()
                   if name.startswith(('demo/addons/godot-sqlite/bin/libgdsqlite.macos.',
                                       'demo/addons/godot-sqlite/bin/libgdsqlite.windows.')) and not name.endswith('/')}
    if not runtime or not any('windows.' in name for name in runtime) or not any('macos.' in name for name in runtime):
        raise ValueError('Required native dependency members missing')
    if args.build and not args.templates_archive:
        parser.error('--build requires the official --templates-archive')
    output.mkdir(parents=True)
    standalone = output / 'standalone'
    kit = output / 'kit'
    copy_sources(ROOT / 'examples/moss_duel', standalone, game_member)
    kit_members = {'README.md', 'export_presets.cfg', 'gec_entry.gd', 'gec_entry.tscn', 'gec_descriptor.json', 'package_web.gd', 'package_native.gd'}
    copy_sources(ROOT / 'integrations/moss_duel', kit, lambda p: p.as_posix() in kit_members or p.suffix == '.uid')
    copy_sources(ROOT / 'examples/synthetic_experiment/data', kit / 'data', lambda p: p.suffix in {'.gd', '.uid'})
    copy_sources(ROOT / 'examples/synthetic_experiment/addons/gec', kit / 'addons/gec', lambda p: p.suffix in {'.gd', '.uid'})
    (kit / 'addons/godot-sqlite').mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'examples/synthetic_experiment/addons/godot-sqlite/gdsqlite.gdextension', kit / 'addons/godot-sqlite/gdsqlite.gdextension')
    for name, content in runtime.items():
        path = kit / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for name in ('sdk.js', 'bridge.js', 'inputs.js', 'shell.js'):
        (kit / 'gec_web').mkdir(exist_ok=True)
        shutil.copy2(ROOT / 'packages/gec_web' / name, kit / 'gec_web' / name)
    notice = engine_notice()
    for project in (standalone, kit):
        shutil.copy2(ROOT / 'LICENSE', project / 'LICENSE')
        (project / 'GODOT_NOTICES.txt').write_text(notice)
        shutil.copy2(ROOT / 'docs/moss_duel_gec.md', project / 'GEC_GUIDE.md')
    shutil.copy2(ROOT / 'third_party/godot_sqlite_license.md', kit / 'SQLITE_LICENSE.md')
    (kit / 'SQLITE_NOTICE.txt').write_text('SQLite is in the public domain: https://www.sqlite.org/copyright.html\n')
    readme = standalone / 'README.md'
    readme.write_text(readme.read_text().replace('../../docs/moss_duel_gec.md', 'GEC_GUIDE.md'))
    zip_tree(standalone, output / 'moss_duel_standalone.zip')
    zip_tree(kit, output / 'moss_duel_gec_kit.zip')
    if args.build:
        if not subprocess.check_output(['godot', '--version'], text=True).startswith('4.7.2.stable.'):
            raise ValueError('Godot 4.7.2 is required')
        integrated = output / 'integrated_verification_copy'
        copy_sources(standalone, integrated)
        copy_sources(kit, integrated)
        path = integrated / 'project.godot'
        path.write_text(path.read_text().replace('res://main.tscn', 'res://gec_entry.tscn'))
        with verified(args.templates_archive, TEMPLATES_SHA) as archive:
            for project in (standalone, integrated):
                templates = project / '.godot/templates'
                templates.mkdir(parents=True, exist_ok=True)
                for name in ('web_nothreads_debug.zip', 'web_nothreads_release.zip', 'macos.zip', 'windows_release_x86_64.exe'):
                    (templates / name).write_bytes(archive.read('templates/' + name))
                presets = project / 'export_presets.cfg'
                content = presets.read_text().replace('[preset.0.options]', '[preset.0.options]\ncustom_template/debug="res://.godot/templates/web_nothreads_debug.zip"\ncustom_template/release="res://.godot/templates/web_nothreads_release.zip"')
                content = content.replace('[preset.1.options]', '[preset.1.options]\ncustom_template/debug="res://.godot/templates/macos.zip"\ncustom_template/release="res://.godot/templates/macos.zip"')
                content = content.replace('[preset.2.options]', '[preset.2.options]\ncustom_template/release="res://.godot/templates/windows_release_x86_64.exe"')
                presets.write_text(content)
        for project in (standalone, integrated):
            command = ['godot', '--headless', '--path', str(project), '--editor', '--import']
            imported = subprocess.run(command, capture_output=True, text=True)
            log = imported.stdout + imported.stderr
            (output / (project.name + '_import.log')).write_text(log)
            if imported.returncode == -6 and not any(error in log for error in ('SCRIPT ERROR', 'Parse Error', 'Failed to load script')):
                # Godot 4.7.2 can abort on cold GDExtension editor shutdown.
                # Preserve that failure and retry the identical import once.
                print(f'{project.name}: cold import aborted; log preserved, retrying once')
                imported = subprocess.run(command, capture_output=True, text=True)
                (output / (project.name + '_import_retry.log')).write_text(imported.stdout + imported.stderr)
            imported.check_returncode()
            for preset, target in [('Web', 'web/index.html'), ('macOS', 'native/moss_duel.zip'), ('Windows', 'windows/Moss Duel/Moss Duel.exe')]:
                target = project / 'exports' / target
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(['godot', '--headless', '--path', str(project), '--export-release', preset, str(target)], check=True)
            if project == standalone:
                for target in ('native', 'windows/Moss Duel', 'web'):
                    folder = project / 'exports' / target
                    shutil.copy2(project / 'GODOT_NOTICES.txt', folder / 'GODOT_NOTICES.txt')
                    shutil.copy2(project / 'LICENSE', folder / 'LICENSE')
                    shutil.copy2(project / 'THIRD_PARTY_NOTICES.md', folder / 'ASSET_NOTICES.md')
                    shutil.copytree(project / 'assets/licenses', folder / 'asset_licenses')
    manifest = {path.relative_to(output).as_posix(): {'size': path.stat().st_size, 'sha256': digest(path)}
                for path in sorted(output.glob('*.zip'))}
    (output / 'delivery_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'files': manifest}, indent=2))


if __name__ == '__main__':
    main()
