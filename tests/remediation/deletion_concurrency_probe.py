#!/usr/bin/env python
"""P03R04 real multi-process deletion concurrency probe.

Run with the project virtualenv:

    .venv/bin/python tests/remediation/deletion_concurrency_probe.py

The probe builds its own temporary ``GEP_DATA_DIR`` with a real SQLite file and
runs real OS processes (never threads) to prove, from outside the test process:

1. an event upload racing the deletion mark serializes at the commit boundary:
   no event is accepted after the mark, the original token gets
   ``study_deleted`` and never a successful upload again;
2. an already-running export ZIP generation racing mark+cleanup leaves no
   accessible artifact and no new export publish after deletion;
3. a SIGKILL in the middle of the bounded cleanup leaves the persisted
   ``marked``/``cleaning`` state (never a false ``complete``) and a later run
   resumes to completion;
4. a SIGKILL in the middle of the bounded file step keeps the unfinished spool
   ownership in the persisted manifest, and a later run removes exactly the
   owned files (never another export or an unknown file) and completes;
5. illegal cleanup parameters (``--batch-size 0``/negative, negative
   ``--max-batches``) exit non-zero instead of printing an error and succeeding.

Writes one JSON report into a fresh unique directory (``GEP_EVIDENCE_DIR`` or
``local_data/phase03_remediation_20260923/p03r04/``) and exits non-zero on any
failed invariant.
"""
import json
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_BASE = REPO_ROOT / 'local_data' / 'phase03_remediation_20260923'
SERVER_DIR = REPO_ROOT / 'server'
PROBE_TIMEOUT = 180
OWNER_PASSWORD = 'Probe-owner-password-1'
OPERATOR_PASSWORD = 'Probe-operator-password-1'

PRELUDE = (
    "import os, sys, django\n"
    f"sys.path.insert(0, {str(SERVER_DIR)!r})\n"
    "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gep.settings')\n"
    "django.setup()\n"
)


def evidence_root():
    given = (os.environ.get('GEP_EVIDENCE_DIR') or '').strip()
    base = Path(given) if given else EVIDENCE_BASE
    if given:
        base = Path(given) / 'p03r04_probe'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    root = base / f'{stamp}-{secrets.token_hex(4)}'
    if root.exists():
        raise SystemExit(f'evidence root already exists, refusing: {root}')
    root.mkdir(parents=True, exist_ok=False)
    return root


def run_python(script, env, timeout=PROBE_TIMEOUT):
    return subprocess.run([sys.executable, '-c', PRELUDE + script], cwd=REPO_ROOT, env=env,
                          capture_output=True, text=True, timeout=timeout)


def manage(env, *arguments, timeout=PROBE_TIMEOUT):
    return subprocess.run([sys.executable, str(SERVER_DIR / 'manage.py'), *arguments], cwd=REPO_ROOT,
                          env=env, capture_output=True, text=True, timeout=timeout)


def read_state(db_path, study_uuid):
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        row = connection.execute(
            'SELECT state, cursor FROM core_studydeletion WHERE study_uuid = ?',
            (str(study_uuid).replace('-', ''),)).fetchone()
        return row
    finally:
        connection.close()


def study_exists(db_path, study_uuid):
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        row = connection.execute('SELECT lifecycle FROM core_study WHERE id = ?',
                                 (str(study_uuid).replace('-', ''),)).fetchone()
        return row
    finally:
        connection.close()


def study_state(db_path, study_uuid):
    """(lifecycle, public, recruitment) of one study row, read directly."""
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        return connection.execute('SELECT lifecycle, public, recruitment FROM core_study WHERE id = ?',
                                  (str(study_uuid).replace('-', ''),)).fetchone()
    finally:
        connection.close()


SETUP = r'''
import json, uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from core import exports, services
from core.models import (AccountProfile, Build, Event, Grant, Instance, Participant,
                         Release, Session, Study)
User = get_user_model()
owner = User.objects.create_user('probe_owner', password=__OWNER__)
Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
AccountProfile.objects.create(user=owner, role='user', policy_version=2)
operator = User.objects.create_user('probe_operator', password=__OPERATOR__)
AccountProfile.objects.create(user=operator, role='user', policy_version=2)

def make_study(title):
    study = Study.objects.create(title=title, recruitment='open', max_sessions=50)
    Grant.objects.bulk_create([Grant(user=operator, study=study, action=action) for action in (
        'study.view', 'study.configure', 'data.export_raw', 'identity_mapping.read',
        'session.recover', 'study.delete')])
    Grant.objects.create(user=owner, study=study, action='study.view')
    build = Build.objects.create(study=study, digest=uuid.uuid4().hex + uuid.uuid4().hex,
        descriptor={'platform': 'godot_web', 'version': '1.0',
                    'schemas': {'exp.rt': {'id': 'rt', 'version': '1', 'schema': {'type': 'object'}}}})
    release = Release.objects.create(study=study, build=build, config={'mode': 'anonymous'}, approved=True)
    study.current_release = release
    study.save(update_fields=['current_release'])
    return study, release

def admit(study, release, proof):
    instance = Instance.objects.get(pk=1)
    return services.admit_request({'operation_id': str(uuid.uuid4()), 'instance_id': str(instance.instance_id),
                                   'study_id': str(study.id), 'release_id': str(release.id),
                                   'proof': proof, 'participant_code': None})

race_study, race_release = make_study('probe race study')
race_session, race_token = admit(race_study, race_release, 'r' * 48)
race_session.completion = None
race_session.save(update_fields=['completion'])

export_study, export_release = make_study('probe export study')
export_session, export_token = admit(export_study, export_release, 'e' * 48)
item = exports.create_v2_export(operator, export_study.id, view='unmapped', language='zh')

kill_study, kill_release = make_study('probe kill study')
kill_session, kill_token = admit(kill_study, kill_release, 'k' * 48)
for start in range(0, 200, 50):
    batch = {'batch_id': str(uuid.uuid4()), 'events': [
        {'protocol_version': 'gep/1', 'event_id': str(uuid.uuid4()), 'session_id': str(kill_session.id),
         'segment_id': str(uuid.uuid4()), 'sequence': start + offset + 1, 'event_type': 'exp.rt',
         'schema_id': 'rt', 'schema_version': '1', 'payload': {'rt_ms': start + offset}}
        for offset in range(50)]}
    services.receive(kill_session.id, kill_token, batch)

killed_export_study, killed_export_release = make_study('probe killed export study')
killed_export_session, killed_export_token = admit(killed_export_study, killed_export_release, 'x' * 48)
killed_item = exports.create_v2_export(operator, killed_export_study.id, view='unmapped', language='zh')

file_kill_study, file_kill_release = make_study('probe file kill study')
file_kill_session, file_kill_token = admit(file_kill_study, file_kill_release, 'f' * 48)
file_kill_item = exports.create_v2_export(operator, file_kill_study.id, view='unmapped', language='zh')

barrier_study, barrier_release = make_study('probe barrier study')

payload = {'race_study': str(race_study.id), 'race_session': str(race_session.id), 'race_token': race_token,
           'export_study': str(export_study.id), 'export_item': str(item.id),
           'export_operation': str(export_session.operation), 'export_release': str(export_release.id),
           'kill_study': str(kill_study.id), 'kill_session': str(kill_session.id),
           'killed_export_study': str(killed_export_study.id), 'killed_export_item': str(killed_item.id),
           'file_kill_study': str(file_kill_study.id), 'file_kill_item': str(file_kill_item.id),
           'barrier_study': str(barrier_study.id)}
with open(__OUTPUT__, 'w') as stream:
    json.dump(payload, stream)
print('setup ok')
'''

WRITER = r'''
import json, time, uuid
from core import services
from core.models import Session
from core.protocol import Rejected
session = Session.objects.get(pk=__SESSION__)
token = __TOKEN__
outcomes = []
for index in range(60):
    batch = {'batch_id': str(uuid.uuid4()), 'events': [
        {'protocol_version': 'gep/1', 'event_id': str(uuid.uuid4()), 'session_id': str(session.id),
         'segment_id': str(uuid.uuid4()), 'sequence': index + 1, 'event_type': 'exp.rt',
         'schema_id': 'rt', 'schema_version': '1', 'payload': {'rt_ms': index}}]}
    try:
        services.receive(session.id, token, batch)
        outcomes.append('ok')
    except Rejected as error:
        outcomes.append(error.code)
    except Exception as error:
        outcomes.append('error:' + type(error).__name__)
    time.sleep(0.001)
with open(__OUTPUT__, 'w') as stream:
    json.dump({'outcomes': outcomes}, stream)
print('writer done')
'''

MARKER = r'''
import json, time
from django.contrib.auth import get_user_model
from core import deletion
from core.models import Study
time.sleep(__DELAY__)
study = Study.objects.get(pk=__STUDY__)
operator = get_user_model().objects.get(username='probe_operator')
result = deletion.mark_study_deletion(operator, study, __PASSWORD__)
with open(__OUTPUT__, 'w') as stream:
    json.dump({'ok': True, 'state': result['state']}, stream)
print('marker done')
'''

FRESH_ATTEMPT = r'''
import json, uuid
from core import services
from core.protocol import Rejected
from core.models import Instance, Session, Study
study = Study.objects.get(pk=__STUDY__)
session = Session.objects.get(pk=__SESSION__)
instance = Instance.objects.get(pk=1)
codes = {}
# The original operation + proof + exact binding is the permanent stop.
try:
    services.admit_request({'operation_id': str(session.operation), 'instance_id': str(instance.instance_id),
                            'study_id': str(study.id), 'release_id': str(session.release_id),
                            'proof': __PROOF__, 'participant_code': None})
    codes['admit'] = 'ok'
except Rejected as error:
    codes['admit'] = error.code
# A fresh operation reusing the proof is not the original operation.
try:
    services.admit_request({'operation_id': str(uuid.uuid4()), 'instance_id': str(instance.instance_id),
                            'study_id': str(study.id), 'release_id': str(session.release_id),
                            'proof': __PROOF__, 'participant_code': None})
    codes['fresh_admit'] = 'ok'
except Rejected as error:
    codes['fresh_admit'] = error.code
# A mismatched release is never the original binding either.
try:
    services.admit_request({'operation_id': str(session.operation), 'instance_id': str(instance.instance_id),
                            'study_id': str(study.id), 'release_id': str(uuid.uuid4()),
                            'proof': __PROOF__, 'participant_code': None})
    codes['mismatched_admit'] = 'ok'
except Rejected as error:
    codes['mismatched_admit'] = error.code
try:
    services.receive(session.id, __TOKEN__, {'batch_id': str(uuid.uuid4()), 'events': [
        {'protocol_version': 'gep/1', 'event_id': str(uuid.uuid4()), 'session_id': str(session.id),
         'segment_id': str(uuid.uuid4()), 'sequence': 999, 'event_type': 'exp.rt',
         'schema_id': 'rt', 'schema_version': '1', 'payload': {'rt_ms': 999}}]})
    codes['receive'] = 'ok'
except Rejected as error:
    codes['receive'] = error.code
with open(__OUTPUT__, 'w') as stream:
    json.dump(codes, stream)
print('fresh attempt done')
'''

GENERATOR = r'''
import json, time
from core import exports
from core.models import Export
from core.protocol import Rejected
outcomes = []
for index in range(10):
    item = Export.objects.filter(pk=__ITEM__).first()
    if item is None:
        outcomes.append('row_gone')
    else:
        try:
            exports.render_zip_download(item)
            outcomes.append('ok')
        except Rejected as error:
            outcomes.append(error.code)
        except Exception as error:
            outcomes.append('error:' + type(error).__name__)
    time.sleep(0.002)
with open(__OUTPUT__, 'w') as stream:
    json.dump({'outcomes': outcomes}, stream)
print('generator done')
'''

KILLED_GENERATOR = r'''
import time
from core import exports
from core.models import Export
item = Export.objects.get(pk=__ITEM__)
exports.render_zip_download(item)
with open(__FLAG__, 'w') as stream:
    stream.write('published')
print('killed generator published', flush=True)
time.sleep(120)
'''

EXPORT_RETRY = r'''
import json, uuid
from core import services
from core.protocol import Rejected
from core.models import Instance
instance = Instance.objects.get(pk=1)
codes = {}
try:
    services.admit_request({'operation_id': __OPERATION__, 'instance_id': str(instance.instance_id),
                            'study_id': __STUDY__, 'release_id': __RELEASE__,
                            'proof': __PROOF__, 'participant_code': None})
    codes['original'] = 'ok'
except Rejected as error:
    codes['original'] = error.code
try:
    services.admit_request({'operation_id': str(uuid.uuid4()), 'instance_id': str(instance.instance_id),
                            'study_id': __STUDY__, 'release_id': __RELEASE__,
                            'proof': __PROOF__, 'participant_code': None})
    codes['fresh'] = 'ok'
except Rejected as error:
    codes['fresh'] = error.code
with open(__OUTPUT__, 'w') as stream:
    json.dump(codes, stream)
print('export retry done')
'''

BARRIER_WRITER = r'''
import json, os, time
from django.contrib.auth import get_user_model
from core import publication
from core.models import Study
from core.protocol import Rejected
study = Study.objects.get(pk=__STUDY__)
# The page's first read: the study was still active before the mark.
if study.lifecycle != 'active':
    raise SystemExit('study was not active at the pre-check')
with open(__READY__, 'w') as stream:
    stream.write('checked')
deadline = time.monotonic() + 60
while not os.path.exists(__GO__):
    if time.monotonic() > deadline:
        raise SystemExit('barrier timeout')
    time.sleep(0.01)
operator = get_user_model().objects.get(username='probe_operator')
codes = {}
try:
    publication.update_policy(operator, study, study.revision, {'public': True})
    codes['update_policy'] = 'ok'
except Rejected as error:
    codes['update_policy'] = error.code
with open(__OUTPUT__, 'w') as stream:
    json.dump(codes, stream)
print('barrier writer done')
'''

DELETER = r'''
import json, time
from django.contrib.auth import get_user_model
from core import deletion
from core.models import Study, StudyDeletion
time.sleep(__DELAY__)
study = Study.objects.get(pk=__STUDY__)
operator = get_user_model().objects.get(username='probe_operator')
deletion.mark_study_deletion(operator, study, __PASSWORD__)
while True:
    row = StudyDeletion.objects.filter(study_uuid=study.pk).first()
    if row is None or row.state == 'complete':
        break
    result = deletion.run_cleanup(row.pk, batch_size=200)
    if result['state'] == 'failed':
        raise SystemExit('cleanup failed: ' + result['error_code'])
with open(__OUTPUT__, 'w') as stream:
    json.dump({'state': 'complete'}, stream)
print('deleter done')
'''

EXPORT_ACCESS = r'''
import json
from django.test import Client
from core.models import Export
codes = {'rows': Export.objects.filter(study_id=__STUDY__).count()}
response = Client().get('/v1/admin/exports/__ITEM__/download?format=zip')
codes['http_status'] = response.status_code
codes['http_code'] = json.loads(response.content).get('code') if response.status_code != 200 else 'served'
with open(__OUTPUT__, 'w') as stream:
    json.dump(codes, stream)
print('export access done')
'''

VERIFY_CLEARED = r'''
import json
from core.models import (Audit, Build, DeletedSession, Event, Export, Grant, Participant,
                         Release, Session, Study, StudyDeletion)
study = __STUDY__
checks = {
    'study': Study.objects.filter(pk=study).count(),
    'sessions': Session.objects.filter(release__study_id=study).count(),
    'participants': Participant.objects.filter(study_id=study).count(),
    'events': Event.objects.filter(session__release__study_id=study).count(),
    'exports': Export.objects.filter(study_id=study).count(),
    'releases': Release.objects.filter(study_id=study).count(),
    'builds': Build.objects.filter(study_id=study).count(),
    'grants': Grant.objects.filter(study_id=study).count(),
    'audits': Audit.objects.filter(study_id=study).count(),
    'tombstones': DeletedSession.objects.filter(study_uuid=study).count(),
    'state': StudyDeletion.objects.get(study_uuid=study).state,
    'manifest': len(StudyDeletion.objects.get(study_uuid=study).file_manifest or []),
}
with open(__OUTPUT__, 'w') as stream:
    json.dump(checks, stream)
print('verify done')
'''


def write_script(root, name, script):
    path = root / f'{name}.py'
    path.write_text(script)
    return path


def main():
    root = evidence_root()
    data_dir = root / 'data'
    data_dir.mkdir()
    db_path = data_dir / 'gep.sqlite3'
    env = dict(os.environ, GEP_DATA_DIR=str(data_dir), GEP_SECRET_KEY='probe-secret-key',
               PYTHONPATH=str(SERVER_DIR))
    report = {'probe': 'p03r04-deletion-concurrency/v1', 'root': str(root)}
    try:
        migrated = manage(env, 'migrate', '--noinput')
        assert migrated.returncode == 0, migrated.stderr[-2000:]
        payload_path = root / 'payload.json'
        setup = (SETUP.replace('__OUTPUT__', repr(str(payload_path)))
                 .replace('__OWNER__', repr(OWNER_PASSWORD)).replace('__OPERATOR__', repr(OPERATOR_PASSWORD)))
        setup_result = run_python(setup, env)
        assert setup_result.returncode == 0, setup_result.stdout + setup_result.stderr
        payload = json.loads(payload_path.read_text())

        # --- 1. event upload vs the deletion mark ---------------------------
        writer_out = root / 'writer.json'
        marker_out = root / 'marker.json'
        writer_script = (WRITER.replace('__SESSION__', repr(payload['race_session']))
                         .replace('__TOKEN__', repr(payload['race_token']))
                         .replace('__OUTPUT__', repr(str(writer_out))))
        marker_script = (MARKER.replace('__STUDY__', repr(payload['race_study']))
                         .replace('__DELAY__', '0.03')
                         .replace('__PASSWORD__', repr(OPERATOR_PASSWORD))
                         .replace('__OUTPUT__', repr(str(marker_out))))
        writer = subprocess.Popen([sys.executable, '-c', PRELUDE + writer_script], cwd=REPO_ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        marker = subprocess.Popen([sys.executable, '-c', PRELUDE + marker_script], cwd=REPO_ROOT, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        writer_out_text, writer_err = writer.communicate(timeout=PROBE_TIMEOUT)
        marker_out_text, marker_err = marker.communicate(timeout=PROBE_TIMEOUT)
        assert writer.returncode == 0, writer_out_text + writer_err
        assert marker.returncode == 0, marker_out_text + marker_err
        outcomes = json.loads(writer_out.read_text())['outcomes']
        marker_state = json.loads(marker_out.read_text())['state']
        assert marker_state == 'marked', marker_state
        allowed = {'ok', 'study_deleted', 'session_unavailable'}
        assert set(outcomes) <= allowed, outcomes
        first_refusal = next((index for index, code in enumerate(outcomes) if code != 'ok'), len(outcomes))
        assert 'ok' not in outcomes[first_refusal:], outcomes
        report['race_write_vs_mark'] = {'outcomes': outcomes, 'ok_before_refusal': outcomes.count('ok'),
                                        'refusal': outcomes[first_refusal] if first_refusal < len(outcomes) else None}

        fresh_out = root / 'fresh.json'
        fresh_script = (FRESH_ATTEMPT.replace('__STUDY__', repr(payload['race_study']))
                        .replace('__SESSION__', repr(payload['race_session']))
                        .replace('__PROOF__', repr('r' * 48)).replace('__TOKEN__', repr(payload['race_token']))
                        .replace('__OUTPUT__', repr(str(fresh_out))))
        fresh_result = run_python(fresh_script, env)
        assert fresh_result.returncode == 0, fresh_result.stdout + fresh_result.stderr
        fresh = json.loads(fresh_out.read_text())
        assert fresh['admit'] == 'study_deleted', fresh
        assert fresh['fresh_admit'] == 'admission_unavailable', fresh
        assert fresh['mismatched_admit'] == 'admission_unavailable', fresh
        assert fresh['receive'] == 'study_deleted', fresh
        report['race_write_vs_mark']['post_mark_admit'] = fresh['admit']
        report['race_write_vs_mark']['post_mark_fresh_admit'] = fresh['fresh_admit']
        report['race_write_vs_mark']['post_mark_mismatched_admit'] = fresh['mismatched_admit']
        report['race_write_vs_mark']['post_mark_receive'] = fresh['receive']

        # --- 2. export generation vs mark + cleanup -------------------------
        generator_out = root / 'generator.json'
        deleter_out = root / 'deleter.json'
        generator_script = (GENERATOR.replace('__ITEM__', repr(payload['export_item']))
                            .replace('__OUTPUT__', repr(str(generator_out))))
        deleter_script = (DELETER.replace('__STUDY__', repr(payload['export_study']))
                          .replace('__DELAY__', '0.01').replace('__PASSWORD__', repr(OPERATOR_PASSWORD))
                          .replace('__OUTPUT__', repr(str(deleter_out))))
        generator = subprocess.Popen([sys.executable, '-c', PRELUDE + generator_script], cwd=REPO_ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deleter = subprocess.Popen([sys.executable, '-c', PRELUDE + deleter_script], cwd=REPO_ROOT, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        generator_text, generator_err = generator.communicate(timeout=PROBE_TIMEOUT)
        deleter_text, deleter_err = deleter.communicate(timeout=PROBE_TIMEOUT)
        assert generator.returncode == 0, generator_text + generator_err
        assert deleter.returncode == 0, deleter_text + deleter_err
        generation = json.loads(generator_out.read_text())['outcomes']
        assert set(generation) <= {'ok', 'row_gone', 'export_unavailable'}, generation
        access_out = root / 'export_access.json'
        access_script = (EXPORT_ACCESS.replace('__STUDY__', repr(payload['export_study']))
                         .replace('__ITEM__', payload['export_item'])
                         .replace('__OUTPUT__', repr(str(access_out))))
        access_result = run_python(access_script, env)
        assert access_result.returncode == 0, access_result.stdout + access_result.stderr
        access = json.loads(access_out.read_text())
        assert access['rows'] == 0, access
        assert access['http_status'] == 404 and access['http_code'] == 'export_unavailable', access
        leftover = (data_dir / 'exports' / f"{payload['export_item']}.zip").exists()
        assert not leftover, 'an inaccessible orphan ZIP was left behind'
        report['race_export_vs_delete'] = {'generation': generation, 'http_status': access['http_status'],
                                           'leftover_file': leftover}

        # After the cleanup completed, the original operation binding still gets
        # the permanent stop and a fresh operation stays un-enumerable.
        retry_out = root / 'export_retry.json'
        retry_script = (EXPORT_RETRY.replace('__STUDY__', repr(payload['export_study']))
                        .replace('__RELEASE__', repr(payload['export_release']))
                        .replace('__OPERATION__', repr(payload['export_operation']))
                        .replace('__PROOF__', repr('e' * 48))
                        .replace('__OUTPUT__', repr(str(retry_out))))
        retry_result = run_python(retry_script, env)
        assert retry_result.returncode == 0, retry_result.stdout + retry_result.stderr
        retry = json.loads(retry_out.read_text())
        assert retry['original'] == 'study_deleted', retry
        assert retry['fresh'] == 'admission_unavailable', retry
        report['race_export_vs_delete']['post_cleanup_retry'] = retry

        # --- 2b. a generation killed while holding the publish boundary ------
        killed_out = root / 'killed_generator.json'
        killed_flag = root / 'killed_generator.flag'
        killed_script = (KILLED_GENERATOR.replace('__ITEM__', repr(payload['killed_export_item']))
                         .replace('__FLAG__', repr(str(killed_flag))))
        killed_generator = subprocess.Popen([sys.executable, '-c', PRELUDE + killed_script], cwd=REPO_ROOT,
                                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        spool_path = data_dir / 'exports' / f"{payload['killed_export_item']}.zip"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not spool_path.exists() and killed_generator.poll() is None:
            time.sleep(0.01)
        assert spool_path.exists(), 'the killed generator never published its spool file'
        killed_generator.send_signal(signal.SIGKILL)
        killed_generator.communicate(timeout=30)
        assert spool_path.exists(), 'the spool file vanished before the cleanup ran'
        killed_deleter_out = root / 'killed_deleter.json'
        killed_deleter_script = (DELETER.replace('__STUDY__', repr(payload['killed_export_study']))
                                 .replace('__DELAY__', '0').replace('__PASSWORD__', repr(OPERATOR_PASSWORD))
                                 .replace('__OUTPUT__', repr(str(killed_deleter_out))))
        killed_deleter = run_python(killed_deleter_script, env)
        assert killed_deleter.returncode == 0, killed_deleter.stdout + killed_deleter.stderr
        spool_files = sorted(path.name for path in (data_dir / 'exports').iterdir()
                             if path.name.startswith(f".{payload['killed_export_item']}.")
                             or path.name == f"{payload['killed_export_item']}.zip")
        assert spool_files == [], spool_files
        report['killed_generation_cleanup'] = {'state': 'complete', 'spool_files_left': spool_files}

        # --- 4. barrier: pre-check before the mark, commit after it ----------
        # A request that read the study while it was active, then submits its
        # write only after the mark committed, must be refused by the final
        # transaction check - not by the ideal execution order.
        ready = root / 'barrier.ready'
        go = root / 'barrier.go'
        barrier_out = root / 'barrier_writer.json'
        barrier_script = (BARRIER_WRITER.replace('__STUDY__', repr(payload['barrier_study']))
                          .replace('__READY__', repr(str(ready))).replace('__GO__', repr(str(go)))
                          .replace('__OUTPUT__', repr(str(barrier_out))))
        barrier_writer = subprocess.Popen([sys.executable, '-c', PRELUDE + barrier_script], cwd=REPO_ROOT,
                                          env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not ready.exists():
            assert barrier_writer.poll() is None, 'the barrier writer exited before its pre-check'
            time.sleep(0.01)
        assert ready.exists(), 'the barrier writer never reached its pre-check'
        barrier_mark_out = root / 'barrier_mark.json'
        barrier_mark_script = (MARKER.replace('__STUDY__', repr(payload['barrier_study']))
                               .replace('__DELAY__', '0').replace('__PASSWORD__', repr(OPERATOR_PASSWORD))
                               .replace('__OUTPUT__', repr(str(barrier_mark_out))))
        barrier_mark = run_python(barrier_mark_script, env)
        assert barrier_mark.returncode == 0, barrier_mark.stdout + barrier_mark.stderr
        assert not barrier_out.exists(), 'the barrier writer committed before the mark'
        go.write_text('go')
        barrier_text, barrier_err = barrier_writer.communicate(timeout=60)
        assert barrier_writer.returncode == 0, barrier_text + barrier_err
        barrier = json.loads(barrier_out.read_text())
        assert barrier['update_policy'] == 'study_deleted', barrier
        state = study_state(db_path, payload['barrier_study'])
        assert state is not None and state[0] == 'deleting' and state[1] == 0 and state[2] == 'open', state
        report['barrier_write_vs_mark'] = {'pre_checked_active': True,
                                           'update_policy': barrier['update_policy'],
                                           'lifecycle_after': state[0], 'public_after': state[1]}

        # --- 3. SIGKILL during cleanup, then resume -------------------------
        kill_marker_out = root / 'kill_marker.json'
        kill_marker_script = (MARKER.replace('__STUDY__', repr(payload['kill_study']))
                              .replace('__DELAY__', '0')
                              .replace('__PASSWORD__', repr(OPERATOR_PASSWORD))
                              .replace('__OUTPUT__', repr(str(kill_marker_out))))
        kill_marker = run_python(kill_marker_script, env)
        assert kill_marker.returncode == 0, kill_marker.stdout + kill_marker.stderr
        cleanup = subprocess.Popen([sys.executable, str(SERVER_DIR / 'manage.py'),
                                    'cleanup_deleted_studies', '--batch-size', '1'],
                                   cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 60
        killed_state = None
        while time.monotonic() < deadline:
            try:
                state_row = read_state(db_path, payload['kill_study'])
            except sqlite3.Error:
                state_row = None
            # Kill as soon as this job really advanced: the long events step
            # holds the cursor for hundreds of one-row batches, so the kill
            # lands mid-cleanup instead of racing the final completion window
            # (where the study row may legitimately be gone while the persisted
            # state is still ``cleaning``).
            if state_row is not None and state_row[1] >= 2:
                killed_state = state_row
                cleanup.send_signal(signal.SIGKILL)
                break
            if cleanup.poll() is not None:
                break
            time.sleep(0.01)
        cleanup.communicate(timeout=30)
        assert killed_state is not None, 'cleanup finished before it could be killed'
        state_after_kill = read_state(db_path, payload['kill_study'])
        assert state_after_kill is not None and state_after_kill[0] != 'complete', state_after_kill
        assert study_exists(db_path, payload['kill_study']) is not None, 'study vanished without complete state'
        report['kill_resume'] = {'cursor_at_kill': killed_state[1], 'state_at_kill': killed_state[0],
                                 'state_after_kill': state_after_kill[0],
                                 'study_row_after_kill': True}

        resumed = manage(env, 'cleanup_deleted_studies', '--batch-size', '50')
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        verify_out = root / 'verify.json'
        verify_script = (VERIFY_CLEARED.replace('__STUDY__', repr(payload['kill_study']))
                         .replace('__OUTPUT__', repr(str(verify_out))))
        verify_result = run_python(verify_script, env)
        assert verify_result.returncode == 0, verify_result.stdout + verify_result.stderr
        checks = json.loads(verify_out.read_text())
        assert checks['state'] == 'complete', checks
        assert checks['manifest'] == 0, checks
        for key in ('study', 'sessions', 'participants', 'events', 'exports', 'releases', 'builds', 'grants', 'audits'):
            assert checks[key] == 0, (key, checks)
        assert checks['tombstones'] >= 1, checks
        report['kill_resume']['after_resume'] = checks

        # --- 5. SIGKILL during the bounded file step, then resume -----------
        # A durable spool larger than one batch: the command removes at most
        # ``--batch-size`` real files per batch (the ZIP and each temp file or
        # link is one unit), a SIGKILL in the middle keeps the unfinished
        # ownership in the persisted manifest, and a later run finishes without
        # a false complete and without touching unrelated files.
        file_kill_item = payload['file_kill_item']
        exports_root = data_dir / 'exports'
        exports_root.mkdir(exist_ok=True)
        spool = [exports_root / f'{file_kill_item}.zip']
        spool += [exports_root / f'.{file_kill_item}.{index:016x}.tmp' for index in range(60)]
        for path in spool:
            path.write_bytes(b'probe spool bytes')
        unrelated = [exports_root / f'{uuid.uuid4()}.zip',
                     exports_root / f'.{uuid.uuid4()}.0123456789abcdef.tmp',
                     exports_root / 'probe-notes.txt']
        for path in unrelated:
            path.write_bytes(b'probe unrelated bytes')

        file_kill_marker_out = root / 'file_kill_marker.json'
        file_kill_marker_script = (MARKER.replace('__STUDY__', repr(payload['file_kill_study']))
                                   .replace('__DELAY__', '0')
                                   .replace('__PASSWORD__', repr(OPERATOR_PASSWORD))
                                   .replace('__OUTPUT__', repr(str(file_kill_marker_out))))
        file_kill_marker = run_python(file_kill_marker_script, env)
        assert file_kill_marker.returncode == 0, file_kill_marker.stdout + file_kill_marker.stderr

        def owned_spool():
            return [path for path in exports_root.iterdir()
                    if path.name == f'{file_kill_item}.zip' or path.name.startswith(f'.{file_kill_item}.')]

        file_cleanup = subprocess.Popen(
            [sys.executable, str(SERVER_DIR / 'manage.py'), 'cleanup_deleted_studies',
             '--batch-size', '1', '--study', payload['file_kill_study']],
            cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 120
        killed = False
        while time.monotonic() < deadline:
            if file_cleanup.poll() is not None:
                break
            if len(owned_spool()) < len(spool):
                file_cleanup.send_signal(signal.SIGKILL)
                killed = True
                break
            time.sleep(0.002)
        file_cleanup.communicate(timeout=30)
        assert killed, 'the file cleanup finished before it could be killed'
        left_after_kill = owned_spool()
        assert left_after_kill, 'the SIGKILL left no owned spool file for the resume'
        state_after_file_kill = read_state(db_path, payload['file_kill_study'])
        assert state_after_file_kill is not None and state_after_file_kill[0] != 'complete', state_after_file_kill
        report['file_kill_resume'] = {'state_after_kill': state_after_file_kill[0],
                                      'files_left_at_kill': len(left_after_kill),
                                      'files_removed_at_kill': len(spool) - len(left_after_kill)}

        resumed_files = manage(env, 'cleanup_deleted_studies', '--batch-size', '50',
                               '--study', payload['file_kill_study'])
        assert resumed_files.returncode == 0, resumed_files.stdout + resumed_files.stderr
        assert owned_spool() == [], owned_spool()
        verify_file_out = root / 'verify_file_kill.json'
        verify_file_script = (VERIFY_CLEARED.replace('__STUDY__', repr(payload['file_kill_study']))
                              .replace('__OUTPUT__', repr(str(verify_file_out))))
        verify_file_result = run_python(verify_file_script, env)
        assert verify_file_result.returncode == 0, verify_file_result.stdout + verify_file_result.stderr
        file_checks = json.loads(verify_file_out.read_text())
        assert file_checks['state'] == 'complete' and file_checks['manifest'] == 0, file_checks
        for key in ('study', 'sessions', 'participants', 'events', 'exports', 'releases', 'builds',
                    'grants', 'audits'):
            assert file_checks[key] == 0, (key, file_checks)
        for path in unrelated:
            assert path.read_bytes() == b'probe unrelated bytes', path
        report['file_kill_resume']['after_resume'] = {'state': file_checks['state'],
                                                      'owned_files_left': 0,
                                                      'unrelated_files_kept': len(unrelated)}

        # --- 6. illegal cleanup parameters exit non-zero ---------------------
        illegal = {}
        for arguments in (('--batch-size', '0'), ('--batch-size', '-3'), ('--max-batches', '-1')):
            refused = manage(env, 'cleanup_deleted_studies', *arguments)
            illegal[' '.join(arguments)] = refused.returncode
            assert refused.returncode != 0, (arguments, refused.stdout, refused.stderr)
        report['illegal_parameters'] = illegal

        report['status'] = 'PASS'
    except Exception as error:  # noqa: BLE001 - the probe reports every failure honestly
        report['status'] = 'FAIL'
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        report_path = root / 'probe_report.json'
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + '\n')
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0 if report.get('status') == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
