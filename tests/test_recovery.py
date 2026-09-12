from datetime import timedelta
import pytest
from django.utils import timezone
from core.models import RecoveryPermit, Grant, Audit
from core.services import recover,digest
from core.protocol import Rejected

def test_recovery_requires_grant_private_proof_and_single_use(setup):
    Grant.objects.create(user=setup['owner'],study=setup['study'],action='session.recover')
    RecoveryPermit.objects.create(session=setup['session'],issuer=setup['owner'],token_hash=digest('synthetic-permit'),expires_at=timezone.now()+timedelta(minutes=15))
    with pytest.raises(Rejected,match='recovery_denied'):
        recover(setup['session'].id,'public-id','synthetic-permit')
    result=recover(setup['session'].id,setup['request']['proof'],'synthetic-permit')
    assert result['session_id']==str(setup['session'].id)
    assert Audit.objects.filter(action='session.recovered').count()==1
    with pytest.raises(Rejected,match='recovery_permit_inactive'):
        recover(setup['session'].id,setup['request']['proof'],'synthetic-permit')

def test_revoked_researcher_cannot_recover(setup):
    RecoveryPermit.objects.create(session=setup['session'],issuer=setup['owner'],token_hash=digest('synthetic-permit'),expires_at=timezone.now()+timedelta(minutes=15))
    with pytest.raises(Rejected,match='forbidden'):
        recover(setup['session'].id,setup['request']['proof'],'synthetic-permit')


def test_expired_finished_queue_can_reauthenticate_but_declaration_stays_closed(setup,event):
    import uuid
    from core.services import finish, receive
    from core.models import Event
    declaration={'event_ids':[event['event_id']],'segment_ids':[event['segment_id']]}
    assert finish(setup['session'].id,setup['token'],declaration)['state']=='pending'
    session=setup['session'];session.expires_at=timezone.now()-timedelta(seconds=1);session.save(update_fields=['expires_at'])
    with pytest.raises(Rejected,match='session_inactive'):
        receive(session.id,setup['token'],{'batch_id':str(uuid.uuid4()),'events':[event]})
    Grant.objects.create(user=setup['owner'],study=setup['study'],action='session.recover')
    RecoveryPermit.objects.create(session=session,issuer=setup['owner'],token_hash=digest('finished-queue-permit'),expires_at=timezone.now()+timedelta(minutes=15))
    result=recover(session.id,setup['request']['proof'],'finished-queue-permit')
    assert result['task_finished'] is True
    assert receive(session.id,result['token'],{'batch_id':str(uuid.uuid4()),'events':[event]})['accepted']==[event['event_id']]
    extra={**event,'event_id':str(uuid.uuid4()),'sequence':2}
    with pytest.raises(Rejected,match='completion_closed'):
        receive(session.id,result['token'],{'batch_id':str(uuid.uuid4()),'events':[extra]})
    assert finish(session.id,result['token'],declaration)['state']=='complete'
    assert Event.objects.filter(session=session).count()==1


def test_revocation_still_prevents_upload_reauthentication(setup):
    session=setup['session'];session.revoked=True;session.save(update_fields=['revoked'])
    Grant.objects.create(user=setup['owner'],study=setup['study'],action='session.recover')
    ticket=RecoveryPermit.objects.create(session=session,issuer=setup['owner'],token_hash=digest('revoked-permit'),expires_at=timezone.now()+timedelta(minutes=15))
    with pytest.raises(Rejected,match='recovery_denied'):
        recover(session.id,setup['request']['proof'],'revoked-permit')
    ticket.refresh_from_db();assert not ticket.consumed
