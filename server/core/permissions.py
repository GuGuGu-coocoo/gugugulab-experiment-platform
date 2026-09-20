"""Preview-bound permission matrix and conflict reconciliation.

Every mutation is prepared as a :class:`PermissionPreview`: a single-use,
expiring commit identity that binds the actor, the intended operations, the
input digest and the observed governance/target state. A commit re-locks the
instance, actor and target rows, re-checks the actor password, expiration,
binding digest and authorization, then applies all changes and audits in one
atomic transaction. Nothing is partially applied and no preview token alone
grants authority; a replayed successful commit is idempotent but is still
refused once the actor is no longer authorized for that exact operation.
"""
import hashlib
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import accounts
from .access import (ACTIONS, conflicts, dominates, is_account_administrator,
                     is_instance_owner, manageable_actions)
from .models import (AccountInvitation, AccountProfile, Grant, Instance, PermissionPreview, Study)
from .protocol import Rejected, require

PREVIEW_TTL = timedelta(minutes=10)
PURGE_LIMIT = 200
MATRIX_KIND = 'matrix'
RECONCILE_KIND = 'reconcile'


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _profile(user_id):
    return AccountProfile.objects.filter(user_id=user_id).first()


def _profile_state(profile):
    return {'role': profile.role, 'revision': profile.revision, 'auth_version': profile.auth_version,
            'must_change_password': profile.must_change_password}


def _account_digest(user_ids):
    state = {}
    for user in get_user_model().objects.filter(pk__in=sorted(user_ids)).order_by('pk'):
        profile = _profile(user.pk)
        state[str(user.pk)] = {'username': user.username, 'is_active': user.is_active,
                               'profile': _profile_state(profile) if profile is not None else None}
    return state


def _grant_digest(pairs):
    normalized = {(str(user_id), str(study_id)) for user_id, study_id in pairs}
    rows = []
    for user_id, study_id, action, delegable in Grant.objects.filter(
            user_id__in={p[0] for p in pairs}, study_id__in={p[1] for p in pairs}
    ).order_by('user_id', 'study_id', 'action').values_list('user_id', 'study_id', 'action', 'delegable'):
        if (str(user_id), str(study_id)) in normalized:
            rows.append({'user': str(user_id), 'study': str(study_id), 'action': action, 'delegable': delegable})
    return rows


def _invitation_digest(usernames):
    return sorted(AccountInvitation.objects.filter(username__in=sorted(usernames), consumed=False, revoked=False,
                                                   expires_at__gt=timezone.now()).values_list('username', flat=True))


def _study_digest(study_ids):
    return {str(study.pk): {'title': study.title, 'mode': study.mode} for study in Study.objects.filter(pk__in=study_ids)}


def grant_diff(current, target):
    """Full per-action boolean state: granted flag *and* delegable flag.

    Used for both writes and secret-free audit, so a change that only flips
    ``delegable`` is still traceable even though the action list is unchanged.
    """
    actions = sorted(set(current) | set(target))
    return ({action: {'granted': action in current, 'delegable': bool(current.get(action, False))} for action in actions},
            {action: {'granted': action in target, 'delegable': bool(target.get(action, False))} for action in actions})


def _preview(actor, kind, scope, summary, errors, binding, staged, revision):
    purge_sensitive_staging()
    row = PermissionPreview.objects.create(
        actor=actor, kind=kind, scope=scope, summary=summary, errors=errors, binding=binding,
        base_revision=revision, staged=staged, expires_at=timezone.now() + PREVIEW_TTL)
    return row


def purge_sensitive_staging(now=None, limit=PURGE_LIMIT):
    """Explicit, bounded cleanup of private staged intent on expired previews.

    Consumed previews already drop ``staged`` in :func:`_finish`; this path
    clears previews that were prepared but never confirmed, so a forgotten
    roster preview cannot keep password hashes after its TTL. Rows are kept
    (so an expired confirmation still fails closed with ``preview_expired``)
    and only ``staged`` is nulled, in batches of ``limit`` rows per call.
    """
    threshold = now or timezone.now()
    stale = list(PermissionPreview.objects.filter(expires_at__lte=threshold, staged__isnull=False)
                 .order_by('expires_at', 'id').values_list('pk', flat=True)[:limit])
    for preview_id in stale:
        PermissionPreview.objects.filter(pk=preview_id, expires_at__lte=threshold).update(staged=None)
    return len(stale)


def preview_payload(row):
    """Safe view context: redacted summary, per-row errors, no staged secrets."""
    return {'preview_id': str(row.id), 'preview_kind': row.kind, 'preview_scope': row.scope,
            'preview_summary': row.summary, 'preview_errors': row.errors,
            'preview_expires_at': row.expires_at}


def pending_preview(actor, preview_id):
    """An unconsumed, unexpired preview owned by the actor, if any."""
    if not preview_id or not getattr(actor, 'pk', None):
        return None
    try:
        return PermissionPreview.objects.filter(pk=preview_id, actor_id=actor.pk,
                                                consumed=False, expires_at__gt=timezone.now()).first()
    except (ValueError, ValidationError):
        return None


def _replay_authorize(locked, row):
    """Re-check the *current* authority for the exact operation of a consumed
    preview. A stored result is only replayed while the actor could still run
    that operation now; revoked scope, an elevated target or a role change
    refuses the replay without touching any row."""
    if row.kind == MATRIX_KIND:
        summary = row.summary or {}
        user = get_user_model().objects.filter(pk=summary.get('user_id')).first()
        study = Study.objects.filter(pk=summary.get('study_id')).first()
        require(user is not None and study is not None, 'preview_invalid', 404)
        target = {action: bool(flag) for action, flag in (summary.get('target_flags') or {}).items()}
        current = {action: delegable for action, delegable in
                   Grant.objects.filter(user=user, study=study).values_list('action', 'delegable')}
        _matrix_authorize(locked, user, study, current, target)
        return
    if row.kind == RECONCILE_KIND:
        require(is_instance_owner(locked), 'owner_only', 403)
        return
    raise Rejected('preview_invalid', 404)


def _gate(actor, password, preview_id, kind, scope='', require_owner=False):
    """Lock instance -> actor -> preview; a consumed preview replays the stored
    result only while the actor still holds the operation's current authority,
    and carries no mutation."""
    require(getattr(actor, 'is_authenticated', False) and getattr(actor, 'pk', None), 'auth_required', 403)
    instance = accounts._locked_instance()
    locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
    require(locked.is_active, 'auth_required', 403)
    row = PermissionPreview.objects.select_for_update().filter(pk=preview_id, actor_id=locked.pk, kind=kind, scope=scope).first()
    require(row is not None, 'preview_invalid', 404)
    profile = _profile(locked.pk)
    require(is_instance_owner(locked) or (profile is not None and profile.role == 'admin'), 'forbidden', 403)
    require(not (profile is not None and profile.must_change_password), 'password_change_required', 403)
    require(not require_owner or is_instance_owner(locked), 'owner_only', 403)
    if row.consumed:
        _replay_authorize(locked, row)
        return instance, locked, row, True
    require(row.expires_at > timezone.now(), 'preview_expired', 409)
    require(bool(password) and check_password(password, locked.password), 'reauth_failed', 403)
    return instance, locked, row, False


def _finish(row, instance, result):
    row.consumed = True
    row.result = result
    row.staged = None
    row.save(update_fields=['consumed', 'result', 'staged'])
    accounts._bump(instance)


def _require_target(actor, target):
    """Owner row is read-only even for the Owner; nobody targets itself."""
    require(not is_instance_owner(target), 'owner_protected', 403)
    require(target.pk != actor.pk, 'self_target', 409)


# --------------------------------------------------------------------------- matrix

def matrix_rows(actor):
    """Per-account study rows for the /users matrix: visibility first, then the
    explicit actions; only studies the actor can inspect or delegate on."""
    from .gui import ACTION_LABELS
    User = get_user_model()
    owner_id = Instance.objects.get(pk=1).owner_id
    profiles = {profile.user_id: profile for profile in AccountProfile.objects.all()}
    grants = {}
    for user_id, study_id, action, delegable in Grant.objects.values_list('user_id', 'study_id', 'action', 'delegable'):
        grants.setdefault((user_id, study_id), {})[action] = delegable
    studies = list(Study.objects.order_by('title', 'id'))
    rows = []
    for user in User.objects.order_by('id'):
        entries = []
        is_owner_row = user.pk == owner_id
        own_row = user.pk == getattr(actor, 'pk', None)
        for study in studies:
            current = grants.get((user.pk, study.pk), {})
            manageable = set() if (is_owner_row or own_row) else manageable_actions(actor, study)
            if not current and not manageable:
                continue
            entries.append({'study': study, 'granted': bool(current), 'current': current,
                            'visibility': 'study.view' in current,
                            'summary': _describe(current), 'editable': bool(manageable) and not is_owner_row and not own_row,
                            'checks': [{'action': action, 'label': ACTION_LABELS.get(action, action),
                                        'checked': action in current, 'delegable': bool(current.get(action, False))}
                                       for action in sorted(manageable) if action != 'study.view']})
        rows.append({'id': user.pk, 'username': user.username, 'is_owner': is_owner_row,
                     'role': profiles[user.pk].role if user.pk in profiles else 'user',
                     'studies': entries})
    return rows


def _describe(current):
    if not current:
        return '（无显式动作）'
    return '、'.join(sorted(current))


def _matrix_submission(post, actor):
    """Rebuild the submitted (user, study, visibility, actions) set from a form POST."""
    require(post.get('user_id'), 'user_id')
    require(post.get('study_id'), 'study_id')
    user = get_user_model().objects.filter(pk=post['user_id']).first()
    require(user is not None, 'account_missing', 404)
    study = Study.objects.filter(pk=post['study_id']).first()
    require(study is not None, 'study_missing', 404)
    visibility = post.get('visibility') == '1'
    actions = {}
    for key in post.keys():
        if key.startswith('action:') and post.get(key) == '1':
            code = key.split(':', 1)[1]
            require(code in ACTIONS, 'action')
            actions[code] = post.get('delegable:' + code) == '1'
    if not visibility and actions:
        raise Rejected('visibility_children_contradiction', 409)
    return user, study, visibility, actions


def _matrix_target(visibility, actions, current):
    target = dict(actions)
    if visibility:
        target['study.view'] = bool(current.get('study.view', False))
    else:
        target = {}
    return target


def _matrix_binding(actor, instance, user, study, target):
    return _digest({
        'kind': MATRIX_KIND, 'actor': actor.pk, 'instance': str(instance.pk), 'revision': instance.governance_revision,
        'user': _account_digest({user.pk}), 'study': _study_digest({study.pk}),
        'grants': _grant_digest({(user.pk, study.pk)}), 'target': {action: target[action] for action in sorted(target)},
    })


def _matrix_authorize(actor, target_user, study, current, target):
    """Non-Owner actors are bounded by effective AND delegable actions and by the
    study-scoped takeover comparison over the affected subjects."""
    _require_target(actor, target_user)
    if not is_instance_owner(actor):
        require(dominates(actor, target_user), 'higher_privilege_target', 403)
        manageable = manageable_actions(actor, study)
        require(set(current) | set(target) <= manageable, 'delegation_forbidden', 403)


def preview_matrix(actor, post):
    require(is_account_administrator(actor), 'forbidden', 403)
    user, study, visibility, actions = _matrix_submission(post, actor)
    with transaction.atomic():
        instance = accounts._locked_instance()
        locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
        require(locked.is_active, 'auth_required', 403)
        profile = _profile(locked.pk)
        require(is_instance_owner(locked) or (profile is not None and profile.role == 'admin'), 'forbidden', 403)
        current = {action: delegable for action, delegable in
                   Grant.objects.filter(user=user, study=study).values_list('action', 'delegable')}
        target = _matrix_target(visibility, actions, current)
        _matrix_authorize(locked, user, study, current, target)
        require(current != target, 'no_change', 409)
        add = sorted(set(target) - set(current))
        remove = sorted(set(current) - set(target))
        delegable_changed = sorted(action for action in set(current) & set(target) if current[action] != target[action])
        summary = {'username': user.username, 'user_id': user.pk, 'study': study.title, 'study_id': str(study.pk),
                   'add': add, 'remove': remove, 'delegable_changed': delegable_changed,
                   'target': sorted(target), 'target_flags': {action: bool(flag) for action, flag in target.items()}}
        row = _preview(locked, MATRIX_KIND, str(study.pk), summary, [], _matrix_binding(locked, instance, user, study, target),
                       {'user_id': user.pk, 'study_id': str(study.pk), 'target': target}, instance.governance_revision)
    return row


def commit_matrix(actor, password, preview_id):
    require(bool(preview_id), 'preview_required', 400)
    with transaction.atomic():
        instance, locked, row, replay = _gate(actor, password, preview_id, MATRIX_KIND, scope=row_scope(preview_id, MATRIX_KIND))
        if replay:
            return row.result
        staged = row.staged
        user = get_user_model().objects.select_for_update().get(pk=staged['user_id'])
        study = Study.objects.get(pk=staged['study_id'])
        target = {action: bool(flag) for action, flag in staged['target'].items()}
        require(_matrix_binding(locked, instance, user, study, target) == row.binding, 'preview_stale', 409)
        current = {action: delegable for action, delegable in
                   Grant.objects.filter(user=user, study=study).values_list('action', 'delegable')}
        _matrix_authorize(locked, user, study, current, target)
        require(current != target, 'preview_stale', 409)
        for action in sorted(current):
            if action not in target:
                Grant.objects.filter(user=user, study=study, action=action).delete()
        for action in sorted(target):
            if current.get(action) != target[action]:
                Grant.objects.update_or_create(user=user, study=study, action=action,
                                               defaults={'delegable': target[action]})
        before, after = grant_diff(current, target)
        accounts.audit(locked, 'permission.matrix_changed', f'{user.pk}:{study.pk}',
                       before={'actions': before}, after={'actions': after})
        result = {'username': user.username, 'study': study.title, 'actions': sorted(target)}
        _finish(row, instance, result)
    return result


# --------------------------------------------------------------------------- reconcile

def _conflict_snapshot():
    rows = conflicts()
    pairs = {(user_id, study_id) for user_id, study_id, _ in rows}
    return {'conflicts': [{'user': str(user_id), 'study': str(study_id), 'actions': actions}
                          for user_id, study_id, actions in rows],
            'grants': _grant_digest(pairs)}


def _reconcile_binding(actor, instance, choice, snapshot):
    return _digest({'kind': RECONCILE_KIND, 'actor': actor.pk, 'revision': instance.governance_revision,
                    'choice': choice, 'snapshot': snapshot})


def preview_reconcile(actor, choice):
    require(is_account_administrator(actor), 'forbidden', 403)
    require(is_instance_owner(actor), 'owner_only', 403)
    require(choice in ('grant_view', 'remove_conflicting'), 'choice')
    with transaction.atomic():
        instance = accounts._locked_instance()
        locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
        require(locked.is_active, 'auth_required', 403)
        snapshot = _conflict_snapshot()
        require(bool(snapshot['conflicts']), 'no_conflicts', 409)
        user_ids = {row['user'] for row in snapshot['conflicts']}
        names = {str(user.pk): user.username for user in get_user_model().objects.filter(pk__in=user_ids)}
        titles = {str(study.pk): study.title for study in Study.objects.filter(pk__in={row['study'] for row in snapshot['conflicts']})}
        summary = {'choice': choice, 'rows': [{'username': names.get(row['user'], row['user']),
                                               'study': titles.get(row['study'], row['study']),
                                               'study_id': row['study'], 'actions': row['actions']}
                                              for row in snapshot['conflicts']]}
        row = _preview(locked, RECONCILE_KIND, '', summary, [], _reconcile_binding(locked, instance, choice, snapshot),
                       {'choice': choice}, instance.governance_revision)
    return row


def commit_reconcile(actor, password, preview_id):
    require(bool(preview_id), 'preview_required', 400)
    with transaction.atomic():
        instance, locked, row, replay = _gate(actor, password, preview_id, RECONCILE_KIND, require_owner=True)
        if replay:
            return row.result
        choice = row.staged['choice']
        require(choice in ('grant_view', 'remove_conflicting'), 'choice')
        snapshot = _conflict_snapshot()
        require(_reconcile_binding(locked, instance, choice, snapshot) == row.binding, 'preview_stale', 409)
        require(bool(snapshot['conflicts']), 'preview_stale', 409)
        resolved = accounts.apply_reconcile(locked, choice, snapshot['conflicts'])
        _finish(row, instance, resolved)
    return resolved


def row_scope(preview_id, kind):
    row = PermissionPreview.objects.filter(pk=preview_id, kind=kind).values_list('scope', flat=True).first()
    return row or ''
