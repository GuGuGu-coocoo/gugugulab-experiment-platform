"""Workbench information architecture for the researcher GUI (03E).

One module page per research concern (overview, participation, builds,
recruitment, sessions, exports) with a stable sidebar. The legacy study route
keeps working as the default overview and every legacy POST contract is still
accepted, but the module pages own the day-to-day controls.

Server-side permission filtering is the authority for this layer:

* ``study.view`` alone shows policy, counts and release summaries;
* concrete roster identities need ``identity_mapping.read``;
* concrete session rows need ``session.view``; recovery may be issued with
  ``session.recover`` (by UUID when rows are not visible);
* raw export snapshots need ``data.export_raw``;
* forged row queries (code/status/page without the row permission) fail 403
  instead of silently returning filtered data.
"""
import math
import uuid
from urllib.parse import urlencode

from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Value, When
from django.db.models.expressions import RawSQL
from django.utils import timezone

from .access import allowed
from .models import Build, Event, Export, Participant, Release, Session
from .protocol import Rejected, require
from . import exports as export_core
from . import ui

MODULES = ('overview', 'participation', 'builds', 'recruitment', 'sessions', 'exports')
MODULE_LABEL_KEYS = {
    'overview': 'module_overview',
    'participation': 'module_participation',
    'builds': 'module_builds',
    'recruitment': 'module_recruitment',
    'sessions': 'module_sessions',
    'exports': 'module_exports',
}
SESSION_STATUSES = ('not_started', 'active', 'declared_pending', 'complete', 'revoked')
STATUS_LABEL_KEYS = {
    'not_started': 'sessions_status_not_started',
    'active': 'sessions_status_active',
    'declared_pending': 'sessions_status_declared_pending',
    'complete': 'sessions_status_complete',
    'revoked': 'sessions_status_revoked',
}
SESSION_PAGE_SIZE = 20
ROSTER_PAGE_SIZE = 50
MAX_PAGE = 10000


def module_url(study, module):
    if module == 'overview':
        return f'/studies/{study.id}'
    return f'/studies/{study.id}/{module}'


def _page_links(base, params, page, pages, window=5):
    """Bounded numbered pagination links that preserve the active filters."""
    if pages <= 1:
        return []
    start = max(1, page - window)
    end = min(pages, page + window)
    links = []
    for number in range(start, end + 1):
        query = {key: value for key, value in params.items() if value}
        if number > 1:
            query['page'] = str(number)
        links.append({'number': number, 'url': base + ('?' + urlencode(query) if query else ''),
                      'current': number == page})
    return links


def nav_items(request, study):
    """Module links this actor can actually use (unauthorized links are absent)."""
    items = [{'key': 'overview', 'url': module_url(study, 'overview')}]
    for key in ('participation', 'builds', 'recruitment'):
        items.append({'key': key, 'url': module_url(study, key)})
    if allowed(request.user, study, 'session.view') or allowed(request.user, study, 'session.recover'):
        items.append({'key': 'sessions', 'url': module_url(study, 'sessions')})
    if allowed(request.user, study, 'data.export_raw'):
        items.append({'key': 'exports', 'url': module_url(study, 'exports')})
    for item in items:
        item['label'] = ui.tr(ui.lang_of(request), MODULE_LABEL_KEYS[item['key']])
    return items


def timezone_label():
    """The active server timezone plus its current UTC offset, for the page label."""
    name = timezone.get_current_timezone_name()
    offset = timezone.localtime(timezone.now()).strftime('%z')
    return f'{name} (UTC{offset[:3]}:{offset[3:]})' if offset else name


def local_time(value):
    if value is None:
        return ''
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S')


def release_label(release):
    descriptor = release.build.descriptor if isinstance(release.build.descriptor, dict) else {}
    platform = descriptor.get('platform') or '—'
    version = descriptor.get('version') or '—'
    return f'{platform} · {version}'


def session_state(session, received_ids):
    """Python reference for the completion contract expressed in :func:`_state_case`.

    ``received_ids`` is the set of event_ids that really arrived. The declared
    set is exact: extra received events never add to it, duplicates collapse,
    and a revoked session is always ``revoked``.
    """
    if session.revoked:
        return 'revoked'
    if session.completion is None:
        return 'not_started' if not received_ids else 'active'
    declared = set(str(value) for value in session.completion.get('event_ids', []))
    missing = declared - received_ids
    return 'complete' if not missing else 'declared_pending'


def _state_case():
    """The same session-state contract, evaluated by the database.

    ``has_events`` and ``declared_missing`` must already be annotated. The
    correlated ``json_each`` subquery counts declared event IDs without a
    matching row in the event table, so a page request never transfers received
    event IDs into Python; only the aggregate counts do the per-row work.
    """
    return Case(
        When(revoked=True, then=Value('revoked')),
        When(has_events=False, completion__isnull=True, then=Value('not_started')),
        When(completion__isnull=True, then=Value('active')),
        When(declared_missing=0, then=Value('complete')),
        default=Value('declared_pending'))


def _state_annotations():
    """Per-session flags for :func:`_state_case`, computed inside the database."""
    session_table = Session._meta.db_table
    event_table = Event._meta.db_table
    declared_missing = RawSQL(
        f"SELECT COUNT(*) FROM json_each({session_table}.completion, '$.event_ids') AS declared "
        f"WHERE NOT EXISTS (SELECT 1 FROM {event_table} received "
        f"WHERE received.session_id = {session_table}.id "
        f"AND received.event_id = REPLACE(declared.value, '-', ''))",
        [], output_field=IntegerField())
    return {
        'has_events': Exists(Event.objects.filter(session=OuterRef('pk'))),
        'declared_missing': declared_missing,
    }


def _session_states(study):
    """Sessions of one study with their exact state annotated by the database."""
    return (Session.objects.filter(release__study=study)
            .annotate(**_state_annotations())
            .annotate(computed_state=_state_case()))


def _int_param(request, name, default):
    raw = request.GET.get(name)
    if raw in (None, ''):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise Rejected('invalid_request', 400)
    require(1 <= value <= MAX_PAGE, 'invalid_request', 400)
    return value


def sessions_module(request, study):
    """Session rows with exact code search, status filter, stable pagination.

    Row-level parameters are refused for actors without ``session.view``; the
    code filter additionally requires ``identity_mapping.read`` so search cannot
    be used as an identity oracle. The status of every row is computed by the
    database (see :func:`_state_case`), the per-status counts are a separate
    aggregate over the whole study, and only the requested page of session rows
    is materialized.
    """
    rows_visible = allowed(request.user, study, 'session.view')
    mapping_visible = allowed(request.user, study, 'identity_mapping.read')
    recover_allowed = allowed(request.user, study, 'session.recover')
    code = (request.GET.get('code') or '').strip()
    session_raw = (request.GET.get('session') or '').strip()
    status = (request.GET.get('status') or '').strip()
    page_raw = request.GET.get('page')
    page_wanted = page_raw not in (None, '')
    if code or session_raw or status or page_wanted:
        require(rows_visible, 'forbidden', 403)
    lang = ui.lang_of(request)
    sessions = _session_states(study)
    counts = {state: 0 for state in SESSION_STATUSES}
    for state, number in (sessions.values_list('computed_state')
                          .annotate(total=Count('id'))):
        counts[state] = number
    sessions_total = sum(counts.values())
    participants_total = Participant.objects.filter(study=study).count()
    context = {
        'sessions_rows_visible': rows_visible,
        'sessions_mapping_visible': mapping_visible,
        'sessions_recover_allowed': recover_allowed,
        'sessions_timezone': timezone_label(),
        'sessions_counts': counts,
        'sessions_total': sessions_total,
        'sessions_participants_total': participants_total,
        'sessions_code': code,
        'sessions_session': session_raw,
        'sessions_status': status,
        'sessions_status_choices': [
            {'value': value, 'label': ui.tr(lang, STATUS_LABEL_KEYS[value]), 'count': counts[value]}
            for value in SESSION_STATUSES],
        'sessions_rows': [],
        'sessions_page': 1,
        'sessions_pages': 1,
        'sessions_has_filters': bool(code or session_raw or status),
    }
    if not rows_visible:
        return context
    filtered = sessions
    if code:
        require(mapping_visible, 'forbidden', 403)
        filtered = filtered.filter(participant__code=code)
    if session_raw:
        try:
            wanted = uuid.UUID(session_raw)
        except (ValueError, AttributeError):
            raise Rejected('sessions_invalid_uuid', 400)
        filtered = filtered.filter(id=wanted)
    if status:
        require(status in SESSION_STATUSES, 'invalid_request', 400)
        filtered = filtered.filter(computed_state=status)
    total = filtered.count()
    pages = max(1, math.ceil(total / SESSION_PAGE_SIZE))
    page = _int_param(request, 'page', 1)
    if page > pages:
        page = pages
    window = filtered.order_by('created_at', 'id')[(page - 1) * SESSION_PAGE_SIZE: page * SESSION_PAGE_SIZE]
    rows = []
    for session in window:
        state = session.computed_state
        rows.append({'id': session.id, 'state': state,
                     'state_label': ui.tr(lang, STATUS_LABEL_KEYS[state]),
                     'created_local': local_time(session.created_at),
                     'release_label': release_label(session.release),
                     'participant': session.participant,
                     'participant_code': session.participant.code if mapping_visible and session.participant else '',
                     'participant_visible': mapping_visible})
    context.update({'sessions_rows': rows, 'sessions_page': page, 'sessions_pages': pages,
                    'sessions_filtered_total': total,
                    'sessions_page_links': _page_links(module_url(study, 'sessions'),
                                                       {'code': code, 'session': session_raw, 'status': status},
                                                       page, pages)})
    return context


def participation_module(request, study):
    mapping_visible = allowed(request.user, study, 'identity_mapping.read')
    participants = Participant.objects.filter(study=study)
    code = (request.GET.get('code') or '').strip()
    page_raw = request.GET.get('page')
    if code or page_raw not in (None, ''):
        require(mapping_visible, 'forbidden', 403)
    ordered = participants.order_by('code', 'id')
    if code:
        ordered = ordered.filter(code=code)
    total = ordered.count()
    pages = max(1, math.ceil(total / ROSTER_PAGE_SIZE))
    page = _int_param(request, 'page', 1)
    if page > pages:
        page = pages
    rows = []
    if mapping_visible:
        for participant in ordered[(page - 1) * ROSTER_PAGE_SIZE: page * ROSTER_PAGE_SIZE]:
            rows.append({'id': participant.id, 'code': participant.code, 'active': participant.active})
    return {
        'roster_mapping_visible': mapping_visible,
        'roster_rows': rows,
        'roster_total': total,
        'roster_pages': pages,
        'roster_page': page,
        'roster_active': participants.filter(active=True).count(),
        'roster_password_entries': participants.exclude(password_hash='').count(),
        'roster_code': code,
        'roster_page_links': _page_links(module_url(study, 'participation'), {'code': code}, page, pages),
    }


def exports_module(request, study):
    """Fixed snapshots plus the authorization-aware v2 preview.

    Each row names its frozen version/view/language, but its counts and download
    links are only offered while the actor still holds every action frozen with
    that export; an identified export therefore never exposes its roster counts
    to a raw-only reader. The preview block carries accurate counts and
    field-permission text only for the views this actor could really create, so
    the page can explain what an identified or unmapped application would
    contain before anything is written.
    """
    exports = Export.objects.filter(study=study).order_by('-created_at', '-id')[:50]
    rows = []
    for item in exports:
        snapshot = item.snapshot if isinstance(item.snapshot, dict) else {}
        version = str(snapshot.get('format_version') or '1')
        required = export_core.required_actions(item)
        permitted = all(allowed(request.user, study, action) for action in required)
        rows.append({'id': item.id, 'created_local': local_time(item.created_at),
                     'format_version': version,
                     'view': snapshot.get('view') if version == '2' else None,
                     'language': snapshot.get('language') if version == '2' else None,
                     'counts': snapshot.get('counts') if version == '2' and permitted else None,
                     'required_actions': list(required),
                     'permitted': permitted})
    return {'exports_rows': rows, 'exports_options': export_core.export_options(request.user, study)}


def overview_module(request, study):
    counts = {
        'sessions': Session.objects.filter(release__study=study).count(),
        'participants': Participant.objects.filter(study=study).count(),
        'builds': Build.objects.filter(study=study).count(),
        'releases': Release.objects.filter(study=study).count(),
        'exports': Export.objects.filter(study=study).count(),
    }
    context = {'overview_frozen': Release.objects.filter(study=study, approved=True).exists(),
               'overview_counts': counts}
    # The red deletion entry only appears for an actor with the v2
    # ``study.delete`` action; the confirmation block shows the real counts and
    # an export-first link when there is anything to lose, and never a forced
    # backup (R00 §D).
    if allowed(request.user, study, 'study.delete'):
        from . import deletion
        delete_counts = deletion.dependency_counts(study)
        context.update({
            'overview_delete_counts': delete_counts,
            'overview_delete_has_data': deletion.has_study_data(delete_counts),
            'overview_export_url': module_url(study, 'exports')
            if allowed(request.user, study, 'data.export_raw') else '',
        })
    return context


def module_context(request, study, module):
    if module == 'sessions':
        return sessions_module(request, study)
    if module == 'participation':
        return participation_module(request, study)
    if module == 'exports':
        return exports_module(request, study)
    if module == 'overview':
        return overview_module(request, study)
    return {}


def module_required_actions(module):
    if module == 'exports':
        return ('data.export_raw',)
    if module == 'sessions':
        return ('session.view', 'session.recover')
    return ()


def module_allowed(request, study, module):
    """Page-level gate: every module requires study.view plus its own actions."""
    if not allowed(request.user, study, 'study.view'):
        return False
    required = module_required_actions(module)
    if not required:
        return True
    return any(allowed(request.user, study, action) for action in required)
