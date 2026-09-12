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

def test_gui_and_preview_use_configured_public_api(setup,tmp_path,settings,monkeypatch):
    from core.models import Build
    monkeypatch.setenv('GEP_PUBLIC_API','http://experiment.localhost:8123/')
    settings.DATA_DIR=tmp_path
    package_root=tmp_path/'packages';package_root.mkdir()
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as output:
        output.writestr('web/index.html','<html><head></head><body>synthetic</body></html>')
    (package_root/'preview.zip').write_bytes(archive.getvalue())
    build=Build.objects.create(study=setup['study'],descriptor={'platform':'godot_web'},digest='b'*64,package_path='preview.zip')
    web_release=Release.objects.create(study=setup['study'],build=build,approved=True,config={'purpose':'synthetic'})
    for action in ['study.view','build.preview']:
        Grant.objects.create(user=setup['owner'],study=setup['study'],action=action)
    c=Client();c.force_login(setup['owner'])
    page=c.get(f'/studies/{setup["study"].id}')
    assert page.status_code==200
    assert f'http://experiment.localhost:8123/run/{web_release.id}/web/index.html'.encode() in page.content
    response=c.post(f'/studies/{setup["study"].id}',{'op':'preview','build_id':str(build.id)})
    assert response.status_code==302
    assert response['Location'].startswith('http://experiment.localhost:8123/preview/')
    preview=c.get(response['Location'],HTTP_HOST='experiment.localhost:8123')
    assert preview.status_code==200
    assert b'"api_url": "http://experiment.localhost:8123"' in preview.content

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


@pytest.mark.parametrize('condition',['expired','revoked','issuer_revoked','delegation_removed'])
def test_inactive_or_outdated_invitation_cannot_create_user(setup,condition):
    from datetime import timedelta
    from django.utils import timezone
    from django.contrib.auth import get_user_model
    from core.services import digest
    for action in ['member.manage','permission.delegate','study.view']:
        Grant.objects.create(user=setup['owner'],study=setup['study'],action=action,delegable=True)
    invite=Invitation.objects.create(study=setup['study'],issuer=setup['owner'],username='new_synthetic_member',actions=['study.view'],token_hash=digest('bounded-invitation'),expires_at=timezone.now()+timedelta(hours=1))
    if condition=='expired':invite.expires_at=timezone.now()-timedelta(seconds=1);invite.save()
    elif condition=='revoked':invite.revoked=True;invite.save()
    elif condition=='issuer_revoked':Grant.objects.filter(user=setup['owner'],study=setup['study'],action='member.manage').delete()
    else:Grant.objects.filter(user=setup['owner'],study=setup['study'],action='study.view').update(delegable=False)
    response=Client().post('/activate',{'token':'bounded-invitation','password':'synthetic-password-long-enough'})
    assert response.status_code==403
    assert not get_user_model().objects.filter(username='new_synthetic_member').exists()
    invite.refresh_from_db();assert not invite.consumed


def test_limited_admin_cannot_delegate_export_or_remove_higher_grants(setup):
    from django.contrib.auth import get_user_model
    user=get_user_model().objects.create_user('limited_synthetic_admin',password='synthetic-password')
    for action in ['study.view','member.manage','permission.delegate']:
        Grant.objects.create(user=user,study=setup['study'],action=action,delegable=True)
    protected=Grant.objects.create(user=setup['owner'],study=setup['study'],action='data.export_raw',delegable=True)
    c=Client();c.force_login(user);url='/studies/'+str(setup['study'].id)
    assert c.post(url,{'op':'invite','username':'unapproved_reader','actions':['data.export_raw']}).status_code==403
    assert c.post(url,{'op':'revoke_member','user_id':setup['owner'].id}).status_code==403
    assert Grant.objects.filter(pk=protected.pk).exists()
    assert not Invitation.objects.filter(study=setup['study']).exists()


@pytest.mark.parametrize('kind,code',[
    ('symlink','non_regular_file'),('modified','program_digest_mismatch'),
    ('remote_schema','schema_references_unsupported'),('missing_entry','entry_missing'),
    ('sdk','incompatible_build'),('file_count','file_count'),('ratio','compression_ratio')])
def test_package_adversarial_metadata_and_contents(kind,code):
    import stat
    source=zipfile.ZipFile(io.BytesIO(package()));target=io.BytesIO()
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as output:
        for entry in source.infolist():
            if kind=='missing_entry' and entry.filename=='web/index.html':continue
            data=source.read(entry)
            if entry.filename=='manifest.json' and kind in ('remote_schema','sdk'):
                descriptor=json.loads(data)
                if kind=='sdk':descriptor['sdk_version']='unsupported'
                else:next(iter(descriptor['schemas'].values()))['schema']={'$ref':'https://example.invalid/never-fetch'}
                data=json.dumps(descriptor).encode()
            if kind=='modified' and entry.filename=='web/index.html':data=b'modified after descriptor'
            output.writestr(entry.filename,data)
        if kind=='symlink':
            link=zipfile.ZipInfo('web/link');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16;output.writestr(link,'index.html')
        elif kind=='file_count':
            for i in range(255):output.writestr(f'web/extra-{i}.txt','x')
        elif kind=='ratio':output.writestr('web/compressed.txt',b'0'*100000)
    with pytest.raises(Rejected,match=code):validate_package(target.getvalue())


@pytest.mark.parametrize('setting,code',[('MAX_ARCHIVE','archive_limit'),('MAX_EXPANDED','expanded_limit')])
def test_package_small_boundaries_reject_before_publication(monkeypatch,setting,code):
    from core import packages
    monkeypatch.setattr(packages,setting,1)
    with pytest.raises(Rejected,match=code) as result:validate_package(package())
    assert result.value.status==413
