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
