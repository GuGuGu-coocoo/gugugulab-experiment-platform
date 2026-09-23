"""P03R08 evidence: the v2 overview + details ZIP delivery and its file safety.

Every expected value here comes from the R00 §E contract (2026-09-23) and the
fixed synthetic world built below, never from the renderer under test:

* the ZIP contains exactly ``participants.csv`` and ``events.csv`` in that
  order, with BOM, RFC 4180 quoting, ``text:``/``json:`` prefixes, UTC ``Z``
  timestamps and the frozen sort orders;
* ``001``, a formula-looking value, an embedded newline, a null roster code and
  a large (but within-limit) cell survive the round trip per value, and the
  technical status stays a technical fact without any scoring column;
* the frozen permission set is re-checked for the ZIP and metadata on every
  read; a raw-only reader neither downloads an identified ZIP nor sees its
  roster counts/links in the listing;
* a disk failure or an unknown stored state never serves a half package: the
  temp file is never published, the download is refused with the JSONL path
  named, and an invalid stored ZIP is regenerated atomically instead of being
  served;
* the download file name is the immutable export UUID, never a study title or
  other untrusted value.
"""
import csv
import io
import json
import os
import re
import shutil
import subprocess
import uuid
import zipfile
from datetime import timedelta, timezone as dt_timezone
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import Client
from django.utils import timezone

from core import exports
from core.models import (AccountProfile, Build, Event, Export, Grant, Instance,
                         Participant, Release, Session, Study)
from core.protocol import Rejected

OWNER_PASSWORD = 'Synthetic-p03r08-owner-password'
LARGE_CELL_CHARS = 30000
FORMULA_TEXT = '=1+1'
NEWLINE_TEXT = 'line1\nline2'
PARTICIPANT_KEYS = ['study_title', 'study_id', 'participant_uuid', 'participant_code', 'session_count',
                    'complete_session_count', 'event_count', 'first_session_created_at',
                    'last_session_created_at', 'technical_status', 'sessions_json']
EVENT_KEYS = ['study_title', 'study_id', 'participant_uuid', 'participant_code', 'session_id',
              'release_id', 'build_id', 'event_id', 'segment_id', 'sequence', 'event_type',
              'schema_id', 'schema_version', 'received_at', 'record_json']


def make_user(name):
    return get_user_model().objects.create_user(name, password=OWNER_PASSWORD)


def sign_in(who):
    client = Client()
    client.force_login(who)
    profile = AccountProfile.objects.filter(user_id=who.pk).first()
    session = client.session
    session['gep_auth_version'] = profile.auth_version if profile is not None else 1
    session.save()
    return client


def grant(who, study, *actions):
    for action in actions:
        Grant.objects.create(user=who, study=study, action=action)


def body_bytes(response):
    """Full response bytes whether Django streams (FileResponse) or not."""
    if getattr(response, 'streaming', False):
        return b''.join(response.streaming_content)
    return response.content


def csv_rows(body):
    reader = csv.reader(io.StringIO(body.decode('utf-8-sig')))
    header = next(reader)
    keys = [column.split('（')[-1].rstrip('）') if '（' in column else column for column in header]
    return keys, [dict(zip(keys, row)) for row in reader]


def utc_text(value):
    """Independent UTC `Z` formatter with microsecond precision."""
    return value.astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def create_v2(client, study, view, language='zh', **extra):
    payload = {'study_id': str(study.id), 'format_version': '2', 'view': view, 'language': language}
    payload.update(extra)
    return client.post('/v1/admin/exports', payload, content_type='application/json')


def download(client, export_id, fmt=None):
    url = f'/v1/admin/exports/{export_id}/download'
    return client.get(url + (f'?format={fmt}' if fmt else ''))


def zip_members(body):
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


class World:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __getitem__(self, key):
        return self.__dict__[key]


@pytest.fixture
def world(db):
    """A v2 instance whose fixed values cover every CSV round-trip edge.

    a: code 001, two sessions (one complete with two events: formula text,
       embedded newline and a large payload; one zero-event)
    b: null code, two sessions (one zero-event, one complete with one event)
    c: code 003, no session at all (identified-only roster entry)
    e: code 005, one complete single-event session
    """
    owner = make_user('p03r08_owner')
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    AccountProfile.objects.create(user=owner, role='user', policy_version=2)
    # The literal newline in the title exercises the RFC 4180 quoted-field path
    # of the text columns; the JSON columns escape newlines by JSON rules.
    study = Study.objects.create(title='Synthetic R08 study\n第二行', recruitment='open', max_sessions=10)
    build = Build.objects.create(study=study, descriptor={'platform': 'synthetic', 'version': '1.0'}, digest='b' * 64)
    release = Release.objects.create(study=study, build=build, config={'purpose': 'synthetic'}, approved=True)

    def add_participant(code):
        return Participant.objects.create(study=study, code=code)

    def add_session(participant, *, completion=None):
        return Session.objects.create(participant=participant, release=release, operation=uuid.uuid4(),
                                      proof_hash='p' * 48, request={'seed': 'synthetic'}, token_hash='t' * 64,
                                      expires_at=timezone.now() + timedelta(days=1), completion=completion)

    def add_event(session, *, sequence=1, payload=None, event_type='exp.rt'):
        envelope = {'protocol_version': 'gep/1', 'event_id': str(uuid.uuid4()), 'session_id': str(session.id),
                    'segment_id': str(uuid.uuid4()), 'sequence': sequence, 'event_type': event_type,
                    'schema_id': 'rt', 'schema_version': '1',
                    'payload': {'rt_ms': 321.5, 'choice': 'left'} if payload is None else payload}
        return Event.objects.create(session=session, event_id=uuid.UUID(envelope['event_id']),
                                    segment_id=uuid.UUID(envelope['segment_id']), sequence=sequence,
                                    envelope=envelope)

    participants = {key: add_participant(code) for key, code in
                    (('a', '001'), ('b', None), ('c', '003'), ('e', '005'))}
    sessions = {}
    sessions['a1'] = add_session(participants['a'])
    events = {'e1': add_event(sessions['a1'], payload={'formula': FORMULA_TEXT, 'text': NEWLINE_TEXT,
                                                       'blob': 'a' * LARGE_CELL_CHARS}),
              'e2': add_event(sessions['a1'], sequence=2, payload={'rt_ms': 12.5})}
    sessions['a1'].completion = {'event_ids': [str(events['e1'].event_id), str(events['e2'].event_id)],
                                 'segment_ids': [str(events['e1'].segment_id), str(events['e2'].segment_id)]}
    sessions['a1'].save(update_fields=['completion'])
    sessions['a2'] = add_session(participants['a'])  # zero-event second session
    sessions['b1'] = add_session(participants['b'])  # zero-event session
    sessions['b2'] = add_session(participants['b'])
    events['e3'] = add_event(sessions['b2'])
    sessions['b2'].completion = {'event_ids': [str(events['e3'].event_id)], 'segment_ids': [str(events['e3'].segment_id)]}
    sessions['b2'].save(update_fields=['completion'])
    sessions['e'] = add_session(participants['e'])
    events['e4'] = add_event(sessions['e'])
    sessions['e'].completion = {'event_ids': [str(events['e4'].event_id)], 'segment_ids': [str(events['e4'].segment_id)]}
    sessions['e'].save(update_fields=['completion'])
    return World(owner=owner, study=study, build=build, release=release, participants=participants,
                 sessions=sessions, events=events, add_participant=add_participant,
                 add_session=add_session, add_event=add_event)


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """Keep every ZIP spool file out of the real local_data tree."""
    root = tmp_path / 'data'
    root.mkdir()
    monkeypatch.setattr(settings, 'DATA_DIR', root)
    return root / 'exports'


def expected_participant_rows(world, view, language):
    """The independent golden of participants.csv, derived from the fixed world."""
    sessions = {}
    for row in world.sessions.values():
        sessions.setdefault(str(row.participant_id), []).append(row)
    people = sorted(world.participants.values(), key=lambda person: str(person.id))
    if view == 'unmapped':
        people = [person for person in people if sessions.get(str(person.id))]
    rows = []
    for person in people:
        own = sorted(sessions.get(str(person.id), ()), key=lambda row: (utc_text(row.created_at), str(row.id)))
        events = sum(Event.objects.filter(session=row).count() for row in own)
        complete = sum(1 for row in own if row.completion is not None)
        if not own:
            status = 'not_started'
        elif complete == len(own):
            status = 'complete'
        else:
            status = 'pending'
        session_json = [{'id': str(row.id), 'release_id': str(row.release_id), 'build_id': str(row.release.build_id),
                         'created_at': utc_text(row.created_at), 'complete': row.completion is not None,
                         'event_count': Event.objects.filter(session=row).count()} for row in own]
        rows.append({
            'study_title': 'text:' + world.study.title, 'study_id': str(world.study.id),
            'participant_uuid': str(person.id),
            'participant_code': ('text:' + person.code) if person.code not in (None, '') else '',
            'session_count': str(len(own)), 'complete_session_count': str(complete), 'event_count': str(events),
            'first_session_created_at': utc_text(own[0].created_at) if own else '',
            'last_session_created_at': utc_text(own[-1].created_at) if own else '',
            'technical_status': status, 'sessions_json': 'json:' + json.dumps(session_json, ensure_ascii=False),
        })
    return rows


def expected_event_rows(world, view):
    """The independent golden of events.csv, derived from the fixed world."""
    codes = {str(person.id): person.code for person in world.participants.values()}
    rows = []
    for event in Event.objects.filter(session__release__study=world.study):
        row = {'study_title': 'text:' + world.study.title, 'study_id': str(world.study.id),
               'participant_uuid': str(event.session.participant_id),
               'participant_code': ('text:' + codes[str(event.session.participant_id)])
               if view == 'identified' and codes.get(str(event.session.participant_id)) not in (None, '') else '',
               'session_id': str(event.session_id), 'release_id': str(event.session.release_id),
               'build_id': str(event.session.release.build_id), 'event_id': str(event.event_id),
               'segment_id': str(event.segment_id), 'sequence': str(event.sequence),
               'event_type': 'text:' + event.envelope['event_type'], 'schema_id': 'text:' + event.envelope['schema_id'],
               'schema_version': 'text:' + event.envelope['schema_version'], 'received_at': utc_text(event.received_at),
               'record_json': event.envelope}
        rows.append(row)
    return sorted(rows, key=lambda row: (row['participant_uuid'], row['session_id'], row['segment_id'],
                                         int(row['sequence']), row['event_id']))


def assert_zip_matches_golden(body, world, view, language, evidence=None, name='zip_golden.json'):
    """Parse the served ZIP with stdlib parsers and reconcile it to the golden."""
    members = zip_members(body)
    assert list(members) == list(exports.EXPORT_ZIP_MEMBERS)
    participants_keys, participants = csv_rows(members['participants.csv'])
    events_keys, events = csv_rows(members['events.csv'])
    expected_keys = [key for key in PARTICIPANT_KEYS if view == 'identified' or key != 'participant_code']
    assert participants_keys == expected_keys
    assert events_keys == [key for key in EVENT_KEYS if view == 'identified' or key != 'participant_code']
    expected_participants = expected_participant_rows(world, view, language)
    assert [row['participant_uuid'] for row in participants] == [row['participant_uuid'] for row in expected_participants]
    for served, golden in zip(participants, expected_participants):
        for key in served:
            if key == 'sessions_json':
                assert served[key].startswith('json:')
                assert json.loads(served[key][len('json:'):]) == json.loads(golden[key][len('json:'):]), key
            else:
                assert served[key] == golden[key], (key, served[key], golden[key])
    expected_events = expected_event_rows(world, view)
    assert len(events) == len(expected_events)
    for served, golden in zip(events, expected_events):
        for key in served:
            if key == 'record_json':
                assert served[key].startswith('json:')
                assert json.loads(served[key][len('json:'):]) == golden[key], key
            else:
                assert served[key] == golden[key], (key, served[key], golden[key])
    if evidence is not None:
        evidence(name, {'members': list(members), 'participants': len(participants), 'events': len(events),
                        'view': view, 'language': language,
                        'statuses': sorted({row['technical_status'] for row in participants})})
    return participants, events


def test_zip_round_trips_every_special_value_against_the_golden(world, spool, evidence):
    """The real HTTP ZIP: exactly two members, every edge value restored by
    independent csv/json parsers, and a technical status without scoring."""
    client = sign_in(world.owner)
    created = create_v2(client, world.study, 'identified', 'zh')
    assert created.status_code == 201, created.content
    export_id = created.json()['export_id']
    response = download(client, export_id, 'zip')
    assert response.status_code == 200
    assert response['Content-Type'] == 'application/zip'
    assert response['Content-Disposition'] == f'attachment; filename="{export_id}.zip"'
    zip_body = body_bytes(response)
    participants, events = assert_zip_matches_golden(zip_body, world, 'identified', 'zh', evidence)

    by_uuid = {row['participant_uuid']: row for row in participants}
    a_row = by_uuid[str(world.participants['a'].id)]
    b_row = by_uuid[str(world.participants['b'].id)]
    c_row = by_uuid[str(world.participants['c'].id)]
    assert a_row['participant_code'] == 'text:001'
    assert b_row['participant_code'] == ''
    assert c_row['technical_status'] == 'not_started' and c_row['sessions_json'] == 'json:[]'
    assert a_row['technical_status'] == 'pending'
    # Technical facts only: the frozen columns carry no scoring/validity column.
    assert not any(word in key for key in participants[0] for word in ('score', 'valid', 'exclud'))

    # The formula-looking value, the embedded newline and the large cell are
    # restored exactly (never evaluated, never truncated).
    formula_event = next(row for row in events if row['event_id'] == str(world.events['e1'].event_id))
    record = json.loads(formula_event['record_json'][len('json:'):])
    assert record['payload']['formula'] == FORMULA_TEXT
    assert record['payload']['text'] == NEWLINE_TEXT
    assert len(record['payload']['blob']) == LARGE_CELL_CHARS
    assert formula_event['record_json'].startswith('json:{')
    assert 'text:' not in formula_event['record_json']
    # RFC 4180: the literal newline of the title stays inside one quoted field.
    raw = zip_members(zip_body)['events.csv'].decode('utf-8-sig')
    assert '"text:Synthetic R08 study\n第二行"' in raw
    assert raw.count('\r\n') == len(events) + 1

    # The stored snapshot still carries the original values (no rewriting).
    item = Export.objects.get(pk=export_id)
    frozen = next(row for row in item.snapshot['events'] if row['event_id'] == str(world.events['e1'].event_id))
    assert frozen['record']['payload']['formula'] == FORMULA_TEXT
    assert frozen['record']['payload']['text'] == NEWLINE_TEXT

    # unmapped: no code column, no non-participant roster entry, same records.
    unmapped = create_v2(client, world.study, 'unmapped', 'en')
    assert unmapped.status_code == 201
    unmapped_response = download(client, unmapped.json()['export_id'], 'zip')
    assert unmapped_response.status_code == 200
    unmapped_participants, unmapped_events = assert_zip_matches_golden(
        body_bytes(unmapped_response), world, 'unmapped', 'en', evidence, 'zip_golden_unmapped.json')
    assert str(world.participants['c'].id) not in {row['participant_uuid'] for row in unmapped_participants}
    assert 'participant_code' not in unmapped_participants[0]
    assert len(unmapped_events) == 4
    # The served bytes are stable across repeated downloads.
    again = download(client, export_id, 'zip')
    assert body_bytes(again) == zip_body


def test_zip_and_metadata_permission_matrix_and_listing_projection(world, spool, evidence):
    """raw-only vs raw+identity: ZIP and metadata each re-check the frozen set;
    the listing never exposes identified roster counts or links to raw-only."""
    from django.test import RequestFactory
    from core import workbench
    owner, study = world.owner, world.study
    raw = make_user('p03r08_raw')
    grant(raw, study, 'study.view', 'data.export_raw')
    full = make_user('p03r08_full')
    grant(full, study, 'study.view', 'data.export_raw', 'identity_mapping.read')
    raw_client, full_client, owner_client = sign_in(raw), sign_in(full), sign_in(owner)

    assert create_v2(raw_client, study, 'identified').status_code == 403
    assert create_v2(raw_client, study, 'unmapped').status_code == 201
    identified = create_v2(full_client, study, 'identified')
    assert identified.status_code == 201
    identified_id = identified.json()['export_id']
    unmapped_id = create_v2(raw_client, study, 'unmapped').json()['export_id']

    matrix = {}
    for name, client, export_id, expected in (
            ('raw_identified_zip', raw_client, identified_id, 403),
            ('raw_unmapped_zip', raw_client, unmapped_id, 200),
            ('full_identified_zip', full_client, identified_id, 200),
            ('full_unmapped_zip', full_client, unmapped_id, 200),
            ('owner_identified_zip', owner_client, identified_id, 200)):
        response = download(client, export_id, 'zip')
        matrix[name] = response.status_code
        assert response.status_code == expected, (name, body_bytes(response)[:200])
        if expected == 403:
            assert b'PK' not in body_bytes(response) and 'Content-Disposition' not in response
    for fmt in ('jsonl', 'metadata'):
        assert download(raw_client, identified_id, fmt).status_code == 403
        assert download(full_client, identified_id, fmt).status_code == 200
        assert download(raw_client, unmapped_id, fmt).status_code == 200

    # The listing: the raw-only reader sees the frozen identified row as
    # restricted - no counts, no download link - while the full reader gets both.
    request = RequestFactory().get(f'/studies/{study.id}/exports')
    request.user = raw
    raw_context = workbench.exports_module(request, study)
    raw_rows = {str(row['id']): row for row in raw_context['exports_rows']}
    assert raw_rows[identified_id]['permitted'] is False and raw_rows[identified_id]['counts'] is None
    assert raw_rows[unmapped_id]['permitted'] is True
    request.user = full
    full_context = workbench.exports_module(request, study)
    full_rows = {str(row['id']): row for row in full_context['exports_rows']}
    assert full_rows[identified_id]['permitted'] is True
    assert full_rows[identified_id]['counts'] == {'participants': 4, 'sessions': 5, 'events': 4}
    assert full_rows[identified_id]['required_actions'] == ['data.export_raw', 'identity_mapping.read']

    raw_page = raw_client.get(f'/studies/{study.id}/exports').content.decode()
    assert f'data-export-row="{identified_id}"' in raw_page
    assert 'data-export-restricted="1"' in raw_page
    assert f'/v1/admin/exports/{identified_id}/download?format=zip' not in raw_page
    assert 'text:001' not in raw_page
    assert 'data-export-view="identified"' not in raw_page
    assert re.search(r'<input[^>]*value="unmapped"[^>]*\bchecked\b', raw_page)
    full_page = full_client.get(f'/studies/{study.id}/exports').content.decode()
    assert f'/v1/admin/exports/{identified_id}/download?format=zip' in full_page
    assert f'/v1/admin/exports/{unmapped_id}/download?format=zip' in full_page
    assert 'data-export-view-counts="identified"' in full_page
    assert re.search(r'<input[^>]*value="identified"[^>]*\bchecked\b', full_page)

    # Preview options: a raw-only actor is told the unmapped view is the only one
    # and gets no identified counts; the full actor may create both. The
    # preselected view is always one this actor may really create, so a hidden
    # identified option can never steal the selection from the raw-only reader.
    raw_options = exports.export_options(raw, study)
    assert raw_options['unmapped_only'] is True
    assert raw_options['default_view'] == 'unmapped'
    assert raw_options['views'][0] == {'value': 'identified', 'allowed': False, 'counts': None,
                                       'required_actions': ['data.export_raw', 'identity_mapping.read']}
    full_options = exports.export_options(full, study)
    assert full_options['unmapped_only'] is False
    assert full_options['default_view'] == 'identified'
    assert full_options['views'][0]['allowed'] is True

    # Revocation refuses the whole export in every format instead of degrading.
    Grant.objects.filter(user=full, study=study, action='identity_mapping.read').delete()
    assert download(full_client, identified_id, 'zip').status_code == 403
    assert download(full_client, identified_id, 'metadata').status_code == 403
    Grant.objects.filter(user=full, study=study, action='data.export_raw').delete()
    assert download(full_client, unmapped_id, 'zip').status_code == 403
    assert download(full_client, unmapped_id, 'jsonl').status_code == 403

    # v1 keeps its bytes and formats; an explicit v1 zip fails closed.
    legacy = owner_client.post('/v1/admin/exports', {'study_id': str(study.id)}, content_type='application/json')
    assert legacy.status_code == 201
    legacy_id = legacy.json()['export_id']
    assert download(owner_client, legacy_id, 'zip').status_code == 400
    assert download(owner_client, legacy_id, 'csv').status_code == 200
    assert download(raw_client, legacy_id, 'jsonl').status_code == 200
    # The v2 single-file CSV stays the explicit refusal, not a half package.
    assert download(full_client, identified_id, 'csv').status_code == 403  # permission first
    assert download(owner_client, identified_id, 'csv').status_code == 409
    evidence('permission_matrix.json', {'matrix': matrix, 'legacy_zip': 400, 'v2_csv': 409,
                                        'restricted_listing': True, 'unmapped_only': True})


def test_disk_failure_unknown_state_and_deadline_never_serve_a_half_package(world, spool, evidence, monkeypatch):
    """File failures and unknown stored states: no temp is published, no invalid
    stored ZIP is served, and every refusal names the JSONL path."""
    owner, study = world.owner, world.study
    client = sign_in(owner)
    created = create_v2(client, study, 'unmapped', 'en')
    assert created.status_code == 201
    item = Export.objects.get(pk=created.json()['export_id'])
    path = exports.export_zip_path(item)

    def temp_files():
        return [entry.name for entry in spool.iterdir() if entry.name.endswith('.tmp')]

    # 1. A disk failure at the atomic rename: nothing is published, no temp stays.
    def failing_replace(source, target):
        raise OSError('synthetic disk failure at rename')

    with monkeypatch.context() as fault:
        fault.setattr(exports.os, 'replace', failing_replace)
        failed = download(client, item.id, 'zip')
        assert failed.status_code == 503 and failed.json()['code'] == 'export_generation_failed'
        assert failed.json()['jsonl_available'] is True
        assert 'Content-Disposition' not in failed and b'PK' not in body_bytes(failed)
        assert not path.exists() and temp_files() == []

    # 2. A disk failure while flushing/fsyncing the temp file: same result.
    def failing_fsync(descriptor):
        raise OSError('synthetic disk failure at fsync')

    with monkeypatch.context() as fault:
        fault.setattr(exports.os, 'fsync', failing_fsync)
        failed = download(client, item.id, 'zip')
        assert failed.status_code == 503 and failed.json()['code'] == 'export_generation_failed'
        assert not path.exists() and temp_files() == []

    # The lossless JSONL path stays available through every file failure, and a
    # later retry publishes a complete package.
    assert download(client, item.id).status_code == 200
    assert download(client, item.id, 'metadata').status_code == 200
    retried = download(client, item.id, 'zip')
    assert retried.status_code == 200
    good = body_bytes(retried)
    assert_zip_matches_golden(good, world, 'unmapped', 'en')

    # 3. Unknown stored state: a truncated file is never served; it is replaced
    #    by a regenerated complete package.
    path.write_bytes(good[:len(good) // 2])
    unknown = download(client, item.id, 'zip')
    assert unknown.status_code == 200
    served = body_bytes(unknown)
    assert served != good[:len(good) // 2]
    assert_zip_matches_golden(served, world, 'unmapped', 'en')

    # 4. A stored ZIP with the wrong members is also unknown state, never served.
    wrong = io.BytesIO()
    with zipfile.ZipFile(wrong, 'w') as archive:
        archive.writestr('evil.txt', 'not the frozen bundle')
    path.write_bytes(wrong.getvalue())
    replaced = download(client, item.id, 'zip')
    assert replaced.status_code == 200
    replaced_body = body_bytes(replaced)
    assert zip_members(replaced_body) == zip_members(good)
    assert b'evil.txt' not in replaced_body

    # 4b. A planted symlink is never followed: it is replaced by the atomic
    #     rename and the linked target is left untouched.
    outside = spool.parent / 'outside-target.zip'
    outside.write_bytes(good)
    path.unlink()
    path.symlink_to(outside)
    symlinked = download(client, item.id, 'zip')
    assert symlinked.status_code == 200
    assert zip_members(body_bytes(symlinked)) == zip_members(good)
    assert not path.is_symlink() and outside.read_bytes() == good

    # 5. A leftover temp file from an interrupted run is never served.
    stale = spool / '.stale-synthetic.tmp'
    stale.write_bytes(b'partial junk that must never be served')
    fresh = download(client, item.id, 'zip')
    assert fresh.status_code == 200 and b'partial junk' not in body_bytes(fresh)
    assert stale.read_bytes() == b'partial junk that must never be served'

    def new_temp_files():
        return [name for name in temp_files() if name != stale.name]

    # 6. The generation deadline: a direct over-budget call publishes nothing.
    path.unlink()
    with pytest.raises(Rejected) as timeout:
        exports.render_zip_download(item, generation_seconds=-1.0)
    assert timeout.value.code == 'export_generation_timeout' and timeout.value.status == 503
    assert not path.exists() and new_temp_files() == []

    # 7. A crossing after the real file commit discards the just-published ZIP.
    now = [0.0]
    original_store = exports._store_export_zip

    def store_then_cross(root, final, payload):
        result = original_store(root, final, payload)
        now[0] = 31.0
        return result

    monkeypatch.setattr(exports, '_store_export_zip', store_then_cross)
    monkeypatch.setattr(exports.time, 'monotonic', lambda: now[0])
    with pytest.raises(Rejected) as committed_late:
        exports.render_zip_download(item, generation_seconds=30.0)
    assert committed_late.value.code == 'export_generation_timeout'
    assert not path.exists() and new_temp_files() == []
    evidence('file_failures.json', {'rename_failure': 503, 'fsync_failure': 503, 'timeout': 503,
                                    'unknown_state_regenerated': True, 'stale_temp_not_served': True,
                                    'jsonl_kept': True})


def test_cached_zip_is_bound_to_the_frozen_export_and_bounded(world, spool, evidence, monkeypatch):
    """A stored ZIP is served only when it is byte-identical to the deterministic
    rebuild of this export's frozen snapshot; the final compressed package is
    bounded like the CSV pair, cached files included."""
    owner, study = world.owner, world.study
    client = sign_in(owner)
    identified = create_v2(client, study, 'identified', 'en').json()['export_id']
    unmapped = create_v2(client, study, 'unmapped', 'en').json()['export_id']
    unmapped_zh = create_v2(client, study, 'unmapped', 'zh').json()['export_id']
    identified_bytes = body_bytes(download(client, identified, 'zip'))
    unmapped_bytes = body_bytes(download(client, unmapped, 'zip'))
    zh_bytes = body_bytes(download(client, unmapped_zh, 'zip'))
    assert identified_bytes != unmapped_bytes and zh_bytes != unmapped_bytes
    path = exports.export_zip_path(Export.objects.get(pk=unmapped))

    raw = make_user('p03r08_cache_raw')
    grant(raw, study, 'study.view', 'data.export_raw')
    full = make_user('p03r08_cache_full')
    grant(full, study, 'study.view', 'data.export_raw', 'identity_mapping.read')
    raw_client, full_client = sign_in(raw), sign_in(full)

    # A valid ZIP of another view (identified) planted at this unmapped export's
    # path is never served - neither to a raw-only reader nor to one who may
    # read identified exports: the served bytes are this export's rebuild.
    for reader in (raw_client, full_client):
        path.write_bytes(identified_bytes)
        served = download(reader, unmapped, 'zip')
        assert served.status_code == 200
        assert body_bytes(served) == unmapped_bytes
    # A valid ZIP of the same view but another language is not this export either.
    path.write_bytes(zh_bytes)
    assert body_bytes(download(raw_client, unmapped, 'zip')) == unmapped_bytes
    # The stored file was really replaced by the bound rebuild, not bypassed.
    assert path.read_bytes() == unmapped_bytes

    # The final compressed ZIP boundary: an over-limit package refuses only the
    # download (JSONL stays open) and publishes nothing.
    path.unlink()
    monkeypatch.setattr(exports, 'MAX_ZIP_BYTES', len(unmapped_bytes) - 1)
    refused = download(client, unmapped, 'zip')
    assert refused.status_code == 413 and refused.json()['code'] == 'export_limit_zip'
    assert refused.json()['jsonl_available'] is True and not path.exists()
    # A stored package over the boundary is never served either, and the stored
    # bytes are left untouched rather than deleted or half-served.
    monkeypatch.setattr(exports, 'MAX_ZIP_BYTES', len(unmapped_bytes))
    assert body_bytes(download(client, unmapped, 'zip')) == unmapped_bytes
    assert path.read_bytes() == unmapped_bytes
    monkeypatch.setattr(exports, 'MAX_ZIP_BYTES', len(unmapped_bytes) - 1)
    cached_refused = download(client, unmapped, 'zip')
    assert cached_refused.status_code == 413 and cached_refused.json()['code'] == 'export_limit_zip'
    assert path.read_bytes() == unmapped_bytes
    assert download(client, unmapped, 'metadata').status_code == 200
    evidence('cache_binding.json', {'other_view_replaced': True, 'other_language_replaced': True,
                                    'zip_limit': 413, 'cached_over_limit_refused': True,
                                    'served_bytes': len(unmapped_bytes)})


def test_spool_setup_and_symlink_components_are_refused(world, spool, evidence, monkeypatch):
    """A filesystem setup failure or a symlinked spool component is refused as
    the documented file failure; nothing outside the spool is written and the
    lossless JSONL path stays open."""
    owner, study = world.owner, world.study
    client = sign_in(owner)
    created = create_v2(client, study, 'unmapped', 'en')
    item = Export.objects.get(pk=created.json()['export_id'])
    path = exports.export_zip_path(item)

    # A setup failure while creating the spool maps to the documented refusal.
    def failing_mkdir(self, *args, **kwargs):
        raise OSError('synthetic spool setup failure')

    with monkeypatch.context() as fault:
        fault.setattr(Path, 'mkdir', failing_mkdir)
        failed = download(client, item.id, 'zip')
    assert failed.status_code == 503 and failed.json()['code'] == 'export_generation_failed'
    assert failed.json()['jsonl_available'] is True
    assert 'Content-Disposition' not in failed and b'PK' not in body_bytes(failed)
    assert not path.exists()
    assert download(client, item.id).status_code == 200

    # A symlinked spool component is refused before anything outside is written.
    outside = spool.parent / 'outside-spool'
    outside.mkdir()
    if spool.is_dir():
        shutil.rmtree(spool)
    spool.symlink_to(outside)
    refused = download(client, item.id, 'zip')
    assert refused.status_code == 503 and refused.json()['code'] == 'export_generation_failed'
    assert list(outside.iterdir()) == []
    assert download(client, item.id).status_code == 200
    evidence('spool_safety.json', {'setup_failure': 503, 'symlink_refused': 503, 'outside_entries': []})


def test_over_limit_cells_keep_jsonl_and_untrusted_names_never_reach_files(world, spool, evidence):
    """A cell over the spreadsheet boundary refuses only the ZIP (JSONL stays);
    a hostile study title never reaches the download file name or member names."""
    owner, study = world.owner, world.study
    client = sign_in(owner)

    # A hostile title (path separators, quote, CRLF, non-ASCII) is frozen as a
    # CSV text value only; the file name stays the immutable UUID.
    study.title = '../../evil" name\r\n报告.zip'
    study.save()
    hostile = create_v2(client, study, 'unmapped', 'en')
    assert hostile.status_code == 201
    hostile_id = hostile.json()['export_id']
    served = download(client, hostile_id, 'zip')
    assert served.status_code == 200, body_bytes(served)[:200]
    assert served['Content-Disposition'] == f'attachment; filename="{hostile_id}.zip"'
    assert 'evil' not in served['Content-Disposition'] and '\r' not in served['Content-Disposition']
    members = zip_members(body_bytes(served))
    assert list(members) == ['participants.csv', 'events.csv']
    assert all('/' not in name and '\\' not in name for name in members)
    rows = csv_rows(members['participants.csv'])[1]
    assert all(row['study_title'] == 'text:../../evil" name\r\n报告.zip' for row in rows)

    # A cell over the spreadsheet boundary refuses only the ZIP; JSONL stays.
    world.add_event(world.sessions['a2'], sequence=1, payload={'blob': 'x' * (exports.MAX_CELL_CHARS + 500)})
    created = create_v2(client, study, 'unmapped', 'en')
    assert created.status_code == 201
    item = Export.objects.get(pk=created.json()['export_id'])

    refused = download(client, item.id, 'zip')
    assert refused.status_code == 413
    body = refused.json()
    assert body['code'] == 'export_cell_limit' and body['jsonl_available'] is True
    assert 'Content-Disposition' not in refused and b'x' * 100 not in body_bytes(refused)
    assert not exports.export_zip_path(item).exists()
    lossless = download(client, item.id)
    assert lossless.status_code == 200 and b'x' * (exports.MAX_CELL_CHARS + 500) in body_bytes(lossless)
    assert download(client, item.id, 'metadata').status_code == 200
    evidence('limits_and_names.json', {'zip_refusal': 413, 'jsonl_bytes': len(body_bytes(lossless)),
                                      'filename': served['Content-Disposition'], 'members': list(members)})


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')
def test_real_chrome_downloads_the_zip_and_matches_the_independent_golden(world, spool, live_server,
                                                                          evidence_root, evidence):
    """A real Chrome session creates the v2 snapshot, downloads the ZIP through
    the browser and the metadata link; the saved bytes are reconciled here with
    independent csv/json parsers against the fixed synthetic golden."""
    owner, study = world.owner, world.study
    root = Path(__file__).resolve().parents[2]
    evidence_dir = evidence_root
    script = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_ADMIN_BASE, study=process.env.GEP_STUDY_ID, evidence=process.env.GEP_EVIDENCE_DIR;
try {
  const page=await browser.newPage();
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  const login=await page.goto(base+'/login'); expect(login.status()).toBe(200);
  await page.locator('[name=username]').fill('p03r08_owner');
  await page.locator('[name=password]').fill(process.env.GEP_OWNER_PASSWORD);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/studies/'+study+'/exports');
  expect(await page.locator('[data-export-view="identified"]').isChecked()).toBe(true);
  expect((await page.locator('[data-export-view-counts="identified"]').innerText()).includes('名单 4')).toBe(true);
  await page.locator('[data-export-language]').selectOption('zh');
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('[data-export-submit]').click()]);
  const name=download.suggestedFilename();
  if(!/^[0-9a-f-]+\.zip$/.test(name)){throw new Error('unexpected download name: '+name);}
  await download.saveAs(evidence+'/chrome_download.zip');
  await page.waitForSelector('[data-created-format="metadata"]');
  const [metaDownload]=await Promise.all([page.waitForEvent('download'),page.locator('[data-created-format="metadata"]').click()]);
  if(!/\.metadata\.json$/.test(metaDownload.suggestedFilename())){throw new Error('unexpected metadata name');}
  await metaDownload.saveAs(evidence+'/chrome_metadata.json');
  // The legacy module-page scripts keep their v1 control and link name.
  await page.locator('#export-form').getByRole('button',{name:'创建 JSONL 固定快照'}).click();
  const legacyLink=page.locator('#export-result').getByRole('link',{name:'下载 JSONL'});
  await legacyLink.waitFor({timeout:30000});
  const [legacyDownload]=await Promise.all([page.waitForEvent('download'),legacyLink.click()]);
  await legacyDownload.saveAs(evidence+'/chrome_legacy.jsonl');
  if(errors.length){throw new Error('page errors: '+JSON.stringify(errors));}
  console.log('OK: real Chrome created the snapshot and downloaded ZIP + metadata');
} finally {await browser.close();}
'''
    env = dict(os.environ, GEP_ADMIN_BASE=live_server.url, GEP_STUDY_ID=str(study.id),
               GEP_EVIDENCE_DIR=str(evidence_dir), GEP_OWNER_PASSWORD=OWNER_PASSWORD)
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr

    downloaded = (evidence_dir / 'chrome_download.zip').read_bytes()
    assert_zip_matches_golden(downloaded, world, 'identified', 'zh')
    metadata = json.loads((evidence_dir / 'chrome_metadata.json').read_text(encoding='utf-8'))
    assert metadata['format_version'] == '2' and metadata['view'] == 'identified' and metadata['language'] == 'zh'
    assert metadata['counts'] == {'participants': 4, 'sessions': 5, 'events': 4}
    # The whitelist names the frozen columns but carries no data value: no
    # records, no roster code, no payload byte of the large cell.
    assert 'records' not in metadata
    assert 'text:001' not in json.dumps(metadata) and 'blob' not in json.dumps(metadata)
    assert set(metadata) == {'id', 'format_version', 'view', 'language', 'study_id', 'title', 'created_at',
                             'counts', 'limits', 'field_contract', 'csv_encoding'}
    # The legacy v1 JSONL control still creates the byte-frozen v1 snapshot.
    legacy_lines = [json.loads(line) for line in
                    (evidence_dir / 'chrome_legacy.jsonl').read_text(encoding='utf-8').splitlines()]
    assert legacy_lines and all(set(line) == {'study_id', 'release_id', 'build_id', 'record'}
                                for line in legacy_lines)
    evidence('chrome_download.json', {'suggested_name': 'zip', 'zip_bytes': len(downloaded),
                                      'metadata_counts': metadata['counts'], 'browser': 'chrome',
                                      'legacy_v1_lines': len(legacy_lines)})


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')
def test_real_chrome_raw_only_main_button_defaults_unmapped_and_reports_zip_failures(
        world, spool, live_server, evidence_root, evidence):
    """A raw-only session clicks the main button without choosing a view: the
    unmapped view is preselected, the downloaded ZIP really carries unmapped
    contents, and a real server or network ZIP failure is reported with retry
    and JSONL links instead of a false download success."""
    owner, study = world.owner, world.study
    raw = make_user('p03r08_chrome_raw')
    grant(raw, study, 'study.view', 'data.export_raw')
    root = Path(__file__).resolve().parents[2]
    evidence_dir = evidence_root
    success_script = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_ADMIN_BASE, study=process.env.GEP_STUDY_ID, evidence=process.env.GEP_EVIDENCE_DIR;
try {
  const page=await browser.newPage();
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  const login=await page.goto(base+'/login'); expect(login.status()).toBe(200);
  await page.locator('[name=username]').fill(process.env.GEP_RAW_USER);
  await page.locator('[name=password]').fill(process.env.GEP_OWNER_PASSWORD);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/studies/'+study+'/exports');
  // The hidden identified option never steals the selection: the only view this
  // account may create is preselected and the main button is clicked directly.
  expect(await page.locator('[data-export-view="identified"]').count()).toBe(0);
  expect(await page.locator('[data-export-view="unmapped"]').isChecked()).toBe(true);
  expect(await page.locator('[data-export-unmapped-note]').isVisible()).toBe(true);
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('[data-export-submit]').click()]);
  const name=download.suggestedFilename();
  if(!/^[0-9a-f-]+\.zip$/.test(name)){throw new Error('unexpected download name: '+name);}
  await download.saveAs(evidence+'/chrome_raw_unmapped.zip');
  if(!(await page.locator('#export-result').innerText()).includes('ZIP 下载已开始')){throw new Error('missing download-started feedback');}
  if(errors.length){throw new Error('page errors: '+JSON.stringify(errors));}
  console.log('OK: raw-only main-button download defaults to unmapped');
} finally {await browser.close();}
'''
    env = dict(os.environ, GEP_ADMIN_BASE=live_server.url, GEP_STUDY_ID=str(study.id),
               GEP_EVIDENCE_DIR=str(evidence_dir), GEP_OWNER_PASSWORD=OWNER_PASSWORD,
               GEP_RAW_USER=raw.username)
    result = subprocess.run(['node', '--input-type=module', '-e', success_script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr

    downloaded = (evidence_dir / 'chrome_raw_unmapped.zip').read_bytes()
    participants, _events = assert_zip_matches_golden(downloaded, world, 'unmapped', 'en')
    assert 'participant_code' not in participants[0]
    assert str(world.participants['c'].id) not in {row['participant_uuid'] for row in participants}

    # A real server-side ZIP failure: a cell over the spreadsheet boundary
    # refuses only the ZIP. The page must report the refusal with the code while
    # keeping the explicit ZIP retry link and the lossless JSONL link, and no
    # download may be triggered; a real network failure gets the same treatment.
    world.add_event(world.sessions['a2'], sequence=1, payload={'blob': 'x' * (exports.MAX_CELL_CHARS + 500)})
    failure_script = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_ADMIN_BASE, study=process.env.GEP_STUDY_ID, evidence=process.env.GEP_EVIDENCE_DIR;
try {
  const page=await browser.newPage();
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  let downloads=0; page.on('download',()=>{downloads++;});
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(process.env.GEP_RAW_USER);
  await page.locator('[name=password]').fill(process.env.GEP_OWNER_PASSWORD);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/studies/'+study+'/exports');
  expect(await page.locator('[data-export-view="unmapped"]').isChecked()).toBe(true);

  // 1. The real server refusal of the ZIP (over-limit cell): reported, not
  //    announced as a download, with retry and JSONL links kept.
  await page.locator('[data-export-submit]').click();
  await expect(page.locator('#export-result')).toContainText('export_cell_limit');
  let text=await page.locator('#export-result').innerText();
  if(text.includes('ZIP 下载已开始')){throw new Error('false download success: '+text);}
  if(downloads!==0){throw new Error('a download was triggered despite the ZIP refusal');}
  expect(await page.locator('[data-created-format="zip"]').count()).toBe(1);
  const jsonlLink=page.locator('[data-created-format="jsonl"]');
  expect(await jsonlLink.count()).toBe(1);
  const [jsonlDownload]=await Promise.all([page.waitForEvent('download'),jsonlLink.click()]);
  await jsonlDownload.saveAs(evidence+'/chrome_raw_lossless.jsonl');

  // 2. A real network failure of the ZIP fetch: same truthful feedback.
  await page.route(/download\?format=zip/,route=>route.abort('failed'));
  await page.locator('[data-export-submit]').click();
  await expect(page.locator('#export-result')).toContainText('ZIP 下载不可用');
  text=await page.locator('#export-result').innerText();
  if(!text.includes('network')){throw new Error('missing network failure detail: '+text);}
  if(text.includes('ZIP 下载已开始')){throw new Error('false download success after network failure: '+text);}
  if(downloads!==1){throw new Error('unexpected extra download: '+downloads);}
  expect(await page.locator('[data-created-format="jsonl"]').count()).toBe(1);
  await page.unroute(/download\?format=zip/);
  if(errors.length){throw new Error('page errors: '+JSON.stringify(errors));}
  console.log('OK: raw-only ZIP server/network failures reported with retry and JSONL links');
} finally {await browser.close();}
'''
    result = subprocess.run(['node', '--input-type=module', '-e', failure_script], cwd=root, env=env,
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [json.loads(line) for line in
             (evidence_dir / 'chrome_raw_lossless.jsonl').read_text(encoding='utf-8').splitlines()]
    assert any(len(line['record']['payload'].get('blob', '')) == exports.MAX_CELL_CHARS + 500 for line in lines)
    evidence('chrome_raw_only.json', {'default_view': 'unmapped', 'zip_bytes': len(downloaded),
                                      'server_zip_failure': 'export_cell_limit',
                                      'network_zip_failure': True, 'jsonl_lines': len(lines),
                                      'browser': 'chrome'})
