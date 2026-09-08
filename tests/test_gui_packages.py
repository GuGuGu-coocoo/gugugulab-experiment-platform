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
