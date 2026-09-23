"""Transactional domain operations. HTTP ACKs are formed after atomic exit."""
import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import transaction, IntegrityError
from django.utils import timezone
from jsonschema import Draft202012Validator
from .models import Audit, Instance, Participant, RecoveryCode, Session, Event, Release, Study
from .protocol import require, Rejected, uuid_text, equal, validate_tree, MAX_BATCH, MAX_EVENTS, PROTOCOL
from .access import guard, ensure_principal
from .artifacts import require_release_artifact
from .throttle import check
from . import deletion


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def token_for(session):
    return hmac.new(settings.SECRET_KEY.encode(), f'{session.id}:{session.proof_hash}'.encode(), hashlib.sha256).hexdigest()


def context(session):
    release = session.release
    return {'protocol_version': PROTOCOL, 'instance_id': str(Instance.objects.get(pk=1).instance_id), 'study_id': str(release.study_id), 'release_id': str(release.id), 'build_id': str(release.build_id), 'session_id': str(session.id), 'participant_uuid': str(session.participant_id), 'participant_code': session.participant.code, 'config': release.config}


def operation_binding(release, data):
    """Canonical create-operation binding for a server-resolved release.

    The release and build identity always come from the resolved release, so a
    repeated stable-entry operation can be compared against its original stored
    binding even after the study's current release moved. Explicit client
    release/build/expected ids must agree with the resolved release.
    """
    binding = {'operation_id': data['operation_id'], 'instance_id': data['instance_id'],
               'study_id': data['study_id'], 'release_id': str(release.id), 'build_id': str(release.build_id),
               'participant_code': data.get('participant_code')}
    require(binding['instance_id'] == str(Instance.objects.get(pk=1).instance_id), 'wrong_instance')
    require(binding['study_id'] == str(release.study_id), 'wrong_binding')
    require('release_id' not in data or data['release_id'] == str(release.id), 'wrong_binding')
    require('build_id' not in data or data['build_id'] == str(release.build_id), 'wrong_binding')
    require('expected_release_id' not in data or data['expected_release_id'] == str(release.id), 'wrong_binding')
    return binding


def _resolved_binding(release):
    """Canonical admission binding of a server-resolved release."""
    return {'instance_id': str(Instance.objects.get(pk=1).instance_id), 'study_id': str(release.study_id),
            'release_id': str(release.id), 'build_id': str(release.build_id)}


def _request_binding(data):
    """Request-only binding for the un-enumerable refusal path.

    Only server-verifiable values participate: the instance must be this
    instance, and the study/release come from the request exactly as submitted
    (``expected_release_id`` first, then ``release_id``). The build joins only
    when the request carries one; the tombstone still verifies it whenever the
    interface can resolve the release. Returns ``None`` when the request cannot
    name a full binding, so the caller refuses with ``admission_unavailable``.
    """
    instance_id = str(Instance.objects.get(pk=1).instance_id)
    if str(data.get('instance_id') or '') != instance_id:
        return None
    study_id = data.get('study_id')
    release_id = data.get('expected_release_id') or data.get('release_id')
    if not study_id or not release_id:
        return None
    return {'instance_id': instance_id, 'study_id': str(study_id), 'release_id': str(release_id),
            'build_id': str(data['build_id']) if data.get('build_id') else None}


def _verify_request_binding(binding, data):
    """The submitted ids must agree with the resolved binding, when present.

    A retry of the original operation still has to carry the exact original
    instance/study/release/build: a mismatched release or build is not the
    original request and must stay ``admission_unavailable``.
    """
    pairs = (('instance_id', data.get('instance_id')), ('study_id', data.get('study_id')),
             ('release_id', data.get('expected_release_id') or data.get('release_id')),
             ('build_id', data.get('build_id')))
    for key, provided in pairs:
        if provided is not None and str(provided) != str(binding.get(key)):
            return None
    return binding


def _admission_refusal(study_uuid, data, proof, *, operation=None, release=None):
    """One refusal call: prefer the resolved release, fall back to the request."""
    binding = _resolved_binding(release) if release is not None else _request_binding(data)
    if binding is not None:
        binding = _verify_request_binding(binding, data)
    deletion.admission_refusal(study_uuid=study_uuid, proof=proof,
                               operation=operation or data['operation_id'], binding=binding)


def admit(release, data):
    """Admit one create operation for a server-resolved release.

    Everything that decides admission -- the repeated-operation retry check, the
    latest approval/recruitment state and the session write -- shares one
    transaction, and the study row is locked while that state is read. A caller
    that resolved or cached the release earlier therefore cannot admit under a
    recruitment or approval state that a concurrent close already superseded.
    """
    uuid_text(data['operation_id'])
    if 'expected_version' in data:
        require(data['expected_version']==release.build.descriptor.get('version'),'wrong_program_version')
    proof = data['proof']
    require(isinstance(proof, str) and 43 <= len(proof) <= 128, 'invalid_proof')
    binding = operation_binding(release, data)
    with transaction.atomic():
        old = _operation_session(data['operation_id'])
        if old is not None:
            study = Study.objects.filter(pk=old.release.study_id).first()
            if study is not None and not deletion.active_study(study):
                _admission_refusal(study.pk, data, proof, operation=old.operation, release=old.release)
            return _resume(old, data, proof)
        return _create_session(release, data, binding, proof)


def _operation_session(operation_id):
    return Session.objects.select_related('release__build').filter(operation=operation_id).first()


def _resume(old, data, proof):
    """Repeated create operation: the original proof and binding win over any
    newer current-release policy, and a retry never creates a second session."""
    require(hmac.compare_digest(old.proof_hash, digest(proof)) and equal(old.request, operation_binding(old.release, data)), 'operation_conflict', 409)
    require(not old.revoked and old.expires_at > timezone.now(), 'session_inactive', 403)
    return old, token_for(old)


def frozen_mode(release, study):
    """Admission mode of one release.

    A release approved with an explicit ``mode`` admits by that frozen contract
    even after the study's policy changed, so the published public configuration
    and the server rules cannot diverge. A legacy release without one keeps the
    pre-shell rule and follows the study policy at admission time. An unknown
    frozen mode fails closed instead of falling back to a guessed protocol.
    """
    frozen = release.config.get('mode') if isinstance(release.config, dict) else None
    if frozen is None:
        return study.mode
    require(frozen in ('anonymous', 'id', 'password'), 'unsupported_capability', 409)
    return frozen


def _create_session(release, data, binding, proof):
    """Write the first session for an operation under the study row lock."""
    study = Study.objects.select_for_update().get(pk=release.study_id)
    release = Release.objects.select_related('build').get(pk=release.pk)
    # The deletion mark shares this transaction boundary: once the mark
    # committed, no new session may be created, and only the original operation
    # with its exact binding and proof gets the permanent ``study_deleted``.
    if not deletion.active_study(study):
        _admission_refusal(study.pk, data, proof, release=release)
    require(release.approved and study.recruitment == 'open', 'admission_closed', 403)
    require_release_artifact(release)
    mode = frozen_mode(release, study)
    if mode == 'anonymous':
        require(data.get('participant_code') is None, 'unexpected_code')
        participant = Participant.objects.create(study=study)
    else:
        participant = Participant.objects.filter(study=study, code=data.get('participant_code'), active=True).first()
        require(participant is not None, 'admission_denied', 403)
        require(participant.expires_at is None or participant.expires_at > timezone.now(), 'admission_denied', 403)
        if mode == 'password':
            require(check_password(data.get('password', ''), participant.password_hash), 'admission_denied', 403)
        require(Session.objects.filter(participant=participant).count() < study.max_sessions, 'participation_limit', 403)
    session = Session(participant=participant, release=release, operation=data['operation_id'], proof_hash=digest(proof), request=binding, expires_at=timezone.now()+timedelta(days=7))
    token = token_for(session)
    session.token_hash = digest(token)
    session.save()
    return session, token


def _entry_fields(data):
    """Validate the stable-entry fields and return the observed revision."""
    require(all(key in data for key in ('study_id', 'expected_release_id', 'expected_revision')), 'entry_fields')
    revision = data['expected_revision']
    require(type(revision) in (int, float) and revision >= 0 and revision == int(revision), 'entry_fields')
    return int(revision)


def _entry_release(data, study, revision):
    """Resolve the observed binding against a study row read in this transaction."""
    release = study.current_release
    require(release is not None, 'entry_closed', 409)
    require(str(release.id) == str(data['expected_release_id']) and revision == study.revision, 'stale_entry', 409)
    return release


def _uuid_or_none(raw):
    """Parse a submitted UUID for the un-enumerable deletion refusal path."""
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def current_entry_release(data):
    """Resolve the study's explicit current release for a stable-entry request.

    The client sends the release id and publication revision it observed when the
    study entry page loaded. A mismatch fails closed with ``stale_entry`` so a
    page loaded before a researcher switch is never silently retargeted; a study
    without a current release does not open new participation through this path.
    A study under deletion never resolves a release at all.
    """
    revision = _entry_fields(data)
    with transaction.atomic():
        study = Study.objects.select_for_update().select_related('current_release__build').filter(pk=data['study_id']).first()
        require(study is not None, 'entry_closed', 409)
        if not deletion.active_study(study):
            _admission_refusal(study.pk, data, data.get('proof', ''), release=study.current_release)
        return _entry_release(data, study, revision)


def admit_request(data):
    """Resolve a new-session request and admit it in one atomic boundary.

    Resolution precedence:

    1. A repeated create operation is checked against its original proof and
       binding *before* any current-release policy, so a retry after a switch
       returns the original session (or ``operation_conflict``) instead of
       creating a second session or failing stale.
    2. A request carrying the stable-entry binding (``expected_release_id`` and
       ``expected_revision``) is resolved against the study's current release and
       publication revision inside this transaction, so a concurrent switch or a
       close is serializable with the first admission. The binding takes
       precedence over ``release_id`` because the platform bridge injects both
       into the observed application context.
    3. A request carrying only ``release_id`` uses the frozen legacy
       direct-release admission contract; the release is never silently
       redirected to the study's latest release.

    Every route first honours the deletion mark: a deleting/deleted study never
    admits, the original proof gets the permanent ``study_deleted`` and every
    other binding gets the same ``admission_unavailable`` as a random object.
    """
    uuid_text(data['operation_id'])
    proof = data['proof']
    require(isinstance(proof, str) and 43 <= len(proof) <= 128, 'invalid_proof')
    with transaction.atomic():
        old = _operation_session(data['operation_id'])
        if old is not None:
            study = Study.objects.filter(pk=old.release.study_id).first()
            if study is not None and not deletion.active_study(study):
                _admission_refusal(study.pk, data, proof, operation=old.operation, release=old.release)
            return _resume(old, data, proof)
        if 'release_id' in data and not ({'expected_release_id', 'expected_revision'} & set(data)):
            release = Release.objects.select_related('study', 'build').filter(pk=data['release_id']).first()
            if release is None:
                parsed = _uuid_or_none(data.get('study_id'))
                if parsed is not None and deletion.study_is_deleting(parsed):
                    _admission_refusal(parsed, data, proof)
                raise Rejected('admission_unavailable', 403)
            if not deletion.active_study(release.study):
                _admission_refusal(release.study_id, data, proof, release=release)
        else:
            revision = _entry_fields(data)
            study = Study.objects.select_for_update().select_related('current_release__build').filter(pk=data['study_id']).first()
            if study is None:
                parsed = _uuid_or_none(data['study_id'])
                if parsed is not None and deletion.study_is_deleting(parsed):
                    _admission_refusal(parsed, data, proof)
                raise Rejected('admission_unavailable', 403)
            if not deletion.active_study(study):
                _admission_refusal(study.pk, data, proof, release=study.current_release)
            release = _entry_release(data, study, revision)
        return admit(release, data)


def authorize_session(session, token):
    require(bool(token) and hmac.compare_digest(session.token_hash, digest(token)), 'unauthorized', 401)
    require(not session.revoked and session.expires_at > timezone.now(), 'session_inactive', 403)


def validate_event(session, event):
    validate_tree(event)
    required = {'protocol_version','event_id','session_id','segment_id','sequence','event_type','schema_id','schema_version','payload'}
    optional = {'observed_time','trial_id','attempt_id','related_record_id'}
    require(isinstance(event, dict) and required <= event.keys() and event.keys() <= required | optional, 'envelope_fields')
    require(event['protocol_version'] == PROTOCOL and event['session_id'] == str(session.id), 'event_binding')
    for field in ('event_id','session_id','segment_id'):
        uuid_text(event[field])
    require(type(event['sequence']) in (int,float) and event['sequence'] >= 1 and event['sequence'] == int(event['sequence']), 'sequence')
    schemas = session.release.build.descriptor['schemas']
    definition = schemas.get(event['event_type'])
    require(definition is not None, 'unknown_event_type')
    require(event['schema_id'] == definition['id'] and event['schema_version'] == definition['version'], 'schema_binding')
    require(not list(Draft202012Validator(definition['schema']).iter_errors(event['payload'])), 'payload_schema')
    if 'observed_time' in event:
        time = event['observed_time']
        require(isinstance(time, dict) and set(time) == {'value','unit','clock_id','epoch','source'}, 'clock_fields')
        require(type(time['value']) in (int,float) and time['unit'] in ('ms','us','s') and all(isinstance(time[k], str) and time[k] for k in ('clock_id','epoch','source')), 'clock_values')


def receive(session_id, token, batch):
    uuid_text(batch['batch_id'])
    events = batch['events']
    require(isinstance(events,list) and 1 <= len(events) <= MAX_BATCH, 'batch_limit', 413)
    accepted, duplicate = [], []
    try:
        with transaction.atomic():
            session = Session.objects.select_related('release__build').filter(pk=session_id).first()
            if session is None:
                deletion.session_refusal(session_id, token)
            study = Study.objects.select_for_update().filter(pk=session.release.study_id).first()
            if study is None or not deletion.active_study(study):
                deletion.session_refusal(session_id, token)
            session = Session.objects.select_for_update().select_related('release__build').get(pk=session_id)
            authorize_session(session, token)
            require(len({e['event_id'] for e in events}) == len(events), 'duplicate_batch_id')
            for event in events:
                validate_event(session, event)
                if session.completion is not None:
                    require(event['event_id'] in session.completion['event_ids'] and event['segment_id'] in session.completion['segment_ids'], 'completion_closed', 409)
                old = Event.objects.filter(session=session, event_id=event['event_id']).first()
                if old:
                    require(equal(old.envelope, event), 'event_conflict', 409)
                    duplicate.append(event['event_id'])
                else:
                    require(Event.objects.filter(session=session).count() < MAX_EVENTS, 'session_limit', 413)
                    Event.objects.create(session=session, event_id=event['event_id'], segment_id=event['segment_id'], sequence=event['sequence'], envelope=event)
                    accepted.append(event['event_id'])
            instance_id = str(Instance.objects.get(pk=1).instance_id)
    except IntegrityError:
        raise Rejected('identity_conflict', 409)
    return {'protocol_version': PROTOCOL, 'instance_id': instance_id, 'session_id': str(session_id), 'batch_id': batch['batch_id'], 'accepted': accepted, 'duplicate': duplicate}


def completion_status(session):
    if session.completion is None:
        return {'state': 'active', 'task_finished': False}
    received = set(str(e) for e in Event.objects.filter(session=session).values_list('event_id', flat=True))
    missing = sorted(set(session.completion['event_ids']) - received)
    return {'protocol_version': PROTOCOL, 'instance_id': str(Instance.objects.get(pk=1).instance_id), 'session_id': str(session.id), 'state': 'pending' if missing else 'complete', 'task_finished': True, 'missing': missing, 'declaration': session.completion}


def finish(session_id, token, declaration):
    require(set(declaration) == {'event_ids','segment_ids'}, 'completion_fields')
    for key in declaration:
        ids = declaration[key]
        require(isinstance(ids,list) and len(ids) <= MAX_EVENTS and len(set(ids)) == len(ids), 'completion_ids')
        for value in ids:
            uuid_text(value)
    with transaction.atomic():
        session = Session.objects.select_related('release__study').filter(pk=session_id).first()
        if session is None:
            deletion.session_refusal(session_id, token)
        study = Study.objects.select_for_update().filter(pk=session.release.study_id).first()
        if study is None or not deletion.active_study(study):
            deletion.session_refusal(session_id, token)
        session = Session.objects.select_for_update().get(pk=session_id)
        authorize_session(session, token)
        if session.completion is not None:
            require(equal(session.completion, declaration), 'completion_conflict', 409)
        else:
            present = Event.objects.filter(session=session)
            require(all(str(e.event_id) in declaration['event_ids'] and str(e.segment_id) in declaration['segment_ids'] for e in present), 'completion_omits_received', 409)
            session.completion = declaration
            session.save(update_fields=['completion'])
        result = completion_status(session)
    return result


def recover(session_id, proof, permit):
    from .models import RecoveryPermit, Audit
    from .access import guard
    with transaction.atomic():
        session=Session.objects.select_related('release__study').filter(pk=session_id).first()
        if session is None:
            deletion.recovery_refusal(session_id=session_id, proof=proof)
        study=Study.objects.select_for_update().filter(pk=session.release.study_id).first()
        if study is None or not deletion.active_study(study):
            deletion.recovery_refusal(session_id=session_id, proof=proof)
        session=Session.objects.select_for_update().get(pk=session_id)
        ticket=RecoveryPermit.objects.get(session=session,token_hash=digest(permit))
        guard(ticket.issuer,session.release.study,'session.recover')
        require(not ticket.consumed and ticket.expires_at>timezone.now(),'recovery_permit_inactive',403)
        require(hmac.compare_digest(session.proof_hash,digest(proof)) and not session.revoked,'recovery_denied',403)
        session.expires_at=timezone.now()+timedelta(days=7);session.save(update_fields=['expires_at'])
        ticket.consumed=True;ticket.save(update_fields=['consumed'])
        Audit.objects.create(study=session.release.study,actor=ticket.issuer,actor_principal=ensure_principal(ticket.issuer),action='session.recovered',target=str(session.id))
    return {'session_id':str(session.id),'token':token_for(session),'task_finished':session.completion is not None}


RECOVERY_CODE_CAPABILITY = 'recovery_code/v1'
RECOVERY_NAMED_CAPABILITY = 'recovery_named/v1'
# Frozen public-configuration capability for the reusable GEC participation
# shell. A release approved with an explicit mode advertises this capability; a
# shell that does not know it must fail closed instead of guessing a protocol.
SHELL_CAPABILITY = 'gec-shell/v1'
RECOVERY_CODE_DIGITS = 6
RECOVERY_CODE_TTL = timedelta(minutes=5)
RECOVERY_CODE_MAX_ATTEMPTS = 5
RECOVERY_RENEWAL_DAYS = 7
RECOVERY_ISSUE_LIMIT = 10
RECOVERY_REDEEM_CLIENT_LIMIT = 20
RECOVERY_REDEEM_STUDY_LIMIT = 50
RECOVERY_REDEEM_DEVICE_LIMIT = 20
RECOVERY_REDEEM_GLOBAL_LIMIT = 60


def recovery_code_digest(code):
    """Server-secret HMAC of the six digits; the code itself is never stored."""
    return hmac.new(settings.SECRET_KEY.encode(), f'gep-recovery-code:{code}'.encode(), hashlib.sha256).hexdigest()


def _recovery_binding(data):
    """Require the fixed adapter binding every redemption must repeat."""
    require(all(key in data for key in ('instance_id', 'study_id', 'release_id', 'build_id')), 'invalid_binding')
    for key in ('instance_id', 'study_id', 'release_id', 'build_id'):
        uuid_text(data[key])
    return {key: data[key] for key in ('instance_id', 'study_id', 'release_id', 'build_id')}


def _binding_matches(release, binding):
    return (binding['instance_id'] == str(Instance.objects.get(pk=1).instance_id)
            and binding['study_id'] == str(release.study_id)
            and binding['release_id'] == str(release.id)
            and binding['build_id'] == str(release.build_id))


def _redeem_throttle(kind, binding, client_key, proof):
    """Bounded study/device/client/global counters.

    Labels contain only server-side identifiers and a non-reversible digest of
    the submitted proof; the raw code and proof are never part of a key.
    """
    instance_id = str(Instance.objects.get(pk=1).instance_id)
    check(f'{kind}:{instance_id}', limit=RECOVERY_REDEEM_GLOBAL_LIMIT)
    study = Study.objects.filter(pk=binding['study_id']).first()
    check(f'{kind}:{instance_id}:study:{study.pk if study is not None else "unknown"}', limit=RECOVERY_REDEEM_STUDY_LIMIT)
    if client_key:
        check(f'{kind}:{instance_id}:client:{client_key[:64]}', limit=RECOVERY_REDEEM_CLIENT_LIMIT)
    check(f'{kind}:{instance_id}:device:{digest(proof)}', limit=RECOVERY_REDEEM_DEVICE_LIMIT)


def _renew_session(session):
    session.expires_at = timezone.now() + timedelta(days=RECOVERY_RENEWAL_DAYS)
    session.save(update_fields=['expires_at'])


def issue_recovery_code(issuer, session_id):
    """Issue the one live six-digit code for a session.

    Requires the current ``session.recover`` authority (whose ``study.view``
    prerequisite is enforced by :func:`core.access.guard`) on a live account;
    the session must not be revoked. A newer issuance supersedes any earlier
    unconsumed ticket, so exactly one code per session can ever redeem. The raw
    digits are returned to the issuing researcher only and are never persisted.
    """
    with transaction.atomic():
        session = Session.objects.select_related('release__study').filter(pk=session_id).first()
        require(session is not None, 'session_missing', 404)
        study = Study.objects.select_for_update().get(pk=session.release.study_id)
        require(deletion.active_study(study), 'study_deleted', 403)
        session = Session.objects.select_for_update().select_related('release__study').get(pk=session_id)
        actor = get_user_model().objects.filter(pk=getattr(issuer, 'pk', None)).first()
        require(actor is not None, 'forbidden', 403)
        check(f'recovery-code-issue:{Instance.objects.get(pk=1).instance_id}:{study.pk}:{actor.pk}', limit=RECOVERY_ISSUE_LIMIT)
        guard(actor, study, 'session.recover')
        require(not session.revoked, 'not_recoverable', 409)
        RecoveryCode.objects.filter(session=session, consumed=False, superseded=False).update(superseded=True)
        for _ in range(32):
            code = f'{secrets.randbelow(10 ** RECOVERY_CODE_DIGITS):0{RECOVERY_CODE_DIGITS}d}'
            code_hash = recovery_code_digest(code)
            if not RecoveryCode.objects.filter(code_hash=code_hash).exists():
                break
        else:
            raise Rejected('recovery_code_unavailable', 503)
        ticket = RecoveryCode.objects.create(session=session, study=study, release=session.release, issuer=actor,
                                             code_hash=code_hash, expires_at=timezone.now() + RECOVERY_CODE_TTL)
        Audit.objects.create(study=study, actor=actor, actor_principal=ensure_principal(actor), action='recovery.code_issued', target=str(session.id),
                             after={'capability': RECOVERY_CODE_CAPABILITY, 'expires_at': ticket.expires_at.isoformat()})
        return {'capability': RECOVERY_CODE_CAPABILITY, 'code': code, 'session_id': str(session.id),
                'expires_at': ticket.expires_at, 'attempts_allowed': RECOVERY_CODE_MAX_ATTEMPTS}


def _code_redeemable(ticket, binding, proof):
    """All redemption predicates for one locked ticket; any miss denies alike."""
    session = ticket.session
    if ticket.consumed or ticket.superseded or ticket.expires_at <= timezone.now():
        return False
    if ticket.attempts >= RECOVERY_CODE_MAX_ATTEMPTS:
        return False
    if ticket.study_id != session.release.study_id or ticket.release_id != session.release_id:
        return False
    if not _binding_matches(session.release, binding):
        return False
    if session.revoked:
        return False
    if not hmac.compare_digest(session.proof_hash, digest(proof)):
        return False
    issuer = get_user_model().objects.filter(pk=ticket.issuer_id).first()
    if issuer is None:
        return False
    try:
        guard(issuer, session.release.study, 'session.recover')
    except Rejected:
        return False
    return True


def redeem_recovery_code(data, client_key=None):
    """Redeem a six-digit code from the original device.

    The device submits the code, its private proof and the frozen binding; the
    server resolves the session internally, so a public session/participant UUID
    is not required and the code alone can never reveal a session or credential.
    Every failed attempt is persisted even though the call raises, and the
    successful consume is single-writer atomic.
    """
    require(data.get('capability') == RECOVERY_CODE_CAPABILITY, 'unsupported_capability', 409)
    binding = _recovery_binding(data)
    proof = data.get('proof')
    require(isinstance(proof, str) and 43 <= len(proof) <= 128, 'invalid_proof')
    code = data.get('code')
    require(isinstance(code, str) and len(code) == RECOVERY_CODE_DIGITS and code.isdigit(), 'invalid_code')
    _redeem_throttle('recovery-code-redeem', binding, client_key, proof)
    code_hash = recovery_code_digest(code)
    denied, payload = True, None
    with transaction.atomic():
        study = Study.objects.select_for_update().filter(pk=binding['study_id']).first()
        if deletion.study_is_deleting(binding['study_id']) or (study is not None and study.lifecycle != 'active'):
            deletion.recovery_refusal(study_uuid=binding['study_id'], proof=proof, binding=binding)
        ticket = (RecoveryCode.objects.select_for_update().select_related('session__release__study')
                  .filter(code_hash=code_hash).first())
        if ticket is not None:
            if _code_redeemable(ticket, binding, proof):
                session = ticket.session
                _renew_session(session)
                ticket.consumed = True
                ticket.save(update_fields=['consumed'])
                Audit.objects.create(study=ticket.study, actor=ticket.issuer, actor_principal=ensure_principal(ticket.issuer), action='recovery.code_redeemed', target=str(session.id),
                                     after={'capability': RECOVERY_CODE_CAPABILITY})
                denied = False
                payload = {'capability': RECOVERY_CODE_CAPABILITY, 'session_id': str(session.id), 'token': token_for(session),
                           'task_finished': session.completion is not None}
            elif ticket.attempts < RECOVERY_CODE_MAX_ATTEMPTS:
                ticket.attempts += 1
                ticket.last_attempt_at = timezone.now()
                ticket.save(update_fields=['attempts', 'last_attempt_at'])
    if denied:
        raise Rejected('recovery_denied', 403)
    return payload


def _named_redeemable(session, binding, proof, password):
    """Frozen-mode, active-roster and same-device checks for named continuation."""
    release = session.release
    if release.config.get('mode') != 'password':
        return False
    if not _binding_matches(release, binding):
        return False
    if session.revoked:
        return False
    participant = session.participant
    if not participant.active:
        return False
    if participant.expires_at is not None and participant.expires_at <= timezone.now():
        return False
    if not hmac.compare_digest(session.proof_hash, digest(proof)):
        return False
    return check_password(password, participant.password_hash)


def recover_named(data, client_key=None):
    """Same-device named continuation with the frozen roster credentials.

    The server validates the frozen release mode, the matching participant ID,
    the configured password and the original device proof inside one
    transaction. A public participant code or session UUID alone restores
    nothing, and a locally front-locked candidate is refused here: it needs the
    separately issued code or long permit path.
    """
    require(data.get('capability') == RECOVERY_NAMED_CAPABILITY, 'unsupported_capability', 409)
    participant_code = data.get('participant_code')
    require(isinstance(participant_code, str) and 0 < len(participant_code) <= 128, 'invalid_request')
    password = data.get('password')
    require(isinstance(password, str), 'invalid_request')
    proof = data.get('proof')
    require(isinstance(proof, str) and 43 <= len(proof) <= 128, 'invalid_proof')
    require(type(data.get('front_locked', False)) is bool, 'invalid_request')
    binding = _recovery_binding(data)
    _redeem_throttle('recovery-named-redeem', binding, client_key, proof)
    require(not data.get('front_locked', False), 'front_locked', 403)
    denied, payload = True, None
    with transaction.atomic():
        study = Study.objects.select_for_update().filter(pk=binding['study_id']).first()
        if deletion.study_is_deleting(binding['study_id']) or (study is not None and study.lifecycle != 'active'):
            deletion.recovery_refusal(study_uuid=binding['study_id'], proof=proof, binding=binding)
        candidates = (Session.objects.select_for_update().select_related('release__build', 'participant')
                      .filter(participant__study_id=binding['study_id'], participant__code=participant_code)
                      .order_by('-created_at'))
        for session in candidates:
            if _named_redeemable(session, binding, proof, password):
                _renew_session(session)
                Audit.objects.create(study=session.release.study, actor=None, action='recovery.named_redeemed', target=str(session.id),
                                     after={'capability': RECOVERY_NAMED_CAPABILITY})
                denied = False
                payload = {'capability': RECOVERY_NAMED_CAPABILITY, 'session_id': str(session.id), 'token': token_for(session),
                           'task_finished': session.completion is not None}
                break
    if denied:
        raise Rejected('recovery_denied', 403)
    return payload
