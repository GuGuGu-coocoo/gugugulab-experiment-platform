#!/usr/bin/env python3
"""R11 Windows engineering kit and human-test package (prepare) plus the real
Windows run gate (verify).

``--prepare`` runs on the preparation host (this macOS machine) and never
claims a Windows result:

1. freezes a *new* Windows x64 program archive from the current sources: the
   pinned official Godot 4.7.2 Windows release template is verified against its
   recorded digest, the program is exported into a brand-new frozen build root
   under ``build/phase03_remediation_20260923/<unique>/`` (the old
   ``build/windows`` bytes are never rewritten), and the archive is packaged
   exactly like the project's own packager (executable, PCK, native SQLite
   library and the GDExtension manifest) with the program digest covering the
   archive bytes only;
2. starts one new isolated synthetic instance (own data directory, database,
   secret, instance id and free loopback port) and drives the real researcher
   lifecycle over authenticated HTTP: one study per participation mode, the
   real descriptor registration and program upload, the real freeze/approve,
   publication and current-release selection, real roster imports and real
   participant accounts, and one scoped member through the real invitation
   flow;
3. downloads every frozen complete package and sidecar through the real
   authenticated release endpoints - never from a locally re-zipped copy - and
   binds every byte to the recorded release digest;
4. records the platform-side WN06 contracts against the real stored artifact
   and uploads (tamper, missing dependency, wrong platform, missing download
   authority), restoring the stored bytes afterwards;
5. writes the public ``kit/`` (delivery packages, sidecars, relocatable
   operator launchers and the harness), the private ``private/`` material
   (accounts and credentials, mode 0600, never inside the kit) and the
   ``human/`` human-test package (the frozen delivery bytes plus the
   human-testing instructions; no credentials);
6. writes ``prepare_report.json`` with ``windows_verified: false``: the new
   Windows bytes still need the real Windows x64 run (``--verify``) in R11W.

``--verify`` runs on the real Windows x64 host against a prepared kit. It
refuses to run anywhere else, refuses an existing run root, re-validates the
kit integrity list and the private-account separation, runs the kit's own
harness ``--run`` into a brand-new run root, and only reports success when the
harness document proves every WN01-WN06 case passed with no failure. A
preparation is never a run and a run is never a human pass.

Every run creates a brand-new unique evidence root and never overwrites or
cleans an existing one. Windows connection values are read from the runtime
environment only (``GEP_TEST_SSH_HOST``, ``GEP_TEST_SSH_HOST_KEY_ALIAS``,
``GEP_TEST_WINDOWS_ROOT``); no default host, alias or Windows directory is
assumed, and the user's ssh configuration is never read or changed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote as urlquote

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from roster_import_client import preview_and_commit, roster_csv  # noqa: E402  (tools/ path above)
from windows_template import TemplateError, find_windows_template, verify_windows_template  # noqa: E402

EVIDENCE_BASE = ROOT / 'local_data' / 'phase03_remediation_20260923' / 'p03r11a_windows'
BUILD_BASE = ROOT / 'build' / 'phase03_remediation_20260923'
PROJECT = ROOT / 'examples' / 'synthetic_experiment'
PROJECT_DESCRIPTOR = PROJECT / 'descriptor.json'
PROGRAM_ROOT = 'GEP Synthetic Experiment'
PROGRAM_FILES = ('GEP Synthetic Experiment.exe', 'GEP Synthetic Experiment.pck',
                 'libgdsqlite.windows.template_release.x86_64.dll')
WINDOWS_EXTENSION = ('addons/godot-sqlite/gdsqlite.gdextension',
                     PROJECT / 'addons' / 'godot-sqlite' / 'gdsqlite.gdextension')
VENV_PYTHON = ROOT / '.venv' / 'bin' / 'python'
GUNICORN = ROOT / '.venv' / 'bin' / 'gunicorn'
HARNESS = ROOT / 'tools' / 'windows_native_harness.py'
KIT_HARNESS = 'operator/harness/windows_native_harness.py'
# The kit/harness document formats are the shared contract of
# tools/windows_native_harness.py; the new preparation is identified by the
# freshly frozen bytes, the current-source digest and the unique evidence root,
# never by inventing an incompatible format string.
KIT_VERSION = 'gep-windows-kit/v2'
PREPARED_BY = 'tools/remediation_windows.py'
PREPARED_FOR = 'R11A'
HUMAN_FORMAT = 'gep-human-test-package/v1'
RUNTIME_FORMAT = 'gep-windows-runtime/v1'
# The frozen Windows verification report produced by ``--verify`` on the device
# (or by the Mac orchestration) and re-read by the aggregate gate. The raw
# harness document keeps its own shared format (tools/windows_native_harness.py);
# this report is only the envelope that names the exact kit, program and run.
VERIFY_FORMAT = 'gep-windows-verify/v1'
RUN_FORMAT = 'gep-windows-run/v1'
WINDOWS_CASES = ('WN01', 'WN02', 'WN03', 'WN04', 'WN05', 'WN06')
# The per-event reconciliation artifacts a real run must retain: the harness
# exports named below are compared against the local store by the harness
# itself, and the run gate re-checks that every recorded path is a real file
# inside the run root with the recorded digest.
RECONCILIATION_KEYS = ('export_path', 'export')
ACCOUNTS_FORMAT = 'gep-windows-accounts/v1'
OWNER_FORMAT = 'gep-windows-owner/v1'
MODES = ('anonymous', 'id', 'password')
MODE_LABELS = {'anonymous': '无需 ID', 'id': '预发名单 ID', 'password': 'ID+密码'}
MEMBER_ACTIONS = ('study.view', 'session.recover', 'data.export_raw')
OWNER_USERNAME = 'synthetic_owner'
MEMBER_USERNAME = 'wn_remediation_member'
ID_CODES = ('001', '002')
ARTIFACT_FORMAT = 'gep-artifact/v1'
SSH_HOST_ENV = 'GEP_TEST_SSH_HOST'
SSH_HOST_KEY_ALIAS_ENV = 'GEP_TEST_SSH_HOST_KEY_ALIAS'
WINDOWS_ROOT_ENV = 'GEP_TEST_WINDOWS_ROOT'
WINDOWS_PYTHON_ENV = 'GEP_TEST_WINDOWS_PYTHON'
# Strict SSH arguments for the Mac -> Windows orchestration. Supplied values are
# always argv elements, never a shell string; the global ssh configuration,
# trust store and firewall are never changed.
SSH_OPTIONS = ('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes')
# Every kit/human member manifest must declare at least these members; an empty
# or shrunken manifest is refused instead of passing a zero-iteration loop.
KIT_REQUIRED_MEMBERS = ('README.md', 'releases.json', 'operator/runtime.json',
                        'operator/harness/windows_native_harness.py')
HUMAN_REQUIRED_MEMBERS = ('README.md',)
_MEMBER_SHA256 = re.compile(r'[0-9a-f]{64}')

_SECRET_PATTERNS = (
    re.compile(rb'BEGIN (RSA|OPENSSH|EC|PRIVATE) PRIVATE KEY'),
    re.compile(rb'password\s*[:=]\s*["\'][^"\'\s]{4,}', re.IGNORECASE),
    re.compile(rb'token\s*[:=]\s*["\'][A-Za-z0-9_\-]{12,}', re.IGNORECASE),
    re.compile(rb'secret\s*[:=]\s*["\'][A-Za-z0-9_\-]{8,}', re.IGNORECASE),
)


class KitError(Exception):
    """The preparation or verification itself is defective."""


# --- guards and generic helpers --------------------------------------------

def utc_stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def guard_evidence_root(explicit):
    """A brand-new unique evidence root inside the project; refusal is a stop.

    Every component from the project root down is checked for links before
    anything is created and an existing path is refused: a failed attempt keeps
    its files and a retry must use a new root.
    """
    root = Path(explicit).expanduser()
    if not root.is_absolute():
        root = ROOT / root
    root = Path(os.path.abspath(root))
    project = Path(os.path.abspath(ROOT))
    try:
        relative = root.relative_to(project)
    except ValueError:
        raise KitError(f'refusing an evidence root outside the project: {root}') from None
    current = project
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise KitError(f'refusing a symlinked evidence path component: {current}')
    if root.exists():
        raise KitError(f'refusing an existing evidence root; each run needs a new unique root: {root}')
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    return root


def new_unique_root(base):
    """One fresh ``<UTC stamp>-<random>`` root under ``base``."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    for _ in range(20):
        root = base / f'{utc_stamp()}-{secrets.token_hex(4)}'
        try:
            root.mkdir(mode=0o700, exist_ok=False)
            return root
        except FileExistsError:
            continue
    raise KitError(f'could not create a unique evidence root under {base}')


def guard_unique_child(base, name):
    """A fresh child directory under an evidence root; an existing one is refused."""
    base = Path(base).resolve()
    target = base / name
    if target.exists() or target.is_symlink():
        raise KitError(f'refusing an existing path inside the evidence root: {target}')
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    return target


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_json(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def unsafe_member_name(name):
    """One reason when a manifest member name is not a safe relative path.

    Absolute names, drive letters, backslashes, ``.``/``..`` components and
    blank names are refused before any file is opened, so a traversal or an
    absolute path can never escape the frozen root.
    """
    if not isinstance(name, str) or not name or name != name.strip():
        return 'blank or padded member name'
    if '\\' in name or name.startswith('/') or re.match(r'^[A-Za-z]:', name):
        return 'absolute or foreign-separator member name'
    parts = PurePosixPath(name).parts
    if not parts or any(part in ('', '.', '..') for part in parts):
        return 'traversing member name'
    return None


def verify_member_manifest(root, manifest_path, *, label, required=()):
    """Strict, fail-closed verification of one frozen member manifest.

    Returns one human problem per defect and never raises for data problems. An
    empty, malformed or missing manifest; a missing, tampered, extra or
    undeclared member; an unsafe member name; a symbolic link; or a missing
    required member is refused. The manifest itself is the only file allowed to
    be undeclared. A Windows directory reparse point is a device-side check
    (R11W); locally every link is refused with ``lstat`` semantics.
    """
    root, manifest_path = Path(root), Path(manifest_path)
    if not root.is_dir():
        return [f'{label} root is missing: {root}']
    if root.is_symlink():
        return [f'{label} root is a symbolic link: {root}']
    # The root's own path components are checked from the project root downwards
    # (or from its parent for a root outside the project), so a parent directory
    # that redirects outside the frozen root is refused before any member is read.
    root_boundary = ROOT if os.path.abspath(root).startswith(os.path.abspath(ROOT) + os.sep) else root.parent
    root_link = symlinked_component(root, root_boundary)
    if root_link is not None:
        return [f'{label} root path contains a symbolic link: {root_link}']
    if not manifest_path.is_file():
        return [f'{label} manifest is missing: {manifest_path}']
    try:
        document = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        return [f'{label} manifest is unreadable ({type(error).__name__})']
    if not isinstance(document, dict):
        return [f'{label} manifest is not a JSON object']
    members = document.get('members')
    if not isinstance(members, dict) or not members:
        return [f'{label} manifest declares no members']
    problems = []
    declared = set()
    for name, entry in sorted(members.items()):
        reason = unsafe_member_name(name)
        if reason:
            problems.append(f'{label} has an unsafe member name ({reason})')
            continue
        declared.add(name)
        if (not isinstance(entry, dict) or not _MEMBER_SHA256.fullmatch(str(entry.get('sha256') or ''))
                or not isinstance(entry.get('size'), int)):
            problems.append(f'{label} member entry is malformed: {name}')
            continue
        candidate = root / PurePosixPath(name)
        link = symlinked_component(candidate, root)
        if link is not None:
            problems.append(f'{label} member path contains a symbolic link: {link}')
            continue
        if candidate.is_symlink():
            problems.append(f'{label} member is a symbolic link: {name}')
            continue
        if not candidate.is_file():
            problems.append(f'{label} member is missing: {name}')
            continue
        if candidate.stat().st_size != entry['size']:
            problems.append(f'{label} member size changed: {name}')
            continue
        if sha256_file(candidate) != entry['sha256']:
            problems.append(f'{label} member digest changed: {name}')
    manifest_relative = None
    try:
        manifest_relative = manifest_path.relative_to(root).as_posix()
    except ValueError:
        pass
    actual = set()
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            problems.append(f'{label} tree contains a symbolic link: {path.relative_to(root).as_posix()}')
            continue
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative != manifest_relative:
                actual.add(relative)
    extra = sorted(actual - declared)
    if extra:
        problems.append(f'{label} has undeclared files: {", ".join(extra[:4])}'
                        + (' …' if len(extra) > 4 else ''))
    for name in required:
        if name not in declared:
            problems.append(f'{label} manifest omits a required member: {name}')
    return problems


def verify_kit_strict(kit_root, *, expected_source_digest=None, expected_program_sha256=None,
                      expected_source_inputs=None):
    """Strict frozen-kit verification, independent of any run.

    Binds the kit to the expected program source digest and input set (a stale
    preparation is refused instead of inherited), proves the declared member set
    equals the actual files, and re-checks every delivery package against the
    release record. Returns problems only; the caller decides the exit code.
    """
    kit_root = Path(kit_root)
    problems = verify_member_manifest(kit_root, kit_root / 'integrity.json', label='kit',
                                      required=KIT_REQUIRED_MEMBERS)
    integrity = releases = None
    try:
        integrity = read_json(kit_root / 'integrity.json')
    except (OSError, ValueError):
        pass
    try:
        releases = read_json(kit_root / 'releases.json')
    except (OSError, ValueError):
        pass
    integrity_inputs = releases_inputs = None
    if not isinstance(integrity, dict):
        problems.append('kit integrity.json is not a readable JSON object')
    else:
        if integrity.get('format') != KIT_VERSION:
            problems.append('kit integrity.json format is not the current kit format')
        source = integrity.get('program_source_digest')
        if not _MEMBER_SHA256.fullmatch(str(source or '')):
            problems.append('kit integrity.json does not record the program source digest')
        elif expected_source_digest and source != expected_source_digest:
            problems.append('kit was frozen from different program sources (stale preparation)')
        program = integrity.get('program_sha256')
        if not _MEMBER_SHA256.fullmatch(str(program or '')):
            problems.append('kit integrity.json does not record the program digest')
        elif expected_program_sha256 and program != expected_program_sha256:
            problems.append('kit program digest is not the expected frozen program')
        integrity_inputs = integrity.get('program_source_inputs')
        problems.extend(input_set_problems(integrity_inputs, label='kit integrity.json'))
        if expected_source_inputs is not None and isinstance(integrity_inputs, list):
            if input_set_key(integrity_inputs) != input_set_key(expected_source_inputs):
                problems.append('kit program source inputs are not the current source inputs (stale preparation)')
    if not isinstance(releases, dict):
        problems.append('kit releases.json is not a readable JSON object')
    else:
        if releases.get('kit_format') != KIT_VERSION or releases.get('prepared_by') != PREPARED_BY:
            problems.append('kit releases.json is not this kit format')
        source = releases.get('program_source_digest')
        if not _MEMBER_SHA256.fullmatch(str(source or '')):
            problems.append('kit releases.json does not record the program source digest')
        elif expected_source_digest and source != expected_source_digest:
            problems.append('kit releases.json was frozen from different program sources (stale preparation)')
        releases_inputs = releases.get('program_source_inputs')
        problems.extend(input_set_problems(releases_inputs, label='kit releases.json'))
        if isinstance(integrity_inputs, list) and isinstance(releases_inputs, list) \
                and input_set_key(integrity_inputs) != input_set_key(releases_inputs):
            problems.append('kit releases.json and integrity.json disagree on the program source inputs')
        if isinstance(integrity, dict) and releases.get('program_sha256') != integrity.get('program_sha256'):
            problems.append('kit releases.json and integrity.json disagree on the program digest')
        modes = releases.get('modes')
        if not isinstance(modes, dict) or sorted(modes) != sorted(MODES):
            problems.append('kit releases.json does not record exactly the three frozen modes')
        else:
            for mode, info in sorted(modes.items()):
                if not isinstance(info, dict):
                    problems.append(f'kit release record is malformed: {mode}')
                    continue
                delivery = info.get('delivery')
                if not isinstance(delivery, str) or unsafe_member_name(delivery):
                    problems.append(f'kit release record {mode} has an unsafe delivery path')
                    continue
                package = kit_root / PurePosixPath(delivery)
                expected = str(info.get('package_sha256') or '')
                if not package.is_file():
                    problems.append(f'kit delivery package is missing: {delivery}')
                elif not _MEMBER_SHA256.fullmatch(expected) or sha256_file(package) != expected:
                    problems.append(f'kit delivery package digest changed: {delivery}')
                elif isinstance(integrity, dict) and integrity.get('program_sha256') != info.get('program_sha256'):
                    problems.append(f'kit {mode} record is bound to a different program than the integrity list')
    return problems


def verify_human_strict(human_root, *, expected_source_digest=None):
    """Strict human-test package verification (manifest, members, source binding)."""
    human_root = Path(human_root)
    problems = verify_member_manifest(human_root, human_root / 'sha256.json',
                                      label='human test package', required=HUMAN_REQUIRED_MEMBERS)
    try:
        index = read_json(human_root / 'sha256.json')
    except (OSError, ValueError):
        index = None
    if not isinstance(index, dict):
        problems.append('human test package sha256.json is not a readable JSON object')
    else:
        if index.get('format') != HUMAN_FORMAT:
            problems.append('human test package sha256.json format is not current')
        source = index.get('program_source_digest')
        if not _MEMBER_SHA256.fullmatch(str(source or '')):
            problems.append('human test package does not record the program source digest')
        elif expected_source_digest and source != expected_source_digest:
            problems.append('human test package was frozen from different program sources (stale preparation)')
    return problems


def validate_accounts(accounts_path, *, kit_root, expected_instance_id):
    """Validate the private account file before any harness or program starts."""
    problems = []
    path = Path(accounts_path)
    if not path.is_file():
        return ['the private runtime_accounts.json was not provided (--accounts or GEP_TEST_WINDOWS_ACCOUNTS)']
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if kit_root is not None:
        try:
            if Path(kit_root).resolve() in resolved.parents:
                problems.append('the private accounts file must stay outside the distributed kit')
        except OSError:
            pass
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        return problems + [f'the private accounts file is unreadable ({type(error).__name__})']
    if not isinstance(document, dict) or document.get('format') != ACCOUNTS_FORMAT:
        return problems + ['the private accounts file is not this accounts format']
    if expected_instance_id and document.get('instance_id') != expected_instance_id:
        problems.append('the private accounts file belongs to a different instance than the kit')
    member = document.get('member')
    if not isinstance(member, dict) or not str(member.get('username') or '').strip() \
            or not str(member.get('password') or ''):
        problems.append('the private accounts file does not carry the prepared member account')
    return problems


def scan_secrets(path, limit=8 * 1024 * 1024):
    """Credential-like patterns in one file; never prints a match."""
    raw = Path(path).read_bytes()[:limit]
    return [pattern.pattern.decode('utf-8', 'ignore') for pattern in _SECRET_PATTERNS if pattern.search(raw)]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def display_path(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def researcher_temporary_password():
    """One synthetic researcher password from the platform's own pure module."""
    server = str(ROOT / 'server')
    if server not in sys.path:
        sys.path.insert(0, server)
    from core import researcher_passwords
    return researcher_passwords.generate_temporary_password()


def free_port(start=8060, stop=8140):
    for candidate in range(start, stop):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', candidate))
            except OSError:
                continue
        return candidate
    raise KitError('no free loopback port in the preparation range')


def windows_config(environ=None):
    """The Windows connection settings, runtime environment only, never defaults."""
    env = os.environ if environ is None else environ
    return {'host': (env.get(SSH_HOST_ENV) or '').strip(),
            'host_key_alias': (env.get(SSH_HOST_KEY_ALIAS_ENV) or '').strip(),
            'windows_root': (env.get(WINDOWS_ROOT_ENV) or '').strip()}


PROGRAM_PACKAGER_FILES = ('tools/build.py', 'tools/package_build.py', 'tools/windows_template.py')
# The Web SDK files the Web build really ships: ``build_current_artifacts``
# copies exactly these four ``packages/gec_web/*.js`` files into the exported
# Web root, so a Web SDK change must invalidate an old program-source binding.
WEB_SDK_SOURCES = ('packages/gec_web/sdk.js', 'packages/gec_web/bridge.js',
                   'packages/gec_web/inputs.js', 'packages/gec_web/shell.js')


def program_source_paths(root=None):
    """Ordered ``(relative name, path)`` pairs of the real program inputs.

    The set is the exported Godot project, the project packagers and the Web SDK
    sources the Web build ships. ``root`` is a parameter so a test can bind an
    isolated source copy instead of editing the real sources.
    """
    root = ROOT if root is None else Path(root)
    project = root / 'examples' / 'synthetic_experiment'
    files = []
    for path in sorted(project.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(project).as_posix()
        if relative.startswith('.godot/') or '__pycache__' in relative or path.suffix in ('.pyc', '.md'):
            continue
        files.append((relative, path))
    for name in PROGRAM_PACKAGER_FILES + WEB_SDK_SOURCES:
        files.append((name, root / name))
    return sorted(files)


def program_source_inputs(root=None):
    """One ``{'name', 'sha256', 'size'}`` record per program input, sorted by name.

    A missing input is an error, never a silently shrunken set: the digest and
    the input list are what a later gate re-checks, so an absent packager or SDK
    file must fail instead of pretending the sources did not change.
    """
    records = []
    for name, path in program_source_paths(root):
        if not path.is_file():
            raise KitError(f'the program source input is missing: {name}')
        records.append({'name': name, 'sha256': sha256_file(path), 'size': path.stat().st_size})
    return records


def program_source_digest(root=None):
    """One deterministic digest of the program sources the new bytes came from.

    A later Windows gate recomputes it from the working tree, so evidence
    produced against older program sources (including a changed Web SDK) is
    refused as stale. ``root`` lets a test digest an isolated source copy.
    """
    digest = hashlib.sha256()
    for relative, path in program_source_paths(root):
        if not path.is_file():
            raise KitError(f'the program source input is missing: {relative}')
        digest.update(relative.encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def input_digest(records):
    """One stable digest of a recorded input list (name, sha256, size).

    Two builds of the same sources must record the same input digest; the
    pre/post build comparison uses it so a source change during the build can
    never be mistaken for a stable build.
    """
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda entry: str(entry.get('name'))):
        digest.update(str(record.get('name')).encode('utf-8'))
        digest.update(b'\0')
        digest.update(str(record.get('sha256')).encode('utf-8'))
        digest.update(b'\0')
        digest.update(str(record.get('size')).encode('utf-8'))
        digest.update(b'\0')
    return digest.hexdigest()


def input_set_key(records):
    """One comparable key for a recorded input list (name, sha256, size)."""
    ordered = []
    for record in records or []:
        if isinstance(record, dict):
            ordered.append((record.get('name'), record.get('sha256'), record.get('size')))
    return sorted(ordered, key=lambda entry: str(entry[0]))


def input_set_problems(records, *, label):
    """Problems for a program-source input list, never a silent empty pass."""
    if not isinstance(records, list) or not records:
        return [f'{label} does not record the program source inputs']
    problems = []
    for record in records:
        if not isinstance(record, dict) \
                or not isinstance(record.get('name'), str) or unsafe_member_name(record.get('name')) \
                or not _MEMBER_SHA256.fullmatch(str(record.get('sha256') or '')) \
                or not isinstance(record.get('size'), int):
            problems.append(f'{label} has a malformed program source input record')
            break
    names = [record.get('name') for record in records if isinstance(record, dict)]
    if len(set(names)) != len(names):
        problems.append(f'{label} program source inputs contain duplicate names')
    return problems


def symlinked_component(path, boundary):
    """The first symbolic-link component between ``boundary`` and ``path``.

    Components are checked with ``lstat`` from the trusted boundary downwards,
    including ancestors, so a link that redirects a parent directory is found
    even though the final component itself is an ordinary file or directory.
    ``resolve()`` is deliberately not used: resolving first would hide the very
    link that must be refused. Returns ``None`` when the path is outside the
    boundary (the caller refuses it separately) or when no component is a link.
    """
    path = Path(os.path.abspath(Path(path)))
    boundary = Path(os.path.abspath(Path(boundary)))
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        return None
    current = boundary
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return current
        except FileNotFoundError:
            return None
        except OSError:
            return None
    return None


def archive_member_index(archive_path):
    """``name -> {'sha256', 'size'}`` for every file member of one zip archive."""
    index = {}
    with zipfile.ZipFile(archive_path) as archive:
        for item in archive.infolist():
            if item.filename.endswith('/'):
                continue
            digest = hashlib.sha256()
            with archive.open(item) as stream:
                while chunk := stream.read(1 << 20):
                    digest.update(chunk)
            index[item.filename] = {'sha256': digest.hexdigest(), 'size': item.file_size}
    return index


def verify_extracted_archive(archive_path, extracted_root, *, label):
    """Every file member of a bound archive must be present byte-identical.

    This checks the actual runtime resources on disk - the ``.pck``, dynamic
    libraries and helper files, not only the executable - so a run never starts
    from an incomplete or substituted unpacked tree. Returns problems only.
    """
    archive_path, extracted_root = Path(archive_path), Path(extracted_root)
    if not archive_path.is_file():
        return [f'{label} archive is missing: {archive_path}']
    if not extracted_root.is_dir():
        return [f'{label} extracted directory is missing: {extracted_root}']
    problems = []
    try:
        index = archive_member_index(archive_path)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        return [f'{label} archive is unreadable ({type(error).__name__})']
    for name, entry in sorted(index.items()):
        candidate = extracted_root / PurePosixPath(name)
        if candidate.is_symlink():
            problems.append(f'{label} extracted member is a symbolic link: {name}')
            continue
        if not candidate.is_file():
            problems.append(f'{label} extracted member is missing: {name}')
            continue
        if candidate.stat().st_size != entry['size'] or sha256_file(candidate) != entry['sha256']:
            problems.append(f'{label} extracted member bytes changed: {name}')
    return problems


def godot_binary():
    """The pinned local Godot engine; missing tooling fails, it never skips."""
    candidate = os.environ.get('GEP_GODOT_BIN') or shutil.which('godot')
    if candidate is None and Path('/Applications/Godot.app/Contents/MacOS/Godot').is_file():
        candidate = '/Applications/Godot.app/Contents/MacOS/Godot'
    if candidate is None or not Path(candidate).is_file():
        raise KitError('official local Godot engine not found (GEP_GODOT_BIN, PATH or /Applications/Godot.app)')
    return candidate


# --- the new frozen Windows program build ----------------------------------

def build_program(build_root):
    """Export and package brand-new Windows x64 program bytes from current sources.

    The pinned official template is re-verified against its digest before it is
    copied into the project template cache, exactly like the project's own
    ``tools/build.py``. The export and the archive are written only inside the
    new ``build_root``; the historical ``build/windows`` bytes stay untouched.
    """
    build_root = Path(build_root)
    # Record the real program inputs (Godot project, packagers, Web SDK) before
    # the export and re-check them after it: a source change during the build is
    # never presented as one stable build, and the kit records the exact set.
    sources_before = program_source_inputs()
    digest_before = program_source_digest()
    try:
        template = find_windows_template()
    except TemplateError as error:
        raise KitError(f'the pinned Windows export template is unavailable: {error}') from None
    template_digest = verify_windows_template(template)
    cache = PROJECT / '.godot' / 'templates'
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / 'windows_release_x86_64.exe'
    if not cached.is_file() or sha256_file(cached) != template_digest:
        shutil.copy2(template, cached)
    program_root = build_root / 'windows' / PROGRAM_ROOT
    program_root.mkdir(parents=True, exist_ok=False)
    output = program_root / PROGRAM_FILES[0]
    result = subprocess.run([godot_binary(), '--headless', '--path', str(PROJECT),
                             '--export-release', 'Windows', str(output)],
                            cwd=ROOT, capture_output=True, text=True, timeout=900)
    (build_root / 'windows' / 'export.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode != 0:
        raise KitError(f'Windows export failed ({result.returncode}); see windows/export.log')
    missing = [name for name in PROGRAM_FILES if not (program_root / name).is_file()]
    if missing:
        raise KitError(f'the Windows export is incomplete: {", ".join(missing)}')
    extension_target = program_root / WINDOWS_EXTENSION[0]
    extension_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(WINDOWS_EXTENSION[1], extension_target)
    archive = build_root / 'windows' / 'synthetic_windows.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name in PROGRAM_FILES:
            package.write(program_root / name, f'{PROGRAM_ROOT}/{name}')
        package.write(WINDOWS_EXTENSION[1], f'{PROGRAM_ROOT}/{WINDOWS_EXTENSION[0]}')
    program_sha256 = sha256_file(archive)
    sources_after = program_source_inputs()
    digest_after = program_source_digest()
    if digest_before != digest_after or input_set_key(sources_before) != input_set_key(sources_after):
        raise KitError('the program sources changed during the Windows export; '
                       'the build is not a stable build')
    document = read_json(PROJECT_DESCRIPTOR)
    document['platform'] = 'windows_x64'
    document['package'] = {'root': PROGRAM_ROOT, 'entry': PROGRAM_FILES[0],
                           'dependencies': [PROGRAM_FILES[2]], 'extension': WINDOWS_EXTENSION[0]}
    document['program_sha256'] = program_sha256
    descriptor = build_root / 'windows' / 'descriptor.json'
    descriptor.write_bytes(canonical_json(document))
    return {'build_root': str(build_root), 'archive': str(archive), 'descriptor': str(descriptor),
            'program_sha256': program_sha256, 'template_sha256': template_digest,
            'program_source_digest': digest_before, 'program_source_digest_after': digest_after,
            'program_source_inputs': sources_before, 'program_source_inputs_after': sources_after}


# --- isolated instance and the real HTTP lifecycle -------------------------

class HttpClient:
    """Minimal authenticated HTTP client for the isolated instance (stdlib only)."""

    def __init__(self, port, timeout=120):
        self.port = port
        self.timeout = timeout
        self.cookies = {}
        self.csrf = ''
        self.requests = []

    def _connection(self):
        return http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)

    def _headers(self, host, extra=None):
        headers = {'Host': host, 'Accept': 'application/json'}
        if self.cookies:
            headers['Cookie'] = '; '.join(f'{name}={value}' for name, value in sorted(self.cookies.items()))
        if extra:
            headers.update(extra)
        return headers

    def _remember(self, response):
        for header, value in response.getheaders():
            if header.lower() == 'set-cookie':
                pair = value.split(';', 1)[0]
                if '=' in pair:
                    name, _, cookie = pair.partition('=')
                    self.cookies[name.strip()] = cookie.strip()
                    if name.strip() == 'csrftoken':
                        self.csrf = cookie.strip()

    def request(self, method, path, host='admin.localhost', body=None, headers=None, expect_redirect=False):
        connection = self._connection()
        try:
            connection.request(method, path, body=body, headers=self._headers(host, headers))
            response = connection.getresponse()
            payload = response.read()
            self._remember(response)
            record = {'method': method, 'path': path, 'host': host, 'status': response.status,
                      'bytes': len(payload), 'location': response.getheader('Location'),
                      'headers': {name.lower(): value for name, value in response.getheaders()}}
            self.requests.append(record)
            if expect_redirect and response.status not in (301, 302, 303):
                raise KitError(f'{method} {path} expected a redirect, got {response.status}: '
                               f'{payload[:200].decode("utf-8", "replace")}')
            return response.status, payload, record
        finally:
            connection.close()

    def get(self, path, host='admin.localhost'):
        return self.request('GET', path, host=host)

    def post_form(self, path, fields, host='admin.localhost', expect_redirect=True):
        body = [(name, value) for name, value in fields]
        if self.csrf and not any(name == 'csrfmiddlewaretoken' for name, _ in body):
            body.append(('csrfmiddlewaretoken', self.csrf))
        encoded = '&'.join(f'{urlquote(str(name))}={urlquote(str(value))}' for name, value in body)
        return self.request('POST', path, host=host, body=encoded.encode('utf-8'),
                            headers={'Content-Type': 'application/x-www-form-urlencoded'},
                            expect_redirect=expect_redirect)

    def post_multipart(self, path, fields, file_field, filename, data, host='admin.localhost',
                       expect_redirect=False):
        boundary = '----gep' + secrets.token_hex(12)
        chunks = []
        for name, value in fields:
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        if self.csrf:
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="csrfmiddlewaretoken"\r\n\r\n'
                          f'{self.csrf}\r\n'.encode())
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                      f'filename="{filename}"\r\nContent-Type: application/zip\r\n\r\n'.encode())
        chunks.append(data)
        chunks.append(f'\r\n--{boundary}--\r\n'.encode())
        return self.request('POST', path, host=host, body=b''.join(chunks),
                            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
                            expect_redirect=expect_redirect)

    def post_json(self, path, document, host='experiment.localhost'):
        return self.request('POST', path, host=host, body=canonical_json(document),
                            headers={'Content-Type': 'application/json'}, expect_redirect=False)

    def login(self, username, password):
        status, payload, _ = self.get('/login')
        if status != 200:
            raise KitError(f'login page unavailable (HTTP {status})')
        match = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', payload)
        if match:
            self.csrf = match.group(1).decode()
        self.post_form('/login', [('username', username), ('password', password)], expect_redirect=False)
        if 'gep_admin' not in self.cookies:
            raise KitError(f'login failed for {username!r} (no session cookie)')


class Instance:
    """One isolated synthetic instance: own volume, database, secret and port."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.data = self.root / 'instance'
        self.db = self.data / 'gep.sqlite3'
        self.logs = self.root / 'http'
        self.owner_password = secrets.token_urlsafe(24)
        self.instance_id = str(uuid.uuid4())
        self.secret_key = secrets.token_urlsafe(48)
        self.port = None
        self.server = None

    def env(self, public_api=None):
        return {
            **os.environ,
            'GEP_DATA_DIR': str(self.data),
            'GEP_SECRET_KEY': self.secret_key,
            'GEP_EXPECTED_INSTANCE': self.instance_id,
            'GEP_PUBLIC_API': public_api or f'http://127.0.0.1:{self.port}',
            'PYTHONPATH': str(ROOT / 'server'),
            'DJANGO_SETTINGS_MODULE': 'gep.settings',
        }

    def initialize(self):
        self.data.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        code = (
            'import os, uuid, django\n'
            'django.setup()\n'
            'from django.core.management import call_command\n'
            'from django.contrib.auth import get_user_model\n'
            'from core.models import AccountProfile, Instance\n'
            "call_command('migrate', verbosity=0)\n"
            "owner = get_user_model().objects.create_user(os.environ['GEP_OWNER_USERNAME'],"
            " password=os.environ['GEP_OWNER_PASSWORD'])\n"
            "Instance.objects.create(instance_id=uuid.UUID(os.environ['GEP_INSTANCE_ID']), owner=owner)\n"
            "AccountProfile.objects.create(user=owner, role='user', must_change_password=False,"
            " auth_version=1, revision=0)\n"
            "print('INSTANCE_READY')\n"
        )
        environment = self.env()
        environment.update({'GEP_OWNER_USERNAME': OWNER_USERNAME,
                            'GEP_OWNER_PASSWORD': self.owner_password,
                            'GEP_INSTANCE_ID': self.instance_id})
        result = subprocess.run([str(VENV_PYTHON), '-c', code], cwd=ROOT, env=environment,
                                capture_output=True, text=True, timeout=600)
        if 'INSTANCE_READY' not in result.stdout:
            raise KitError(f'isolated instance initialization failed: {result.stdout}\n{result.stderr}')
        for name, value in (('secret', self.secret_key), ('instance', self.instance_id)):
            descriptor = os.open(self.data / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write(value)
        return self

    def start(self):
        if self.server is not None and self.server.poll() is None:
            return self
        log = open(self.logs / 'gunicorn.log', 'a')
        self.server = subprocess.Popen(
            [str(GUNICORN), 'gep.wsgi:application', '--bind', f'127.0.0.1:{self.port}',
             '--workers', '1', '--threads', '4', '--timeout', '1800', '--access-logfile', '-'],
            cwd=ROOT, env=self.env(), stdout=log, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', self.port), timeout=1):
                    return self
            except OSError:
                if self.server.poll() is not None:
                    raise KitError(f'isolated server exited early, see {self.logs / "gunicorn.log"}')
                time.sleep(0.3)
        raise KitError('isolated server did not become ready')

    def stop(self):
        if self.server is not None and self.server.poll() is None:
            self.server.send_signal(signal.SIGTERM)
            try:
                self.server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.server.kill()
        self.server = None

    def db_rows(self, sql, params=()):
        connection = sqlite3.connect(f'file:{self.db}?mode=ro', uri=True, timeout=20)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def revision(self):
        return self.db_rows('select governance_revision from core_instance')[0][0]

    def release_record(self, release_id):
        rows = self.db_rows('select artifact_digest, artifact_path, artifact_size, config, approved, build_id '
                            'from core_release where id=?', [release_id.replace('-', '')])
        if not rows:
            raise KitError(f'release {release_id} missing in the isolated database')
        return {'digest': rows[0][0], 'path': rows[0][1], 'size': rows[0][2],
                'config': json.loads(rows[0][3]), 'approved': bool(rows[0][4]),
                'build_id': _uuid_text(rows[0][5])}

    def build_record(self, study_id, version=None):
        sql = 'select id, digest, package_path, descriptor from core_build where study_id=?'
        params = [study_id.replace('-', '')]
        if version is not None:
            sql += " and json_extract(descriptor,'$.version')=?"
            params.append(version)
        rows = self.db_rows(sql, params)
        if not rows:
            raise KitError(f'build {version!r} missing for study {study_id}')
        return {'id': _uuid_text(rows[0][0]), 'digest': rows[0][1], 'package_path': rows[0][2],
                'descriptor': json.loads(rows[0][3])}

    def session_rows(self, study_id):
        return self.db_rows(
            'select session.id, participant.code, release.id, session.revoked from core_session session '
            'join core_release release on session.release_id=release.id '
            'join core_participant participant on session.participant_id=participant.id '
            'where release.study_id=? order by session.created_at', [study_id.replace('-', '')])

    def participant_codes(self, study_id):
        return sorted(row[0] for row in self.db_rows(
            'select code from core_participant where study_id=?', [study_id.replace('-', '')]) if row[0])


def _uuid_text(raw):
    if isinstance(raw, bytes):
        raw = raw.decode()
    raw = str(raw)
    if len(raw) == 32:
        return f'{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}'
    return raw


class KitBuilder:
    """Drives the real lifecycle and writes the kit, privates and human package."""

    def __init__(self, root: Path, build, record, require):
        self.root = Path(root).resolve()
        self.build = build
        self.kit = self.root / 'kit'
        self.private = self.root / 'private'
        self.human = self.root / 'human'
        self.evidence = self.root / 'evidence'
        self.record = record
        self.require = require
        self.instance = Instance(self.root / 'instance-root')
        self.http = None
        self.member = None
        self.member_password = researcher_temporary_password()
        self.participant_passwords = {code: secrets.token_urlsafe(16) for code in ID_CODES}
        self.releases = {}
        self.alternates = {}
        self.artifacts = {}
        self.accounts = {'format': ACCOUNTS_FORMAT, 'synthetic_only': True,
                         'instance_id': None, 'member': None, 'participants': {},
                         'note': 'Synthetic test material for the isolated R11A instance. Never distributed with '
                                 'the tester package, never committed, never logged.'}
        config = windows_config()
        self.runtime = {'format': RUNTIME_FORMAT, 'instance_id': None, 'port': None, 'api_url': None,
                        'tunnel': {}, 'member': None, 'studies': {}, 'expected': {},
                        'private_accounts': 'private/runtime_accounts.json',
                        'windows': {'configured': bool(config['host'] and config['windows_root']),
                                    'settings_source': 'runtime environment only'},
                        'notes': [
                            'The frozen connection.json points at this loopback port; the Windows run needs a '
                            'scoped route to it (R11W). Without it the runtime cases stay BLOCKED; a missing '
                            'prerequisite is never represented as ready.',
                            'The kit carries no credential; usable accounts live in the private artifact.',
                        ]}

    # ------------------------------------------------------------------ helpers
    def study_operation(self, study_id, fields, expect=(200, 302)):
        status, payload, record = self.http.post_form(f'/studies/{study_id}', fields, expect_redirect=False)
        if status not in expect:
            raise KitError(f'study op {fields[0][1]!r} returned {status}, expected {expect}: '
                           f'{payload[:300].decode("utf-8", "replace")}')
        return status, payload, record

    def prepare(self):
        self.kit.mkdir(parents=True, exist_ok=True)
        self.private.mkdir(parents=True, exist_ok=True)
        self.human.mkdir(parents=True, exist_ok=True)
        self.evidence.mkdir(parents=True, exist_ok=True)
        self._prepare_instance()
        for mode in MODES:
            self._prepare_mode(mode)
        self._prepare_member()
        self._record_admissions()
        self._record_platform_negatives()
        self._write_kit_documents()
        self._write_private_artifacts()
        self._write_human_package()
        self.instance.stop()
        return {'kit': str(self.kit), 'human': str(self.human), 'runtime': self.runtime,
                'releases': self.releases, 'alternates': self.alternates, 'artifacts': self.artifacts,
                'instance': str(self.instance.root), 'build': str(self.build['build_root'])}

    def _prepare_instance(self):
        self.instance.initialize()
        self.instance.start()
        self.http = HttpClient(self.instance.port)
        self.http.login(OWNER_USERNAME, self.instance.owner_password)
        self.record(True, '隔离合成实例与真实服务器就绪（独立数据目录/数据库/密钥/端口）',
                    {'port': self.instance.port, 'instance_id': self.instance.instance_id,
                     'data': display_path(self.instance.data)})
        self.accounts['instance_id'] = self.instance.instance_id
        self.runtime['instance_id'] = self.instance.instance_id
        self.runtime['port'] = self.instance.port
        self.runtime['api_url'] = f'http://127.0.0.1:{self.instance.port}'
        listen_port = self.instance.port + 100
        config = windows_config()
        self.runtime['tunnel'] = {
            'listen': f'127.0.0.1:{listen_port}', 'listen_port': listen_port,
            'target': f'127.0.0.1:{self.instance.port}', 'target_port': self.instance.port,
            'direction': 'reverse',
            'runtime_prerequisite': 'PENDING_R11W',
            'note': 'Scoped route from the Windows machine to this preparation-host loopback port. R11W '
                    'establishes it without changing firewall, DNS or certificate trust; a preparation never '
                    'represents this prerequisite as ready.',
        }
        self.runtime['expected']['program_sha256'] = self.build['program_sha256']
        self.runtime['expected']['descriptor_sha256'] = sha256_file(self.build['descriptor'])
        self.runtime['expected']['program_source_digest'] = self.build['program_source_digest']
        self.runtime['expected']['template_sha256'] = self.build['template_sha256']
        # The exact program input set this kit was frozen from; the kit release
        # record and integrity list carry it so a later gate can compare it with
        # the current sources instead of trusting the digest string alone.
        self.runtime['expected']['program_source_inputs'] = self.build['program_source_inputs']

    def _create_study(self, mode):
        status, payload, record = self.http.post_form(
            '/', [('title', f'R11 Windows 合成研究 · {MODE_LABELS[mode]}')])
        location = record.get('location') or ''
        match = re.search(r'/studies/([0-9a-f-]{36})', location)
        if status != 302 or not match:
            raise KitError(f'study creation failed for mode {mode}: HTTP {status} {location!r}')
        return match.group(1)

    def _import_roster(self, study_id, mode, rows):
        if not rows:
            return 0
        added = preview_and_commit(self.http, study_id, roster_csv(rows), self.instance.owner_password)
        self.require(added == len(rows), f'{mode}：真实名单导入报告新增 {added} 个 ID',
                     {'added': added, 'expected': len(rows)})
        codes = self.instance.participant_codes(study_id)
        expected = sorted(row[0] for row in rows)
        self.require(codes == expected, f'{mode}：真实名单账号已创建', {'codes': codes, 'count': len(codes)})
        return added

    def _prepare_mode(self, mode):
        study_id = self._create_study(mode)
        self.study_operation(study_id, [('op', 'configure'), ('mode', mode), ('max_sessions', '24')])
        self.record(True, f'{mode}：真实研究已创建并配置冻结模式',
                    {'study_id': study_id, 'mode': mode, 'max_sessions': 24})
        descriptor = read_json(self.build['descriptor'])
        self.study_operation(study_id, [('op', 'native'), ('descriptor', json.dumps(descriptor))])
        build = self.instance.build_record(study_id, descriptor['version'])
        self.require(build['descriptor'] == descriptor, '平台登记的描述等于新冻结构建描述', build['id'])
        if mode == 'id':
            rows = [(code,) for code in ID_CODES]
        elif mode == 'password':
            rows = [(code, self.participant_passwords[code]) for code in ID_CODES]
        else:
            rows = []
        if self._import_roster(study_id, mode, rows):
            for code in ID_CODES:
                self.accounts['participants'].setdefault(mode, {})[code] = {
                    'code': code,
                    'password': self.participant_passwords[code] if mode == 'password' else None,
                    'study_id': study_id, 'release_id': None}
        raw = Path(self.build['archive']).read_bytes()
        status, payload, _ = self.http.post_multipart(
            f'/studies/{study_id}', [('op', 'native_archive'), ('build_id', build['id'])],
            'package', Path(self.build['archive']).name, raw)
        if status not in (200, 302):
            raise KitError(f'{mode}: program upload returned {status}: '
                           f'{payload[:300].decode("utf-8", "replace")}')
        build = self.instance.build_record(study_id, descriptor['version'])
        self.require(bool(build['package_path']), f'{mode}：新程序归档经真实上传绑定到构建', build['package_path'])
        self.study_operation(study_id, [('op', 'approve'), ('build_id', build['id'])])
        rows = self.instance.db_rows('select id from core_release where study_id=?', [study_id.replace('-', '')])
        release_id = _uuid_text(rows[0][0])
        record = self.instance.release_record(release_id)
        self.require(record['approved'] and record['digest'] and record['path'],
                     f'{mode}：平台冻结并批准完整包', {'release_id': release_id, 'digest': record['digest']})
        self.require(record['config'].get('artifact_format_version') == ARTIFACT_FORMAT
                     and record['config'].get('mode') == mode,
                     f'{mode}：发行冻结了完整包格式与本研究模式', record['config'].get('mode'))
        status, payload, _ = self.http.post_form(
            f'/studies/{study_id}', [('op', 'configure'), ('mode', 'password' if mode != 'password' else 'id'),
                                     ('max_sessions', '25')], expect_redirect=False)
        frozen_code = payload.decode('utf-8', 'replace')
        self.require(status == 409 and 'policy_frozen_after_release' in frozen_code,
                     f'{mode}：批准后参与模式不可再编辑（真实拒绝）',
                     {'status': status, 'code': 'policy_frozen_after_release'})
        current = self.instance.db_rows('select mode, max_sessions from core_study where id=?',
                                        [study_id.replace('-', '')])[0]
        self.require(current[0] == mode and current[1] == 24, f'{mode}：研究策略未被越权修改',
                     {'mode': current[0], 'max_sessions': current[1]})
        self.study_operation(study_id, [('op', 'recruitment'), ('state', 'open')])
        self._publish(study_id, release_id, mode)
        _manifest, info = self._download_release(mode, mode, study_id, release_id, build['id'], record)
        info.update({'program_sha256': descriptor['program_sha256'],
                     'frozen_config_mode': record['config'].get('mode')})
        self.releases[mode] = info
        self.runtime['studies'][mode] = {key: value for key, value in info.items()
                                         if key in ('study_id', 'release_id', 'build_id', 'package_sha256',
                                                    'package_size', 'config_member', 'entry', 'participant_codes')}
        if mode != 'anonymous':
            for code in ID_CODES:
                if mode in self.accounts['participants']:
                    self.accounts['participants'][mode][code]['release_id'] = release_id

    def _publish(self, study_id, release_id, mode):
        revision = self.instance.db_rows('select revision from core_study where id=?',
                                         [study_id.replace('-', '')])[0][0]
        self.study_operation(study_id, [('op', 'publication'), ('study_revision', str(revision)), ('public', '1'),
                                        ('public_summary', 'R11 合成 Windows 原生研究'),
                                        ('public_duration', '约 5 分钟'),
                                        ('public_device_requirements', 'Windows x64 桌面')])
        revision = self.instance.db_rows('select revision from core_study where id=?',
                                         [study_id.replace('-', '')])[0][0]
        self.study_operation(study_id, [('op', 'current_release'), ('study_revision', str(revision)),
                                        ('release_id', release_id)])
        current = self.instance.db_rows('select current_release_id from core_study where id=?',
                                        [study_id.replace('-', '')])[0][0]
        self.require(_uuid_text(current) == release_id, f'{mode}：真实发行设为当前发行', {'release_id': release_id})

    def _download_release(self, label, mode, study_id, release_id, build_id, record):
        target = self.kit / 'delivery' / label
        target.mkdir(parents=True, exist_ok=True)
        status, payload, headers = self.http.get(f'/releases/{release_id}/artifact')
        if status != 200:
            raise KitError(f'{mode}: artifact download returned {status}')
        package = target / f'gep-{release_id}.zip'
        package.write_bytes(payload)
        digest = sha256_file(package)
        self.require(digest == record['digest'] and len(payload) == record['size'],
                     f'{mode}：真实授权下载的完整包等于数据库记录',
                     {'sha256': digest, 'bytes': len(payload)})
        self.require(headers['headers'].get('x-artifact-sha256') == record['digest'],
                     f'{mode}：下载响应头记录同一外层摘要', headers['headers'].get('x-artifact-sha256'))
        sidecars = {}
        status, manifest_payload, _headers = self.http.get(
            f'/releases/{release_id}/artifact/artifact_manifest.json')
        if status != 200:
            raise KitError(f'{mode}: manifest sidecar download returned {status}')
        (target / 'artifact_manifest.json').write_bytes(manifest_payload)
        manifest = json.loads(manifest_payload)
        sidecar_members = ['artifact_manifest.json', manifest['config_member'],
                           'LICENSE', 'THIRD_PARTY_NOTICES.txt']
        for name in sidecar_members:
            encoded = urlquote(name, safe='/')
            status, member, member_headers = self.http.get(f'/releases/{release_id}/artifact/{encoded}')
            if status != 200:
                raise KitError(f'{mode}: sidecar {name} download returned {status}')
            destination = target / PurePosixPath(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(member)
            sidecars[name] = {'sha256': sha256_file(destination), 'size': len(member),
                              'header': member_headers['headers'].get('x-artifact-member-sha256')}
        self.require(sidecars['artifact_manifest.json']['header'] == sha256_file(target / 'artifact_manifest.json'),
                     f'{mode}：清单 sidecar 的响应头摘要与字节一致')
        self.require(manifest.get('artifact_format_version') == ARTIFACT_FORMAT
                     and manifest.get('platform') == 'windows_x64',
                     f'{mode}：下载清单声明平台与格式', manifest.get('artifact_format_version'))
        self.require(manifest.get('study_id') == study_id and manifest.get('release_id') == release_id
                     and manifest.get('build_id') == build_id,
                     f'{mode}：下载清单绑定本研究/发行/构建',
                     {'study_id': manifest.get('study_id'), 'release_id': manifest.get('release_id'),
                      'build_id': manifest.get('build_id')})
        self.require(manifest.get('program_sha256') == self.runtime['expected']['program_sha256'],
                     f'{mode}：下载清单记录本批新程序摘要', manifest.get('program_sha256'))
        with zipfile.ZipFile(package) as archive:
            bundled = archive.read('artifact_manifest.json')
        self.require(bundled == (target / 'artifact_manifest.json').read_bytes(),
                     f'{mode}：包内清单与平台 sidecar 逐字节一致（未被本工具改写）')
        config = json.loads((target / PurePosixPath(manifest['config_member'])).read_bytes())
        self.require(config.get('api_url') == f'http://127.0.0.1:{self.instance.port}'
                     and config.get('instance_id') == self.instance.instance_id
                     and config.get('study_id') == study_id and config.get('release_id') == release_id
                     and config.get('mode') == mode,
                     f'{mode}：冻结公开配置绑定本次实例/研究/发行/模式',
                     {'api_url': config.get('api_url'), 'mode': config.get('mode')})
        self.require(not ({'password', 'participants', 'roster', 'token', 'secret'} & set(config)),
                     f'{mode}：冻结公开配置不含口令/名单/凭据')
        self.artifacts[label] = {'package': str(package.relative_to(self.root)), 'sidecars': sidecars}
        info = {'study_id': study_id, 'release_id': release_id, 'build_id': build_id, 'mode': mode,
                'package_sha256': record['digest'], 'package_size': record['size'],
                'artifact_sha256': record['digest'], 'artifact_size': record['size'],
                'delivery': f'delivery/{label}/gep-{release_id}.zip',
                'config_member': manifest.get('config_member'), 'entry': manifest.get('entry'),
                'participant_codes': list(ID_CODES) if mode != 'anonymous' else []}
        self.record(True, f'{label}：真实授权下载的新冻结完整包与 sidecar 已入 kit（未重新打包）',
                    {'package': package.name, 'members': len(manifest.get('members', []))})
        return manifest, info

    def _prepare_member(self):
        username = MEMBER_USERNAME
        studies = {mode: info['study_id'] for mode, info in self.releases.items()}
        member_client = HttpClient(self.instance.port)
        for mode, study_id in studies.items():
            revision = self.instance.revision()
            fields = [('op', 'invite'), ('revision', str(revision)), ('username', username)]
            fields.extend(('actions', action) for action in MEMBER_ACTIONS)
            status, payload, _ = self.study_operation(study_id, fields, expect=(200, 302))
            text = payload.decode('utf-8', 'replace')
            match = re.search(r'邀请密钥（请通过可信渠道交付）：([A-Za-z0-9_\-]{16,})', text)
            if not match:
                raise KitError(f'invitation key for study {study_id} not found in the response')
            token = match.group(1)
            existing = bool(self.instance.db_rows('select 1 from auth_user where username=?', [username]))
            client = member_client if existing else self.http
            if existing:
                member_client.login(username, self.member_password)
            status, payload, _ = client.post_form(
                '/activate', [('token', token), ('password', self.member_password)], expect_redirect=False)
            if status != 302:
                raise KitError(f'invitation activation failed for study {study_id}: HTTP {status}')
            self.record(True, f'{mode}：经真实邀请流程创建/复用受限成员（study.view/session.recover/data.export_raw）',
                        {'username': username, 'study_id': study_id, 'actions': list(MEMBER_ACTIONS),
                         'activated_by': 'member' if existing else 'owner'})
        grants = self.instance.db_rows(
            'select count(*) from core_grant grant join auth_user user on grant.user_id=user.id '
            'where user.username=?', [username])[0][0]
        self.require(grants == len(MEMBER_ACTIONS) * len(studies), '受限成员按邀请动作获得授权',
                     {'grants': grants, 'expected': len(MEMBER_ACTIONS) * len(studies)})
        self.require(not self.instance.db_rows(
            'select count(*) from core_grant grant join auth_user user on grant.user_id=user.id '
            "where user.username=? and grant.action in ('release.approve_pilot','build.upload','study.configure')",
            [username])[0][0], '受限成员没有被授予批准/上传/配置权限')
        member = HttpClient(self.instance.port)
        member.login(username, self.member_password)
        self.member = member
        self.record(True, '受限成员真实登录成功（会话 cookie）', {'username': username})
        self.accounts['member'] = {'username': username, 'password': self.member_password,
                                   'actions': list(MEMBER_ACTIONS)}
        self.runtime['member'] = {'username': username, 'actions': list(MEMBER_ACTIONS)}

    def _record_admissions(self):
        for mode, info in self.releases.items():
            operation = str(uuid.uuid4())
            body = {'operation_id': operation, 'proof': secrets.token_hex(32),
                    'instance_id': self.instance.instance_id, 'study_id': info['study_id'],
                    'release_id': info['release_id'], 'build_id': info['build_id']}
            if mode != 'anonymous':
                body['participant_code'] = ID_CODES[0]
            if mode == 'password':
                body['password'] = self.participant_passwords[ID_CODES[0]]
            status, payload, _ = self.http.post_json('/v1/participant/sessions', body)
            if status != 200:
                raise KitError(f'{mode}: real admission returned {status}: '
                               f'{payload[:200].decode("utf-8", "replace")}')
            admission = json.loads(payload)
            self.require(admission.get('session_id') and admission.get('token')
                         and admission.get('release_id') == info['release_id']
                         and admission.get('instance_id') == self.instance.instance_id
                         and admission.get('config', {}).get('mode') == mode,
                         f'{mode}：真实 HTTP 准入创建会话并绑定本实例/发行',
                         {'session_id': admission.get('session_id')})
            rows = self.instance.session_rows(info['study_id'])
            self.require(len(rows) == 1 and _uuid_text(rows[0][0]) == admission['session_id'],
                         f'{mode}：准入会话写入真实数据库', {'sessions': len(rows)})
            info['admission_session_id'] = admission['session_id']
            if mode != 'anonymous':
                self.require(rows[0][1] == ID_CODES[0], f'{mode}：会话绑定名单账号', rows[0][1])
        self._record_wrong_credentials()

    def _record_wrong_credentials(self):
        info = self.releases['password']
        body = {'operation_id': str(uuid.uuid4()), 'proof': secrets.token_hex(32),
                'instance_id': self.instance.instance_id, 'study_id': info['study_id'],
                'release_id': info['release_id'], 'build_id': info['build_id'],
                'participant_code': ID_CODES[0], 'password': 'wrong-' + secrets.token_urlsafe(8)}
        status, payload, _ = self.http.post_json('/v1/participant/sessions', body)
        code = json.loads(payload).get('code') if payload[:1] == b'{' else None
        self.require(status == 403 and code == 'admission_denied',
                     '错误口令被真实拒绝且不创建会话', {'status': status, 'code': code})
        self.require(len(self.instance.session_rows(info['study_id'])) == 1,
                     '错误口令没有增加会话', {'sessions': len(self.instance.session_rows(info['study_id']))})

    def _record_platform_negatives(self):
        """WN06 platform-side contracts against the real stored artifact."""
        info = self.releases['anonymous']
        release_id = info['release_id']
        record = self.instance.release_record(release_id)
        stored = self.instance.data / 'artifacts' / record['path']
        original = stored.read_bytes()
        try:
            tampered = bytearray(original)
            tampered[len(tampered) // 2] = (tampered[len(tampered) // 2] + 1) % 256
            stored.write_bytes(bytes(tampered))
            status, _, _ = self.http.get(f'/releases/{release_id}/artifact')
            self.require(status == 409, 'WN06：存储完整包被篡改后授权下载失败关闭（409）', {'status': status})
            status, _, _ = self.http.get(f'/releases/{release_id}/artifact/artifact_manifest.json')
            self.require(status == 409, 'WN06：篡改后 sidecar 下载失败关闭（409）', {'status': status})
            body = {'operation_id': str(uuid.uuid4()), 'proof': secrets.token_hex(32),
                    'instance_id': self.instance.instance_id, 'study_id': info['study_id'],
                    'release_id': release_id, 'build_id': info['build_id']}
            status, payload, _ = self.http.post_json('/v1/participant/sessions', body)
            code = json.loads(payload).get('code') if payload[:1] == b'{' else None
            self.require(status == 409 and code == 'release_unavailable',
                         'WN06：篡改后准入在创建会话前失败关闭', {'status': status, 'code': code})
            self.require(len(self.instance.session_rows(info['study_id'])) == 1,
                         'WN06：篡改后没有创建新会话')
        finally:
            stored.write_bytes(original)
        self.require(sha256_file(stored) == record['digest'], 'WN06：恢复原始字节后摘要一致')
        status, payload, _ = self.http.get(f'/releases/{release_id}/artifact')
        self.require(status == 200 and sha256_file_bytes(payload) == record['digest'],
                     'WN06：恢复后同一发行可再次逐字节下载', {'status': status})
        self._record_upload_negatives()

    def _record_upload_negatives(self):
        info = self.releases['anonymous']
        study_id = info['study_id']
        descriptor = read_json(self.build['descriptor'])
        with zipfile.ZipFile(self.build['archive']) as source:
            members = {item.filename: source.read(item.filename) for item in source.infolist()
                       if not item.filename.endswith('/')}
        dependency = descriptor['package']['dependencies'][0]
        missing = {name: raw for name, raw in members.items()
                   if name != f'{descriptor["package"]["root"]}/{dependency}'}
        broken = self.evidence / 'missing_dependency.zip'
        with zipfile.ZipFile(broken, 'w', compression=zipfile.ZIP_DEFLATED) as out:
            for name, raw in missing.items():
                out.writestr(name, raw)
        broken_descriptor = {**descriptor, 'version': 'synthetic-negative-missing',
                             'program_sha256': sha256_file(broken)}
        self.study_operation(study_id, [('op', 'native'), ('descriptor', json.dumps(broken_descriptor))])
        broken_build = self.instance.build_record(study_id, 'synthetic-negative-missing')
        status, payload, _ = self.http.post_multipart(
            f'/studies/{study_id}', [('op', 'native_archive'), ('build_id', broken_build['id'])],
            'package', 'missing_dependency.zip', broken.read_bytes())
        code = json.loads(payload).get('code') if payload[:1] == b'{' else None
        self.require(status == 422 and code == 'missing_dependencies',
                     'WN06：缺少声明原生依赖的新程序归档被真实拒绝', {'status': status, 'code': code})
        stored = self.instance.build_record(study_id, 'synthetic-negative-missing')
        self.require(not stored['package_path'], 'WN06：被拒上传没有绑定程序包', stored['package_path'])
        wrong_archive = self.evidence / 'wrong_platform.zip'
        with zipfile.ZipFile(wrong_archive, 'w', compression=zipfile.ZIP_DEFLATED) as out:
            for name, raw in missing.items():
                out.writestr(name, raw)
            out.writestr('GEP Synthetic Experiment/README.txt', b'synthetic wrong-platform probe')
        wrong = {**descriptor, 'version': 'synthetic-negative-platform', 'platform': 'macos_arm64',
                 'program_sha256': sha256_file(wrong_archive)}
        wrong.pop('package', None)
        self.study_operation(study_id, [('op', 'native'), ('descriptor', json.dumps(wrong))])
        wrong_build = self.instance.build_record(study_id, 'synthetic-negative-platform')
        status, payload, _ = self.http.post_multipart(
            f'/studies/{study_id}', [('op', 'native_archive'), ('build_id', wrong_build['id'])],
            'package', 'wrong_platform.zip', wrong_archive.read_bytes())
        code = json.loads(payload).get('code') if payload[:1] == b'{' else None
        self.require(status == 422 and code in ('reserved_path', 'missing_binary', 'unsafe_path',
                                                'missing_info_plist', 'multiple_bundles', 'invalid_archive'),
                     'WN06：错平台新程序归档被真实拒绝', {'status': status, 'code': code})
        grants = self.instance.db_rows(
            'select count(*) from core_grant grant join auth_user user on grant.user_id=user.id '
            "where user.username=? and grant.action='build.upload'", [MEMBER_USERNAME])[0][0]
        self.require(grants == 0, 'WN06：受限成员没有下载权限（撤权前提）', {'build.upload grants': grants})
        status, _, _ = self.member.get(f"/releases/{info['release_id']}/artifact")
        self.require(status == 403, 'WN06：缺少下载授权的成员被真实拒绝（403）', {'status': status})
        status, _, _ = self.http.get(f"/releases/{info['release_id']}/artifact")
        self.require(status == 200, 'WN06：具备授权的账号仍可下载（对照）', {'status': status})

    def _write_kit_documents(self):
        lines = ['# GEP Windows x64 交付 kit（合成，真实平台发行，R11 新冻结）', '',
                 f'- kit 格式：{KIT_VERSION}',
                 '- 每个模式一个独立合成研究/发行，三者共用同一不可变 Windows x64 新程序构建。',
                 '- 完整包与 sidecar 全部来自平台授权下载端点，未被本工具重新打包或改写。',
                 f'- 冻结 API：{self.runtime["api_url"]}（Windows 侧运行需要作用域路由；未配置时运行项保持未运行）。',
                 '',
                 '## 交付内容', '']
        for mode, info in sorted(self.releases.items()):
            lines.append(f'- `{info["delivery"]}`：{MODE_LABELS[mode]}（release {info["release_id"]}，'
                         f'sha256 {info["artifact_sha256"][:16]}…）')
        lines += ['', '## 边界', '',
                  '- `integrity.json` 只是 kit 成员哈希清单（传输完整性），**不是签名**，也不代表代码签名。',
                  '- kit 内没有账号、口令、令牌或恢复证明；可用测试账号在私有产物中，不随测试者包分发。',
                  '- 平台侧完整性（发行外层摘要、成员清单、篡改失败关闭）由准备工具与严格门槛复核。',
                  '- 本地副本被篡改的 PCK 不会被程序运行时自动拒绝：运行时不做包完整性校验，这是已知限制。',
                  '- 本准备输出不是 Windows 运行结果；WN01–W6 必须在真实 Windows x64 上运行 `--verify`。',
                  '']
        (self.kit / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
        self.runtime['kit_format'] = KIT_VERSION
        self.runtime['prepared_by'] = PREPARED_BY
        self.runtime['prepared_for'] = PREPARED_FOR
        (self.kit / 'releases.json').write_bytes(canonical_json(
            {'kit_format': KIT_VERSION, 'prepared_by': PREPARED_BY, 'prepared_for': PREPARED_FOR,
             'instance_id': self.instance.instance_id,
             'api_url': self.runtime['api_url'], 'modes': self.releases,
             'program_sha256': self.runtime['expected']['program_sha256'],
             'descriptor_sha256': self.runtime['expected']['descriptor_sha256'],
             'program_source_digest': self.runtime['expected']['program_source_digest'],
             'program_source_digest_after': self.runtime['expected']['program_source_digest'],
             'program_source_inputs': self.runtime['expected']['program_source_inputs'],
             'note': 'Recorded from the real platform lifecycle; the packages are the downloaded bytes; the '
                     'current-source program digest and its exact input set bind this preparation to the '
                     'sources it was frozen from.'}))
        operator = self.kit / 'operator'
        (operator / 'harness').mkdir(parents=True, exist_ok=True)
        shutil.copy2(HARNESS, self.kit / KIT_HARNESS)
        self.require((self.kit / KIT_HARNESS).is_file(), 'kit 内自带可搬移 harness', KIT_HARNESS)
        (operator / 'runtime.json').write_bytes(canonical_json(self.runtime))

    def _write_private_artifacts(self):
        self.private.mkdir(parents=True, exist_ok=True)
        accounts = self.private / 'runtime_accounts.json'
        accounts.write_bytes(canonical_json(self.accounts))
        accounts.chmod(0o600)
        owner = self.private / 'owner_credentials.json'
        owner.write_bytes(canonical_json({'format': OWNER_FORMAT, 'username': OWNER_USERNAME,
                                          'password': self.instance.owner_password,
                                          'instance_id': self.instance.instance_id,
                                          'note': 'Owner material for the isolated R11A instance; never copied to '
                                                  'the Windows test machine and never distributed.'}))
        owner.chmod(0o600)
        runtime = self.private / 'runtime_private.json'
        private_runtime = {'format': RUNTIME_FORMAT, 'instance_id': self.instance.instance_id,
                           'owner_username': OWNER_USERNAME, 'member_username': MEMBER_USERNAME,
                           'windows': windows_config(),
                           'api_url': self.runtime['api_url'], 'port': self.instance.port,
                           'note': 'Private runtime material: connection settings and account names. Never '
                                   'committed and never part of a distributed package.'}
        runtime.write_bytes(canonical_json(private_runtime))
        runtime.chmod(0o600)
        self.require(stat.S_IMODE(accounts.stat().st_mode) == 0o600, '私有账号产物权限为 0600')
        self.require(stat.S_IMODE(runtime.stat().st_mode) == 0o600, '私有连接产物权限为 0600')
        self.require(self.kit not in accounts.parents and self.kit not in owner.parents
                     and self.kit not in runtime.parents,
                     '私有账号/口令/连接产物不在可分发 kit 内')
        return accounts

    def _write_human_package(self):
        """The tester-facing package: frozen delivery bytes plus the human guide.

        Credentials stay outside; this directory is what a human tester may
        receive. Every delivered byte is a copy of an authorised download whose
        digest is recorded here and in the kit release record.
        """
        delivery = self.human / 'delivery'
        shutil.copytree(self.kit / 'delivery', delivery)
        digests = {}
        for path in sorted(delivery.rglob('*')):
            if path.is_file():
                digests[path.relative_to(self.human).as_posix()] = {
                    'sha256': sha256_file(path), 'size': path.stat().st_size}
        lines = [            '# GEP 新一轮人工测试包（R11 工程准备，合成）', '',
            f'- 格式：{HUMAN_FORMAT}',
            f'- 程序（Windows x64 新冻结）sha256：`{self.runtime["expected"]["program_sha256"]}`',
            '- 交付字节来自真实登记/上传/批准/下载生命周期；本包不含任何账号、口令或令牌。',
            '- 工程准备不等于运行结果：Windows 运行（WN01–WN06）必须由 `--verify` 在真实 Windows x64 上完成。',
            '',
            '## 人工测试记录口径（T17）', '',
            '- 帮助、失败、未运行分别如实记录，不合并为“通过”。',
            '- 工具与自动检查不能产生人的 PASS；任何人的结论都单独记录。',
            '- 本轮只到人工测试准备；Phase 04 未授权，不做真实部署或真实数据。',
            '- 测试账号与连接材料由操作者私下交付，绝不随本包分发。',
            '',
            '## 本包内容', '',
            '- `delivery/<mode>/`：三种参与模式的新冻结完整包与 sidecar（匿名 / 名单 ID / ID+密码）。',
            '- `sha256.json`：交付字节的哈希清单（传输完整性，不是签名）。',
            '',
        ]
        (self.human / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
        digests['README.md'] = {'sha256': sha256_file(self.human / 'README.md'),
                                'size': (self.human / 'README.md').stat().st_size}
        (self.human / 'sha256.json').write_bytes(canonical_json(
            {'format': HUMAN_FORMAT, 'program_sha256': self.runtime['expected']['program_sha256'],
             'program_source_digest': self.runtime['expected']['program_source_digest'],
             'note': 'Integrity list of the human-test package delivery bytes. Not a signature.',
             'members': digests}))
        self.require(not any(p.name in ('runtime_accounts.json', 'owner_credentials.json', 'runtime_private.json')
                             for p in self.human.rglob('*')),
                     '人工测试包内不含私有账号/口令/连接产物')

    def check_no_secrets(self, paths):
        findings = []
        for path in paths:
            candidates = [item for item in Path(path).rglob('*') if item.is_file()] if Path(path).is_dir() else [Path(path)]
            for candidate in candidates:
                if candidate.name in ('runtime_accounts.json', 'owner_credentials.json', 'runtime_private.json'):
                    continue
                hits = scan_secrets(candidate)
                if hits:
                    findings.append({'path': display_path(candidate), 'patterns': len(hits)})
        self.require(not findings, 'kit 与人工包不含凭据/密钥样式内容', findings or None)

    def check_relocatable_launchers(self):
        """Copy the kit plus privates to a fresh directory and prove the layout.

        A launcher that points at a sibling ``tools/`` directory would break on
        the Windows machine, so the complete operator tooling must travel inside
        the kit; the frozen endpoint contract is re-read from the copied file.
        """
        fresh = guard_unique_child(self.root, 'relocatable-check')
        shutil.copytree(self.kit, fresh / 'kit')
        (fresh / 'private').mkdir(parents=True)
        shutil.copy2(self.private / 'runtime_accounts.json', fresh / 'private' / 'runtime_accounts.json')
        targets = [fresh / 'kit' / KIT_HARNESS, fresh / 'kit' / 'operator' / 'runtime.json',
                   fresh / 'private' / 'runtime_accounts.json']
        missing = [display_path(path) for path in targets if not path.is_file()]
        self.require(not missing, '复制 kit+私有账号到全新目录后目标齐全', missing or None)
        runtime = read_json(fresh / 'kit' / 'operator' / 'runtime.json')
        self.require(runtime.get('format') == RUNTIME_FORMAT and runtime.get('port'),
                     '搬移后的 runtime.json 仍是本 kit 格式', runtime.get('format'))
        harness = (fresh / 'kit' / KIT_HARNESS).read_text(encoding='utf-8')
        self.require('--run' in harness, '搬移后的 harness 是可执行的工程运行入口')
        return fresh

    def human_package(self):
        return self.human

    def kit_members(self):
        members = {}
        for path in sorted(self.kit.rglob('*')):
            if not path.is_file() or path.name == 'integrity.json':
                continue
            members[path.relative_to(self.kit).as_posix()] = {'sha256': sha256_file(path),
                                                             'size': path.stat().st_size}
        return members

    def write_integrity(self):
        sidecar = {'format': KIT_VERSION, 'app': 'GEP Synthetic Experiment',
                   'prepared_by': PREPARED_BY, 'prepared_for': PREPARED_FOR,
                   'program_sha256': self.runtime['expected']['program_sha256'],
                   'program_source_digest': self.runtime['expected']['program_source_digest'],
                   'program_source_inputs': self.runtime['expected']['program_source_inputs'],
                   'note': 'Integrity list of the kit members (transport integrity) with the program source '
                           'input set. Not a signature.',
                   'members': self.kit_members()}
        (self.kit / 'integrity.json').write_bytes(canonical_json(sidecar))
        return sidecar


def prepare(evidence_root=None, quiet=False):
    """Run one full preparation; returns the summary document."""
    root = guard_evidence_root(evidence_root) if evidence_root else new_unique_root(EVIDENCE_BASE)
    build_root = BUILD_BASE / f'{utc_stamp()}-{secrets.token_hex(4)}'
    build_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    checks = []

    def record(ok, label, detail=None):
        checks.append({'ok': bool(ok), 'label': label, 'detail': detail})
        if not quiet:
            print(('ok   ' if ok else 'FAIL ') + label + (f' :: {detail}' if detail is not None else ''), flush=True)
        return bool(ok)

    def require(ok, label, detail=None):
        if not record(ok, label, detail):
            raise KitError(label)
        return True

    report = {'task': 'p03r11a-windows-prepare', 'verdict': 'failed', 'evidence_root': str(root),
              'build_root': str(build_root), 'windows_verified': False,
              'run_at': datetime.now(timezone.utc).isoformat()}
    try:
        build = build_program(build_root)
        record(True, '官方 Windows 模板摘要复核并导出新冻结程序（当前源码）',
               {'template_sha256': build['template_sha256'][:16], 'build_root': display_path(build_root)})
        record(True, '新冻结程序归档摘要与源码摘要已绑定（构建前后输入一致）',
               {'program_sha256': build['program_sha256'][:16],
                'program_source_digest': build['program_source_digest'][:16],
                'inputs': len(build['program_source_inputs'])})
        builder = KitBuilder(root, build, record, require)
        summary = builder.prepare()
        sidecar = builder.write_integrity()
        record(True, 'kit 成员完整性清单已写入', {'members': len(sidecar['members'])})
        builder.check_no_secrets([builder.kit, builder.human])
        record(True, 'kit 与人工测试包不含凭据/密钥样式内容')
        builder.check_relocatable_launchers()
        record(True, 'kit+私有账号复制到全新目录后布局可搬移')
        # Fail closed on the exact frozen tree before any later run inherits it:
        # an empty manifest, a missing/tampered/extra member or a stale source
        # binding must fail the preparation itself, not the device run.
        strict_problems = (verify_kit_strict(builder.kit, expected_source_digest=build['program_source_digest'],
                                             expected_source_inputs=build['program_source_inputs'])
                           + verify_human_strict(builder.human, expected_source_digest=build['program_source_digest']))
        if strict_problems:
            raise KitError('the frozen kit/human manifest self-check failed: ' + '; '.join(strict_problems[:3]))
        record(True, '严格冻结包自检：成员集合、摘要/大小、程序/源码/输入绑定与安全路径全部通过',
               {'kit_members': len(sidecar['members']), 'source_inputs': len(build['program_source_inputs'])})
        report.update({'verdict': 'ok', 'build': build, 'summary': summary,
                       'integrity': {'members': len(sidecar['members']),
                                     'program_sha256': sidecar['program_sha256'],
                                     'program_source_digest': sidecar['program_source_digest'],
                                     'program_source_inputs': len(sidecar['program_source_inputs'])},
                       'human_package': str(builder.human),
                       'windows_verified': False,
                       'windows_run_required': 'R11W: tools/remediation_windows.py --verify on the real Windows x64 host'})
    except (KitError, OSError, ValueError, subprocess.SubprocessError) as error:
        report['error'] = f'{type(error).__name__}: {error}'
        report['checks'] = checks
        (root / 'prepare_report.json').write_bytes(canonical_json(report))
        print(f'WINDOWS PREPARE FAILED :: {report["error"]}', file=sys.stderr, flush=True)
        print(json.dumps(report, ensure_ascii=False, default=str))
        return 1, report
    report['checks'] = checks
    (root / 'prepare_report.json').write_bytes(canonical_json(report))
    print(json.dumps({'verdict': report['verdict'], 'evidence_root': report['evidence_root'],
                      'build_root': report['build_root'],
                      'program_sha256': report['build']['program_sha256'],
                      'program_source_digest': report['build']['program_source_digest'],
                      'windows_verified': False,
                      'next': report['windows_run_required']}, ensure_ascii=False, indent=2))
    return 0, report


# --- the real Windows run gate ---------------------------------------------

def _ssh_options(config):
    options = list(SSH_OPTIONS)
    alias = (config.get('host_key_alias') or '').strip()
    if alias:
        options += ['-o', f'HostKeyAlias={alias}']
    return options


def _powershell(script):
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return 'powershell -NoProfile -NonInteractive -EncodedCommand ' + encoded


def _ssh_run(config, script, timeout):
    command = ['ssh', *_ssh_options(config), config['host'], _powershell(script)]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def _scp_copy(config, local, remote, to_remote, timeout=3600):
    command = ['scp', *_ssh_options(config)]
    if to_remote:
        command += [str(local), f"{config['host']}:{remote}"]
    else:
        command += [f"{config['host']}:{remote}", str(local)]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def _harness_outcome(exit_code, document):
    cases = {}
    if isinstance(document, dict):
        cases = {case.get('id'): case.get('status') for case in document.get('cases', [])
                 if isinstance(case, dict)}
    expected_cases = ('WN01', 'WN02', 'WN03', 'WN04', 'WN05', 'WN06')
    missing = [case for case in expected_cases if cases.get(case) != 'PASS']
    ok = (exit_code == 0 and isinstance(document, dict)
          and document.get('runtime_acceptance') == 'PASS' and document.get('checks_failed') == 0
          and not missing)
    return cases, missing, ok


def _harness_case_map(document):
    cases = {}
    for case in (document or {}).get('cases') or []:
        if isinstance(case, dict) and case.get('id'):
            cases[str(case['id'])] = case
    return cases


def _case_failures(case):
    return [check for check in case.get('checks') or [] if not check.get('ok')]


def _evidence_artifact_refs(node, refs):
    """Collect every ``path``/``*_path`` reference that carries a real digest.

    Only references with a recorded 64-hex digest are treated as bound
    artifacts (the authorized exports and the preserved store copies); a bare
    process path without a digest is not a reconciliation artifact.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and value and (key == 'path' or key.endswith('_path')):
                sha_key = 'sha256' if key == 'path' else key[:-5] + '_sha256'
                recorded = node.get(sha_key)
                if isinstance(recorded, str) and _MEMBER_SHA256.fullmatch(recorded):
                    refs.append((value, recorded))
            _evidence_artifact_refs(value, refs)
    elif isinstance(node, list):
        for item in node:
            _evidence_artifact_refs(item, refs)


def _bound_evidence_file(root, relative, digest=None):
    """One problem when a recorded relative artifact escapes or does not match."""
    if unsafe_member_name(relative):
        return f'the run evidence path is not a safe relative member: {relative!r}'
    candidate = root / PurePosixPath(relative)
    link = symlinked_component(candidate, root)
    if link is not None:
        return f'the run evidence path contains a symbolic link: {relative}'
    if not candidate.is_file():
        return f'the recorded run evidence file is missing: {relative}'
    if digest is not None and sha256_file(candidate) != digest:
        return f'the recorded run evidence digest changed: {relative}'
    return None


def device_facts(document):
    """The device facts a run report records from the raw harness host section."""
    host = (document or {}).get('host') or {}
    machine = host.get('machine') or {}
    return {'platform': host.get('platform'),
            'architecture': host.get('processor_architecture'),
            'os': machine.get('Caption'),
            'os_version': machine.get('Version'),
            'os_architecture': machine.get('Arch'),
            'system_type': machine.get('System'),
            'windows_build': host.get('windows_build'),
            'console_session': host.get('console_session'),
            'interactive': host.get('interactive'),
            'python': host.get('python'),
            'session_name': host.get('session_name')}


def validate_windows_run_evidence(evidence_root, *, expected_source_digest, expected_program_sha256=None,
                                  kit_root=None, expected_source_inputs=None):
    """Re-read one complete Windows x64 run; a summary is never a pass.

    The evidence root must carry the frozen ``verify_report.json`` with the
    current report format, the current program source digest, the frozen program
    digest, the kit instance identity and the run id, and the **raw harness
    document** the report names (relative to this root, never an external path).
    The harness document must be a full ``run`` produced on a real Windows x64
    host (platform, architecture, OS caption/build, interactive console session),
    bind the same instance, the same program/source digests and the same run id,
    pass every WN01-WN06 case with real checks, and keep every recorded
    reconciliation artifact inside this root with the recorded digest. The
    recorded frozen kit is re-verified strictly, so an old kit, an old package
    or a relocated report cannot inherit a pass.
    """
    root = Path(os.path.abspath(Path(evidence_root).expanduser()))
    if not root.is_dir():
        return [f'the Windows run evidence root is missing: {root}']
    report_path = root / 'verify_report.json'
    if not report_path.is_file():
        return [f'the Windows verify report is missing: {report_path}']
    try:
        report = read_json(report_path)
    except (OSError, ValueError) as error:
        return [f'the Windows verify report is unreadable ({type(error).__name__})']
    if not isinstance(report, dict):
        return ['the Windows verify report is not a JSON object']
    return _run_report_problems(root, report, expected_source_digest=expected_source_digest,
                                expected_program_sha256=expected_program_sha256, kit_root=kit_root,
                                expected_source_inputs=expected_source_inputs, check_flags=True)


def _run_report_problems(root, report, *, expected_source_digest, expected_program_sha256=None,
                         kit_root=None, expected_source_inputs=None, check_flags=True):
    """Every structural/identity/digest check of one run report and its raw files."""
    problems = []
    if check_flags:
        if report.get('format') != VERIFY_FORMAT:
            problems.append('the Windows verify report does not carry the current format')
        if report.get('verdict') != 'ok' or report.get('windows_verified') is not True:
            problems.append('the Windows verify report is not an ok, verified run')
    if report.get('program_source_digest') != expected_source_digest:
        problems.append('the Windows run was produced from different program sources (stale evidence)')
    program = str(report.get('program_sha256') or '')
    if not _MEMBER_SHA256.fullmatch(program):
        problems.append('the Windows verify report does not record the frozen program digest')
    elif expected_program_sha256 and program != expected_program_sha256:
        problems.append('the Windows run was produced from a different frozen program than the fresh preparation')
    kit_instance = str(report.get('kit_instance_id') or '')
    if not kit_instance:
        problems.append('the Windows verify report does not record the kit instance identity')
    run_id = str(report.get('run_id') or '')
    if not run_id:
        problems.append('the Windows verify report does not record the run identity')
    device_problems = device_facts_problems(report.get('device'))
    problems.extend(device_problems)
    harness_problems, harness, document_path = _read_harness_document(root, report)
    problems.extend(harness_problems)
    if harness is not None:
        problems.extend(_validate_harness_document(document_path.parent, harness, report,
                                                  kit_instance=kit_instance, run_id=run_id,
                                                  expected_source_digest=expected_source_digest, program=program))
    kit_problems = _validate_recorded_kit(report, harness=harness, kit_root=kit_root,
                                         expected_source_digest=expected_source_digest,
                                         expected_program_sha256=program,
                                         expected_source_inputs=expected_source_inputs)
    problems.extend(kit_problems)
    return problems


def device_facts_problems(device):
    """A real device run must name the Windows OS, architecture and build."""
    if not isinstance(device, dict):
        return ['the Windows verify report does not record the device facts']
    problems = []
    if str(device.get('platform') or '').lower() != 'win32':
        problems.append('the run device is not a real Windows host')
    if str(device.get('architecture') or '').upper() != 'AMD64':
        problems.append('the run device architecture is not native x64 (AMD64)')
    os_text = str(device.get('os') or '')
    if 'windows' not in os_text.lower():
        problems.append('the device does not report a Windows OS caption')
    if '64' not in str(device.get('os_architecture') or '') and '64' not in str(device.get('system_type') or ''):
        problems.append('the device does not report a 64-bit system')
    if str(device.get('windows_build') or 'unknown').lower() in ('', 'unknown'):
        problems.append('the device does not report a Windows build')
    if device.get('console_session') is None:
        problems.append('the run device does not record an interactive console session')
    return problems


def _read_harness_document(root, report):
    """The raw harness document the report names, inside this evidence root."""
    problems = []
    relative = report.get('harness_path')
    if not isinstance(relative, str) or not relative:
        return ['the Windows verify report does not name the raw harness document'], None, None
    problem = _bound_evidence_file(root, relative)
    if problem is not None:
        return [problem], None, None
    document_path = root / PurePosixPath(relative)
    try:
        document = read_json(document_path)
    except (OSError, ValueError) as error:
        return [f'the raw harness document is unreadable ({type(error).__name__})'], None, document_path
    if not isinstance(document, dict):
        return ['the raw harness document is not a JSON object'], None, document_path
    recorded = report.get('harness_sha256')
    if not _MEMBER_SHA256.fullmatch(str(recorded or '')):
        problems.append('the Windows verify report does not record the raw harness digest')
    elif sha256_file(document_path) != recorded:
        problems.append('the raw harness document digest changed since the report was written')
    return problems, document, document_path


def _validate_harness_document(anchor, document, report, *, kit_instance, run_id, expected_source_digest, program):
    """The raw harness document itself: identity, device, digests and cases.

    The harness records its own artifact paths relative to the run root it drove
    (the directory the raw ``run.json`` lives in, locally fetched under this
    evidence root), so every reference is resolved against that anchor and may
    never escape it.
    """
    root = Path(anchor)
    problems = []
    if document.get('format') != RUN_FORMAT:
        problems.append('the raw harness document does not carry the current run format')
    if document.get('mode') != 'run':
        problems.append('the raw harness document is not a full engineering run')
    if str(document.get('run_id') or '') != run_id:
        problems.append('the raw harness document run id does not match the verify report')
    host = document.get('host') or {}
    if str(host.get('run_id') or '') != run_id:
        problems.append('the raw harness host record does not carry the same run id')
    if str(host.get('platform') or '').lower() != 'win32':
        problems.append('the raw harness document was not produced on a real Windows host')
    if str(host.get('processor_architecture') or '').upper() != 'AMD64':
        problems.append('the raw harness host architecture is not native x64 (AMD64)')
    machine = host.get('machine') or {}
    if 'windows' not in str(machine.get('Caption') or '').lower():
        problems.append('the raw harness host does not report a Windows caption')
    if 'x64' not in str(machine.get('System') or '') and '64' not in str(machine.get('Arch') or ''):
        problems.append('the raw harness host does not report a 64-bit system')
    if str(host.get('windows_build') or 'unknown').lower() in ('', 'unknown'):
        problems.append('the raw harness host does not report a Windows build')
    if host.get('console_session') is None:
        problems.append('the raw harness host does not record an interactive console session')
    binding = document.get('binding') or {}
    if kit_instance and str(binding.get('instance_id') or '') != kit_instance:
        problems.append('the raw harness binding instance does not match the verify report kit instance')
    expected = document.get('expected') or {}
    if expected.get('program_source_digest') != expected_source_digest:
        problems.append('the raw harness expected the sources of a different program build')
    if program and expected.get('program_sha256') != program:
        problems.append('the raw harness expected a different frozen program')
    if not _MEMBER_SHA256.fullmatch(str(document.get('harness_sha256') or '')):
        problems.append('the raw harness document does not record its harness revision')
    if document.get('runtime_acceptance') != 'PASS' or document.get('checks_failed') != 0:
        problems.append('the raw harness document is not a passing engineering run')
    cases = _harness_case_map(document)
    for case_id in WINDOWS_CASES:
        case = cases.get(case_id)
        if case is None:
            problems.append(f'the raw harness document is missing case {case_id}')
            continue
        if _case_failures(case):
            problems.append(f'{case_id} has failing checks in the raw harness document')
        if not case.get('checks'):
            problems.append(f'{case_id} has no executed checks in the raw harness document')
        if str(case.get('status') or '') != 'PASS':
            problems.append(f'{case_id} did not pass in the raw harness document')
        refs = []
        _evidence_artifact_refs(case.get('evidence') or {}, refs)
        for relative, digest in refs:
            problem = _bound_evidence_file(root, relative, digest)
            if problem is not None:
                problems.append(problem)
        if case_id in ('WN02', 'WN03', 'WN04') \
                and not any('export' in str(relative).lower() for relative, _digest in refs):
            problems.append(f'{case_id} does not retain a bound per-event reconciliation export')
    recorded_cases = report.get('cases')
    derived = {case_id: str(cases[case_id].get('status') or '') for case_id in cases}
    if not isinstance(recorded_cases, dict) or not recorded_cases:
        problems.append('the Windows verify report does not record the case results')
    elif {key: value for key, value in recorded_cases.items() if key in WINDOWS_CASES} != derived:
        problems.append('the Windows verify report case results do not match the raw harness document')
    checks_total = document.get('checks_total')
    if not isinstance(checks_total, int) or checks_total <= 0:
        problems.append('the raw harness document does not record the executed check count')
    elif checks_total != sum(len(case.get('checks') or []) for case in cases.values()):
        problems.append('the raw harness document check count does not match its cases')
    return problems


def _validate_recorded_kit(report, *, harness, kit_root, expected_source_digest, expected_program_sha256,
                           expected_source_inputs):
    """Re-verify the frozen kit the run names, never just its recorded digest."""
    problems = []
    recorded = report.get('kit_root')
    candidates = []
    if kit_root:
        candidates.append(Path(kit_root))
    if isinstance(recorded, str) and recorded:
        candidates.append(Path(recorded))
    if not candidates:
        return ['the Windows verify report does not record the frozen kit it ran']
    chosen = None
    for candidate in candidates:
        if candidate.is_dir() and (candidate / 'integrity.json').is_file():
            chosen = candidate
            break
    if chosen is None:
        return [f'the frozen kit recorded by the Windows run is missing or incomplete: {candidates[-1]}']
    if kit_root and isinstance(recorded, str) and recorded \
            and Path(os.path.abspath(Path(kit_root))) != Path(os.path.abspath(Path(recorded))):
        problems.append('the Windows run was produced from a different frozen kit than the selected preparation')
    problems.extend(verify_kit_strict(chosen, expected_source_digest=expected_source_digest,
                                      expected_program_sha256=expected_program_sha256,
                                      expected_source_inputs=expected_source_inputs))
    integrity_sha = sha256_file(chosen / 'integrity.json')
    if report.get('kit_integrity_sha256') != integrity_sha:
        problems.append('the Windows run kit integrity digest does not match the frozen kit')
    harness_member = chosen / KIT_HARNESS
    harness_revision = str((harness or {}).get('harness_sha256') or '')
    if not _MEMBER_SHA256.fullmatch(harness_revision):
        problems.append('the raw harness document does not record its harness revision')
    elif harness_member.is_file() and sha256_file(harness_member) != harness_revision:
        problems.append('the raw harness document was not produced by the frozen kit harness revision')
    try:
        releases = read_json(chosen / 'releases.json')
        runtime = read_json(chosen / 'operator' / 'runtime.json')
    except (OSError, ValueError):
        releases, runtime = {}, {}
    if report.get('kit_instance_id') != releases.get('instance_id') \
            or report.get('kit_instance_id') != runtime.get('instance_id'):
        problems.append('the Windows run kit instance identity does not match the frozen kit')
    if report.get('program_sha256') != releases.get('program_sha256'):
        problems.append('the Windows run program digest is not the frozen kit program')
    modes = releases.get('modes') or {}
    studies = ((harness or {}).get('binding') or {}).get('studies') or {}
    for mode in MODES:
        entry = studies.get(mode) or {}
        kit_entry = modes.get(mode) or {}
        if not entry:
            problems.append(f'the run binding does not record the {mode} study')
            continue
        if entry.get('release_id') != kit_entry.get('release_id') \
                or entry.get('study_id') != kit_entry.get('study_id') \
                or entry.get('build_id') != kit_entry.get('build_id'):
            problems.append(f'the run {mode} binding does not match the frozen kit release')
        if kit_entry.get('package_sha256') \
                and entry.get('package_sha256') != kit_entry.get('package_sha256'):
            problems.append(f'the run {mode} package digest does not match the frozen kit delivery')
    return problems


def _validate_verify_inputs(kit_root, accounts_path, expected_source_digest, expected_source_inputs=None):
    """Every deterministic check that must finish before anything is started."""
    problems = verify_kit_strict(kit_root, expected_source_digest=expected_source_digest,
                                 expected_source_inputs=expected_source_inputs)
    runtime_path = Path(kit_root) / 'operator' / 'runtime.json'
    instance_id = None
    try:
        runtime = read_json(runtime_path)
    except (OSError, ValueError):
        runtime = None
    if not isinstance(runtime, dict):
        problems.append('kit operator/runtime.json is not a readable JSON object')
    else:
        instance_id = runtime.get('instance_id')
        if runtime.get('format') != RUNTIME_FORMAT:
            problems.append('kit operator/runtime.json is not this runtime format')
        if not runtime.get('port'):
            problems.append('kit operator/runtime.json does not record the frozen API port')
    problems += validate_accounts(accounts_path, kit_root=kit_root, expected_instance_id=instance_id)
    return problems, runtime_path


def _run_kit_locally(kit_root, runtime_path, accounts_path, run_root):
    json_out = run_root / 'run.json'
    command = [sys.executable, str(Path(kit_root) / KIT_HARNESS), '--run', '--kit', str(kit_root),
               '--runtime', str(runtime_path), '--accounts', str(accounts_path),
               '--run-root', str(run_root), '--json-out', str(json_out)]
    result = subprocess.run(command, cwd=str(kit_root), capture_output=True, text=True, timeout=7200)
    document = None
    if json_out.is_file():
        try:
            document = read_json(json_out)
        except (OSError, ValueError):
            document = None
    return result.returncode, document, json_out, result.stdout + '\n' + result.stderr


def restore_prepared_instance(kit_root):
    """Restart the prepared instance from its own data for the real run.

    A preparation never leaves its server running; the Windows run talks to the
    same frozen instance through the scoped tunnel, so the run gate restarts it
    from the recorded data directory, secret and instance id. Nothing here
    re-initializes or rewrites the prepared data.
    """
    prepare_root = Path(kit_root).parent
    runtime = read_json(Path(kit_root) / 'operator' / 'runtime.json')
    data = prepare_root / 'instance-root' / 'instance'
    secret_file, instance_file = data / 'secret', data / 'instance'
    if not secret_file.is_file() or not instance_file.is_file():
        raise KitError('the prepared instance data is missing; re-run --prepare for this kit')
    instance = Instance(prepare_root / 'instance-root')
    instance.secret_key = secret_file.read_text(encoding='utf-8').strip()
    instance.instance_id = str(runtime.get('instance_id') or '')
    if instance.instance_id != instance_file.read_text(encoding='utf-8').strip():
        raise KitError('kit runtime instance id does not match the prepared instance data')
    instance.port = int(runtime.get('port') or 0)
    if instance.port <= 0:
        raise KitError('kit runtime does not record the prepared API port')
    return instance, runtime


def start_reverse_tunnel(config, runtime):
    """The scoped reverse tunnel the harness documents as its prerequisite.

    Only the configured host alias and the recorded loopback ports are used;
    no firewall, DNS or certificate trust is changed.
    """
    tunnel = runtime.get('tunnel') or {}
    listen_port = int(tunnel.get('listen_port') or int(runtime.get('port', 0)) + 100)
    target_port = int(tunnel.get('target_port') or runtime.get('port') or 0)
    command = ['ssh', *_ssh_options(config), '-N', '-o', 'ExitOnForwardFailure=yes',
               '-R', f'127.0.0.1:{listen_port}:127.0.0.1:{target_port}', config['host']]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(3)
    if process.poll() is not None:
        error = ((process.stderr.read() if process.stderr else '') or '')[-400:]
        raise KitError(f'the scoped reverse tunnel exited before the run: {error.strip()}')
    return process, listen_port


def _run_kit_over_ssh(config, kit_root, runtime_path, accounts_path, run_root, evidence_name):
    """Mac -> strict SSH -> real Windows x64; fetch the bound run evidence.

    The kit is transferred as one bundle, expanded into a brand-new remote root,
    driven by the kit's own harness on the device, and the run directory is
    fetched back for local validation. A missing Windows python, a refused
    transfer or a dead tunnel is an explicit failure, never a pass.
    """
    instance, runtime = restore_prepared_instance(kit_root)
    bundle = run_root / 'transfer-bundle.zip'
    with zipfile.ZipFile(bundle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(Path(kit_root).rglob('*')):
            if path.is_file():
                archive.write(path, 'kit/' + path.relative_to(kit_root).as_posix())
        archive.write(accounts_path, 'private/runtime_accounts.json')
    windows_root = config['windows_root'].strip().rstrip('\\/').replace('/', '\\')
    remote_root = f'{windows_root}\\runs\\{evidence_name}'
    remote_kit = f'{remote_root}\\kit'
    remote_private = f'{remote_root}\\private\\runtime_accounts.json'
    remote_run = f'{remote_root}\\run'
    remote_zip = f'{remote_root}\\bundle.zip'
    remote_run_zip = f'{remote_root}\\run.zip'
    python = (os.environ.get(WINDOWS_PYTHON_ENV) or 'python').strip() or 'python'
    instance.start()
    tunnel = None
    try:
        tunnel, listen_port = start_reverse_tunnel(config, runtime)
        prepared = _ssh_run(config, f"New-Item -ItemType Directory -Force -Path '{remote_root}' | Out-Null; "
                                    f"exit 0", 120)
        if prepared.returncode != 0:
            raise KitError(f'the remote run root could not be created (exit {prepared.returncode})')
        upload = _scp_copy(config, bundle, remote_zip, to_remote=True)
        if upload.returncode != 0:
            raise KitError(f'the kit transfer to Windows failed (exit {upload.returncode}): '
                           f'{(upload.stderr or "")[-300:].strip()}')
        expanded = _ssh_run(
            config, f"Expand-Archive -LiteralPath '{remote_zip}' -DestinationPath '{remote_root}' -Force; "
                    f"New-Item -ItemType Directory -Force -Path '{remote_run}' | Out-Null; exit 0", 900)
        if expanded.returncode != 0:
            raise KitError(f'the kit could not be expanded on Windows (exit {expanded.returncode}): '
                           f'{(expanded.stderr or "")[-300:].strip()}')
        harness = f'{remote_kit}\\operator\\harness\\windows_native_harness.py'
        command = (f"& '{python}' '{harness}' --run --kit '{remote_kit}' "
                   f"--runtime '{remote_kit}\\operator\\runtime.json' --accounts '{remote_private}' "
                   f"--run-root '{remote_run}' --json-out '{remote_run}\\run.json'; exit $LASTEXITCODE")
        result = _ssh_run(config, command, 7200)
        (run_root / 'remote_harness.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
        fetched = _ssh_run(config, f"Compress-Archive -Path '{remote_run}\\*' "
                                   f"-DestinationPath '{remote_run_zip}' -Force; exit 0", 900)
        if fetched.returncode == 0:
            download = _scp_copy(config, run_root / 'remote-run.zip', remote_run_zip, to_remote=False)
            if download.returncode == 0 and (run_root / 'remote-run.zip').is_file():
                target = run_root / 'remote'
                target.mkdir(exist_ok=True)
                shutil.unpack_archive(str(run_root / 'remote-run.zip'), str(target))
        document = None
        remote_document = run_root / 'remote' / 'run.json'
        if remote_document.is_file():
            try:
                document = read_json(remote_document)
            except (OSError, ValueError):
                document = None
        (run_root / 'tunnel.json').write_bytes(canonical_json(
            {'listen_port': listen_port, 'target_port': int(runtime.get('port') or 0),
             'remote_root': remote_root, 'exit': result.returncode}))
        return result.returncode, document, remote_document
    finally:
        if tunnel is not None and tunnel.poll() is None:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tunnel.kill()
        instance.stop()


def verify_kit(kit_root, run_root=None, evidence_root=None, accounts_path=None):
    """Run the prepared kit on the real Windows x64 host and gate WN01-WN06.

    On Windows the harness runs directly. On the preparation host the command is
    the Mac orchestration: strict local validation first, then the scoped SSH
    transfer and the real device run, and the fetched raw run document decides
    the verdict. The report written here is the same complete evidence the
    aggregate gate re-reads: format, kit/program/source identities, the raw
    harness document inside this evidence root and every WN01-WN06 case with its
    bound artifacts. A local command, a cross-compile or a missing Windows
    connection configuration is refused with a non-zero code and an exact
    reason; nothing here ever fabricates a device result from ``sys.platform``.
    """
    config = windows_config()
    kit_root = Path(kit_root).resolve() if kit_root else None
    if kit_root is None or not kit_root.is_dir():
        print('--verify needs --kit-root: the prepared kit directory (gep-windows-kit/v2)',
              file=sys.stderr, flush=True)
        return 2, {'verdict': 'refused', 'reason': 'kit_root_missing'}
    root = guard_evidence_root(evidence_root) if evidence_root else new_unique_root(EVIDENCE_BASE)
    run_root = Path(run_root).resolve() if run_root else root / 'windows-run'
    if run_root.exists():
        print(f'refusing an existing run root: {run_root}', file=sys.stderr, flush=True)
        return 2, {'verdict': 'refused', 'reason': 'run_root_exists'}
    if not os.path.abspath(run_root).startswith(os.path.abspath(root) + os.sep):
        # The raw run document is evidence of this root; an external run root
        # would make the recorded relative path point outside its own evidence.
        print(f'refusing a run root outside the evidence root: {run_root}', file=sys.stderr, flush=True)
        return 2, {'verdict': 'refused', 'reason': 'run_root_outside_evidence'}
    run_root.mkdir(parents=True, exist_ok=False)
    expected_source = program_source_digest()
    expected_inputs = program_source_inputs()
    report = {'task': 'p03r11a-windows-verify', 'format': VERIFY_FORMAT, 'verdict': 'failed',
              'kit_root': str(kit_root), 'run_root': str(run_root), 'evidence_root': str(root),
              'platform': sys.platform, 'windows_verified': False,
              'program_source_digest': expected_source,
              'run_at': datetime.now(timezone.utc).isoformat()}
    accounts_path = Path(accounts_path) if accounts_path else Path(
        os.environ.get('GEP_TEST_WINDOWS_ACCOUNTS') or '')
    problems, runtime_path = _validate_verify_inputs(kit_root, accounts_path, expected_source, expected_inputs)
    if problems:
        report['problems'] = problems
        (root / 'verify_report.json').write_bytes(canonical_json(report))
        print(f'WINDOWS VERIFY FAILED :: {problems[:3]}', file=sys.stderr, flush=True)
        return 1, report
    releases = read_json(kit_root / 'releases.json')
    report.update({'program_sha256': releases.get('program_sha256'),
                   'kit_instance_id': releases.get('instance_id'),
                   'kit_integrity_sha256': sha256_file(kit_root / 'integrity.json')})
    if sys.platform.startswith('win'):
        try:
            exit_code, document, harness_path, harness_log = _run_kit_locally(
                kit_root, runtime_path, accounts_path, run_root)
        except (OSError, subprocess.SubprocessError) as error:
            report['problems'] = [f'{type(error).__name__}: {error}']
            (root / 'verify_report.json').write_bytes(canonical_json(report))
            print(f'WINDOWS VERIFY FAILED :: {report["problems"][0]}', file=sys.stderr, flush=True)
            return 1, report
    else:
        if not (config['host'] and config['windows_root']):
            reason = (f'the Mac orchestration needs {SSH_HOST_ENV} and {WINDOWS_ROOT_ENV}; without them no real '
                      'Windows run is attempted and no local command is a Windows pass')
            report['problems'] = [reason]
            report['verdict'] = 'refused'
            report['reason'] = 'ssh_not_configured'
            (root / 'verify_report.json').write_bytes(canonical_json(report))
            print(f'WINDOWS VERIFY REFUSED :: {reason}', file=sys.stderr, flush=True)
            return 2, report
        try:
            exit_code, document, harness_path = _run_kit_over_ssh(config, kit_root, runtime_path, accounts_path,
                                                                  run_root, root.name)
        except (KitError, OSError, subprocess.SubprocessError, ValueError) as error:
            report['problems'] = [f'{type(error).__name__}: {error}']
            (root / 'verify_report.json').write_bytes(canonical_json(report))
            print(f'WINDOWS VERIFY FAILED :: {report["problems"][0]}', file=sys.stderr, flush=True)
            return 1, report
        harness_log = ''
    if harness_log:
        (run_root / 'harness.log').write_text(harness_log, encoding='utf-8')
    cases = _harness_case_map(document)
    relative = None
    if harness_path is not None and Path(harness_path).is_file():
        try:
            relative = Path(harness_path).resolve().relative_to(root).as_posix()
        except ValueError:
            relative = None
    report.update({'harness_exit': exit_code, 'cases': {case: str(cases[case].get('status') or '')
                                                        for case in cases},
                   'harness_document': bool(document),
                   'run_id': (document or {}).get('run_id'),
                   'harness_path': relative,
                   'harness_sha256': sha256_file(Path(harness_path)) if relative else None,
                   'device': device_facts(document),
                   'windows_verified': False})
    # The report is written first so the self-check reads exactly the evidence a
    # later aggregate gate will read; the final verdict is written after it.
    (root / 'verify_report.json').write_bytes(canonical_json(report))
    evidence_problems = _run_report_problems(
        root, report, expected_source_digest=expected_source, expected_program_sha256=releases.get('program_sha256'),
        kit_root=kit_root, expected_source_inputs=expected_inputs, check_flags=False)
    ok = exit_code == 0 and not evidence_problems
    report['evidence_problems'] = evidence_problems
    report['verdict'] = 'ok' if ok else 'failed'
    report['windows_verified'] = bool(ok)
    (root / 'verify_report.json').write_bytes(canonical_json(report))
    final_problems = validate_windows_run_evidence(
        root, expected_source_digest=expected_source,
        expected_program_sha256=releases.get('program_sha256'), kit_root=kit_root,
        expected_source_inputs=expected_inputs) if ok else evidence_problems
    if ok and final_problems:
        # The final on-disk evidence must pass the very same re-read the
        # aggregate gate performs; a discrepancy is a failed verification.
        report['evidence_problems'] = final_problems
        report['verdict'] = 'failed'
        report['windows_verified'] = False
        (root / 'verify_report.json').write_bytes(canonical_json(report))
        ok = False
    print(json.dumps({'verdict': report['verdict'], 'windows_verified': report['windows_verified'],
                      'cases': report['cases'], 'run_root': str(run_root),
                      'evidence_problems': report.get('evidence_problems') or []}, ensure_ascii=False, indent=2))
    return 0 if ok else 1, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true',
                        help='prepare the new frozen Windows kit and human-test package (preparation host)')
    parser.add_argument('--verify', action='store_true',
                        help='run the prepared kit on the real Windows x64 host and gate WN01-WN06')
    parser.add_argument('--kit-root', default=None, help='prepared kit directory (--verify)')
    parser.add_argument('--accounts', default=None,
                        help='private runtime_accounts.json outside the kit (--verify)')
    parser.add_argument('--run-root', default=None, help='brand-new run root (--verify)')
    parser.add_argument('--evidence-root', default=None,
                        help='brand-new unique evidence root inside the project; defaults to the dedicated base')
    args = parser.parse_args(argv)
    if args.prepare and args.verify:
        parser.error('choose exactly one of --prepare or --verify')
    if args.prepare:
        try:
            code, _ = prepare(args.evidence_root)
        except KitError as error:
            print(f'WINDOWS PREPARE REFUSED :: {error}', file=sys.stderr, flush=True)
            print(json.dumps({'task': 'p03r11a-windows-prepare', 'verdict': 'refused', 'ok': False,
                              'error': f'{type(error).__name__}: {error}',
                              'requested_evidence_root': args.evidence_root}, ensure_ascii=False))
            return 2
        return code
    if args.verify:
        code, _ = verify_kit(args.kit_root, args.run_root, args.evidence_root, args.accounts)
        return code
    parser.error('--prepare or --verify is required')


if __name__ == '__main__':
    sys.exit(main())
