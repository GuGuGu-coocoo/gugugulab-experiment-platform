"""P03R07 evidence: frozen export v2 snapshots, the permission projection and the
capacity boundaries.

The expected side of every check is the R00 §E contract (2026-09-23) and the
fixed synthetic world built below, never the implementation under test:

* ``identified`` freezes raw records plus identity mapping
  (``data.export_raw`` + ``identity_mapping.read``); ``unmapped`` freezes raw
  records only and never stores a participant code. All formats re-check the
  frozen set on create and download, so a revoked grant refuses the whole
  export instead of degrading it, and metadata is a fixed whitelist rather than
  a snapshot passthrough.
* A v2 export is frozen at creation: a later study rename, a late event, a new
  participant, a new release or a changed code never changes its bytes. The
  identified roster keeps participants without sessions; unmapped keeps only
  participants who really have a session in scope.
* The legacy v1 bytes stay untouched, an explicit session scope can never omit
  an unknown or cross-study id, every hard count boundary refuses before a row
  exists, and an over-limit spreadsheet cell refuses only CSV/ZIP while the
  lossless JSONL path keeps working.
"""
import csv
import io
import json
import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from core import exports
from core.models import (AccountProfile, Build, Event, Export, Grant, Instance,
                         Participant, Release, Session, Study)
from core.protocol import Rejected

OWNER_PASSWORD = 'Synthetic-p03r07-owner-password'
METADATA_WHITELIST = {'id', 'format_version', 'view', 'language', 'study_id', 'title',
                      'created_at', 'counts', 'limits', 'field_contract', 'csv_encoding'}


def make_user(name):
    return get_user_model().objects.create_user(name, password=OWNER_PASSWORD)


def sign_in(who):
    """Login that also satisfies the AccountGate auth_version check."""
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


def csv_rows(body):
    """Rows keyed by stable column key, independent of the CSV header language."""
    reader = csv.reader(io.StringIO(body.decode('utf-8-sig')))
    header = next(reader)
    keys = [column.split('（')[-1].rstrip('）') if '（' in column else column for column in header]
    return [dict(zip(keys, row)) for row in reader]


def v2_request(study, view, language='zh', **extra):
    payload = {'study_id': str(study.id), 'format_version': '2', 'view': view, 'language': language}
    payload.update(extra)
    return payload


def create_v2(client, study, view, language='zh', **extra):
    return client.post('/v1/admin/exports', v2_request(study, view, language, **extra), content_type='application/json')


def download(client, export_id, fmt=None):
    url = f'/v1/admin/exports/{export_id}/download'
    return client.get(url + (f'?format={fmt}' if fmt else ''))


class World:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __getitem__(self, key):
        return self.__dict__[key]


@pytest.fixture
def world(db):
    """A v2 instance with a fixed participant/session/event layout.

    a: code 001, two sessions (one complete with two events, one zero-event)
    b: null code, two sessions (one zero-event, one complete with one event)
    c: code 003, no session at all (identified-only participant)
    e: code 005, one complete single-event session
    """
    owner = make_user('p03r07_owner')
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    AccountProfile.objects.create(user=owner, role='user', policy_version=2)
    study = Study.objects.create(title='Synthetic R07 study', recruitment='open', max_sessions=10)
    build = Build.objects.create(study=study, descriptor={'platform': 'synthetic', 'version': '1.0'}, digest='b' * 64)
    release = Release.objects.create(study=study, build=build, config={'purpose': 'synthetic'}, approved=True)

    def add_participant(code):
        return Participant.objects.create(study=study, code=code)

    def add_session(participant, *, completion=None):
        return Session.objects.create(participant=participant, release=release, operation=uuid.uuid4(),
                                      proof_hash='p' * 48, request={'seed': 'synthetic'}, token_hash='t' * 64,
                                      expires_at=timezone.now() + timedelta(days=1), completion=completion)

    def add_event(session, *, sequence=1, payload=None):
        envelope = {'protocol_version': 'gep/1', 'event_id': str(uuid.uuid4()), 'session_id': str(session.id),
                    'segment_id': str(uuid.uuid4()), 'sequence': sequence, 'event_type': 'exp.rt',
                    'schema_id': 'rt', 'schema_version': '1',
                    'payload': {'rt_ms': 321.5, 'choice': 'left'} if payload is None else payload}
        return Event.objects.create(session=session, event_id=uuid.UUID(envelope['event_id']),
                                    segment_id=uuid.UUID(envelope['segment_id']), sequence=sequence,
                                    envelope=envelope)

    participants = {key: add_participant(code) for key, code in
                    (('a', '001'), ('b', None), ('c', '003'), ('e', '005'))}
    sessions = {}
    sessions['a1'] = add_session(participants['a'])
    events = {'e1': add_event(sessions['a1']), 'e2': add_event(sessions['a1'], sequence=2)}
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


def test_v2_permission_matrix_metadata_whitelist_and_revocation(world, evidence):
    """identified/unmapped x create/download x JSONL/metadata/v1 CSV, with the
    frozen requirement re-checked on read and a whitelist-only metadata body."""
    owner, study = world['owner'], world['study']
    raw = make_user('p03r07_raw')
    grant(raw, study, 'study.view', 'data.export_raw')
    full = make_user('p03r07_full')
    grant(full, study, 'study.view', 'data.export_raw', 'identity_mapping.read')
    identity_only = make_user('p03r07_identity')
    grant(identity_only, study, 'study.view', 'identity_mapping.read')
    owner_client, raw_client, full_client, identity_client = (sign_in(who) for who in (owner, raw, full, identity_only))

    matrix = {}
    for name, client, view, expected in (
            ('raw_identified', raw_client, 'identified', 403), ('raw_unmapped', raw_client, 'unmapped', 201),
            ('full_identified', full_client, 'identified', 201), ('full_unmapped', full_client, 'unmapped', 201),
            ('identity_identified', identity_client, 'identified', 403), ('identity_unmapped', identity_client, 'unmapped', 403),
            ('owner_identified', owner_client, 'identified', 201), ('owner_unmapped', owner_client, 'unmapped', 201)):
        response = create_v2(client, study, view)
        matrix[name] = response.status_code
        assert response.status_code == expected, (name, response.content)

    identified_id = create_v2(full_client, study, 'identified').json()['export_id']
    unmapped_id = create_v2(raw_client, study, 'unmapped').json()['export_id']

    # Read projection: a reader without identity mapping can never read the
    # frozen identified export in any format, and the refusal carries no data.
    for fmt in (None, 'metadata'):
        refused = download(raw_client, identified_id, fmt)
        assert refused.status_code == 403 and b'321.5' not in refused.content and b'text:001' not in refused.content
    for fmt in (None, 'metadata'):
        assert download(full_client, identified_id, fmt).status_code == 200
        assert download(full_client, unmapped_id, fmt).status_code == 200
        assert download(raw_client, unmapped_id, fmt).status_code == 200
        assert download(raw_client, identified_id, 'csv').status_code == 403  # the frozen set comes first
        assert download(full_client, identified_id, 'csv').status_code == 409  # the v2 CSV is the ZIP bundle

    # v1 keeps its original frozen requirement (raw only) in every format,
    # including the legacy single-file CSV.
    legacy = owner_client.post('/v1/admin/exports', {'study_id': str(study.id)}, content_type='application/json')
    assert legacy.status_code == 201
    legacy_id = legacy.json()['export_id']
    assert download(raw_client, legacy_id).status_code == 200
    assert download(raw_client, legacy_id, 'csv').status_code == 200
    assert download(raw_client, legacy_id, 'metadata').status_code == 200
    assert download(identity_client, legacy_id).status_code == 403
    assert download(identity_client, legacy_id, 'csv').status_code == 403
    assert download(identity_client, legacy_id, 'metadata').status_code == 403

    # metadata is the version-2 whitelist, never the snapshot: no records, no
    # roster, no code, no event payload for either view.
    described = download(full_client, identified_id, 'metadata')
    assert described.status_code == 200
    metadata = described.json()
    assert set(metadata) == METADATA_WHITELIST
    assert metadata['view'] == 'identified' and metadata['format_version'] == '2'
    assert metadata['counts'] == {'participants': 4, 'sessions': 5, 'events': 4}
    assert b'321.5' not in described.content and b'text:001' not in described.content
    unmapped_metadata = download(full_client, unmapped_id, 'metadata').json()
    assert set(unmapped_metadata) == METADATA_WHITELIST
    assert unmapped_metadata['counts'] == {'participants': 3, 'sessions': 5, 'events': 4}
    assert 'participant_code' not in json.dumps(unmapped_metadata)

    # Revocation refuses the whole export in every format instead of degrading.
    assert download(full_client, identified_id).status_code == 200
    Grant.objects.filter(user=full, study=study, action='identity_mapping.read').delete()
    for fmt in (None, 'metadata', 'csv'):
        revoked = download(full_client, identified_id, fmt)
        assert revoked.status_code == 403 and b'321.5' not in revoked.content
    Grant.objects.filter(user=full, study=study, action='data.export_raw').delete()
    assert download(full_client, unmapped_id).status_code == 403
    assert download(full_client, unmapped_id, 'metadata').status_code == 403

    evidence('permission_matrix.json', {
        'create': matrix, 'v1_csv_raw': 200, 'v1_jsonl_identity_only': 403, 'v1_csv_identity_only': 403,
        'identified_read_without_identity': 403, 'unmapped_read_without_raw': 403,
        'metadata_keys': sorted(metadata), 'identified_counts': metadata['counts'],
        'unmapped_counts': unmapped_metadata['counts']})


def test_v2_snapshot_is_byte_frozen_and_roster_views_are_exact(world, evidence):
    """Freeze, then rename/add event/add participant/change code/new release:
    old bytes stay identical; identified keeps non-participants, unmapped never
    exposes a code."""
    owner, study = world['owner'], world['study']
    owner_client = sign_in(owner)
    created = create_v2(owner_client, study, 'identified', 'zh')
    assert created.status_code == 201
    identified = Export.objects.get(pk=created.json()['export_id'])
    assert identified.snapshot['title'] == 'Synthetic R07 study'
    unmapped = create_v2(owner_client, study, 'unmapped', 'en')
    assert unmapped.status_code == 201
    unmapped_item = Export.objects.get(pk=unmapped.json()['export_id'])
    scoped = create_v2(owner_client, study, 'identified', 'zh',
                       session_ids=[str(world.sessions['a1'].id), str(world.sessions['b2'].id)])
    assert scoped.status_code == 201
    scoped_item = Export.objects.get(pk=scoped.json()['export_id'])

    before = {fmt: download(owner_client, identified.id, fmt).content for fmt in (None, 'metadata')}
    csv_before = exports.render_csv_bundle(identified.snapshot)
    jsonl_before = exports.render_jsonl(identified.snapshot)
    unmapped_csv_before = exports.render_csv_bundle(unmapped_item.snapshot)
    scoped_csv_before = exports.render_csv_bundle(scoped_item.snapshot)
    # Independent source-side order key for the frozen events.
    source = [(str(event.session.participant_id), str(event.session_id), str(event.segment_id),
               event.sequence, str(event.event_id)) for event in Event.objects.filter(session__release__study=study)]

    # Everything below happens after the freeze and must not alter the export.
    study.title = 'Renamed after the freeze'
    study.save()
    world.add_event(world.sessions['a2'], sequence=1)
    world.add_participant('998')
    world.participants['a'].code = '777'
    world.participants['a'].save()
    second_build = Build.objects.create(study=study, descriptor={'platform': 'synthetic', 'version': '2.0'}, digest='c' * 64)
    Release.objects.create(study=study, build=second_build, config={}, approved=True)
    world.add_session(world.participants['c'])

    after = {fmt: download(owner_client, identified.id, fmt).content for fmt in (None, 'metadata')}
    assert after == before
    assert exports.render_csv_bundle(identified.snapshot) == csv_before
    assert exports.render_jsonl(identified.snapshot) == jsonl_before
    assert exports.render_csv_bundle(unmapped_item.snapshot) == unmapped_csv_before
    assert exports.render_csv_bundle(scoped_item.snapshot) == scoped_csv_before
    identified.refresh_from_db()
    assert identified.snapshot['title'] == 'Synthetic R07 study'

    # identified roster: every participant, including the one never admitted.
    participants_csv = csv_before[0]
    rows = {row['participant_uuid']: row for row in csv_rows(participants_csv)}
    assert set(rows) == {str(world.participants[key].id) for key in ('a', 'b', 'c', 'e')}
    assert list(rows) == sorted(rows)  # overview order is the participant UUID
    header = next(csv.reader(io.StringIO(participants_csv.decode('utf-8-sig'))))
    assert header[3] == '名单 ID（participant_code）' and header[-1] == '会话列表 JSON（sessions_json）'
    c_row = rows[str(world.participants['c'].id)]
    assert (c_row['session_count'], c_row['complete_session_count'], c_row['event_count']) == ('0', '0', '0')
    assert c_row['first_session_created_at'] == '' and c_row['last_session_created_at'] == ''
    assert c_row['technical_status'] == 'not_started' and c_row['sessions_json'] == 'json:[]'
    a_row = rows[str(world.participants['a'].id)]
    assert (a_row['session_count'], a_row['complete_session_count'], a_row['event_count']) == ('2', '1', '2')
    assert a_row['technical_status'] == 'pending' and a_row['participant_code'] == 'text:001'
    own_sessions = json.loads(a_row['sessions_json'][len('json:'):])
    expected_sessions = sorted(
        [{'id': str(session.id), 'created_at': exports.iso_z(session.created_at)}
         for session in (world.sessions['a1'], world.sessions['a2'])],
        key=lambda row: (row['created_at'], row['id']))
    assert [row['id'] for row in own_sessions] == [row['id'] for row in expected_sessions]
    assert sorted(row['event_count'] for row in own_sessions) == [0, 2]
    assert all(set(row) == {'id', 'release_id', 'build_id', 'created_at', 'complete', 'event_count'} for row in own_sessions)
    b_row = rows[str(world.participants['b'].id)]
    assert b_row['participant_code'] == ''  # identified shows the null code as empty
    assert any(row['code'] is None for row in identified.snapshot['participants'])  # the JSON keeps null

    # JSONL: faithful order and values, identified carries the real null code.
    lines = [json.loads(line) for line in jsonl_before.decode().splitlines()]
    assert [line['event_id'] for line in lines] == [row[4] for row in sorted(source)]
    b_line = next(line for line in lines if line['participant_uuid'] == str(world.participants['b'].id))
    assert b_line['participant_code'] is None and b_line['sequence'] == 1
    assert b_line['record']['payload'] == {'rt_ms': 321.5, 'choice': 'left'}

    # unmapped: only participants with a real session in scope, and no code column.
    unmapped_participants, unmapped_events = exports.render_csv_bundle(unmapped_item.snapshot)
    unmapped_rows = csv_rows(unmapped_participants)
    assert [row['participant_uuid'] for row in unmapped_rows] == sorted(
        str(world.participants[key].id) for key in ('a', 'b', 'e'))
    assert 'participant_code' not in unmapped_participants.decode('utf-8-sig').splitlines()[0]
    assert 'participant_code' not in unmapped_events.decode('utf-8-sig').splitlines()[0]
    assert 'text:001' not in unmapped_participants.decode('utf-8-sig')
    assert 'text:001' not in unmapped_events.decode('utf-8-sig')
    assert all(row['code'] is None for row in unmapped_item.snapshot['participants'])
    assert len(csv_rows(unmapped_events)) == 4

    # A real explicit session scope keeps exactly the selected participants.
    scoped_participants, scoped_events = exports.render_csv_bundle(scoped_item.snapshot)
    assert [row['participant_uuid'] for row in csv_rows(scoped_participants)] == sorted(
        str(world.participants[key].id) for key in ('a', 'b'))
    assert len(csv_rows(scoped_events)) == 3
    assert scoped_item.snapshot['counts'] == {'participants': 2, 'sessions': 2, 'events': 3}

    evidence('freeze_and_roster.json', {
        'download_bytes_identical': {str(fmt): len(body) for fmt, body in before.items()},
        'identified_participants': len(rows),
        'identified_statuses': {row['participant_uuid']: row['technical_status'] for row in rows.values()},
        'scoped_counts': scoped_item.snapshot['counts'], 'unmapped_participants': len(unmapped_rows),
        'jsonl_events': len(lines)})


def test_v1_format_is_unchanged_and_never_backfilled(world):
    owner, study = world['owner'], world['study']
    client = sign_in(owner)
    created = client.post('/v1/admin/exports', {'study_id': str(study.id)}, content_type='application/json')
    assert created.status_code == 201
    item = Export.objects.get(pk=created.json()['export_id'])
    assert set(item.snapshot) == {'records', 'builds', 'sessions', 'format_version', 'csv_encoding'}
    assert item.snapshot['format_version'] == '1' and 'participants' not in item.snapshot
    before = {fmt: download(client, item.id, fmt).content for fmt in (None, 'csv', 'metadata')}
    assert [json.loads(line) for line in before[None].decode().splitlines()] == item.snapshot['records']
    assert len(csv_rows(before['csv'])) == len(item.snapshot['records'])
    assert json.loads(before['metadata']) == {key: value for key, value in item.snapshot.items() if key != 'records'}

    study.title = 'Renamed after v1'
    study.save()
    world.add_event(world.sessions['a2'], sequence=1)
    world.participants['a'].code = '777'
    world.participants['a'].save()
    after = {fmt: download(client, item.id, fmt).content for fmt in (None, 'csv', 'metadata')}
    assert after == before
    assert len(Event.objects.filter(session__release__study=study)) == len(item.snapshot['records']) + 1


def test_scope_refuses_unknown_cross_study_duplicate_and_oversized_lists(world):
    owner, study = world['owner'], world['study']
    client = sign_in(owner)
    other_study = Study.objects.create(title='Synthetic R07 other')
    other_build = Build.objects.create(study=other_study, descriptor={}, digest='d' * 64)
    other_release = Release.objects.create(study=other_study, build=other_build, config={}, approved=True)
    other_participant = Participant.objects.create(study=other_study, code='foreign')
    foreign_session = Session.objects.create(participant=other_participant, release=other_release, operation=uuid.uuid4(),
                                             proof_hash='p' * 48, request={}, token_hash='t' * 64,
                                             expires_at=timezone.now() + timedelta(days=1))
    known = str(world.sessions['a1'].id)

    def attempt(session_ids):
        return create_v2(client, study, 'unmapped', 'en', session_ids=session_ids)

    baseline = Export.objects.count()
    unknown = attempt([known, str(uuid.uuid4())])
    assert unknown.status_code == 400 and unknown.json()['code'] == 'export_scope_unknown'
    cross = attempt([known, str(foreign_session.id)])
    assert cross.status_code == 400 and cross.json()['code'] == 'export_scope_unknown'
    duplicate = attempt([known, known])
    assert duplicate.status_code == 400 and duplicate.json()['code'] == 'export_scope'
    malformed = attempt([known, 'not-a-session'])
    assert malformed.status_code == 400 and malformed.json()['code'] == 'export_scope'
    # P03R07R correction (R07 guarded review; requirement U09 §5.3): the declared
    # 20,000-session range is now really usable over HTTP. Old expectation: the
    # 20,001-id body was cut by the general 256 KiB request envelope
    # (`body_limit`). New expectation: the export route has its own bounded
    # envelope whose array bound is exactly the declared 20,000, so the request
    # is refused as `array_limit`; the kernel's scoped validation keeps refusing
    # the same input as `export_limit_sessions`.
    oversized_ids = [str(uuid.uuid4()) for _ in range(exports.MAX_SESSIONS + 1)]
    envelope = attempt(oversized_ids)
    assert envelope.status_code == 422 and envelope.json()['code'] == 'array_limit'
    with pytest.raises(Rejected) as ceiling:
        exports.create_v2_export(owner, study.id, view='unmapped', language='en', session_ids=oversized_ids)
    assert ceiling.value.code == 'export_limit_sessions' and ceiling.value.status == 413
    assert Export.objects.count() == baseline  # no half-ready export from any refusal
    assert Session.objects.filter(release__study=other_study).count() == 1


def test_count_limits_refuse_before_any_row_exists(world):
    """Roster, session and event boundaries above their hard value leave no row."""
    owner = world['owner']
    client = sign_in(owner)
    baseline = Export.objects.count()

    events_study = Study.objects.create(title='Synthetic R07 events')
    events_build = Build.objects.create(study=events_study, descriptor={}, digest='e' * 64)
    events_release = Release.objects.create(study=events_study, build=events_build, config={}, approved=True)
    participant = Participant.objects.create(study=events_study, code='bulk')
    session = Session.objects.create(participant=participant, release=events_release, operation=uuid.uuid4(),
                                     proof_hash='p' * 48, request={}, token_hash='t' * 64,
                                     expires_at=timezone.now() + timedelta(days=1))
    rows = []
    for index in range(exports.MAX_EVENTS + 1):
        event_id, segment_id = uuid.uuid4(), uuid.uuid4()
        rows.append(Event(session=session, event_id=event_id, segment_id=segment_id, sequence=index + 1,
                          envelope={'event_id': str(event_id), 'segment_id': str(segment_id),
                                    'sequence': index + 1, 'payload': {'index': index}}))
    Event.objects.bulk_create(rows, batch_size=2000)
    response = create_v2(client, events_study, 'unmapped', 'en')
    assert response.status_code == 413 and response.json()['code'] == 'export_limit_events'
    assert Export.objects.count() == baseline

    roster_study = Study.objects.create(title='Synthetic R07 roster')
    roster_build = Build.objects.create(study=roster_study, descriptor={}, digest='f' * 64)
    Release.objects.create(study=roster_study, build=roster_build, config={}, approved=True)
    Participant.objects.bulk_create([Participant(study=roster_study, code=f'p{index:05d}')
                                     for index in range(exports.MAX_PARTICIPANTS + 1)], batch_size=2000)
    response = create_v2(client, roster_study, 'identified', 'zh')
    assert response.status_code == 413 and response.json()['code'] == 'export_limit_participants'
    assert Export.objects.count() == baseline

    sessions_study = Study.objects.create(title='Synthetic R07 sessions')
    sessions_build = Build.objects.create(study=sessions_study, descriptor={}, digest='g' * 64)
    sessions_release = Release.objects.create(study=sessions_study, build=sessions_build, config={}, approved=True)
    one_participant = Participant.objects.create(study=sessions_study, code='many')
    sessions = [Session(participant=one_participant, release=sessions_release, operation=uuid.uuid4(),
                        proof_hash='p' * 48, request={}, token_hash='t' * 64,
                        expires_at=timezone.now() + timedelta(days=1))
                for _ in range(exports.MAX_SESSIONS + 1)]
    Session.objects.bulk_create(sessions, batch_size=4000)
    assert Session.objects.filter(release__study=sessions_study).count() == exports.MAX_SESSIONS + 1
    response = create_v2(client, sessions_study, 'unmapped', 'en')
    assert response.status_code == 413 and response.json()['code'] == 'export_limit_sessions'
    assert Export.objects.count() == baseline


def test_preview_carries_counts_and_field_permissions_before_creation(world):
    """The module preview: exact counts for a createable view, no counting
    oracle for a view the actor could not create, and the frozen field rules."""
    from django.test import RequestFactory
    from core import workbench
    owner, study = world['owner'], world['study']
    raw = make_user('p03r07_preview_raw')
    grant(raw, study, 'study.view', 'data.export_raw')
    identity_only = make_user('p03r07_preview_identity')
    grant(identity_only, study, 'study.view', 'identity_mapping.read')

    options = exports.export_options(raw, study)
    identified, unmapped = options['views']
    assert identified['value'] == 'identified' and identified['allowed'] is False and identified['counts'] is None
    assert identified['required_actions'] == ['data.export_raw', 'identity_mapping.read']
    assert unmapped['allowed'] is True and unmapped['counts'] == {'participants': 3, 'sessions': 5, 'events': 4}
    assert unmapped['required_actions'] == ['data.export_raw']
    assert options['limits'] == exports.limits_document()
    assert exports.export_options(identity_only, study)['views'] == []  # no counting without the action

    preview = exports.preview_export(raw, study, view='unmapped', language='en')
    assert preview['counts'] == {'participants': 3, 'sessions': 5, 'events': 4}
    assert [field['key'] for field in preview['field_contract']['participants']] == [
        'study_title', 'study_id', 'participant_uuid', 'session_count', 'complete_session_count',
        'event_count', 'first_session_created_at', 'last_session_created_at', 'technical_status', 'sessions_json']
    assert [field['key'] for field in preview['field_contract']['events']] == [
        'study_title', 'study_id', 'participant_uuid', 'session_id', 'release_id', 'build_id', 'event_id',
        'segment_id', 'sequence', 'event_type', 'schema_id', 'schema_version', 'received_at', 'record_json']
    assert 'participant_code' in [field['key'] for field in
                                  exports.preview_export(owner, study, view='identified', language='zh')['field_contract']['participants']]
    with pytest.raises(Rejected) as refused:
        exports.preview_export(raw, study, view='identified', language='en')
    assert refused.value.code == 'forbidden' and refused.value.status == 403

    request = RequestFactory().get(f'/studies/{study.id}/exports')
    request.user = raw
    context = workbench.exports_module(request, study)
    assert context['exports_options']['views'][1]['counts'] == {'participants': 3, 'sessions': 5, 'events': 4}
    assert context['exports_rows'] == []
    assert sign_in(raw).get(f'/studies/{study.id}/exports').status_code == 200


def test_cell_and_time_boundaries_keep_the_lossless_path(world):
    owner, study = world['owner'], world['study']
    client = sign_in(owner)
    world.add_event(world.sessions['a2'], sequence=1, payload={'blob': 'a' * 40000})

    created = create_v2(client, study, 'unmapped', 'en')
    assert created.status_code == 201
    item = Export.objects.get(pk=created.json()['export_id'])
    with pytest.raises(exports.ExportCellLimit) as info:
        exports.render_csv_bundle(item.snapshot)
    assert info.value.code == 'export_cell_limit' and info.value.status == 413
    assert info.value.detail['jsonl_available'] is True
    assert 'a' * 100 not in json.dumps(info.value.detail)

    lossless = download(client, item.id)
    assert lossless.status_code == 200 and b'a' * 40000 in lossless.content
    assert download(client, item.id, 'metadata').status_code == 200
    refused = download(client, item.id, 'csv')
    assert refused.status_code == 409 and refused.json()['code'] == 'export_zip_required'
    assert b'a' * 40000 not in refused.content and 'Content-Disposition' not in refused
    assert any(row['record']['payload'].get('blob') == 'a' * 40000
               for row in Export.objects.get(pk=item.id).snapshot['events'])

    # The exact character boundary is accepted, one character more is refused,
    # and the JSONL bytes are the same either way.
    wrapper = len(exports.json_cell({'blob': ''}))
    snapshot = dict(item.snapshot)
    snapshot['events'] = [dict(item.snapshot['events'][0])]
    boundary = 'a' * (exports.MAX_CELL_CHARS - wrapper)
    snapshot['events'][0]['record'] = {'blob': boundary}
    assert len(exports.json_cell(snapshot['events'][0]['record'])) == exports.MAX_CELL_CHARS
    assert len(exports.render_events_csv(snapshot)) > 0
    snapshot['events'][0]['record'] = {'blob': boundary + 'a'}
    with pytest.raises(exports.ExportCellLimit):
        exports.render_events_csv(snapshot)

    baseline = Export.objects.count()
    with pytest.raises(Rejected) as timeout:
        exports.create_v2_export(owner, study.id, view='unmapped', language='en', generation_seconds=-1.0)
    assert timeout.value.code == 'export_generation_timeout' and timeout.value.status == 503
    assert Export.objects.count() == baseline
