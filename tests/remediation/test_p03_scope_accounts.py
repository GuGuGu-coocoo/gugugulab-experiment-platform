"""Actual account authorization and retry boundaries found in the GUI review."""
from datetime import timedelta
import re
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from core import accounts
from core.models import AccountInvitation, AccountProfile, Instance, Principal, Study

PASSWORD = 'Synthetic-review-2026!'


def user(name, role='user', **policy):
    result = get_user_model().objects.create_user(name, password=PASSWORD)
    Principal.objects.create(user=result)
    AccountProfile.objects.create(user=result, role=role, policy_version=2, **policy)
    return result


def signed_in(actor):
    client = Client()
    client.force_login(actor)
    session = client.session
    session['gep_auth_version'] = 1
    session.save()
    return client


@pytest.fixture
def world(db):
    owner = user('scope_owner')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner, authorization_version=2)
    admin = user('scope_admin', 'admin')
    target = user('scope_target')
    study = Study.objects.create(title='Scope synthetic study')
    return owner, instance, admin, target, study


def row(page, actor):
    return next(item for item in page.context['rows'] if item['id'] == actor.pk)








@pytest.mark.parametrize('failure', ['password_weak', 'password_mismatch'])
def test_valid_invitation_can_retry_password_without_reentering_token(world, failure):
    owner, instance, *_ = world
    issued = accounts.invite_account(owner, PASSWORD, instance.governance_revision, 'invite_retry')
    client = Client()
    value, confirm = ('weakxx', 'weakxx') if failure == 'password_weak' else (PASSWORD, PASSWORD + 'x')
    page = client.post('/activate-account', {'token': issued['token'], 'password': value, 'confirm': confirm})
    assert page.status_code == 422
    assert page.context['token'] == issued['token']
    assert page['Cache-Control'] == 'no-store'
    assert 'value="' + value + '"' not in page.content.decode()
    assert not AccountInvitation.objects.get(username='invite_retry').consumed
    page = client.post('/activate-account', {'token': page.context['token'], 'password': PASSWORD, 'confirm': PASSWORD})
    assert page.status_code == 200 and page.context['done']
    assert AccountInvitation.objects.get(username='invite_retry').consumed
    page = client.post('/activate-account', {'token': issued['token'], 'password': PASSWORD, 'confirm': PASSWORD})
    assert page.status_code == 403 and page.context['token'] == ''


@pytest.mark.parametrize('state', ['expired', 'revoked', 'consumed', 'issuer_disabled', 'unknown'])
def test_invalid_invitation_is_not_rehydrated_after_password_error(world, state):
    owner, instance, *_ = world
    issued = accounts.invite_account(owner, PASSWORD, instance.governance_revision, 'invalid_retry')
    invitation = AccountInvitation.objects.get(username='invalid_retry')
    if state == 'expired':
        invitation.expires_at = timezone.now() - timedelta(seconds=1)
    elif state in ('revoked', 'consumed'):
        setattr(invitation, state, True)
    elif state == 'issuer_disabled':
        owner.is_active = False
        owner.save()
    invitation.save()
    token = 'unknown-token' if state == 'unknown' else issued['token']
    page = Client().post('/activate-account', {'token': token, 'password': 'weakxx', 'confirm': 'weakxx'})
    assert page.context['token'] == ''
    assert not get_user_model().objects.filter(username='invalid_retry').exists()


def test_invitation_retry_rechecks_issuer_current_create_authority(world):
    _, instance, admin, *_ = world
    issued = accounts.invite_account(admin, PASSWORD, instance.governance_revision, 'withdrawn_retry')
    profile = AccountProfile.objects.get(user=admin)
    profile.platform_overrides = {'accounts.create_user': False}
    profile.save()
    client = Client()
    weak = client.post('/activate-account', {'token': issued['token'], 'password': 'weakxx', 'confirm': 'weakxx'})
    assert weak.status_code == 422 and weak.context['token'] == ''
    valid = client.post('/activate-account', {'token': issued['token'], 'password': PASSWORD, 'confirm': PASSWORD})
    assert valid.status_code == 403 and valid.context['token'] == ''
    assert not AccountInvitation.objects.get(username='withdrawn_retry').consumed
    assert not get_user_model().objects.filter(username='withdrawn_retry').exists()
