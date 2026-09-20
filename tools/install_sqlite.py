"""Install the verified SQLite runtime for the selected native platforms.

The archive is the official godot-sqlite v4.7 demo release published at
https://github.com/2shady4u/godot-sqlite/releases/download/v4.7/demo.zip and is
pinned by SHA-256 before any member is read. Only the bundled runtime files of
the requested platforms are copied into the (git-ignored) addon ``bin/``
directory: macOS frameworks and/or the Windows x86_64 DLLs. Nothing is fetched,
executed or built here.

``gdsqlite.gdextension`` is rewritten to the fixed declaration for every
platform this project supports, so the file does not depend on which runtimes
were installed locally; an export for a platform whose runtime is missing fails
loudly in Godot instead of silently shipping without SQLite.

Historical invocation (no platform argument) keeps installing macOS only.
"""
import hashlib
import sys
import zipfile
from pathlib import Path

ARCHIVE_SHA256 = '26966044757cf86a223a8027f8bc88c49c289ab047dcf8138bb591d7632e580e'
PREFIX = 'demo/addons/godot-sqlite/'
PLATFORMS = ('macos', 'windows')
DECLARATION = {
    'configuration': [
        '[configuration]',
        'entry_symbol = "sqlite_library_init"',
        'compatibility_minimum = "4.5"',
        '[libraries]',
    ],
    'macos': [
        'macos.debug = "res://addons/godot-sqlite/bin/libgdsqlite.macos.template_debug.framework"',
        'macos.release = "res://addons/godot-sqlite/bin/libgdsqlite.macos.template_release.framework"',
    ],
    'windows': [
        'windows.debug.x86_64 = "res://addons/godot-sqlite/bin/libgdsqlite.windows.template_debug.x86_64.dll"',
        'windows.release.x86_64 = "res://addons/godot-sqlite/bin/libgdsqlite.windows.template_release.x86_64.dll"',
    ],
}


def selected_platforms(argv):
    requested = argv[2:] or ['macos']
    unknown = sorted(set(requested) - set(PLATFORMS))
    assert not unknown, f'Unknown platform(s): {", ".join(unknown)}; expected one of {", ".join(PLATFORMS)}'
    return tuple(dict.fromkeys(requested))


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    archive_path = Path(sys.argv[1])
    platforms = selected_platforms(sys.argv)
    assert archive_path.is_file(), f'Archive not found: {archive_path}'
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert digest == ARCHIVE_SHA256, 'Archive digest mismatch'
    root = Path(__file__).resolve().parents[1] / 'examples/synthetic_experiment/addons/godot-sqlite'
    installed = []
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for platform in platforms:
            prefix = f'{PREFIX}bin/libgdsqlite.{platform}.'
            members = [name for name in names if name.startswith(prefix) and not name.endswith('/')]
            assert members, f'No {platform} runtime in the verified archive'
            for name in members:
                target = root / name.removeprefix(PREFIX)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
            installed.append(f'{platform} ({len(members)} files)')
        declaration = list(DECLARATION['configuration'])
        for platform in PLATFORMS:
            declaration.extend(DECLARATION[platform])
        (root / 'gdsqlite.gdextension').write_text('\n'.join(declaration) + '\n')
    print(f'Installed verified Godot SQLite v4.7 runtime for {", ".join(installed)}; '
          'gdextension declares macOS and Windows x86_64.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
