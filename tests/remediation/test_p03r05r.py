"""Regression for revoked-object disclosure from a refused permission commit."""
import re
import uuid
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from core.models import AccountProfile, Instance, Principal, Study
OWNER_PASSWORD = 'synthetic-p03r05r-owner-password'
ADMIN_PASSWORD = 'synthetic-p03r05r-admin-password'
USER_PASSWORD = 'synthetic-p03r05r-user-password'
VIEW = 'study.view'
PREVIEW_RE = re.compile(r'data-preview-id="([0-9a-fA-F-]{36})"')

def make_user(username, password, *, role='user', platform=None, studies=None):
    user = get_user_model().objects.create_user(username, password=password)
    Principal.objects.create(user=user)
    AccountProfile.objects.create(user=user, role=role, policy_version=2,
                                  platform_overrides=dict(platform or {}),
                                  study_overrides=dict(studies or {}))
    return user

def signed_in(username, password):
    client = Client()
    assert client.post('/login', {'username': username, 'password': password}).status_code == 302
    return client

@pytest.fixture
def world(db):
    owner = make_user('p03r05r_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    study_a = Study.objects.create(title='P03R05R study A', recruitment='open', max_sessions=5)
    study_b = Study.objects.create(title='P03R05R study B')
    AccountProfile.objects.filter(user=owner).update(
        study_overrides={str(study_a.pk): [VIEW, 'study.configure'],
                         str(study_b.pk): [VIEW, 'study.configure']})
    admin = make_user('p03r05r_admin', ADMIN_PASSWORD, role='admin',
                      platform={'accounts.manage_user': True},
                      studies={str(study_a.pk): [VIEW, 'study.configure'],
                               str(study_b.pk): [VIEW, 'study.configure']})
    target = make_user('p03r05r_target', USER_PASSWORD,
                       studies={str(study_a.pk): [VIEW], str(study_b.pk): [VIEW]})
    return {'owner': owner, 'instance': instance, 'study_a': study_a, 'study_b': study_b,
            'admin': admin, 'target': target}

def preview_payload(client, user_id, study_id, *, visibility=False, actions=()):
    data = {'op': 'matrix_preview', 'user_id': str(user_id), 'study_id': str(study_id)}
    if visibility:
        data['visibility'] = '1'
    for action in actions:
        data[f'action:{action}'] = '1'
    return client.post('/users', data)

def preview_id_of(response):
    match = PREVIEW_RE.search(response.content.decode())
    assert match, response.content[:400]
    return match.group(1)

def test_refused_commit_does_not_rerender_a_revoked_preview(world):
    """The error page of a refused commit must not become a second leak path.

    A recoverable refusal (a mistyped own password) still shows the actor their
    pending preview so they can retry; once the current authority for that exact
    operation is gone, the same refused commit must not re-render the stored
    account or study names.
    """
    admin, target, study_a = world['admin'], world['target'], world['study_a']
    client = signed_in(admin.username, ADMIN_PASSWORD)
    preview = preview_payload(client, target.pk, study_a.pk, visibility=True,
                              actions=['study.configure'])
    assert preview.status_code == 200
    pid = preview_id_of(preview)
    refused = client.post('/users', {'op': 'matrix_commit', 'preview_id': pid,
                                     'password': 'wrong-password-2026'})
    assert refused.status_code == 403
    body = refused.content.decode()
    assert '重新认证失败' in body and 'data-preview="matrix"' in body
    assert study_a.title in body
    # Revoke the Admin's own visibility of the study: the same refusal (wrong
    # and then correct password) no longer re-renders the stored preview, and
    # the study title is gone from the whole page (the Admin cannot see it).
    profile = AccountProfile.objects.get(user=admin)
    profile.study_overrides[str(study_a.pk)] = []
    profile.revision += 1
    profile.save(update_fields=['study_overrides', 'revision'])
    for password in ('wrong-password-2026', ADMIN_PASSWORD):
        after = client.post('/users', {'op': 'matrix_commit', 'preview_id': pid,
                                       'password': password})
        assert after.status_code == 403
        body = after.content.decode()
        assert 'data-preview="matrix"' not in body
        assert study_a.title not in body
    assert AccountProfile.objects.get(user=target).study_overrides[str(study_a.pk)] == [VIEW]
