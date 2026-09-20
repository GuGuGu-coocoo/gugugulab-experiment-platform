"""Package one exported platform build with its descriptor and program digest.

Web and macOS keep their historical layout. The Windows x64 program archive is
the exported program root directory (executable, PCK, the native SQLite DLL and
the packaged GDExtension manifest) stored as one bounded ZIP; its digest covers
the program bytes only and never the external connection configuration.

Usage: ``python tools/package_build.py web|native|windows``
"""
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_ROOT = 'GEP Synthetic Experiment'
WINDOWS_PROGRAM = ROOT / 'build' / 'windows' / WINDOWS_ROOT
WINDOWS_FILES = (
    'GEP Synthetic Experiment.exe',
    'GEP Synthetic Experiment.pck',
    'libgdsqlite.windows.template_release.x86_64.dll',
)
WINDOWS_EXTENSION = ('addons/godot-sqlite/gdsqlite.gdextension',
                     ROOT / 'examples/synthetic_experiment/addons/godot-sqlite/gdsqlite.gdextension')
WINDOWS_ARCHIVE = ROOT / 'build' / 'windows' / 'synthetic_windows.zip'
WINDOWS_DESCRIPTOR = ROOT / 'build' / 'windows' / 'descriptor.json'


def windows_descriptor():
    """The immutable Windows x64 descriptor: explicit entry, dependency and manifest."""
    return {
        'root': WINDOWS_ROOT,
        'entry': 'GEP Synthetic Experiment.exe',
        'dependencies': ['libgdsqlite.windows.template_release.x86_64.dll'],
        'extension': WINDOWS_EXTENSION[0],
    }


def package_windows(document):
    missing = [name for name in WINDOWS_FILES if not (WINDOWS_PROGRAM / name).is_file()]
    assert not missing, f'Missing Windows runtime output: {", ".join(missing)}'
    assert WINDOWS_EXTENSION[1].is_file(), 'Missing Godot SQLite GDExtension manifest'
    extension_target = WINDOWS_PROGRAM / WINDOWS_EXTENSION[0]
    extension_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(WINDOWS_EXTENSION[1], extension_target)
    document['platform'] = 'windows_x64'
    document['package'] = windows_descriptor()
    document['program_sha256'] = '0' * 64
    with zipfile.ZipFile(WINDOWS_ARCHIVE, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in WINDOWS_FILES:
            archive.write(WINDOWS_PROGRAM / name, f'{WINDOWS_ROOT}/{name}')
        archive.write(WINDOWS_EXTENSION[1], f'{WINDOWS_ROOT}/{WINDOWS_EXTENSION[0]}')
    # The digest covers the program archive bytes only (the descriptor is never a member).
    document['program_sha256'] = hashlib.sha256(WINDOWS_ARCHIVE.read_bytes()).hexdigest()
    WINDOWS_DESCRIPTOR.write_text(json.dumps(document))


def main():
    document = json.loads((ROOT / 'examples/synthetic_experiment/descriptor.json').read_text())
    target = sys.argv[1]
    if target == 'windows':
        package_windows(document)
    elif target == 'native':
        archive = ROOT / 'build/native/synthetic.zip'
        document['platform'] = 'macos_arm64'
        document['program_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        (ROOT / 'build/native/descriptor.json').write_text(json.dumps(document))
        with zipfile.ZipFile(archive) as source:
            for entry in source.infolist():
                destination = ROOT / 'build/native' / entry.filename
                if entry.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read(entry))
                destination.chmod((entry.external_attr >> 16) & 0o777 or 0o644)
    else:
        # Package only known synthetic runtime outputs, never acceptance URLs or reports.
        names = ['index.html', 'index.js', 'index.wasm', 'index.pck', 'index.png',
                 'index.audio.worklet.js', 'index.audio.position.worklet.js',
                 'gec/sdk.js', 'gec/bridge.js', 'gec/inputs.js', 'gec/shell.js']
        files = sorted(ROOT / 'build/web' / name for name in names)
        assert all(path.is_file() for path in files), 'Missing Web runtime output'
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.relative_to(ROOT / 'build').as_posix().encode())
            digest.update(path.read_bytes())
        document['platform'] = 'godot_web'
        document['program_sha256'] = digest.hexdigest()
        with zipfile.ZipFile(ROOT / 'build/synthetic_web.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(document))
            for path in files:
                archive.write(path, path.relative_to(ROOT / 'build'))
    print('Packaged ' + target + ' build with separate public configuration.')


if __name__ == '__main__':
    main()
