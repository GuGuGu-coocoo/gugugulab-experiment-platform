"""03D bounded native program archive intake.

These tests exercise the real validator against real ZIP bytes: one accepted
synthetic macOS ``.app`` archive and one archive per rejected intake rule
(traversal, absolute, colliding, linked, script, missing members, digest,
compression ratio and both expansion limits). Nothing is extracted, executed or
written to disk. The complete frozen package and the platform-aware current
release are covered by the tests the next batch adds to this module.
"""
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest

from core.packages import native_program_valid
from core.protocol import Rejected


APP = 'GEP Synthetic Experiment.app'
PLIST = f'{APP}/Contents/Info.plist'
BINARY = f'{APP}/Contents/MacOS/GEP Synthetic Experiment'
PCK = f'{APP}/Contents/Resources/GEP Synthetic Experiment.pck'
FRAMEWORK = f'{APP}/Contents/Frameworks/libgdsqlite.dylib'
SIGNATURE = f'{APP}/Contents/_CodeSignature/CodeResources'


def descriptor_document():
    return json.loads(Path('examples/synthetic_experiment/descriptor.json').read_text())


def member(name, mode, data):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info, data


def directory(name, mode=0o755):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFDIR | mode) << 16
    return info, b''


def app_members():
    return [
        member(SIGNATURE, 0o644, b'synthetic signature resources'),
        member(BINARY, 0o755, b'\xcf\xfa\xed\xfe' + b'synthetic-arm64-binary' * 64),
        member(PCK, 0o644, b'synthetic-pck-payload' * 32),
        member(FRAMEWORK, 0o755, b'synthetic-dylib' * 16),
        member(PLIST, 0o644, b'<plist><dict><key>CFBundleName</key><string>GEP</string></dict></plist>'),
    ]


def archive_bytes(members):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for info, data in members:
            output.writestr(info, data)
    return target.getvalue()


def descriptor_for(raw, **overrides):
    document = descriptor_document()
    document.update({'platform': 'macos_arm64', 'program_sha256': hashlib.sha256(raw).hexdigest(), **overrides})
    return document


def valid_archive():
    raw = archive_bytes(app_members())
    return raw, descriptor_for(raw)


def hostile_archive(kind):
    """One archive per rejected native intake rule."""
    raw, _descriptor = valid_archive()
    if kind == 'malformed':
        return raw[: len(raw) // 2]
    if kind == 'file_count':
        members = app_members() + [member(f'{APP}/Contents/Resources/extra-{index}.txt', 0o644, b'x') for index in range(4100)]
        return archive_bytes(members)
    if kind == 'ratio':
        members = app_members() + [member(f'{APP}/Contents/Resources/repeat.txt', 0o644, b'0' * 400000)]
        return archive_bytes(members)
    members = app_members()
    if kind == 'traversal':
        members.append(member(f'{APP}/Contents/../escape', 0o644, b'x'))
    elif kind == 'absolute':
        members.append(member('/tmp/escape', 0o644, b'x'))
    elif kind == 'backslash':
        members.append(member(f'{APP}\\Contents\\escape', 0o644, b'x'))
    elif kind == 'duplicate_path':
        members.append(member(BINARY.upper(), 0o644, b'x'))
    elif kind == 'symlink':
        info = zipfile.ZipInfo(f'{APP}/Contents/Resources/link')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        members.append((info, b'../MacOS/GEP Synthetic Experiment'))
    elif kind == 'script':
        members.append(member(f'{APP}/Contents/Resources/launch.command', 0o755, b'#!/bin/sh\necho synthetic\n'))
    elif kind == 'outside_app':
        members.append(member('readme.txt', 0o644, b'external note'))
    elif kind == 'missing_binary':
        members = [entry for entry in members if entry[0].filename != BINARY]
    elif kind == 'missing_info_plist':
        members = [entry for entry in members if entry[0].filename != PLIST]
    elif kind == 'missing_pck':
        members = [entry for entry in members if entry[0].filename != PCK]
    elif kind == 'missing_dependencies':
        members.append(directory(f'{APP}/Contents/Frameworks/'))
        members = [entry for entry in members if entry[0].filename != FRAMEWORK]
    else:
        raise AssertionError(kind)
    return archive_bytes(members)


def test_native_program_accepts_bound_app_archive():
    raw, descriptor = valid_archive()
    summary = native_program_valid(raw, descriptor)
    assert summary['platform'] == 'macos_arm64' and summary['app'] == APP
    assert summary['digest'] == descriptor['program_sha256'] and summary['members'] == 5


@pytest.mark.parametrize('kind,code', [
    ('traversal', 'unsafe_path'), ('absolute', 'unsafe_path'), ('backslash', 'unsafe_path'),
    ('duplicate_path', 'duplicate_path'), ('symlink', 'non_regular_file'), ('script', 'unsafe_script'),
    ('outside_app', 'reserved_path'), ('missing_binary', 'missing_binary'),
    ('missing_info_plist', 'missing_info_plist'), ('missing_pck', 'missing_pck'),
    ('missing_dependencies', 'missing_dependencies'), ('malformed', 'invalid_archive'),
    ('file_count', 'file_count'), ('ratio', 'compression_ratio'),
])


def test_native_program_rejects_hostile_archive(kind, code):
    hostile = hostile_archive(kind)
    descriptor = descriptor_for(hostile)
    with pytest.raises(Rejected, match=code):
        native_program_valid(hostile, descriptor)


def test_native_program_rejects_mismatched_digest_and_bounds(monkeypatch):
    raw, descriptor = valid_archive()
    other = descriptor_for(archive_bytes(app_members() + [member(f'{APP}/Contents/Resources/extra.txt', 0o644, b'y')]))
    with pytest.raises(Rejected, match='program_digest_mismatch'):
        native_program_valid(raw, other)
    from core import packages
    monkeypatch.setattr(packages, 'MAX_NATIVE_ARCHIVE', 1)
    with pytest.raises(Rejected, match='archive_limit') as result:
        native_program_valid(raw, descriptor)
    assert result.value.status == 413
    monkeypatch.undo()
    monkeypatch.setattr(packages, 'MAX_NATIVE_EXPANDED', 1)
    with pytest.raises(Rejected, match='expanded_limit'):
        native_program_valid(raw, descriptor)
