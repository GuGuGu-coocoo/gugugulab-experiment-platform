"""03D complete native distribution artifacts: bounded intake, frozen assembly,
authorized immutable download and the admission integrity gate.

These tests use a real temporary data directory and the real database: the
program archive is stored on disk, the complete artifact is assembled by the
production code, downloaded through the real admin endpoints and re-read from
disk. Only the synthetic ``.app`` payload is small; no mock replaces storage or
the ORM.
"""
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import uuid
import zipfile
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from core import artifacts, notices, publication
from core.gui import connection_config
from core.models import Audit, Build, Grant, Instance, Release, Session, Study
from core.packages import native_program_valid
from core.protocol import Rejected
from core.services import admit

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


@pytest.fixture
def world(db, tmp_path, settings):
    """Owner, temporary data directory and one study; no release is created yet."""
    settings.DATA_DIR = tmp_path
    owner = get_user_model().objects.create_user('synthetic_owner', password='synthetic-test-password')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='Complete package study', recruitment='open', max_sessions=4)
    Grant.objects.create(user=owner, study=study, action='study.view', delegable=True)
    for action in ('study.configure', 'build.upload', 'build.preview', 'release.approve_pilot'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    client = Client()
    client.force_login(owner)
    return {'owner': owner, 'instance': instance, 'study': study, 'client': client, 'tmp': tmp_path}


def register_and_upload(world, raw=None):
    """Register the descriptor through the GUI, bind the program archive, return the build."""
    raw = raw if raw is not None else valid_archive()[0]
    descriptor = descriptor_for(raw)
    client = world['client']
    url = f'/studies/{world["study"].id}'
    response = client.post(url, {'op': 'native', 'descriptor': json.dumps(descriptor)})
    assert response.status_code == 302
    build = Build.objects.get(study=world['study'])
    upload = io.BytesIO(raw)
    upload.name = 'synthetic.zip'
    response = client.post(url, {'op': 'native_archive', 'build_id': str(build.id), 'package': upload})
    assert response.status_code == 302
    build.refresh_from_db()
    return build, descriptor


def approve(world, build):
    return world['client'].post(f'/studies/{world["study"].id}', {'op': 'approve', 'build_id': str(build.id)})


def body_bytes(response):
    """Full response bytes whether Django streams (FileResponse) or not."""
    if getattr(response, 'streaming', False):
        return b''.join(response.streaming_content)
    return response.content


def admission_request(world, release, operation=None, proof='p' * 48):
    return {'operation_id': operation or str(uuid.uuid4()), 'proof': proof,
            'instance_id': str(world['instance'].instance_id), 'study_id': str(world['study'].id),
            'release_id': str(release.id), 'build_id': str(release.build_id)}


# ------------------------------------------------------------------- intake
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


def test_gui_native_upload_binds_one_descriptor_and_stores_unchanged_bytes(world):
    raw, _descriptor = valid_archive()
    build, descriptor = register_and_upload(world, raw)
    stored = world['tmp'] / 'packages' / build.package_path
    assert stored.read_bytes() == raw and build.package_path == build.digest + '.zip'
    assert Release.objects.filter(study=world['study']).count() == 0

    # A repeated identical upload reuses the same stored bytes.
    before = stored.stat().st_mtime_ns
    upload = io.BytesIO(raw)
    upload.name = 'synthetic.zip'
    response = world['client'].post(f'/studies/{world["study"].id}',
                                    {'op': 'native_archive', 'build_id': str(build.id), 'package': upload})
    assert response.status_code == 302 and stored.stat().st_mtime_ns == before

    # A tampered archive is refused before anything is written or replaced.
    tampered = bytearray(raw)
    tampered[-1] = (tampered[-1] + 1) % 256
    upload = io.BytesIO(bytes(tampered))
    upload.name = 'synthetic.zip'
    response = world['client'].post(f'/studies/{world["study"].id}',
                                    {'op': 'native_archive', 'build_id': str(build.id), 'package': upload})
    assert response.status_code == 422 and response.json()['code'] == 'program_digest_mismatch'
    assert stored.read_bytes() == raw


def test_native_upload_requires_build_upload_scope(world):
    raw, _descriptor = valid_archive()
    build = Build.objects.create(study=world['study'], descriptor=descriptor_for(raw), digest=build_digest(raw), package_path='')
    stranger = get_user_model().objects.create_user('synthetic_stranger', password='synthetic-test-password')
    Grant.objects.create(user=stranger, study=world['study'], action='study.view')
    client = Client()
    client.force_login(stranger)
    upload = io.BytesIO(raw)
    upload.name = 'synthetic.zip'
    response = client.post(f'/studies/{world["study"].id}',
                           {'op': 'native_archive', 'build_id': str(build.id), 'package': upload})
    assert response.status_code == 403
    build.refresh_from_db()
    assert build.package_path == '' and not (world['tmp'] / 'packages').exists()


def build_digest(raw):
    return hashlib.sha256(raw).hexdigest()


# ------------------------------------------------------------ frozen artifact
def test_approval_publishes_unchanged_frozen_complete_package(world):
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    assert release.approved and len(release.artifact_digest) == 64
    assert release.config['artifact_format_version'] == artifacts.ARTIFACT_FORMAT_VERSION

    path = world['tmp'] / 'artifacts' / release.artifact_path
    assert path.name == release.artifact_digest + '.zip'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == release.artifact_digest
    assert path.stat().st_size == release.artifact_size

    with zipfile.ZipFile(path) as package, zipfile.ZipFile(world['tmp'] / 'packages' / build.package_path) as program:
        manifest = artifacts.load_manifest(package.read(artifacts.ARTIFACT_MANIFEST))
        names = {item.filename for item in package.infolist() if not item.filename.endswith('/')}
        assert {entry['path'] for entry in manifest['members']} | {artifacts.ARTIFACT_MANIFEST} == names
        assert manifest['program_sha256'] == build.digest and manifest['app'] == APP
        assert manifest['members'] and manifest['artifact_format_version'] == 'gep-artifact/v1'
        # The manifest never contains the artifact's own digest.
        assert not ({'artifact_digest', 'artifact_sha256', 'self_sha256'} & set(manifest))
        for entry in manifest['members']:
            blob = package.read(entry['path'])
            info = package.getinfo(entry['path'])
            assert hashlib.sha256(blob).hexdigest() == entry['sha256'] and len(blob) == entry['size']
            assert f'{((info.external_attr >> 16) & 0o7777):04o}' == entry['mode']
        # The executable, resources and signature are the uploaded program bytes.
        program_names = {item.filename for item in program.infolist() if not item.filename.endswith('/')}
        assert program_names <= names
        for name in program_names:
            source = program.getinfo(name)
            copied = package.getinfo(name)
            assert program.read(name) == package.read(name)
            assert (source.external_attr >> 16) & 0o7777 == (copied.external_attr >> 16) & 0o7777
        assert (program.getinfo(BINARY).external_attr >> 16) & 0o7777 == 0o755

    # Frozen public configuration, schema, codebook and license travel with it.
    config = json.loads(world['client'].get(f'/releases/{release.id}/artifact/connection.json').content)
    assert config == connection_config(release)
    assert config['mode'] == 'anonymous' and config['shell_capability'] == 'gec-shell/v1'
    assert not ({'password', 'roster', 'token', 'secret'} & set(config))
    descriptor_schemas = build.descriptor['schemas']
    for key in descriptor_schemas:
        member = json.loads(world['client'].get(f'/releases/{release.id}/artifact/schemas/{key}.json').content)
        assert member == descriptor_schemas[key]
    assert json.loads(world['client'].get(f'/releases/{release.id}/artifact/metadata/codebook.json').content) == build.descriptor['codebook']
    assert world['client'].get(f'/releases/{release.id}/artifact/LICENSE').content == (Path('LICENSE')).read_bytes()

    # The complete package is a real, unmodified macOS bundle once unpacked.
    target = world['tmp'] / 'unpacked'
    with zipfile.ZipFile(path) as package:
        for item in package.infolist():
            destination = target / item.filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(package.read(item))
            destination.chmod((item.external_attr >> 16) & 0o7777 or 0o644)
    assert (target / APP / 'Contents' / 'MacOS').is_dir()
    assert oct((target / BINARY).stat().st_mode & 0o777) == '0o755'


def test_artifact_download_immutable_and_reauthorized(world):
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    client = world['client']

    first = client.get(f'/releases/{release.id}/artifact')
    second = client.get(f'/releases/{release.id}/artifact')
    first_bytes, second_bytes = body_bytes(first), body_bytes(second)
    assert first.status_code == 200 and second_bytes == first_bytes
    assert first['X-Artifact-SHA256'] == release.artifact_digest
    assert hashlib.sha256(first_bytes).hexdigest() == release.artifact_digest
    manifest = client.get(f'/releases/{release.id}/artifact/artifact_manifest.json')
    assert manifest.status_code == 200 and artifacts.load_manifest(manifest.content)
    bundled_config = client.get(f'/releases/{release.id}/artifact/connection.json')
    assert bundled_config.status_code == 200 and json.loads(bundled_config.content)['release_id'] == str(release.id)
    # The program bundle itself is only served as the complete artifact.
    assert client.get(f'/releases/{release.id}/artifact/{BINARY}').status_code == 404

    # Revoked build scope: both the artifact and every sidecar are refused.
    Grant.objects.filter(user=world['owner'], study=world['study'], action='build.upload').delete()
    assert client.get(f'/releases/{release.id}/artifact').status_code == 403
    assert client.get(f'/releases/{release.id}/artifact/connection.json').status_code == 403
    Grant.objects.create(user=world['owner'], study=world['study'], action='build.upload')
    assert client.get(f'/releases/{release.id}/artifact').status_code == 200


def test_artifact_download_denied_outside_the_build_scope(world):
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    outsider = get_user_model().objects.create_user('synthetic_outsider', password='synthetic-test-password')
    other_study = Study.objects.create(title='Other package study')
    Grant.objects.create(user=outsider, study=other_study, action='study.view')
    Grant.objects.create(user=outsider, study=other_study, action='build.upload')
    client = Client()
    client.force_login(outsider)
    assert client.get(f'/releases/{release.id}/artifact').status_code == 403
    assert client.get(f'/releases/{release.id}/artifact/artifact_manifest.json').status_code == 403


def test_tampered_or_missing_artifact_fails_before_participation(world):
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    path = world['tmp'] / 'artifacts' / release.artifact_path
    original = path.read_bytes()
    tampered = bytearray(original)
    tampered[10] = (tampered[10] + 1) % 256
    path.write_bytes(bytes(tampered))

    assert world['client'].get(f'/releases/{release.id}/artifact').status_code == 409
    assert world['client'].get(f'/releases/{release.id}/artifact/connection.json').status_code == 409
    with pytest.raises(Rejected, match='release_unavailable'):
        admit(release, admission_request(world, release))
    assert not Session.objects.filter(release=release).exists()
    assert not Session.objects.filter(release__study=world['study']).exists()

    path.unlink()
    assert world['client'].get(f'/releases/{release.id}/artifact').status_code == 404
    with pytest.raises(Rejected, match='release_unavailable'):
        admit(release, admission_request(world, release))
    assert Session.objects.filter(release=release).count() == 0

    path.write_bytes(original)
    session, _token = admit(release, admission_request(world, release))
    assert session.release_id == release.id


def test_interrupted_packaging_never_replaces_the_previous_release(world):
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    first = Release.objects.get(study=world['study'], build=build)
    before = body_bytes(world['client'].get(f'/releases/{first.id}/artifact'))

    # A second immutable build: another program archive with its own descriptor.
    other_raw = archive_bytes(app_members() + [member(f'{APP}/Contents/Resources/extra.txt', 0o644, b'second release')])
    other_descriptor = descriptor_for(other_raw, version='synthetic-2')
    other = Build.objects.create(study=world['study'], descriptor=other_descriptor, digest=build_digest(other_raw), package_path='')
    upload = io.BytesIO(other_raw)
    upload.name = 'synthetic-2.zip'
    assert world['client'].post(f'/studies/{world["study"].id}',
                                {'op': 'native_archive', 'build_id': str(other.id), 'package': upload}).status_code == 302
    other.refresh_from_db()

    # Interruption before the file commit: the assembled program was corrupted
    # on disk, so nothing is published and the previous release stays complete.
    stored_program = world['tmp'] / 'packages' / other.package_path
    original_program = stored_program.read_bytes()
    stored_program.unlink()
    missing = approve(world, other)
    assert missing.status_code == 409 and missing.json()['code'] == 'native_program_missing'
    assert Release.objects.filter(study=world['study']).count() == 1
    stored_program.write_bytes(original_program + b'corruption')
    interrupted = approve(world, other)
    assert interrupted.status_code == 409 and interrupted.json()['code'] == 'program_digest_mismatch'
    assert Release.objects.filter(study=world['study']).count() == 1
    assert body_bytes(world['client'].get(f'/releases/{first.id}/artifact')) == before
    assert not list((world['tmp'] / 'artifacts').glob('*.tmp'))
    stored_program.write_bytes(other_raw)

    # Interruption after the file commit: the process dies before the database
    # transaction commits, so the release row rolls back and the old release is
    # still the only reachable one. The unreferenced file is never served.
    config = {**first.config, 'artifact_format_version': artifacts.ARTIFACT_FORMAT_VERSION}
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            draft = Release.objects.create(study=world['study'], build=other, approved=False, config=config)
            record = artifacts.publish_complete_artifact(draft, actor=world['owner'])
            raise RuntimeError('simulated crash after the artifact file was committed')
    assert not Release.objects.filter(study=world['study'], artifact_digest=record['digest']).exists()
    assert Release.objects.filter(study=world['study']).count() == 1
    assert (world['tmp'] / 'artifacts' / record['path']).is_file()
    assert body_bytes(world['client'].get(f'/releases/{first.id}/artifact')) == before

    # A retry after the interruption publishes normally; the unreferenced file
    # from the crashed attempt stays unreferenced and is never served.
    assert approve(world, other).status_code == 302
    second = Release.objects.get(study=world['study'], build=other)
    assert second.approved and second.artifact_digest != record['digest']
    assert not Release.objects.filter(artifact_digest=record['digest']).exists()
    assert body_bytes(world['client'].get(f'/releases/{first.id}/artifact')) == before
    published = body_bytes(world['client'].get(f'/releases/{second.id}/artifact'))
    assert hashlib.sha256(published).hexdigest() == second.artifact_digest


def test_content_addressed_storage_never_overwrites(world):
    raw, _descriptor = valid_archive()
    path = artifacts.programs_root() / f'{build_digest(raw)}.zip'
    artifacts.store_program_archive(path, raw)
    with pytest.raises(Rejected, match='storage_conflict'):
        artifacts.store_program_archive(path, raw + b'different bytes')
    assert path.read_bytes() == raw


def test_descriptor_only_and_web_releases_keep_the_legacy_contract(world):
    # Descriptor-only native builds stay registrable and approvable for external
    # distribution, with no platform artifact and no admission gate.
    raw, _descriptor = valid_archive()
    descriptor = descriptor_for(raw)
    response = world['client'].post(f'/studies/{world["study"].id}', {'op': 'native', 'descriptor': json.dumps(descriptor)})
    assert response.status_code == 302
    build = Build.objects.get(study=world['study'])
    assert build.package_path == '' and approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    assert release.approved and not release.artifact_digest and not release.artifact_path
    session, _token = admit(release, admission_request(world, release))
    assert session.release_id == release.id
    assert world['client'].get(f'/releases/{release.id}/config').status_code == 200
    assert world['client'].get(f'/releases/{release.id}/artifact').status_code == 404

    # A Web release keeps its original upload/approve/admission path untouched.
    study = Study.objects.create(title='Web compatibility study', recruitment='open')
    web = Build.objects.create(study=study, descriptor={'platform': 'godot_web', 'version': 'web-1'}, digest='c' * 64)
    web_release = Release.objects.create(study=study, build=web, approved=True, config={'purpose': 'synthetic'})
    assert not web_release.artifact_digest
    request = {'operation_id': str(uuid.uuid4()), 'proof': 'q' * 48, 'instance_id': str(world['instance'].instance_id),
               'study_id': str(study.id), 'release_id': str(web_release.id), 'build_id': str(web.id)}
    session, _token = admit(web_release, request)
    assert session.release_id == web_release.id


def test_unknown_artifact_versions_and_self_reference_fail_closed(world):
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    path = world['tmp'] / 'artifacts' / release.artifact_path

    document = json.loads(path and __import__('zipfile').ZipFile(path).read(artifacts.ARTIFACT_MANIFEST))
    document['artifact_format_version'] = 'gep-artifact/v99'
    with pytest.raises(Rejected, match='unsupported_artifact_version'):
        artifacts.load_manifest(json.dumps(document).encode())
    document['artifact_format_version'] = artifacts.ARTIFACT_FORMAT_VERSION
    document['artifact_digest'] = release.artifact_digest
    with pytest.raises(Rejected, match='artifact_self_reference'):
        artifacts.verify_artifact_file(path, document, world['tmp'] / 'packages' / build.package_path)

    # A release frozen with an unknown artifact version is never admitted.
    release.config = {**release.config, 'artifact_format_version': 'gep-artifact/v99'}
    release.save(update_fields=['config'])
    with pytest.raises(Rejected, match='unsupported_artifact_version'):
        admit(release, admission_request(world, release))
    assert Session.objects.filter(release=release).count() == 0


def test_approval_records_the_artifact_in_the_audit_trail(world):
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    entry = Audit.objects.filter(action=artifacts.PUBLISHED_ACTION, target=str(release.id)).first()
    assert entry is not None and entry.actor_id == world['owner'].id
    # Old expectation: 10 members (5 program members + config, 2 schemas, codebook,
    # LICENSE). New contract: the complete package also freezes
    # THIRD_PARTY_NOTICES.txt, so the published artifact has 11 members.
    assert entry.after['artifact_digest'] == release.artifact_digest and entry.after['members'] == 11


# ------------------------------------------------- third-party notices (03D)
def test_complete_package_freezes_and_serves_the_third_party_notices(world):
    """The GEP license is not the whole notice: the bundled engine and addon keep
    their own frozen copyright/license documents, downloadable under the same
    build scope and hashed in the manifest."""
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    client = world['client']

    expected = notices.third_party_notices()
    engine = notices.engine_notices()
    sqlite_license = (Path('third_party') / 'godot_sqlite_license.md').read_bytes()
    project_license = Path('LICENSE').read_bytes()
    assert expected != project_license

    with zipfile.ZipFile(world['tmp'] / 'artifacts' / release.artifact_path) as package:
        manifest = artifacts.load_manifest(package.read(artifacts.ARTIFACT_MANIFEST))
        members = {entry['path']: entry for entry in manifest['members']}
        assert members[notices.THIRD_PARTY_NOTICES_MEMBER]['sha256'] == hashlib.sha256(expected).hexdigest()
        bundled = package.read(notices.THIRD_PARTY_NOTICES_MEMBER)
        assert bundled == expected and package.read('LICENSE') == project_license

    text = bundled.decode('utf-8')
    assert engine['engine_license'].rstrip() in text
    assert engine['engine_version']['string'] in text
    assert sqlite_license.decode('utf-8').rstrip() in text
    for component in ('Godot', 'godot-sqlite', 'SQLite'):
        assert component in text

    response = client.get(f'/releases/{release.id}/artifact/{notices.THIRD_PARTY_NOTICES_MEMBER}')
    assert response.status_code == 200 and response.content == expected
    assert response['X-Artifact-Member-SHA256'] == hashlib.sha256(expected).hexdigest()
    assert client.get(f'/releases/{release.id}/artifact/LICENSE').content == project_license

    Grant.objects.filter(user=world['owner'], study=world['study'], action='build.upload').delete()
    assert client.get(f'/releases/{release.id}/artifact/{notices.THIRD_PARTY_NOTICES_MEMBER}').status_code == 403


def godot_binary():
    """The official local engine; missing tooling fails the test, never skips."""
    candidate = os.environ.get('GEP_GODOT_BIN') or shutil.which('godot')
    if candidate is None and Path('/Applications/Godot.app/Contents/MacOS/Godot').is_file():
        candidate = '/Applications/Godot.app/Contents/MacOS/Godot'
    if candidate is None or not Path(candidate).exists():
        pytest.fail('official local Godot engine not found (GEP_GODOT_BIN, PATH or /Applications/Godot.app)')
    return candidate


def test_frozen_engine_notices_match_the_official_local_engine(tmp_path):
    """The frozen document is the engine's own notice output, not a hand copy.

    The official local engine is re-exported through its own notice interface
    (Engine.get_license_text / get_copyright_info / get_license_info) and the
    result must equal the committed source byte for byte; the same engine
    version the native descriptor contract accepts."""
    script = Path('tests/native/engine_notices_export.gd')
    assert script.is_file()
    export = tmp_path / 'engine_notices.json'
    result = subprocess.run([godot_binary(), '--headless', '--script', str(script)],
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'GEP_NOTICE_OUTPUT': str(export)},
                            capture_output=True, text=True, timeout=180)
    assert export.is_file(), f'engine notice export produced no file: {result.stdout[-500:]}{result.stderr[-500:]}'
    exported = json.loads(export.read_text())
    assert exported == notices.engine_notices()
    assert exported['engine_version']['string'] == '4.7.2-stable (official)'
    assert exported['engine_license'].strip() and exported['third_party_licenses']


# ------------------------------------- platform-aware current release (03D)
def make_current(world, monkeypatch, summary):
    """Public + open study with its complete native release as current release."""
    monkeypatch.setenv('GEP_PUBLIC_API', 'http://experiment.localhost:8123')
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    native = Release.objects.get(study=world['study'], build=build)
    owner, study = world['owner'], world['study']
    publication.update_policy(owner, study, '0', {'public': True, 'public_summary': summary})
    study.refresh_from_db()
    publication.select_current_release(owner, study, str(study.revision), str(native.id))
    study.refresh_from_db()
    assert study.current_release_id == native.id
    return native


def test_native_current_release_never_offers_a_web_start(world, monkeypatch):
    """A complete native current release is presented as a controlled independent
    program; the portal never fabricates a Web path for it, and a real Web
    release on the same study still gets its browser start when it is current."""
    native = make_current(world, monkeypatch, '原生受控分发研究')
    study, client = world['study'], world['client']

    body = client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    assert f'data-study="{study.id}"' in body and 'data-participation="native"' in body
    assert '原生受控分发研究' in body and 'data-native-participation="1"' in body
    assert 'web/index.html' not in body and '/run/' not in body
    assert f'/join/{study.id}' in body

    entry = client.get(f'/join/{study.id}', HTTP_HOST='experiment.localhost').content.decode()
    assert 'data-startable="0"' in entry and 'data-participation="native"' in entry
    assert 'data-native-participation="1"' in entry and 'data-expected-release=""' in entry
    assert '/run/' not in entry and 'web/index.html' not in entry
    # The program archive is never served as a Web application.
    assert client.get(f'/run/{native.id}/web/index.html', HTTP_HOST='experiment.localhost').status_code == 404
    assert Session.objects.filter(release__study=study).count() == 0

    # A Web release on the same study is presentable and switchable; the native
    # release keeps its frozen artifact, download and direct admission.
    web_build = Build.objects.create(study=study, descriptor={'platform': 'godot_web', 'version': 'web-1'},
                                     digest='d' * 64, package_path='web.zip')
    web = Release.objects.create(study=study, build=web_build, approved=True, config={'purpose': 'synthetic'})
    publication.select_current_release(world['owner'], study, str(study.revision), str(web.id))
    study.refresh_from_db()
    body = client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    assert f'data-study="{study.id}"' in body and 'data-participation="web"' in body
    assert 'data-native-participation' not in body
    entry = client.get(f'/join/{study.id}', HTTP_HOST='experiment.localhost').content.decode()
    assert 'data-startable="1"' in entry and 'data-participation="web"' in entry
    assert f'{web.id}/web/index.html' in entry and 'data-native-participation' not in entry
    assert client.get(f'/releases/{native.id}/artifact').status_code == 200
    session, _token = admit(native, admission_request(world, native))
    assert session.release_id == native.id

    # Switching back restores the native presentation through the real portal.
    publication.select_current_release(world['owner'], study, str(study.revision), str(native.id))
    study.refresh_from_db()
    body = client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    entry = client.get(f'/join/{study.id}', HTTP_HOST='experiment.localhost').content.decode()
    assert 'data-participation="native"' in body and 'data-participation="native"' in entry
    assert 'data-startable="0"' in entry and '/run/' not in entry


def test_descriptor_only_native_is_not_a_presentable_current_release(world):
    """Descriptor-only native stays an external distribution record: registrable,
    approvable and directly admissible, but never presented as a runnable Web
    package and never selectable as the portal's current release."""
    raw, _descriptor = valid_archive()
    descriptor = descriptor_for(raw)
    assert world['client'].post(f'/studies/{world["study"].id}',
                                {'op': 'native', 'descriptor': json.dumps(descriptor)}).status_code == 302
    build = Build.objects.get(study=world['study'])
    assert build.package_path == '' and approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)

    with pytest.raises(Rejected, match='release_unavailable'):
        publication.select_current_release(world['owner'], world['study'], '0', str(release.id))
    session, _token = admit(release, admission_request(world, release))
    assert session.release_id == release.id
    assert publication.release_kind(release) is None


def test_missing_or_tampered_native_artifact_is_not_advertised(world, monkeypatch):
    """A current native release whose stored artifact is missing or tampered fails
    closed: the portal hides it and the entry page offers no start or download."""
    native = make_current(world, monkeypatch, '原生完整性研究')
    study, client = world['study'], world['client']
    stored = world['tmp'] / 'artifacts' / native.artifact_path
    original = stored.read_bytes()

    tampered = bytearray(original)
    tampered[11] = (tampered[11] + 1) % 256
    stored.write_bytes(bytes(tampered))
    assert f'data-study="{study.id}"' not in client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    entry = client.get(f'/join/{study.id}', HTTP_HOST='experiment.localhost').content.decode()
    assert 'data-native-unavailable="1"' in entry and 'data-startable="0"' in entry
    assert '/run/' not in entry and 'web/index.html' not in entry

    stored.unlink()
    assert f'data-study="{study.id}"' not in client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    entry = client.get(f'/join/{study.id}', HTTP_HOST='experiment.localhost').content.decode()
    assert 'data-native-unavailable="1"' in entry and '/run/' not in entry
    assert Session.objects.filter(release__study=study).count() == 0

    # Restoring the bytes restores the same presentation; nothing was rewritten.
    stored.write_bytes(original)
    body = client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    entry = client.get(f'/join/{study.id}', HTTP_HOST='experiment.localhost').content.decode()
    assert f'data-study="{study.id}"' in body and 'data-participation="native"' in body
    assert 'data-native-participation="1"' in entry
