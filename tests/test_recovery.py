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
