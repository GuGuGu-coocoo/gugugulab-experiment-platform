"""P03R07R evidence: the bounded corrections to the export v2 core.

Every expected value here is the R07R contract text (TASK.md, derived from the
R07 guarded review and requirement U09 §5.3), never the implementation:

* the generation deadline covers the final snapshot serialization and the DB
  persistence step, and a crossing rolls back the just-written row;
* same-second sessions keep the real creation order with source microsecond
  precision, including ``received_at``, and the v1 rendering stays byte-fixed;
* the declared 20,000-session range is really usable over authenticated HTTP
  under the export route's own bounded envelope, one over / malformed / foreign
  / duplicate ids are refused, and unrelated routes keep their original bounds;
* the frozen selection is nested in ``limits.scope`` of the preview and of the
  snapshot metadata and is never derived from the current database at download;
* capacity evidence roots must be brand new, symlink-free and never cleaned.
"""
import csv
import io
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

import pytest
from django.test import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: tools.*
sys.path.insert(0, str(Path(__file__).resolve().parent))       # sibling fixtures

import tools.remediation_export_capacity as capacity  # noqa: E402
from test_p03r07 import (METADATA_WHITELIST, create_v2, csv_rows,  # noqa: E402
                         download, sign_in, world)

from core import exports, protocol  # noqa: E402
from core.models import (Build, Event, Export, Participant, Release,  # noqa: E402
                         Session, Study)
from core.protocol import Rejected  # noqa: E402
from django.utils import timezone  # noqa: E402

V1_STUDY_ID = '11111111-1111-4111-8111-111111111111'
V1_RELEASE_ID = '22222222-2222-4222-8222-222222222222'
V1_BUILD_ID = '33333333-3333-4333-8333-333333333333'
V1_RECORD = {'protocol_version': 'gep/1', 'sequence': 1,
             'payload': {'rt_ms': 321.5, 'text': '中文', 'formula': '=1+1'}}
V1_RECORD_JSON = ('{"protocol_version": "gep/1", "sequence": 1, '
                  '"payload": {"rt_ms": 321.5, "text": "中文", "formula": "=1+1"}}')
V1_JSONL = ('{"study_id": "11111111-1111-4111-8111-111111111111", '
            '"release_id": "22222222-2222-4222-8222-222222222222", '
            '"build_id": "33333333-3333-4333-8333-333333333333", '
            '"record": ' + V1_RECORD_JSON + '}\n')


def make_session(participant, release, *, identifier, created_microsecond=None):
    """One synthetic session of the world study, optionally at a fixed time."""
    session = Session.objects.create(id=identifier, participant=participant,
                                     release=release, operation=uuid.uuid4(),
                                     proof_hash='p' * 48, request={'seed': 'p03r07r'},
                                     token_hash='t' * 64, expires_at=datetime(2030, 1, 1, tzinfo=dt_timezone.utc))
    if created_microsecond is not None:
        Session.objects.filter(pk=session.pk).update(
            created_at=datetime(2026, 9, 23, 12, 0, 0, created_microsecond, tzinfo=dt_timezone.utc))
    return session


def test_deadline_covers_late_serialization_and_persistence(world, monkeypatch):
    """The probe fixes the serialization crossing; this fixes the persistence
    crossing: the row really exists when the clock crosses, so only the
    post-create check inside the atomic block can keep it out of the database."""
    owner, study = world['owner'], world['study']
    baseline = Export.objects.count()

    # 1. The clock crosses inside the final serialization: no row is written.
    now = [0.0]
    monkeypatch.setattr(exports.time, 'monotonic', lambda: now[0])
    original = exports.snapshot_utf8_bytes

    def serialization_crosses(snapshot):
        result = original(snapshot)
        now[0] = 31.0
        return result

    monkeypatch.setattr(exports, 'snapshot_utf8_bytes', serialization_crosses)
    with pytest.raises(Rejected) as serialization_error:
        exports.create_v2_export(owner, study.id, view='unmapped', language='en')
    assert serialization_error.value.code == 'export_generation_timeout'
    assert serialization_error.value.status == 503
    assert Export.objects.count() == baseline

    # 2. The clock crosses after the real INSERT: the row must roll back.
    monkeypatch.setattr(exports, 'snapshot_utf8_bytes', original)
    now[0] = 0.0
    real_save = Export.save

    def save_crosses(self, *args, **kwargs):
        result = real_save(self, *args, **kwargs)
        now[0] = 31.0
        return result

    monkeypatch.setattr(Export, 'save', save_crosses)
    with pytest.raises(Rejected) as persistence_error:
        exports.create_v2_export(owner, study.id, view='unmapped', language='en')
    assert persistence_error.value.code == 'export_generation_timeout'
    assert persistence_error.value.status == 503
    assert Export.objects.count() == baseline
    assert Export.objects.filter(study=study).count() == baseline


def test_same_second_sessions_keep_exact_order_and_precision(world, evidence):
    """Two sessions in one second: the earlier one has the larger UUID, so a
    seconds-truncated sort would reverse them. Frozen timestamps carry the exact
    source microseconds and the CSV first/last columns follow the real order."""
    participant = world['participants']['c']
    first = make_session(participant, world['release'],
                         identifier=uuid.UUID('ffffffff-ffff-4fff-8fff-ffffffffffff'),
                         created_microsecond=100000)
    second = make_session(participant, world['release'],
                          identifier=uuid.UUID('00000000-0000-4000-8000-000000000001'),
                          created_microsecond=900000)
    item = exports.create_v2_export(world['owner'], world['study'].id, view='identified', language='en')
    rows = csv.DictReader(io.StringIO(exports.render_participants_csv(item.snapshot).decode('utf-8-sig')))
    own = next(row for row in rows if row['participant_uuid'] == str(participant.id))
    sessions = json.loads(own['sessions_json'].removeprefix('json:'))
    expected_ids = [str(first.id), str(second.id)]
    expected_times = ['2026-09-23T12:00:00.100000Z', '2026-09-23T12:00:00.900000Z']
    assert [row['id'] for row in sessions] == expected_ids
    assert [row['created_at'] for row in sessions] == expected_times
    assert own['first_session_created_at'] == expected_times[0]
    assert own['last_session_created_at'] == expected_times[1]
    assert item.snapshot['created_at'].endswith('Z')
    evidence('same_second_order.json', {
        'order': [row['id'] for row in sessions], 'created_at': expected_times,
        'first_session_created_at': own['first_session_created_at'],
        'last_session_created_at': own['last_session_created_at']})


def test_received_at_keeps_source_precision(world):
    """received_at fidelity: the frozen CSV cell and JSONL value carry the exact
    microsecond timestamp, and the record envelope stays the original value."""
    event = world['events']['e1']
    Event.objects.filter(pk=event.pk).update(
        received_at=datetime(2026, 9, 23, 12, 0, 0, 123456, tzinfo=dt_timezone.utc))
    item = exports.create_v2_export(world['owner'], world['study'].id, view='unmapped', language='en')
    row = next(row for row in csv_rows(exports.render_events_csv(item.snapshot))
               if row['event_id'] == str(event.event_id))
    assert row['received_at'] == '2026-09-23T12:00:00.123456Z'
    line = next(line for line in (json.loads(value) for value in
                                  exports.render_jsonl(item.snapshot).decode().splitlines())
                if line['event_id'] == str(event.event_id))
    assert line['received_at'] == '2026-09-23T12:00:00.123456Z'
    assert line['record']['payload'] == {'rt_ms': 321.5, 'choice': 'left'}


def test_v1_rendering_bytes_stay_a_fixed_golden(world):
    """The v1 stored shape and all three rendered formats stay byte-fixed after
    the timestamp change: exact JSONL bytes, exact CSV cells, exact metadata."""
    owner, study = world['owner'], world['study']
    client = sign_in(owner)
    snapshot = {'records': [{'study_id': V1_STUDY_ID, 'release_id': V1_RELEASE_ID,
                             'build_id': V1_BUILD_ID, 'record': V1_RECORD}],
                'builds': {V1_BUILD_ID: {'platform': 'synthetic', 'version': '1.0'}},
                'sessions': {}, 'format_version': '1',
                'csv_encoding': 'record_json begins with json: followed by a lossless JSON value; '
                                'remove prefix then parse JSON'}
    item = Export.objects.create(study=study, snapshot=snapshot)

    assert download(client, item.id).content == V1_JSONL.encode('utf-8')
    body = download(client, item.id, 'csv').content
    assert body.startswith(b'\xef\xbb\xbf')
    assert list(csv.reader(io.StringIO(body.decode('utf-8-sig')))) == [
        ['study_id', 'release_id', 'build_id', 'record_json'],
        [V1_STUDY_ID, V1_RELEASE_ID, V1_BUILD_ID, 'json:' + V1_RECORD_JSON]]
    assert json.loads(download(client, item.id, 'metadata').content) == {
        key: value for key, value in snapshot.items() if key != 'records'}


def test_http_scope_boundary_is_really_usable(world, evidence):
    """A complete known 20,000-session range is accepted over authenticated HTTP
    under the export route's own envelope; one over, malformed, unknown, foreign
    and duplicate ids are refused, and no refusal leaves a ready row."""
    owner, study = world['owner'], world['study']
    client = sign_in(owner)
    baseline = Export.objects.count()
    expires = timezone.now() + timedelta(days=1)
    rows = [Session(id=uuid.uuid4(), participant=world['participants']['a'], release=world['release'],
                    operation=uuid.uuid4(), proof_hash='p' * 48, request={'seed': 'r07r'},
                    token_hash='t' * 64, expires_at=expires)
            for _ in range(exports.MAX_SESSIONS)]
    Session.objects.bulk_create(rows, batch_size=2000)
    ids = [str(session.id) for session in rows]
    payload = {'study_id': str(study.id), 'format_version': '2', 'view': 'unmapped', 'language': 'en',
               'session_ids': ids}
    body = json.dumps(payload).encode('utf-8')
    # The full range exceeds the general participant envelope and still fits the
    # export route's own bound, so this is a real HTTP path, not a library call.
    assert protocol.MAX_BYTES < len(body) <= protocol.MAX_EXPORT_BYTES

    def attempt(session_ids):
        return client.post('/v1/admin/exports', json.dumps(dict(payload, session_ids=session_ids)),
                           content_type='application/json')

    response = client.post('/v1/admin/exports', body, content_type='application/json')
    assert response.status_code == 201, response.content
    item = Export.objects.get(pk=response.json()['export_id'])
    assert item.snapshot['counts'] == {'participants': 1, 'sessions': exports.MAX_SESSIONS, 'events': 0}
    assert item.snapshot['limits']['scope'] == {'mode': 'sessions', 'session_ids': sorted(ids)}
    assert response.json()['counts'] == item.snapshot['counts']

    over = attempt(ids + [str(world['sessions']['a1'].id)])
    assert over.status_code == 422 and over.json()['code'] == 'array_limit'
    with pytest.raises(Rejected) as ceiling:
        exports.create_v2_export(owner, study.id, view='unmapped', language='en',
                                 session_ids=ids + [str(world['sessions']['a1'].id)])
    assert ceiling.value.code == 'export_limit_sessions' and ceiling.value.status == 413
    malformed = attempt(['not-a-session'])
    assert malformed.status_code == 400 and malformed.json()['code'] == 'export_scope'
    duplicate = attempt([ids[0], ids[0]])
    assert duplicate.status_code == 400 and duplicate.json()['code'] == 'export_scope'
    unknown = attempt([ids[0], str(uuid.uuid4())])
    assert unknown.status_code == 400 and unknown.json()['code'] == 'export_scope_unknown'
    shape = attempt('not-a-list')
    assert shape.status_code == 400 and shape.json()['code'] == 'export_scope'

    other = Study.objects.create(title='P03R07R foreign study')
    other_build = Build.objects.create(study=other, descriptor={}, digest='9' * 64)
    other_release = Release.objects.create(study=other, build=other_build, config={}, approved=True)
    other_person = Participant.objects.create(study=other, code='foreign')
    foreign = Session.objects.create(participant=other_person, release=other_release, operation=uuid.uuid4(),
                                     proof_hash='p' * 48, request={}, token_hash='t' * 64, expires_at=expires)
    cross = attempt([ids[0], str(foreign.id)])
    assert cross.status_code == 400 and cross.json()['code'] == 'export_scope_unknown'

    # A small explicit range keeps exactly the selected sessions, never narrowed.
    small = attempt(sorted([str(world['sessions']['a1'].id), str(world['sessions']['b2'].id)]))
    assert small.status_code == 201
    small_item = Export.objects.get(pk=small.json()['export_id'])
    assert small_item.snapshot['counts'] == {'participants': 2, 'sessions': 2, 'events': 3}

    assert Export.objects.count() == baseline + 2
    evidence('http_scope_boundary.json', {
        'request_bytes': len(body), 'participant_envelope_bytes': protocol.MAX_BYTES,
        'export_envelope_bytes': protocol.MAX_EXPORT_BYTES, 'accepted_counts': item.snapshot['counts'],
        'one_over': over.json()['code'], 'malformed': malformed.json()['code'],
        'duplicate': duplicate.json()['code'], 'unknown': unknown.json()['code'],
        'foreign': cross.json()['code'], 'small_counts': small_item.snapshot['counts']})


def test_export_envelope_does_not_loosen_other_routes(world):
    """Only the admin export route got the wider envelope: the participant route
    keeps 256 KiB, the default parser keeps 10,000 arrays, and the export parser
    keeps duplicate-key, non-finite and depth checks."""
    assert protocol.MAX_BYTES == 262144 and protocol.MAX_EVENTS == 10000
    assert protocol.MAX_EXPORT_BYTES == 1024 * 1024 and protocol.MAX_EXPORT_ARRAY == 20000
    assert protocol.MAX_EXPORT_ARRAY == exports.MAX_SESSIONS
    client = Client()
    oversized = b'x' * (protocol.MAX_EXPORT_BYTES + 1)
    refused = client.generic('POST', '/v1/admin/exports', oversized, content_type='application/json',
                             CONTENT_LENGTH=str(len(oversized)))
    assert refused.status_code == 413 and refused.json()['code'] == 'body_limit'
    refused = client.generic('POST', '/v1/participant/sessions', b'{}', content_type='application/json',
                             CONTENT_LENGTH=str(protocol.MAX_BYTES + 1))
    assert refused.status_code == 413 and refused.json()['code'] == 'body_limit'
    with pytest.raises(Rejected) as default_array:
        protocol.parse(b'[' + b'0,' * (protocol.MAX_EVENTS + 1) + b'0]')
    assert default_array.value.code == 'array_limit'
    with pytest.raises(Rejected) as default_bytes:
        protocol.parse(b'x' * (protocol.MAX_BYTES + 1))
    assert default_bytes.value.code == 'body_limit'

    authed = sign_in(world['owner'])
    duplicate = authed.generic(
        'POST', '/v1/admin/exports',
        b'{"study_id":"a","study_id":"b","format_version":"2","view":"unmapped","language":"en"}',
        content_type='application/json')
    assert duplicate.status_code == 422 and duplicate.json()['code'] == 'duplicate_key'
    nonfinite = authed.generic(
        'POST', '/v1/admin/exports',
        b'{"study_id":"a","format_version":"2","view":"unmapped","language":"en","session_ids":[NaN]}',
        content_type='application/json')
    assert nonfinite.status_code == 422 and nonfinite.json()['code'] == 'number_range'
    deep = authed.generic('POST', '/v1/admin/exports',
                          b'{"session_ids":' + b'[' * 17 + b']' * 17 + b'}',
                          content_type='application/json')
    assert deep.status_code == 422 and deep.json()['code'] == 'json_depth'


def test_metadata_carries_frozen_scope_and_never_reads_current_db(world):
    """The frozen selection lives under ``limits.scope`` (mode + session_ids) in
    the preview and in the snapshot metadata; full study, selected range and
    empty range stay distinguishable, and a later rename/new session never
    changes the downloaded metadata."""
    owner, study = world['owner'], world['study']
    client = sign_in(owner)
    selected = sorted([str(world['sessions']['a1'].id), str(world['sessions']['b2'].id)])
    full = create_v2(client, study, 'identified', 'zh')
    scoped = create_v2(client, study, 'unmapped', 'en', session_ids=selected)
    empty = create_v2(client, study, 'unmapped', 'en', session_ids=[])
    assert (full.status_code, scoped.status_code, empty.status_code) == (201, 201, 201)
    full_item = Export.objects.get(pk=full.json()['export_id'])
    scoped_item = Export.objects.get(pk=scoped.json()['export_id'])
    empty_item = Export.objects.get(pk=empty.json()['export_id'])

    preview = exports.preview_export(owner, study, view='unmapped', language='en', session_ids=selected)
    assert preview['limits']['scope'] == {'mode': 'sessions', 'session_ids': selected}
    assert preview['counts'] == {'participants': 2, 'sessions': 2, 'events': 3}
    full_preview = exports.preview_export(owner, study, view='identified', language='zh')
    assert full_preview['limits']['scope'] == {'mode': 'study', 'session_ids': []}

    full_meta = download(client, full_item.id, 'metadata').json()
    scoped_meta = download(client, scoped_item.id, 'metadata').json()
    empty_meta = download(client, empty_item.id, 'metadata').json()
    assert set(full_meta) == METADATA_WHITELIST and 'scope' not in full_meta
    assert full_meta['limits']['scope'] == {'mode': 'study', 'session_ids': []}
    assert scoped_meta['limits']['scope'] == {'mode': 'sessions', 'session_ids': selected}
    assert empty_meta['limits']['scope'] == {'mode': 'sessions', 'session_ids': []}
    assert scoped_meta['counts'] == {'participants': 2, 'sessions': 2, 'events': 3}
    assert empty_meta['counts'] == {'participants': 0, 'sessions': 0, 'events': 0}
    assert full_meta['limits']['scope'] != scoped_meta['limits']['scope']
    assert scoped_meta['limits']['scope'] != empty_meta['limits']['scope']

    # The metadata is read from the frozen row: a rename and a new session in the
    # current database never change an existing export's metadata.
    study.title = 'Renamed after the freeze'
    study.save()
    world['add_session'](world['participants']['c'])
    assert download(client, scoped_item.id, 'metadata').json() == scoped_meta
    assert download(client, full_item.id, 'metadata').json() == full_meta


def test_capacity_evidence_root_guard_uses_new_tmp_fixtures(tmp_path, monkeypatch):
    """The capacity root guard: a brand-new root is created, an existing root
    (even empty) is refused, a symlinked component or leaf is refused before
    anything is resolved or created, and the default root stays inside the base."""
    base = tmp_path / 'evidence_base'
    base.mkdir()
    monkeypatch.setattr(capacity, 'EVIDENCE_BASE', base)

    fresh = capacity.evidence_root(str(base / 'task' / 'run-1'))
    assert fresh.is_dir() and list(fresh.iterdir()) == []
    with pytest.raises(SystemExit):
        capacity.evidence_root(str(base / 'task' / 'run-1'))
    with pytest.raises(SystemExit):
        capacity.evidence_root(str(base / 'task' / '..' / 'run-2'))

    outside = tmp_path / 'outside'
    outside.mkdir()
    (base / 'link').symlink_to(outside)
    with pytest.raises(SystemExit):
        capacity.evidence_root(str(base / 'link' / 'run-3'))
    (base / 'task' / 'run-4').symlink_to(outside)
    with pytest.raises(SystemExit):
        capacity.evidence_root(str(base / 'task' / 'run-4'))
    assert list(outside.iterdir()) == []
    with pytest.raises(SystemExit):
        capacity.evidence_root(str(tmp_path / 'elsewhere' / 'run-5'))

    default = capacity.evidence_root(None)
    assert default.parent.name == 'p03r07r' and default.parent.parent == base
    assert default.is_dir() and list(default.iterdir()) == []

    link_base = tmp_path / 'link_base'
    link_base.symlink_to(outside)
    monkeypatch.setattr(capacity, 'EVIDENCE_BASE', link_base)
    with pytest.raises(SystemExit):
        capacity.evidence_root(str(link_base / 'run-6'))
