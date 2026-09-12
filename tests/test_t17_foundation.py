import json

import pytest
from django.test import Client
from core.models import Build, Export, Grant, Release


def client_for(setup, *actions):
    for action in actions:
        Grant.objects.create(user=setup['owner'],study=setup['study'],action=action)
    client=Client()
    client.force_login(setup['owner'])
    return client


def test_metadata_download_is_authorized_fixed_attachment(setup):
    client=client_for(setup,'data.export_raw')
    response=client.post('/v1/admin/exports',data=json.dumps({'study_id':str(setup['study'].id)}),content_type='application/json')
    assert response.status_code==201
    export=Export.objects.get(pk=response.json()['export_id'])
    before=export.snapshot.copy()
    url=f'/v1/admin/exports/{export.id}/download?format=metadata'
    response=client.get(url)
    assert response.status_code==200
    assert response['Content-Disposition']==f'attachment; filename="{export.id}.metadata.json"'
    assert response['Cache-Control']=='no-store'
    assert response['Content-Type']=='application/json'
    assert response.json()=={k:v for k,v in before.items() if k!='records'}
    assert 'records' not in response.json()
    export.refresh_from_db()
    assert export.snapshot==before
    Grant.objects.filter(user=setup['owner'],action='data.export_raw').delete()
    assert client.get(url).status_code==403


@pytest.mark.parametrize('mode',['anonymous','id','password'])
@pytest.mark.parametrize('state',['open','paused','closed'])
def test_policy_and_recruitment_show_persisted_values(setup,mode,state):
    study=setup['study'];study.mode=mode;study.recruitment=state;study.save()
    client=client_for(setup,'study.view','study.configure','recruitment.manage')
    page=client.get(f'/studies/{study.id}').content.decode()
    assert f'<option value="{mode}" selected>' in page
    assert f'<option value="{state}" selected>' in page
    assert page.count('selected>')==2


def test_only_approved_hosted_web_release_has_participation_link(setup):
    client=client_for(setup,'study.view')
    releases=[]
    for index,(platform,path,approved) in enumerate([
        ('macos_arm64','',True),('godot_web','web.zip',True),
        ('godot_web','',True),('godot_web','draft.zip',False),
    ]):
        build=Build.objects.create(study=setup['study'],descriptor={'platform':platform,'version':f'v{index}'},digest=str(index)*64,package_path=path)
        releases.append(Release.objects.create(study=setup['study'],build=build,approved=approved,config={}))
    page=client.get(f'/studies/{setup["study"].id}').content.decode()
    for index,release in enumerate(releases):
        assert (f'/run/{release.id}/web/index.html' in page)==(index==1)
        assert f'v{index}' in page
    assert f'/run/{setup["release"].id}/web/index.html' not in page


def test_roster_csv_preserves_ids_and_quoted_passwords_and_rolls_back(setup):
    from core.models import Participant
    from django.contrib.auth.hashers import check_password
    study=setup['study'];study.mode='password';study.save()
    client=client_for(setup,'study.view','study.configure')
    url=f'/studies/{study.id}'
    response=client.post(url,{'op':'roster','roster_format':'csv','roster':'001,"synthetic,password-long"\n002,synthetic-password-long'},follow=True)
    assert response.status_code==200
    assert '新增 2 个 ID' in response.content.decode()
    assert 'synthetic,password-long' not in response.content.decode()
    assert check_password('synthetic,password-long',Participant.objects.get(study=study,code='001').password_hash)
    before=Participant.objects.count()
    response=client.post(url,{'op':'roster','roster_format':'csv','roster':'003,synthetic-password-long\n001,synthetic-password-long'},HTTP_ACCEPT='text/html')
    assert response.status_code==422
    assert '本次未导入任何行' in response.content.decode()
    assert Participant.objects.count()==before
    assert not Participant.objects.filter(code='003').exists()


def test_viewer_has_no_mutating_controls_and_forged_post_is_denied(setup):
    client=client_for(setup,'study.view')
    url=f'/studies/{setup["study"].id}'
    page=client.get(url).content.decode()
    for op in ('configure','roster','upload','approve','recruitment','recover','invite'):
        assert f'name="op" value="{op}"' not in page
    response=client.post(url,{'op':'recruitment','state':'closed'},HTTP_ACCEPT='text/html')
    assert response.status_code==403
    assert '当前账号没有此操作权限' in response.content.decode()
    setup['study'].refresh_from_db()
    assert setup['study'].recruitment=='open'


def test_owner_cannot_be_removed_by_equal_delegable_grants(setup):
    from django.contrib.auth import get_user_model
    from core.access import ACTIONS
    user=get_user_model().objects.create_user('synthetic_delegate')
    for action in ACTIONS:
        Grant.objects.create(user=user,study=setup['study'],action=action,delegable=True)
        Grant.objects.create(user=setup['owner'],study=setup['study'],action=action,delegable=True)
    client=Client();client.force_login(user)
    url=f'/studies/{setup["study"].id}'
    before=Grant.objects.filter(user=setup['owner']).count()
    response=client.post(url,{'op':'revoke_member','user_id':setup['owner'].id})
    assert response.status_code==403
    assert response.json()['code']=='owner_protected'
    assert Grant.objects.filter(user=setup['owner']).count()==before


def test_policy_error_keeps_study_page_and_json_contract(setup):
    client=client_for(setup,'study.view','study.configure')
    url=f'/studies/{setup["study"].id}'
    data={'op':'configure','mode':'id','max_sessions':'2'}
    response=client.post(url,data,HTTP_ACCEPT='text/html')
    assert response.status_code==409
    assert '参与政策已冻结' in response.content.decode()
    assert 'role="alert"' in response.content.decode()
    response=client.post(url,data)
    assert response.status_code==409
    assert response.json()['code']=='policy_frozen_after_release'
