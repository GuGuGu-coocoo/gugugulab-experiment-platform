#!/usr/bin/env python
"""Independent capacity measurement for the export v2 core (P03R07R).

Run:

    .venv/bin/python tools/remediation_export_capacity.py --verify

The tool never touches the development instance. It creates its own synthetic
SQLite database under a brand-new, unique evidence root and drives the real
``core.exports`` creation and rendering path:

* the maximum-size sample (participants 10000, sessions 20000, events 10000)
  with elapsed time, peak memory, snapshot bytes, CSV/JSONL/metadata bytes and
  the largest spreadsheet cell;
* the exact boundary and the one-over case of every hard limit (participants,
  sessions, events, snapshot UTF-8 bytes, CSV cell characters, CSV pair total,
  generation time), with no ready export row left behind by a refusal;
* the JSONL fallback: a cell over its boundary refuses only CSV/ZIP, while the
  lossless JSONL keeps the whole payload.

The evidence root must be wholly new: an existing root (even an empty one) and
any symlinked path component are refused before anything is created, and every
run keeps its synthetic database, successful or failed, so the measured bytes
stay re-checkable. The report carries numbers and error codes only - never a
payload byte, a roster code, an account or a secret - and any failed check
exits non-zero.
"""
import argparse
import gc
import json
import os
import resource
import secrets
import sys
import time
import tracemalloc
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_BASE = REPO_ROOT / 'local_data' / 'phase03_remediation_20260923'


def utc_stamp():
    return datetime.now(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def evidence_root(explicit):
    """A brand-new unique root strictly inside the dedicated evidence base.

    Every path component from the base down to the leaf is checked for symlinks
    before anything is resolved or created, the leaf must not exist at all (an
    existing empty directory is refused too), and only the leaf is created.
    """
    base = EVIDENCE_BASE
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_absolute():
            root = REPO_ROOT / root
    else:
        root = base / 'p03r07r' / f'{utc_stamp()}-{secrets.token_hex(4)}'
    try:
        relative = root.relative_to(base)
    except ValueError:
        raise SystemExit(f'refusing an evidence root outside {base}: {root}')
    if not relative.parts or any(part in ('..', '') for part in relative.parts):
        raise SystemExit(f'refusing a non-canonical evidence root: {root}')
    if base.is_symlink():
        raise SystemExit(f'refusing a symlinked evidence base: {base}')
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SystemExit(f'refusing a symlinked evidence path component: {current}')
    if root.exists():
        raise SystemExit(f'refusing an existing evidence root: {root}')
    root.mkdir(parents=True, exist_ok=False)
    return root


def peak_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == 'darwin' else value * 1024


def setup_django(data_dir):
    sys.path.insert(0, str(REPO_ROOT / 'server'))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'gep.settings'
    os.environ['GEP_DATA_DIR'] = str(data_dir)
    os.environ['GEP_SECRET_KEY'] = secrets.token_urlsafe(32)
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', verbosity=0, interactive=False)


class Report:
    def __init__(self):
        self.checks = []

    def check(self, name, ok, detail=None):
        self.checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    def failures(self):
        return [row['name'] for row in self.checks if not row['ok']]

    @property
    def ok(self):
        return not self.failures()


def create_world(models, *, title, participants, sessions, events, blob=0):
    """One synthetic study with exact participant/session/event counts.

    The first participant has no session at all (a real identified-only roster
    entry) and a null code; the second has two extra zero-event sessions (a real
    zero-event multi-session participant). Events go to the earliest sessions,
    one each, and a declared completion is written only where the declared set
    really arrived - or, once, as a real empty declaration.
    """
    study = models.Study.objects.create(title=title, recruitment='open', max_sessions=10)
    build = models.Build.objects.create(study=study, descriptor={'platform': 'synthetic', 'version': '1.0'},
                                        digest=secrets.token_hex(32))
    release = models.Release.objects.create(study=study, build=build, config={}, approved=True)
    people = [models.Participant(study=study, code=None if index == 0 else f'p{index:05d}')
              for index in range(participants)]
    models.Participant.objects.bulk_create(people, batch_size=1999)
    # Deterministic positions: SQLite orders NULL codes first, so the null-code
    # participant is the one without sessions and the fixed layout makes the
    # rendered byte counts reproducible across runs.
    stored = list(models.Participant.objects.filter(study=study).order_by('code'))

    plan = [0 if index == 0 else 2 + (2 if index == 1 else 0) for index in range(participants)]
    planned = sum(plan)
    while planned < sessions:
        plan[-1] += 1
        planned += 1
    while planned > sessions:
        reduced = False
        for index in range(participants - 1, -1, -1):
            if plan[index] > 0 and planned > sessions:
                plan[index] -= 1
                planned -= 1
                reduced = True
        if not reduced:
            break
    expires = datetime.now(dt_timezone.utc) + timedelta(days=1)
    rows = []
    for index, participant in enumerate(stored):
        for order in range(plan[index]):
            rows.append(models.Session(participant=participant, release=release, operation=uuid.uuid4(),
                                       proof_hash='p' * 48, request={'seed': index, 'order': order},
                                       token_hash='t' * 64, expires_at=expires))
    models.Session.objects.bulk_create(rows, batch_size=1999)

    event_rows = []
    created = list(models.Session.objects.filter(release__study=study).order_by('created_at', 'id'))
    for session in created[:events]:
        event_id, segment_id = uuid.uuid4(), uuid.uuid4()
        payload = {'index': len(event_rows)}
        if blob:
            payload['blob'] = 'a' * blob
        envelope = {'protocol_version': 'gep/1', 'event_id': str(event_id), 'session_id': str(session.id),
                    'segment_id': str(segment_id), 'sequence': 1, 'event_type': 'exp.rt',
                    'schema_id': 'rt', 'schema_version': '1', 'payload': payload}
        event_rows.append(models.Event(session=session, event_id=event_id, segment_id=segment_id,
                                       sequence=1, envelope=envelope))
    models.Event.objects.bulk_create(event_rows, batch_size=1999)

    received = {event.session_id: event for event in event_rows}
    complete = []
    for session in created:
        event = received.get(session.id)
        if event is not None:
            session.completion = {'event_ids': [str(event.event_id)], 'segment_ids': [str(event.segment_id)]}
            complete.append(session)
        elif session.participant_id == stored[1].id and plan[1] > 2:
            session.completion = {'event_ids': [], 'segment_ids': []}
            complete.append(session)
    models.Session.objects.bulk_update(complete, ['completion'], batch_size=1999)
    return study, release


def measure_cells(exports, snapshot):
    """Largest rendered CSV cell in characters and UTF-8 bytes."""
    maxima = [0, 0]
    for rows in exports.csv_rows_for(snapshot):
        for row in rows:
            for cell in row:
                maxima[0] = max(maxima[0], len(cell))
                maxima[1] = max(maxima[1], len(cell.encode('utf-8')))
    return maxima


def time_it(function, *args, **kwargs):
    gc.collect()
    tracemalloc.start()
    started = time.monotonic()
    value = function(*args, **kwargs)
    elapsed = time.monotonic() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, elapsed, peak


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true',
                        help='run the measurement and exit non-zero on any failed check')
    parser.add_argument('--evidence-dir', default=None,
                        help='brand-new evidence root inside local_data/phase03_remediation_20260923 '
                             '(must not exist; symlinked path components are refused)')
    args = parser.parse_args(argv)
    if not args.verify:
        parser.error('--verify is required')

    root = evidence_root(args.evidence_dir or os.environ.get('GEP_EVIDENCE_DIR') or None)
    data_dir = root / 'db'
    data_dir.mkdir()
    setup_django(data_dir)

    import core.models as models
    from core import exports
    from core.protocol import Rejected
    from django.contrib.auth import get_user_model

    report = Report()
    owner = get_user_model().objects.create_user('capacity_owner', password=secrets.token_urlsafe(24))
    models.Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    limits = exports.limits_document()

    # --- maximum sample: every count exactly at its hard boundary ----
    max_study, _release = create_world(models, title='capacity maximum synthetic study',
                                       participants=exports.MAX_PARTICIPANTS,
                                       sessions=exports.MAX_SESSIONS, events=exports.MAX_EVENTS)
    baseline = models.Export.objects.count()
    item, create_seconds, create_peak = time_it(
        exports.create_v2_export, owner, max_study.id, view='identified', language='zh')
    snapshot = item.snapshot
    bundle, render_seconds, render_peak = time_it(exports.render_csv_bundle, snapshot)
    jsonl, jsonl_seconds, _ = time_it(exports.render_jsonl, snapshot)
    metadata = json.dumps(exports.metadata_document(item), ensure_ascii=False, allow_nan=False).encode('utf-8')
    cells = measure_cells(exports, snapshot)
    snapshot_bytes = exports.snapshot_utf8_bytes(snapshot)
    csv_total = len(bundle[0]) + len(bundle[1])
    max_sample_peak_rss = peak_rss_bytes()

    report.check('max_sample_created', models.Export.objects.count() == baseline + 1)
    report.check('max_sample_counts_at_boundaries', snapshot['counts'] == {
        'participants': exports.MAX_PARTICIPANTS, 'sessions': exports.MAX_SESSIONS, 'events': exports.MAX_EVENTS},
        snapshot['counts'])
    report.check('max_sample_snapshot_bytes', snapshot_bytes <= exports.MAX_SNAPSHOT_BYTES,
                 {'bytes': snapshot_bytes, 'limit': exports.MAX_SNAPSHOT_BYTES})
    report.check('max_sample_csv_total_bytes', csv_total <= exports.MAX_CSV_TOTAL_BYTES,
                 {'bytes': csv_total, 'limit': exports.MAX_CSV_TOTAL_BYTES})
    report.check('max_sample_cell_chars', cells[0] <= exports.MAX_CELL_CHARS, cells)
    report.check('max_sample_cell_bytes', cells[1] <= exports.MAX_CELL_BYTES, cells)
    report.check('max_sample_generation_seconds', create_seconds <= exports.MAX_GENERATION_SECONDS,
                 {'seconds': round(create_seconds, 3), 'limit': exports.MAX_GENERATION_SECONDS})
    report.check('max_sample_outputs_non_empty', all(len(part) > 0 for part in (bundle[0], bundle[1], jsonl, metadata)),
                 {'participants_csv': len(bundle[0]), 'events_csv': len(bundle[1]),
                  'jsonl': len(jsonl), 'metadata': len(metadata)})
    participant_uuids = {row['uuid'] for row in snapshot['participants']}
    participants_with_sessions = {row['participant_uuid'] for row in snapshot['sessions']}
    roster_rows = list(exports.csv_rows_for(snapshot))[0]
    next(roster_rows)
    code_cells = [row[3] for row in roster_rows]
    report.check('max_sample_roster_includes_non_participants',
                 len(participant_uuids - participants_with_sessions) == 1,
                 {'roster': len(participant_uuids), 'with_sessions': len(participants_with_sessions)})
    report.check('max_sample_null_code_kept_in_json', any(row['code'] is None for row in snapshot['participants']))
    report.check('max_sample_null_code_cell_empty', '' in code_cells,
                 {'empty_code_cells': code_cells.count(''), 'roster_rows': len(code_cells)})

    # --- one over each count boundary: refused, no ready row left ----
    def refused_creation(*args, **kwargs):
        try:
            exports.create_v2_export(owner, max_study.id, *args, **kwargs)
            return None
        except Rejected as error:
            return error

    overflow = models.Participant.objects.create(study=max_study, code='overflow')
    error = refused_creation(view='identified', language='zh')
    report.check('participants_over_limit_refused', error is not None and error.code == 'export_limit_participants',
                 None if error is None else error.code)
    overflow.delete()
    report.check('participants_over_limit_no_ready_row', models.Export.objects.count() == baseline + 1)

    extra_session = models.Session.objects.create(
        participant=models.Participant.objects.filter(study=max_study).first(), release=_release,
        operation=uuid.uuid4(), proof_hash='p' * 48, request={}, token_hash='t' * 64,
        expires_at=datetime.now(dt_timezone.utc) + timedelta(days=1))
    error = refused_creation(view='unmapped', language='en')
    report.check('sessions_over_limit_refused', error is not None and error.code == 'export_limit_sessions',
                 None if error is None else error.code)
    extra_session.delete()
    report.check('sessions_over_limit_no_ready_row', models.Export.objects.count() == baseline + 1)

    first_session = models.Session.objects.filter(release__study=max_study).first()
    event_id, segment_id = uuid.uuid4(), uuid.uuid4()
    extra_event = models.Event.objects.create(
        session=first_session, event_id=event_id, segment_id=segment_id, sequence=2,
        envelope={'event_id': str(event_id), 'segment_id': str(segment_id), 'sequence': 2, 'payload': {}})
    error = refused_creation(view='unmapped', language='en')
    report.check('events_over_limit_refused', error is not None and error.code == 'export_limit_events',
                 None if error is None else error.code)
    extra_event.delete()
    report.check('events_over_limit_no_ready_row', models.Export.objects.count() == baseline + 1)

    scope_ids = [str(uuid.uuid4()) for _ in range(exports.MAX_SESSIONS + 1)]
    try:
        exports.create_v2_export(owner, max_study.id, view='unmapped', language='en', session_ids=scope_ids)
        report.check('session_scope_over_limit_refused', False)
    except Rejected as error:
        report.check('session_scope_over_limit_refused', error.code == 'export_limit_sessions', error.code)
    del scope_ids
    report.check('session_scope_over_limit_no_ready_row', models.Export.objects.count() == baseline + 1)

    # --- generation time: the deadline aborts a real long build ----
    try:
        exports.create_v2_export(owner, max_study.id, view='unmapped', language='en', generation_seconds=0.05)
        report.check('time_over_limit_refused', False)
    except Rejected as error:
        report.check('time_over_limit_refused', error.code == 'export_generation_timeout', error.code)
    report.check('time_over_limit_no_ready_row', models.Export.objects.count() == baseline + 1)

    # --- CSV cell: the exact character boundary, then one character over ----
    cell_study, cell_release = create_world(models, title='capacity cell synthetic study',
                                            participants=1, sessions=1, events=1, blob=1)
    probe = exports.create_v2_export(owner, cell_study.id, view='unmapped', language='en')
    probe_record = probe.snapshot['events'][0]['record']
    wrapper = len(exports.json_cell(probe_record)) - len(probe_record['payload']['blob'])
    boundary_length = exports.MAX_CELL_CHARS - wrapper
    boundary_event = models.Event.objects.filter(session__release__study=cell_study).first()
    boundary_event.envelope['payload']['blob'] = 'a' * boundary_length
    boundary_event.save(update_fields=['envelope'])
    boundary_item = exports.create_v2_export(owner, cell_study.id, view='unmapped', language='en')
    boundary_cell = measure_cells(exports, boundary_item.snapshot)[0]
    report.check('cell_exactly_at_boundary_accepted', boundary_cell == exports.MAX_CELL_CHARS, boundary_cell)
    boundary_event.envelope['payload']['blob'] = 'a' * (boundary_length + 1)
    boundary_event.save(update_fields=['envelope'])
    creations = models.Export.objects.filter(study=cell_study).count()
    exports.create_v2_export(owner, cell_study.id, view='unmapped', language='en')  # creation keeps working
    over_item = models.Export.objects.filter(study=cell_study).latest('created_at')
    report.check('cell_over_limit_creation_keeps_ready_row',
                 models.Export.objects.filter(study=cell_study).count() == creations + 1)
    try:
        exports.render_csv_bundle(over_item.snapshot)
        report.check('cell_one_char_over_refused', False)
    except exports.ExportCellLimit as error:
        report.check('cell_one_char_over_refused',
                     error.code == 'export_cell_limit' and error.detail.get('jsonl_available') is True,
                     {'code': error.code, 'jsonl_available': error.detail.get('jsonl_available')})
    lossless = exports.render_jsonl(over_item.snapshot)
    report.check('cell_over_limit_keeps_jsonl', b'a' * (boundary_length + 1) in lossless, len(lossless))
    cell_limits = {'boundary_cell_chars': boundary_cell, 'max_cell_chars': exports.MAX_CELL_CHARS,
                   'wrapper_chars': wrapper, 'jsonl_bytes': len(lossless)}

    # --- snapshot UTF-8 bytes: the exact boundary, then one byte over ----
    bytes_study, bytes_release = create_world(models, title='capacity bytes synthetic study',
                                              participants=1, sessions=1, events=1, blob=1)
    probe = exports.create_v2_export(owner, bytes_study.id, view='unmapped', language='en')
    probe_bytes = exports.snapshot_utf8_bytes(probe.snapshot)
    probe_blob = len(probe.snapshot['events'][0]['record']['payload']['blob'])
    payload_length = exports.MAX_SNAPSHOT_BYTES - (probe_bytes - probe_blob)
    big_event = models.Event.objects.filter(session__release__study=bytes_study).first()
    big_event.envelope['payload']['blob'] = 'a' * payload_length
    big_event.save(update_fields=['envelope'])
    exact = exports.create_v2_export(owner, bytes_study.id, view='unmapped', language='en')
    exact_bytes = exports.snapshot_utf8_bytes(exact.snapshot)
    # The construction adds exactly one ASCII byte per added blob character, so
    # the boundary case really sits on the limit - not "within one percent".
    report.check('snapshot_exactly_at_boundary_accepted', exact_bytes == exports.MAX_SNAPSHOT_BYTES,
                 {'bytes': exact_bytes, 'limit': exports.MAX_SNAPSHOT_BYTES})
    ready_before = models.Export.objects.filter(study=bytes_study).count()
    big_event.envelope['payload']['blob'] = 'a' * (payload_length + 1)
    big_event.save(update_fields=['envelope'])
    try:
        exports.create_v2_export(owner, bytes_study.id, view='unmapped', language='en')
        report.check('snapshot_over_limit_refused', False)
    except Rejected as error:
        report.check('snapshot_over_limit_refused', error.code == 'export_limit_snapshot', error.code)
    report.check('snapshot_over_limit_no_ready_row',
                 models.Export.objects.filter(study=bytes_study).count() == ready_before)
    snapshot_limits = {'boundary_bytes': exact_bytes, 'max_snapshot_bytes': exports.MAX_SNAPSHOT_BYTES,
                       'probe_bytes': probe_bytes}
    del exact, probe, payload_length, big_event
    gc.collect()

    # --- CSV pair total: the exact byte boundary of the two rendered CSVs ----
    # A synthetic snapshot with many bounded cells (no cell over its own limit)
    # drives the real renderer, so the aggregate boundary is measured exactly
    # instead of trusting a percentage or weakening the ceiling.
    csv_events = [{'participant_uuid': str(uuid.uuid4()), 'session_id': str(uuid.uuid4()),
                   'release_id': str(uuid.uuid4()), 'build_id': str(uuid.uuid4()),
                   'event_id': str(uuid.uuid4()), 'segment_id': str(uuid.uuid4()),
                   'sequence': index + 1, 'event_type': 'exp.rt', 'schema_id': 'rt',
                   'schema_version': '1', 'received_at': '2026-09-23T12:00:00.000000Z',
                   'record': {'payload': {'blob': 'a'}}}
                  for index in range(2400)]
    csv_snapshot = {'language': 'en', 'view': 'unmapped', 'title': 'capacity csv synthetic study',
                    'study_id': str(uuid.uuid4()),
                    'participants': [{'uuid': csv_events[0]['participant_uuid'], 'code': None}],
                    'sessions': [], 'events': csv_events}

    def csv_pair_total(lengths):
        for row, length in zip(csv_events, lengths):
            row['record']['payload']['blob'] = 'a' * length
        parts = exports.render_csv_bundle(csv_snapshot)
        return len(parts[0]) + len(parts[1])

    probe_total = csv_pair_total([1] * len(csv_events))
    budget = exports.MAX_CSV_TOTAL_BYTES - (probe_total - len(csv_events))
    base_length, extra = divmod(budget, len(csv_events))
    lengths = [base_length + (1 if index < extra else 0) for index in range(len(csv_events))]
    exact_csv_total = csv_pair_total(lengths)
    report.check('csv_total_exactly_at_boundary_accepted',
                 exact_csv_total == exports.MAX_CSV_TOTAL_BYTES,
                 {'bytes': exact_csv_total, 'limit': exports.MAX_CSV_TOTAL_BYTES})
    over_lengths = list(lengths)
    over_lengths[0] += 1
    try:
        csv_pair_total(over_lengths)
        report.check('csv_total_one_byte_over_refused', False)
    except Rejected as error:
        report.check('csv_total_one_byte_over_refused', error.code == 'export_limit_csv_total', error.code)
    csv_limits = {'boundary_bytes': exact_csv_total, 'max_csv_total_bytes': exports.MAX_CSV_TOTAL_BYTES,
                  'events': len(csv_events), 'blob_chars': base_length}
    del csv_events, csv_snapshot, lengths, over_lengths, probe_total
    gc.collect()

    log = {'task': 'p03r07r', 'tool': 'remediation_export_capacity.py',
           'generated_at': datetime.now(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'limits': limits,
           'max_sample': {'counts': snapshot['counts'], 'create_seconds': round(create_seconds, 3),
                          'render_seconds': round(render_seconds, 3), 'jsonl_seconds': round(jsonl_seconds, 3),
                          'tracemalloc_create_peak_bytes': create_peak, 'tracemalloc_render_peak_bytes': render_peak,
                          'peak_rss_bytes': max_sample_peak_rss, 'snapshot_bytes': snapshot_bytes,
                          'participants_csv_bytes': len(bundle[0]), 'events_csv_bytes': len(bundle[1]),
                          'csv_total_bytes': csv_total, 'jsonl_bytes': len(jsonl), 'metadata_bytes': len(metadata),
                          'max_cell_chars': cells[0], 'max_cell_bytes': cells[1]},
           'time_over_limit': {'measured_max_seconds': round(create_seconds, 3),
                               'limit_seconds': exports.MAX_GENERATION_SECONDS,
                               'aborted_after_seconds': 0.05},
           'cell_boundary': cell_limits, 'snapshot_boundary': snapshot_limits,
           'csv_pair_boundary': csv_limits,
           'process_peak_rss_bytes': peak_rss_bytes(),
           'checks': report.checks, 'result': 'PASS' if report.ok else 'FAIL',
           'failures': report.failures()}
    (root / 'capacity.json').write_text(json.dumps(log, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                                        encoding='utf-8')
    database = data_dir / 'gep.sqlite3'
    database_bytes = database.stat().st_size if database.exists() else 0
    # Every run keeps its synthetic database, successful or failed: the measured
    # bytes stay re-checkable and no evidence root is ever cleaned.
    report.check('evidence_database_retained', database.exists() and database_bytes > 0,
                 {'database': str(database), 'bytes': database_bytes})
    log['checks'] = report.checks
    log['result'] = 'PASS' if report.ok else 'FAIL'
    log['failures'] = report.failures()
    (root / 'capacity.json').write_text(json.dumps(log, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                                        encoding='utf-8')
    print(json.dumps({'result': log['result'], 'max_counts': snapshot['counts'],
                      'create_seconds': round(create_seconds, 3), 'snapshot_bytes': snapshot_bytes,
                      'csv_total_bytes': csv_total, 'max_cell_chars': cells[0],
                      'peak_rss_bytes': log['max_sample']['peak_rss_bytes'],
                      'failed_checks': report.failures(), 'evidence': str(root / 'capacity.json'),
                      'database_kept': True, 'database_bytes': database_bytes},
                     ensure_ascii=False, indent=2))
    return 0 if report.ok else 1


if __name__ == '__main__':
    sys.exit(main())
