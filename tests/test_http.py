import uuid
import pytest
from django.test import Client
from core.models import Grant, Study, Export
from core.services import receive


def test_http_authority_snapshot_and_revocation(setup,event):
    client=Client()
    route=f"/v1/participant/sessions/{setup['session'].id}/event-batches"
    data={'batch_id':str(uuid.uuid4()),'events':[event]}
    assert client.post(route,data,content_type='application/json').status_code==401
    assert client.post(route,data,content_type='application/json',HTTP_AUTHORIZATION='Bearer '+setup['token']).status_code==200
    export_route='/v1/admin/exports'
    assert client.post(export_route,{'study_id':str(setup['study'].id)},content_type='application/json',HTTP_AUTHORIZATION='Bearer '+setup['token']).status_code==403
    client.force_login(setup['owner'])
    grant=Grant.objects.create(user=setup['owner'],study=setup['study'],action='data.export_raw')
    response=client.post(export_route,{'study_id':str(setup['study'].id)},content_type='application/json')
    assert response.status_code==201
    export_id=response.json()['export_id']
    other=Study.objects.create(title='B')
    assert client.post(export_route,{'study_id':str(other.id)},content_type='application/json').status_code==403
    fresh=dict(event,event_id=str(uuid.uuid4()),sequence=2)
    receive(setup['session'].id,setup['token'],{'batch_id':str(uuid.uuid4()),'events':[fresh]})
    download=f'/v1/admin/exports/{export_id}/download'
    response=client.get(download)
    assert response.status_code==200 and len(response.content.splitlines())==1
    assert b'321.5' in response.content and b'password' not in response.content and b'token' not in response.content
    grant.delete()
    assert client.get(download).status_code==403


def test_host_boundary_and_csrf(setup):
    client=Client(enforce_csrf_checks=True)
    client.force_login(setup['owner'])
    assert client.post('/v1/admin/exports',{},content_type='application/json').status_code==403
    assert client.get('/v1/admin/exports/'+str(uuid.uuid4())+'/download',HTTP_HOST='experiment.localhost').status_code==403
    assert client.post('/v1/participant/sessions',setup['request'],content_type='application/json',HTTP_HOST='admin.localhost').status_code==403

def test_csv_lossless_nested_values_and_sidecar_authorization(setup,event):
    import csv,io,json
    c=Client();c.force_login(setup['owner'])
    grant=Grant.objects.create(user=setup['owner'],study=setup['study'],action='data.export_raw')
    # Fixed export fixture exercises CSV encoding independently of payload schema.
    golden={'null':None,'zero':0,'false':False,'chinese':'中文','code':'001','negative':-3,'formula':'=1+1','multi':['a','b']}
    item=Export.objects.create(study=setup['study'],snapshot={'records':[{'study_id':str(setup['study'].id),'release_id':'r','build_id':'b','record':golden}],'builds':{},'sessions':{}})
    url=f'/v1/admin/exports/{item.id}/download'
    result=c.get(url+'?format=csv')
    from pathlib import Path
    Path('build').mkdir(exist_ok=True)
    Path('build/csv_golden.csv').write_bytes(result.content)
    rows=list(csv.DictReader(io.StringIO(result.content.decode("utf-8-sig"))))
    assert rows[0]['record_json'].startswith('json:')
    assert json.loads(rows[0]['record_json'][5:])==golden
    assert 'missing' not in json.loads(rows[0]['record_json'][5:])
    assert c.get(url+'?format=metadata').status_code==200
    grant.delete()
    assert c.get(url+'?format=csv').status_code==403
    assert c.get(url+'?format=metadata').status_code==403

def test_body_limit_before_parsing_and_wrong_program_version(setup):
    c=Client()
    response=c.generic('POST','/v1/participant/sessions',b'{}',content_type='application/json',CONTENT_LENGTH=str(262145))
    assert response.status_code==413
    data=dict(setup['request'],operation_id=str(uuid.uuid4()),expected_version='wrong-version')
    assert c.post('/v1/participant/sessions',data,content_type='application/json').status_code==422
