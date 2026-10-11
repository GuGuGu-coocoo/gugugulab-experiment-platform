"""Regressions found by clicking the isolated Phase 03 researcher GUI."""

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from core.models import AccountProfile, Build, Instance, Principal, Release, Study


@pytest.mark.django_db
def test_build_rejection_keeps_the_forms_available_for_retry(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    owner = get_user_model().objects.create_user('gui_review_owner')
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    Principal.objects.create(user=owner)
    AccountProfile.objects.create(user=owner, role='user', policy_version=2)
    study = Study.objects.create(title='GUI retry synthetic study')
    client = Client()
    client.force_login(owner)
    session = client.session
    session['gep_auth_version'] = 1
    session.save()
    url = f'/studies/{study.id}/builds'

    descriptor = 'not json'
    response = client.post(url, {'op': 'native', 'descriptor': descriptor}, HTTP_ACCEPT='text/html')
    body = response.content.decode()
    assert response.status_code == 422
    assert 'data-error="invalid_json"' in body
    assert 'data-native-register="1"' in body and 'data-package-upload="1"' in body
    assert 'data-module-error' not in body
    assert f'>{descriptor}</textarea>' in body

    response = client.post(url, {'op': 'upload',
                                 'package': SimpleUploadedFile('broken.zip', b'not a zip')},
                           HTTP_ACCEPT='text/html')
    body = response.content.decode()
    assert response.status_code == 422
    assert 'data-error="invalid_archive"' in body
    assert 'data-package-upload="1"' in body and 'data-module-error' not in body
    assert Build.objects.filter(study=study).count() == 0
    assert Release.objects.filter(study=study).count() == 0
