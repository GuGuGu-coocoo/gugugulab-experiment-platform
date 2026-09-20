"""Windows x64 complete native distribution (03D): descriptor contract, bounded
Windows archive intake, PE x64 and PCK inspection, frozen artifact assembly,
authorized immutable download and the platform-aware presentation.

The fixtures are synthetic programs shaped like a real Godot Windows x64 export
(one program root with an EXE, a PCK, the SQLite DLL and the packaged GDExtension
manifest); the real cross-built program is verified by
``tools/phase03_verify_windows_package.py``. Storage, database and HTTP paths
are the production ones -- no mock replaces the ORM, the file system or the
artifact assembly.
"""
import hashlib
import io
import json
import os
import stat
import struct
import uuid
import zipfile
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core import artifacts, notices, publication
from core.gui import connection_config
from core.models import Audit, Build, Grant, Instance, Release, Session, Study
from core.packages import HOST_VERSION, descriptor_valid, native_program_valid
from core.protocol import Rejected
from core.services import admit

ROOT_NAME = 'GEP Synthetic Experiment'
ENTRY = 'GEP Synthetic Experiment.exe'
PCK = 'GEP Synthetic Experiment.pck'
DEPENDENCY = 'libgdsqlite.windows.template_release.x86_64.dll'
EXTENSION = 'addons/godot-sqlite/gdsqlite.gdextension'
EXTENSION_TEXT = (
    '[configuration]\nentry_symbol = "sqlite_library_init"\n[libraries]\n'
    f'windows.release.x86_64 = "res://addons/godot-sqlite/bin/{DEPENDENCY}"\n'
)
PCK_HEADER = b'GDPC' + struct.pack('<I', 4) + struct.pack('<III', *HOST_VERSION) + b'\x00' * 16


def pe_image(machine=0x8664, magic=0x20b, dll=False, marker=b'synthetic'):
    """One minimal but structurally real PE image header."""
    offset = 0x40
    stub = bytearray(offset)
    stub[0:2] = b'MZ'
    struct.pack_into('<I', stub, 0x3c, offset)
    optional_size = 0xf0
    optional = bytearray(optional_size)
    struct.pack_into('<H', optional, 0, magic)
    characteristics = 0x0002 | (0x2000 if dll else 0)
    body = b'PE\x00\x00' + struct.pack('<HHIIIHH', machine, 1, 0, 0, 0, optional_size, characteristics)
    return bytes(stub) + body + bytes(optional) + marker * 16


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


def program_members():
    return [
        member(f'{ROOT_NAME}/{ENTRY}', 0o755, pe_image()),
        member(f'{ROOT_NAME}/{PCK}', 0o644, PCK_HEADER + b'payload' * 64),
        member(f'{ROOT_NAME}/{DEPENDENCY}', 0o755, pe_image(dll=True, marker=b'sqlite')),
        member(f'{ROOT_NAME}/{EXTENSION}', 0o644, EXTENSION_TEXT.encode()),
        member(f'{ROOT_NAME}/licenses/binaries_here.txt', 0o644, b'synthetic layout note'),
    ]


def archive_bytes(members, root=True):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        if root:
            output.writestr(*directory(f'{ROOT_NAME}/'))
        for info, data in members:
            output.writestr(info, data)
    return target.getvalue()


def descriptor_document():
    return json.loads(Path('examples/synthetic_experiment/descriptor.json').read_text())


def descriptor_for(raw, **overrides):
    document = descriptor_document()
    document.update({
        'platform': 'windows_x64',
        'program_sha256': hashlib.sha256(raw).hexdigest(),
        'package': {'root': ROOT_NAME, 'entry': ENTRY, 'dependencies': [DEPENDENCY], 'extension': EXTENSION},
        **overrides,
    })
    return document


def valid_archive():
    raw = archive_bytes(program_members())
    return raw, descriptor_for(raw)


def hostile_archive(kind):
    """One archive per rejected Windows intake rule."""
    raw, _descriptor = valid_archive()
    members = program_members()
    if kind == 'traversal':
        members.append(member(f'{ROOT_NAME}/../escape.txt', 0o644, b'x'))
    elif kind == 'absolute':
        members.append(member('/Windows/escape.exe', 0o644, pe_image()))
    elif kind == 'drive':
        members.append(member('C:/escape.exe', 0o644, pe_image()))
    elif kind == 'unc':
        members.append(member(f'//server/{ROOT_NAME}/escape.exe', 0o644, pe_image()))
    elif kind == 'backslash':
        members.append(member(f'{ROOT_NAME}\\alt.exe', 0o644, pe_image()))
    elif kind == 'case_collision':
        members.append(member(f'{ROOT_NAME}/LICENSES/Binaries_Here.txt', 0o644, b'x'))
    elif kind == 'unicode_collision':
        members.append(member(f'{ROOT_NAME}/caf\u00e9.txt', 0o644, b'x'))
        members.append(member(f'{ROOT_NAME}/cafe\u0301.txt', 0o644, b'x'))
    elif kind == 'dot_component':
        members.append(member(f'{ROOT_NAME}/./notes.txt', 0o644, b'x'))
    elif kind == 'illegal_character':
        members.append(member(f'{ROOT_NAME}/notes?.txt', 0o644, b'x'))
    elif kind == 'file_directory_conflict':
        # A file and a directory can never share one normalized path: on Windows
        # ``root/Foo`` and ``root/foo/bar`` are the same name in both roles.
        members.append(member(f'{ROOT_NAME}/Notes.txt', 0o644, b'x'))
        members.append(member(f'{ROOT_NAME}/notes.txt/child.txt', 0o644, b'x'))
    elif kind == 'reserved_name':
        members.append(member(f'{ROOT_NAME}/CON', 0o644, b'x'))
    elif kind == 'reserved_name_extension':
        members.append(member(f'{ROOT_NAME}/nul.txt', 0o644, b'x'))
    elif kind == 'trailing_dot':
        members.append(member(f'{ROOT_NAME}/notes.', 0o644, b'x'))
    elif kind == 'trailing_space':
        members.append(member(f'{ROOT_NAME}/notes ', 0o644, b'x'))
    elif kind == 'ads':
        members.append(member(f'{ROOT_NAME}/notes.txt:stream', 0o644, b'x'))
    elif kind == 'control_character':
        members.append(member(f'{ROOT_NAME}/no\u0007te.txt', 0o644, b'x'))
    elif kind == 'symlink':
        info = zipfile.ZipInfo(f'{ROOT_NAME}/settings.cfg')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        members.append((info, b'../outside.cfg'))
    elif kind == 'special_file':
        info = zipfile.ZipInfo(f'{ROOT_NAME}/pipe')
        info.create_system = 3
        info.external_attr = (stat.S_IFIFO | 0o600) << 16
        members.append((info, b''))
    elif kind == 'script_command':
        members.append(member(f'{ROOT_NAME}/launch.cmd', 0o755, b'@echo synthetic\r\n'))
    elif kind == 'script_powershell':
        members.append(member(f'{ROOT_NAME}/install.ps1', 0o644, b'Write-Host synthetic'))
    elif kind == 'undeclared_executable':
        members.append(member(f'{ROOT_NAME}/helper.exe', 0o755, pe_image(marker=b'helper')))
    elif kind == 'root_file':
        members.append(member('readme.txt', 0o644, b'x'))
    elif kind == 'second_root':
        members.append(member('Other Root/other.exe', 0o755, pe_image()))
    else:
        raise AssertionError(kind)
    return archive_bytes(members)


def broken_archive(kind):
    """One archive per rejected declared-file rule."""
    members = program_members()
    if kind == 'missing_entry':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{ENTRY}']
    elif kind == 'entry_not_pe':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{ENTRY}']
        members.append(member(f'{ROOT_NAME}/{ENTRY}', 0o755, b'synthetic text, not an image'))
    elif kind == 'entry_x86':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{ENTRY}']
        members.append(member(f'{ROOT_NAME}/{ENTRY}', 0o755, pe_image(machine=0x14c, magic=0x10b)))
    elif kind == 'entry_is_dll':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{ENTRY}']
        members.append(member(f'{ROOT_NAME}/{ENTRY}', 0o755, pe_image(dll=True)))
    elif kind == 'dependency_missing':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{DEPENDENCY}']
    elif kind == 'dependency_x86':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{DEPENDENCY}']
        members.append(member(f'{ROOT_NAME}/{DEPENDENCY}', 0o755, pe_image(machine=0x14c, magic=0x10b, dll=True)))
    elif kind == 'dependency_not_dll':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{DEPENDENCY}']
        members.append(member(f'{ROOT_NAME}/{DEPENDENCY}', 0o755, pe_image(dll=False)))
    elif kind == 'missing_pck':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{PCK}']
    elif kind == 'wrong_engine_version':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{PCK}']
        members.append(member(f'{ROOT_NAME}/{PCK}', 0o644, b'GDPC' + struct.pack('<I', 4) + struct.pack('<III', 4, 5, 0)))
    elif kind == 'invalid_pck':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{PCK}']
        members.append(member(f'{ROOT_NAME}/{PCK}', 0o644, b'NOPE' + struct.pack('<I', 4) + struct.pack('<III', 4, 7, 2)))
    elif kind == 'missing_extension':
        members = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{EXTENSION}']
    elif kind == 'extension_unknown_dependency':
        members = extension_archive(members, b'[configuration]\nentry_symbol = "sqlite_library_init"\n[libraries]\n'
                                            b'windows.release.x86_64 = "res://addons/godot-sqlite/bin/other.dll"\n')
    elif kind == 'extension_comment_only':
        # The declared file name appears only in a comment: it must not satisfy
        # the check and no library entry exists at all.
        members = extension_archive(members, b'[configuration]\nentry_symbol = "sqlite_library_init"\n[libraries]\n'
                                            b'; windows.release.x86_64 = "res://addons/godot-sqlite/bin/'
                                            + DEPENDENCY.encode() + b'"\n')
    elif kind == 'extension_other_platform':
        members = extension_archive(members, b'[configuration]\nentry_symbol = "sqlite_library_init"\n[libraries]\n'
                                            b'macos.release = "res://addons/godot-sqlite/bin/'
                                            + DEPENDENCY.encode() + b'"\n')
    elif kind == 'extension_no_entry_symbol':
        members = extension_archive(members, b'[configuration]\ncompatibility_minimum = "4.5"\n[libraries]\n'
                                            b'windows.release.x86_64 = "res://addons/godot-sqlite/bin/'
                                            + DEPENDENCY.encode() + b'"\n')
    elif kind == 'extension_path_escape':
        members = extension_archive(members, b'[configuration]\nentry_symbol = "sqlite_library_init"\n[libraries]\n'
                                            b'windows.release.x86_64 = "res://../' + DEPENDENCY.encode() + b'"\n')
    elif kind == 'extension_illegal_character':
        members = extension_archive(members, b'[configuration]\nentry_symbol = "sqlite_library_init"\n[libraries]\n'
                                            b'windows.release.x86_64 = "res://addons/godot-sqlite/bin/lib?sqlite.dll"\n')
    elif kind == 'extension_unquoted':
        members = extension_archive(members, b'[configuration]\nentry_symbol = "sqlite_library_init"\n[libraries]\n'
                                            b'windows.release.x86_64 = res://addons/godot-sqlite/bin/'
                                            + DEPENDENCY.encode() + b'\n')
    else:
        raise AssertionError(kind)
    return archive_bytes(members)


def extension_archive(members, text):
    """Replace the packaged GDExtension manifest with one synthetic document."""
    replaced = [entry for entry in members if entry[0].filename != f'{ROOT_NAME}/{EXTENSION}']
    replaced.append(member(f'{ROOT_NAME}/{EXTENSION}', 0o644, text))
    return replaced


def frozen_conflicting_archive(extra, root=ROOT_NAME):
    """A valid program root that also carries one member the freeze generates."""
    members = [
        member(f'{root}/{ENTRY}', 0o755, pe_image()),
        member(f'{root}/{PCK}', 0o644, PCK_HEADER + b'payload' * 64),
        member(f'{root}/{DEPENDENCY}', 0o755, pe_image(dll=True, marker=b'sqlite')),
        member(f'{root}/{EXTENSION}', 0o644, EXTENSION_TEXT.encode()),
        *extra,
    ]
    raw = archive_bytes(members, root=False)
    package = {'root': root, 'entry': ENTRY, 'dependencies': [DEPENDENCY], 'extension': EXTENSION}
    return raw, descriptor_for(raw, package=package)


@pytest.fixture
def world(db, tmp_path, settings):
    """Owner, temporary data directory and one study; no release is created yet."""
    settings.DATA_DIR = tmp_path
    owner = get_user_model().objects.create_user('synthetic_owner', password='synthetic-test-password')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='Windows package study', recruitment='open', max_sessions=4)
    Grant.objects.create(user=owner, study=study, action='study.view', delegable=True)
    for action in ('study.configure', 'build.upload', 'build.preview', 'release.approve_pilot'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    client = Client()
    client.force_login(owner)
    return {'owner': owner, 'instance': instance, 'study': study, 'client': client, 'tmp': tmp_path}


def register_and_upload(world, raw=None, descriptor=None):
    """Register the descriptor through the GUI, bind the program archive, return the build."""
    raw = raw if raw is not None else valid_archive()[0]
    descriptor = descriptor if descriptor is not None else descriptor_for(raw)
    client = world['client']
    url = f'/studies/{world["study"].id}'
    response = client.post(url, {'op': 'native', 'descriptor': json.dumps(descriptor)})
    assert response.status_code == 302
    build = Build.objects.get(study=world['study'])
    upload = io.BytesIO(raw)
    upload.name = 'synthetic_windows.zip'
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


# ------------------------------------------------------------ descriptor contract
def test_windows_descriptor_requires_explicit_package_metadata():
    raw, descriptor = valid_archive()
    descriptor_valid(descriptor)
    without = descriptor_for(raw)
    without.pop('package')
    with pytest.raises(Rejected, match='package_metadata'):
        descriptor_valid(without)


@pytest.mark.parametrize('package', [
    {'entry': ENTRY, 'dependencies': [DEPENDENCY], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': ENTRY, 'dependencies': [], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': '../escape.exe', 'dependencies': [DEPENDENCY], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': ENTRY, 'dependencies': ['C:/escape.dll'], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': ENTRY, 'dependencies': [DEPENDENCY, DEPENDENCY], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': ENTRY, 'dependencies': [DEPENDENCY], 'extension': EXTENSION, 'extra': 1},
    {'root': 'nested/root', 'entry': ENTRY, 'dependencies': [DEPENDENCY], 'extension': EXTENSION},
    {'root': 'nul', 'entry': ENTRY, 'dependencies': [DEPENDENCY], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': ENTRY, 'dependencies': [DEPENDENCY], 'extension': EXTENSION, 'entry_2': 1, 'root_2': 1},
    # Declared paths go through the same raw rules as the ZIP members: a dot or
    # empty component must not be normalized away, and the characters NTFS
    # rejects, reserved device names and the generated config path are refused.
    {'root': ROOT_NAME, 'entry': './GEP Synthetic Experiment.exe', 'dependencies': [DEPENDENCY], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': 'bin/./program.exe', 'dependencies': [DEPENDENCY], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': ENTRY, 'dependencies': ['lib?sqlite.dll'], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': ENTRY, 'dependencies': ['nul.dll'], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': ENTRY, 'dependencies': [DEPENDENCY], 'extension': 'addons/<sqlite>.gdextension'},
    {'root': ROOT_NAME, 'entry': 'connection.json', 'dependencies': [DEPENDENCY], 'extension': EXTENSION},
    {'root': ROOT_NAME, 'entry': 'bin/program.exe', 'dependencies': ['bin/connection.json'], 'extension': EXTENSION},
])
def test_windows_descriptor_rejects_invalid_package_metadata(package):
    raw, descriptor = valid_archive()
    descriptor['package'] = package
    with pytest.raises(Rejected, match='package_metadata'):
        descriptor_valid(descriptor)


def test_windows_descriptor_accepts_a_declared_subdirectory_entry():
    """A narrower layout stays valid: an entry in a subdirectory and its
    dependencies declared relative to the program root, as long as no declared
    path names the generated configuration member."""
    raw, descriptor = valid_archive()
    descriptor['package'] = {'root': ROOT_NAME, 'entry': 'bin/GEP Synthetic Experiment.exe',
                             'dependencies': ['bin/libgdsqlite.windows.template_release.x86_64.dll'],
                             'extension': 'addons/godot-sqlite/gdsqlite.gdextension'}
    descriptor_valid(descriptor)


def test_unknown_platform_and_legacy_descriptors_fail_closed():
    raw, descriptor = valid_archive()
    descriptor['platform'] = 'linux_x64'
    with pytest.raises(Rejected, match='unsupported_platform'):
        descriptor_valid(descriptor)
    with pytest.raises(Rejected, match='native_platform'):
        native_program_valid(raw, descriptor)

    # The historical macOS descriptor (no package metadata) still validates, and a
    # Web manifest is unaffected by the Windows contract.
    legacy = descriptor_for(raw)
    legacy['platform'] = 'macos_arm64'
    legacy.pop('package')
    descriptor_valid(legacy)
    web = descriptor_document()
    descriptor_valid(web)


# ------------------------------------------------------------------- intake
def test_windows_program_accepts_declared_program_root():
    raw, descriptor = valid_archive()
    summary = native_program_valid(raw, descriptor)
    assert summary['platform'] == 'windows_x64' and summary['app'] == ROOT_NAME
    assert summary['entry'] == f'{ROOT_NAME}/{ENTRY}'
    assert summary['dependencies'] == [f'{ROOT_NAME}/{DEPENDENCY}']
    assert summary['pcks'] == [f'{ROOT_NAME}/{PCK}']
    assert summary['digest'] == descriptor['program_sha256']


def test_windows_program_accepts_a_real_exported_layout_without_directory_entries():
    raw = archive_bytes(program_members(), root=False)
    descriptor = descriptor_for(raw)
    summary = native_program_valid(raw, descriptor)
    assert summary['app'] == ROOT_NAME and summary['members'] == 5


@pytest.mark.parametrize('kind,code', [
    ('traversal', 'unsafe_path'), ('absolute', 'unsafe_path'), ('drive', 'unsafe_path'),
    ('unc', 'unsafe_path'), ('backslash', 'unsafe_path'), ('case_collision', 'duplicate_path'),
    ('unicode_collision', 'duplicate_path'), ('dot_component', 'unsafe_path'),
    ('illegal_character', 'unsafe_path'), ('file_directory_conflict', 'duplicate_path'),
    ('reserved_name', 'unsafe_path'),
    ('reserved_name_extension', 'unsafe_path'), ('trailing_dot', 'unsafe_path'),
    ('trailing_space', 'unsafe_path'), ('ads', 'unsafe_path'), ('control_character', 'unsafe_path'),
    ('symlink', 'non_regular_file'), ('special_file', 'non_regular_file'),
    ('script_command', 'unsafe_script'), ('script_powershell', 'unsafe_script'),
    ('undeclared_executable', 'undeclared_executable'), ('root_file', 'reserved_path'),
    ('second_root', 'multiple_bundles'),
])
def test_windows_program_rejects_hostile_archive(kind, code):
    hostile = hostile_archive(kind)
    with pytest.raises(Rejected, match=code):
        native_program_valid(hostile, descriptor_for(hostile))


@pytest.mark.parametrize('kind,code', [
    ('missing_entry', 'missing_entry'), ('entry_not_pe', 'invalid_image'),
    ('entry_x86', 'wrong_architecture'), ('entry_is_dll', 'invalid_entry'),
    ('dependency_missing', 'missing_dependencies'), ('dependency_x86', 'wrong_architecture'),
    ('dependency_not_dll', 'invalid_dependency'), ('missing_pck', 'missing_pck'),
    ('wrong_engine_version', 'engine_version_mismatch'), ('invalid_pck', 'invalid_pck'),
    ('missing_extension', 'missing_extension'), ('extension_unknown_dependency', 'extension_dependency_missing'),
    ('extension_comment_only', 'extension_library_missing'),
    ('extension_other_platform', 'extension_library_missing'),
    ('extension_no_entry_symbol', 'extension_entry_symbol'),
    ('extension_path_escape', 'unsafe_path'),
    ('extension_illegal_character', 'unsafe_path'),
    ('extension_unquoted', 'extension_library_missing'),
])
def test_windows_program_rejects_broken_declarations(kind, code):
    broken = broken_archive(kind)
    with pytest.raises(Rejected, match=code):
        native_program_valid(broken, descriptor_for(broken))


@pytest.mark.parametrize('root,extra', [
    (ROOT_NAME, [f'{ROOT_NAME}/connection.json']),
    (ROOT_NAME, [f'{ROOT_NAME}/connection.json/nested.txt']),
    ('metadata', ['metadata/codebook.json']),
    ('schemas', ['schemas/exp.rt.json']),
    ('LICENSE', ['LICENSE/program.txt']),
])
def test_windows_program_rejects_members_the_freeze_would_generate(root, extra):
    """A program that already carries a generated member is refused at intake so
    the freeze can never replace program bytes with the configuration, schema,
    codebook, license or notices."""
    members = [member(name, 0o644, b'synthetic program bytes') for name in extra]
    raw, descriptor = frozen_conflicting_archive(members, root=root)
    with pytest.raises(Rejected, match='frozen_path_conflict'):
        native_program_valid(raw, descriptor)


def test_windows_program_rejects_undigested_bytes_and_bounds(monkeypatch):
    raw, descriptor = valid_archive()
    other = descriptor_for(archive_bytes(program_members() + [member(f'{ROOT_NAME}/extra.txt', 0o644, b'y')]))
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
    monkeypatch.undo()
    monkeypatch.setattr(packages, 'MAX_NATIVE_FILES', 1)
    with pytest.raises(Rejected, match='file_count'):
        native_program_valid(raw, descriptor)


def test_macos_program_requires_a_real_dependency_not_an_absent_directory():
    """A macOS bundle without any native dependency is refused: deleting the
    entire Frameworks directory must not evade the check."""
    import test_phase03_packages as mac_fixture

    without_frameworks = [entry for entry in mac_fixture.app_members() if entry[0].filename != mac_fixture.FRAMEWORK]
    raw = mac_fixture.archive_bytes(without_frameworks)
    with pytest.raises(Rejected, match='missing_dependencies'):
        native_program_valid(raw, mac_fixture.descriptor_for(raw))

    with pytest.raises(Rejected, match='missing_dependencies'):
        empty = mac_fixture.archive_bytes(without_frameworks + [directory(f'{mac_fixture.APP}/Contents/Frameworks/')])
        native_program_valid(empty, mac_fixture.descriptor_for(empty))


def test_windows_conflicting_program_is_refused_before_the_freeze_writes(world):
    """The GUI refuses the program archive that already carries the generated
    configuration: nothing is stored, no release exists, and no program byte is
    ever replaced by a frozen member."""
    members = program_members() + [member(f'{ROOT_NAME}/connection.json', 0o644, b'{"program":"own"}')]
    raw = archive_bytes(members)
    descriptor = descriptor_for(raw)
    url = f'/studies/{world["study"].id}'
    assert world['client'].post(url, {'op': 'native', 'descriptor': json.dumps(descriptor)}).status_code == 302
    build = Build.objects.get(study=world['study'])
    upload = io.BytesIO(raw)
    upload.name = 'synthetic_windows_conflict.zip'
    response = world['client'].post(url, {'op': 'native_archive', 'build_id': str(build.id), 'package': upload})
    assert response.status_code == 422 and response.json()['code'] == 'frozen_path_conflict'
    build.refresh_from_db()
    assert build.package_path == '' and not (world['tmp'] / 'packages').exists()
    assert not Release.objects.filter(study=world['study']).exists()
    assert not (world['tmp'] / 'artifacts').exists()


def test_windows_approval_refuses_a_conflicting_stored_program(world):
    """A stored program that somehow bypassed intake is still refused at freeze
    time, before any generated member could replace its bytes."""
    raw, descriptor = frozen_conflicting_archive([member(f'{ROOT_NAME}/connection.json', 0o644, b'{"program":"own"}')])
    build = Build.objects.create(study=world['study'], descriptor=descriptor,
                                 digest=hashlib.sha256(raw).hexdigest(), package_path='')
    stored = artifacts.programs_root() / (build.digest + '.zip')
    artifacts.store_program_archive(stored, raw)
    build.package_path = stored.name
    build.save(update_fields=['package_path'])
    response = approve(world, build)
    assert response.status_code == 409 and response.json()['code'] == 'frozen_path_conflict'
    assert Release.objects.filter(study=world['study']).count() == 0
    assert stored.read_bytes() == raw
    artifacts_dir = world['tmp'] / 'artifacts'
    assert not (artifacts_dir.exists() and list(artifacts_dir.iterdir()))


# ------------------------------------------------------------ frozen artifact
def test_windows_approval_freezes_program_config_and_metadata(world):
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    assert release.approved and len(release.artifact_digest) == 64
    assert release.config['artifact_format_version'] == artifacts.ARTIFACT_FORMAT_VERSION

    path = world['tmp'] / 'artifacts' / release.artifact_path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == release.artifact_digest
    with zipfile.ZipFile(path) as package, zipfile.ZipFile(world['tmp'] / 'packages' / build.package_path) as program:
        manifest = artifacts.load_manifest(package.read(artifacts.ARTIFACT_MANIFEST))
        names = {item.filename for item in package.infolist() if not item.filename.endswith('/')}
        assert {entry['path'] for entry in manifest['members']} | {artifacts.ARTIFACT_MANIFEST} == names
        assert manifest['platform'] == 'windows_x64' and manifest['app'] == ROOT_NAME
        assert manifest['entry'] == f'{ROOT_NAME}/{ENTRY}'
        assert manifest['dependencies'] == [f'{ROOT_NAME}/{DEPENDENCY}']
        assert manifest['extension'] == f'{ROOT_NAME}/{EXTENSION}'
        assert manifest['config_member'] == f'{ROOT_NAME}/connection.json'

        # Every member hash matches, and every program byte/mode is unchanged.
        for entry in manifest['members']:
            blob = package.read(entry['path'])
            info = package.getinfo(entry['path'])
            assert hashlib.sha256(blob).hexdigest() == entry['sha256'] and len(blob) == entry['size']
            assert f'{((info.external_attr >> 16) & 0o7777):04o}' == entry['mode']
        program_names = {item.filename for item in program.infolist() if not item.filename.endswith('/')}
        assert program_names <= names
        for name in program_names:
            assert program.read(name) == package.read(name)

        # The frozen configuration sits next to the executable inside the program
        # root, and the metadata members are the frozen public documents.
        bundled_config = json.loads(package.read(manifest['config_member']))
        assert bundled_config == connection_config(release)
        assert bundled_config['mode'] == 'anonymous' and bundled_config['shell_capability'] == 'gec-shell/v1'
        assert not ({'password', 'participants', 'roster', 'token', 'secret'} & set(bundled_config))
        assert manifest['config_sha256'] == hashlib.sha256(package.read(manifest['config_member'])).hexdigest()
        for key, definition in build.descriptor['schemas'].items():
            assert json.loads(package.read(f'schemas/{key}.json')) == definition
        assert json.loads(package.read('metadata/codebook.json')) == build.descriptor['codebook']
        assert package.read('LICENSE') == Path('LICENSE').read_bytes()
        assert package.read(notices.THIRD_PARTY_NOTICES_MEMBER) == notices.third_party_notices()
        assert not ({'artifact_digest', 'artifact_sha256', 'self_sha256'} & set(manifest))


def test_windows_artifact_download_sidecars_and_permissions(world):
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

    # The frozen configuration is served as a sidecar at its real member path; the
    # program bytes themselves are never served individually.
    config = client.get(f'/releases/{release.id}/artifact/{ROOT_NAME}/connection.json')
    assert config.status_code == 200 and json.loads(config.content)['release_id'] == str(release.id)
    assert client.get(f'/releases/{release.id}/artifact/{ROOT_NAME}/{ENTRY}').status_code == 404
    assert client.get(f'/releases/{release.id}/artifact/{ROOT_NAME}/{DEPENDENCY}').status_code == 404
    manifest = client.get(f'/releases/{release.id}/artifact/artifact_manifest.json')
    assert manifest.status_code == 200 and artifacts.load_manifest(manifest.content)['platform'] == 'windows_x64'

    Grant.objects.filter(user=world['owner'], study=world['study'], action='build.upload').delete()
    assert client.get(f'/releases/{release.id}/artifact').status_code == 403
    assert client.get(f'/releases/{release.id}/artifact/{ROOT_NAME}/connection.json').status_code == 403
    Grant.objects.create(user=world['owner'], study=world['study'], action='build.upload')
    assert client.get(f'/releases/{release.id}/artifact').status_code == 200


@pytest.mark.parametrize('mode', ['anonymous', 'id', 'password'])
def test_windows_release_freezes_each_mode_without_credentials(world, mode):
    """The same task build serves all three frozen participation modes: only the
    public configuration differs and it never carries a password or roster."""
    world['study'].mode = mode
    world['study'].save()
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    with zipfile.ZipFile(world['tmp'] / 'artifacts' / release.artifact_path) as package:
        manifest = artifacts.load_manifest(package.read(artifacts.ARTIFACT_MANIFEST))
        config = json.loads(package.read(manifest['config_member']))
    assert config['mode'] == mode and config['purpose'] == 'synthetic'
    assert not ({'password', 'participants', 'roster', 'token', 'secret'} & set(config))
    assert build.digest == release.build.digest


def test_windows_tamper_rollback_and_old_release_binding(world):
    """Tamper and missing-dependency refusals never replace an older reachable
    release, and the old artifact bytes keep their original binding."""
    first_raw, _descriptor = valid_archive()
    first_build, _descriptor = register_and_upload(world, first_raw)
    assert approve(world, first_build).status_code == 302
    first = Release.objects.get(study=world['study'], build=first_build)
    original = body_bytes(world['client'].get(f'/releases/{first.id}/artifact'))

    # A second immutable build whose descriptor declares a dependency the stored
    # program does not contain is refused before anything is published; the
    # previous release stays complete and reachable.
    second_raw = archive_bytes(program_members() + [member(f'{ROOT_NAME}/extra-2.txt', 0o644, b'second')])
    second_descriptor = descriptor_for(second_raw)
    other = Build.objects.create(study=world['study'], descriptor=second_descriptor,
                                 digest=hashlib.sha256(second_raw).hexdigest(), package_path='')
    upload = io.BytesIO(second_raw)
    upload.name = 'synthetic_windows_2.zip'
    assert world['client'].post(f'/studies/{world["study"].id}',
                                {'op': 'native_archive', 'build_id': str(other.id), 'package': upload}).status_code == 302
    other.refresh_from_db()
    forged = {**other.descriptor, 'package': {**other.descriptor['package'], 'dependencies': ['missing.dll']}}
    other.descriptor = forged
    other.save(update_fields=['descriptor'])
    missing = approve(world, other)
    assert missing.status_code == 409 and missing.json()['code'] == 'missing_dependencies'
    assert Release.objects.filter(study=world['study']).count() == 1
    other.refresh_from_db()
    assert not other.package_path.endswith('.tmp') and (world['tmp'] / 'packages' / other.package_path).is_file()

    # Tampering the stored complete package refuses download, sidecars and
    # admission without creating a session, and restoring restores service.
    path = world['tmp'] / 'artifacts' / first.artifact_path
    original_bytes = path.read_bytes()
    tampered = bytearray(original_bytes)
    tampered[len(tampered) // 2] = (tampered[len(tampered) // 2] + 1) % 256
    path.write_bytes(bytes(tampered))
    assert world['client'].get(f'/releases/{first.id}/artifact').status_code == 409
    assert world['client'].get(f'/releases/{first.id}/artifact/{ROOT_NAME}/connection.json').status_code == 409
    with pytest.raises(Rejected, match='release_unavailable'):
        admit(first, admission_request(world, first))
    assert not Session.objects.filter(release=first).exists()
    path.write_bytes(original_bytes)
    assert body_bytes(world['client'].get(f'/releases/{first.id}/artifact')) == original

    # A failed second packaging never rewrote the old release row or bytes.
    first.refresh_from_db()
    assert first.artifact_digest == hashlib.sha256(original_bytes).hexdigest()
    assert first.artifact_path and first.approved
    assert first.build.package_path == first_build.package_path


def test_windows_approval_records_the_artifact_in_the_audit_trail(world):
    raw, _descriptor = valid_archive()
    build, _descriptor = register_and_upload(world, raw)
    assert approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    entry = Audit.objects.filter(action=artifacts.PUBLISHED_ACTION, target=str(release.id)).first()
    assert entry is not None and entry.after['platform'] == 'windows_x64'
    assert entry.after['artifact_digest'] == release.artifact_digest
    # Five program members (entry, PCK, dependency, extension manifest, extra
    # resource), the frozen config, the schemas, the codebook, LICENSE and the
    # third-party notices.
    assert entry.after['members'] == 5 + 1 + len(release.build.descriptor['schemas']) + 1 + 1 + 1


# ------------------------------------------- presentation fail-closed
def make_current(world, monkeypatch, descriptor, raw, summary):
    """Public + open study with the complete windows release as current release."""
    monkeypatch.setenv('GEP_PUBLIC_API', 'http://experiment.localhost:8123')
    build, _descriptor = register_and_upload(world, raw, descriptor)
    assert approve(world, build).status_code == 302
    native = Release.objects.get(study=world['study'], build=build)
    owner, study = world['owner'], world['study']
    publication.update_policy(owner, study, '0', {'public': True, 'public_summary': summary})
    study.refresh_from_db()
    publication.select_current_release(owner, study, str(study.revision), str(native.id))
    study.refresh_from_db()
    assert study.current_release_id == native.id
    return native


def test_windows_current_release_never_falls_back_to_a_web_start(world, monkeypatch):
    raw, descriptor = valid_archive()
    native = make_current(world, monkeypatch, descriptor, raw, 'Windows 完整原生研究')
    study, client = world['study'], world['client']
    assert publication.release_kind(native) == 'native'

    body = client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    assert f'data-study="{study.id}"' in body and 'data-participation="native"' in body
    assert 'Windows 完整原生研究' in body and 'data-native-participation="1"' in body
    assert '/run/' not in body and 'web/index.html' not in body

    entry = client.get(f'/join/{study.id}', HTTP_HOST='experiment.localhost').content.decode()
    assert 'data-startable="0"' in entry and 'data-participation="native"' in entry
    assert 'data-native-participation="1"' in entry
    assert '/run/' not in entry and 'web/index.html' not in entry
    assert client.get(f'/run/{native.id}/web/index.html', HTTP_HOST='experiment.localhost').status_code == 404
    assert Session.objects.filter(release__study=study).count() == 0

    # A tampered stored package is reported as unavailable, never as a Web start.
    stored = world['tmp'] / 'artifacts' / native.artifact_path
    original = stored.read_bytes()
    tampered = bytearray(original)
    tampered[11] = (tampered[11] + 1) % 256
    stored.write_bytes(bytes(tampered))
    entry = client.get(f'/join/{study.id}', HTTP_HOST='experiment.localhost').content.decode()
    assert 'data-native-unavailable="1"' in entry and 'data-startable="0"' in entry
    assert '/run/' not in entry and 'web/index.html' not in entry
    stored.write_bytes(original)
    assert publication.release_available(native)
    session, _token = admit(native, admission_request(world, native))
    assert session.release_id == native.id


def test_unknown_platform_builds_are_never_presentable(world):
    """A build with an unregistered platform cannot be registered, and a release
    row that somehow carries one is never selectable as a Web current release."""
    raw, descriptor = valid_archive()
    descriptor['platform'] = 'linux_x64'
    response = world['client'].post(f'/studies/{world["study"].id}', {'op': 'native', 'descriptor': json.dumps(descriptor)})
    assert response.status_code == 422 and response.json()['code'] == 'unsupported_platform'
    assert not Build.objects.filter(study=world['study']).exists()

    build = Build.objects.create(study=world['study'], descriptor={'platform': 'linux_x64', 'version': 'v1'},
                                 digest='e' * 64, package_path='linux.zip')
    release = Release.objects.create(study=world['study'], build=build, approved=True, config={'purpose': 'synthetic'})
    assert publication.release_kind(release) == 'web'
    assert publication.entry_state(world['study'])['kind'] == ''


def test_windows_descriptor_only_release_keeps_the_legacy_contract(world):
    raw, descriptor = valid_archive()
    response = world['client'].post(f'/studies/{world["study"].id}', {'op': 'native', 'descriptor': json.dumps(descriptor)})
    assert response.status_code == 302
    build = Build.objects.get(study=world['study'])
    assert build.package_path == '' and approve(world, build).status_code == 302
    release = Release.objects.get(study=world['study'], build=build)
    assert release.approved and not release.artifact_digest and not release.artifact_path
    assert publication.release_kind(release) is None
    with pytest.raises(Rejected, match='release_unavailable'):
        publication.select_current_release(world['owner'], world['study'], '0', str(release.id))
    session, _token = admit(release, admission_request(world, release))
    assert session.release_id == release.id
    assert world['client'].get(f'/releases/{release.id}/artifact').status_code == 404
