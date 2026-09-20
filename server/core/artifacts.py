"""Complete immutable distribution artifacts (03D).

A complete package binds one approved native program build to the release's
frozen public configuration and the platform metadata (schema, codebook, the
project license, the bundled third-party notices and a manifest of member
hashes). The heavy pieces never leave memory in one
piece: every member is streamed in bounded chunks while it is hashed.

The lifecycle is fixed by the design:

* the release id is preallocated, then the *public* configuration is frozen
  (never a credential or roster value);
* the program bytes and executable modes are copied unchanged -- nothing inside
  the ``.app`` is rewritten, so the exported signature stays valid;
* the member manifest is part of the artifact, the outer SHA-256 of the finished
  file is stored in the database only, so the manifest can never reference
  itself;
* the file is validated and committed with one atomic rename, and a released
  artifact is never overwritten; a repeated release of identical bytes reuses
  the stored file, different bytes are refused;
* downloads, sidecars and admission all re-verify the stored bytes against the
  recorded digest, so a tampered or missing artifact fails before a session can
  be created.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings

from .models import Audit
from .notices import THIRD_PARTY_NOTICES_MEMBER, third_party_notices
from .protocol import Rejected, require

ARTIFACT_FORMAT_VERSION = 'gep-artifact/v1'
ARTIFACT_MANIFEST = 'artifact_manifest.json'
CONFIG_MEMBER = 'connection.json'
SCHEMA_DIR = 'schemas'
CODEBOOK_MEMBER = 'metadata/codebook.json'
LICENSE_MEMBER = 'LICENSE'
PUBLISHED_ACTION = 'release.artifact_published'
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_CHUNK = 1 << 20
_REQUIRED_MEMBER_FIELDS = {'path', 'sha256', 'size', 'mode'}


def artifacts_root():
    root = Path(settings.DATA_DIR) / 'artifacts'
    root.mkdir(mode=0o700, exist_ok=True)
    return root


def programs_root():
    root = Path(settings.DATA_DIR) / 'packages'
    root.mkdir(mode=0o700, exist_ok=True)
    return root


def canonical_json(document):
    """One deterministic JSON encoding for frozen member bytes."""
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_file(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _zipinfo(name, mode):
    info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | (mode & 0o7777)) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def _stream_member(source, item):
    """Yield bounded chunks of one stored program member."""
    with source.open(item) as stream:
        while chunk := stream.read(_CHUNK):
            yield chunk


def _write_streamed(out, name, mode, chunks):
    hasher = hashlib.sha256()
    size = 0
    with out.open(_zipinfo(name, mode), 'w') as destination:
        for chunk in chunks:
            hasher.update(chunk)
            size += len(chunk)
            destination.write(chunk)
    return {'path': name, 'sha256': hasher.hexdigest(), 'size': size, 'mode': f'{mode & 0o7777:04o}'}


def _write_bytes(out, name, mode, data):
    with out.open(_zipinfo(name, mode), 'w') as destination:
        destination.write(data)
    return {'path': name, 'sha256': digest_bytes(data), 'size': len(data), 'mode': f'{mode & 0o7777:04o}'}


def _license_bytes():
    path = Path(settings.BASE_DIR) / 'LICENSE'
    require(path.is_file(), 'license_missing', 409)
    return path.read_bytes()


def _generated_members(release, config_bytes):
    """Frozen public configuration plus the platform metadata members."""
    descriptor = release.build.descriptor
    schemas = descriptor.get('schemas') if isinstance(descriptor, dict) else None
    require(isinstance(schemas, dict) and schemas, 'schema_missing', 409)
    members = {CONFIG_MEMBER: (0o644, config_bytes)}
    for key in sorted(schemas):
        require(isinstance(key, str) and 0 < len(key) <= 64 and all(c.isalnum() or c in '._-' for c in key), 'schema_name', 409)
        definition = schemas[key]
        require(isinstance(definition, dict) and set(definition) == {'id', 'version', 'schema'}, 'schema_definition', 409)
        members[f'{SCHEMA_DIR}/{key}.json'] = (0o644, canonical_json(definition))
    codebook = descriptor.get('codebook') if isinstance(descriptor, dict) else None
    require(isinstance(codebook, dict) and codebook, 'codebook_required', 409)
    members[CODEBOOK_MEMBER] = (0o644, canonical_json(codebook))
    members[LICENSE_MEMBER] = (0o644, _license_bytes())
    # The GEP license alone does not cover the bundled engine and its addon: the
    # complete package always carries the frozen third-party notices too.
    members[THIRD_PARTY_NOTICES_MEMBER] = (0o644, third_party_notices())
    return members


def _program_members(archive):
    """Stored program members with their modes; the app root and entry binary."""
    items = {}
    for item in archive.infolist():
        if item.filename.endswith('/'):
            continue
        path = PurePosixPath(item.filename)
        require(len(path.parts) >= 2 and path.parts[0].endswith('.app'), 'invalid_program', 409)
        items[item.filename] = item
    require(items, 'invalid_program', 409)
    roots = {PurePosixPath(name).parts[0] for name in items}
    require(len(roots) == 1, 'multiple_bundles', 409)
    entry = sorted(name for name, item in items.items()
                   if PurePosixPath(name).parts[1:3] == ('Contents', 'MacOS')
                   and ((item.external_attr >> 16) & 0o111))
    require(bool(entry), 'missing_binary', 409)
    return items, next(iter(roots)), entry[0]


def _freeze_config(release):
    """The release's public configuration, exactly as the download endpoint serves it."""
    from .gui import connection_config
    return connection_config(release)


def manifest_for(release, members, app, entry, config_bytes):
    descriptor = release.build.descriptor
    schemas = {}
    for key in sorted(descriptor['schemas']):
        definition = descriptor['schemas'][key]
        schemas[key] = {'id': definition['id'], 'version': definition['version'], 'path': f'{SCHEMA_DIR}/{key}.json'}
    return {
        'artifact_format_version': ARTIFACT_FORMAT_VERSION,
        'platform': 'macos_arm64',
        'app': app,
        'entry': entry,
        'config_member': CONFIG_MEMBER,
        'config_sha256': digest_bytes(config_bytes),
        'program_sha256': descriptor['program_sha256'],
        'study_id': str(release.study_id),
        'release_id': str(release.id),
        'build_id': str(release.build_id),
        'descriptor': {'version': descriptor['version'], 'platform': descriptor['platform'],
                       'host_version': descriptor['host_version'], 'sdk_version': descriptor['sdk_version'],
                       'protocol_version': descriptor['protocol_version'], 'schemas': schemas},
        'members': members,
    }


def assemble_artifact(target, release, config_bytes):
    """Write one complete artifact to ``target``; returns its member manifest."""
    program_path = programs_root() / release.build.package_path
    require(program_path.is_file(), 'native_program_missing', 409)
    stored_digest, _size = digest_file(program_path)
    require(stored_digest == release.build.descriptor.get('program_sha256'), 'program_digest_mismatch', 409)
    with zipfile.ZipFile(program_path) as source:
        items, app, entry = _program_members(source)
        generated = _generated_members(release, config_bytes)
        members = []
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as out:
            for name in sorted(set(items) | set(generated)):
                if name in generated:
                    mode, data = generated[name]
                    members.append(_write_bytes(out, name, mode, data))
                else:
                    item = items[name]
                    mode = (item.external_attr >> 16) & 0o7777 or 0o644
                    members.append(_write_streamed(out, name, mode, _stream_member(source, item)))
            manifest = manifest_for(release, members, app, entry, config_bytes)
            _write_bytes(out, ARTIFACT_MANIFEST, 0o644, canonical_json(manifest))
    return manifest


def _read_member(path, name):
    with zipfile.ZipFile(path) as archive:
        try:
            return archive.read(name)
        except KeyError:
            raise Rejected('invalid_artifact')


def verify_artifact_file(path, manifest, program_path):
    """Re-read the committed artifact and prove every member still matches.

    The manifest cannot prove itself: the program members are additionally
    compared against the stored program archive, so a complete package always
    contains the unchanged executable and resources.
    """
    require(not ({'artifact_digest', 'artifact_sha256', 'self_sha256'} & set(manifest)), 'artifact_self_reference', 409)
    expected = {entry['path']: entry for entry in manifest['members']}
    require(len(expected) == len(manifest['members']), 'invalid_artifact')
    with zipfile.ZipFile(path) as archive, zipfile.ZipFile(program_path) as program:
        names = [item.filename for item in archive.infolist() if not item.filename.endswith('/')]
        require(set(names) == set(expected) | {ARTIFACT_MANIFEST}, 'artifact_incomplete', 409)
        program_items = {item.filename: item for item in program.infolist() if not item.filename.endswith('/')}
        for name in names:
            if name == ARTIFACT_MANIFEST:
                continue
            entry = expected[name]
            info = archive.getinfo(name)
            require(f'{((info.external_attr >> 16) & 0o7777):04o}' == entry['mode'], 'artifact_mode_changed', 409)
            hasher = hashlib.sha256()
            size = 0
            with archive.open(name) as stream:
                while chunk := stream.read(_CHUNK):
                    hasher.update(chunk)
                    size += len(chunk)
            require(size == entry['size'] and hasher.hexdigest() == entry['sha256'], 'artifact_tampered', 409)
            if name in program_items:
                source_hash = hashlib.sha256()
                source_size = 0
                with program.open(program_items[name]) as stream:
                    while chunk := stream.read(_CHUNK):
                        source_hash.update(chunk)
                        source_size += len(chunk)
                require(source_size == size and source_hash.hexdigest() == entry['sha256'], 'artifact_program_changed', 409)
    return True


def load_manifest(raw):
    """Parse one artifact manifest; unknown format versions fail closed."""
    try:
        document = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise Rejected('invalid_artifact')
    require(isinstance(document, dict), 'invalid_artifact')
    require(document.get('artifact_format_version') == ARTIFACT_FORMAT_VERSION, 'unsupported_artifact_version', 409)
    members = document.get('members')
    require(isinstance(members, list) and members, 'invalid_artifact')
    seen = set()
    for entry in members:
        require(isinstance(entry, dict) and set(entry) == _REQUIRED_MEMBER_FIELDS, 'invalid_artifact')
        path = entry['path']
        require(isinstance(path, str) and path and path not in seen and '..' not in path.split('/') and not path.startswith('/'), 'invalid_artifact')
        require(isinstance(entry['sha256'], str) and len(entry['sha256']) == 64, 'invalid_artifact')
        require(type(entry['size']) is int and entry['size'] >= 0 and isinstance(entry['mode'], str), 'invalid_artifact')
        seen.add(path)
    return document


def store_program_archive(path, raw):
    """Atomically store one content-addressed program archive, never overwriting.

    The file name is the archive digest, so an existing file is only reused when
    its bytes still hash to that name; otherwise the upload is refused instead of
    replacing stored bytes.
    """
    if path.exists():
        digest, size = digest_file(path)
        require(digest == path.stem and size == len(raw), 'storage_conflict', 409)
        return path
    temp = path.parent / f'{secrets.token_hex(16)}.tmp'
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    return path


def publish_complete_artifact(release, actor=None):
    """Assemble, validate and atomically commit one complete artifact.

    The caller owns the database transaction and the draft release row: this
    function only writes files. A failure before the rename leaves no artifact;
    a failure after it leaves an unreferenced file while the transaction rolls
    the release back, so the previous release stays reachable. An existing
    artifact file is never overwritten.
    """
    require(not release.artifact_digest and not release.artifact_path, 'artifact_conflict', 409)
    descriptor = release.build.descriptor
    require(isinstance(descriptor, dict) and descriptor.get('platform') == 'macos_arm64', 'native_platform', 409)
    require(bool(release.build.package_path), 'native_program_missing', 409)
    config_bytes = canonical_json(_freeze_config(release))
    root = artifacts_root()
    temp = root / f'{secrets.token_hex(16)}.tmp'
    final = None
    try:
        manifest = assemble_artifact(temp, release, config_bytes)
        verify_artifact_file(temp, manifest, programs_root() / release.build.package_path)
        digest, size = digest_file(temp)
        require(len(digest) == 64, 'artifact_digest')
        final = root / f'{digest}.zip'
        if final.exists():
            stored, stored_size = digest_file(final)
            require(stored == digest and stored_size == size, 'artifact_conflict', 409)
            temp.unlink()
        else:
            os.replace(temp, final)
    finally:
        if temp.exists():
            temp.unlink()
    release.artifact_path = final.name
    release.artifact_digest = digest
    release.artifact_size = size
    release.save(update_fields=['artifact_path', 'artifact_digest', 'artifact_size'])
    if actor is not None:
        Audit.objects.create(study=release.study, actor=actor, action=PUBLISHED_ACTION, target=str(release.id),
                             after={'artifact_digest': digest, 'artifact_size': size, 'platform': manifest['platform'],
                                    'members': len(manifest['members'])})
    return {'path': final.name, 'digest': digest, 'size': size, 'manifest': manifest}


def artifact_file(release):
    """The stored artifact path for an approved release, or ``artifact_unavailable``."""
    require(release.approved and bool(release.artifact_path) and bool(release.artifact_digest), 'artifact_unavailable', 404)
    digest = release.artifact_digest
    require(len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'artifact_unavailable', 404)
    root = artifacts_root()
    path = root / release.artifact_path
    require(path.name == f'{digest}.zip' and path.parent == root, 'artifact_unavailable', 404)
    require(path.is_file(), 'artifact_unavailable', 404)
    return path


def stored_artifact_intact(release):
    """Full byte check of the stored artifact against the database record."""
    try:
        path = artifact_file(release)
    except Rejected:
        return False
    if os.path.getsize(path) != release.artifact_size:
        return False
    digest, size = digest_file(path)
    return digest == release.artifact_digest and size == release.artifact_size


def require_release_artifact(release):
    """Admission gate for a release frozen with a complete artifact.

    A completed native distribution is only admitted while its stored artifact
    is present and byte-intact; a legacy or descriptor-only release has no
    frozen artifact contract and keeps its original admission rules.
    """
    frozen = release.config.get('artifact_format_version') if isinstance(release.config, dict) else None
    if frozen is None:
        return
    require(frozen == ARTIFACT_FORMAT_VERSION, 'unsupported_artifact_version', 409)
    require(bool(release.artifact_path) and bool(release.artifact_digest), 'release_unavailable', 409)
    require(stored_artifact_intact(release), 'release_unavailable', 409)


def read_manifest(release):
    """The validated manifest of one stored artifact, or a fail-closed rejection."""
    path = artifact_file(release)
    require(stored_artifact_intact(release), 'artifact_tampered', 409)
    manifest = load_manifest(_read_member(path, ARTIFACT_MANIFEST))
    require(manifest.get('platform') == 'macos_arm64' and isinstance(manifest.get('app'), str), 'invalid_artifact')
    return manifest, path


def sidecar_members(manifest):
    """Artifact members outside the program bundle; the bundle stays whole."""
    app = manifest.get('app') if isinstance(manifest, dict) else None
    sidecars = {}
    for entry in manifest.get('members', []):
        path = entry.get('path') if isinstance(entry, dict) else None
        if not isinstance(path, str) or path == ARTIFACT_MANIFEST:
            continue
        if isinstance(app, str) and app and (path == app or path.startswith(app + '/')):
            continue
        sidecars[path] = entry
    return sidecars


def sidecar_bytes(release, member):
    """One bounding-checked sidecar member of the stored artifact."""
    manifest, path = read_manifest(release)
    if member == ARTIFACT_MANIFEST:
        raw = _read_member(path, ARTIFACT_MANIFEST)
        return {'path': ARTIFACT_MANIFEST, 'sha256': digest_bytes(raw), 'size': len(raw), 'mode': '0644'}, raw
    entry = sidecar_members(manifest).get(member)
    require(entry is not None, 'artifact_member_unknown', 404)
    raw = _read_member(path, member)
    require(len(raw) == entry['size'] and digest_bytes(raw) == entry['sha256'], 'artifact_tampered', 409)
    return entry, raw
