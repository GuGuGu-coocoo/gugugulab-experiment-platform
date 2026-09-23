"""Export v1/v2 core: frozen snapshots, permission projection, capacity.

The export contracts live in exactly one module:

* **v1 stays byte-frozen.** ``format_version='1'`` keeps the original snapshot
  shape and the original JSONL/CSV/metadata bytes. It is never back-filled from
  the current database and its creation path is :func:`create_legacy_export`.
* **v2 freezes at creation.** One transaction reads the study title, the roster
  (or the participants of the explicitly selected sessions), the sessions, the
  events and the involved build descriptors, and stores them inside the
  :class:`~core.models.Export` row. Downloads render only from that row, so a
  later rename, a new event or a changed roster never alters an existing export
  byte for byte. ``identified`` freezes names/codes, ``unmapped`` never stores
  a participant code.
* **One permission projection.** ``identified`` needs ``data.export_raw`` and
  ``identity_mapping.read``; ``unmapped`` needs ``data.export_raw`` only. The
  frozen requirement set is re-checked on every create and every download, so a
  revoked grant refuses the whole export instead of degrading its content.
* **Capacity is enforced here and measured independently.** Every hard limit is
  checked before a row exists (an over-limit snapshot leaves no half-ready
  export), and an over-limit spreadsheet cell refuses only the CSV/ZIP
  rendering: the lossless JSONL path stays available.
* **A stored ZIP is never trusted by its name.** The spreadsheet bundle is a
  deterministic function of the frozen snapshot, so a download rebuilds it and
  serves the stored file only when the same opened stream carries exactly those
  bytes. A valid ZIP of another view, language or export can therefore never be
  served through this export's permission check.
"""
import csv
import io
import json
import os
import secrets
import stat
import time
import uuid
import zipfile
from datetime import timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.http import FileResponse, HttpResponse, JsonResponse
from django.utils import timezone

from .access import allowed, guard
from .models import Event, Export, Participant, Release, Session, Study
from .protocol import Rejected, require
from .services import completion_status
from . import deletion

# --- fixed vocabulary -------------------------------------------------------

VIEWS = ('identified', 'unmapped')
LANGUAGES = ('zh', 'en')
TEXT_PREFIX = 'text:'
JSON_PREFIX = 'json:'
# The v2 spreadsheet bundle contains exactly these two members, in this order.
EXPORT_ZIP_MEMBERS = ('participants.csv', 'events.csv')
# Deterministic member timestamps keep repeated downloads byte-identical.
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
CSV_ENCODING = ('utf-8-sig (BOM); RFC 4180 quoting with doubled double quotes; '
                'text/ID cells start with text: and JSON cells with json:; '
                'UUID, numeric and fixed-enum cells stay literal; timestamps are UTC Z '
                'with source microsecond precision')

# --- hard capacity boundaries (only tightened with a Supervisor note) --------
MAX_EVENTS = 10000
MAX_PARTICIPANTS = 10000
MAX_SESSIONS = 20000
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_CSV_TOTAL_BYTES = 64 * 1024 * 1024
# The final compressed ZIP carries the same boundary as the decompressed pair.
MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_CELL_CHARS = 32767
MAX_CELL_BYTES = 131068
MAX_GENERATION_SECONDS = 30.0

# --- frozen field contract --------------------------------------------------

PARTICIPANT_COLUMNS = (
    ('study_title', '研究标题', 'text', False),
    ('study_id', '研究 ID', None, False),
    ('participant_uuid', '被试 UUID', None, False),
    ('participant_code', '名单 ID', 'text', True),
    ('session_count', '会话数', None, False),
    ('complete_session_count', '已完成会话数', None, False),
    ('event_count', '记录数', None, False),
    ('first_session_created_at', '首次会话创建时间', None, False),
    ('last_session_created_at', '最近会话创建时间', None, False),
    ('technical_status', '技术状态', None, False),
    ('sessions_json', '会话列表 JSON', 'json', False),
)
EVENT_COLUMNS = (
    ('study_title', '研究标题', 'text', False),
    ('study_id', '研究 ID', None, False),
    ('participant_uuid', '被试 UUID', None, False),
    ('participant_code', '名单 ID', 'text', True),
    ('session_id', '会话 ID', None, False),
    ('release_id', '发行 ID', None, False),
    ('build_id', '构建 ID', None, False),
    ('event_id', '事件 ID', None, False),
    ('segment_id', '分段 ID', None, False),
    ('sequence', '序号', None, False),
    ('event_type', '事件类型', 'text', False),
    ('schema_id', 'Schema ID', 'text', False),
    ('schema_version', 'Schema 版本', 'text', False),
    ('received_at', '接收时间', None, False),
    ('record_json', '原始记录 JSON', 'json', False),
)
TECHNICAL_STATUSES = ('not_started', 'pending', 'complete')


class ExportCellLimit(Rejected):
    """One CSV cell exceeds the spreadsheet boundary: CSV/ZIP refused only.

    The lossless JSONL path carries the same records without spreadsheet cell
    limits, so the refusal names it instead of truncating anything.
    """

    def __init__(self, *, cells_checked, max_chars, max_bytes):
        super().__init__('export_cell_limit', 413)
        self.detail = {'jsonl_available': True, 'cells_checked': cells_checked,
                       'max_cell_chars': max_chars, 'max_cell_bytes': max_bytes}


class ExportFileFailure(Rejected):
    """The ZIP file step failed: no half package is served, JSONL stays open."""

    def __init__(self):
        super().__init__('export_generation_failed', 503)
        self.detail = {'jsonl_available': True}


class ExportZipLimit(Rejected):
    """The final compressed ZIP exceeds its boundary: JSONL stays available."""

    def __init__(self):
        super().__init__('export_limit_zip', 413)
        self.detail = {'jsonl_available': True, 'max_zip_bytes': MAX_ZIP_BYTES}


def limits_document(scope=None):
    """Frozen capacity boundaries; a requested export adds its frozen ``scope``.

    The full-study scope is ``{'mode': 'study', 'session_ids': []}`` and an
    explicit range is ``{'mode': 'sessions', 'session_ids': [...]}``, so a full
    study, a selected range and an empty range stay distinguishable.
    """
    document = {'max_events': MAX_EVENTS, 'max_participants': MAX_PARTICIPANTS,
                'max_sessions': MAX_SESSIONS, 'max_snapshot_bytes': MAX_SNAPSHOT_BYTES,
                'max_csv_total_bytes': MAX_CSV_TOTAL_BYTES, 'max_zip_bytes': MAX_ZIP_BYTES,
                'max_csv_cell_chars': MAX_CELL_CHARS,
                'max_csv_cell_bytes': MAX_CELL_BYTES, 'max_generation_seconds': MAX_GENERATION_SECONDS}
    if scope is not None:
        document['scope'] = scope
    return document


def scope_document(scope_ids):
    """The frozen selection of one request: full study or the explicit range."""
    return {'mode': 'study' if scope_ids is None else 'sessions',
            'session_ids': [] if scope_ids is None else sorted(scope_ids)}


def export_requirements(version, view):
    """The frozen permission set of one export version/view combination.

    Unknown or missing values fail closed: only an explicit v2 ``unmapped``
    export is readable with ``data.export_raw`` alone.
    """
    if str(version) == '2' and view == 'unmapped':
        return ('data.export_raw',)
    if str(version) != '2':
        return ('data.export_raw',)
    return ('data.export_raw', 'identity_mapping.read')


def required_actions(item):
    """Frozen requirement set of a stored export row."""
    snapshot = item.snapshot if isinstance(item.snapshot, dict) else {}
    return export_requirements(snapshot.get('format_version', '1'), snapshot.get('view'))


def format_version_of(item):
    snapshot = item.snapshot if isinstance(item.snapshot, dict) else {}
    return str(snapshot.get('format_version') or '1')


def field_contract(view):
    """Columns, prefixes and identified-only fields of the current v2 contract."""
    def columns(rows):
        return [{'key': key, 'zh': zh, 'prefix': prefix, 'identified_only': identified_only}
                for key, zh, prefix, identified_only in rows
                if view == 'identified' or not identified_only]
    return {'participants': columns(PARTICIPANT_COLUMNS), 'events': columns(EVENT_COLUMNS),
            'rules': {'text_prefix': TEXT_PREFIX, 'json_prefix': JSON_PREFIX,
                      'empty_text': 'an empty text/ID cell stays empty', 'literal': 'UUID, numeric and fixed-enum cells stay literal',
                      'zh_header': '中文（稳定key）', 'en_header': 'stable key', 'timestamp': 'UTC Z',
                      'csv_encoding': CSV_ENCODING}}


def csv_header(language, rows, view):
    header = []
    for key, zh, _prefix, identified_only in rows:
        if identified_only and view != 'identified':
            continue
        header.append(f'{zh}（{key}）' if language == 'zh' else key)
    return header


# --- small value helpers ----------------------------------------------------

def iso_z(value):
    if value is None:
        return ''
    if timezone.is_naive(value):
        value = timezone.make_aware(value, dt_timezone.utc)
    # Fixed-width microseconds keep the frozen string order exactly chronological
    # inside one second; truncating to seconds would let the UUID tie-break
    # reverse sessions that were really created in a different order.
    return value.astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def text_cell(value):
    """Reversible text/ID prefix; an empty value stays empty, never ``text:``."""
    if value in (None, ''):
        return ''
    return TEXT_PREFIX + str(value)


def json_cell(value):
    return JSON_PREFIX + json.dumps(value, ensure_ascii=False, allow_nan=False)


def snapshot_utf8_bytes(snapshot):
    return len(json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _check_deadline(deadline):
    if time.monotonic() > deadline:
        raise Rejected('export_generation_timeout', 503)


# --- scope and counting (permission first, then counts) ---------------------

def _session_scope(study, scope_ids):
    """Fresh, unevaluated session queryset so large scopes stay SQL subqueries."""
    queryset = Session.objects.filter(release__study=study)
    if scope_ids is not None:
        queryset = queryset.filter(id__in=scope_ids)
    return queryset


def _validated_session_scope(study, session_ids):
    """Explicit session range: bounded, complete, never silently narrowed.

    Unknown ids, ids of another study, duplicates and malformed values are
    refused so a scope can never omit a requested session by accident.
    """
    if session_ids is None:
        return None
    require(isinstance(session_ids, list), 'export_scope', 400)
    require(len(session_ids) <= MAX_SESSIONS, 'export_limit_sessions', 413)
    for value in session_ids:
        require(isinstance(value, str), 'export_scope', 400)
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError, TypeError):
            raise Rejected('export_scope', 400)
        require(str(parsed) == value, 'export_scope', 400)
    require(len(set(session_ids)) == len(session_ids), 'export_scope', 400)
    found = set(str(value) for value in
                Session.objects.filter(release__study=study, id__in=session_ids).values_list('id', flat=True))
    require(found == set(session_ids), 'export_scope_unknown', 400)
    return list(session_ids)


def _scope_queryset_participants(study, scope_ids, view):
    """Roster scope: identified full-study keeps every participant, including
    non-participants; a session scope and the unmapped view keep only the
    participants who really have a session in scope."""
    participants = Participant.objects.filter(study=study)
    if view == 'unmapped' or scope_ids is not None:
        participants = participants.filter(session__in=_session_scope(study, scope_ids))
    return participants.distinct()


def _scope_counts(study, scope_ids, view):
    """Counts after the permission check; the caller fixes them in metadata."""
    participants = _scope_queryset_participants(study, scope_ids, view).count()
    sessions = _session_scope(study, scope_ids).count()
    events = Event.objects.filter(session__in=_session_scope(study, scope_ids)).count()
    return {'participants': participants, 'sessions': sessions, 'events': events}


def _enforce_counts(counts):
    require(counts['participants'] <= MAX_PARTICIPANTS, 'export_limit_participants', 413)
    require(counts['sessions'] <= MAX_SESSIONS, 'export_limit_sessions', 413)
    require(counts['events'] <= MAX_EVENTS, 'export_limit_events', 413)


# --- creation ---------------------------------------------------------------

def create_legacy_export(user, data):
    """The unchanged v1 creation path: records only, no roster, no title."""
    with transaction.atomic():
        study = Study.objects.select_for_update().filter(pk=data['study_id']).first()
        if study is None or not deletion.active_study(study):
            deletion.study_unavailable()
        guard(user, study, 'data.export_raw')
        require(Event.objects.filter(session__release__study=study).count() <= MAX_EVENTS, 'export_limit', 413)
        records = []
        builds = {}
        statuses = {}
        for event in Event.objects.filter(session__release__study=study).select_related('session__release__build').order_by('id'):
            release = event.session.release
            builds[str(release.build_id)] = release.build.descriptor
            statuses[str(event.session_id)] = completion_status(event.session)
            records.append({'study_id': str(study.id), 'release_id': str(release.id),
                            'build_id': str(release.build_id), 'record': event.envelope})
        item = Export.objects.create(study=study, snapshot={
            'records': records, 'builds': builds, 'sessions': statuses, 'format_version': '1',
            'csv_encoding': 'record_json begins with json: followed by a lossless JSON value; remove prefix then parse JSON'})
    return item


@transaction.atomic
def create_v2_export(user, study_id, *, view, language, session_ids=None,
                     generation_seconds=MAX_GENERATION_SECONDS):
    """Freeze one v2 export in a single transaction; nothing half-ready is kept.

    The permission check comes first, the scope is validated completely, the
    counts are fixed inside the same transaction as the frozen rows, and a
    snapshot over its byte boundary is refused before the row exists.
    """
    require(view in VIEWS, 'export_view', 400)
    require(language in LANGUAGES, 'export_language', 400)
    study = Study.objects.select_for_update().filter(pk=study_id).first()
    # The deletion mark shares this transaction boundary: an export application
    # that commits first is cleaned up with the study, and one that arrives
    # after the mark gets the same 404 as a random study UUID.
    if study is None or not deletion.active_study(study):
        deletion.study_unavailable()
    guard(user, study, 'data.export_raw')
    if view == 'identified':
        guard(user, study, 'identity_mapping.read')
    deadline = time.monotonic() + float(generation_seconds)
    _check_deadline(deadline)
    scope_ids = _validated_session_scope(study, session_ids)
    counts = _scope_counts(study, scope_ids, view)
    _enforce_counts(counts)

    received = {}
    for session_id, event_id in (Event.objects.filter(session__in=_session_scope(study, scope_ids))
                                 .values_list('session_id', 'event_id')):
        received.setdefault(session_id, set()).add(str(event_id))

    session_rows = []
    for session in (_session_scope(study, scope_ids).select_related('release')
                    .order_by('created_at', 'id').iterator(chunk_size=1000)):
        _check_deadline(deadline)
        arrived = received.get(session.id, set())
        declared = set(str(value) for value in (session.completion or {}).get('event_ids', []))
        complete = session.completion is not None and not (declared - arrived)
        row = {'id': str(session.id), 'participant_uuid': str(session.participant_id),
               'release_id': str(session.release_id), 'build_id': str(session.release.build_id),
               'created_at': iso_z(session.created_at), 'complete': complete, 'event_count': len(arrived)}
        session_rows.append(row)

    participant_rows = []
    for participant in _scope_queryset_participants(study, scope_ids, view).order_by('id'):
        _check_deadline(deadline)
        participant_rows.append({'uuid': str(participant.id),
                                 'code': participant.code if view == 'identified' else None})

    event_rows = []
    for event in (Event.objects.filter(session__in=_session_scope(study, scope_ids))
                  .select_related('session__release')
                  .order_by('session__participant_id', 'session_id', 'segment_id', 'sequence', 'event_id')
                  .iterator(chunk_size=1000)):
        _check_deadline(deadline)
        envelope = event.envelope if isinstance(event.envelope, dict) else {}
        event_rows.append({
            'participant_uuid': str(event.session.participant_id), 'session_id': str(event.session_id),
            'release_id': str(event.session.release_id), 'build_id': str(event.session.release.build_id),
            'event_id': str(event.event_id), 'segment_id': str(event.segment_id), 'sequence': event.sequence,
            'event_type': envelope.get('event_type'), 'schema_id': envelope.get('schema_id'),
            'schema_version': envelope.get('schema_version'), 'received_at': iso_z(event.received_at),
            'record': event.envelope})

    builds = {}
    for release in (Release.objects.filter(session__in=_session_scope(study, scope_ids))
                    .select_related('build').distinct()):
        builds[str(release.id)] = release.build.descriptor

    frozen_counts = {'participants': len(participant_rows), 'sessions': len(session_rows),
                     'events': len(event_rows)}
    require(frozen_counts == counts, 'export_conflict', 409)
    snapshot = {
        'format_version': '2', 'view': view, 'language': language, 'title': study.title,
        'study_id': str(study.id), 'created_at': iso_z(timezone.now()),
        'participants': participant_rows, 'sessions': session_rows, 'events': event_rows,
        'builds': builds, 'counts': frozen_counts, 'limits': limits_document(scope_document(scope_ids)),
        'field_contract': field_contract(view), 'csv_encoding': CSV_ENCODING,
        'required_actions': list(export_requirements('2', view))}
    snapshot_bytes = snapshot_utf8_bytes(snapshot)
    # The deadline covers the final serialization and the persistence step: a
    # crossing here still raises inside the atomic block, so the just-written
    # row (if any) rolls back and no READY export survives an over-budget run.
    _check_deadline(deadline)
    require(snapshot_bytes <= MAX_SNAPSHOT_BYTES, 'export_limit_snapshot', 413)
    item = Export.objects.create(study=study, snapshot=snapshot)
    _check_deadline(deadline)
    return item


# --- preview ----------------------------------------------------------------

def preview_export(user, study, *, view, language, session_ids=None):
    """Counts, limits and field permissions for one requested v2 export."""
    require(view in VIEWS, 'export_view', 400)
    require(language in LANGUAGES, 'export_language', 400)
    guard(user, study, 'data.export_raw')
    if view == 'identified':
        guard(user, study, 'identity_mapping.read')
    scope_ids = _validated_session_scope(study, session_ids)
    counts = _scope_counts(study, scope_ids, view)
    return {'view': view, 'language': language, 'counts': counts,
            'limits': limits_document(scope_document(scope_ids)),
            'field_contract': field_contract(view),
            'required_actions': list(export_requirements('2', view))}


def export_options(user, study):
    """Authorization-aware options for the export module preview.

    Counts are only computed for a view this actor could really create, so the
    preview can never become a counting oracle for an actor without the action.
    ``default_view`` names the view the form preselects: the identified view
    only with its identity action, otherwise the unmapped view. A hidden
    identified option must never steal the selection from the only option a
    raw-only reader may create.
    """
    options = {'limits': limits_document(), 'languages': list(LANGUAGES), 'views': [],
               'unmapped_only': True, 'default_view': None}
    if not allowed(user, study, 'data.export_raw'):
        return options
    identity = allowed(user, study, 'identity_mapping.read')
    options['unmapped_only'] = not identity
    options['default_view'] = 'identified' if identity else 'unmapped'
    full_roster = _scope_queryset_participants(study, None, 'identified')
    scoped_roster = _scope_queryset_participants(study, None, 'unmapped')
    sessions = _session_scope(study, None)
    events = Event.objects.filter(session__in=_session_scope(study, None))
    for view in VIEWS:
        view_allowed = identity if view == 'identified' else True
        counts = None
        if view_allowed:
            counts = {'participants': full_roster.count() if view == 'identified' else scoped_roster.count(),
                      'sessions': sessions.count(), 'events': events.count()}
        options['views'].append({'value': view, 'allowed': view_allowed, 'counts': counts,
                                 'required_actions': list(export_requirements('2', view))})
    return options


# --- rendering --------------------------------------------------------------

def metadata_document(item):
    """v2 metadata is the fixed whitelist; v1 keeps the legacy passthrough."""
    snapshot = item.snapshot if isinstance(item.snapshot, dict) else {}
    if format_version_of(item) != '2':
        return {key: value for key, value in snapshot.items() if key != 'records'}
    return {'id': str(item.id), 'format_version': '2', 'view': snapshot.get('view'),
            'language': snapshot.get('language'), 'study_id': snapshot.get('study_id'),
            'title': snapshot.get('title'), 'created_at': snapshot.get('created_at'),
            'counts': snapshot.get('counts'), 'limits': snapshot.get('limits'),
            'field_contract': snapshot.get('field_contract'), 'csv_encoding': snapshot.get('csv_encoding')}


def _selected(row, columns, view):
    """Keep only the columns this view really renders, in the frozen order."""
    return [cell for cell, (_key, _zh, _prefix, identified_only) in zip(row, columns)
            if view == 'identified' or not identified_only]


def _participant_csv_rows(snapshot):
    language = snapshot.get('language', 'en')
    view = snapshot.get('view')
    grouped = {}
    for row in snapshot.get('sessions') or []:
        grouped.setdefault(row['participant_uuid'], []).append(row)
    yield csv_header(language, PARTICIPANT_COLUMNS, view)
    for participant in snapshot.get('participants') or []:
        own = sorted(grouped.get(participant['uuid'], ()), key=lambda row: (row['created_at'], row['id']))
        events = sum(row['event_count'] for row in own)
        complete = sum(1 for row in own if row['complete'])
        if not own:
            status = 'not_started'
        elif complete == len(own):
            status = 'complete'
        else:
            status = 'pending'
        # sessions_json carries exactly the frozen session contract keys, never
        # the internal participant link.
        session_json = [{key: row[key] for key in
                         ('id', 'release_id', 'build_id', 'created_at', 'complete', 'event_count')}
                        for row in own]
        row = [text_cell(snapshot.get('title')), snapshot.get('study_id'), participant['uuid'],
               text_cell(participant.get('code')), str(len(own)), str(complete), str(events),
               own[0]['created_at'] if own else '', own[-1]['created_at'] if own else '', status,
               json_cell(session_json)]
        yield _selected(row, PARTICIPANT_COLUMNS, view)


def _event_csv_rows(snapshot):
    language = snapshot.get('language', 'en')
    view = snapshot.get('view')
    codes = {row['uuid']: row.get('code') for row in snapshot.get('participants') or []}
    yield csv_header(language, EVENT_COLUMNS, view)
    for event in snapshot.get('events') or []:
        row = [text_cell(snapshot.get('title')), snapshot.get('study_id'), event['participant_uuid'],
               text_cell(codes.get(event['participant_uuid'])), event['session_id'], event['release_id'],
               event['build_id'], event['event_id'], event['segment_id'], str(event['sequence']),
               text_cell(event.get('event_type')), text_cell(event.get('schema_id')),
               text_cell(event.get('schema_version')), event['received_at'], json_cell(event.get('record'))]
        yield _selected(row, EVENT_COLUMNS, view)


def _csv_bytes(rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    checked = 0
    for row in rows:
        for cell in row:
            checked += 1
            if len(cell) > MAX_CELL_CHARS or len(cell.encode('utf-8')) > MAX_CELL_BYTES:
                raise ExportCellLimit(cells_checked=checked, max_chars=MAX_CELL_CHARS,
                                      max_bytes=MAX_CELL_BYTES)
        writer.writerow(row)
    return output.getvalue().encode('utf-8-sig')


def render_participants_csv(snapshot):
    return _csv_bytes(_participant_csv_rows(snapshot))


def render_events_csv(snapshot):
    return _csv_bytes(_event_csv_rows(snapshot))


def csv_rows_for(snapshot):
    """The two CSV row streams (participants, events) exactly as rendered.

    Each stream starts with its header; the capacity tool measures the largest
    cell on these real rows instead of a second implementation.
    """
    return _participant_csv_rows(snapshot), _event_csv_rows(snapshot)


def render_csv_bundle(snapshot):
    """ZIP member bytes (participants.csv, events.csv) with the total boundary.

    Used by the ZIP delivery step; the cell boundary refuses CSV/ZIP only and
    never touches the JSONL path.
    """
    participants = render_participants_csv(snapshot)
    events = render_events_csv(snapshot)
    require(len(participants) + len(events) <= MAX_CSV_TOTAL_BYTES, 'export_limit_csv_total', 413)
    return participants, events


def export_spool_root():
    """Private server-owned spool for the frozen ZIP delivery (created lazily).

    A symlinked data or spool component, a non-directory and any filesystem
    setup error are refused as the documented file failure instead of being
    followed or half-created, so nothing outside the intended tree is written.
    """
    base = Path(settings.DATA_DIR)
    root = base / 'exports'
    if base.is_symlink() or root.is_symlink():
        raise ExportFileFailure()
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise ExportFileFailure()
    if root.is_symlink() or not root.is_dir():
        raise ExportFileFailure()
    return root


def export_zip_path(item):
    """The one READY path of one export: the file name is its immutable UUID."""
    return export_spool_root() / f'{item.id}.zip'


def _zip_info(name):
    info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def build_export_zip(snapshot, deadline):
    """One deterministic ZIP with exactly the two CSV members.

    Serialization, compression and the final deadline check share the same
    generation budget as the CSV rendering, so an over-budget compression is
    refused before any file is published. The fixed member order, fixed member
    metadata and fixed timestamps make the bytes a deterministic function of the
    frozen snapshot, which is what lets a stored file be verified against the
    current export instead of trusted by its name.
    """
    participants, events = render_csv_bundle(snapshot)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in zip(EXPORT_ZIP_MEMBERS, (participants, events)):
            _check_deadline(deadline)
            archive.writestr(_zip_info(name), payload)
        _check_deadline(deadline)
    return output.getvalue()


def _store_export_zip(root, path, payload):
    """Publish one complete ZIP through a temp file and one atomic rename.

    A failure while writing, flushing, fsyncing or renaming leaves neither a
    partial file at the final path nor a temp file behind, so the caller can
    only ever serve a complete package. The returned ``(device, inode)`` names
    the published file, so a later cleanup can never remove a file that a
    concurrent request has already replaced with its own complete package.
    """
    temp = root / f'.{path.stem}.{secrets.token_hex(8)}.tmp'
    identity = None
    try:
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            published = os.fstat(stream.fileno())
            identity = (published.st_dev, published.st_ino)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    return identity


def _open_verified_cache(path, payload, deadline):
    """Open the stored ZIP once and serve only those exact verified bytes.

    The deterministic rebuild of the frozen snapshot is the expected package: a
    stored file is READY only when the same opened stream carries exactly those
    bytes, so a valid ZIP of another view, language or export can never be
    served through this export's permission check. A missing, symlinked,
    oversized or different file is an unknown state that is never served. The
    declared size is bounded before any read and the deadline covers the
    comparison. Returns an open stream positioned at 0, or ``None``.
    """
    if path.is_symlink() or not path.is_file():
        return None
    if len(payload) > MAX_ZIP_BYTES:
        return None
    try:
        if path.stat().st_size != len(payload):
            return None
    except OSError:
        return None
    _check_deadline(deadline)
    stream = None
    try:
        stream = open(path, 'rb')
        if stream.read(len(payload) + 1) != payload:
            stream.close()
            return None
        _check_deadline(deadline)
        stream.seek(0)
        return stream
    except Rejected:
        if stream is not None:
            stream.close()
        raise
    except OSError:
        if stream is not None:
            stream.close()
        return None


def _discard_export_zip(path, identity):
    """Remove only the exact file this request published, never a successor.

    A crossing deadline discards the just-committed package, but a concurrent
    request may already have replaced it with the same complete bytes; the
    ``(device, inode)`` identity check leaves that file (and any open download
    of it) untouched.
    """
    try:
        if identity is not None:
            current = path.stat()
            if (current.st_dev, current.st_ino) != identity:
                return
        path.unlink()
    except OSError:
        pass


def render_zip_download(item, *, generation_seconds=MAX_GENERATION_SECONDS):
    """The v2 spreadsheet bundle: exactly participants.csv and events.csv.

    The expected package is deterministically rebuilt from the frozen snapshot;
    a stored file is served only when the same opened stream carries exactly
    those bytes, so no cached ZIP can cross this export's frozen view, language
    or permission check. A missing or different file is replaced through one
    atomic rename, and a valid stored file is left alone, so concurrent
    same-export requests can neither delete a successful download nor return
    mixed bytes. The deadline covers serialization, compression, cache
    comparison and the file commit; the final compressed ZIP is bounded like the
    decompressed CSV pair. Any filesystem setup/open/write/flush/rename failure
    refuses only this download: the lossless JSONL path stays open. The cache
    check and the publish run inside :func:`core.deletion.live_export_lock`, so
    the deletion job and the generation path serialize on the same database
    write lock and no file is published after the deletion committed.
    """
    snapshot = item.snapshot if isinstance(item.snapshot, dict) else {}
    require(format_version_of(item) == '2', 'export_format', 400)
    deadline = time.monotonic() + float(generation_seconds)
    path = export_zip_path(item)
    payload = build_export_zip(snapshot, deadline)
    if len(payload) > MAX_ZIP_BYTES:
        raise ExportZipLimit()
    _check_deadline(deadline)
    # The cache check and any file publish happen inside the cross-process
    # generation boundary: the database write lock is held from before the
    # liveness read until the file is committed, so a concurrent cleanup either
    # already removed the row (this request refuses and publishes nothing) or
    # runs after the commit and removes the just-published file by ownership.
    identity = None
    with deletion.live_export_lock(item.pk):
        stream = _open_verified_cache(path, payload, deadline)
        if stream is not None:
            return FileResponse(stream, as_attachment=True, filename=f'{item.id}.zip',
                                content_type='application/zip')
        try:
            identity = _store_export_zip(path.parent, path, payload)
        except OSError:
            raise ExportFileFailure()
    try:
        _check_deadline(deadline)
    except Rejected:
        _discard_export_zip(path, identity)
        raise
    # The served bytes are exactly the bytes this request built and committed;
    # no unchecked path is re-opened after validation.
    return FileResponse(io.BytesIO(payload), as_attachment=True, filename=f'{item.id}.zip',
                        content_type='application/zip')


def _jsonl_line(snapshot, event, code):
    line = {'format_version': '2', 'view': snapshot.get('view'), 'study_id': snapshot.get('study_id'),
            'title': snapshot.get('title'), 'participant_uuid': event['participant_uuid'],
            'session_id': event['session_id'], 'release_id': event['release_id'],
            'build_id': event['build_id'], 'event_id': event['event_id'],
            'segment_id': event['segment_id'], 'sequence': event['sequence'],
            'event_type': event.get('event_type'), 'schema_id': event.get('schema_id'),
            'schema_version': event.get('schema_version'), 'received_at': event['received_at'],
            'record': event.get('record')}
    if snapshot.get('view') == 'identified':
        line['participant_code'] = code
    return json.dumps(line, ensure_ascii=False, allow_nan=False) + '\n'


def render_jsonl(snapshot):
    """Lossless JSONL: every frozen envelope, never a spreadsheet cell limit."""
    codes = {row['uuid']: row.get('code') for row in snapshot.get('participants') or []}
    return ''.join(_jsonl_line(snapshot, event, codes.get(event['participant_uuid']))
                   for event in snapshot.get('events') or []).encode('utf-8')


def render_download(item, fmt):
    """The frozen bytes of one export format; v1 keeps the original rendering."""
    snapshot = item.snapshot if isinstance(item.snapshot, dict) else {}
    if format_version_of(item) != '2':
        # v1 has no two-CSV bundle: an explicit zip request fails closed instead
        # of silently returning the legacy JSONL under the wrong format.
        if fmt == 'zip':
            raise Rejected('export_format', 400)
        if fmt == 'metadata':
            return JsonResponse({key: value for key, value in snapshot.items() if key != 'records'})
        if fmt == 'csv':
            output = io.StringIO(newline='')
            writer = csv.writer(output)
            writer.writerow(['study_id', 'release_id', 'build_id', 'record_json'])
            for row in snapshot['records']:
                writer.writerow([row['study_id'], row['release_id'], row['build_id'],
                                 'json:' + json.dumps(row['record'], ensure_ascii=False, allow_nan=False)])
            return HttpResponse(output.getvalue().encode('utf-8-sig'), content_type='text/csv; charset=utf-8')
        return HttpResponse(''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n'
                                    for row in snapshot['records']),
                            content_type='application/x-ndjson')
    if fmt == 'metadata':
        return JsonResponse(metadata_document(item))
    if fmt == 'jsonl':
        return HttpResponse(render_jsonl(snapshot), content_type='application/x-ndjson')
    if fmt == 'zip':
        return render_zip_download(item)
    if fmt == 'csv':
        # A v2 CSV is delivered as the ZIP bundle (participants.csv + events.csv)
        # with the metadata beside it; a single-file CSV would be a half package.
        raise Rejected('export_zip_required', 409)
    raise Rejected('export_format', 400)
