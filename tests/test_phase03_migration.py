"""T26 migration evidence: additive rehearsal on a real legacy schema and copy.

The MigrationExecutor test builds the actual pre-03B schema, inserts legacy
synthetic rows, then runs the new migration and compares independently computed
identity/content digests. The tool test rehearses on a named synthetic copy and
proves the source volume is byte-identical afterwards.
"""
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = PROJECT_ROOT / 'server'
sys.path.insert(0, str(PROJECT_ROOT / 'tools'))
phase03_migration = importlib.import_module('phase03_migration')
LEGACY_TARGET = [('core', '0003_throttle_recoverypermit')]
REQUIRED_MIGRATION = list(phase03_migration.REQUIRED_MIGRATION)


@pytest.mark.django_db(transaction=True)
def test_legacy_fixture_migration_is_additive_and_preserves_references():
    executor = MigrationExecutor(connection)
    try:
        executor.migrate(LEGACY_TARGET)
        legacy = executor.loader.project_state(LEGACY_TARGET).apps
        User = legacy.get_model('auth', 'User')
        owner = User.objects.create(username='legacy_owner', password=make_password('synthetic-legacy-password'))
        editor = User.objects.create(username='legacy_editor', password=make_password('synthetic-legacy-password'))
        conflicted = User.objects.create(username='legacy_conflict', password=make_password('synthetic-legacy-password'))
        Instance = legacy.get_model('core', 'Instance')
        instance = Instance.objects.create(id=1, instance_id=uuid.uuid4(), owner_id=owner.pk)
        Study = legacy.get_model('core', 'Study')
        study = Study.objects.create(title='Legacy synthetic A', mode='anonymous', recruitment='open', max_sessions=2)
        Build = legacy.get_model('core', 'Build')
        build = Build.objects.create(study_id=study.pk, descriptor={'platform': 'godot_web', 'version': 'v1'}, digest='a' * 64)
        Release = legacy.get_model('core', 'Release')
        release = Release.objects.create(study_id=study.pk, build_id=build.pk, config={'purpose': 'synthetic'}, approved=True)
        Participant = legacy.get_model('core', 'Participant')
        participant = Participant.objects.create(study_id=study.pk, code='001')
        Session = legacy.get_model('core', 'Session')
        session = Session.objects.create(participant_id=participant.pk, release_id=release.pk, operation=uuid.uuid4(), proof_hash='p' * 64,
                                         request={'operation_id': 'legacy'}, token_hash='t' * 64, expires_at=timezone.now() + timedelta(days=7))
        Event = legacy.get_model('core', 'Event')
        Event.objects.create(session_id=session.pk, event_id=uuid.uuid4(), segment_id=uuid.uuid4(), sequence=1, envelope={'payload': {'rt_ms': 321.5}})
        Export = legacy.get_model('core', 'Export')
        Export.objects.create(study_id=study.pk, snapshot={'records': [{'record': {'rt_ms': 321.5}}], 'format_version': '1'})
        Grant = legacy.get_model('core', 'Grant')
        for action in ('study.view', 'study.configure', 'data.export_raw'):
            Grant.objects.create(user_id=owner.pk, study_id=study.pk, action=action, delegable=True)
        Grant.objects.create(user_id=editor.pk, study_id=study.pk, action='build.upload', delegable=True)
        Grant.objects.create(user_id=conflicted.pk, study_id=study.pk, action='build.upload', delegable=False)
        Audit = legacy.get_model('core', 'Audit')
        Audit.objects.create(study_id=study.pk, actor_id=owner.pk, action='legacy.audit', target=str(study.pk))
        before = phase03_migration.inspect_sqlite(connection.connection)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    after = phase03_migration.inspect_sqlite(connection.connection)

    # Additive only: every legacy reference/count and the whole grant matrix survive unchanged.
    assert before['integrity_check'] == 'ok' and after['integrity_check'] == 'ok'
    assert after['reference_digests'] == before['reference_digests']
    assert after['counts'] == before['counts']
    assert after['grants'] == before['grants']
    assert after['audit'] == before['audit'] and before['audit']['count'] == 1
    assert after['counts']['core_study'] == 1 and after['counts']['core_event'] == 1 and after['counts']['core_export'] == 1

    # New 03C contract: publication columns are added private with a NULL current
    # release, while the legacy release config bytes and session bindings survive.
    assert before['publication'] is None
    assert after['publication'] == [{'study': study.pk.hex, 'public': False,
                                     'show_closed_summary': False, 'revision': 0, 'current_release': None}]
    assert after['release_configs'] == before['release_configs']
    assert after['session_bindings'] == before['session_bindings'] == {session.pk.hex: release.pk.hex}

    # Owner stays the authoritative pointer; nobody is promoted by the data migration.
    assert after['account']['instance'] == before['account']['instance'] == {'id': 1, 'instance_id': instance.instance_id.hex, 'owner_id': owner.pk}
    profiles = {row[0]: row[1:] for row in after['account']['profiles']}
    assert sorted(profiles) == sorted([owner.pk, editor.pk, conflicted.pk])
    assert all(role == 'user' for role, must_change, auth_version, revision in profiles.values())
    assert all(must_change is False and auth_version == 1 and revision == 0 for role, must_change, auth_version, revision in profiles.values())

    # Contradictions are reported, not silently repaired or widened.
    assert before['contradictions'] == [
        {'user_id': str(editor.pk), 'study_id': study.pk.hex, 'actions': ['build.upload']},
        {'user_id': str(conflicted.pk), 'study_id': study.pk.hex, 'actions': ['build.upload']},
    ]
    assert after['contradictions'] == before['contradictions']
    assert after['events_exports']['event_count'] == 1 and after['events_exports']['export_count'] == 1
    assert REQUIRED_MIGRATION in after['migrations']


def legacy_volume(root):
    """Build a real pre-03B synthetic volume (schema + rows) in a temp directory."""
    root.mkdir(mode=0o700)
    (root / 'secret').write_text('synthetic-rehearsal-secret-key')
    (root / 'instance').write_text('11111111-2222-3333-4444-555555555555')
    env = dict(os.environ, PYTHONPATH=str(SERVER_DIR), DJANGO_SETTINGS_MODULE='gep.settings',
               GEP_DATA_DIR=str(root), GEP_SECRET_KEY='synthetic-rehearsal-secret-key')
    for migrate_args in (['migrate', '--noinput', '--verbosity', '0'],
                         ['migrate', 'core', '0003_throttle_recoverypermit', '--noinput', '--verbosity', '0']):
        result = subprocess.run([sys.executable, str(SERVER_DIR / 'manage.py'), *migrate_args],
                                cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    study = uuid.uuid4().hex
    with sqlite3.connect(root / 'gep.sqlite3') as db:
        db.execute("INSERT INTO auth_user (password, last_login, is_superuser, username, last_name, email, is_staff, is_active, date_joined, first_name) VALUES ('x', NULL, 0, 'legacy_owner', '', '', 0, 1, '2026-01-01 00:00:00', '')")
        db.execute("INSERT INTO auth_user (password, last_login, is_superuser, username, last_name, email, is_staff, is_active, date_joined, first_name) VALUES ('x', NULL, 0, 'legacy_reader', '', '', 0, 1, '2026-01-01 00:00:00', '')")
        db.execute("INSERT INTO core_instance (id, instance_id, owner_id) VALUES (1, '11111111222233334444555555555555', 1)")
        db.execute("INSERT INTO core_study (id, title, mode, recruitment, max_sessions) VALUES (?, 'Legacy synthetic B', 'anonymous', 'open', 1)", (study,))
        db.execute("INSERT INTO core_grant (action, delegable, user_id, study_id) VALUES ('study.view', 1, 1, ?)", (study,))
        db.execute("INSERT INTO core_grant (action, delegable, user_id, study_id) VALUES ('data.export_raw', 1, 1, ?)", (study,))
        db.execute("INSERT INTO core_grant (action, delegable, user_id, study_id) VALUES ('build.upload', 0, 2, ?)", (study,))
        db.execute("INSERT INTO core_audit (study_id, actor_id, action, target, created_at) VALUES (?, 1, 'legacy.audit', 'legacy-target', '2026-01-01 00:00:00')", (study,))
    return study


def test_migration_tool_rehearses_named_copy_and_refuses_existing_destination(tmp_path):
    source = tmp_path / 'protected_synthetic_copy'
    study = legacy_volume(source)
    source_digest = phase03_migration.sha256_file(source / 'gep.sqlite3')
    evidence = tmp_path / 'evidence'
    result = subprocess.run([sys.executable, str(PROJECT_ROOT / 'tools' / 'phase03_migration.py'), '--verify',
                             '--source', str(source), '--evidence-root', str(evidence)],
                            cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    runs = list(evidence.glob('rehearsal_protected_synthetic_copy_*'))
    assert len(runs) == 1
    destination = runs[0]
    report = json.loads((destination / 'migration_report.json').read_text())
    assert report['verdict'] == 'ok' and all(report['checks'].values())
    assert report['source_digest'] == {'before': source_digest, 'after': source_digest}
    assert phase03_migration.sha256_file(source / 'gep.sqlite3') == source_digest
    assert report['contradictions_before'] == [{'user_id': '2', 'study_id': study, 'actions': ['build.upload']}]
    assert report['after']['counts'] == report['before']['counts']
    assert report['after']['audit'] == report['before']['audit'] and report['before']['audit']['count'] == 1
    assert (destination / 'secret').stat().st_mode & 0o777 == 0o600
    assert (destination / 'instance').read_text() == '11111111-2222-3333-4444-555555555555'
    with sqlite3.connect(destination / 'gep.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM core_accountprofile').fetchone()[0] == 2
        assert {row[0] for row in db.execute('SELECT role FROM core_accountprofile')} == {'user'}
        assert db.execute('SELECT owner_id FROM core_instance').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM core_grant').fetchone()[0] == 3
        assert [list(row) for row in db.execute('SELECT app, name FROM django_migrations')].count(REQUIRED_MIGRATION) == 1

    blocked = tmp_path / 'explicit_destination'
    blocked.mkdir()
    refused = subprocess.run([sys.executable, str(PROJECT_ROOT / 'tools' / 'phase03_migration.py'), '--verify',
                              '--source', str(source), '--evidence-root', str(tmp_path / 'other_evidence'), '--destination', str(blocked)],
                             cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert refused.returncode != 0 and 'refusing to overwrite' in refused.stdout + refused.stderr
    assert blocked.stat().st_mode & 0o777 == 0o755  # untouched pre-existing directory
    assert phase03_migration.sha256_file(source / 'gep.sqlite3') == source_digest
