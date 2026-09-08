import hashlib
import io
import json
import zipfile
from pathlib import Path
import pytest
from django.test import Client
from core.models import Grant, Study, Release, Invitation
from core.packages import validate_package
from core.protocol import Rejected


def package(extra=None):
    target=io.BytesIO()
    with zipfile.ZipFile(target,'w') as z:
        descriptor=json.loads(Path('examples/synthetic_experiment/descriptor.json').read_text())
        descriptor['program_sha256']=hashlib.sha256(b'web/index.html<html>synthetic</html>').hexdigest()
        z.writestr('manifest.json',json.dumps(descriptor))
        z.writestr('web/index.html','<html>synthetic</html>')
        if extra:z.writestr(extra,'bad')
    return target.getvalue()

@pytest.mark.parametrize('extra',['../outside','web/../../outside','/absolute','web\\file','v1/route','web/INDEX.html'])
def test_archive_rejects_unsafe_paths(extra):
    with pytest.raises(Rejected):validate_package(package(extra))

def test_valid_package():
    descriptor,sha=validate_package(package())
    assert descriptor['platform']=='godot_web' and len(sha)==64

def test_gui_real_object_authority_and_invitation(setup):
    c=Client();c.force_login(setup['owner'])
    response=c.post('/',{'title':'GUI synthetic'})
    assert response.status_code==302
    study=Study.objects.get(title='GUI synthetic')
    url=f'/studies/{study.id}'
    assert c.get(url).status_code==200
    assert c.post(url,{'op':'configure','mode':'password','max_sessions':'2'}).status_code==302
    assert c.post(url,{'op':'roster','roster':'001\tsynthetic-password'}).status_code==302
    response=c.post(url,{'op':'invite','username':'synthetic_reader','actions':['study.view']})
    assert response.status_code==200
    invite=Invitation.objects.get(study=study)
    assert invite.actions==['study.view']
    other=Study.objects.create(title='No grant')
    assert c.get(f'/studies/{other.id}').status_code==403
    Grant.objects.filter(user=setup['owner'],study=study,action='permission.delegate').delete()
    assert c.post(url,{'op':'invite','username':'blocked','actions':['study.view']}).status_code==403

def test_existing_account_invite_never_resets_password(setup):
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from datetime import timedelta
    from core.services import digest
    user=get_user_model().objects.create_user('existing',password='original-synthetic-password')
    for action in ['member.manage','permission.delegate','study.view']:
        Grant.objects.create(user=setup['owner'],study=setup['study'],action=action,delegable=True)
    invitation=Invitation.objects.create(study=setup['study'],issuer=setup['owner'],username=user.username,actions=['study.view'],token_hash=digest('synthetic-ticket'),expires_at=timezone.now()+timedelta(hours=1))
    c=Client()
    assert c.post('/activate',{'token':'synthetic-ticket','password':'attempted-replacement'}).status_code==403
    c.force_login(user)
    assert c.post('/activate',{'token':'synthetic-ticket'}).status_code==302
    user.refresh_from_db();assert user.check_password('original-synthetic-password')
    assert c.post('/activate',{'token':'synthetic-ticket'}).status_code==403
    assert Grant.objects.filter(user=user,study=setup['study'],action='study.view').exists()
