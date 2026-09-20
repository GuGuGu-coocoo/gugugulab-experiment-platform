"""T30/T27/P0307 server evidence: public portal, stable study selection, preferences.

Independently specified expectations about the real database and HTTP behavior:
the portal lists only explicitly public + open studies whose current release is
really presentable, renders only the approved public fields (never release IDs,
roster, sessions or credentials), keeps the stable study-level entry, lets a
private study's direct stable link keep the 03C admission contract, keeps old
sessions bound to their release after a switch, and resolves language/theme
preferences without an open redirect.
"""
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from core import publication
from core.models import Build, Event, Grant, Instance, Participant, Release, Session, Study
from core.services import admit_request, receive

OWNER_PASSWORD = 'synthetic-test-password'
PUBLIC_FIELDS = {'public_summary': '公开简介 A', 'public_duration': '12 分钟',
                 'public_device_requirements': '桌面 Chrome'}
DESCRIPTOR = {'platform': 'godot_web', 'version': 'v1',
              'schemas': {'exp.rt': {'id': 'rt', 'version': '1', 'schema': {
                  'type': 'object', 'properties': {'rt_ms': {'type': 'number'}}, 'required': ['rt_ms'],
                  'additionalProperties': False}}},
              'codebook': {'rt_ms': {'unit': 'ms', 'source': 'host'}}}


def grant(user, study, *actions):
    for action in actions:
        Grant.objects.create(user=user, study=study, action=action, delegable=True)


def make_release(study, version, package='package.zip', approved=True):
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f'{study.id}-{version}').hex
    build = Build.objects.create(study=study, descriptor=dict(DESCRIPTOR, version=version),
                                 digest=digest, package_path=package)
    return Release.objects.create(study=study, build=build, approved=approved,
                                  config={'purpose': 'synthetic', 'mode': study.mode, 'tag': version})


def make_world(title='Portal synthetic', username='portal_owner'):
    owner = get_user_model().objects.create_user(username, password=OWNER_PASSWORD)
    instance = Instance.objects.filter(pk=1).first() or Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title=title, mode='id', recruitment='open', max_sessions=3)
    release = make_release(study, 'v1')
    grant(owner, study, 'study.view', 'study.configure', 'recruitment.manage')
    study.revision = 0
    publication.update_policy(owner, study, '0', {**PUBLIC_FIELDS, 'public': True})
    study.refresh_from_db()
    publication.select_current_release(owner, study, str(study.revision), str(release.id))
    study.refresh_from_db()
    return {'owner': owner, 'instance': instance, 'study': study, 'release': release}


def enter(client, study_id):
    return client.get(f'/join/{study_id}', HTTP_HOST='experiment.localhost')


@pytest.mark.django_db
def test_portal_public_fields_only_and_stable_study_selection():
    world = make_world()
    study, release = world['study'], world['release']
    Participant.objects.create(study=study, code='ROSTER-CODE-777')
    session, token = admit_request({'operation_id': str(uuid.uuid4()), 'proof': 'p' * 48,
                                    'instance_id': str(world['instance'].instance_id), 'study_id': str(study.id),
                                    'expected_release_id': str(release.id), 'expected_revision': study.revision,
                                    'participant_code': 'ROSTER-CODE-777'})
    page = Client().get('/', HTTP_HOST='experiment.localhost')
    assert page.status_code == 200
    body = page.content.decode()
    assert f'data-study="{study.id}"' in body
    assert '/join/' + str(study.id) in body
    for value in PUBLIC_FIELDS.values():
        assert value in body
    # Only approved public fields: no release/build identity, no roster/session,
    # no admission policy, no admin surface.
    for leaked in (str(release.id), release.build.digest, 'ROSTER-CODE-777', str(session.id),
                   str(Participant.objects.get(study=study).id), 'data-current-release', '名单 ID',
                   'max_sessions', 'data-session', '/users', 'admin.localhost', '/run/'):
        assert leaked not in body, leaked
    # The portal is a read-only public page.
    assert Client().post('/', {}, HTTP_HOST='experiment.localhost').status_code == 405


@pytest.mark.django_db
def test_private_paused_closed_and_no_current_cases_behave_as_03c():
    world = make_world()
    owner, study = world['owner'], world['study']
    client = Client()

    # The listed study stays visible while public + open + presentable.
    assert f'data-study="{study.id}"' in client.get('/', HTTP_HOST='experiment.localhost').content.decode()

    # Paused: not listed, the direct stable entry explains it and offers no start.
    study.recruitment = 'paused'
    study.save(update_fields=['recruitment'])
    assert f'data-study="{study.id}"' not in client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    paused = enter(client, study.id).content.decode()
    assert 'data-startable="0"' in paused and '暂停' in paused and '/run/' not in paused

    # Closed without the explicit opt-in: not listed at all; with the opt-in the
    # summary is shown without any start link.
    study.recruitment = 'closed'
    study.save(update_fields=['recruitment'])
    assert f'data-study="{study.id}"' not in client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    assert f'data-closed-study="{study.id}"' not in client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    study.show_closed_summary = True
    study.save(update_fields=['show_closed_summary'])
    closed = client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    assert f'data-closed-study="{study.id}"' in closed and PUBLIC_FIELDS['public_summary'] in closed
    assert f'/join/{study.id}' not in closed
    closed_entry = enter(client, study.id).content.decode()
    assert 'data-startable="0"' in closed_entry and '已结束' in closed_entry

    # A private study is never listed, but its direct stable link keeps the 03C
    # admission contract: the title is shown, the public fields are not.
    private_world = make_world(title='Portal private', username='portal_private_owner')
    private_study = private_world['study']
    publication.update_policy(private_world['owner'], private_study, str(private_study.revision), {**PUBLIC_FIELDS, 'public': False})
    listed = client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    assert f'data-study="{private_study.id}"' not in listed
    direct = enter(client, private_study.id).content.decode()
    assert private_study.title in direct and 'data-startable="1"' in direct
    assert PUBLIC_FIELDS['public_summary'] not in direct

    # No current release: not listed and the entry offers no start.
    study.show_closed_summary = False
    study.recruitment = 'open'
    study.current_release = None
    study.save(update_fields=['show_closed_summary', 'recruitment', 'current_release'])
    assert f'data-study="{study.id}"' not in client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    no_current = enter(client, study.id).content.decode()
    assert 'data-startable="0"' in no_current and 'data-expected-release=""' in no_current
    assert '/run/' not in no_current

    # An unapproved current release is not presentable either.
    unapproved = make_world(title='Portal unapproved', username='portal_unapproved_owner')
    bad = make_release(unapproved['study'], 'v9', approved=False)
    Study.objects.filter(pk=unapproved['study'].pk).update(current_release=bad)
    assert f'data-study="{unapproved["study"].id}"' not in client.get('/', HTTP_HOST='experiment.localhost').content.decode()


@pytest.mark.django_db
def test_old_bound_sessions_keep_their_release_after_a_current_switch():
    world = make_world()
    owner, study, first = world['owner'], world['study'], world['release']
    second = make_release(study, 'v2')
    Participant.objects.create(study=study, code='001')
    binding = {'operation_id': str(uuid.uuid4()), 'proof': 'p' * 48,
               'instance_id': str(world['instance'].instance_id), 'study_id': str(study.id),
               'expected_release_id': str(first.id), 'expected_revision': study.revision,
               'participant_code': '001'}
    session, token = admit_request(binding)
    assert session.release_id == first.id

    # Researcher switches the current release: new entry binds v2.
    publication.select_current_release(owner, study, str(study.revision), str(second.id))
    study.refresh_from_db()
    entry = enter(Client(), study.id).content.decode()
    assert f'data-expected-release="{second.id}"' in entry
    assert f'/run/{second.id}/web/index.html' in entry

    # The old session keeps its frozen release and still accepts uploads.
    session.refresh_from_db()
    assert session.release_id == first.id
    event = {'protocol_version': 'gep/1', 'event_id': str(uuid.uuid4()), 'session_id': str(session.id),
             'segment_id': str(uuid.uuid4()), 'sequence': 1, 'event_type': 'exp.rt',
             'schema_id': 'rt', 'schema_version': '1', 'payload': {'rt_ms': 250}}
    result = receive(session.id, token, {'batch_id': str(uuid.uuid4()), 'events': [event]})
    assert event['event_id'] in result['accepted']
    assert Event.objects.filter(session=session).count() == 1
    # The study is still listed: switching never unlists a presentable study.
    assert f'data-study="{study.id}"' in Client().get('/', HTTP_HOST='experiment.localhost').content.decode()


@pytest.mark.django_db
def test_language_and_theme_preferences_without_open_redirect():
    world = make_world()
    study = world['study']
    client = Client()

    # Default language is Chinese and the default theme follows the system.
    default = client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    assert 'lang="zh"' in default and 'data-theme="system"' in default
    assert 'data-pref-lang="en"' in default and 'data-pref-theme="dark"' in default

    # English + dark persist through the cookie and render translated generic UI.
    switch = client.get('/prefs?lang=en&theme=dark&next=/', HTTP_HOST='experiment.localhost')
    assert switch.status_code == 302 and switch['Location'] == '/'
    assert switch.cookies['gep_lang'].value == 'en' and switch.cookies['gep_theme'].value == 'dark'
    client.cookies['gep_lang'] = 'en'
    client.cookies['gep_theme'] = 'dark'
    english = client.get('/', HTTP_HOST='experiment.localhost').content.decode()
    assert 'lang="en"' in english and 'data-theme="dark"' in english
    assert 'Now recruiting' in english and study.title in english
    entry = enter(client, study.id).content.decode()
    assert 'lang="en"' in entry and 'Start participation' in entry

    # Invalid values are ignored instead of being reflected.
    invalid = client.get('/prefs?lang=fr&theme=blue&next=/', HTTP_HOST='experiment.localhost')
    assert invalid.status_code == 302 and invalid['Location'] == '/'
    assert 'gep_lang' not in invalid.cookies and 'gep_theme' not in invalid.cookies

    # The return target can only be a same-origin absolute path.
    for hostile in ('https://evil.example/', '//evil.example/', 'http://evil.example', '/\\evil'):
        response = client.get(f'/prefs?lang=en&next={hostile}', HTTP_HOST='experiment.localhost')
        assert response.status_code == 302 and response['Location'] == '/', hostile
