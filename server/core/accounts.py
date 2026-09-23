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

from . import access, researcher_passwords
from .access import (ROLES, allowed_platform, effective_role, ensure_principal,
                     is_instance_owner)
from .models import (AccountInvitation, AccountProfile, Audit, Grant, Instance,
                     Invitation, PermissionPreview, Principal, RecoveryCode,
                     RecoveryPermit)
from .protocol import Rejected, require
from .services import digest

INVITATION_TTL = timedelta(hours=24)

# The finite v2 platform action(s) every lifecycle operation requires. Ordinary
# accounts are managed with ``accounts.manage_user``; an Admin target needs
# ``accounts.manage_admin`` (and ``accounts.delete_admin`` to delete). Role
# promotion, Admin invitation and withdrawing a pending Admin invitation need
# ``accounts.create_admin``; demotion is Admin maintenance. The same table drives
# the importers and the matrix.
LIFECYCLE_PLATFORM_ACTIONS = {
    'create_user': ('accounts.create_user',),
    'create_admin': ('accounts.create_admin',),
    'manage_user': ('accounts.manage_user',),
    'manage_admin': ('accounts.manage_admin',),
    'promote': ('accounts.create_admin',),
    'demote': ('accounts.manage_admin',),
    'revoke_user': ('accounts.manage_user',),
    'revoke_admin': ('accounts.create_admin',),
    'delete_user': ('accounts.manage_user',),
    'delete_admin': ('accounts.manage_admin', 'accounts.delete_admin'),
}
OWNER_ONLY_LIFECYCLE = ('create_admin', 'promote', 'demote', 'revoke_admin')


def lifecycle_platform_actions(operation, target_role='user'):
    """The exact v2 platform action(s) one lifecycle operation requires."""
    if operation in ('manage', 'delete'):
        operation = f'{operation}_{"admin" if target_role == "admin" else "user"}'
    try:
        return LIFECYCLE_PLATFORM_ACTIONS[operation]
    except KeyError:
        raise ValueError(operation) from None


def _require_lifecycle_platform(instance, actor, operation, *, target_role='user'):
    """Reject unless the actor's current platform policy covers the operation.

    Version 1 keeps the legacy capability map in :func:`access.allowed_platform`
    (Owner or Admin for ordinary accounts and per-target Admin maintenance,
    Owner-only for appointment/demotion and Admin invitations); the per-target
    study dominance guard stays in ``_mutable_target``. Version 2 reads the
    canonical stored policy, so an ordinary Admin's default cannot manage Admins
    and the Owner-controlled switches are needed explicitly.
    """
    version = access.authorization_version(instance)
    require(version in (1, 2), 'forbidden', 403)
    required = lifecycle_platform_actions(operation, target_role)
    code = 'admin_appointment_owner_only' if operation in OWNER_ONLY_LIFECYCLE else 'forbidden'
    require(all(allowed_platform(actor, action, instance=instance) for action in required), code, 403)


def instance_revision():
    return Instance.objects.get(pk=1).governance_revision


def audit(actor, action, target, before=None, after=None, study=None):
    """Write one secret-free audit with the actor's stable subject.

    The principal is created on demand (migration 0010 backfilled existing
    accounts), so an audit written now still names the stable subject after the
    account is deleted and its username may be reused.
    """
    principal = ensure_principal(actor) if getattr(actor, 'pk', None) else None
    Audit.objects.create(study=study, actor=actor, actor_principal=principal, action=action,
                         target=str(target), before=before, after=after)


def _rand_password():
    """One-time URL-safe secret; invitation tokens minted by the importers use it."""
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


def _mutable_target(actor, target, *, instance=None, takeover=False, after=None):
    """Owner row is read-only and nobody targets itself. Lifecycle operations on
    non-Owner accounts (including peer Admins) require the whole-account
    takeover guard; appoint/demote Admin stays Owner-only in set_account_role
    (v1) or needs create_admin/manage_admin (v2)."""
    require(target.pk != actor.pk, 'self_target', 409)
    require(not is_instance_owner(target), 'owner_protected', 403)
    if takeover:
        require(access.takeover_allowed(actor, target, instance=instance, after=after),
                'higher_privilege_target', 403)


def _clean_username(username):
    username = (username or '').strip()
    require(0 < len(username) <= 150, 'username')
    return username


def _account_state(user, profile):
    return {'username': user.username, 'is_active': user.is_active, 'role': profile.role if profile else 'user', 'must_change_password': profile.must_change_password if profile else False, 'auth_version': profile.auth_version if profile else 1}


def _v2_policy_snapshot(user, *, instance=None):
    """Full before/after policy sets for v2 governance audits; None on v1."""
    if access.authorization_version(instance) != 2:
        return None
    try:
        return access.policy_snapshot(access.canonical_policy(user, instance=instance))
    except ValueError:
        return None


def invite_account(actor, password, expected_revision, username, role='user'):
    username = _clean_username(username)
    require(role in ROLES, 'role')
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        _require_lifecycle_platform(instance, actor, 'create_admin' if role == 'admin' else 'create_user')
        require(not get_user_model().objects.filter(username=username).exists(), 'account_exists', 409)
        require(not AccountInvitation.objects.filter(username=username, consumed=False, revoked=False, expires_at__gt=timezone.now()).exists(), 'invitation_active', 409)
        bound = None
        if (role == 'admin' and access.authorization_version(instance) == 2
                and not is_instance_owner(actor)):
            # A non-Owner issuer may only open a *restricted* Admin: the bound
            # fixes every study and the future upper bound, and the issuer must
            # dominate exactly that policy.
            bound = access.conservative_admin_bound(actor, instance=instance)
            require(access.takeover_creation_allowed(actor, access.bound_subject_policy(bound),
                                                     instance=instance),
                    'higher_privilege_target', 403)
        token = secrets.token_urlsafe(32)
        invitation = AccountInvitation.objects.create(issuer=actor, username=username, role=role,
                                                      token_hash=digest(token), bound_policy=bound,
                                                      expires_at=timezone.now() + INVITATION_TTL)
        after = {'username': username, 'role': role}
        if bound is not None:
            after['bound_policy'] = bound
        audit(actor, 'account.invite_issued', invitation.id, after=after)
        _bump(instance)
    return {'token': token, 'username': username, 'role': role, 'expires_at': invitation.expires_at,
            'bound_policy': bound}


def create_temporary_account(actor, password, expected_revision, username):
    username = _clean_username(username)
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        _require_lifecycle_platform(instance, actor, 'create_user')
        require(not get_user_model().objects.filter(username=username).exists(), 'account_exists', 409)
        temporary = researcher_passwords.generate_temporary_password()
        user = get_user_model().objects.create_user(username, password=temporary)
        AccountProfile.objects.create(user=user, role='user', must_change_password=True, auth_version=1, revision=0)
        ensure_principal(user)
        audit(actor, 'account.created_temporary', user.pk, after={'username': username, 'role': 'user', 'must_change_password': True})
        _bump(instance)
    return {'username': username, 'temporary_password': temporary}


def reset_temporary_password(actor, password, expected_revision, target_id):
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        target = get_user_model().objects.select_for_update().get(pk=target_id)
        _mutable_target(actor, target, instance=instance)
        profile = _profile_locked(target)
        _require_lifecycle_platform(instance, actor, 'manage', target_role=profile.role)
        require(access.takeover_allowed(actor, target, instance=instance),
                'higher_privilege_target', 403)
        before = _account_state(target, profile)
        before['policy'] = _v2_policy_snapshot(target, instance=instance)
        temporary = researcher_passwords.generate_temporary_password()
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
        _mutable_target(actor, target, instance=instance)
        profile = _profile_locked(target)
        _require_lifecycle_platform(instance, actor, 'manage', target_role=profile.role)
        require(access.takeover_allowed(actor, target, instance=instance),
                'higher_privilege_target', 403)
        apply_account_active(actor, target, profile, active)
        _bump(instance)
    return {'username': target.username, 'is_active': bool(active)}


def set_account_role(actor, password, expected_revision, target_id, role):
    require(role in ROLES, 'role')
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        version = access.authorization_version(instance)
        if version == 1:
            # Legacy boundary: appointment/demotion stays Owner-only.
            require(is_instance_owner(actor), 'owner_only', 403)
        target = get_user_model().objects.select_for_update().get(pk=target_id)
        require(target.pk != actor.pk, 'self_target', 409)
        require(not is_instance_owner(target), 'owner_protected', 403)
        profile = _profile_locked(target)
        require(profile.role != role, 'no_change', 409)
        after = None
        before_policy = None
        if version != 1:
            # Exact v2 route: an unknown stored version is refused instead of
            # being read through the v2 checks.
            require(version == 2, 'unsupported_version', 409)
            _require_lifecycle_platform(instance, actor, 'promote' if role == 'admin' else 'demote')
            try:
                before_policy = access.canonical_policy(target, instance=instance)
                after = access.project_policy(before_policy, role=role)
            except ValueError:
                raise Rejected('unsupported_policy_version', 409) from None
        require(access.takeover_allowed(actor, target, instance=instance, after=after),
                'higher_privilege_target', 403)
        before = _account_state(target, profile)
        if before_policy is not None:
            before['policy'] = access.policy_snapshot(before_policy)
        profile.role = role
        profile.revision += 1
        profile.save(update_fields=['role', 'revision'])
        after_state = _account_state(target, profile)
        if after is not None:
            after_state['policy'] = access.policy_snapshot(after)
        audit(actor, 'account.role_changed', target.pk, before=before, after=after_state)
        _bump(instance)
    return {'username': target.username, 'role': role}


def change_own_password(user, current, new, confirm):
    require(getattr(user, 'is_authenticated', False) and getattr(user, 'pk', None), 'auth_required', 403)
    researcher_passwords.require_acceptable(new)
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
    is consumed, under the same lock order as governance mutations. On a v2
    instance a stored finite Admin bound is re-validated and the issuer's
    current policy must still dominate it, so a narrowed issuer can never
    activate an Admin invitation it could no longer create.
    """
    require(bool(token), 'activation_failed', 403)
    researcher_passwords.require_acceptable(password)
    require(password == confirm, 'password_mismatch')
    with transaction.atomic():
        instance = _locked_instance()
        pending = AccountInvitation.objects.filter(token_hash=digest(token)).first()
        require(pending is not None, 'activation_failed', 403)
        issuer = get_user_model().objects.select_for_update().get(pk=pending.issuer_id)
        invitation = AccountInvitation.objects.select_for_update().filter(pk=pending.pk).first()
        require(invitation is not None and not invitation.consumed and not invitation.revoked and invitation.expires_at > timezone.now(), 'activation_failed', 403)
        require(invitation.role in ROLES, 'activation_failed', 403)
        version = access.authorization_version(instance)
        bound_fields = {}
        if version == 1:
            require(issuer.is_active and (is_instance_owner(issuer) or effective_role(issuer) == 'admin'), 'activation_failed', 403)
            require(invitation.role == 'user' or is_instance_owner(issuer), 'activation_failed', 403)
        else:
            # Exact v2 route: an unknown stored version refuses here instead of
            # being silently treated as v2.
            require(version == 2, 'unsupported_version', 409)
            require(issuer.is_active, 'activation_failed', 403)
            operation = 'create_admin' if invitation.role == 'admin' else 'create_user'
            require(all(allowed_platform(issuer, action, instance=instance)
                        for action in lifecycle_platform_actions(operation)), 'activation_failed', 403)
            if invitation.role == 'admin' and invitation.bound_policy is not None:
                try:
                    normalized = access.validate_account_bound(invitation.bound_policy)
                except ValueError:
                    raise Rejected('activation_failed', 403) from None
                require(normalized['role'] == invitation.role, 'activation_failed', 403)
                require(access.takeover_bound_allowed(issuer, invitation.bound_policy, instance=instance),
                        'activation_failed', 403)
                bound_fields = {'platform_overrides': normalized['platform_overrides'],
                                'study_overrides': normalized['study_overrides'],
                                'future_study_actions': normalized['future_study_actions']}
        require(not get_user_model().objects.filter(username=invitation.username).exists(), 'activation_failed', 403)
        user = get_user_model().objects.create_user(invitation.username, password=password)
        AccountProfile.objects.create(user=user, role=invitation.role, must_change_password=False,
                                      auth_version=1, revision=0, **bound_fields)
        ensure_principal(user)
        invitation.consumed = True
        invitation.save(update_fields=['consumed'])
        audit(issuer, 'account.invitation_activated', user.pk,
              after={'username': invitation.username, 'role': invitation.role,
                     # The invitation UUID is the independent application identity:
                     # it is recorded with the created account so a later
                     # same-name application can never be confused with this one.
                     'application': str(invitation.pk),
                     'principal': str(ensure_principal(user).pk),
                     'bound_policy': bound_fields or None})
        _bump(instance)
    return user


def revoke_invitation(actor, password, expected_revision, invitation_id):
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        invitation = AccountInvitation.objects.select_for_update().get(pk=invitation_id)
        require(not invitation.consumed, 'invitation_inactive', 409)
        require(not invitation.revoked, 'invitation_inactive', 409)
        _require_lifecycle_platform(instance, actor,
                                    'revoke_admin' if invitation.role == 'admin' else 'revoke_user')
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


# --------------------------------------------------------------------------- permanent deletion (R03)

def deletion_after_policy(before_policy):
    """The empty whole-account policy a permanent deletion projects to.

    Deletion removes every platform and study permission, so the whole-account
    takeover comparison must dominate the target before *and* after. In
    particular a non-Owner actor can never remove one of the Owner-controlled
    Admin switches, even while holding both switches themselves.
    """
    return access.project_policy(before_policy, role='user', platform_overrides={},
                                 study_overrides={}, future_study_actions=None)


def _preview_operates_on(preview, target_key, username):
    """Does one preview summary/staged payload name this account?

    Only explicit account keys count: the stable UUID under ``user``/
    ``user_id``/``target_user``, or the username under ``username``. A roster
    code that merely equals a username is never an account operation.
    """
    def scan(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'username' and item == username:
                    return True
                if key in ('user', 'user_id', 'target_user') and str(item) == target_key:
                    return True
                if scan(item):
                    return True
        elif isinstance(value, (list, tuple)):
            return any(scan(item) for item in value)
        return False
    return scan(preview.summary) or scan(preview.staged)


def invalidate_target_previews(target):
    """Drop the target's own previews and expire previews that operate on it.

    The target's own rows are removed (the account reference is PROTECT and a
    deleted account has no previews at all). Every other actor's unconsumed
    preview that names this account is expired and its private staged intent
    cleared, so a later commit can never apply a staged operation to a username
    that no longer belongs to that subject. Returns (deleted, invalidated).
    """
    deleted, _ = PermissionPreview.objects.filter(actor_id=target.pk).delete()
    now = timezone.now()
    target_key, username = str(target.pk), target.username
    invalidated = 0
    for preview in PermissionPreview.objects.filter(consumed=False).iterator():
        if preview.staged is None and preview.expires_at <= now:
            continue
        if _preview_operates_on(preview, target_key, username):
            PermissionPreview.objects.filter(pk=preview.pk).update(staged=None, expires_at=now)
            invalidated += 1
    return deleted, invalidated


def _delete_account_sessions(user_id):
    """Remove every live Django session authenticated as this account.

    Session rows are opaque and carry no user column, so each live row is
    decoded and matched by its stored ``_auth_user_id``. The account therefore
    cannot keep a usable session through a request already in flight.
    """
    from django.contrib.sessions.models import Session as DjangoSession
    key = str(user_id)
    removed = 0
    for row in DjangoSession.objects.filter(expire_date__gt=timezone.now()).iterator():
        if row.get_decoded().get('_auth_user_id') == key:
            row.delete()
            removed += 1
    return removed


def delete_account(actor, password, expected_revision, target_id, confirm_username=None):
    """Permanently delete one non-Owner account (R03; R00 contract section C).

    One atomic transaction: re-authenticate the actor and check the governance
    revision, lock the target, require the exact platform action(s) for the
    target role and the whole-account takeover guard over the target's stored
    policy projected to the empty policy, then require the typed username
    confirmation, invalidate every credential bound to the account (issued
    account/study invitations, recovery permits and codes; unconsumed previews
    and previews operating on the target; pending username-bound invitations),
    delete the login sessions, mark the stable Principal deleted without ever
    removing it, write the audit and delete the User (profile and grants
    cascade). Studies, participants, sessions, events and earlier audits stay
    untouched. An audit failure raises inside the same transaction and rolls
    the whole deletion, credential invalidation and session removal back.
    """
    with transaction.atomic():
        instance, actor, _ = _reauth(actor, password, expected_revision)
        target = get_user_model().objects.select_for_update().get(pk=target_id)
        _mutable_target(actor, target, instance=instance)
        profile = AccountProfile.objects.select_for_update().filter(user_id=target.pk).first()
        role = profile.role if profile is not None else 'user'
        require(role in ROLES, 'role')
        _require_lifecycle_platform(instance, actor, 'delete', target_role=role)
        version = access.authorization_version(instance)
        require(version in (1, 2), 'forbidden', 403)
        before = _account_state(target, profile)
        after_policy = None
        if version == 2:
            try:
                before_policy = access.canonical_policy(target, instance=instance)
            except ValueError:
                raise Rejected('unsupported_policy_version', 409) from None
            before['policy'] = access.policy_snapshot(before_policy)
            after_policy = deletion_after_policy(before_policy)
        require(access.takeover_allowed(actor, target, instance=instance, after=after_policy),
                'higher_privilege_target', 403)
        require((confirm_username or '') == target.username, 'delete_confirm_mismatch', 409)

        principal = ensure_principal(target)
        principal.deleted_at = timezone.now()
        principal.user = None
        principal.save(update_fields=['deleted_at', 'user'])

        issued = {
            'account_invitations': AccountInvitation.objects.filter(issuer_id=target.pk).delete()[0],
            'study_invitations': Invitation.objects.filter(issuer_id=target.pk).delete()[0],
            'recovery_permits': RecoveryPermit.objects.filter(issuer_id=target.pk).delete()[0],
            'recovery_codes': RecoveryCode.objects.filter(issuer_id=target.pk).delete()[0],
        }
        # Every pending credential bound to the released username *or* to the
        # target's stable Principal is revoked before the username can be reused.
        # A version-2 invitation for this account carries the Principal binding;
        # a legacy username-only row is caught by the username filter.
        pending = {
            'account_invitations_revoked': AccountInvitation.objects.filter(
                username=target.username, consumed=False, revoked=False).update(revoked=True),
            'study_invitations_revoked': Invitation.objects.filter(
                username=target.username, consumed=False, revoked=False).update(revoked=True),
            'study_invitations_principal_revoked': Invitation.objects.filter(
                principal_id=principal.pk, consumed=False, revoked=False).update(revoked=True),
        }
        # Historical audit rows written by a path that did not store the stable
        # subject are rebound from the exact, still-present actor foreign key --
        # never from a username guess -- before the account row is removed.
        audits_rebound = Audit.objects.filter(actor_id=target.pk,
                                              actor_principal__isnull=True).update(
            actor_principal_id=principal.pk)
        previews_deleted, previews_invalidated = invalidate_target_previews(target)
        sessions_removed = _delete_account_sessions(target.pk)
        after = {'username': target.username, 'role': role, 'deleted': True,
                 'principal': str(principal.pk),
                 'principal_deleted_at': principal.deleted_at.isoformat(),
                 'issued_credentials_removed': issued,
                 'pending_credentials_revoked': pending,
                 'audits_rebound': audits_rebound,
                 'previews_deleted': previews_deleted,
                 'previews_invalidated': previews_invalidated,
                 'sessions_removed': sessions_removed}
        audit(actor, 'account.deleted', target.pk, before=before, after=after)
        target.delete()
        _bump(instance)
    return {'username': after['username'], 'principal': after['principal']}


def audit_actor_display(entry):
    """Stable subject label for one audit row.

    A deleted account is shown as ``已删除账号 <principal uuid>``; a username is
    only shown while its account still exists, so a reused username never
    re-labels an older audit row.
    """
    if getattr(entry, 'actor_id', None):
        actor = getattr(entry, 'actor', None)
        if actor is None:
            actor = get_user_model().objects.filter(pk=entry.actor_id).first()
        if actor is not None:
            return actor.username
    principal_id = getattr(entry, 'actor_principal_id', None)
    if principal_id:
        principal = getattr(entry, 'actor_principal', None)
        if principal is None or getattr(principal, 'pk', None) is None:
            principal = Principal.objects.filter(pk=principal_id).first()
        if principal is not None and principal.deleted_at is not None:
            return f'已删除账号 {principal_id}'
        return f'账号 {principal_id}'
    return ''


def recent_account_audits(limit=10):
    """Bounded account-lifecycle audit view rows with stable actor labels.

    Only ``account.*`` events are shown, newest first, and the stable principal
    label is rendered through :func:`audit_actor_display`, so the page never
    maps an old event onto an account that reused the username.
    """
    rows = (Audit.objects.filter(action__startswith='account.')
            .select_related('actor', 'actor_principal').order_by('-created_at', '-id')[:limit])
    return [{'action': entry.action, 'target': str(entry.target),
             'created_at': entry.created_at, 'actor': audit_actor_display(entry),
             'actor_principal': str(entry.actor_principal_id or ''),
             'actor_deleted': bool(entry.actor_id is None and entry.actor_principal_id)}
            for entry in rows]
