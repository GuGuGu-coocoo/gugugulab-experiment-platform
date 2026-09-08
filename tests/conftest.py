import uuid
import pytest
from django.contrib.auth import get_user_model
from core.models import Instance, Study, Build, Release
from core.services import admit

@pytest.fixture
def setup(db):
    owner = get_user_model().objects.create_user('synthetic_owner', password='synthetic-test-password')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='Synthetic A', recruitment='open', max_sessions=3)
    descriptor = {'schemas': {'exp.rt': {'id':'rt', 'version':'1', 'schema': {'type':'object','properties':{'rt_ms':{'type':'number'},'choice':{'type':'string'}},'required':['rt_ms','choice'],'additionalProperties':False}}}, 'codebook': {'rt_ms': {'unit':'ms','source':'host'}}}
    build = Build.objects.create(study=study, descriptor=descriptor, digest='a'*64)
    release = Release.objects.create(study=study, build=build, config={'purpose':'synthetic'}, approved=True)
    request = {'operation_id':str(uuid.uuid4()), 'proof':'p'*48, 'instance_id':str(instance.instance_id),'study_id':str(study.id),'release_id':str(release.id),'build_id':str(build.id)}
    session, token = admit(release, request)
    return {'owner':owner,'instance':instance,'study':study,'release':release,'request':request,'session':session,'token':token}

@pytest.fixture
def event(setup):
    return {'protocol_version':'gep/1','event_id':str(uuid.uuid4()),'session_id':str(setup['session'].id),'segment_id':str(uuid.uuid4()),'sequence':1,'event_type':'exp.rt','schema_id':'rt','schema_version':'1','payload':{'rt_ms':321.5,'choice':'left'}}
