"""T30/P0307 server evidence: dashboard modules, session/roster queries, permissions.

Every assertion states an independently specified expectation about the real
database and HTTP behavior: separate module pages behind a stable sidebar,
exact roster-code search including leading zeros, status filtering, deterministic
(created_at, id) pagination, a labelled server timezone, several sessions per
participant, and the split between study.view counts, session.view rows,
identity_mapping.read identities, session.recover issuance and data.export_raw
downloads. Forged row queries and mutating requests must answer 403 and write
nothing.
"""
import io
import json
import re
import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.query import QuerySet
from django.test import Client
from django.utils import timezone
from openpyxl import Workbook

from core.models import (AccountProfile, Build, Event, Export, Grant, Instance, Participant,
                         RecoveryCode, Release, Session, Study)
from core.services import admit, finish, receive

OWNER_PASSWORD = 'synthetic-test-password'
ROW_RE = re.compile(r'data-session-row="([0-9a-f-]{36})"')
MATRIX_ACCOUNT_RE = re.compile(r'data-matrix-account="(\d+)"')
MATRIX_ROW_RE = re.compile(r'data-matrix-row="(\d+)-([0-9a-f-]{36})"')
SECRET_RE = re.compile(r'data-one-time-secret="1".*?<code>(.*?)</code>', re.S)
PREVIEW_ID_RE = re.compile(r'data-preview-id="([0-9a-fA-F-]{36})"')


def materialized_rows(request_callable):
    """Run one request and count the ORM rows Django materializes per model.

    Returns ``(response, instances, tuples)``: ``instances`` counts fully
    materialized model rows, ``tuples`` counts the rows of ``values()`` /
    ``values_list()`` queries. This measures the Python-side row work of a
    request, not its output length: counts and SQL subqueries contribute
    nothing, and event IDs must never appear as materialized event rows.
    """
    from django.db.models.query import ModelIterable

    instances, tuples = {}, {}
    original = QuerySet._fetch_all

    def spy(self):
        was_cached = self._result_cache is not None
        original(self)
        if was_cached:
            return
        target = instances if issubclass(self._iterable_class, ModelIterable) else tuples
        label = self.model._meta.label
        target[label] = target.get(label, 0) + len(self._result_cache)

    with mock.patch.object(QuerySet, '_fetch_all', spy):
        response = request_callable()
    return response, instances, tuples

DESCRIPTOR = {'platform': 'godot_web', 'version': 'v1',
              'schemas': {'exp.rt': {'id': 'rt', 'version': '1', 'schema': {
                  'type': 'object', 'properties': {'rt_ms': {'type': 'number'}}, 'required': ['rt_ms'],
                  'additionalProperties': False}}},
              'codebook': {'rt_ms': {'unit': 'ms', 'source': 'host'}}}


def grant(user, study, *actions, delegable=False):
    for action in actions:
        Grant.objects.create(user=user, study=study, action=action, delegable=delegable)


def make_world(setup, title='Queries synthetic'):
    """A second study inside the fixture instance, with its own web release."""
    owner = setup['owner']
    instance = setup['instance']
    study = Study.objects.create(title=title, mode='id', recruitment='open', max_sessions=50)
    build = Build.objects.create(study=study, descriptor=dict(DESCRIPTOR), digest='a' * 64, package_path='package.zip')
    release = Release.objects.create(study=study, build=build, approved=True,
                                     config={'purpose': 'synthetic', 'mode': 'id'})
    return {'owner': owner, 'instance': instance, 'study': study, 'release': release}


def add_session(world, code, seed='p'):
    data = {'operation_id': str(uuid.uuid4()), 'proof': (seed * 48)[:48],
            'instance_id': str(world['instance'].instance_id), 'study_id': str(world['study'].id),
            'release_id': str(world['release'].id), 'build_id': str(world['release'].build_id),
            'participant_code': code}
    session, token = admit(world['release'], data)
    return session, token


def send_event(session, token, sequence):
    event_id, segment_id = uuid.uuid4(), uuid.uuid4()
    result = receive(session.id, token, {'batch_id': str(uuid.uuid4()), 'events': [{
        'protocol_version': 'gep/1', 'event_id': str(event_id), 'session_id': str(session.id),
        'segment_id': str(segment_id), 'sequence': sequence, 'event_type': 'exp.rt',
        'schema_id': 'rt', 'schema_version': '1', 'payload': {'rt_ms': 100 + sequence}}]})
    assert str(event_id) in result['accepted']
    return event_id, segment_id


def login_client(username, password=OWNER_PASSWORD):
    client = Client()
    assert client.post('/login', {'username': username, 'password': password}).status_code == 302
    return client


def test_dashboard_cards_stable_navigation_and_module_pages(setup):
    study = setup['study']
    owner = setup['owner']
    grant(owner, study, 'study.view', 'study.configure', 'build.upload', 'build.preview',
          'release.approve_pilot', 'recruitment.manage', 'data.export_raw', 'session.recover',
          'member.manage', 'permission.delegate', 'audit.view', 'identity_mapping.read', 'session.view')
    client = login_client('synthetic_owner')

    # Card dashboard with the current account and the stable sidebar.
    home = client.get('/').content.decode()
    assert f'data-study-card="{study.id}"' in home
    assert 'data-nav="1"' in home and 'data-current-account="1"' in home
    assert 'data-nav-link="users"' in home and 'data-nav-link="password"' in home
    assert 'synthetic_owner' in home

    # Each module is its own page with its own marker, not one long mixed page.
    markers = {'participation': 'data-participation-policy="1"', 'builds': 'data-builds-list="1"',
               'recruitment': 'data-publication-form="1"', 'sessions': 'data-sessions="1"',
               'exports': 'data-exports="1"'}
    for module, marker in markers.items():
        page = client.get(f'/studies/{study.id}/{module}')
        assert page.status_code == 200, module
        body = page.content.decode()
        assert marker in body and f'data-module="{module}"' in body
        # The sidebar repeats the module links on every page (stable navigation).
        assert 'data-nav="1"' in body
        assert f'data-nav-link="{module}"' in body
    # The legacy route is the default overview with links to the modules.
    overview = client.get(f'/studies/{study.id}').content.decode()
    assert 'data-module="overview"' in overview and 'data-overview="1"' in overview
    for module in ('participation', 'builds', 'recruitment'):
        assert f'/studies/{study.id}/{module}" data-module-link="{module}"' in overview
    # An unknown module is a 404, never a 500 or a mixed page.
    assert client.get(f'/studies/{study.id}/bogus').status_code == 404


def test_sessions_exact_code_search_status_filter_timezone_and_pagination(setup):
    world = make_world(setup)
    study, owner = world['study'], world['owner']
    grant(owner, study, 'study.view', 'session.view', 'identity_mapping.read', 'session.recover')
    for code in ('001', '002', 'ZZZ'):
        Participant.objects.create(study=study, code=code)
    created, tokens = [], {}
    for index in range(23):
        code = ('001', '002', 'ZZZ')[index % 3]
        session, token = add_session(world, code, seed=chr(ord('a') + index))
        created.append(session)
        tokens[session.id] = token
    by_code = {code: [s for s in created if s.participant.code == code] for code in ('001', '002', 'ZZZ')}

    # Four sessions get explicit non-default server states; everything else stays
    # not started. Expected states are written down here, not read back from code.
    active = by_code['001'][0]
    send_event(active, tokens[active.id], 1)
    revoked = by_code['001'][1]
    revoked.revoked = True
    revoked.save(update_fields=['revoked'])
    pending = by_code['002'][0]
    eid, segid = send_event(pending, tokens[pending.id], 1)
    finish(pending.id, tokens[pending.id],
           {'event_ids': [str(eid), str(uuid.uuid4())], 'segment_ids': [str(segid), str(uuid.uuid4())]})
    complete = by_code['ZZZ'][0]
    eid, segid = send_event(complete, tokens[complete.id], 1)
    finish(complete.id, tokens[complete.id], {'event_ids': [str(eid)], 'segment_ids': [str(segid)]})
    expected = {active.id: 'active', revoked.id: 'revoked', pending.id: 'declared_pending', complete.id: 'complete'}

    client = login_client('synthetic_owner')
    rows_page = client.get(f'/studies/{study.id}/sessions').content.decode()
    assert f'data-sessions-total="{len(created)}"' in rows_page
    assert timezone.get_current_timezone_name() in rows_page
    assert '(UTC' in rows_page
    assert 'data-timezone="' in rows_page
    for label in ('未开始', '进行中', '已声明待收齐', '服务器已收齐', '已撤销'):
        assert label in rows_page
    # No impossible status filter value is accepted.
    assert client.get(f'/studies/{study.id}/sessions?status=finished').status_code == 400

    # Deterministic pagination by (created_at, id): 20 rows per page, no repeats.
    ordered = list(Session.objects.filter(release__study=study).order_by('created_at', 'id'))
    page_one = client.get(f'/studies/{study.id}/sessions').content.decode()
    page_two = client.get(f'/studies/{study.id}/sessions?page=2').content.decode()
    first_ids = [session.id for session in ordered[:20]]
    second_ids = [session.id for session in ordered[20:]]
    for session_id in first_ids:
        assert f'data-session-row="{session_id}"' in page_one
    for session_id in second_ids:
        assert f'data-session-row="{session_id}"' in page_two
        assert f'data-session-row="{session_id}"' not in page_one
    # The same request renders the same rows in the same order (CSRF values differ).
    repeat = client.get(f'/studies/{study.id}/sessions').content.decode()
    assert ROW_RE.findall(repeat) == ROW_RE.findall(page_one)
    combined = page_one + page_two
    for session_id, state in expected.items():
        assert f'data-session-row="{session_id}" data-status="{state}"' in combined
    assert combined.count('data-status="not_started"') == len(created) - 4
    assert combined.count('data-status="active"') == 1
    assert combined.count('data-status="declared_pending"') == 1
    assert combined.count('data-status="complete"') == 1
    assert combined.count('data-status="revoked"') == 1
    # The rendered local time is the server-localized timestamp with its label.
    first_local = timezone.localtime(ordered[0].created_at).strftime('%Y-%m-%d %H:%M:%S')
    assert f'data-local-time="{first_local}"' in page_one

    # Exact code search keeps leading zeros and never matches a prefix.
    exact = client.get(f'/studies/{study.id}/sessions?code=001').content.decode()
    assert exact.count('data-session-row=') == len(by_code['001'])
    assert 'data-participant="001"' in exact
    assert 'data-participant="002"' not in exact and 'data-participant="ZZZ"' not in exact
    assert client.get(f'/studies/{study.id}/sessions?code=00').content.decode().count('data-session-row=') == 0
    # Multiple sessions of the same participant are all visible with their own state.
    assert exact.count('data-participant="001"') == len(by_code['001'])

    # Status filter selects exactly the sessions in that server state.
    revoked_page = client.get(f'/studies/{study.id}/sessions?status=revoked').content.decode()
    assert revoked_page.count('data-session-row=') == 1
    assert f'data-session-row="{revoked.id}" data-status="revoked"' in revoked_page
    complete_page = client.get(f'/studies/{study.id}/sessions?status=complete').content.decode()
    assert complete_page.count('data-session-row=') == 1 and 'data-status="complete"' in complete_page
    pending_page = client.get(f'/studies/{study.id}/sessions?status=declared_pending').content.decode()
    assert pending_page.count('data-session-row=') == 1 and 'data-status="declared_pending"' in pending_page


def test_permission_separation_and_forged_requests(setup):
    world = make_world(setup)
    study = world['study']
    Participant.objects.create(study=study, code='PRIVATE-CODE-77')
    session, _ = add_session(world, 'PRIVATE-CODE-77', seed='q')
    viewer = get_user_model().objects.create_user('queries_viewer', password=OWNER_PASSWORD)
    grant(viewer, study, 'study.view')
    viewer_client = login_client('queries_viewer')

    # study.view alone: counts and policy, never identities or session rows.
    overview = viewer_client.get(f'/studies/{study.id}').content.decode()
    assert 'data-overview-count="sessions"' in overview
    assert 'PRIVATE-CODE-77' not in overview and str(session.id) not in overview
    # The sessions/exports modules are refused without their own action.
    assert viewer_client.get(f'/studies/{study.id}/sessions').status_code == 403
    assert viewer_client.get(f'/studies/{study.id}/exports').status_code == 403
    assert viewer_client.get(f'/studies/{study.id}/sessions?status=complete').status_code == 403
    assert viewer_client.get(f'/studies/{study.id}/sessions?page=2').status_code == 403

    # session.view shows rows but not the identity mapping.
    rows_viewer = get_user_model().objects.create_user('queries_rows', password=OWNER_PASSWORD)
    grant(rows_viewer, study, 'study.view', 'session.view')
    rows_client = login_client('queries_rows')
    rows_page = rows_client.get(f'/studies/{study.id}/sessions').content.decode()
    assert f'data-session-row="{session.id}"' in rows_page
    assert 'data-participant="PRIVATE-CODE-77"' not in rows_page
    assert 'data-participant-hidden="1"' in rows_page
    assert 'data-participant="001"' not in rows_page
    # Code search without identity_mapping.read is refused, not silently empty.
    assert rows_client.get(f'/studies/{study.id}/sessions?code=PRIVATE-CODE-77').status_code == 403
    # A second page is authorized for session.view but clamps to the last real page.
    assert rows_client.get(f'/studies/{study.id}/sessions?page=2').status_code == 200

    # identity_mapping.read alone does not open session rows.
    mapping_user = get_user_model().objects.create_user('queries_mapping', password=OWNER_PASSWORD)
    grant(mapping_user, study, 'study.view', 'identity_mapping.read')
    mapping_client = login_client('queries_mapping')
    assert mapping_client.get(f'/studies/{study.id}/sessions').status_code == 403
    assert mapping_client.get(f'/studies/{study.id}/sessions?code=PRIVATE-CODE-77').status_code == 403

    # session.recover can issue by UUID without rows, RAW or identity mapping.
    recover_user = get_user_model().objects.create_user('queries_recover', password=OWNER_PASSWORD)
    grant(recover_user, study, 'study.view', 'session.recover')
    recover_client = login_client('queries_recover')
    page = recover_client.get(f'/studies/{study.id}/sessions').content.decode()
    assert 'data-recover-uuid-form="1"' in page and 'value="recover_code"' in page
    assert 'data-session-row=' not in page and 'PRIVATE-CODE-77' not in page
    assert recover_client.get(f'/studies/{study.id}/exports').status_code == 403
    assert recover_client.post(f'/studies/{study.id}/sessions',
                               {'op': 'recover_code', 'session_id': str(session.id)}).status_code == 200
    assert RecoveryCode.objects.filter(session=session).count() == 1
    # RAW export cannot be created with session.recover alone.
    assert recover_client.post('/v1/admin/exports', data=json.dumps({'study_id': str(study.id)}),
                               content_type='application/json').status_code == 403
    assert Export.objects.count() == 0

    # session.recover never extends to another study (no mixed-scope recovery).
    other = make_world(setup, title='Queries synthetic B')
    Participant.objects.create(study=other['study'], code='OTHER-CODE')
    other_session, _ = add_session(other, 'OTHER-CODE', seed='r')
    assert recover_client.post(f'/studies/{study.id}/sessions',
                               {'op': 'recover_code', 'session_id': str(other_session.id)}).status_code == 400

    # Forged mutating requests are refused without writes.
    before = (study.mode, study.recruitment, Grant.objects.count())
    assert viewer_client.post(f'/studies/{study.id}', {'op': 'configure', 'mode': 'password', 'max_sessions': '9'},
                              HTTP_ACCEPT='text/html').status_code == 403
    assert viewer_client.post(f'/studies/{study.id}', {'op': 'roster', 'roster': 'LATE-ID'},
                              HTTP_ACCEPT='text/html').status_code == 403
    assert viewer_client.post(f'/studies/{study.id}', {'op': 'recover_code', 'session_id': str(session.id)},
                              HTTP_ACCEPT='text/html').status_code == 403
    assert viewer_client.post(f'/studies/{study.id}/participation',
                              {'op': 'configure', 'mode': 'password', 'max_sessions': '9'},
                              HTTP_ACCEPT='text/html').status_code == 403
    study.refresh_from_db()
    assert (study.mode, study.recruitment, Grant.objects.count()) == before
    assert not Participant.objects.filter(study=study, code='LATE-ID').exists()


def test_roster_query_identity_gate_and_pagination(setup):
    world = make_world(setup)
    study = world['study']
    owner = world['owner']
    grant(owner, study, 'study.view', 'study.configure', 'identity_mapping.read')
    for index in range(1, 56):
        Participant.objects.create(study=study, code=f'{index:03d}')
    client = login_client('synthetic_owner')

    page = client.get(f'/studies/{study.id}/participation').content.decode()
    assert f'data-roster-total="{Participant.objects.filter(study=study).count()}"' in page
    assert page.count('data-roster-row=') == 50
    assert '001' in page
    second = client.get(f'/studies/{study.id}/participation?page=2').content.decode()
    assert second.count('data-roster-row=') == 5
    assert '<td>051</td>' in second and '<td>001</td>' not in second
    exact = client.get(f'/studies/{study.id}/participation?code=001').content.decode()
    assert exact.count('data-roster-row=') == 1 and '<td>001</td>' in exact and '<td>002</td>' not in exact
    assert 'data-roster-empty="1"' in client.get(f'/studies/{study.id}/participation?code=00').content.decode()

    # Without identity_mapping.read the same page shows counts plus a note only.
    user = get_user_model().objects.create_user('queries_roster_viewer', password=OWNER_PASSWORD)
    grant(user, study, 'study.view', 'study.configure')
    viewer = login_client('queries_roster_viewer')
    hidden = viewer.get(f'/studies/{study.id}/participation').content.decode()
    assert f'data-roster-total="{Participant.objects.filter(study=study).count()}"' in hidden
    assert 'data-roster-hidden="1"' in hidden and 'data-roster-row' not in hidden
    assert '<td>001</td>' not in hidden and 'data-roster-row=' not in hidden
    assert viewer.get(f'/studies/{study.id}/participation?code=001').status_code == 403
    assert viewer.get(f'/studies/{study.id}/participation?page=2').status_code == 403
    # No password hash or secret column ever reaches the page.
    for leak in ('password_hash', 'token_hash', 'proof_hash', 'study_code'):
        assert leak not in page and leak not in hidden




def test_exports_module_lists_authorized_snapshots(setup):
    world = make_world(setup)
    study, owner = world['study'], world['owner']
    grant(owner, study, 'study.view', 'data.export_raw')
    client = login_client('synthetic_owner')
    created = client.post('/v1/admin/exports', data=json.dumps({'study_id': str(study.id)}),
                          content_type='application/json')
    assert created.status_code == 201
    export_id = created.json()['export_id']
    page = client.get(f'/studies/{study.id}/exports').content.decode()
    assert f'data-export-row="{export_id}"' in page
    assert f'/v1/admin/exports/{export_id}/download' in page
    # The snapshot list is a module of its own, not mixed into the overview.
    overview = client.get(f'/studies/{study.id}').content.decode()
    assert f'data-export-row="{export_id}"' not in overview
    # Download still re-authorizes: a viewer without data.export_raw is refused.
    viewer = get_user_model().objects.create_user('queries_export_viewer', password=OWNER_PASSWORD)
    grant(viewer, study, 'study.view')
    viewer_client = login_client('queries_export_viewer')
    assert viewer_client.get(f'/v1/admin/exports/{export_id}/download').status_code == 403


def test_session_page_loading_bounded_and_exact_on_large_synthetic_data(setup):
    """A 600-session study still renders one page per request.

    The database computes the per-session state and the per-status counts; no
    received event ID crosses into Python and only 20 session rows are
    materialized per page. The declared-set completion contract stays exact:
    an extra received event does not break completeness and a revoked session
    stays revoked even with a completion.
    """
    world = make_world(setup)
    study, owner, release = world['study'], world['owner'], world['release']
    grant(owner, study, 'study.view', 'session.view', 'identity_mapping.read', 'session.recover')
    participants = Participant.objects.bulk_create([
        Participant(study=study, code=f'P{index:04d}') for index in range(25)])
    now = timezone.now()
    Session.objects.bulk_create([
        Session(participant=participants[index % len(participants)], release=release,
                operation=uuid.uuid4(), proof_hash='p' * 64, request={}, token_hash='t' * 64,
                expires_at=now + timedelta(hours=1)) for index in range(600)])
    ordered = list(Session.objects.filter(release__study=study).order_by('created_at', 'id'))
    assert len(ordered) == 600
    revoked, pending, complete, active = ordered[0], ordered[1], ordered[2], ordered[3]
    received = {session.id: uuid.uuid4() for session in (revoked, pending, complete, active)}
    missing = uuid.uuid4()
    revoked.completion = {'event_ids': [str(received[revoked.id])], 'segment_ids': [str(uuid.uuid4())]}
    revoked.revoked = True
    revoked.save(update_fields=['completion', 'revoked'])
    pending.completion = {'event_ids': [str(received[pending.id]), str(missing)],
                          'segment_ids': [str(uuid.uuid4()), str(uuid.uuid4())]}
    pending.save(update_fields=['completion'])
    complete.completion = {'event_ids': [str(received[complete.id])], 'segment_ids': [str(uuid.uuid4())]}
    complete.save(update_fields=['completion'])
    Event.objects.bulk_create([
        Event(session=session, event_id=received[session.id], segment_id=uuid.uuid4(), sequence=1, envelope={})
        for session in (revoked, pending, complete, active)])
    # An extra event beyond the declared set must not break completeness.
    Event.objects.create(session=complete, event_id=uuid.uuid4(), segment_id=uuid.uuid4(), sequence=2, envelope={})

    # The database state expression agrees with the documented Python contract
    # on every session of the large dataset, not only the special four.
    from core import workbench
    from core.workbench import session_state
    received_sets = {}
    for session_id, event_id in Event.objects.filter(session__in=ordered).values_list('session_id', 'event_id'):
        received_sets.setdefault(session_id, set()).add(str(event_id))
    database_states = {session.id: session.computed_state for session in workbench._session_states(study)}
    assert {session.id: session_state(session, received_sets.get(session.id, set())) for session in ordered} == database_states

    client = login_client('synthetic_owner')
    page_one, rows_one, tuples_one = materialized_rows(lambda: client.get(f'/studies/{study.id}/sessions'))
    body = page_one.content.decode()
    assert page_one.status_code == 200
    assert 'core.Event' not in rows_one and 'core.Event' not in tuples_one, (rows_one, tuples_one)
    assert rows_one.get('core.Session', 0) == 20, rows_one
    assert body.count('data-session-row=') == 20
    assert 'data-sessions-total="600"' in body
    # Exact database counts: 596 not started plus the four written-down states.
    for label, count in (('未开始', 596), ('进行中', 1), ('已声明待收齐', 1),
                         ('服务器已收齐', 1), ('已撤销', 1)):
        assert f'{label}（{count}）' in body, label
    assert f'data-session-row="{revoked.id}" data-status="revoked"' in body
    assert f'data-session-row="{pending.id}" data-status="declared_pending"' in body
    assert f'data-session-row="{complete.id}" data-status="complete"' in body
    assert f'data-session-row="{active.id}" data-status="active"' in body

    # Page two is bounded too, has no repeats and the same request repeats.
    page_two, rows_two, tuples_two = materialized_rows(lambda: client.get(f'/studies/{study.id}/sessions?page=2'))
    assert 'core.Event' not in rows_two and 'core.Event' not in tuples_two, (rows_two, tuples_two)
    assert rows_two.get('core.Session', 0) == 20, rows_two
    ids_one = ROW_RE.findall(body)
    ids_two = ROW_RE.findall(page_two.content.decode())
    assert len(ids_one) == 20 and len(ids_two) == 20 and not set(ids_one) & set(ids_two)
    assert ROW_RE.findall(client.get(f'/studies/{study.id}/sessions').content.decode()) == ids_one

    # Status filtering and exact code search stay bounded and exact.
    filtered, rows_filtered, tuples_filtered = materialized_rows(
        lambda: client.get(f'/studies/{study.id}/sessions?status=declared_pending'))
    assert 'core.Event' not in rows_filtered and 'core.Event' not in tuples_filtered, (rows_filtered, tuples_filtered)
    assert rows_filtered.get('core.Session', 0) == 1, rows_filtered
    assert f'data-session-row="{pending.id}" data-status="declared_pending"' in filtered.content.decode()
    completed, rows_completed, tuples_completed = materialized_rows(
        lambda: client.get(f'/studies/{study.id}/sessions?status=complete'))
    assert rows_completed.get('core.Session', 0) == 1
    assert completed.content.decode().count('data-session-row=') == 1
    searched, rows_searched, tuples_searched = materialized_rows(
        lambda: client.get(f'/studies/{study.id}/sessions?code=P0000'))
    # 24 sessions share the code; only the first page of 20 is materialized.
    assert 'core.Event' not in rows_searched and 'core.Event' not in tuples_searched, (rows_searched, tuples_searched)
    assert rows_searched.get('core.Session', 0) == 20, rows_searched
    assert searched.content.decode().count('data-session-row=') == 20
