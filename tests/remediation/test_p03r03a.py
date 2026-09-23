"""P03R03A evidence: ordinary-user study creation and the unified researcher password.

The expected side is the R00 contract (2026-09-23, sections A/C), requirement
U07 and the fixed synthetic inputs below, never the implementation under test:

- ``study.create`` opens the home POST for every role that holds it; the creator
  receives the complete finite business set once (complete explicit override +
  Grant rows + stable Principal) and can fully manage that study while instance
  accounts stay out of reach; revoking ``study.create`` refuses new studies and
  removing a study action is never refilled from ``creator_principal``;
- one researcher-password rule (>= 6 characters, one ASCII upper/lower/digit and
  one visible ASCII punctuation each, no trim, a space is not a symbol) is
  enforced at every entry: invitation activation, temporary create/reset, self
  change, the legacy activate route and both initialize paths;
- generated temporary passwords guarantee all four classes with >= 24 characters
  by construction, and a stored legacy hash keeps logging in untouched;
- one real Chrome journey: ordinary login -> create -> business page plus a
  temporary account's forced first password change.

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r03a/<UTC>-<random>/`` root.
"""
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import Client
from django.utils import timezone

from core import accounts, researcher_passwords
from core.access import allowed, allowed_platform
from core.models import (AccountInvitation, AccountProfile, Audit, Grant,
                         Instance, Invitation, Principal, Study)
from core.services import digest

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = 'synthetic-p03r03a-owner-password'  # pre-existing hash, never re-set
USER_PASSWORD = 'Synthetic-p03r03a-user-password1'   # pre-existing hash, never re-set
NEW_PASSWORD = 'Aa1!aa'                              # the confirmed accepted example
STUDY_V2 = ('study.view', 'study.configure', 'build.upload', 'build.preview',
            'release.approve_pilot', 'recruitment.manage', 'data.export_raw',
            'identity_mapping.read', 'session.view', 'session.recover',
            'audit.view', 'study.delete')
WEAK_PASSWORDS = ('Aa1aaa', 'aa1!aa', 'AA1!AA', 'Aaa!aa', 'Aa1!a', 'Aa1 aa', ' ' * 8)
SECRET_RE = re.compile(r'data-one-time-secret="1".*?<code>(.*?)</code>', re.S)
INVITE_RE = re.compile(r'data-one-time-invitation="1".*?data-invitation-link="[^"]*/activate-account\?token=([^"&]+)"', re.S)
TEMPLATES = ('server/core/templates/core/password.html',
             'server/core/templates/core/activate.html',
             'server/core/templates/core/activate_account.html')
ENTRY_FILES = ('server/core/accounts.py', 'server/core/gui.py',
               'server/core/management/commands/initialize.py',
               'server/gep/initialize.py', 'tools/dev_instance.py')


# --- synthetic world --------------------------------------------------------

def make_user(username, password, *, role='user', policy_version=2, platform=None,
              study_overrides=None):
    """One account with an explicit stored v2 policy and no implicit defaults."""
    user = get_user_model().objects.create_user(username, password=password)
    Principal.objects.create(user=user)
    AccountProfile.objects.create(
        user=user, role=role, must_change_password=False, policy_version=policy_version,
        platform_overrides=dict(platform or {}),
        study_overrides={str(getattr(key, 'pk', key)): sorted(value)
                         for key, value in (study_overrides or {}).items()})
    return user


@pytest.fixture
def world(db):
    owner = make_user('p03r03a_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    user = make_user('p03r03a_user', USER_PASSWORD)
    return {'owner': owner, 'instance': instance, 'user': user}


def login(client, username, password, **extra):
    return client.post('/login', {'username': username, 'password': password}, **extra)


def signed_in(username, password):
    client = Client()
    assert login(client, username, password).status_code == 302
    return client


def generated_secret(client, op, username, *, revision=None, **fields):
    response = client.post('/users', {'op': op, 'username': username,
                                      'password': OWNER_PASSWORD,
                                      'revision': str(revision if revision is not None
                                                      else accounts.instance_revision()),
                                      **fields})
    assert response.status_code == 200, response.content[:400]
    match = SECRET_RE.search(response.content.decode())
    assert match, 'one-time secret must be shown exactly once'
    return match.group(1)


# --- the rule itself --------------------------------------------------------

def test_password_policy_accepts_and_rejects_the_fixed_matrix():
    assert researcher_passwords.password_problem('Aa1!aa') is None
    assert researcher_passwords.password_problem('Aa1aaa') == researcher_passwords.WEAK_CODE
    # Each of the four classes is required; length alone never passes.
    for weak in ('aa1!aa', 'AA1!AA', 'Aaa!aa', 'Aa1aaa', 'Aa1!a', '', None, 123456, True):
        assert researcher_passwords.password_problem(weak) is not None, weak
    # A space is not a symbol, and the value is never trimmed.
    assert researcher_passwords.password_problem('Aa1 aa') is not None
    assert researcher_passwords.password_problem('      ') is not None
    assert researcher_passwords.password_problem(' Aa1!aa ') is None
    assert researcher_passwords.password_problem('Aa1!aa中文') is None
    assert researcher_passwords.password_problem('\tAa1!aa') is None


def test_generated_temporary_passwords_guarantee_all_four_classes():
    generated = {researcher_passwords.generate_temporary_password() for _ in range(256)}
    assert len(generated) == 256, 'generation must not repeat itself in a bounded sample'
    for password in generated:
        assert len(password) >= 24
        assert researcher_passwords.password_problem(password) is None
        assert not set(password) & researcher_passwords.HTML_ESCAPED
        for characters in researcher_passwords.CLASSES:
            assert any(character in characters for character in password)
    with pytest.raises(ValueError):
        researcher_passwords.generate_temporary_password(23)


def test_every_entry_file_and_template_uses_the_unified_rule():
    for relative in ENTRY_FILES:
        source = (REPO_ROOT / relative).read_text(encoding='utf-8')
        assert 'researcher_passwords' in source, relative
    accounts_source = (REPO_ROOT / 'server/core/accounts.py').read_text(encoding='utf-8')
    assert 'MINIMUM_PASSWORD' not in accounts_source
    assert 'len(password) >= 16' not in accounts_source
    for relative in TEMPLATES:
        source = (REPO_ROOT / relative).read_text(encoding='utf-8')
        assert 'minlength="6"' in source, relative
        assert 'minlength="16"' not in source, relative


# --- entry points: self change, invitation, temporary create/reset ----------

def test_self_change_enforces_the_rule_and_never_trims(world):
    client = signed_in('p03r03a_user', USER_PASSWORD)
    page = client.get('/account/password').content.decode()
    assert 'minlength="6"' in page and '至少 6 位' in page  # hint and browser gate agree with the server rule
    user = get_user_model().objects.get(username='p03r03a_user')
    stored = user.password
    for weak in WEAK_PASSWORDS:
        response = client.post('/account/password',
                               {'current': USER_PASSWORD, 'new': weak, 'confirm': weak})
        assert response.status_code == 422, weak
        user.refresh_from_db()
        assert user.password == stored, weak  # nothing was written
    assert client.post('/account/password', {'current': USER_PASSWORD, 'new': NEW_PASSWORD,
                                             'confirm': NEW_PASSWORD}).status_code == 302
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    # The exact submitted value (including surrounding spaces) is what is stored.
    assert client.post('/account/password', {'current': NEW_PASSWORD, 'new': ' Aa1!bb ',
                                             'confirm': ' Aa1!bb '}).status_code == 302
    assert login(Client(), 'p03r03a_user', ' Aa1!bb ').status_code == 302
    assert login(Client(), 'p03r03a_user', 'Aa1!bb').status_code == 200  # no silent trim


def test_invitation_activation_enforces_the_rule(world):
    owner = signed_in('p03r03a_owner', OWNER_PASSWORD)
    response = owner.post('/users', {'op': 'invite_account', 'username': 'p03r03a_invitee',
                                     'password': OWNER_PASSWORD,
                                     'revision': str(accounts.instance_revision())})
    assert response.status_code == 200
    token = INVITE_RE.search(response.content.decode()).group(1)
    anonymous = Client()
    # A dedicated source address keeps this module's attempts out of the shared
    # activation throttle window used by tests/test_phase03_accounts.py.
    address = {'REMOTE_ADDR': '203.0.113.10'}
    weak = anonymous.post('/activate-account',
                          {'token': token, 'password': 'Aa1aaa', 'confirm': 'Aa1aaa'}, **address)
    assert weak.status_code == 422
    assert not get_user_model().objects.filter(username='p03r03a_invitee').exists()
    invitation = AccountInvitation.objects.get(username='p03r03a_invitee')
    assert invitation.consumed is False
    ok = anonymous.post('/activate-account',
                        {'token': token, 'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD}, **address)
    assert ok.status_code == 200
    user = get_user_model().objects.get(username='p03r03a_invitee')
    assert user.check_password(NEW_PASSWORD)
    profile = AccountProfile.objects.get(user=user)
    assert profile.role == 'user' and profile.must_change_password is False
    replay = anonymous.post('/activate-account',
                            {'token': token, 'password': 'Aa1!cc', 'confirm': 'Aa1!cc'}, **address)
    assert replay.status_code == 403 and '激活失败' in replay.content.decode()


def test_temporary_create_and_reset_generate_policy_passwords(world):
    owner = signed_in('p03r03a_owner', OWNER_PASSWORD)
    temporary = generated_secret(owner, 'create_temp', 'p03r03a_temp')
    assert len(temporary) >= 24 and researcher_passwords.password_problem(temporary) is None
    user = get_user_model().objects.get(username='p03r03a_temp')
    assert user.check_password(temporary)
    profile = AccountProfile.objects.get(user=user)
    assert profile.must_change_password is True
    client = signed_in('p03r03a_temp', temporary)
    assert client.get('/')['Location'] == '/account/password'
    weak = client.post('/account/password', {'current': temporary, 'new': 'Aa1aaa',
                                             'confirm': 'Aa1aaa'})
    assert weak.status_code == 422
    assert AccountProfile.objects.get(user=user).must_change_password is True
    assert client.post('/account/password', {'current': temporary, 'new': NEW_PASSWORD,
                                             'confirm': NEW_PASSWORD}).status_code == 302
    assert AccountProfile.objects.get(user=user).must_change_password is False
    reset = generated_secret(owner, 'reset_password', 'p03r03a_temp')
    assert reset != temporary and len(reset) >= 24
    assert researcher_passwords.password_problem(reset) is None
    user.refresh_from_db()
    assert user.check_password(reset) and not user.check_password(NEW_PASSWORD)
    assert AccountProfile.objects.get(user=user).must_change_password is True


def test_legacy_activate_enforces_the_rule(world):
    instance = Instance.objects.get(pk=1)
    instance.authorization_version = 1
    instance.save(update_fields=['authorization_version'])
    owner = world['owner']
    study = Study.objects.create(title='P03R03A legacy study')
    for action in ('study.view', 'study.configure', 'member.manage', 'permission.delegate'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    token = 'p03r03a-legacy-ticket'
    Invitation.objects.create(study=study, issuer=owner, username='p03r03a_legacy_member',
                              actions=['study.view'], token_hash=digest(token),
                              expires_at=timezone.now() + timedelta(hours=1))
    anonymous = Client()
    weak = anonymous.post('/activate', {'token': token, 'password': 'Aa1aaa'})
    assert weak.status_code == 422
    assert not get_user_model().objects.filter(username='p03r03a_legacy_member').exists()
    invitation = Invitation.objects.get(token_hash=digest(token))
    assert invitation.consumed is False
    ok = anonymous.post('/activate', {'token': token, 'password': NEW_PASSWORD})
    assert ok.status_code == 302 and ok['Location'] == '/login'
    member = get_user_model().objects.get(username='p03r03a_legacy_member')
    assert member.check_password(NEW_PASSWORD)
    assert sorted(Grant.objects.filter(user=member, study=study).values_list('action', flat=True)) == ['study.view']
    assert anonymous.post('/activate', {'token': token, 'password': 'Aa1!cc'}).status_code == 403


def test_existing_hashes_keep_logging_in_and_are_never_reset(world):
    legacy = make_user('p03r03a_legacy_hash', 'synthetic-test-password')
    stored = get_user_model().objects.get(pk=legacy.pk).password
    assert login(Client(), 'p03r03a_legacy_hash', 'synthetic-test-password').status_code == 302
    owner = signed_in('p03r03a_owner', OWNER_PASSWORD)
    generated_secret(owner, 'create_temp', 'p03r03a_untouched_neighbour')
    assert get_user_model().objects.get(pk=legacy.pk).password == stored
    assert login(Client(), 'p03r03a_legacy_hash', 'synthetic-test-password').status_code == 302


# --- entry points: the two initialize paths ---------------------------------

def _run_initialize(root, password, username='p03r03a_init_owner'):
    environment = {**os.environ, 'PYTHONPATH': str(REPO_ROOT / 'server'),
                   'DJANGO_SETTINGS_MODULE': 'gep.settings', 'GEP_DATA_DIR': str(root),
                   'GEP_SECRET_KEY': 'synthetic-p03r03a-secret-key'}
    return subprocess.run([sys.executable, '-m', 'django', 'initialize', '--username', username],
                          input=password + '\n', env=environment, capture_output=True, text=True,
                          cwd=REPO_ROOT, start_new_session=True)


def test_initialize_command_refuses_weak_and_accepts_the_policy(tmp_path):
    import sqlite3
    weak_root = tmp_path / 'weak-volume'
    weak = _run_initialize(weak_root, 'Aa1aaa')
    assert weak.returncode != 0
    assert 'at least 6 characters' in weak.stderr
    assert not (weak_root / 'secret').exists() and not (weak_root / 'instance').exists()
    valid_root = tmp_path / 'valid-volume'
    valid = _run_initialize(valid_root, NEW_PASSWORD)
    assert valid.returncode == 0, valid.stderr
    assert (valid_root / 'secret').exists() and (valid_root / 'instance').exists()
    with sqlite3.connect(valid_root / 'gep.sqlite3') as database:
        stored = database.execute('select password from auth_user').fetchone()[0]
        assert check_password(NEW_PASSWORD, stored)
        assert database.execute('select authorization_version from core_instance').fetchone()[0] == 2


def test_dev_instance_tool_generates_a_policy_password_in_a_sandbox(tmp_path):
    """Run the real tool from a copied tree so the repository's local_data is never touched."""
    tools = tmp_path / 'tools'
    tools.mkdir()
    (tools / 'dev_instance.py').write_bytes((REPO_ROOT / 'tools/dev_instance.py').read_bytes())
    (tmp_path / 'server').symlink_to(REPO_ROOT / 'server', target_is_directory=True)
    result = subprocess.run([sys.executable, str(tools / 'dev_instance.py')],
                            cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, 'PYTHONPATH': str(REPO_ROOT / 'server')})
    assert result.returncode == 0, result.stderr
    credentials = json.loads((tmp_path / 'local_data/dev_credentials.json').read_text())
    password = credentials['password']
    assert len(password) >= 24
    assert researcher_passwords.password_problem(password) is None
    for characters in researcher_passwords.CLASSES:
        assert any(character in password for character in characters)
    assert (tmp_path / 'local_data/instance').exists()


# --- ordinary creator: business management, no accounts, no refill ----------

def test_ordinary_creator_manages_business_but_never_accounts(world):
    user = world['user']
    client = signed_in('p03r03a_user', USER_PASSWORD)
    response = client.post('/', {'title': 'P03R03A creator study'})
    assert response.status_code == 302 and response['Location'].startswith('/studies/')
    study = Study.objects.get(title='P03R03A creator study')
    assert response['Location'] == '/studies/' + str(study.pk)
    assert study.creator_principal_id == Principal.objects.get(user=user).pk
    assert set(Grant.objects.filter(user=user, study=study).values_list('action', flat=True)) == set(STUDY_V2)
    profile = AccountProfile.objects.get(user=user)
    assert profile.study_overrides[str(study.pk)] == sorted(STUDY_V2)
    assert profile.must_change_password is False
    audit = Audit.objects.get(action='study.created')
    assert audit.actor_id == user.pk and audit.study_id == study.pk
    # Full business management on that study, including the real configure write.
    assert client.get('/studies/' + str(study.pk)).status_code == 200
    assert client.get(f'/studies/{study.pk}/participation').status_code == 200
    configured = client.post(f'/studies/{study.pk}',
                             {'op': 'configure', 'mode': 'id', 'max_sessions': '5'})
    assert configured.status_code == 302
    study.refresh_from_db()
    assert study.mode == 'id' and study.max_sessions == 5
    for action in STUDY_V2:
        assert allowed(user, study, action) is True, action
    # Instance accounts stay out of reach: no view, no lifecycle, no grants.
    assert allowed_platform(user, 'accounts.view') is False
    assert client.get('/users').status_code == 403
    for op in ('invite_account', 'create_temp', 'reset_password', 'set_role', 'matrix_preview'):
        denied = client.post('/users', {'op': op, 'username': 'p03r03a_other',
                                        'password': USER_PASSWORD, 'role': 'admin',
                                        'revision': str(accounts.instance_revision())})
        assert denied.status_code == 403, op
    assert not AccountInvitation.objects.filter(username='p03r03a_other').exists()
    assert not get_user_model().objects.filter(username='p03r03a_other').exists()
    assert not Grant.objects.filter(user__username='p03r03a_other').exists()


def test_study_create_revocation_refuses_and_never_refills_from_creator(world):
    user = world['user']
    client = signed_in('p03r03a_user', USER_PASSWORD)
    assert client.post('/', {'title': 'P03R03A creator study one'}).status_code == 302
    study = Study.objects.get(title='P03R03A creator study one')
    assert study.creator_principal_id is not None
    profile = AccountProfile.objects.get(user=user)
    # The Owner revokes study.create: the server refuses, and the entry disappears.
    profile.platform_overrides = {'study.create': False}
    profile.save(update_fields=['platform_overrides'])
    refused = client.post('/', {'title': 'P03R03A creator study two'})
    assert refused.status_code == 403
    assert not Study.objects.filter(title='P03R03A creator study two').exists()
    assert allowed_platform(user, 'study.create') is False
    assert 'name="title"' not in client.get('/').content.decode()
    # Removing one study action is never refilled from the creator fact, not even
    # by creating another study afterwards.
    overrides = dict(AccountProfile.objects.get(user=user).study_overrides)
    overrides[str(study.pk)] = ['study.view']
    profile = AccountProfile.objects.get(user=user)
    profile.study_overrides = overrides
    profile.save(update_fields=['study_overrides'])
    assert allowed(user, study, 'study.configure') is False
    denied = client.post(f'/studies/{study.pk}', {'op': 'configure', 'mode': 'id', 'max_sessions': '5'})
    assert denied.status_code == 403
    study.refresh_from_db()
    assert study.mode == 'anonymous' and study.max_sessions == 1
    profile.platform_overrides = {}
    profile.save(update_fields=['platform_overrides'])
    assert client.post('/', {'title': 'P03R03A creator study three'}).status_code == 302
    study.refresh_from_db()
    assert study.creator_principal_id is not None
    assert allowed(user, study, 'study.configure') is False
    assert allowed(user, study, 'study.view') is True


# --- real Chrome: ordinary login -> create -> business page, temp change -----

@pytest.fixture
def chrome_world(db):
    owner = make_user('p03r03a_chrome_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    user = make_user('p03r03a_chrome_user', USER_PASSWORD)
    return {'owner': owner, 'instance': instance, 'user': user}


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')
def test_actual_chrome_creator_journey_and_forced_password_change(
        live_server, chrome_world, evidence, run_chrome_tokens):
    env = dict(os.environ, GEP_TEST_BASE=live_server.url,
               GEP_OWNER_PASSWORD=OWNER_PASSWORD, GEP_USER_PASSWORD=USER_PASSWORD)
    result, reported = run_chrome_tokens(SCRIPT, env, timeout=240)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert reported == []
    observations = json.loads(re.search(r'P03R03A_OBSERVATIONS (\{.*\})', result.stdout).group(1))
    assert observations['owner_created_temporary'] is True
    assert observations['temp_secret_policy'] is True
    assert observations['created_and_entered'] is True
    assert observations['business_controls_visible'] is True
    assert observations['users_denied'] == 403
    assert observations['forced_change_redirect'] is True
    assert observations['weak_change_refused'] is True
    assert observations['change_completed'] is True
    assert observations['page_errors'] == []

    study = Study.objects.get(title='P03R03A chrome created study')
    assert study.creator_principal_id is not None
    temporary = get_user_model().objects.get(username='p03r03a_chrome_temp')
    assert temporary.check_password(NEW_PASSWORD)
    profile = AccountProfile.objects.get(user=temporary)
    assert profile.must_change_password is False and profile.auth_version == 2
    assert Audit.objects.filter(action='account.created_temporary').count() >= 1
    assert NEW_PASSWORD not in str(list(Audit.objects.values()))
    evidence('chrome_journey.json', observations)


SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD,
      userPassword=process.env.GEP_USER_PASSWORD;
const observations={page_errors:[]};
try {
  // Owner creates a temporary-password account; the one-time secret stays in
  // this process and is used directly by the temporary account's context.
  const owner=await browser.newContext();
  const ownerPage=await owner.newPage();
  ownerPage.on('pageerror',e=>observations.page_errors.push('owner:'+e.message));
  await ownerPage.goto(base+'/login');
  await ownerPage.locator('[name=username]').fill('p03r03a_chrome_owner');
  await ownerPage.locator('[name=password]').fill(ownerPassword);
  await ownerPage.getByRole('button',{name:'登录'}).click();
  await ownerPage.waitForURL(base+'/');
  await ownerPage.goto(base+'/users');
  const createForm=ownerPage.locator('form').filter({has:ownerPage.locator('[name=op][value=create_temp]')});
  await createForm.locator('[name=username]').fill('p03r03a_chrome_temp');
  await createForm.locator('[name=password]').fill(ownerPassword);
  await createForm.getByRole('button').click();
  await ownerPage.waitForLoadState('load');
  const secret=(await ownerPage.locator('[data-one-time-secret="1"] code').first().textContent()).trim();
  observations.owner_created_temporary=secret.length>0;
  observations.temp_secret_policy=secret.length>=24&&/[A-Z]/.test(secret)&&/[a-z]/.test(secret)&&/[0-9]/.test(secret)&&/[^A-Za-z0-9\s]/.test(secret);
  expect(observations.temp_secret_policy).toBe(true);

  // Ordinary user: login, create a study, land on its business page; the
  // instance account page stays refused.
  const user=await browser.newContext();
  const userPage=await user.newPage();
  userPage.on('pageerror',e=>observations.page_errors.push('user:'+e.message));
  await userPage.goto(base+'/login');
  await userPage.locator('[name=username]').fill('p03r03a_chrome_user');
  await userPage.locator('[name=password]').fill(userPassword);
  await userPage.getByRole('button',{name:'登录'}).click();
  await userPage.waitForURL(base+'/');
  await userPage.locator('[name=title]').fill('P03R03A chrome created study');
  await userPage.getByRole('button',{name:'创建'}).click();
  await userPage.waitForURL(/\/studies\/[0-9a-f-]{36}$/);
  observations.created_and_entered=true;
  const studyUrl=userPage.url();
  await userPage.goto(studyUrl+'/participation');
  observations.business_controls_visible=await userPage.locator('[data-participation-policy="1"] select[name=mode]').isVisible();
  expect(observations.business_controls_visible).toBe(true);
  const denied=await userPage.goto(base+'/users');
  observations.users_denied=denied.status();
  expect(observations.users_denied).toBe(403);

  // Temporary account: forced first-login change, weak value refused, the
  // confirmed four-class example completes the gate.
  const temporary=await browser.newContext();
  const tempPage=await temporary.newPage();
  tempPage.on('pageerror',e=>observations.page_errors.push('temp:'+e.message));
  await tempPage.goto(base+'/login');
  await tempPage.locator('[name=username]').fill('p03r03a_chrome_temp');
  await tempPage.locator('[name=password]').fill(secret);
  await tempPage.getByRole('button',{name:'登录'}).click();
  await tempPage.waitForURL(base+'/account/password');
  observations.forced_change_redirect=true;
  await tempPage.locator('[name=current]').fill(secret);
  await tempPage.locator('[name=new]').fill('Aa1aaa');
  await tempPage.locator('[name=confirm]').fill('Aa1aaa');
  await tempPage.getByRole('button',{name:'保存新密码'}).click();
  await tempPage.waitForLoadState('load');
  observations.weak_change_refused=await tempPage.locator('div.error').first().isVisible();
  expect(observations.weak_change_refused).toBe(true);
  await tempPage.locator('[name=current]').fill(secret);
  await tempPage.locator('[name=new]').fill('Aa1!aa');
  await tempPage.locator('[name=confirm]').fill('Aa1!aa');
  await tempPage.getByRole('button',{name:'保存新密码'}).click();
  await tempPage.waitForURL(base+'/');
  const home=await tempPage.goto(base+'/');
  observations.change_completed=home.status()===200;
  expect(observations.change_completed).toBe(true);
} finally {
  await browser.close();
}
console.log('P03R03A_OBSERVATIONS '+JSON.stringify(observations));
'''
