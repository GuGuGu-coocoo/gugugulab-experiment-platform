"""Build a task-neutral GEC Godot download from this checkout and pinned SQLite.

No network, game project mutation, connection configuration or export occurs.
Usage: python tools/build_gec_addon.py --output build/<NEW-directory>
       --sqlite-archive <official-godot-sqlite-v4.7.zip>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SQLITE_SHA = '26966044757cf86a223a8027f8bc88c49c289ab047dcf8138bb591d7632e580e'
SQLITE_SOURCE = 'https://github.com/2shady4u/godot-sqlite/releases/tag/v4.7'
SDK_VERSION = '0.1.0'
HOST_VERSION = '4.7.2'
ARCHIVE_NAME = 'gec_godot_0_1_0.zip'
MAX_SOURCE_BYTES = 16 * 1024 * 1024
SQLITE_MEMBERS = (
    'addons/godot-sqlite/bin/libgdsqlite.windows.template_debug.x86_64.dll',
    'addons/godot-sqlite/bin/libgdsqlite.windows.template_release.x86_64.dll',
    'addons/godot-sqlite/bin/libgdsqlite.macos.template_debug.framework/Resources/Info.plist',
    'addons/godot-sqlite/bin/libgdsqlite.macos.template_debug.framework/libgdsqlite.macos.template_debug',
    'addons/godot-sqlite/bin/libgdsqlite.macos.template_release.framework/Resources/Info.plist',
    'addons/godot-sqlite/bin/libgdsqlite.macos.template_release.framework/libgdsqlite.macos.template_release',
)
DOC_LINKS = ('gec_godot.md', 'moss_duel_gec.md', 'experiment_developer.md', 'protocol.md',
             'researcher.md', 'maintainer.md')


def read_source(path: Path) -> bytes:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError(f'Source path links refused: {path.name}')
    if not path.is_file():
        raise FileNotFoundError(f'Required package source missing: {path.relative_to(ROOT)}')
    if not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError(f'Source empty or exceeds limit: {path.name}')
    content = path.read_bytes()
    if not 0 < len(content) <= MAX_SOURCE_BYTES:
        raise ValueError(f'Source empty or changed beyond limit: {path.name}')
    return content


def source_members() -> dict[str, bytes]:
    sources = {
        'addons/gec/experiment_data.gd': 'examples/synthetic_experiment/data/experiment_data.gd',
        'addons/gec/local_backend.gd': 'examples/synthetic_experiment/data/local_backend.gd',
        'addons/gec/session.gd': 'packages/gec_godot/session.gd',
        'addons/godot-sqlite/gdsqlite.gdextension':
            'examples/synthetic_experiment/addons/godot-sqlite/gdsqlite.gdextension',
        'package_web.gd': 'packages/gec_godot/package_web.gd',
        'package_native.gd': 'packages/gec_godot/package_native.gd',
        'gec_descriptor.example.json': 'packages/gec_godot/gec_descriptor.example.json',
        'GEC_LICENSE.txt': 'LICENSE',
        'SQLITE_LICENSE.md': 'third_party/godot_sqlite_license.md',
    }
    for name in ('native_backend', 'web_backend', 'shell'):
        sources[f'addons/gec/{name}.gd'] = f'examples/synthetic_experiment/addons/gec/{name}.gd'
    for name in ('sdk', 'bridge', 'inputs', 'shell'):
        sources[f'gec_web/{name}.js'] = f'packages/gec_web/{name}.js'
    payloads = {destination: read_source(ROOT / source) for destination, source in sources.items()}
    # Optional Godot UID sidecars refer to the same source scripts. Nothing is
    # recursively collected: scenes, presets, private configuration and caches
    # cannot enter the download through an unrelated file beside a script.
    for destination, source in sources.items():
        uid = ROOT / (source + '.uid')
        if source.endswith('.gd') and (uid.exists() or uid.is_symlink()):
            payloads[destination + '.uid'] = read_source(uid)
    guide = read_source(ROOT / 'docs/gec_godot.md').decode('utf-8')
    for name in DOC_LINKS:
        guide = guide.replace(f'({name})',
                              f'(https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/main/docs/{name})')
    payloads['README.md'] = guide.encode('utf-8')
    notices = json.loads(read_source(ROOT / 'server/core/data/godot_engine_notices.json'))
    version = notices.get('engine_version', {})
    if tuple(version.get(key) for key in ('major', 'minor', 'patch')) != (4, 7, 2):
        raise ValueError('Frozen Godot notices do not match 4.7.2')
    engine_license = notices.get('engine_license')
    licenses = notices.get('third_party_licenses')
    copyrights = notices.get('third_party_copyright')
    if not isinstance(engine_license, str) or not engine_license.strip() or not isinstance(licenses, dict) or not licenses or not isinstance(copyrights, list) or not copyrights:
        raise ValueError('Frozen Godot notices are incomplete')
    sections = ['Godot 4.7.2 and bundled dependencies', engine_license,
                json.dumps(copyrights, indent=2, ensure_ascii=False)]
    for name, text in sorted(licenses.items()):
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Frozen Godot license text is invalid')
        sections.append(f'--- {name} ---\n{text}')
    payloads['GEC_GODOT_NOTICES.txt'] = ('\n\n'.join(sections) + '\n').encode('utf-8')
    payloads['SQLITE_NOTICE.txt'] = b'SQLite is in the public domain: https://www.sqlite.org/copyright.html\n'
    payloads['GEC_THIRD_PARTY_NOTICES.txt'] = (
        'Optional GEC Godot SDK: third-party notices\n\n'
        f'godot-sqlite v4.7 ({SQLITE_SOURCE}) is MIT licensed; its original notice is SQLITE_LICENSE.md.\n'
        'SQLite is in the public domain; see SQLITE_NOTICE.txt.\n'
        'The pinned engine notices for exported Godot 4.7.2 programs are GEC_GODOT_NOTICES.txt.\n'
        'GEC project code is covered by GEC_LICENSE.txt. These files do not change the host project or asset licenses.\n'
    ).encode('utf-8')
    return payloads


def sqlite_members(path: Path) -> dict[str, bytes]:
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError('SQLite archive must be an existing ordinary file without path links')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != SQLITE_SHA:
            raise ValueError('Official SQLite archive digest mismatch')
    result = {}
    with zipfile.ZipFile(path) as archive:
        for destination in SQLITE_MEMBERS:
            try:
                member = archive.getinfo('demo/' + destination)
            except KeyError as error:
                raise ValueError(f'Official SQLite dependency missing: {destination}') from error
            if stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG) or not 0 < member.file_size <= MAX_SOURCE_BYTES:
                raise ValueError(f'Invalid SQLite dependency member: {destination}')
            content = archive.read(member)
            if len(content) != member.file_size:
                raise ValueError(f'Truncated SQLite dependency: {destination}')
            result[destination] = content
    return result


def atomic_archive(payloads: dict[str, bytes], target: Path) -> None:
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise ValueError('Download target path links refused')
    if target.exists() or target.is_symlink():
        raise FileExistsError(f'Download target already exists: {target.name}')
    fd, temporary_name = tempfile.mkstemp(prefix=f'.{target.name}.', suffix='.tmp', dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'w+b') as stream:
            with zipfile.ZipFile(stream, 'w') as archive:
                for name, content in sorted(payloads.items()):
                    info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    archive.writestr(info, content)
            stream.flush()
            os.fsync(stream.fileno())
        with zipfile.ZipFile(temporary) as archive:
            if set(archive.namelist()) != set(payloads) or archive.testzip() is not None:
                raise ValueError('GEC download ZIP failed its member/CRC check')
            for name, original in payloads.items():
                if archive.read(name) != original:
                    raise ValueError(f'GEC download bytes changed while writing: {name}')
        os.link(temporary, target)  # Atomic, fails on a concurrent name collision.
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sqlite-archive', type=Path, required=True)
    args = parser.parse_args()
    raw_output = args.output.absolute()
    if any(part.is_symlink() for part in (raw_output, *raw_output.parents)):
        raise ValueError('Output path links refused')
    output = raw_output.resolve()
    if ROOT / 'build' not in output.parents or output.exists():
        raise ValueError('Use a NEW directory below repository build/')
    payloads = source_members()  # Missing session.gd is a real failure, no stub.
    payloads.update(sqlite_members(args.sqlite_archive))
    content_manifest = {
        'format_version': 1, 'component': 'gec_godot', 'sdk_version': SDK_VERSION,
        'host_version': HOST_VERSION, 'protocol_version': 'gep/1',
        'sqlite_archive': {'source': SQLITE_SOURCE, 'sha256': SQLITE_SHA},
        'files': {name: {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
                  for name, content in sorted(payloads.items())},
    }
    payloads['content_manifest.json'] = (json.dumps(content_manifest, indent=2) + '\n').encode('utf-8')
    output.mkdir(parents=True)
    target = output / ARCHIVE_NAME
    atomic_archive(payloads, target)
    result = {'archive': str(target), 'size': target.stat().st_size,
              'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'members': len(payloads)}
    with (output / 'delivery_manifest.json').open('x') as manifest:
        manifest.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
