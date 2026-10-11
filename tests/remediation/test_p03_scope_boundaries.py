"""Researcher GUI fixes found in the 2026-10-11 requirements walk-through.

These use a new pytest database and actual view/authorization/admission paths.
They cover Web-only preview controls and signed resources, exact roster-code
search, and the existing danger style hook; they are not human T17 evidence.
"""
import io
import re
import uuid
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import Client

from core.models import (AccountProfile, Audit, Build, Instance, Participant,
                         Principal, Release, Study)
from core.services import admit


def login(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session['gep_auth_version'] = 1
    session.save()
    return client


@pytest.fixture
def world(db, settings, tmp_path):
    settings.DATA_DIR = tmp_path
    owner = get_user_model().objects.create_user('scope_owner', password='Aa1!aa')
    AccountProfile.objects.create(user=owner)
    Principal.objects.create(user=owner)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    study = Study.objects.create(title='Scope boundary synthetic study', mode='id',
                                  recruitment='open', max_sessions=3)
    (tmp_path / 'packages').mkdir()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('web/index.html', '<html><head></head><body>Web only</body></html>')
    (tmp_path / 'packages' / 'web.zip').write_bytes(archive.getvalue())
    web = Build.objects.create(study=study, descriptor={'platform': 'godot_web'},
                               digest='a' * 64, package_path='web.zip')
    native = [Build.objects.create(study=study, descriptor={'platform': platform},
                                   digest=digest * 64, package_path=f'{platform}.zip')
              for platform, digest in [('macos_arm64', 'b'), ('windows_x64', 'c')]]
    release = Release.objects.create(study=study, build=web, approved=True,
                                      config={'purpose': 'synthetic', 'mode': 'id', 'max_sessions': 3})
    return {'owner': owner, 'instance': instance, 'study': study, 'web': web,
            'native': native, 'release': release, 'client': login(owner)}


def build_article(body, build):
    match = re.search(r'<article\b[^>]*data-build-row="' + str(build.id) + r'"[^>]*>(.*?)</article>',
                      body, re.S)
    assert match is not None
    return match.group(1)


def test_only_web_builds_offer_preview_and_native_posts_do_not_sign(world):
    client, study = world['client'], world['study']
    url = f'/studies/{study.id}/builds'
    body = client.get(url).content.decode()
    assert 'value="preview"' in build_article(body, world['web'])
    for build in world['native']:
        assert 'value="preview"' not in build_article(body, build)
        response = client.post(url, {'op': 'preview', 'build_id': str(build.id)},
                               HTTP_ACCEPT='application/json')
        assert response.status_code == 422
        assert response.json()['code'] == 'web_preview_only'
        assert not response.has_header('Location')
    assert not Audit.objects.filter(study=study).exists()
    assert Release.objects.filter(study=study).count() == 1


def test_web_preview_still_serves_local_context_and_requires_current_authority(world):
    client, study = world['client'], world['study']
    response = client.post(f'/studies/{study.id}/builds',
                           {'op': 'preview', 'build_id': str(world['web'].id)})
    assert response.status_code == 302
    preview = client.get(response['Location'], HTTP_HOST='experiment.localhost')
    assert preview.status_code == 200
    assert b'Web only' in preview.content and b'"preview": true' in preview.content
    assert b'"mode": "id"' in preview.content
    assert not Audit.objects.filter(study=study).exists()

    reader = get_user_model().objects.create_user('scope_preview_reader')
    AccountProfile.objects.create(user=reader, study_overrides={str(study.id): ['study.view']})
    denied = login(reader).post(f'/studies/{study.id}/builds',
                               {'op': 'preview', 'build_id': str(world['web'].id)},
                               HTTP_ACCEPT='application/json')
    assert denied.status_code == 403
    signed_reader = signing.dumps({'build': str(world['web'].id), 'user': reader.id}, salt='preview')
    assert client.get(f'/preview/{signed_reader}/web/index.html',
                      HTTP_HOST='experiment.localhost').status_code == 403


def test_old_signed_native_preview_is_rejected_before_opening_any_archive(world):
    # Native archives live in programs/, never packages/. There are deliberately
    # no native files in either root, so a successful rejection proves the route
    # does not attempt a wrong Web read (which would raise FileNotFoundError).
    for build in world['native']:
        token = signing.dumps({'build': str(build.id), 'user': world['owner'].id}, salt='preview')
        response = world['client'].get(f'/preview/{token}/web/index.html',
                                       HTTP_HOST='experiment.localhost')
        assert response.status_code == 404
        assert response.json()['code'] == 'web_preview_only'
    missing = Build.objects.create(study=world['study'], descriptor={'platform': 'godot_web'},
                                   digest='d' * 64)
    response = world['client'].post(f'/studies/{world["study"].id}/builds',
                                    {'op': 'preview', 'build_id': str(missing.id)},
                                    HTTP_ACCEPT='application/json')
    assert response.status_code == 422 and response.json()['code'] == 'web_preview_only'


@pytest.mark.parametrize('code', [' 001 ', 'leading ', ' trailing'])
def test_exact_code_search_keeps_admitted_identity_in_roster_and_sessions(world, code):
    study, client = world['study'], world['client']
    exact = Participant.objects.create(study=study, code=code)
    trimmed = Participant.objects.create(study=study, code=code.strip())
    sessions = []
    for participant in (exact, trimmed):
        session, _ = admit(world['release'], {
            'operation_id': str(uuid.uuid4()), 'proof': 'p' * 48,
            'instance_id': str(world['instance'].instance_id), 'study_id': str(study.id),
            'release_id': str(world['release'].id), 'build_id': str(world['web'].id),
            'participant_code': participant.code,
        })
        sessions.append(session)
    assert sessions[0].participant_id == exact.id
    for module, marker, expected_id, other_id in (
        ('participation', 'data-roster-row', exact.id, trimmed.id),
        ('sessions', 'data-session-row', sessions[0].id, sessions[1].id),
    ):
        url = f'/studies/{study.id}/{module}'
        response = client.get(url, {'code': code})
        assert response.status_code == 200
        body = response.content.decode()
        assert f'{marker}="{expected_id}"' in body
        assert f'{marker}="{other_id}"' not in body
        assert f'name="code" value="{code}"' in body
        # Searching the unspaced identity reaches exactly that separate subject.
        other = client.get(url, {'code': code.strip()}).content.decode()
        assert f'{marker}="{other_id}"' in other
        assert f'{marker}="{expected_id}"' not in other
        # An empty query continues to clear the filter rather than finding an ID.
        all_rows = client.get(url, {'code': ''}).content.decode()
        assert f'{marker}="{expected_id}"' in all_rows and f'{marker}="{other_id}"' in all_rows


@pytest.mark.parametrize('module', ['participation', 'sessions'])
def test_code_search_still_refuses_identity_mapping_oracles(world, module):
    study = world['study']
    reader = get_user_model().objects.create_user('scope_row_reader')
    AccountProfile.objects.create(user=reader, study_overrides={str(study.id): ['study.view', 'session.view']})
    client = login(reader)
    for code in (' 001 ', ' ', '001'):
        response = client.get(f'/studies/{study.id}/{module}', {'code': code})
        assert response.status_code == 403


def test_permanent_study_delete_uses_existing_danger_style(world):
    body = world['client'].get(f'/studies/{world["study"].id}').content.decode()
    assert re.search(r'<button\b[^>]*data-danger="1"[^>]*data-delete-submit="1"', body)
    assert 'button[data-danger="1"]' in body
    reader = get_user_model().objects.create_user('scope_no_delete')
    AccountProfile.objects.create(user=reader, study_overrides={str(world['study'].id): ['study.view']})
    assert 'data-delete-submit="1"' not in login(reader).get(f'/studies/{world["study"].id}').content.decode()
