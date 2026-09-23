"""Phase 03 R02B/R02BR synthetic-copy migration and Owner-enablement rehearsal.

``--verify`` builds one fresh, unique evidence root and then:

1. creates a synthetic *old-schema* source volume (migrations through 0009, raw
   SQL rows, reference records and a private file set) in that root;
2. takes a consistent copy with the SQLite online backup API and read-only
   private file copies. SQLite ``-wal`` / ``-shm`` / ``-journal`` sidecars are
   never copied: the backup result is self-contained. The source is probed on
   one connection across the copy window (file manifest plus ``data_version``),
   so a committed write - including a WAL-only write that never touches the
   main database file - refuses the copy as an inconsistent set and the tool
   reports the refusal instead of silently mixing states;
3. exercises a real WAL synthetic source (uncheckpointed WAL content while a
   writer stays open) and verifies that the committed WAL data is present in
   the sidecar-free copy, and that a concurrent commit during a second copy is
   refused;
4. migrates only the copy, then checks the additive extension: the stored
   version stays exactly 1, every old user has one Principal, the audit rows are
   backfilled, the legacy creator stays NULL, and every reference record,
   release config, session/event/export binding, instance/Owner identity and
   file digest is identical to the source;
5. runs the real Owner flow on that copy: preview with explicit choices
   (including the deliberate adoption of the fixed v2 set), password +
   revision confirmation, audit-proven exceptions, old invitation/preview
   invalidation, and a repeated confirm that is refused without resetting the
   stored policy;
6. takes a read-only copy of ``local_data/independent_acceptance_20260912``
   (database plus private instance/secret markers only - the 166 MiB
   ``native_run`` payload is not part of schema or difference rehearsal) into the
   same evidence root, migrates that copy and only *reads* the difference: the
   real acceptance Owner is never confirmed or migrated, and the source file
   digest must be byte-identical afterwards.

Every run creates a new ``<UTC stamp>-<random>`` root under
``local_data/phase03_remediation_20260923/p03r02br`` and never overwrites or
cleans an existing root. All Django work happens in a generated step runner so
each volume uses its own ``GEP_DATA_DIR``; only this tool's own evidence root is
ever written. The tool never commits, pushes, deletes or touches a source
volume.
"""
import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = PROJECT_ROOT / 'server'
EVIDENCE_BASE = PROJECT_ROOT / 'local_data' / 'phase03_remediation_20260923' / 'p03r02br'
ACCEPTANCE_SOURCE = PROJECT_ROOT / 'local_data' / 'independent_acceptance_20260912'
REQUIRED_MIGRATION = ['core', '0010_policy_principal']
OLD_SCHEMA_TARGET = ['core', '0009_release_artifact']
SQLITE_SIDECAR_PREFIX = 'gep.sqlite3-'
WAL_MARKER_KEY = 'r02br_wal_marker'
WAL_CONTENTION_KEY = 'r02br_wal_contention'
REFERENCE_TABLES = {
    'core_study': ['id', 'title', 'mode', 'recruitment', 'max_sessions', 'public', 'public_summary',
                   'public_duration', 'public_device_requirements', 'show_closed_summary',
                   'current_release_id', 'revision'],
    'core_build': ['id', 'study_id', 'descriptor', 'digest', 'package_path'],
    'core_release': ['id', 'study_id', 'build_id', 'config', 'approved', 'artifact_path',
                     'artifact_digest', 'artifact_size'],
    'core_participant': ['id', 'study_id', 'code', 'password_hash', 'active', 'expires_at'],
    'core_session': ['id', 'participant_id', 'release_id', 'operation', 'proof_hash', 'request',
                     'token_hash', 'expires_at', 'revoked', 'completion', 'created_at'],
    'core_event': ['id', 'session_id', 'event_id', 'segment_id', 'sequence', 'envelope', 'received_at'],
    'core_export': ['id', 'study_id', 'snapshot', 'created_at'],
}
PRIVATE_FILES = {
    'packages/build_a.bin': b'gep-r02b-synthetic-build:' + b'a' * 96,
    'artifacts/release_a.bin': b'gep-r02b-synthetic-release:' + b'r' * 160,
}
OMITTED_ACCEPTANCE_PAYLOAD = ('native_run', 'packages')


class RehearsalError(Exception):
    pass


# --- generic helpers -------------------------------------------------------

def sha256_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def new_unique_root(base):
    """One fresh ``<UTC stamp>-<random>`` root; never an existing directory."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    for _ in range(20):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        root = base / f'{stamp}-{secrets.token_hex(4)}'
        try:
            root.mkdir(mode=0o700, exist_ok=False)
            return root
        except FileExistsError:
            continue
    raise RehearsalError(f'could not create a unique evidence root under {base}')


def resolve_within(base, relative):
    """Resolve ``relative`` under ``base``; traversal and absolutes are refused."""
    base = Path(base).resolve()
    candidate = (base / relative).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f'path escapes its root: {relative!r}')
    return candidate


def write_private(path, payload, root):
    path = resolve_within(root, path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
    return path


def connect_readonly(path):
    return sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)


def table_exists(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                              (name,)).fetchone() is not None


def column_names(connection, table):
    if not table_exists(connection, table):
        return set()
    return {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}


def is_sqlite_sidecar(relative):
    """True for a source SQLite ``-wal`` / ``-shm`` / ``-journal`` sidecar."""
    return Path(relative).name.startswith(SQLITE_SIDECAR_PREFIX)


def file_manifest(root):
    """Every protected regular file under ``root`` with size and digest.

    SQLite sidecars are explicitly excluded: they are never copied (the online
    backup already carries their committed content) and their churn must not be
    reported as a changed protected file. The database's own consistency probe
    covers WAL-only commits instead. Symlinks are refused, never followed.
    """
    root = Path(root)
    manifest = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise RehearsalError(f'refusing symlink in a protected volume: {path}')
        relative = str(path.relative_to(root))
        if is_sqlite_sidecar(relative):
            continue
        if path.is_file():
            manifest[relative] = {'size': path.stat().st_size, 'sha256': sha256_file(path)}
    return manifest


def sidecars(root):
    """Present SQLite sidecar file names under ``root`` (never part of a copy)."""
    root = Path(root)
    return sorted(path.name for path in root.iterdir()
                  if path.is_file() and is_sqlite_sidecar(path.name))


def _data_version(connection):
    """``PRAGMA data_version`` on one long-lived connection (or ``None``)."""
    if connection is None:
        return None
    return connection.execute('PRAGMA data_version').fetchone()[0]


def inspect_database(path):
    connection = connect_readonly(path)
    try:
        reference = {}
        for table, columns in REFERENCE_TABLES.items():
            if not table_exists(connection, table):
                reference[table] = {'count': 0, 'missing': True}
                continue
            available = column_names(connection, table)
            missing = [column for column in columns if column not in available]
            if missing:
                count = connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                reference[table] = {'count': count, 'missing_columns': missing}
                continue
            rows = [list(row) for row in connection.execute(
                f'SELECT {", ".join(columns)} FROM {table} ORDER BY 1')]
            reference[table] = {
                'count': len(rows),
                'identity_sha256': sha256_text(json.dumps([str(row[0]) for row in rows])),
                'content_sha256': sha256_text(json.dumps(rows, default=str, separators=(',', ':'))),
            }
        users = [list(row) for row in connection.execute(
            'SELECT id, username, is_active, is_staff, is_superuser FROM auth_user ORDER BY id')]
        instance_columns = [column for column in ('id', 'instance_id', 'owner_id', 'governance_revision')
                            if column in column_names(connection, 'core_instance')]
        instance_row = connection.execute(
            f'SELECT {", ".join(instance_columns)} FROM core_instance').fetchone() if instance_columns else None
        instance = dict(zip(instance_columns, instance_row)) if instance_row else None
        grants = [list(row) for row in connection.execute(
            'SELECT user_id, study_id, action, delegable FROM core_grant ORDER BY 1, 2, 3')]
        audits = [list(row) for row in connection.execute(
            'SELECT id, study_id, actor_id, action, target FROM core_audit ORDER BY id')]
        migrations = [list(row) for row in connection.execute('SELECT app, name FROM django_migrations ORDER BY id')]
        data = {
            'integrity_check': connection.execute('PRAGMA integrity_check').fetchone()[0],
            'foreign_key_violations': [list(row) for row in connection.execute('PRAGMA foreign_key_check')],
            'reference': reference,
            'users': users,
            'instance': {'id': instance['id'], 'instance_id': str(instance['instance_id']),
                         'owner_id': instance['owner_id'],
                         'governance_revision': instance.get('governance_revision')} if instance else None,
            'grants_sha256': sha256_text(json.dumps(grants)),
            'grants_count': len(grants),
            'audits_sha256': sha256_text(json.dumps(audits)),
            'migrations': migrations,
            'columns': {table: sorted(column_names(connection, table)) for table in
                        sorted(set(REFERENCE_TABLES) | {'core_instance', 'core_accountprofile', 'core_principal',
                                                        'core_audit', 'core_grant'})},
        }
        return data
    finally:
        connection.close()


# --- synthetic old-schema source and consistent copy -----------------------

STEP_HARNESS = '''\
"""Generated R02B rehearsal step runner; all Django work uses GEP_DATA_DIR."""
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


def context():
    return json.loads(Path(os.environ['GEP_STEP_CONTEXT']).read_text())


def setup():
    import django
    django.setup()


def _stamp(now, minutes=0):
    return (now + timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S.%f')


def seed_old_source():
    ctx = context()
    setup()
    from django.contrib.auth.hashers import make_password
    volume = Path(os.environ['GEP_DATA_DIR'])
    now = datetime.now(timezone.utc)
    connection = sqlite3.connect(volume / 'gep.sqlite3')
    try:
        for user in ctx['users']:
            connection.execute(
                'INSERT INTO auth_user (id, password, last_login, is_superuser, username, first_name, last_name,'
                ' email, is_staff, is_active, date_joined) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (user['id'], make_password(ctx['owner_password']), _stamp(now, -600), user['is_superuser'],
                 user['username'], '', '', '', user['is_staff'], user['is_active'], _stamp(now, -600)))
            connection.execute(
                'INSERT INTO core_accountprofile (id, user_id, role, must_change_password, auth_version, revision)'
                ' VALUES (?,?,?,?,?,?)', (user['id'], user['id'], user['role'], 0, 1, 0))
        connection.execute(
            'INSERT INTO core_instance (id, instance_id, owner_id, governance_revision) VALUES (1,?,?,0)',
            (ctx['instance_id'], ctx['users'][0]['id']))
        for key, title in (('a', 'Synthetic source study A'), ('b', 'Synthetic source study B')):
            connection.execute(
                'INSERT INTO core_study (id, title, mode, recruitment, max_sessions, public, public_summary,'
                ' public_duration, public_device_requirements, show_closed_summary, current_release_id, revision)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?)',
                (ctx['studies'][key], title, 'anonymous' if key == 'a' else 'id', 'paused', 1, 0, '', '', '', 0, 0))
        connection.execute(
            'INSERT INTO core_build (id, study_id, descriptor, digest, package_path) VALUES (?,?,?,?,?)',
            (ctx['build_id'], ctx['studies']['a'], json.dumps({'version': '1.0.0', 'synthetic': True}),
             ctx['build_digest'], 'packages/build_a.bin'))
        connection.execute(
            'INSERT INTO core_release (id, study_id, build_id, config, approved, artifact_path, artifact_digest,'
            ' artifact_size) VALUES (?,?,?,?,1,?,?,?)',
            (ctx['release_id'], ctx['studies']['a'], ctx['build_id'], json.dumps({'public': False}),
             'artifacts/release_a.bin', ctx['artifact_digest'], ctx['artifact_size']))
        connection.execute(
            'INSERT INTO core_study (id, title, mode, recruitment, max_sessions, public, public_summary,'
            ' public_duration, public_device_requirements, show_closed_summary, current_release_id, revision)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?)',
            (ctx['deleted_probe_study'], 'Synthetic source study C', 'anonymous', 'closed', 1, 0, '', '', '', 0, 0))
        connection.execute(
            'UPDATE core_study SET current_release_id=? WHERE id=?', (ctx['release_id'], ctx['studies']['a']))
        connection.execute(
            'INSERT INTO core_participant (id, study_id, code, password_hash, active, expires_at)'
            ' VALUES (?,?,?,?,1,NULL)', (ctx['participant_id'], ctx['studies']['a'], '001', ''))
        connection.execute(
            'INSERT INTO core_session (id, participant_id, release_id, operation, proof_hash, request,'
            ' token_hash, expires_at, revoked, completion, created_at) VALUES (?,?,?,?,?,?,?,?,0,?,?)',
            (ctx['session_id'], ctx['participant_id'], ctx['release_id'], ctx['operation'],
             'd' * 64, json.dumps({'mode': 'id', 'code': '001'}), 'e' * 64,
             _stamp(now, 60), json.dumps({'complete': True, 'event_count': 2}), _stamp(now, -30)))
        for index, event_id in enumerate(ctx['event_ids']):
            connection.execute(
                'INSERT INTO core_event (id, session_id, event_id, segment_id, sequence, envelope, received_at)'
                ' VALUES (?,?,?,?,?,?,?)',
                (index + 1, ctx['session_id'], event_id, ctx['segment_id'], index,
                 json.dumps({'event_type': 'synthetic', 'sequence': index}), _stamp(now, -20)))
        connection.execute(
            'INSERT INTO core_export (id, study_id, snapshot, created_at) VALUES (?,?,?,?)',
            (ctx['export_id'], ctx['studies']['a'], json.dumps({'synthetic': True, 'count': 2}), _stamp(now, -10)))
        for grant in ctx['grants']:
            connection.execute(
                'INSERT INTO core_grant (id, user_id, study_id, action, delegable) VALUES (?,?,?,?,?)',
                (grant['id'], grant['user_id'], grant['study'], grant['action'], grant['delegable']))
        for audit in ctx['audits']:
            connection.execute(
                'INSERT INTO core_audit (id, study_id, actor_id, action, target, before, after, created_at)'
                ' VALUES (?,?,?,?,?,?,?,?)',
                (audit['id'], audit.get('study'), audit.get('actor'), audit['action'], audit['target'],
                 json.dumps(audit.get('before')) if audit.get('before') is not None else None,
                 json.dumps(audit.get('after')) if audit.get('after') is not None else None, _stamp(now, -5)))
        connection.execute(
            'INSERT INTO core_accountinvitation (id, issuer_id, token_hash, username, role, expires_at,'
            ' consumed, revoked) VALUES (?,?,?,?,?,?,0,0)',
            (ctx['account_invitation_id'], ctx['users'][0]['id'], 'f' * 64, 'synthetic_pending', 'user',
             _stamp(now, 60)))
        connection.execute(
            'INSERT INTO core_invitation (id, study_id, issuer_id, token_hash, username, actions, expires_at,'
            ' consumed, revoked) VALUES (?,?,?,?,?,?,?,0,0)',
            (ctx['study_invitation_id'], ctx['studies']['a'], ctx['users'][0]['id'], '0' * 64,
             'synthetic_member', json.dumps(['study.view']), _stamp(now, 60)))
        connection.execute(
            'INSERT INTO core_permissionpreview (id, actor_id, kind, scope, summary, errors, binding,'
            ' base_revision, staged, expires_at, consumed, result, created_at)'
            ' VALUES (?,?,?,?,?,?,?,?,?,?,0,NULL,?)',
            (ctx['pending_preview_id'], ctx['users'][0]['id'], 'matrix', '', '{}', '[]', '1' * 64, 0,
             '{}', _stamp(now, 30), _stamp(now, -1)))
        connection.commit()
    finally:
        connection.close()
    print(json.dumps({'seeded': True, 'users': len(ctx['users']), 'studies': 3}))


def verify_extension():
    """Additive-extension invariants on the migrated copy."""
    setup()
    ctx = context()
    from django.contrib.auth import get_user_model
    from core import access, governance_migration
    from core.models import AccountProfile, Audit, Grant, Instance, Principal, Study
    instance = Instance.objects.get(pk=1)
    report = {
        'authorization_version': governance_migration.instance_version(instance),
        'instance_id': str(instance.instance_id), 'owner_id': instance.owner_id,
        'users': get_user_model().objects.count(), 'principals': Principal.objects.count(),
        'audits_with_actor': Audit.objects.filter(actor__isnull=False).count(),
        'audits_backfilled': Audit.objects.filter(actor__isnull=False, actor_principal__isnull=False).count(),
        'device_audits': Audit.objects.filter(actor__isnull=True).count(),
        'creator_principal_null': not Study.objects.exclude(creator_principal__isnull=True).exists(),
        'profile_policy_versions': sorted(set(AccountProfile.objects.values_list('policy_version', flat=True))),
        'overrides_empty': not AccountProfile.objects.exclude(platform_overrides={}, study_overrides={}).exists(),
        'future_null': not AccountProfile.objects.exclude(future_study_actions__isnull=True).exists(),
        'grants': Grant.objects.count(),
    }
    # The documented v1 rule, computed from raw grants (expected side).
    expected = {}
    for user_id, study_id, action in Grant.objects.values_list('user_id', 'study_id', 'action'):
        expected.setdefault((user_id, str(study_id)), set()).add(action)
    mismatch = []
    for (user_id, study_id), actions in sorted(expected.items()):
        visible = 'study.view' in actions
        user = get_user_model().objects.get(pk=user_id)
        study = Study.objects.get(pk=study_id)
        for action in ('study.view', 'data.export_raw', 'member.manage', 'build.upload'):
            want = bool(user.is_active) and visible and (action in actions or action == 'study.view')
            if action not in actions and action != 'study.view':
                want = False
            got = access.allowed(user, study, action)
            if bool(got) != bool(want):
                mismatch.append({'user': user_id, 'study': study_id, 'action': action, 'want': want, 'got': bool(got)})
    report['legacy_mismatches'] = mismatch
    report['legacy_decisions'] = len(expected) * 4
    print(json.dumps(report, sort_keys=True))


def enable():
    """The real Owner flow on the synthetic copy (preview -> confirm -> repeat)."""
    setup()
    ctx = context()
    from django.contrib.auth.hashers import check_password
    from django.utils import timezone
    from core import access, governance_migration
    from core.models import (AccountInvitation, AccountProfile, Audit, Instance,
                             Invitation, PermissionPreview, Study)
    from core.protocol import Rejected
    instance = Instance.objects.get(pk=1)
    owner = instance.owner
    report = {'version_before': governance_migration.instance_version(instance),
              'password_matches': check_password(ctx['owner_password'], owner.password)}
    diff = governance_migration.read_diff(instance=instance)
    report['unknown'] = len(diff['unknown'])
    report['exceptions'] = len(diff['exceptions'])
    report['subjects'] = len(diff['subjects'])
    kinds = {}
    for item in diff['unknown']:
        kinds[item['kind']] = kinds.get(item['kind'], 0) + 1
    report['unknown_kinds'] = kinds
    # Explicit Owner choices: deliberately adopt the fixed v2 set for the
    # legacy Admin (existing studies and future studies); everything else keeps
    # the conservative first choice and never expands silently.
    choices = {}
    for item in diff['unknown']:
        if item['kind'] in ('admin_missing_permissions', 'admin_future_default'):
            choices[item['id']] = 'adopt_v2'
        else:
            choices[item['id']] = item['choices'][0]
    report['choices'] = choices
    row = governance_migration.preview_enablement(owner, choices)
    preview_summary = row.summary
    report['preview_kind'] = row.kind
    report['preview_digest'] = preview_summary['digest']
    report['preview_choices'] = preview_summary['choices']
    result = governance_migration.confirm_enablement(owner, ctx['owner_password'],
                                                    instance.governance_revision, row.pk)
    instance.refresh_from_db()
    report['version_after'] = governance_migration.instance_version(instance)
    report['revision_after'] = instance.governance_revision
    report['applied_overrides'] = len(result['overrides'])
    report['applied_future_defaults'] = len(result['future_defaults'])
    report['confirm_overrides'] = result['overrides']
    try:
        governance_migration.confirm_enablement(owner, ctx['owner_password'], instance.governance_revision, row.pk)
        report['repeat'] = 'unexpected-success'
    except Rejected as error:
        report['repeat'] = error.code
    admin_profile = AccountProfile.objects.get(user__username='synthetic_admin')
    study_a = Study.objects.get(pk=ctx['studies']['a'])
    study_b = Study.objects.get(pk=ctx['studies']['b'])
    frozen = admin_profile.study_overrides.get(str(study_a.pk))
    report['exception_frozen'] = bool(frozen) and 'data.export_raw' not in frozen and 'study.view' in frozen
    admin_user = admin_profile.user
    policy_admin = access.canonical_policy(admin_user)
    expected_a = next(row for row in preview_summary['subjects']
                      if row['username'] == 'synthetic_admin')['studies']
    expected_a = next(row for row in expected_a if row['study'] == str(study_a.pk))['actions']
    actual_a = sorted(action for action in access.STUDY_V2_ACTIONS
                      if access.allowed(admin_user, study_a, action, version=2, policy=policy_admin))
    expected_b = next(row for row in preview_summary['subjects']
                      if row['username'] == 'synthetic_admin')['studies']
    expected_b = next(row for row in expected_b if row['study'] == str(study_b.pk))['actions']
    actual_b = sorted(action for action in access.STUDY_V2_ACTIONS
                      if access.allowed(admin_user, study_b, action, version=2, policy=policy_admin))
    report['preview_matches_confirm'] = (expected_a == actual_a and expected_b == actual_b)
    report['admin_study_a_actions'] = actual_a
    report['admin_study_b_actions'] = actual_b
    report['admin_future_default'] = admin_profile.future_study_actions
    report['old_invitations_revoked'] = (not AccountInvitation.objects.filter(revoked=False).exists()
                                         and not Invitation.objects.filter(revoked=False).exists())
    report['pending_previews_left'] = PermissionPreview.objects.filter(
        consumed=False, expires_at__gt=timezone.now()).count()
    report['audit'] = {'enabled': Audit.objects.filter(action='policy.v2_enabled').count(),
                       'override_frozen': Audit.objects.filter(action='policy.v2_override_frozen').count(),
                       'future_default': Audit.objects.filter(action='policy.v2_future_default_set').count()}
    member = AccountProfile.objects.get(user__username='synthetic_member').user
    policy_member = access.canonical_policy(member)
    policy_owner = access.canonical_policy(owner)
    report['v2_owner_all'] = all(access.allowed(owner, study_a, action, version=2, policy=policy_owner)
                                 for action in ('study.view', 'data.export_raw', 'study.delete'))
    report['v2_admin_gained'] = access.allowed(admin_user, study_a, 'build.upload', version=2, policy=policy_admin)
    report['v2_admin_export_revoked'] = not access.allowed(admin_user, study_a, 'data.export_raw', version=2,
                                                           policy=policy_admin)
    report['v2_member_unchanged'] = (access.allowed(member, study_a, 'build.preview', version=2, policy=policy_member)
                                     and not access.allowed(member, study_a, 'data.export_raw', version=2,
                                                            policy=policy_member))
    inactive_user = AccountProfile.objects.get(user__username='synthetic_inactive').user
    report['v2_inactive_denied'] = not access.allowed(inactive_user, study_b, 'study.view', version=2,
                                                      policy=access.canonical_policy(inactive_user))
    print(json.dumps(report, sort_keys=True))


def acceptance_diff():
    """Migrate the read-only acceptance copy and only *read* its difference."""
    setup()
    from core import governance_migration
    from core.models import Instance, Study
    instance = Instance.objects.get(pk=1)
    report = {'version': governance_migration.instance_version(instance),
              'subjects': 0, 'studies': Study.objects.count(), 'unknown': 0,
              'creator_unknowns': 0, 'exceptions': 0, 'creator_principal_null': True,
              'preview_kind': '', 'still_v1': False, 'confirmed': False}
    diff = governance_migration.read_diff(instance=instance)
    report['subjects'] = len(diff['subjects'])
    report['unknown'] = len(diff['unknown'])
    report['creator_unknowns'] = len([item for item in diff['unknown'] if item['kind'] == 'legacy_creator'])
    report['exceptions'] = len(diff['exceptions'])
    report['creator_principal_null'] = not Study.objects.exclude(creator_principal__isnull=True).exists()
    choices = {item['id']: item['choices'][0] for item in diff['unknown']}
    row = governance_migration.preview_enablement(instance.owner, choices)
    report['preview_kind'] = row.kind
    instance.refresh_from_db()
    report['still_v1'] = governance_migration.instance_version(instance) == 1
    report['confirmed'] = False  # the real acceptance Owner is never confirmed
    print(json.dumps(report, sort_keys=True))


COMMANDS = {'seed-old-source': seed_old_source, 'verify-extension': verify_extension,
            'enable': enable, 'acceptance-diff': acceptance_diff}


def main():
    command = sys.argv[1]
    if command not in COMMANDS:
        raise SystemExit(f'unknown step: {command}')
    COMMANDS[command]()


if __name__ == '__main__':
    main()
'''


def harness_path(root):
    return write_private('workflow/django_step.py', STEP_HARNESS.encode('utf-8'), root)


def run_step(command, volume, secret, context_path, harness):
    environment = dict(os.environ)
    environment.update({
        'DJANGO_SETTINGS_MODULE': 'gep.settings', 'GEP_DATA_DIR': str(volume),
        'GEP_SECRET_KEY': secret, 'GEP_STEP_CONTEXT': str(context_path),
        'PYTHONPATH': str(SERVER_DIR),
    })
    result = subprocess.run([sys.executable, str(harness), command], cwd=PROJECT_ROOT, env=environment,
                            capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RehearsalError(f'step {command} failed ({result.returncode}): '
                             f'{result.stdout[-2000:]}{result.stderr[-2000:]}')
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise RehearsalError(f'step {command} produced no readable JSON: {error}') from None


def run_manage(volume, secret, *arguments, timeout=600):
    environment = dict(os.environ)
    environment.update({
        'DJANGO_SETTINGS_MODULE': 'gep.settings', 'GEP_DATA_DIR': str(volume),
        'GEP_SECRET_KEY': secret, 'PYTHONPATH': str(SERVER_DIR),
    })
    result = subprocess.run([sys.executable, str(SERVER_DIR / 'manage.py'), *arguments],
                            cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RehearsalError(f'manage.py {" ".join(arguments)} failed: '
                             f'{result.stdout[-2000:]}{result.stderr[-2000:]}')


def snapshot_database(source_db, destination_db, *, connection=None):
    """SQLite online backup into a fresh file; an existing connection is reused."""
    destination_db = Path(destination_db)
    descriptor = os.open(destination_db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    source = connection or connect_readonly(source_db)
    try:
        target = sqlite3.connect(destination_db)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        if connection is None:
            source.close()


def copy_consistent(source, destination, include=None, on_start=None):
    """SQLite online backup + read-only private copies with a stable manifest.

    SQLite sidecars are never copied: the backup carries their committed
    content, so the result is self-contained. Consistency is checked on one
    source connection across the whole copy window: the protected file manifest
    must be identical before and after, and ``PRAGMA data_version`` must not
    change, which also catches a WAL-only commit that never touches the main
    database file. Any change refuses the copy (no bare copy of an active
    database, no inconsistent set). ``include`` restricts the copy to the named
    top-level entries (used for the acceptance volume, whose 166 MiB
    ``native_run`` payload is not part of schema or difference rehearsal);
    ``on_start`` runs after the copy window opens and is used by the rehearsal
    to inject a deterministic concurrent commit.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    source_db = source / 'gep.sqlite3'
    connection = connect_readonly(source_db) if source_db.is_file() else None
    try:
        manifest_before = file_manifest(source)
        data_version_before = _data_version(connection)
        skipped_sidecars = sidecars(source)
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        if on_start is not None:
            on_start()
        copied = {}
        for relative in sorted(manifest_before):
            top = relative.split('/', 1)[0]
            if include is not None and top not in include:
                continue
            if relative == 'gep.sqlite3':
                snapshot_database(source / relative, destination / relative, connection=connection)
            else:
                payload = (source / relative).read_bytes()
                write_private(relative, payload, destination)
            copied[relative] = sha256_file(destination / relative)
        manifest_after = file_manifest(source)
        data_version_after = _data_version(connection)
        copied_sidecars = sidecars(destination)
    finally:
        if connection is not None:
            connection.close()
    if manifest_before != manifest_after:
        changed = sorted(name for name in set(manifest_before) | set(manifest_after)
                         if manifest_before.get(name) != manifest_after.get(name))
        raise RehearsalError(f'source changed while copying; inconsistent file set: {changed}')
    if data_version_before != data_version_after:
        raise RehearsalError('source committed a change while copying (WAL data_version moved); '
                             'inconsistent file set; retry the copy')
    if copied_sidecars:
        raise RehearsalError(f'the copy must be self-contained; sidecars appeared: {copied_sidecars}')
    return {'manifest': manifest_before, 'copied': copied, 'sidecars_skipped': skipped_sidecars,
            'data_version': {'before': data_version_before, 'after': data_version_after},
            'database': {'source_sha256': manifest_before.get('gep.sqlite3', {}).get('sha256'),
                         'copy_sha256': sha256_file(destination / 'gep.sqlite3')}}


# --- the verification workflow ---------------------------------------------

def build_synthetic_source(root, harness):
    source = resolve_within(root, 'synthetic_source')
    source.mkdir(mode=0o700)
    secret = secrets.token_urlsafe(48)
    write_private('secret', secret.encode('utf-8'), source)
    files = {name: PRIVATE_FILES[name] for name in sorted(PRIVATE_FILES)}
    digests = {}
    for name, payload in files.items():
        write_private(name, payload, source)
        digests[name] = hashlib.sha256(payload).hexdigest()
    run_manage(source, secret, 'migrate', 'core', OLD_SCHEMA_TARGET[1], '--noinput', '--verbosity', '0')
    context = {
        'owner_password': secrets.token_urlsafe(24),
        'users': [
            {'id': 1, 'username': 'synthetic_owner', 'role': 'user', 'is_active': 1, 'is_staff': 0, 'is_superuser': 0},
            {'id': 2, 'username': 'synthetic_admin', 'role': 'admin', 'is_active': 1, 'is_staff': 0, 'is_superuser': 0},
            {'id': 3, 'username': 'synthetic_member', 'role': 'user', 'is_active': 1, 'is_staff': 0, 'is_superuser': 0},
            {'id': 4, 'username': 'synthetic_inactive', 'role': 'user', 'is_active': 0, 'is_staff': 0, 'is_superuser': 0},
        ],
        'instance_id': uuid.uuid4().hex,
        'studies': {'a': uuid.uuid4().hex, 'b': uuid.uuid4().hex},
        'deleted_probe_study': uuid.uuid4().hex,
        'build_id': uuid.uuid4().hex, 'release_id': uuid.uuid4().hex,
        'participant_id': uuid.uuid4().hex, 'session_id': uuid.uuid4().hex,
        'segment_id': uuid.uuid4().hex, 'operation': uuid.uuid4().hex,
        'event_ids': [uuid.uuid4().hex, uuid.uuid4().hex],
        'export_id': uuid.uuid4().hex,
        'account_invitation_id': uuid.uuid4().hex,
        'study_invitation_id': uuid.uuid4().hex,
        'pending_preview_id': uuid.uuid4().hex,
        'build_digest': sha256_text('synthetic build digest'),
        'artifact_digest': digests['artifacts/release_a.bin'],
        'artifact_size': len(files['artifacts/release_a.bin']),
        'grants': [
            {'id': 1, 'user_id': 2, 'study': 'STUDY_A', 'action': 'study.view', 'delegable': 0},
            {'id': 2, 'user_id': 2, 'study': 'STUDY_A', 'action': 'data.export_raw', 'delegable': 1},
            {'id': 3, 'user_id': 2, 'study': 'STUDY_A', 'action': 'member.manage', 'delegable': 0},
            {'id': 4, 'user_id': 3, 'study': 'STUDY_A', 'action': 'study.view', 'delegable': 0},
            {'id': 5, 'user_id': 3, 'study': 'STUDY_A', 'action': 'build.preview', 'delegable': 0},
            {'id': 6, 'user_id': 4, 'study': 'STUDY_B', 'action': 'study.view', 'delegable': 0},
        ],
        'audits': [
            {'id': 1, 'study': 'STUDY_A', 'actor': 1, 'action': 'permission.matrix_changed', 'target': '2:STUDY_A',
             'before': {'actions': {'data.export_raw': {'granted': True, 'delegable': True}}},
             'after': {'actions': {'data.export_raw': {'granted': False, 'delegable': True}}}},
            {'id': 2, 'study': None, 'actor': None, 'action': 'recovery.named_redeemed', 'target': 'device'},
            {'id': 3, 'study': 'STUDY_B', 'actor': 1, 'action': 'release.published', 'target': 'SYNTHETIC'},
        ],
    }
    for grant in context['grants']:
        grant['study'] = context['studies']['a' if grant['study'] == 'STUDY_A' else 'b']
    for audit in context['audits']:
        if audit.get('study'):
            audit['study'] = context['studies']['a' if audit['study'] == 'STUDY_A' else 'b']
        if audit.get('study') in (context['studies']['a'], context['studies']['b']) and audit['action'] == 'permission.matrix_changed':
            audit['target'] = f"2:{context['studies']['a']}"
    write_private('instance', context['instance_id'].encode('utf-8'), source)
    write_json(resolve_within(root, 'workflow/source_context.json'), context)
    seeded = run_step('seed-old-source', source, secret, resolve_within(root, 'workflow/source_context.json'), harness)
    write_json(resolve_within(root, 'workflow/source_seed.json'), seeded)
    return source, secret, context


def wal_consistency_checks(root, source):
    """Real WAL source: self-contained copy and refusal on a concurrent commit.

    A writer connection stays open with ``wal_autocheckpoint=0`` so committed
    rows live in the source ``-wal`` while the main database file stays
    byte-identical. The copy must carry that committed data without copying the
    sidecars; a second copy whose copy window contains a real concurrent commit
    must be refused, and the refusal must be provable as a WAL/data_version
    change (the main file hash stays unchanged).
    """
    checks = {}
    source_db = source / 'gep.sqlite3'
    writer = sqlite3.connect(source_db)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute('INSERT OR REPLACE INTO core_throttle (key, window, count) VALUES (?,?,?)',
                   (WAL_MARKER_KEY, 1, 1))
    writer.commit()
    refusal = None
    try:
        main_before = sha256_file(source_db)
        wal_copy = resolve_within(root, 'wal_copy')
        report = copy_consistent(source, wal_copy)
        main_after = sha256_file(source_db)
        checks['wal_write_does_not_touch_main_file'] = main_before == main_after
        checks['wal_sidecars_excluded'] = {'gep.sqlite3-wal', 'gep.sqlite3-shm'} <= set(report['sidecars_skipped'])
        checks['wal_copy_has_no_sidecars'] = sidecars(wal_copy) == []
        connection = connect_readonly(wal_copy / 'gep.sqlite3')
        try:
            marker = connection.execute('SELECT count FROM core_throttle WHERE key=?',
                                        (WAL_MARKER_KEY,)).fetchone()
            checks['wal_committed_data_in_copy'] = marker == (1,)
            checks['wal_copy_integrity'] = connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        finally:
            connection.close()

        def commit_during_copy():
            writer.execute('INSERT OR REPLACE INTO core_throttle (key, window, count) VALUES (?,?,?)',
                           (WAL_CONTENTION_KEY, 1, 1))
            writer.commit()

        contention_copy = resolve_within(root, 'wal_contention')
        try:
            copy_consistent(source, contention_copy, on_start=commit_during_copy)
        except RehearsalError as error:
            refusal = str(error)
        checks['wal_concurrent_change_refused'] = refusal is not None
        checks['wal_contention_seen_without_main_file_change'] = sha256_file(source_db) == main_after
        checks['wal_refusal_names_change'] = bool(refusal) and 'inconsistent file set' in refusal
        write_json(resolve_within(root, 'wal_report.json'),
                   {'checks': checks, 'refusal': refusal, 'source_sidecars': sidecars(source),
                    'main_file_stable': main_after == main_before})
    finally:
        writer.close()
    return checks


def verify_workflow(root, harness):
    checks = {}
    source, secret, context = build_synthetic_source(root, harness)
    source_inspect_before = inspect_database(source / 'gep.sqlite3')
    source_files_before = file_manifest(source)

    copy = resolve_within(root, 'synthetic_copy')
    copy_report = copy_consistent(source, copy)
    source_inspect_after = inspect_database(source / 'gep.sqlite3')
    source_files_after = file_manifest(source)

    checks['source_database_stable'] = source_inspect_before == source_inspect_after
    checks['source_files_stable'] = source_files_before == source_files_after
    checks['old_schema_before_extension'] = OLD_SCHEMA_TARGET in source_inspect_before['migrations'] and \
        REQUIRED_MIGRATION not in source_inspect_before['migrations']
    checks['copy_database_logically_equal'] = (
        {key: source_inspect_before[key] for key in ('reference', 'users', 'instance', 'grants_sha256',
                                                     'grants_count', 'audits_sha256', 'integrity_check')} ==
        {key: inspect_database(copy / 'gep.sqlite3')[key] for key in ('reference', 'users', 'instance', 'grants_sha256',
                                                                     'grants_count', 'audits_sha256', 'integrity_check')})
    # The online backup rewrites the page image, so byte equality is not
    # expected for the database; every other private file must be identical.
    copy_files_equal = {name: digest for name, digest in copy_report['copied'].items() if name != 'gep.sqlite3'} == {
        name: value['sha256'] for name, value in source_files_before.items()
        if name in copy_report['copied'] and name != 'gep.sqlite3'}
    checks['copy_files_equal'] = copy_files_equal

    run_manage(copy, secret, 'migrate', '--noinput', '--verbosity', '0')
    extension = run_step('verify-extension', copy, secret, resolve_within(root, 'workflow/source_context.json'), harness)
    copy_inspect = inspect_database(copy / 'gep.sqlite3')
    checks['required_migration_applied'] = REQUIRED_MIGRATION in copy_inspect['migrations']
    checks['authorization_version_stays_one'] = extension['authorization_version'] == 1
    checks['principal_per_old_user'] = extension['principals'] == extension['users'] == len(context['users'])
    checks['audit_backfilled'] = extension['audits_backfilled'] == extension['audits_with_actor'] == 2
    checks['device_audit_without_principal'] = extension['device_audits'] == 1
    checks['legacy_creator_null'] = extension['creator_principal_null'] is True
    checks['profile_defaults'] = extension['profile_policy_versions'] == [2] and extension['overrides_empty'] \
        and extension['future_null']
    checks['legacy_decisions_unchanged'] = not extension['legacy_mismatches']
    checks['instance_owner_unchanged'] = source_inspect_before['instance'] == copy_inspect['instance']
    for table in ('core_study', 'core_build', 'core_release', 'core_participant', 'core_session',
                  'core_event', 'core_export'):
        checks[f'reference_{table}_unchanged'] = source_inspect_before['reference'][table] == \
            copy_inspect['reference'][table]
    checks['release_config_unchanged'] = source_inspect_before['reference']['core_release'] == \
        copy_inspect['reference']['core_release']
    checks['file_digests_unchanged'] = all(
        copy_report['copied'][name] == source_files_after[name]['sha256']
        for name in copy_report['copied'] if name != 'gep.sqlite3')
    checks['copy_database_backup_api'] = copy_report['database']['copy_sha256'] is not None
    checks.update(wal_consistency_checks(root, source))

    enablement = run_step('enable', copy, secret, resolve_within(root, 'workflow/source_context.json'), harness)
    checks['password_reauth'] = enablement['password_matches'] is True
    checks['preview_and_confirm'] = enablement['version_before'] == 1 and enablement['preview_kind'] == 'migration_enable' \
        and enablement['version_after'] == 2 and enablement['revision_after'] == 1
    checks['unknown_items_acknowledged'] = enablement['unknown'] >= 3 \
        and enablement['unknown_kinds'].get('admin_missing_permissions', 0) >= 1 \
        and enablement['unknown_kinds'].get('admin_future_default', 0) >= 1
    checks['explicit_admin_choices'] = all(
        choice == 'adopt_v2' for key, choice in enablement['choices'].items()
        if key.startswith('missing:') or key.startswith('future:')) and \
        any(key.startswith('missing:') for key in enablement['choices'])
    checks['exception_applied'] = enablement['exceptions'] >= 1 and enablement['applied_overrides'] >= 1 \
        and enablement['exception_frozen'] is True
    checks['preview_matches_confirm'] = enablement['preview_matches_confirm'] is True
    checks['repeat_refused_without_reset'] = enablement['repeat'] == 'already_enabled'
    checks['old_credentials_invalidated'] = enablement['old_invitations_revoked'] is True \
        and enablement['pending_previews_left'] == 0
    checks['audit_in_transaction'] = enablement['audit']['enabled'] == 1 \
        and enablement['audit']['override_frozen'] >= 1
    checks['v2_effective'] = enablement['v2_admin_gained'] is True \
        and enablement['v2_admin_export_revoked'] is True and enablement['v2_member_unchanged'] is True \
        and enablement['v2_inactive_denied'] is True

    # The protected acceptance volume: read-only copy, migrate the copy, read the
    # difference, never confirm and never write the source.
    acceptance_checks = {}
    if ACCEPTANCE_SOURCE.is_dir():
        acceptance_db = ACCEPTANCE_SOURCE / 'gep.sqlite3'
        acceptance_before = sha256_file(acceptance_db)
        acceptance_manifest_before = file_manifest(ACCEPTANCE_SOURCE)
        acceptance_copy = resolve_within(root, 'acceptance_rehearsal')
        acceptance_copy_report = copy_consistent(ACCEPTANCE_SOURCE, acceptance_copy,
                                                 include={'gep.sqlite3', 'secret', 'instance'})
        acceptance_source_inspect = inspect_database(acceptance_db)
        acceptance_copy_before = inspect_database(acceptance_copy / 'gep.sqlite3')
        common = lambda state: {table: entry for table, entry in state['reference'].items()
                                if 'missing_columns' not in entry and not entry.get('missing')}
        acceptance_secret = (ACCEPTANCE_SOURCE / 'secret').read_text().strip()
        run_manage(acceptance_copy, acceptance_secret, 'migrate', '--noinput', '--verbosity', '0')
        acceptance_context = write_json(resolve_within(root, 'workflow/acceptance_context.json'), {})
        acceptance = run_step('acceptance-diff', acceptance_copy, acceptance_secret, acceptance_context, harness)
        acceptance_after = sha256_file(acceptance_db)
        acceptance_checks['source_database_unchanged'] = acceptance_before == acceptance_after
        acceptance_checks['source_files_unchanged'] = acceptance_manifest_before == file_manifest(ACCEPTANCE_SOURCE)
        acceptance_checks['copy_database_logically_equal'] = (
            common(acceptance_source_inspect) == common(acceptance_copy_before)
            and acceptance_copy_before['integrity_check'] == 'ok')
        acceptance_checks['copy_private_markers_equal'] = all(
            acceptance_copy_report['copied'][name] == acceptance_manifest_before[name]['sha256']
            for name in ('secret', 'instance') if name in acceptance_copy_report['copied'])
        acceptance_checks['acceptance_sidecars_not_copied'] = not any(
            is_sqlite_sidecar(name) for name in acceptance_copy_report['copied'])
        acceptance_checks['required_migration_applied'] = REQUIRED_MIGRATION in inspect_database(
            acceptance_copy / 'gep.sqlite3')['migrations']
        acceptance_checks['real_owner_never_confirmed'] = acceptance['confirmed'] is False and acceptance['still_v1'] is True
        acceptance_checks['creator_unknowns_reported'] = acceptance['creator_unknowns'] >= 1 \
            and acceptance['creator_principal_null'] is True
        acceptance_checks['difference_readable'] = acceptance['preview_kind'] == 'migration_enable' \
            and acceptance['subjects'] >= 1
        write_json(resolve_within(root, 'acceptance_report.json'), acceptance)
        checks.update({f'acceptance_{key}': value for key, value in acceptance_checks.items()})
    else:
        raise RehearsalError(f'protected acceptance volume missing: {ACCEPTANCE_SOURCE}')

    report = {
        'task': 'p03r02br', 'evidence_root': str(root), 'source': str(source),
        'acceptance_source': str(ACCEPTANCE_SOURCE),
        'omitted_acceptance_payload': list(OMITTED_ACCEPTANCE_PAYLOAD),
        'checks': checks, 'extension': extension, 'enablement': enablement,
        'synthetic_counts': {table: value.get('count', 0) for table, value in source_inspect_before['reference'].items()},
        'copy': {'sidecars_skipped': copy_report['sidecars_skipped'],
                 'data_version': copy_report['data_version']},
        'run_at': datetime.now(timezone.utc).isoformat(),
    }
    report['verdict'] = 'ok' if all(checks.values()) else 'failed'
    write_json(resolve_within(root, 'report.json'), report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true', help='run the rehearsal (required)')
    args = parser.parse_args(argv)
    if not args.verify:
        parser.error('--verify is required; this tool never migrates a source volume in place')
    root = new_unique_root(EVIDENCE_BASE)
    try:
        harness = harness_path(root)
        report = verify_workflow(root, harness)
    except (RehearsalError, ValueError) as error:
        print(f'REHEARSAL REFUSED: {error}', file=sys.stderr)
        print(json.dumps({'evidence_root': str(root), 'verdict': 'refused'}, ensure_ascii=False))
        return 2
    summary = {'verdict': report['verdict'], 'evidence_root': report['evidence_root'],
               'checks_failed': sorted(key for key, value in report['checks'].items() if not value),
               'checks_total': len(report['checks']),
               'extension': report['extension'], 'enablement': report['enablement'],
               'synthetic_counts': report['synthetic_counts']}
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if report['verdict'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
