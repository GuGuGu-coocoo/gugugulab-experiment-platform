"""Canonical policy binding rejects foreign and forged identity facts."""
import uuid
import pytest
from django.contrib.auth import get_user_model
from core import access, authorization
from core.models import AccountProfile, Grant, Instance, Study

OWNER_PASSWORD = 'synthetic-p03r02br-owner-password'

def make_user(username, **kwargs):
    return get_user_model().objects.create_user(username, password=OWNER_PASSWORD, **kwargs)

def make_instance(owner, version=1):
    return Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                   authorization_version=version)

def make_study(title):
    return Study.objects.create(title=title)

def grant(user, study, *actions, delegable=False):
    for action in actions:
        Grant.objects.create(user=user, study=study, action=action, delegable=delegable)

def world(*, study_count=1, prefix='p03r02br'):
    owner = make_user(f'{prefix}_owner')
    instance = make_instance(owner, version=1)
    AccountProfile.objects.create(user=owner, role='user')
    admin = make_user(f'{prefix}_admin')
    AccountProfile.objects.create(user=admin, role='admin')
    studies = [make_study(f'P03R02BR study {index}') for index in range(study_count)]
    return owner, instance, admin, studies

def test_canonical_policy_rejects_foreign_profile_and_forged_owner(db, evidence):
    owner, instance, admin, (study,) = world()
    ordinary = make_user('p03r02br_ordinary')
    ordinary_profile = AccountProfile.objects.create(user=ordinary, role='user')
    admin_profile = AccountProfile.objects.get(user=admin)
    grant(admin, study, 'study.view')

    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(ordinary, instance=instance, profile=admin_profile)
    assert info.value.code == 'profile_subject_mismatch'
    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(ordinary, instance=instance,
                                profile=AccountProfile(user_id=ordinary.pk, role='admin', pk=admin_profile.pk))
    assert info.value.code == 'profile_subject_mismatch'

    # Modified in-memory rows are not authorization facts: the stored values win.
    admin_profile.role = 'user'
    admin_profile.study_overrides = {}
    assert access.canonical_policy(admin, instance=instance).role == 'admin'
    ordinary_profile.role = 'admin'
    assert access.canonical_policy(ordinary, instance=instance).role == 'user'

    # A forged in-memory Owner is re-read from storage in both directions.
    forged = Instance.objects.get(pk=instance.pk)
    forged.owner_id = admin.pk
    assert access.canonical_policy(admin, instance=forged).is_instance_owner is False
    assert access.canonical_policy(owner, instance=forged).is_instance_owner is True
    owner.is_active = False
    assert access.canonical_policy(owner, instance=instance).active is True

    ghost = get_user_model()(username='p03r02br_ghost')
    with pytest.raises(authorization.PolicyError) as info:
        access.canonical_policy(ghost, instance=instance)
    assert info.value.code == 'auth_required'
    evidence('canonical_binding.json', {'foreign_profile': 'profile_subject_mismatch',
                                        'forged_owner': False, 'ghost': 'auth_required'})
