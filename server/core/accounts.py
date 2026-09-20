"""Instance account lifecycle.

Every governance mutation runs in one atomic transaction that re-reads the actor
role, validates the actor password and the instance governance revision, applies
the change and writes a secret-free structured audit. A missing profile is
treated as an ordinary account and never grants Admin.
"""
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.utils import timezone

from .access import ROLES, dominates, effective_role, is_instance_owner
from .models import AccountInvitation, AccountProfile, Audit, Grant, Instance
from .protocol import Rejected, require
from .services import digest

INVITATION_TTL = timedelta(hours=24)
MINIMUM_PASSWORD = 16


def instance_revision():
    return Instance.objects.get(pk=1).governance_revision


def audit(actor, action, target, before=None, after=None, study=None):
    Audit.objects.create(study=study, actor=actor, action=action, target=str(target), before=before, after=after)


def _rand_password():
    return secrets.token_urlsafe(18)


def _locked_instance():
    return Instance.objects.select_for_update().get(pk=1)


def _revision_matches(raw, current):
    try:
        return int(raw) == current
    except (TypeError, ValueError):
        return False


def _reauth(actor, password, expected_revision):
    """Reauthenticate the actor from freshly locked rows inside the transaction.

    Lock order is always Instance -> actor User -> target User/invitation, and
    every check uses the locked database state, never the caller's cached object.
    """
    require(getattr(actor, 'is_authenticated', False) and getattr(actor, 'pk', None), 'auth_required', 403)
    instance = _locked_instance()
    locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
    require(locked.is_active, 'auth_required', 403)
    require(bool(password) and check_password(password, locked.password), 'reauth_failed', 403)
    require(_revision_matches(expected_revision, instance.governance_revision), 'revision_conflict', 409)
    profile = AccountProfile.objects.select_for_update().filter(user_id=locked.pk).first()
    require(not (profile is not None and profile.must_change_password), 'password_change_required', 403)
    require(is_instance_owner(locked) or (profile is not None and profile.role == 'admin'), 'forbidden', 403)
    return instance, locked, profile


def _bump(instance):
    instance.governance_revision += 1
    instance.save(update_fields=['governance_revision'])


def _profile_locked(user, role='user', must_change=False):
    profile = AccountProfile.objects.select_for_update().filter(user_id=user.pk).first()
    if profile is None:
        profile = AccountProfile.objects.create(user=user, role=role, must_change_password=must_change)
    return profile


def _mutable_target(actor, target, takeover=False):
    """Owner row is read-only and nobody targets itself. Lifecycle operations on
    non-Owner accounts (including peer Admins) require the study takeover guard;
    appoint/demote Admin stays Owner-only in set_account_role."""
    require(target.pk != actor.pk, 'self_target', 409)
    require(not is_instance_owner(target), 'owner_protected', 403)
    if takeover:
        require(dominates(actor, target), 'higher_privilege_target', 403)


def _clean_username(username):
    username = (username or '').strip()
    require(0 < len(username) <= 150, 'username')
    return username


def _account_state(user, profile):
    return {'username': user.username, 'is_active': user.is_active, 'role': profile.role if profile else 'user', 'must_change_password': profile.must_change_password if profile else False, 'auth_version': profile.auth_version if profile else 1}


def invite_account(actor, password, expected_revision, username, role='user'):
    username = _clean_username(username)
    require(role in ROLES, 'role')
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        require(role == 'user' or is_instance_owner(actor), 'admin_appointment_owner_only', 403)
        require(not get_user_model().objects.filter(username=username).exists(), 'account_exists', 409)
        require(not AccountInvitation.objects.filter(username=username, consumed=False, revoked=False, expires_at__gt=timezone.now()).exists(), 'invitation_active', 409)
        token = secrets.token_urlsafe(32)
        invitation = AccountInvitation.objects.create(issuer=actor, username=username, role=role, token_hash=digest(token), expires_at=timezone.now() + INVITATION_TTL)
        audit(actor, 'account.invite_issued', invitation.id, after={'username': username, 'role': role})
        _bump(instance)
    return {'token': token, 'username': username, 'role': role, 'expires_at': invitation.expires_at}


def create_temporary_account(actor, password, expected_revision, username):
    username = _clean_username(username)
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        require(not get_user_model().objects.filter(username=username).exists(), 'account_exists', 409)
        temporary = _rand_password()
        user = get_user_model().objects.create_user(username, password=temporary)
        AccountProfile.objects.create(user=user, role='user', must_change_password=True, auth_version=1, revision=0)
        audit(actor, 'account.created_temporary', user.pk, after={'username': username, 'role': 'user', 'must_change_password': True})
        _bump(instance)
    return {'username': username, 'temporary_password': temporary}


def reset_temporary_password(actor, password, expected_revision, target_id):
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        target = get_user_model().objects.select_for_update().get(pk=target_id)
        _mutable_target(actor, target, takeover=True)
        profile = _profile_locked(target)
        before = _account_state(target, profile)
        temporary = _rand_password()
        target.set_password(temporary)
        target.save(update_fields=['password'])
        profile.must_change_password = True
        profile.auth_version += 1
        profile.revision += 1
        profile.save(update_fields=['must_change_password', 'auth_version', 'revision'])
        audit(actor, 'account.temporary_password_reset', target.pk, before=before, after=_account_state(target, profile))
        _bump(instance)
    return {'username': target.username, 'temporary_password': temporary}


def apply_account_active(actor, target, profile, active):
    """Apply an activity transition on locked rows; caller owns the transaction."""
    active = bool(active)
    require(target.is_active != active, 'no_change', 409)
    before = _account_state(target, profile)
    target.is_active = active
    target.save(update_fields=['is_active'])
    if not active:
        profile.auth_version += 1
    profile.revision += 1
    profile.save(update_fields=['auth_version', 'revision'])
    audit(actor, 'account.enabled' if active else 'account.disabled', target.pk, before=before, after=_account_state(target, profile))


def set_account_active(actor, password, expected_revision, target_id, active):
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        target = get_user_model().objects.select_for_update().get(pk=target_id)
        # Reactivation restores privileged access too, so the takeover guard always applies.
        _mutable_target(actor, target, takeover=True)
        profile = _profile_locked(target)
        apply_account_active(actor, target, profile, active)
        _bump(instance)
    return {'username': target.username, 'is_active': bool(active)}


def set_account_role(actor, password, expected_revision, target_id, role):
    require(role in ROLES, 'role')
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        require(is_instance_owner(actor), 'owner_only', 403)
        target = get_user_model().objects.select_for_update().get(pk=target_id)
        require(target.pk != actor.pk, 'self_target', 409)
        require(not is_instance_owner(target), 'owner_protected', 403)
        profile = _profile_locked(target)
        require(profile.role != role, 'no_change', 409)
        before = _account_state(target, profile)
        profile.role = role
        profile.revision += 1
        profile.save(update_fields=['role', 'revision'])
        audit(actor, 'account.role_changed', target.pk, before=before, after=_account_state(target, profile))
        _bump(instance)
    return {'username': target.username, 'role': role}


def change_own_password(user, current, new, confirm):
    require(getattr(user, 'is_authenticated', False) and getattr(user, 'pk', None), 'auth_required', 403)
    require(bool(new) and len(new) >= MINIMUM_PASSWORD, 'password_too_short')
    require(new == confirm, 'password_mismatch')
    with transaction.atomic():
        locked = get_user_model().objects.select_for_update().get(pk=user.pk)
        require(locked.is_active, 'auth_required', 403)
        require(check_password(current or '', locked.password), 'current_password_wrong', 403)
        # Reusing the current temporary password must not clear the forced-change gate.
        require(not check_password(new, locked.password), 'password_reused', 409)
        profile = _profile_locked(locked)
        before = _account_state(locked, profile)
        locked.set_password(new)
        locked.save(update_fields=['password'])
        profile.must_change_password = False
        profile.auth_version += 1
        profile.revision += 1
        profile.save(update_fields=['must_change_password', 'auth_version', 'revision'])
        audit(locked, 'account.password_changed', locked.pk, before=before, after=_account_state(locked, profile))
        return profile


def activate_account(token, password, confirm):
    """Single-use activation; every token failure is one generic non-enumerating error.

    The issuer's *current* active status and current authority to issue the
    invitation's role are re-checked before the account is created or the token
    is consumed, under the same lock order as governance mutations.
    """
    require(bool(token), 'activation_failed', 403)
    require(bool(password) and len(password) >= MINIMUM_PASSWORD, 'password_too_short')
    require(password == confirm, 'password_mismatch')
    with transaction.atomic():
        instance = _locked_instance()
        pending = AccountInvitation.objects.filter(token_hash=digest(token)).first()
        require(pending is not None, 'activation_failed', 403)
        issuer = get_user_model().objects.select_for_update().get(pk=pending.issuer_id)
        invitation = AccountInvitation.objects.select_for_update().filter(pk=pending.pk).first()
        require(invitation is not None and not invitation.consumed and not invitation.revoked and invitation.expires_at > timezone.now(), 'activation_failed', 403)
        require(invitation.role in ROLES, 'activation_failed', 403)
        require(issuer.is_active and (is_instance_owner(issuer) or effective_role(issuer) == 'admin'), 'activation_failed', 403)
        require(invitation.role == 'user' or is_instance_owner(issuer), 'activation_failed', 403)
        require(not get_user_model().objects.filter(username=invitation.username).exists(), 'activation_failed', 403)
        user = get_user_model().objects.create_user(invitation.username, password=password)
        AccountProfile.objects.create(user=user, role=invitation.role, must_change_password=False, auth_version=1, revision=0)
        invitation.consumed = True
        invitation.save(update_fields=['consumed'])
        audit(issuer, 'account.invitation_activated', user.pk, after={'username': invitation.username, 'role': invitation.role})
        _bump(instance)
    return user


def revoke_invitation(actor, password, expected_revision, invitation_id):
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        invitation = AccountInvitation.objects.select_for_update().get(pk=invitation_id)
        require(not invitation.consumed, 'invitation_inactive', 409)
        require(not invitation.revoked, 'invitation_inactive', 409)
        require(invitation.role == 'user' or is_instance_owner(actor), 'admin_appointment_owner_only', 403)
        invitation.revoked = True
        invitation.save(update_fields=['revoked'])
        audit(actor, 'account.invite_revoked', invitation.id, before={'revoked': False}, after={'revoked': True, 'username': invitation.username})
        _bump(instance)
    return {'username': invitation.username}


def apply_reconcile(actor, choice, rows):
    """Apply an Owner-confirmed reconciliation of view-less grants on locked rows.

    ``rows`` is the conflict snapshot bound by the preview; the caller owns the
    transaction and has already verified the binding digest. Each row is audited
    with its explicit before/after actions and the choice.
    """
    require(choice in ('grant_view', 'remove_conflicting'), 'choice')
    resolved = []
    for entry in rows:
        user_id, study_id, actions = entry['user'], entry['study'], entry['actions']
        if choice == 'grant_view':
            Grant.objects.get_or_create(user_id=user_id, study_id=study_id, action='study.view', defaults={'delegable': False})
        else:
            Grant.objects.filter(user_id=user_id, study_id=study_id).exclude(action='study.view').delete()
        after = sorted(Grant.objects.filter(user_id=user_id, study_id=study_id).values_list('action', flat=True))
        audit(actor, 'access.conflict_resolved', f'{user_id}:{study_id}', before={'actions': actions, 'choice': choice}, after={'actions': after})
        resolved.append({'user_id': str(user_id), 'study_id': str(study_id), 'actions': after})
    return resolved
