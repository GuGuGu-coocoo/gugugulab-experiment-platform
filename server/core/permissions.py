"""Preview-bound permission matrix and conflict reconciliation.

Every mutation is prepared as a :class:`PermissionPreview`: a single-use,
expiring commit identity that binds the actor, the intended operations, the
input digest and the observed governance/target state. A commit re-locks the
instance, actor and target rows, re-checks the actor password, expiration,
binding digest and authorization, then applies all changes and audits in one
atomic transaction. Nothing is partially applied and no preview token alone
grants authority; a replayed successful commit is idempotent but is still
refused once the actor is no longer authorized for that exact operation.

Bounded XLSX imports live in :mod:`core.importers` and share this preview core.
"""
import hashlib
import json
import math
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from . import accounts, ui
from .access import (ACTIONS, conflicts, delegable_authority, dominates,
                     is_account_administrator, is_instance_owner, manageable_actions)
from . import access
from .models import (AccountInvitation, AccountProfile, Grant, Instance, PermissionPreview, Principal, Study)
from .protocol import Rejected, require

PREVIEW_TTL = timedelta(minutes=10)
PURGE_LIMIT = 200
MATRIX_PAGE_SIZE = 20
CONFLICT_PAGE_SIZE = 20
STUDY_CHOICE_LIMIT = 50
MATRIX_KIND = 'matrix'
RECONCILE_KIND = 'reconcile'
USERS_KIND = 'users_import'
ROSTER_KIND = 'roster_import'
PLATFORM_KIND = 'platform'
# The kinds the unified confirmation dialog can ask about. Any other kind is
# refused by :func:`preview_status` instead of exposing stored object data.
DIALOG_STATUS_KINDS = (MATRIX_KIND, PLATFORM_KIND)

# Display-only source labels for one account/study effective selection. They
# explain where the final checkboxes come from; they never authorize anything.
SOURCE_KEYS = {
    'owner': 'users_source_owner',
    'override': 'users_source_override',
    'role_default': 'users_source_role_default',
    'grants': 'users_source_grants',
    'none': 'users_source_none',
}

PLATFORM_LABEL_KEYS = {
    'study.create': 'platform_study_create',
    'accounts.view': 'platform_accounts_view',
    'accounts.create_user': 'platform_accounts_create_user',
    'accounts.manage_user': 'platform_accounts_manage_user',
    'accounts.create_admin': 'platform_accounts_create_admin',
    'accounts.manage_admin': 'platform_accounts_manage_admin',
    'accounts.delete_admin': 'platform_accounts_delete_admin',
}


def platform_label(action, lang='zh'):
    """Bilingual label for one finite platform action (code never changes)."""
    return ui.tr(lang, PLATFORM_LABEL_KEYS.get(action, action))


STUDY_LABEL_KEYS = {'study.delete': 'study_action_delete'}


def study_action_label(action, lang='zh'):
    """Bilingual label for one study action; unknown codes stay the raw code.

    ``study.delete`` is a v2-only catalog entry, so its label lives in the shared
    string table instead of the historical v1 label map.
    """
    if action in STUDY_LABEL_KEYS:
        return ui.tr(lang, STUDY_LABEL_KEYS[action])
    from .gui import action_label
    return action_label(action, lang)


def study_source(policy, is_owner_row, study_key):
    """Where a target's stored selection for one study comes from (display).

    Owner rows always render the fixed Owner catalog; an explicit complete
    override wins; otherwise an Admin follows the role default/future bound and
    an ordinary account follows its explicit grants. This is presentation only
    and reads the same canonical policy the kernel already computed.
    """
    if is_owner_row:
        return 'owner'
    if policy is None:
        return 'none'
    if study_key in policy.study_overrides:
        return 'override'
    if policy.role == 'admin':
        return 'role_default'
    if policy.grants.get(study_key):
        return 'grants'
    return 'none'


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _profile(user_id):
    return AccountProfile.objects.filter(user_id=user_id).first()


def _target(username):
    user = get_user_model().objects.filter(username=(username or '').strip()).first()
    require(user is not None, 'account_missing', 404)
    return user


def _profile_state(profile):
    return {'role': profile.role, 'revision': profile.revision, 'auth_version': profile.auth_version,
            'must_change_password': profile.must_change_password,
            'policy_version': profile.policy_version,
            'platform_overrides': profile.platform_overrides or {},
            'study_overrides': profile.study_overrides or {},
            'future_study_actions': profile.future_study_actions}


def _account_digest(user_ids):
    state = {}
    principals = {principal.user_id: principal for principal in
                  Principal.objects.filter(user_id__in=sorted(user_ids))}
    for user in get_user_model().objects.filter(pk__in=sorted(user_ids)).order_by('pk'):
        profile = _profile(user.pk)
        principal = principals.get(user.pk)
        state[str(user.pk)] = {'username': user.username, 'is_active': user.is_active,
                               'principal': str(principal.pk) if principal is not None else None,
                               'principal_deleted_at': (principal.deleted_at.isoformat()
                                                        if principal is not None and principal.deleted_at else None),
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


def preview_payload(row, lang='zh'):
    """Safe view context: redacted summary, per-row errors, no staged secrets.

    Row errors carry a Chinese message and its English counterpart; the active
    language is resolved here so templates stay language-agnostic.
    """
    errors = [{'row': item.get('row'), 'code': item.get('code'),
               'message': (item.get('message_en') or item.get('message')) if lang == 'en' else item.get('message')}
              for item in (row.errors or [])]
    return {'preview_id': str(row.id), 'preview_kind': row.kind, 'preview_scope': row.scope,
            'preview_summary': row.summary, 'preview_errors': errors,
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


def preview_status(actor, preview_id):
    """Minimal read-only state of one own preview for the confirmation dialog.

    The unified confirmation dialog calls this after a submit whose network
    result is unknown. It reports only the state of the actor's own preview of
    the kinds that dialog can confirm: a consumed preview proves the operation
    already ran (the server replays it idempotently and never writes twice),
    while a pending preview proves nothing about the original request beyond
    "not finished at query time". No stored result, staged intent, study name,
    account name, identity or token is returned: a caller that needs object data
    must re-authorize a fresh object read. Revoked scope, a downgraded or
    disabled actor, a must-change account, another actor's preview and an
    unknown kind all fail closed here.
    """
    require(getattr(actor, 'is_authenticated', False) and getattr(actor, 'pk', None), 'auth_required', 403)
    require(bool(preview_id), 'preview_required', 400)
    # Same governance gate as every permission entry: the check never becomes a
    # way to read past a lost ``accounts.view`` or a forced password change.
    require(access.allowed_platform(actor, 'accounts.view'), 'forbidden', 403)
    profile = _profile(actor.pk)
    require(not (profile is not None and profile.must_change_password), 'password_change_required', 403)
    try:
        row = PermissionPreview.objects.filter(pk=preview_id, actor_id=actor.pk).first()
    except (ValueError, ValidationError):
        row = None
    require(row is not None, 'preview_invalid', 404)
    require(row.kind in DIALOG_STATUS_KINDS, 'preview_invalid', 404)
    if row.consumed:
        state = 'consumed'
    elif row.expires_at > timezone.now():
        state = 'pending'
    else:
        state = 'expired'
    return {'preview_id': str(row.id), 'kind': row.kind, 'state': state,
            'expires_at': row.expires_at.isoformat()}


def preview_retry_authorized(actor, row):
    """Whether a refused confirmation may re-render this still-pending preview.

    The error page of a refused commit shows the actor their own pending preview
    again so a recoverable refusal (for example a mistyped own password) can be
    retried. That re-render must never become a way to read stored object data
    (account or study names) after the current authority for the operation was
    lost, so it re-uses the same current-authority re-check as a consumed replay
    and fails closed on any refusal. It writes nothing.
    """
    try:
        require(access.allowed_platform(actor, 'accounts.view'), 'forbidden', 403)
        profile = _profile(actor.pk)
        require(not (profile is not None and profile.must_change_password), 'password_change_required', 403)
        from .governance_migration import ENABLEMENT_KIND
        if row.kind == ENABLEMENT_KIND:
            # Owner-only preview (the enablement entry is Owner-only); the Owner
            # fact cannot be revoked, so the owner check is the authority.
            require(is_instance_owner(actor), 'owner_only', 403)
        else:
            _replay_authorize(actor, row)
        return True
    except Rejected:
        return False


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
        # Version-routed current state: v2 re-checks the stored complete
        # selection through the kernel instead of the legacy grant rows.
        current = _matrix_current(access.authorization_version(), user, study)
        _matrix_authorize(locked, user, study, current, target)
        return
    if row.kind == RECONCILE_KIND:
        require(is_instance_owner(locked), 'owner_only', 403)
        return
    from .importers import replay_authorize
    replay_authorize(locked, row)


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
    # One version-routed governance gate: v1 keeps the Owner/Admin boundary,
    # v2 requires the finite ``accounts.view`` platform action from the stored
    # policy; the operation-specific authority is re-checked below.
    require(access.allowed_platform(locked, 'accounts.view', instance=instance), 'forbidden', 403)
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

def _pager_links(param, page, pages, params=None, window=5):
    """Numbered links for one bounded table that keep the active filters.

    ``param`` is the page parameter of this table (``page`` for accounts/matrix,
    ``cpage`` for conflicts); every other non-empty filter is carried over so
    paging never silently drops the user's search.
    """
    if pages <= 1:
        return []
    start = max(1, page - window)
    end = min(pages, page + window)
    links = []
    for number in range(start, end + 1):
        query = {key: value for key, value in (params or {}).items() if value and key != param}
        if number > 1:
            query[param] = str(number)
        links.append({'number': number, 'current': number == page,
                      'url': '/users' + ('?' + urlencode(query) if query else '')})
    return links


def _matrix_study_scope(actor):
    """The actor's editable (effective AND delegable) action map per study.

    Reading and delegating are separate concerns: Owner and every Admin may
    inspect the account permission metadata of the whole instance, so the study
    catalog is never filtered for inspection. Delegation stays bounded: only the
    Owner governs every action, while a non-Owner Admin's editable map (and thus
    every rendered form) is computed once from their own effective AND delegable
    grants. Inspection itself grants nothing: study/data/identity APIs
    re-authorize independently and forged preview/commit submissions stay 403.
    """
    if is_instance_owner(actor):
        return True, {}
    manageable = {}
    for study_id, action in delegable_authority(actor):
        manageable.setdefault(study_id, set()).add(action)
    return False, manageable


def _matrix_page_v2(actor, search='', page=1, lang='zh'):
    """v2 matrix page built from the canonical kernel for every cell.

    Only studies the actor can actually see are loaded, so a title the actor has
    no ``study.view`` on is never rendered (a restricted count is reported
    instead). A target Admin's future/default actions are included through the
    same kernel, so the Admin default is never missed; each cell is editable in
    exactly the actor's assignable set and only while the whole-account takeover
    comparison still holds.
    """
    User = get_user_model()
    instance = Instance.objects.get(pk=1)
    owner_id = instance.owner_id
    search = (search or '').strip()
    try:
        actor_policy = access.canonical_policy(actor)
    except ValueError:
        raise Rejected('unsupported_policy_version', 409) from None
    account_rows = User.objects.order_by('id')
    if search:
        account_rows = account_rows.filter(username__icontains=search)
    total = account_rows.count()
    pages = max(1, math.ceil(total / MATRIX_PAGE_SIZE))
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)
    window = list(account_rows[(page - 1) * MATRIX_PAGE_SIZE: page * MATRIX_PAGE_SIZE])
    page_ids = [user.pk for user in window]
    profiles = {profile.user_id: profile for profile in AccountProfile.objects.filter(user_id__in=page_ids)}
    owner_scope = actor_policy.is_instance_owner
    visible = list(access.viewable_studies(actor, policy=actor_policy))
    visible_keys = {str(study.pk) for study in visible}
    all_keys = {str(pk) for pk in Study.objects.values_list('pk', flat=True)}
    studies = sorted(visible, key=lambda study: (study.title, str(study.pk)))
    assignable = {str(study.pk): access.assignable_actions(actor, study, policy=actor_policy)
                  for study in studies}
    joiner = ui.tr(lang, 'users_preview_joiner')
    rows = []
    for user in window:
        profile = profiles.get(user.pk)
        is_owner_row = user.pk == owner_id
        own_row = user.pk == getattr(actor, 'pk', None)
        try:
            target_policy = access.canonical_policy(user)
        except ValueError:
            target_policy = None
        dominated = False
        if target_policy is not None and not is_owner_row and not own_row:
            target_role = profile.role if profile is not None else 'user'
            platform_action = 'accounts.manage_admin' if target_role == 'admin' else 'accounts.manage_user'
            dominated = ((owner_scope or access.takeover_allowed(actor, user, after=target_policy))
                         and access.allowed_platform(actor, platform_action))
        hidden = set()
        if target_policy is not None and not is_owner_row:
            stored = set(target_policy.study_overrides) | set(target_policy.grants)
            hidden |= {key for key in stored if key not in visible_keys}
            if authorization_default_has_view(target_policy):
                hidden |= all_keys - visible_keys
        entries = []
        for study in studies:
            key = str(study.pk)
            current = (set(access.configured_study_actions(target_policy, key))
                       if target_policy is not None else set())
            manageable = set(assignable.get(key, set())) if not (is_owner_row or own_row) else set()
            if not current and not manageable:
                continue
            if is_owner_row:
                reason = 'owner'
            elif own_row:
                reason = 'self'
            elif not manageable or not current <= manageable or not dominated:
                reason = 'outside'
            else:
                reason = ''
            source = study_source(target_policy, is_owner_row, key)
            entries.append({'study': study, 'granted': bool(current),
                            'current': {action: False for action in current},
                            'visibility': 'study.view' in current,
                            'summary': joiner.join(sorted(current)) if current else ui.tr(lang, 'users_preview_none_actions'),
                            'effective': sorted(current), 'source': source,
                            'source_label': ui.tr(lang, SOURCE_KEYS[source]),
                            'editable': not reason, 'readonly': bool(reason), 'readonly_reason': reason,
                            'checks': [{'action': action, 'label': study_action_label(action, lang),
                                        'checked': action in current, 'delegable': False}
                                       for action in sorted(manageable) if action != 'study.view']})
        # Owner platform switches (display facts only; every write re-authorizes
        # through preview_platform/commit_platform). The section is offered only
        # to the Owner and never for the Owner or own row, so an Admin can never
        # reach or self-grant the three Owner-controlled switches from the page.
        platform_effective, platform_overrides = set(), {}
        if target_policy is not None:
            platform_effective = set(access.configured_platform_actions(target_policy))
            platform_overrides = dict(target_policy.platform_overrides)
        platform_editable = (owner_scope and not (is_owner_row or own_row)
                             and target_policy is not None
                             and profiles.get(user.pk) is not None)
        # The red permanent-delete entry is offered exactly where the delete
        # transaction would accept it (platform switches and whole-account
        # scope); every value comes from the canonical policies already loaded
        # for this page, so the hint adds no query. The server re-checks
        # everything on submission.
        can_delete = False
        if not (is_owner_row or own_row) and target_policy is not None:
            required = accounts.lifecycle_platform_actions(
                'delete', profile.role if profile is not None else 'user')
            can_delete = (
                all(action in access.platform_actions_of(actor_policy) for action in required)
                and access.takeover_allowed_from(actor_policy, target_policy,
                                                 accounts.deletion_after_policy(target_policy),
                                                 sorted(all_keys)))
        rows.append({'id': user.pk, 'username': user.username, 'is_owner': is_owner_row,
                     'role': profile.role if profile is not None else 'user',
                     'is_active': user.is_active,
                     'must_change_password': bool(profile.must_change_password) if profile is not None else False,
                     'hidden_studies': len(hidden), 'studies': entries,
                     'study_count': len(entries),
                     'editable_studies': sum(1 for entry in entries if entry['editable']),
                     'editable_actions': sum(len(entry['checks']) for entry in entries if entry['editable']),
                     'platform_effective': sorted(platform_effective),
                     'platform_overrides': platform_overrides,
                     'platform_options': [{'action': action, 'label': platform_label(action, lang),
                                           'checked': action in platform_effective}
                                          for action in sorted(access.PLATFORM_V2_ACTIONS)],
                     'platform_editable': platform_editable,
                     'can_delete': can_delete})
    # Browser-session drafts are keyed by this stable scope (instance identity
    # plus the actor's stable subject), never by revision alone: another account
    # or another instance in the same tab never reads the previous actor's
    # drafts, and a revision change keeps the batch instead of silently
    # dropping it (the client marks such entries as needing a fresh preview).
    principal_id = Principal.objects.filter(user_id=actor.pk).values_list('pk', flat=True).first()
    return {'rows': rows, 'search': search, 'page': page, 'pages': pages, 'total': total,
            'page_links': _pager_links('page', page, pages, {'q': search}), 'owner_scope': owner_scope,
            'draft_scope': f'{instance.instance_id}:{principal_id or actor.pk}',
            'version': 2}


def authorization_default_has_view(policy):
    """Whether the subject's override-less default carries ``study.view``."""
    return access.VIEW_ACTION in access.configured_default_study_actions(policy)


def matrix_page(actor, search='', page=1, lang='zh'):
    """One bounded page of the /users permission matrix (version routed)."""
    version = access.authorization_version()
    if version == 2:
        return _matrix_page_v2(actor, search, page, lang)
    if version == 1:
        return _matrix_page_v1(actor, search, page, lang)
    raise Rejected('unsupported_version', 409)


def _matrix_page_v1(actor, search='', page=1, lang='zh'):
    """One bounded page of the /users permission matrix.

    Accounts are ordered by id, optionally filtered by a case-insensitive
    username search, and paged at ``MATRIX_PAGE_SIZE`` accounts per page so a
    growing instance never renders an unbounded table. Only the page's accounts
    are materialized: profiles and grants are queried for those ids only, and a
    non-Owner's study data is limited to the studies named by those grants plus
    the actor's editable studies (the Owner governs every study, so the catalog
    is the Owner's scope).

    Owner and Admin inspect the whole instance read-only, but a row outside the
    actor's effective and delegable authority renders as a read-only summary
    (``readonly``/``readonly_reason``) instead of a form, and the Owner row stays
    read-only for everyone. The search never widens the per-account authority
    checks in :func:`_matrix_authorize`.
    """
    from .gui import action_label
    User = get_user_model()
    owner_id = Instance.objects.get(pk=1).owner_id
    search = (search or '').strip()
    account_rows = User.objects.order_by('id')
    if search:
        account_rows = account_rows.filter(username__icontains=search)
    total = account_rows.count()
    pages = max(1, math.ceil(total / MATRIX_PAGE_SIZE))
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)
    window = list(account_rows[(page - 1) * MATRIX_PAGE_SIZE: page * MATRIX_PAGE_SIZE])
    page_ids = [user.pk for user in window]
    profiles = {profile.user_id: profile for profile in AccountProfile.objects.filter(user_id__in=page_ids)}
    owner, manageable_by_study = _matrix_study_scope(actor)
    page_grants = Grant.objects.filter(user_id__in=page_ids)
    grants = {}
    for user_id, study_id, action, delegable in page_grants.values_list('user_id', 'study_id', 'action', 'delegable'):
        grants.setdefault((user_id, study_id), {})[action] = delegable
    if owner:
        # The Owner governs every study, so every study can produce an entry.
        studies = list(Study.objects.order_by('title', 'id'))
    else:
        # Only studies that can produce an entry for this page are loaded: the
        # ones the page accounts already hold grants on (read-only inspection)
        # plus the actor's editable studies. A large catalog never materializes.
        needed = {study_id for _, study_id in grants} | set(manageable_by_study)
        studies = list(Study.objects.filter(pk__in=needed).order_by('title', 'id'))
    joiner = ui.tr(lang, 'users_preview_joiner')
    rows = []
    for user in window:
        profile = profiles.get(user.pk)
        is_owner_row = user.pk == owner_id
        own_row = user.pk == getattr(actor, 'pk', None)
        entries = []
        for study in studies:
            current = grants.get((user.pk, study.pk), {})
            if is_owner_row or own_row:
                manageable = set()
            elif owner:
                manageable = set(ACTIONS)
            else:
                manageable = manageable_by_study.get(study.pk, set())
            if not current and not manageable:
                continue
            if is_owner_row:
                reason = 'owner'
            elif own_row:
                reason = 'self'
            elif not manageable or not set(current) <= manageable:
                reason = 'outside'
            else:
                reason = ''
            entries.append({'study': study, 'granted': bool(current), 'current': current,
                            'visibility': 'study.view' in current,
                            'summary': joiner.join(sorted(current)) if current else ui.tr(lang, 'users_preview_none_actions'),
                            'editable': not reason, 'readonly': bool(reason), 'readonly_reason': reason,
                            'checks': [{'action': action, 'label': action_label(action, lang),
                                        'checked': action in current, 'delegable': bool(current.get(action, False))}
                                       for action in sorted(manageable) if action != 'study.view']})
        # The v1 hint reuses the page's own grant rows and the actor's delegable
        # scope (already loaded), so it adds no query; the write path re-checks.
        can_delete = False
        if not (is_owner_row or own_row):
            actor_profile = profiles.get(actor.pk) or AccountProfile.objects.filter(user_id=actor.pk).first()
            actor_role = actor_profile.role if actor_profile is not None else 'user'
            required = accounts.lifecycle_platform_actions(
                'delete', profile.role if profile is not None else 'user')
            platform_ok = owner or (actor_role == 'admin'
                                    and set(required) <= access.LEGACY_PLATFORM_ADMIN)
            target_privileges = {(study_id, action)
                                 for (user_id, study_id), actions in grants.items()
                                 if user_id == user.pk for action in actions}
            actor_authority = {(study_id, action)
                               for study_id, actions in manageable_by_study.items()
                               for action in actions}
            can_delete = platform_ok and (owner or actor_authority >= target_privileges)
        rows.append({'id': user.pk, 'username': user.username, 'is_owner': is_owner_row,
                     'role': profile.role if profile is not None else 'user',
                     'is_active': user.is_active,
                     'must_change_password': bool(profile.must_change_password) if profile is not None else False,
                     'studies': entries, 'can_delete': can_delete})
    return {'rows': rows, 'search': search, 'page': page, 'pages': pages, 'total': total,
            'page_links': _pager_links('page', page, pages, {'q': search}), 'owner_scope': owner,
            'version': 1}


def _conflict_groups():
    """Database-side grouping of grants that are missing ``study.view``.

    The queryset yields ``(user_id, study_id)`` value rows, so slicing a page
    never materializes Grant instances and no page has to load the whole table.
    """
    return (Grant.objects.values('user_id', 'study_id')
            .annotate(has_view=Count('action', filter=Q(action='study.view')))
            .filter(has_view=0).order_by('user_id', 'study_id'))


def conflict_page(page=1, size=CONFLICT_PAGE_SIZE, keep=None):
    """One bounded page of authorization-conflict groups for the /users page.

    The count comes from the grouped query; only its own page of groups and
    their actions are loaded. Preview and commit keep using the whole snapshot
    (see :func:`_conflict_snapshot`), so paging the presentation never narrows
    what a reconciliation actually checks.
    """
    groups = _conflict_groups()
    total = groups.count()
    pages = max(1, math.ceil(total / size))
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)
    pairs = [(row['user_id'], row['study_id']) for row in groups[(page - 1) * size: page * size]]
    user_ids = {user_id for user_id, _ in pairs}
    study_ids = {study_id for _, study_id in pairs}
    actions = {}
    for user_id, study_id, action in Grant.objects.filter(
            user_id__in=user_ids, study_id__in=study_ids).values_list('user_id', 'study_id', 'action'):
        actions.setdefault((user_id, study_id), set()).add(action)
    names = {pk: name for pk, name in
             get_user_model().objects.filter(pk__in=user_ids).values_list('pk', 'username')}
    titles = {pk: title for pk, title in
              Study.objects.filter(pk__in=study_ids).values_list('pk', 'title')}
    rows = [{'user_id': user_id, 'study_id': study_id,
             'username': names.get(user_id, str(user_id)), 'study': titles.get(study_id, str(study_id)),
             'actions': sorted(actions.get((user_id, study_id), set()) - {'study.view'})}
            for user_id, study_id in pairs]
    return {'rows': rows, 'total': total, 'page': page, 'pages': pages,
            'page_links': _pager_links('cpage', page, pages, keep)}


def configure_studies_page(actor, search='', limit=STUDY_CHOICE_LIMIT):
    """Bounded, searchable study choices for roster templates and imports.

    Authorization is unchanged: the Owner sees every study, and another account
    sees exactly the studies where it holds both ``study.view`` and
    ``study.configure`` (effective grants, matching :func:`access.allowed`). A
    large catalog is capped at ``limit`` by title search, but the cap is
    explicit (``total``/``more``) and the search keeps every configurable study
    reachable, so choices are bounded by query rather than silently dropped.
    """
    search = (search or '').strip()
    studies = Study.objects.order_by('title', 'id')
    if search:
        studies = studies.filter(title__icontains=search)
    if not is_instance_owner(actor):
        if not (getattr(actor, 'is_authenticated', False) and getattr(actor, 'is_active', False)):
            studies = studies.none()
        else:
            version = access.authorization_version()
            if version == 2:
                # Same kernel as every other entry: an Admin default or an
                # explicit override counts, and only studies with both actions
                # are offered.
                studies = studies.filter(pk__in=access.manageable_study_ids(actor))
            elif version == 1:
                visible = Grant.objects.filter(user=actor, action='study.view', study=OuterRef('pk'))
                configure = Grant.objects.filter(user=actor, action='study.configure', study=OuterRef('pk'))
                studies = (studies.annotate(_visible=Exists(visible), _configure=Exists(configure))
                           .filter(_visible=True, _configure=True))
            else:
                # Unknown stored version: no study choice at all.
                studies = studies.none()
    total = studies.count()
    window = list(studies[:limit])
    return {'studies': window, 'total': total, 'more': total > len(window),
            'search': search, 'limit': limit}


def _matrix_submission(post, version):
    """Rebuild the submitted (user, study, visibility, actions) set from a form POST."""
    require(post.get('user_id'), 'user_id')
    require(post.get('study_id'), 'study_id')
    user = get_user_model().objects.filter(pk=post['user_id']).first()
    require(user is not None, 'account_missing', 404)
    study = Study.objects.filter(pk=post['study_id']).first()
    require(study is not None, 'study_missing', 404)
    visibility = post.get('visibility') == '1'
    catalog = access.STUDY_V2_ACTIONS if version == 2 else ACTIONS
    actions = {}
    for key in post.keys():
        if key.startswith('action:') and post.get(key) == '1':
            code = key.split(':', 1)[1]
            require(code in catalog, 'action')
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
        'kind': MATRIX_KIND, 'actor': actor.pk, 'instance': str(instance.pk),
        'version': access.authorization_version(instance),
        'revision': instance.governance_revision,
        'user': _account_digest({user.pk}), 'study': _study_digest({study.pk}),
        'grants': _grant_digest({(user.pk, study.pk)}), 'target': {action: target[action] for action in sorted(target)},
    })


def _matrix_authorize(actor, target_user, study, current, target):
    """Version-routed matrix authority for one target/study edit.

    v1 keeps effective AND delegable actions plus the study-scoped dominance
    comparison. v2 uses the kernel's assignable set on this study and the
    whole-account before/after takeover comparison over every study, so a legal
    difference on B can never overwrite the target's A permission.
    """
    _require_target(actor, target_user)
    version = access.authorization_version()
    if version == 1:
        if not is_instance_owner(actor):
            require(dominates(actor, target_user), 'higher_privilege_target', 403)
            manageable = manageable_actions(actor, study)
            require(set(current) | set(target) <= manageable, 'delegation_forbidden', 403)
        return
    require(version == 2, 'unsupported_version', 409)
    # Permission configuration is not a separate product: the actor must also
    # hold the target role's lifecycle capability (manage_user / manage_admin)
    # besides view + configure on this study.
    target_profile = _profile(target_user.pk)
    platform_action = 'accounts.manage_admin' if (target_profile is not None and target_profile.role == 'admin') else 'accounts.manage_user'
    require(access.allowed_platform(actor, platform_action), 'forbidden', 403)
    manageable = access.assignable_actions(actor, study)
    require(set(current) | set(target) <= manageable, 'delegation_forbidden', 403)
    try:
        after = access.with_study_override(access.canonical_policy(target_user), study.pk, target)
    except ValueError:
        raise Rejected('unsupported_policy_version', 409) from None
    require(access.takeover_allowed(actor, target_user, after=after), 'higher_privilege_target', 403)


def _matrix_current(version, user, study):
    """Stored per-action current state for one target/study (version routed).

    v1 reads the grant rows (action -> delegable). v2 reads the complete stored
    selection from the kernel (the delegable flag has no v2 meaning and is
    always False). Any other stored version is refused, never read as v1."""
    require(version in (1, 2), 'unsupported_version', 409)
    if version == 2:
        try:
            policy = access.canonical_policy(user)
        except ValueError:
            raise Rejected('unsupported_policy_version', 409) from None
        return {action: False for action in access.configured_study_actions(policy, study.pk)}
    return {action: delegable for action, delegable in
            Grant.objects.filter(user=user, study=study).values_list('action', 'delegable')}


def preview_matrix(actor, post):
    version = access.authorization_version()
    if version == 1:
        require(is_account_administrator(actor), 'forbidden', 403)
    else:
        require(version == 2, 'unsupported_version', 409)
        require(access.allowed_platform(actor, 'accounts.view'), 'forbidden', 403)
    user, study, visibility, actions = _matrix_submission(post, version)
    with transaction.atomic():
        instance = accounts._locked_instance()
        locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
        require(locked.is_active, 'auth_required', 403)
        require(access.allowed_platform(locked, 'accounts.view', instance=instance), 'forbidden', 403)
        current = _matrix_current(version, user, study)
        target = _matrix_target(visibility, actions, current)
        if version == 2:
            # Complete explicit selection: empty, view alone, or view plus known
            # v2 actions; invisible sub-actions stay a contradiction.
            require(access.validate_selection(sorted(target)) == set(target), 'invisible_subaction', 409)
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
        version = access.authorization_version(instance)
        require(version in (1, 2), 'unsupported_version', 409)
        if replay:
            return row.result
        staged = row.staged
        user = get_user_model().objects.select_for_update().get(pk=staged['user_id'])
        study = Study.objects.get(pk=staged['study_id'])
        target = {action: bool(flag) for action, flag in staged['target'].items()}
        require(_matrix_binding(locked, instance, user, study, target) == row.binding, 'preview_stale', 409)
        current = _matrix_current(version, user, study)
        _matrix_authorize(locked, user, study, current, target)
        require(current != target, 'preview_stale', 409)
        policy_before = access.canonical_policy(user) if version == 2 else None
        if version == 2:
            # Complete stored selection for this one study only; legacy grant
            # rows stay untouched and are shadowed by the explicit override.
            profile = AccountProfile.objects.select_for_update().filter(user_id=user.pk).first()
            require(profile is not None, 'preview_stale', 409)
            overrides = dict(profile.study_overrides or {})
            overrides[str(study.pk)] = sorted(target)
            profile.study_overrides = overrides
            profile.revision += 1
            profile.save(update_fields=['study_overrides', 'revision'])
        else:
            for action in sorted(current):
                if action not in target:
                    Grant.objects.filter(user=user, study=study, action=action).delete()
            for action in sorted(target):
                if current.get(action) != target[action]:
                    Grant.objects.update_or_create(user=user, study=study, action=action,
                                                   defaults={'delegable': target[action]})
        before, after = grant_diff(current, target)
        change_before, change_after = {'actions': before}, {'actions': after}
        if version == 2:
            # The audit carries the complete before/after policy, not only the
            # edited study, so a later reader can rebuild the whole decision.
            change_before['policy'] = access.policy_snapshot(
                access.with_study_override(policy_before, study.pk, current))
            change_after['policy'] = access.policy_snapshot(
                access.with_study_override(policy_before, study.pk, target))
        accounts.audit(locked, 'permission.matrix_changed', f'{user.pk}:{study.pk}',
                       before=change_before, after=change_after)
        result = {'username': user.username, 'study': study.title, 'actions': sorted(target),
                  'user_id': user.pk, 'study_id': str(study.pk)}
        _finish(row, instance, result)
    return result


# --------------------------------------------------------------------------- platform switches

def _platform_submission(post, current):
    """Patch semantics: only explicitly posted switches change; unknown refuse."""
    overrides = dict(current or {})
    seen = set()
    for key, value in post.items():
        if not isinstance(key, str) or not key.startswith('platform:'):
            continue
        action = key.split(':', 1)[1]
        require(action in access.PLATFORM_V2_ACTIONS, 'action')
        require(value in ('0', '1'), 'platform_value')
        overrides[action] = value == '1'
        seen.add(action)
    require(bool(seen), 'platform_required')
    return access.validate_platform_overrides(overrides)


def _platform_binding(actor, instance, target, overrides):
    return _digest({'kind': PLATFORM_KIND, 'actor': actor.pk, 'instance': str(instance.pk),
                    'revision': instance.governance_revision, 'version': access.authorization_version(instance),
                    'target': _account_digest({target.pk}), 'overrides': overrides})


def preview_platform(actor, post):
    """Limited Owner platform-switch preview (server side; UI is a later task).

    Only the Owner may write these finite switches, and only for another
    non-Owner account: an Admin can never pass the three Owner-controlled Admin
    lifecycle switches on. The preview binds the target's complete stored state
    and the full resulting override map; nothing is written here.
    """
    with transaction.atomic():
        instance = accounts._locked_instance()
        require(access.authorization_version(instance) == 2, 'authorization_upgrade_required', 409)
        locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
        require(locked.is_active, 'auth_required', 403)
        require(is_instance_owner(locked), 'owner_only', 403)
        target = _target(post.get('username'))
        _require_target(locked, target)
        profile = AccountProfile.objects.select_for_update().filter(user_id=target.pk).first()
        current = dict(profile.platform_overrides or {}) if profile is not None else {}
        overrides = _platform_submission(post, current)
        require(overrides != current, 'no_change', 409)
        try:
            before_policy = access.canonical_policy(target, instance=instance)
            after_policy = access.project_policy(before_policy, platform_overrides=overrides)
        except ValueError:
            raise Rejected('unsupported_policy_version', 409) from None
        before_set = set(access.configured_platform_actions(before_policy))
        after_set = set(access.configured_platform_actions(after_policy))
        summary = {'username': target.username, 'user_id': target.pk,
                   'before': sorted(before_set), 'after': sorted(after_set),
                   'added': sorted(after_set - before_set), 'removed': sorted(before_set - after_set),
                   'overrides': overrides}
        row = _preview(locked, PLATFORM_KIND, str(target.pk), summary, [],
                       _platform_binding(locked, instance, target, overrides),
                       {'user_id': target.pk, 'overrides': overrides},
                       instance.governance_revision)
    return row


def commit_platform(actor, password, preview_id):
    require(bool(preview_id), 'preview_required', 400)
    with transaction.atomic():
        instance, locked, row, replay = _gate(actor, password, preview_id, PLATFORM_KIND,
                                              scope=row_scope(preview_id, PLATFORM_KIND),
                                              require_owner=True)
        # The platform switches are an exact v2 entry: a v1 instance (or an
        # unknown stored version) can never commit a stored platform override.
        require(access.authorization_version(instance) == 2, 'authorization_upgrade_required', 409)
        if replay:
            return row.result
        staged = row.staged
        target = get_user_model().objects.select_for_update().get(pk=staged['user_id'])
        _require_target(locked, target)
        overrides = access.validate_platform_overrides(staged['overrides'])
        require(_platform_binding(locked, instance, target, overrides) == row.binding, 'preview_stale', 409)
        profile = AccountProfile.objects.select_for_update().filter(user_id=target.pk).first()
        require(profile is not None, 'preview_stale', 409)
        current = dict(profile.platform_overrides or {})
        require(overrides != current, 'preview_stale', 409)
        try:
            before_policy = access.canonical_policy(target, instance=instance)
            after_policy = access.project_policy(before_policy, platform_overrides=overrides)
        except ValueError:
            raise Rejected('unsupported_policy_version', 409) from None
        before_set = sorted(access.configured_platform_actions(before_policy))
        after_set = sorted(access.configured_platform_actions(after_policy))
        profile.platform_overrides = overrides
        profile.revision += 1
        profile.save(update_fields=['platform_overrides', 'revision'])
        accounts.audit(locked, 'permission.platform_changed', target.pk,
                       before={'platform': before_set, 'platform_overrides': current,
                               'policy': access.policy_snapshot(before_policy)},
                       after={'platform': after_set, 'platform_overrides': overrides,
                              'policy': access.policy_snapshot(after_policy)})
        result = {'username': target.username, 'platform': after_set, 'user_id': target.pk}
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
