"""Phase 03 safe additive-migration rehearsal.

Reads a protected synthetic volume read-only, copies it with the SQLite backup
API into a NEW task folder, copies the instance/secret privately, migrates only
the copy, and proves the source hash, schema/content references, grant matrix,
legacy session/release bindings, release-config bytes and instance owner are
unchanged. 03C studies must come out of the migration private with a NULL
current release and no guessed release. Refuses to overwrite any existing
destination and never migrates or writes the source volume.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = PROJECT_ROOT / 'server'
REQUIRED_MIGRATION = ('core', '0006_study_publication')
PUBLICATION_COLUMNS = ('public', 'public_summary', 'public_duration', 'public_device_requirements',
                       'show_closed_summary', 'current_release_id', 'revision')
REFERENCE_TABLES = {
    'core_study': ['id', 'title', 'mode', 'recruitment', 'max_sessions'],
    'core_build': ['id', 'study_id', 'descriptor', 'digest', 'package_path'],
    'core_release': ['id', 'study_id', 'build_id', 'config', 'approved'],
    'core_session': ['id', 'participant_id', 'release_id', 'operation', 'proof_hash', 'request', 'token_hash', 'expires_at', 'revoked', 'completion', 'created_at'],
    'core_event': ['id', 'session_id', 'event_id', 'segment_id', 'sequence', 'envelope', 'received_at'],
    'core_export': ['id', 'study_id', 'snapshot', 'created_at'],
}


class RehearsalError(Exception):
    pass


def sha256_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def connect_readonly(path):
    return sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)


def table_exists(connection, name):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def column_names(connection, table):
    if not table_exists(connection, table):
        return set()
    return {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}


def study_publication(connection, normalize=True):
    """03C publication state per study, or None while the columns do not exist yet."""
    if not set(PUBLICATION_COLUMNS) <= column_names(connection, 'core_study'):
        return None
    rows = []
    for study_id, public, show_closed, revision, current_release in connection.execute(
            'SELECT id, public, show_closed_summary, revision, current_release_id FROM core_study ORDER BY id'):
        rows.append({'study': str(study_id), 'public': bool(public) if normalize else public,
                     'show_closed_summary': bool(show_closed) if normalize else show_closed,
                     'revision': revision, 'current_release': str(current_release) if current_release else None})
    return rows


def release_config_digests(connection):
    """Release id -> SHA-256 of the stored config JSON text (byte structure)."""
    if not table_exists(connection, 'core_release'):
        return {}
    return {str(row[0]): hashlib.sha256((row[1] or '').encode('utf-8')).hexdigest()
            for row in connection.execute('SELECT id, config FROM core_release ORDER BY id')}


def session_release_bindings(connection):
    """Session id -> release id, the frozen legacy direct-URL binding."""
    if not table_exists(connection, 'core_session'):
        return {}
    return {str(row[0]): str(row[1]) for row in connection.execute('SELECT id, release_id FROM core_session ORDER BY id')}


def reference_digests(connection):
    result = {}
    for table, columns in REFERENCE_TABLES.items():
        if not table_exists(connection, table):
            result[table] = {'count': 0, 'identity_sha256': None, 'content_sha256': None, 'missing': True}
            continue
        rows = [list(row) for row in connection.execute(f'SELECT {", ".join(columns)} FROM {table} ORDER BY 1')]
        result[table] = {
            'count': len(rows),
            'identity_sha256': sha256_text(json.dumps([str(row[0]) for row in rows], ensure_ascii=False)),
            'content_sha256': sha256_text(json.dumps(rows, ensure_ascii=False, default=str, separators=(',', ':'))),
        }
    return result


def contradictions(connection):
    if not table_exists(connection, 'core_grant'):
        return []
    grouped = {}
    for user_id, study_id, action in connection.execute('SELECT user_id, study_id, action FROM core_grant'):
        grouped.setdefault((str(user_id), str(study_id)), set()).add(action)
    return [{'user_id': user_id, 'study_id': study_id, 'actions': sorted(actions - {'study.view'})}
            for (user_id, study_id), actions in sorted(grouped.items()) if actions - {'study.view'} and 'study.view' not in actions]


def grants_summary(connection):
    rows = [list(row) for row in connection.execute('SELECT user_id, study_id, action, delegable FROM core_grant ORDER BY 1, 2, 3')]
    return {'count': len(rows), 'sha256': sha256_text(json.dumps(rows, ensure_ascii=False, default=str))}


def account_summary(connection):
    users = [list(row) for row in connection.execute('SELECT id, username, is_active, is_staff, is_superuser FROM auth_user ORDER BY id')]
    instance = connection.execute('SELECT id, instance_id, owner_id FROM core_instance').fetchone()
    profiles = []
    if table_exists(connection, 'core_accountprofile'):
        profiles = [list(row) for row in connection.execute('SELECT user_id, role, must_change_password, auth_version, revision FROM core_accountprofile ORDER BY user_id')]
    return {'users': users, 'user_count': len(users), 'instance': {'id': instance[0], 'instance_id': str(instance[1]), 'owner_id': instance[2]} if instance else None, 'profiles': profiles}


def migrations(connection):
    return [list(row) for row in connection.execute('SELECT app, name FROM django_migrations ORDER BY id')]


def audit_summary(connection):
    if not table_exists(connection, 'core_audit'):
        return {'count': 0}
    rows = [list(row) for row in connection.execute('SELECT id, study_id, actor_id, action, target, created_at FROM core_audit ORDER BY id')]
    return {'count': len(rows), 'sha256': sha256_text(json.dumps(rows, ensure_ascii=False, default=str))}


def inspect_sqlite(connection):
    integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
    foreign_keys = [list(row) for row in connection.execute('PRAGMA foreign_key_check')]
    digests = reference_digests(connection)
    data = {
        'integrity_check': integrity,
        'foreign_key_violations': foreign_keys,
        'reference_digests': digests,
        'counts': {table: digests[table]['count'] for table in digests},
        'grants': grants_summary(connection),
        'contradictions': contradictions(connection),
        'account': account_summary(connection),
        'audit': audit_summary(connection),
        'migrations': migrations(connection),
        'publication': study_publication(connection),
        'release_configs': release_config_digests(connection),
        'session_bindings': session_release_bindings(connection),
    }
    data['events_exports'] = {
        'event_count': digests.get('core_event', {}).get('count', 0),
        'export_count': digests.get('core_export', {}).get('count', 0),
        'export_ids': [str(row[0]) for row in connection.execute('SELECT id FROM core_export ORDER BY 1')] if table_exists(connection, 'core_export') else [],
    }
    return data


def inspect_path(path):
    connection = connect_readonly(path)
    try:
        return inspect_sqlite(connection)
    finally:
        connection.close()


def snapshot_database(source_db, destination_db):
    destination_db = Path(destination_db)
    descriptor = os.open(destination_db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    source = connect_readonly(source_db)
    try:
        target = sqlite3.connect(destination_db)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def copy_private(source, destination):
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(Path(source).read_bytes())
        stream.flush()
        os.fsync(stream.fileno())


def migrate_copy(destination, secret):
    env = dict(os.environ)
    env.update({'DJANGO_SETTINGS_MODULE': 'gep.settings', 'GEP_DATA_DIR': str(destination), 'GEP_SECRET_KEY': secret, 'PYTHONPATH': str(SERVER_DIR)})
    result = subprocess.run([sys.executable, str(SERVER_DIR / 'manage.py'), 'migrate', '--noinput', '--verbosity', '0'], cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RehearsalError(f'migration of rehearsal copy failed: {result.stdout[-2000:]}{result.stderr[-2000:]}')


def _write_json(path, value):
    path = Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)


def rehearse(source, evidence_root, destination=None, label='rehearsal'):
    source = Path(source).resolve()
    evidence_root = Path(evidence_root).resolve()
    source_db = source / 'gep.sqlite3'
    if not source_db.is_file():
        raise RehearsalError(f'source volume has no gep.sqlite3: {source}')
    for name in ('secret', 'instance'):
        if not (source / name).is_file():
            raise RehearsalError(f'source volume has no {name} marker: {source}')
    if source == evidence_root or source in evidence_root.parents or evidence_root in source.parents:
        raise RehearsalError('evidence root must be outside the protected source volume')
    if destination is None:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        destination = evidence_root / f'{label}_{source.name}_{stamp}'
    destination = Path(destination).resolve()
    if destination == source or source in destination.parents or destination in source.parents:
        raise RehearsalError('destination must be outside the protected source volume')
    if destination.exists():
        raise RehearsalError(f'destination already exists, refusing to overwrite: {destination}')

    evidence_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.mkdir(mode=0o700)
    source_digest_before = sha256_file(source_db)
    snapshot_database(source_db, destination / 'gep.sqlite3')
    copy_private(source / 'secret', destination / 'secret')
    copy_private(source / 'instance', destination / 'instance')

    source_state = inspect_path(source_db)
    copy_before = inspect_path(destination / 'gep.sqlite3')
    migrate_copy(destination, (destination / 'secret').read_text().strip())
    copy_after = inspect_path(destination / 'gep.sqlite3')
    source_digest_after = sha256_file(source_db)

    digests_equal = copy_after['reference_digests'] == copy_before['reference_digests']
    copy_matches_source = (copy_before['reference_digests'] == source_state['reference_digests']
                           and copy_before['grants'] == source_state['grants']
                           and copy_before['account'] == source_state['account'])
    profiles_backfilled = (copy_after['account']['profiles'] and
                           len(copy_after['account']['profiles']) == copy_after['account']['user_count'] and
                           all(row[1] == 'user' for row in copy_after['account']['profiles']))
    publication_added = copy_before['publication'] is None and copy_after['publication'] is not None
    publication_unchanged = (copy_before['publication'] is not None
                             and copy_before['publication'] == copy_after['publication'])
    publication_defaults = bool(publication_added) and all(
        row['public'] is False and row['current_release'] is None
        and row['show_closed_summary'] is False and row['revision'] == 0
        for row in copy_after['publication'])
    checks = {
        'source_hash_unchanged': source_digest_before == source_digest_after,
        'copy_matches_source': copy_matches_source,
        'integrity_ok_before': copy_before['integrity_check'] == 'ok',
        'integrity_ok_after': copy_after['integrity_check'] == 'ok',
        'foreign_keys_ok_before': not copy_before['foreign_key_violations'],
        'foreign_keys_ok_after': not copy_after['foreign_key_violations'],
        'reference_digests_unchanged': digests_equal,
        'counts_unchanged': copy_before['counts'] == copy_after['counts'],
        'grants_unchanged': copy_before['grants'] == copy_after['grants'],
        'release_configs_unchanged': copy_before['release_configs'] == copy_after['release_configs'],
        'session_release_bindings_unchanged': copy_before['session_bindings'] == copy_after['session_bindings'],
        'publication_added_or_unchanged': publication_added or publication_unchanged,
        'publication_defaults_private_null': publication_defaults,
        'instance_owner_unchanged': (copy_before['account']['instance'] == copy_after['account']['instance']
                                     and copy_before['account']['users'] == copy_after['account']['users']),
        'ordinary_profiles_backfilled': bool(profiles_backfilled),
        'required_migration_applied': list(REQUIRED_MIGRATION) in copy_after['migrations'],
    }
    report = {
        'rehearsal': {'label': label, 'destination': str(destination), 'source': str(source), 'run_at': datetime.now(timezone.utc).isoformat()},
        'source_digest': {'before': source_digest_before, 'after': source_digest_after},
        'checks': checks,
        'verdict': 'ok' if all(checks.values()) else 'failed',
        'before': copy_before,
        'after': copy_after,
        'source_reference_digests': source_state['reference_digests'],
        'contradictions_before': copy_before['contradictions'],
        'contradictions_after': copy_after['contradictions'],
        'events_exports': copy_after['events_exports'],
        'migrations_before': copy_before['migrations'],
        'migrations_after': copy_after['migrations'],
    }
    _write_json(destination / 'migration_report.json', report)
    _write_json(destination / 'source_digest.json', report['source_digest'])
    _write_json(destination / 'counts_before.json', copy_before['counts'])
    _write_json(destination / 'counts_after.json', copy_after['counts'])
    _write_json(destination / 'reference_digests_before.json', copy_before['reference_digests'])
    _write_json(destination / 'reference_digests_after.json', copy_after['reference_digests'])
    _write_json(destination / 'contradictions_before.json', copy_before['contradictions'])
    _write_json(destination / 'contradictions_after.json', copy_after['contradictions'])
    _write_json(destination / 'migrations_before.json', copy_before['migrations'])
    _write_json(destination / 'migrations_after.json', copy_after['migrations'])
    _write_json(destination / 'publication_after.json', copy_after['publication'])
    _write_json(destination / 'release_config_digests_after.json', copy_after['release_configs'])
    _write_json(destination / 'session_release_bindings_after.json', copy_after['session_bindings'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true', help='run the read-only rehearsal (required)')
    parser.add_argument('--source', required=True, help='protected synthetic volume directory')
    parser.add_argument('--evidence-root', required=True, help='new task folder for the rehearsal copy and evidence')
    parser.add_argument('--destination', default=None, help='explicit destination; refuses to overwrite')
    parser.add_argument('--label', default='rehearsal')
    args = parser.parse_args(argv)
    if not args.verify:
        parser.error('--verify is required; this tool never migrates a source volume in place')
    try:
        report = rehearse(args.source, args.evidence_root, args.destination, args.label)
    except RehearsalError as error:
        print(f'REHEARSAL REFUSED: {error}', file=sys.stderr)
        return 2
    summary = {'verdict': report['verdict'], 'destination': report['rehearsal']['destination'],
               'source_digest_before': report['source_digest']['before'], 'source_digest_after': report['source_digest']['after'],
               'counts_after': report['after']['counts'], 'checks': report['checks'],
               'publication_after': report['after']['publication'],
               'contradictions_before': report['contradictions_before'], 'contradictions_after': report['contradictions_after']}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report['verdict'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
