"""Transactional domain operations. HTTP ACKs are formed after atomic exit."""
import hashlib
import hmac
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import transaction, IntegrityError
from django.utils import timezone
from jsonschema import Draft202012Validator
from .models import Instance, Participant, Session, Event
from .protocol import require, Rejected, uuid_text, equal, validate_tree, MAX_BATCH, MAX_EVENTS, PROTOCOL


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def token_for(session):
    return hmac.new(settings.SECRET_KEY.encode(), f'{session.id}:{session.proof_hash}'.encode(), hashlib.sha256).hexdigest()


def context(session):
    release = session.release
    return {'protocol_version': PROTOCOL, 'instance_id': str(Instance.objects.get(pk=1).instance_id), 'study_id': str(release.study_id), 'release_id': str(release.id), 'build_id': str(release.build_id), 'session_id': str(session.id), 'participant_uuid': str(session.participant_id), 'participant_code': session.participant.code, 'config': release.config}


def admit(release, data):
    uuid_text(data['operation_id'])
    proof = data['proof']
    require(isinstance(proof, str) and 43 <= len(proof) <= 128, 'invalid_proof')
    binding = {k: data[k] for k in ('operation_id', 'instance_id', 'study_id', 'release_id', 'build_id')}
    binding['participant_code'] = data.get('participant_code')
    require(binding['instance_id'] == str(Instance.objects.get(pk=1).instance_id), 'wrong_instance')
    require(binding['study_id'] == str(release.study_id) and binding['release_id'] == str(release.id) and binding['build_id'] == str(release.build_id), 'wrong_binding')
    with transaction.atomic():
        old = Session.objects.filter(operation=data['operation_id']).first()
        if old:
            require(hmac.compare_digest(old.proof_hash, digest(proof)) and equal(old.request, binding), 'operation_conflict', 409)
            require(not old.revoked and old.expires_at > timezone.now(), 'session_inactive', 403)
            return old, token_for(old)
        study = release.study
        require(release.approved and study.recruitment == 'open', 'admission_closed', 403)
        if study.mode == 'anonymous':
            require(data.get('participant_code') is None, 'unexpected_code')
            participant = Participant.objects.create(study=study)
        else:
            participant = Participant.objects.filter(study=study, code=data.get('participant_code'), active=True).first()
            require(participant is not None, 'admission_denied', 403)
            require(participant.expires_at is None or participant.expires_at > timezone.now(), 'admission_denied', 403)
            if study.mode == 'password':
                require(check_password(data.get('password', ''), participant.password_hash), 'admission_denied', 403)
            require(Session.objects.filter(participant=participant).count() < study.max_sessions, 'participation_limit', 403)
        session = Session(participant=participant, release=release, operation=data['operation_id'], proof_hash=digest(proof), request=binding, expires_at=timezone.now()+timedelta(days=7))
        token = token_for(session)
        session.token_hash = digest(token)
        session.save()
    return session, token


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
    require(type(event['sequence']) is int and event['sequence'] >= 1, 'sequence')
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
            session = Session.objects.select_related('release__build').get(pk=session_id)
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
        session = Session.objects.get(pk=session_id)
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
