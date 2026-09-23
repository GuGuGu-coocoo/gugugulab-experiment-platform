"""P03R03AR evidence: the U07 researcher-password regression closure.

The expected side is the R00 contract (2026-09-23, section C), requirement U07
and the checkpoint decision (``p03r03a_review.md``), never the implementation
under test:

- the Chinese and English ``password_weak`` text states the same rule at every
  account entry (>= 6 characters, one ASCII uppercase letter, lowercase letter,
  digit and visible punctuation each; a space is not a symbol and the value is
  never trimmed);
- a weak password is refused with 422 and writes nothing: the stored hash stays
  byte-identical, the invitation stays unconsumed, no account is created and the
  same one-time token still activates afterwards;
- the legacy ``/activate`` route refuses the same code atomically and resolves
  the same zh/en text through the shared study message map;
- the acceptance tools that set a researcher password through a real entry point
  reuse the one pure module (Windows kit) or carry fixed four-class synthetic
  values (both package verifiers) instead of a weak token generator;
- one real Chrome journey: the activation page shows the specific Chinese rule,
  the refused weak attempt consumes nothing, and the confirmed ``Aa1!aa``-class
  value activates.

U07 fixture change recorded here: the old ``tests/test_phase03_accounts_browser.py``
values lacked an uppercase letter and a digit; that file now uses fixed
four-class synthetic values and keeps every scenario, assertion and wait, while
the Owner keeps the pre-existing weak hash. Participant/roster passwords, keys,
invitation tokens and stored legacy hashes stay untouched.

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r03ar/<UTC>-<random>/`` root.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory
from django.utils import timezone

from core import accounts, gui, gui_accounts, researcher_passwords, ui
from core.models import (AccountInvitation, AccountProfile, Grant, Instance,
                         Invitation, Principal, Study)
from core.services import digest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / 'tools'
OWNER_PASSWORD = 'synthetic-p03r03ar-owner-password'  # pre-existing hash, never re-set
USER_PASSWORD = 'synthetic-p03r03ar-user-password'    # pre-existing hash, never re-set
WEAK_PASSWORD = 'Aa1aaa'    # confirmed rejected example (U07)
STRONG_PASSWORD = 'Aa1!aa'  # confirmed accepted example (U07)
INVITE_RE = re.compile(r'data-one-time-invitation="1".*?data-invitation-link="[^"]*/activate-account\?token=([^"&]+)"', re.S)
OBSERVATIONS_RE = re.compile(r'P03R03AR_OBSERVATIONS (\{.*\})')
ZH_RULE = gui_accounts.MESSAGES['password_weak']
EN_RULE = ui.ERRORS_EN['password_weak']
# Fixed four-class fixtures the account browser suite now sets as new passwords.
BROWSER_FIXTURES = ('Synthetic-browser-admin-2026!', 'Synthetic-browser-user-2026!',
                    'Synthetic-browser-invitee-2026!')


# --- synthetic world ---------------------------------------------------------

def make_user(username, password, *, role='user'):
    """One account with an explicit stored v2 policy and no implicit defaults."""
    user = get_user_model().objects.create_user(username, password=password)
    Principal.objects.create(user=user)
    AccountProfile.objects.create(user=user, role=role, must_change_password=False,
                                  policy_version=2, platform_overrides={},
                                  study_overrides={})
    return user


@pytest.fixture
def world(db):
    owner = make_user('p03r03ar_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    user = make_user('p03r03ar_user', USER_PASSWORD)
    return {'owner': owner, 'instance': instance, 'user': user}


def signed_in(username, password, lang=None):
    client = Client()
    assert client.post('/login', {'username': username, 'password': password}).status_code == 302
    if lang is not None:
        client.cookies['gep_lang'] = lang
    return client


def invitation_for(owner_client, username):
    response = owner_client.post('/users', {'op': 'invite_account', 'username': username,
                                            'password': OWNER_PASSWORD,
                                            'revision': str(accounts.instance_revision())})
    assert response.status_code == 200, response.content[:400]
    match = INVITE_RE.search(response.content.decode())
    assert match, 'one-time invitation link must be shown exactly once'
    return match.group(1)


# --- the unified zh/en text --------------------------------------------------

def test_password_weak_text_is_specific_and_consistent_at_every_entry():
    assert ZH_RULE == gui.STUDY_MESSAGES['password_weak']
    for token in ('至少 6 位', 'ASCII', '大写', '小写', '数字', '可见标点', '空格', '首尾空白'):
        assert token in ZH_RULE, token
    assert '至少 16' not in ZH_RULE and '16 个字符' not in ZH_RULE
    for token in ('at least 6', 'ASCII', 'uppercase', 'lowercase', 'digit',
                  'visible ASCII symbol', 'space does not count', 'never trimmed'):
        assert token in EN_RULE, token
    assert '16' not in EN_RULE
    # The page hints shown next to both password fields carry the same rule.
    for key in ('password_new', 'activate_hint'):
        for lang in ('zh', 'en'):
            assert '6' in ui.STRINGS[key][lang], (key, lang)
        assert 'ASCII' in ui.STRINGS[key]['zh'] and '可见标点' in ui.STRINGS[key]['zh']
        assert 'ASCII' in ui.STRINGS[key]['en']
    # The legacy /activate entry resolves the same code from the shared map (zh
    # default and English preference) instead of the generic message.
    request = RequestFactory().get('/activate')
    assert gui.study_error_message(request, 'password_weak') == ZH_RULE
    request.COOKIES = {'gep_lang': 'en'}
    assert gui.study_error_message(request, 'password_weak') == EN_RULE


# --- real account entries: refused, atomic, localized ------------------------

def test_self_change_refuses_weak_with_localized_messages_and_no_write(world):
    zh_client = signed_in('p03r03ar_user', USER_PASSWORD)
    en_client = signed_in('p03r03ar_user', USER_PASSWORD, lang='en')
    stored = get_user_model().objects.get(username='p03r03ar_user').password
    for client, expected in ((zh_client, ZH_RULE), (en_client, EN_RULE)):
        response = client.post('/account/password', {'current': USER_PASSWORD,
                                                     'new': WEAK_PASSWORD,
                                                     'confirm': WEAK_PASSWORD})
        assert response.status_code == 422
        body = response.content.decode()
        assert expected in body
        assert '至少 16' not in body and 'at least 16' not in body
        generic = ui.tr('en' if expected == EN_RULE else 'zh', 'error_invalid_request')
        assert generic not in body
        assert get_user_model().objects.get(username='p03r03ar_user').password == stored
    completed = zh_client.post('/account/password', {'current': USER_PASSWORD,
                                                     'new': STRONG_PASSWORD,
                                                     'confirm': STRONG_PASSWORD})
    assert completed.status_code == 302
    user = get_user_model().objects.get(username='p03r03ar_user')
    assert user.check_password(STRONG_PASSWORD) and not user.check_password(WEAK_PASSWORD)


def test_account_activation_refuses_weak_atomically_and_keeps_the_token(world):
    owner = signed_in('p03r03ar_owner', OWNER_PASSWORD)
    token = invitation_for(owner, 'p03r03ar_invitee')
    address = {'REMOTE_ADDR': '203.0.113.21'}  # own activation throttle window
    for lang, expected in ((None, ZH_RULE), ('en', EN_RULE)):
        anonymous = Client()
        if lang is not None:
            anonymous.cookies['gep_lang'] = lang
        weak = anonymous.post('/activate-account', {'token': token, 'password': WEAK_PASSWORD,
                                                    'confirm': WEAK_PASSWORD}, **address)
        assert weak.status_code == 422
        body = weak.content.decode()
        assert expected in body
        assert '至少 16' not in body and 'at least 16' not in body
        assert not get_user_model().objects.filter(username='p03r03ar_invitee').exists()
        invitation = AccountInvitation.objects.get(username='p03r03ar_invitee')
        assert invitation.consumed is False and invitation.revoked is False
    ok = Client().post('/activate-account', {'token': token, 'password': STRONG_PASSWORD,
                                             'confirm': STRONG_PASSWORD}, **address)
    assert ok.status_code == 200
    user = get_user_model().objects.get(username='p03r03ar_invitee')
    assert user.check_password(STRONG_PASSWORD) and not user.check_password(WEAK_PASSWORD)
    assert AccountProfile.objects.get(user=user).must_change_password is False
    assert AccountInvitation.objects.get(username='p03r03ar_invitee').consumed is True
    replay = Client().post('/activate-account', {'token': token, 'password': 'Aa1!cc',
                                                 'confirm': 'Aa1!cc'}, **address)
    assert replay.status_code == 403 and '激活失败' in replay.content.decode()


def test_legacy_activate_refuses_weak_atomically(world):
    instance = Instance.objects.get(pk=1)
    instance.authorization_version = 1
    instance.save(update_fields=['authorization_version'])
    owner = world['owner']
    study = Study.objects.create(title='P03R03AR legacy study')
    for action in ('study.view', 'study.configure', 'member.manage', 'permission.delegate'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    token = 'p03r03ar-legacy-ticket'
    Invitation.objects.create(study=study, issuer=owner, username='p03r03ar_legacy_member',
                              actions=['study.view'], token_hash=digest(token),
                              expires_at=timezone.now() + timedelta(hours=1))
    weak = Client().post('/activate', {'token': token, 'password': WEAK_PASSWORD})
    assert weak.status_code == 422
    assert weak.json()['code'] == researcher_passwords.WEAK_CODE
    assert not get_user_model().objects.filter(username='p03r03ar_legacy_member').exists()
    assert Invitation.objects.get(token_hash=digest(token)).consumed is False
    ok = Client().post('/activate', {'token': token, 'password': STRONG_PASSWORD})
    assert ok.status_code == 302 and ok['Location'] == '/login'
    member = get_user_model().objects.get(username='p03r03ar_legacy_member')
    assert member.check_password(STRONG_PASSWORD) and not member.check_password(WEAK_PASSWORD)
    assert list(Grant.objects.filter(user=member, study=study).values_list('action', flat=True)) == ['study.view']
    assert Client().post('/activate', {'token': token, 'password': 'Aa1!cc'}).status_code == 403


def test_account_browser_fixtures_are_four_class_and_owner_hash_stays_weak():
    source = (REPO_ROOT / 'tests/test_phase03_accounts_browser.py').read_text(encoding='utf-8')
    for name, value in zip(('GEP_ADMIN_PASSWORD', 'GEP_USER_PASSWORD', 'GEP_INVITEE_PASSWORD'),
                           BROWSER_FIXTURES):
        match = re.search(r"%s='([^']+)'" % name, source)
        assert match, name
        assert match.group(1) == value, name
        assert researcher_passwords.password_problem(value) is None, name
        assert 'synthetic-browser-' not in value
    assert "GEP_OWNER_PASSWORD='synthetic-test-password'" in source


# --- the acceptance tools' real generation path ------------------------------









# --- real Chrome: activation feedback and token single use -------------------

@pytest.fixture
def chrome_world(db):
    owner = make_user('p03r03ar_chrome_owner', OWNER_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    return {'owner': owner, 'instance': instance}


@pytest.mark.skipif(os.environ.get('GEP_T17_BROWSER') != '1',
                    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')
def test_actual_chrome_activation_shows_the_chinese_rule_and_keeps_the_token(
        live_server, chrome_world, evidence, run_chrome_tokens):
    env = dict(os.environ, GEP_TEST_BASE=live_server.url, GEP_OWNER_PASSWORD=OWNER_PASSWORD,
               GEP_EXPECTED_ZH_RULE=ZH_RULE)
    result, reported = run_chrome_tokens(SCRIPT, env, timeout=240)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert len(reported) == 1 and len(reported[0]) > 20
    observations = json.loads(OBSERVATIONS_RE.search(result.stdout).group(1))
    assert observations['weak_status'] == 422
    assert observations['weak_error_text'] == ZH_RULE
    assert '至少 6 位' in observations['hint_text'] and 'ASCII' in observations['hint_text']
    assert observations['activated'] is True
    assert observations['users_denied'] == 403
    assert observations['page_errors'] == []
    assert reported[0] not in result.stdout and reported[0] not in result.stderr
    invitation = AccountInvitation.objects.get(username='p03r03ar_chrome_invitee')
    assert invitation.consumed is True
    user = get_user_model().objects.get(username='p03r03ar_chrome_invitee')
    assert user.check_password(STRONG_PASSWORD) and not user.check_password(WEAK_PASSWORD)
    profile = AccountProfile.objects.get(user=user)
    assert profile.role == 'user' and profile.must_change_password is False
    evidence('chrome_password_feedback.json',
             {'observations': observations, 'token_length': len(reported[0])})


SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
import fs from 'node:fs';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, ownerPassword=process.env.GEP_OWNER_PASSWORD;
const observations={page_errors:[]};
try {
  // Owner creates one invitation through the real /users form.
  const owner=await browser.newContext();
  const page=await owner.newPage();
  page.on('pageerror',e=>observations.page_errors.push('owner:'+e.message));
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('p03r03ar_chrome_owner');
  await page.locator('[name=password]').fill(ownerPassword);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/users');
  const inviteForm=page.locator('form').filter({has:page.locator('[name=op][value=invite_account]')});
  await inviteForm.locator('[name=username]').fill('p03r03ar_chrome_invitee');
  await inviteForm.locator('[name=password]').fill(ownerPassword);
  await inviteForm.getByRole('button',{name:'生成邀请（本人设置密码）'}).click();
  await page.waitForLoadState('load');
  const link=await page.locator('[data-one-time-invitation] a[data-invitation-link]').getAttribute('href');
  const token=new URL(link).searchParams.get('token');
  if(process.env.GEP_TOKEN_FD){fs.writeSync(Number(process.env.GEP_TOKEN_FD),token+'\n');}
  await owner.close();

  // The invitee turns the form in with a weak value: the page answers 422 and
  // shows the specific Chinese four-class rule, and the same token still works.
  const invitee=await browser.newContext();
  const inviteePage=await invitee.newPage();
  inviteePage.on('pageerror',e=>observations.page_errors.push('invitee:'+e.message));
  let activationStatus=0;
  inviteePage.on('response',r=>{if(r.url()===link&&r.request().method()==='POST'){activationStatus=r.status();}});
  let response=await inviteePage.goto(link);
  expect(response.status()).toBe(200);
  observations.hint_text=(await inviteePage.locator('label',{hasText:'新密码'}).first().innerText()).trim();
  await inviteePage.locator('[name=password]').fill('Aa1aaa');
  await inviteePage.locator('[name=confirm]').fill('Aa1aaa');
  await inviteePage.getByRole('button',{name:'激活账号'}).click();
  await inviteePage.waitForLoadState('load');
  const error=inviteePage.locator('div.error[role=alert]');
  await expect(error).toBeVisible();
  observations.weak_status=activationStatus;
  observations.weak_error_text=(await error.innerText()).trim();
  expect(observations.weak_status).toBe(422);
  expect(observations.weak_error_text).toBe(process.env.GEP_EXPECTED_ZH_RULE);
  expect(await inviteePage.getByText('已激活').count()).toBe(0);

  // Re-open the same one-time link and use the confirmed four-class value.
  response=await inviteePage.goto(link);
  expect(response.status()).toBe(200);
  await inviteePage.locator('[name=password]').fill('Aa1!aa');
  await inviteePage.locator('[name=confirm]').fill('Aa1!aa');
  await inviteePage.getByRole('button',{name:'激活账号'}).click();
  await expect(inviteePage.getByText('已激活')).toBeVisible();
  observations.activated=true;

  // The new account signs in with that value and stays an ordinary user.
  await inviteePage.goto(base+'/login');
  await inviteePage.locator('[name=username]').fill('p03r03ar_chrome_invitee');
  await inviteePage.locator('[name=password]').fill('Aa1!aa');
  await inviteePage.getByRole('button',{name:'登录'}).click();
  await inviteePage.waitForURL(base+'/');
  const denied=await inviteePage.goto(base+'/users');
  observations.users_denied=denied.status();
  expect(observations.users_denied).toBe(403);
  await invitee.close();
  expect(observations.page_errors).toEqual([]);
} finally {await browser.close();}
console.log('P03R03AR_OBSERVATIONS '+JSON.stringify(observations));
'''
