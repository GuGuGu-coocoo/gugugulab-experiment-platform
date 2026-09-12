import copy
import uuid
import pytest
from django.db import connection
from django.contrib.auth.hashers import make_password
from core.models import Event, Participant, Session, Study, Release
from core.services import admit, receive, finish, authorize_session
from core.protocol import Rejected


def batch(*events):
    return {'batch_id':str(uuid.uuid4()), 'events':list(events)}

def test_ack_loss_and_database_reconnect(setup,event):
    receive(setup['session'].id, setup['token'], batch(event))  # deliberately discard ACK
    connection.close()
    ack = receive(setup['session'].id, setup['token'], batch(event))
    assert ack['accepted'] == [] and ack['duplicate'] == [event['event_id']]
    assert Event.objects.count() == 1
    assert Event.objects.get().envelope['payload'] == {'rt_ms':321.5,'choice':'left'}

def test_conflict_rolls_back_entire_batch(setup,event):
    receive(setup['session'].id, setup['token'], batch(event))
    fresh = copy.deepcopy(event); fresh['event_id']=str(uuid.uuid4()); fresh['sequence']=2
    conflict = copy.deepcopy(event); conflict['payload']['rt_ms']=322
    with pytest.raises(Rejected, match='event_conflict'):
        receive(setup['session'].id, setup['token'], batch(fresh,conflict))
    assert Event.objects.count() == 1
    assert Event.objects.get().envelope == event

def test_new_identity_same_value_retained(setup,event):
    fresh = copy.deepcopy(event); fresh['event_id']=str(uuid.uuid4()); fresh['sequence']=2
    receive(setup['session'].id, setup['token'], batch(event,fresh))
    assert Event.objects.count() == 2

def test_pending_completion_then_exact_set(setup,event):
    declaration = {'event_ids':[event['event_id']], 'segment_ids':[event['segment_id']]}
    result=finish(setup['session'].id,setup['token'],declaration)
    assert result['state']=='pending' and result['missing']==[event['event_id']]
    receive(setup['session'].id,setup['token'],batch(event))
    assert finish(setup['session'].id,setup['token'],declaration)['state']=='complete'
    fresh=copy.deepcopy(event); fresh['event_id']=str(uuid.uuid4()); fresh['sequence']=2
    with pytest.raises(Rejected, match='completion_closed'):
        receive(setup['session'].id,setup['token'],batch(fresh))

def test_admission_retry_requires_private_proof(setup):
    old, token=admit(setup['release'],setup['request'])
    assert old.id==setup['session'].id and token==setup['token'] and Session.objects.count()==1
    wrong=dict(setup['request'], proof='q'*48)
    with pytest.raises(Rejected, match='operation_conflict'):
        admit(setup['release'],wrong)

def test_three_admission_modes_and_study_identity(setup):
    study=setup['study']; study.mode='password'; study.save()
    person=Participant.objects.create(study=study, code='001',password_hash=make_password('synthetic-only'))
    req=dict(setup['request'],operation_id=str(uuid.uuid4()),participant_code='001',password='wrong')
    with pytest.raises(Rejected,match='admission_denied'):
        admit(setup['release'],req)
    req['password']='synthetic-only'
    session,_=admit(setup['release'],req)
    assert session.participant_id==person.id and session.participant.code=='001'
    study.mode='id';study.save()
    req['operation_id']=str(uuid.uuid4()); req.pop('password')
    another,_=admit(setup['release'],req)
    assert another.id!=session.id and another.participant_id==person.id
    req['operation_id']=str(uuid.uuid4());req['participant_code']='unknown'
    with pytest.raises(Rejected,match='admission_denied'):
        admit(setup['release'],req)
    other=Study.objects.create(title='Synthetic B')
    other_person=Participant.objects.create(study=other,code='001')
    assert other_person.id!=person.id
    with pytest.raises(Rejected,match='unauthorized'):
        authorize_session(session,str(person.id))

def test_schema_and_sequence_failures_rollback(setup,event):
    bad=copy.deepcopy(event); bad['event_id']=str(uuid.uuid4());bad['sequence']=2;bad['payload']['extra']=True
    with pytest.raises(Rejected,match='payload_schema'):
        receive(setup['session'].id,setup['token'],batch(event,bad))
    assert Event.objects.count()==0
    bad.pop('payload');bad['payload']=event['payload']
    bad['sequence']=1
    with pytest.raises(Rejected,match='identity_conflict'):
        receive(setup['session'].id,setup['token'],batch(event,bad))
    assert Event.objects.count()==0

def test_godot_integral_json_number_sequence(setup,event):
    event['sequence']=1.0
    ack=receive(setup['session'].id,setup['token'],batch(event))
    assert ack['accepted']==[event['event_id']]
    assert Event.objects.get().envelope['sequence']==1.0
    event['event_id']=str(uuid.uuid4());event['sequence']=1.5
    with pytest.raises(Rejected,match='sequence'):
        receive(setup['session'].id,setup['token'],batch(event))

def test_configuration_binding_expiry_and_revocation(setup,event):
    from datetime import timedelta
    from django.utils import timezone
    wrong=dict(setup['request'],operation_id=str(uuid.uuid4()),study_id=str(uuid.uuid4()))
    with pytest.raises(Rejected,match='wrong_binding'):admit(setup['release'],wrong)
    wrong=dict(setup['request'],operation_id=str(uuid.uuid4()),build_id=str(uuid.uuid4()))
    with pytest.raises(Rejected,match='wrong_binding'):admit(setup['release'],wrong)
    setup['session'].revoked=True;setup['session'].save()
    with pytest.raises(Rejected,match='session_inactive'):receive(setup['session'].id,setup['token'],batch(event))
    setup['session'].revoked=False;setup['session'].expires_at=timezone.now()-timedelta(seconds=1);setup['session'].save()
    with pytest.raises(Rejected,match='session_inactive'):receive(setup['session'].id,setup['token'],batch(event))
    assert Event.objects.count()==0


@pytest.mark.parametrize('state',['paused','closed'])
def test_recruitment_stops_new_admission_but_honors_valid_upload_window(setup,event,state):
    setup['study'].recruitment=state;setup['study'].save(update_fields=['recruitment'])
    with pytest.raises(Rejected,match='admission_closed'):
        admit(setup['release'],dict(setup['request'],operation_id=str(uuid.uuid4())))
    assert receive(setup['session'].id,setup['token'],batch(event))['accepted']==[event['event_id']]
    assert Event.objects.count()==1
