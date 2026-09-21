"""Frozen third-party notices for the complete native distribution (03D).

The platform accepts two native contracts (``macos_arm64`` and
``windows_x64``, both with ``host_version`` 4.7.2), so every complete package it
assembles bundles the
official Godot 4.7.2 export template and the godot-sqlite GDExtension. Those
components require their own license and copyright notices, which the exported
``.app`` does not carry: the platform materializes them from the sources kept
in this repository.

Nothing here is restated by hand. The engine document is the byte-for-byte
output of the official local engine's own notice interface
(``Engine.get_license_text``, ``Engine.get_copyright_info``,
``Engine.get_license_info``; the export script and evidence are recorded with
the platform verification), and the godot-sqlite document is the vendored
upstream license file. This module only frames both documents and fails closed
when a source is missing or malformed, so a complete package can never claim
notices the platform cannot reproduce.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings

from .protocol import Rejected, require

THIRD_PARTY_NOTICES_MEMBER = 'THIRD_PARTY_NOTICES.txt'
GODOT_SQLITE_LICENSE = Path('third_party') / 'godot_sqlite_license.md'
ENGINE_NOTICES_SOURCE = Path(__file__).resolve().parent / 'data' / 'godot_engine_notices.json'
EXPECTED_ENGINE = (4, 7, 2)
_SECTIONS = '=' * 60


def engine_notices():
    """The frozen official-engine notices document, validated before use."""
    path = ENGINE_NOTICES_SOURCE
    require(path.is_file(), 'engine_notices_missing', 409)
    try:
        document = json.loads(path.read_text('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise Rejected('engine_notices_invalid')
    require(isinstance(document, dict), 'engine_notices_invalid', 409)
    version = document.get('engine_version')
    require(isinstance(version, dict), 'engine_notices_invalid', 409)
    require(tuple(version.get(key) for key in ('major', 'minor', 'patch')) == EXPECTED_ENGINE,
            'engine_notices_version', 409)
    require(isinstance(version.get('string'), str) and version['string'], 'engine_notices_version', 409)
    require(isinstance(document.get('engine_license'), str) and document['engine_license'].strip(),
            'engine_notices_invalid', 409)
    entries = document.get('third_party_copyright')
    require(isinstance(entries, list) and entries, 'engine_notices_invalid', 409)
    licenses = document.get('third_party_licenses')
    require(isinstance(licenses, dict) and licenses, 'engine_notices_invalid', 409)
    for name, text in licenses.items():
        require(isinstance(name, str) and name and isinstance(text, str) and text.strip(),
                'engine_notices_invalid', 409)
    return document


def godot_sqlite_license_bytes():
    """The vendored upstream godot-sqlite license, unchanged."""
    path = Path(settings.BASE_DIR) / GODOT_SQLITE_LICENSE
    require(path.is_file(), 'third_party_license_missing', 409)
    return path.read_bytes()


def _copyright_block(entries):
    lines = []
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get('name'), str) and entry['name'],
                'engine_notices_invalid', 409)
        lines.append(entry['name'])
        parts = entry.get('parts')
        require(isinstance(parts, list) and parts, 'engine_notices_invalid', 409)
        for part in parts:
            require(isinstance(part, dict), 'engine_notices_invalid', 409)
            copyrights = part.get('copyright')
            files = part.get('files')
            require(isinstance(copyrights, list) and copyrights and isinstance(files, list) and files,
                    'engine_notices_invalid', 409)
            lines.append('  copyright: ' + '; '.join(str(item) for item in copyrights))
            lines.append('  files: ' + ', '.join(str(item) for item in files))
            lines.append('  license: ' + str(part.get('license', '')))
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def _license_block(licenses):
    blocks = []
    for name in sorted(licenses):
        blocks.append(f'--- {name} ---\n\n{licenses[name].rstrip()}\n')
    return '\n'.join(blocks)


def third_party_notices():
    """One deterministic, human-readable notice for the bundled components."""
    document = engine_notices()
    sqlite_license = godot_sqlite_license_bytes().decode('utf-8').rstrip()
    header = (
        'GEP third-party notices for the complete native distribution\n'
        f'{_SECTIONS}\n'
        '\n'
        'This complete package bundles the official Godot {version} engine and the\n'
        'godot-sqlite GDExtension (which bundles SQLite). Their license and copyright\n'
        'notices are reproduced below, unchanged, from the official local engine\n'
        'notice interface (Engine.get_license_text, Engine.get_copyright_info,\n'
        'Engine.get_license_info) and the vendored upstream godot-sqlite license file.\n'
        'The GEP project license is the separate LICENSE member of this package and\n'
        'does not cover these third-party components.\n'
        '\n'
        '\n'
        '1. {version} - Expat (MIT license)\n'
        '{rule}\n'
        '\n'
        '{engine}\n'
        '\n'
        '2. Third-party components bundled in the official Godot engine\n'
        '{rule}\n'
        '\n'
        '{copyright}\n'
        '\n'
        '3. License texts for those bundled components\n'
        '{rule}\n'
        '\n'
        '{licenses}\n'
        '\n'
        '4. godot-sqlite - MIT license\n'
        '{rule}\n'
        '\n'
        '{sqlite}\n'
        '\n'
        '5. SQLite - public domain\n'
        '{rule}\n'
        '\n'
        'godot-sqlite bundles SQLite. SQLite is in the public domain; see\n'
        'https://www.sqlite.org/copyright.html (source: docs/third_party.md).\n'
    ).format(
        version=document['engine_version']['string'],
        engine=document['engine_license'].rstrip(),
        copyright=_copyright_block(document['third_party_copyright']).rstrip(),
        licenses=_license_block(document['third_party_licenses']).rstrip(),
        sqlite=sqlite_license,
        rule='-' * 60,
    )
    return header.encode('utf-8')
