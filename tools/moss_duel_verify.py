"""Verify one fresh Moss Duel GEC export against a new synthetic instance.

Run: .venv/bin/python tools/moss_duel_verify.py --build build/<delivery-directory>
No existing database, browser profile, game package or protected port is used.
The explicit build must contain integrated_verification_copy exported by
moss_duel_delivery.py. Evidence and synthetic secrets stay ignored in local_data/.
"""
from __future__ import annotations
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv/bin/python'

def run(command, *, env=None, timeout=300, log=None):
    # Stream evidence while the process runs; a parse error or timeout must not
    # discard its diagnostic output or make an idle runtime appear opaque.
    if log:
        with log.open('w') as stream:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, text=True, timeout=timeout)
        output = log.read_text()
    else:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
        output = result.stdout + result.stderr
    if result.returncode:
        raise RuntimeError(f'{Path(command[0]).name} exited {result.returncode}; see {log.name if log else "captured output"}')
    return output

def private_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)

def free_port():
    for candidate in range(8520, 8560):
        with socket.socket() as sock:
            try: sock.bind(('127.0.0.1', candidate))
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EPERM):
                    raise RuntimeError('Execution sandbox denied local loopback bind; use an authorized local test environment') from error
                continue
            return candidate
    raise RuntimeError('No free owned verification port in 8520–8559')

PACKAGE_SCRIPT = '''extends SceneTree
func _initialize():
 var web = load("res://package_web.gd").package_export(ProjectSettings.globalize_path("res://exports/web/index.html"))
 var descriptor = JSON.parse_string(FileAccess.get_file_as_string("res://gec_descriptor.json"))
 var native = load("res://package_native.gd")._macos(ProjectSettings.globalize_path("res://exports/native/moss_duel.zip"), descriptor)
 var result = {"web":web,"native":native}
 var output = FileAccess.open(OS.get_environment("MOSS_PACKAGE_RESULT"), FileAccess.WRITE)
 output.store_string(JSON.stringify(result));output.close()
 quit(0 if web.get("ok", false) and not native.has("error") else 1)
'''

NATIVE_SCRIPT = '''extends SceneTree
var entry
var admission_deadline := 0
var admission_done := false
func _initialize():
 print("MOSS_NATIVE_HARNESS_BEGIN")
 call_deferred("begin")
func begin():
 entry = load("res://gec_entry.tscn").instantiate()
 root.add_child(entry)
 await process_frame
 print("MOSS_NATIVE_SHELL_READY ", entry.shell.state)
 if entry.shell.locale != "en":
  print("MOSS_NATIVE_DISPLAY_LANGUAGE_FAILED");quit(6);return
 var frozen_config := JSON.stringify(entry.backend.config)
 entry.backend.timer.stop()
 admission_deadline = Time.get_ticks_msec() + 30000
 watch_admission()
 var result = await entry.shell.auto_submit({})
 admission_done = true
 print("MOSS_NATIVE_ADMISSION_RESULT ", str(result.get("state", result.get("error", "unknown"))))
 if result.has("error"):
  print("MOSS_NATIVE_ADMISSION_FAILED ", JSON.stringify(result));quit(2);return
 if JSON.stringify(entry.backend.config) != frozen_config:
  print("MOSS_NATIVE_FROZEN_CONFIGURATION_CHANGED");quit(7);return
 var deadline = Time.get_ticks_msec() + 180000
 while not entry.finished and Time.get_ticks_msec() < deadline:
  if entry.stopped:
   print("MOSS_NATIVE_LOCAL_SAVE_FAILED ", entry.shell.last_announcement);quit(5);return
  await process_frame
 if not entry.finished:
  print("MOSS_NATIVE_FINISH_TIMEOUT");quit(3);return
 print("MOSS_NATIVE_ROUND_FINISHED")
 var id = entry.backend.session_id
 var local = entry.backend.read_session(id)
 write("native_before_upload.json", {"id":id,"kind":local.get("kind"),"records":local.get("records",[]),"pending":local.get("pending",[]),"completion":local.get("completion"),"checkpoint":local.get("checkpoint"),"complete_ack":local.get("complete_ack") != null})
 entry.backend.timer.start()
 deadline = Time.get_ticks_msec() + 90000
 while entry.data.status().state != "remote_acknowledged" and Time.get_ticks_msec() < deadline:
  await process_frame
 var after = entry.backend.read_session(id)
 write("native_after_ack.json", {"id":id,"kind":after.get("kind"),"records":after.get("records",[]),"pending":after.get("pending",[]),"state":after.get("state"),"complete_ack":after.get("complete_ack") != null})
 if entry.data.status().state != "remote_acknowledged":
  print("MOSS_NATIVE_ACK_TIMEOUT");quit(4);return
 print("MOSS_NATIVE_DONE ", id, " ", local.records.size())
 entry.queue_free()
 await process_frame
 entry = null
 await process_frame
 quit(0)
func watch_admission():
 while not admission_done and Time.get_ticks_msec() < admission_deadline:
  await process_frame
 if not admission_done:
  print("MOSS_NATIVE_ADMISSION_TIMEOUT");quit(8)
func write(name, value):
 var file = FileAccess.open(OS.get_environment("MOSS_EVIDENCE").path_join(name), FileAccess.WRITE)
 file.store_string(JSON.stringify(value));file.close()
'''

def compare(db, before, export, label):
    source = {event['event_id']: event for event in before['records']}
    assert len(source) == len(before['records']), f'{label}: duplicate local event identities'
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as connection:
        stored_rows = list(connection.execute(
            'SELECT event_id,envelope FROM core_event WHERE session_id=?', [before['id'].replace('-', '')]))
        stored = {str(uuid.UUID(event_id)): json.loads(envelope) for event_id, envelope in stored_rows}
        completion = json.loads(connection.execute('SELECT completion FROM core_session WHERE id=?',
                                                  [before['id'].replace('-', '')]).fetchone()[0])
    exported = {line['event_id']: line['record'] for line in export}
    assert len(exported) == len(export) == len(source) == len(stored_rows), f'{label}: duplicate or missing exported/database event identities'
    assert source == stored == exported, f'{label}: local, database and authorized JSONL differ'
    assert set(completion['event_ids']) == set(source), f'{label}: server completion set differs'
    assert set(completion['segment_ids']) == {event['segment_id'] for event in source.values()}, f'{label}: segment completion set differs'
    assert before['checkpoint'] is None, f'{label}: battle checkpoint was invented'
    assert before['pending'] and not before['complete_ack'], f'{label}: no unacknowledged local boundary evidence'
    assert len([event for event in source.values() if event['payload']['kind'] == 'round_summary']) == 1
    return {'events': len(source), 'identity_and_payload_match': True, 'completion_set_match': True}

def import_native_source(native_source, evidence, retry):
    first = evidence / ('native_source_import_retry.log' if retry else 'native_source_import.log')
    try:
        run(['godot', '--headless', '--path', str(native_source), '--import'],
            log=first, timeout=90)
        return False
    except RuntimeError:
        diagnostics = first.read_text()
        if 'handle_crash: Program crashed with signal' not in diagnostics or any(
            error in diagnostics for error in ('SCRIPT ERROR', 'Parse Error', 'Error loading')):
            raise
        # Preserve the engine shutdown crash and retry once after its completed
        # cold import. A repeat or a script error remains a failed verification.
        run(['godot', '--headless', '--path', str(native_source), '--import'],
            log=evidence / 'native_source_import_engine_retry.log', timeout=90)
        return True

def verify_native(evidence, project, job, report, summary, retry=False):
    target = evidence / 'native_program'
    if not retry:
        target.mkdir()
        with zipfile.ZipFile(evidence / 'native_complete.zip') as archive:
            archive.extractall(target)
            for info in archive.infolist():
                if not info.is_dir(): (target / info.filename).chmod((info.external_attr >> 16) & 0o777 or 0o644)
    manifest = json.loads((target / 'artifact_manifest.json').read_text())
    binary = target / manifest['entry']
    # Release exports ignore editor-only --script. The actual frozen export is
    # booted separately; source automation must not be labelled exported UI QA.
    if not retry:
        boot_storage = evidence / 'native_export_boot_storage'; boot_storage.mkdir()
        run([str(binary), '--headless', '--quit-after', '120'],
            env={**os.environ, 'GEP_SYNTHETIC_STORAGE': str(boot_storage)},
            log=evidence / 'native_export_boot.log', timeout=30)
    boot_log = (evidence / 'native_export_boot.log').read_text()
    assert 'SCRIPT ERROR' not in boot_log and 'ERROR:' not in boot_log
    with sqlite3.connect(f'file:{evidence / "native_export_boot_storage/queue.sqlite"}?mode=ro', uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    native_source = evidence / 'native_source'
    if not retry:
        shutil.copytree(project, native_source, ignore=shutil.ignore_patterns('.godot', 'exports'))
        shutil.copy2(target / 'connection.json', native_source / 'connection.json')
    else:
        # Keep the failed runtime copy and cache, but refuse a changed delivery.
        for source in project.rglob('*'):
            relative = source.relative_to(project)
            if source.is_file() and not ({'.godot', 'exports'} & set(relative.parts)):
                assert source.read_bytes() == (native_source / relative).read_bytes(), f'Delivery source changed: {relative}'
    import_retried = import_native_source(native_source, evidence, retry)
    suffix = '_retry_' + secrets.token_hex(3) if retry else ''
    native_script = evidence / ('native_runtime' + suffix + '.gd'); native_script.write_text(NATIVE_SCRIPT)
    storage = evidence / ('native_storage' + suffix); storage.mkdir()
    run(['godot', '--headless', '--path', str(native_source), '--script', str(native_script)],
        env={**os.environ, 'GEP_SYNTHETIC_STORAGE': str(storage), 'MOSS_EVIDENCE': str(evidence)},
        log=evidence / ('native_runtime' + suffix + '.log'), timeout=300)
    job = {**job, 'native_study': report['evidence']['native']['study_url']}
    export_job = evidence / ('private_export_job' + suffix + '.json'); private_json(export_job, job)
    run(['node', str(ROOT / 'tests/browser/moss_duel_e2e.mjs'), 'export', str(export_job)],
        log=evidence / ('native_export' + suffix + '.log'), timeout=90)
    native_before = json.loads((evidence / 'native_before_upload.json').read_text())
    native_export = [json.loads(line) for line in (evidence / 'native_authorized.jsonl').read_text().splitlines()]
    summary['native'] = compare(evidence / 'server/gep.sqlite3', native_before, native_export, 'macOS')
    after = json.loads((evidence / 'native_after_ack.json').read_text())
    assert after['kind'] == 'cleaned' and not after['records'] and not after['pending']
    summary.update(verdict='PASS', browser_checks=len(report['checks']), engine_import_retried=import_retried or retry,
                   native_execution='downloaded macOS export boot plus delivered-source Godot tool SceneTree automation; no native UI acceptance',
                   native_upload_boundary='real SQLite queue with uploader timer held, then real HTTP; not a physical/network outage check',
                   preview='NOT_RUN in this native-only regression; retained prior Web evidence applies' if summary.get('native_only')
                   else 'real Godot battle; IndexedDB only; zero participant requests/server sessions')

def resume_native(evidence, build):
    evidence = evidence.resolve()
    if evidence.parent != ROOT / 'local_data' or not evidence.name.startswith('moss_duel_verify_'):
        raise ValueError('Resume only an explicit owned Moss Duel verification directory')
    previous = json.loads((evidence / 'summary.json').read_text())
    assert previous['build'] == str(build.relative_to(ROOT)) and previous.get('web', {}).get('identity_and_payload_match')
    assert not (evidence / 'native_before_upload.json').exists(), 'Do not overwrite a completed or failed native data boundary'
    job = json.loads((evidence / 'private_job.json').read_text())
    port = urlsplit(job['admin']).port
    with socket.socket() as check:
        check.bind(('127.0.0.1', port))
    data = evidence / 'server'
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'server'), 'DJANGO_SETTINGS_MODULE': 'gep.settings',
           'GEP_DATA_DIR': str(data), 'GEP_SECRET_KEY': (data / 'secret').read_text(),
           'GEP_EXPECTED_INSTANCE': (data / 'instance').read_text(),
           'GEP_EXPERIMENT_HOST': '127.0.0.1', 'GEP_PUBLIC_API': f'http://127.0.0.1:{port}'}
    summary = {**previous, 'verdict': 'FAILED', 'resumed_native_only': True, 'prior_failure': previous.get('error')}
    summary.pop('error', None)
    log = (evidence / 'server_native_retry.log').open('w')
    server = subprocess.Popen([str(ROOT / '.venv/bin/gunicorn'), 'gep.wsgi:application', '--bind', f'127.0.0.1:{port}',
                               '--workers', '1', '--threads', '4', '--timeout', '300', '--access-logfile', '/dev/null'],
                              cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=1): break
            except OSError:
                if server.poll() is not None: raise RuntimeError('Owned retry service exited before readiness')
                time.sleep(.2)
        else: raise RuntimeError('Owned retry service startup timeout')
        report = json.loads((evidence / 'browser_report.json').read_text())
        verify_native(evidence, build / 'integrated_verification_copy', job, report, summary, retry=True)
    except Exception as error:
        summary['error'] = str(error)
        raise
    finally:
        (evidence / 'summary_native_retry.json').write_text(json.dumps(summary, indent=2) + '\n')
        server.send_signal(signal.SIGTERM)
        try: server.wait(timeout=15)
        except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
        log.close()
        print(json.dumps(summary, indent=2))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--resume-native', type=Path, help='Resume only native verification after a preserved pre-admission tooling failure')
    parser.add_argument('--native-only', action='store_true', help='Fresh native regression; do not repeat unchanged Web verification')
    args = parser.parse_args()
    build = args.build.resolve()
    if ROOT / 'build' not in build.parents or not build.is_dir():
        raise ValueError('Select an existing explicit delivery directory under build/')
    if args.resume_native:
        return resume_native(args.resume_native, build)
    project = build / 'integrated_verification_copy'
    for path in (project / 'exports/web/index.html', project / 'exports/native/moss_duel.zip'):
        if not path.is_file(): raise ValueError('Missing fresh exported game: ' + str(path.relative_to(ROOT)))
    evidence = ROOT / 'local_data' / ('moss_duel_verify_' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '_' + secrets.token_hex(3))
    evidence.mkdir(mode=0o700)
    data = evidence / 'server'; data.mkdir(mode=0o700)
    package_script = evidence / 'package_exports.gd'; package_script.write_text(PACKAGE_SCRIPT)
    package_result = evidence / 'package_result.json'
    run(['godot', '--headless', '--path', str(project), '--script', str(package_script)],
        env={**os.environ, 'MOSS_PACKAGE_RESULT': str(package_result)}, log=evidence / 'packaging.log')
    packages = json.loads(package_result.read_text())
    port = free_port()
    secret = secrets.token_urlsafe(48); instance = str(uuid.uuid4()); password = secrets.token_urlsafe(30) + 'Aa9!'
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'server'), 'DJANGO_SETTINGS_MODULE': 'gep.settings',
           'GEP_DATA_DIR': str(data), 'GEP_SECRET_KEY': secret, 'GEP_EXPECTED_INSTANCE': instance,
           'GEP_EXPERIMENT_HOST': '127.0.0.1', 'GEP_PUBLIC_API': f'http://127.0.0.1:{port}'}
    initialize = '''import django,os,uuid;django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from core.models import AccountProfile,Instance,Principal
call_command('migrate',verbosity=0)
owner=get_user_model().objects.create_user('moss_engineering_owner',password=os.environ['MOSS_OWNER_PASSWORD'])
Instance.objects.create(instance_id=uuid.UUID(os.environ['GEP_EXPECTED_INSTANCE']),owner=owner,authorization_version=2)
Principal.objects.create(user=owner)
AccountProfile.objects.create(user=owner,role='user',policy_version=2,must_change_password=False,auth_version=1,revision=0)
'''
    run([str(PYTHON), '-c', initialize], env={**env, 'MOSS_OWNER_PASSWORD': password}, log=evidence / 'initialize.log')
    for name, value in [('secret', secret), ('instance', instance)]:
        fd = os.open(data / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream: stream.write(value)
    job = {'evidence_dir': str(evidence), 'admin': f'http://admin.localhost:{port}',
           'credentials': {'username': 'moss_engineering_owner', 'password': password},
           'web_zip': packages['web']['path'], 'native_zip': packages['native']['archive'],
           'native_descriptor': packages['native']['descriptor']}
    job_file = evidence / 'private_job.json'; private_json(job_file, job)
    summary = {'verdict': 'FAILED', 'evidence': str(evidence.relative_to(ROOT)),
               'build': str(build.relative_to(ROOT)), 'human_manual_integration': 'NOT_RUN',
               'native_only': args.native_only, 'web_verification': 'NOT_RUN' if args.native_only else 'requested'}
    log = (evidence / 'server.log').open('w')
    server = subprocess.Popen([str(ROOT / '.venv/bin/gunicorn'), 'gep.wsgi:application', '--bind', f'127.0.0.1:{port}',
                               '--workers', '1', '--threads', '4', '--timeout', '300', '--access-logfile', '/dev/null'],
                              cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=1): break
            except OSError:
                if server.poll() is not None: raise RuntimeError('Owned service exited before readiness')
                time.sleep(.2)
        else: raise RuntimeError('Owned service startup timeout')
        driver = ROOT / 'tests/browser/moss_duel_e2e.mjs'
        run(['node', str(driver), 'native-prepare' if args.native_only else 'prepare', str(job_file)], log=evidence / 'browser.log', timeout=540)
        report = json.loads((evidence / 'browser_report.json').read_text())
        if not args.native_only:
            before = json.loads((evidence / 'web_before_upload.json').read_text())
            web_export = [json.loads(line) for line in (evidence / 'web_authorized.jsonl').read_text().splitlines()]
            summary['web'] = compare(data / 'gep.sqlite3', before, web_export, 'Web')
            preview_rows = json.loads((evidence / 'preview_indexeddb.json').read_text())
            preview_ids = {row['id'].replace('-', '') for row in preview_rows if row['kind'] == 'local'}
            with sqlite3.connect(f'file:{data / "gep.sqlite3"}?mode=ro', uri=True) as connection:
                session_ids = {row[0] for row in connection.execute('SELECT id FROM core_session')}
            assert session_ids == {before['id'].replace('-', '')}, 'Isolated preview or another unexpected request created a server session'
            assert not preview_ids & session_ids, 'Isolated preview incorrectly created server session'
            summary['web_verification'] = 'PASS'
        verify_native(evidence, project, job, report, summary)
    except Exception as error:
        summary['error'] = str(error)
        raise
    finally:
        (evidence / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        server.send_signal(signal.SIGTERM)
        try: server.wait(timeout=15)
        except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
        log.close()
        print(json.dumps(summary, indent=2))

if __name__ == '__main__': main()
