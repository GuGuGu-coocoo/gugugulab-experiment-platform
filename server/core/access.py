import dataclasses
from collections.abc import Mapping

from django.contrib.auth import get_user_model

from . import authorization
from .models import AccountProfile, Grant, Instance, Principal, Study, StudyDeletion
from .protocol import require

# Re-exported kernel facts for the single-entry rule: only this module imports
# the pure kernel, and every permission caller (including the Owner enablement
# in ``governance_migration``) goes through here.
STUDY_V2_ACTIONS = authorization.STUDY_ACTIONS
PLATFORM_V2_ACTIONS = authorization.PLATFORM_ACTIONS
VIEW_ACTION = authorization.VIEW
SUPPORTED_POLICY_VERSIONS = authorization.SUPPORTED_POLICY_VERSIONS
# The fixed v2 Admin study set, used when the Owner explicitly adopts it during
# enablement (independent of any per-profile future bound).
ADMIN_STUDY_TEMPLATE = authorization.ROLE_STUDY_TEMPLATE['admin']

# Legacy v1 catalog, kept unchanged for every version-1 instance. The v2 finite
# catalogs (without member.manage / permission.delegate) live in authorization.py
# and are reached through the version-routed resolvers below.
ACTIONS = {'study.view','study.configure','build.upload','build.preview','release.approve_pilot','recruitment.manage','data.export_raw','session.recover','member.manage','permission.delegate','audit.view','identity_mapping.read','session.view'}
ROLES = {'admin','user'}
# New in 03B: explicit identity/session visibility actions. They are never
# backfilled into existing grants; only explicit assignment (or a newly created
# study for its creator) can add them.
NEW_ACTIONS = {'identity_mapping.read','session.view'}


def profile_of(user):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        return None
    return AccountProfile.objects.filter(user_id=user.pk).first()


def effective_role(user):
    """Fail closed/safe ordinary: a missing or unknown profile never grants Admin."""
    profile = profile_of(user)
    return profile.role if profile is not None and profile.role in ROLES else 'user'


def is_instance_owner(user):
    """Owner derives only from Instance.owner, never from is_staff/is_superuser/Grant."""
    return bool(getattr(user, 'is_authenticated', False) and getattr(user, 'pk', None) and Instance.objects.filter(owner_id=user.pk).exists())


def is_account_administrator(user):
    return is_instance_owner(user) or effective_role(user) == 'admin'


def actions_for(user, study):
    return set(Grant.objects.filter(user=user, study=study).values_list('action', flat=True))


def exact_policy_version(value):
    """Exact supported policy version (1 or 2) or ``None`` (fail closed)."""
    return authorization.exact_version(value)


def canonical_policy(user, *, instance=None, profile=None):
    """Canonical v2 policy for an authenticated actor, from stored rows only.

    Every authorization fact is re-read from the current database: a
    caller-supplied ``profile`` is only accepted as a prefetch hint that must
    belong to this exact subject (its primary key is re-checked against the
    stored row and the stored values are used), and a caller-supplied
    ``instance`` is re-read by primary key so an in-memory ``Instance.owner``
    edit cannot fabricate an Owner. The Owner fact always comes from the stored
    ``Instance.owner``; a role string, a grant or any caller-supplied flag can
    never produce it. Unsupported policy versions, non-boolean state flags,
    unknown catalog entries and malformed stored JSON raise
    :class:`authorization.PolicyError` (fail closed). A missing profile is an
    ordinary account (matches ``effective_role``) with no overrides.
    """
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'pk', None):
        raise authorization.PolicyError('auth_required')
    User = get_user_model()
    stored_user = User.objects.filter(pk=user.pk).only('is_active').first()
    if stored_user is None:
        raise authorization.PolicyError('auth_required')
    if instance is None:
        instance = Instance.objects.first()
    elif getattr(instance, 'pk', None) is None:
        raise authorization.PolicyError('instance_missing')
    else:
        stored_instance = Instance.objects.filter(pk=instance.pk).only('owner_id').first()
        if stored_instance is None:
            raise authorization.PolicyError('instance_missing')
        instance = stored_instance
    is_owner = instance is not None and instance.owner_id == user.pk
    stored_profile = AccountProfile.objects.filter(user_id=user.pk).first()
    if profile is not None:
        # A trusted prefetch must be bound to this subject and to a stored row;
        # its in-memory field values are never trusted, only the stored ones.
        if getattr(profile, 'pk', None) is None or getattr(profile, 'user_id', None) != user.pk:
            raise authorization.PolicyError('profile_subject_mismatch')
        if stored_profile is None or stored_profile.pk != profile.pk:
            raise authorization.PolicyError('profile_subject_mismatch')
    profile = stored_profile
    if profile is None:
        role, must_change = 'user', False
        platform_overrides, study_overrides, future = {}, {}, None
    else:
        if authorization.exact_version(profile.policy_version) is None:
            raise authorization.PolicyError('unsupported_policy_version', repr(profile.policy_version))
        role = profile.role
        must_change = authorization.state_flag(profile.must_change_password, 'must_change_password')
        platform_overrides = profile.platform_overrides
        study_overrides = profile.study_overrides
        future = profile.future_study_actions
    principal = Principal.objects.filter(user_id=user.pk).first()
    grants = {}
    for study_id, action in Grant.objects.filter(user_id=user.pk).values_list('study_id', 'action'):
        grants.setdefault(str(study_id), []).append(action)
    return authorization.SubjectPolicy(
        role, is_instance_owner=is_owner, authenticated=True,
        active=authorization.state_flag(stored_user.is_active, 'is_active'),
        must_change_password=must_change,
        deleted=principal is not None and principal.deleted_at is not None,
        platform_overrides=platform_overrides, study_overrides=study_overrides,
        future_study_actions=future, grants=grants)


def configured_platform_actions(policy):
    """Stored platform permissions; used by previews/takeover comparisons."""
    return authorization.configured_platform_actions(policy)


def configured_default_study_actions(policy):
    """Stored actions for a study without an explicit complete override."""
    return authorization.configured_default_study_actions(policy)


def configured_study_actions(policy, study_uuid):
    """Stored actions on one study regardless of the account state gate."""
    return authorization.configured_study_actions(policy, study_uuid)


def authorization_version(instance=None):
    """Exact stored instance authorization version, or ``None`` if unsupported.

    A database extended by migration 0010 keeps ``authorization_version = 1``;
    a pre-0010 schema without the field and an instance that does not exist yet
    are the conservative legacy version 1. Every other stored value must be
    exactly 1 or 2: a future version, a string, a bool or any malformed value is
    unsupported and resolves to a fail-closed denial by the callers below, never
    to a silent downgrade and never to v2 through a ``>=`` comparison.
    """
    if instance is None:
        instance = Instance.objects.first()
    if instance is None:
        return 1
    if not hasattr(instance, 'authorization_version'):
        return 1
    return authorization.exact_version(instance.authorization_version)


def _resolved_version(version=None, instance=None):
    if version is None:
        return authorization_version(instance)
    return authorization.exact_version(version)


def _active_account(user):
    return bool(getattr(user, 'is_authenticated', False) and getattr(user, 'is_active', False))


def _legacy_study_actions(user, study):
    """Version-1 effective actions: explicit grants with a view prerequisite."""
    if not _active_account(user):
        return frozenset()
    actions = frozenset(Grant.objects.filter(user=user, study=study).values_list('action', flat=True)) & ACTIONS
    return actions if 'study.view' in actions else frozenset()


def resolve_study_actions(user, study, *, version=None, instance=None, policy=None):
    """Single study-effective resolver.

    Version 1 keeps the conservative legacy grant contract. Version 2 delegates
    to the pure kernel and needs the caller's canonical policy; without one the
    answer is empty (fail closed) instead of falling back to legacy delegation
    data. An unsupported stored version is denied by both routes.
    """
    resolved = _resolved_version(version, instance)
    if resolved == 1:
        return _legacy_study_actions(user, study)
    if resolved != 2:
        return frozenset()
    if not _active_account(user) or policy is None:
        return frozenset()
    return authorization.study_actions(policy, study.pk)


def resolve_platform_actions(user, *, version=None, instance=None, policy=None):
    """Single platform-effective resolver.

    Version 1 returns nothing on purpose: the legacy role helpers
    (``effective_role``, ``is_account_administrator``, ...) stay authoritative
    until the R02C switch, and translating them into v2 actions here would
    change the old boundary. Version 2 delegates to the pure kernel and fails
    closed without a canonical policy; an unsupported version denies everything.
    """
    resolved = _resolved_version(version, instance)
    if resolved != 2:
        return frozenset()
    if not _active_account(user) or policy is None:
        return frozenset()
    return authorization.platform_actions(policy)


def allowed(user, study, action, *, version=None, instance=None, policy=None):
    """Single study decision entry used by every server permission caller.

    Version 1 (the default while ``authorization_version`` is unset or exactly
    1) keeps the explicit-grant contract. Version 2 resolves the *stored*
    instance exactly like every other entry: without an explicit ``version`` the
    canonical policy is built from the current database rows here, so the caller
    can never fall back to legacy ``delegable`` data and never has to construct
    a policy itself. The low-level explicit ``version=2`` route still requires a
    canonical policy and fails closed without one, so a caller cannot pick v2
    semantics and skip the stored state binding. Unknown actions are never
    granted by either side, and an unsupported stored version grants nothing.
    Callers that already hold a canonical policy (preview/commit inside one
    transaction) may pass it; it must come from :func:`canonical_policy`.
    """
    resolved = _resolved_version(version, instance)
    if resolved == 1:
        if not _active_account(user):
            return False
        if action not in ACTIONS:
            return False
        actions = actions_for(user, study)
        if not actions:
            return False
        if action == 'study.view':
            return 'study.view' in actions
        return action in actions and 'study.view' in actions
    if resolved != 2:
        return False
    if not _active_account(user):
        return False
    if action not in authorization.STUDY_ACTIONS:
        return False
    if policy is None:
        if version is not None:
            return False
        try:
            policy = canonical_policy(user, instance=instance)
        except authorization.PolicyError:
            return False
    return action in authorization.study_actions(policy, study.pk)


# Legacy v1 platform capability map, kept exactly at the old boundary:
# ``is_account_administrator`` (Owner or Admin) could view accounts, invite
# ordinary users and manage ordinary user lifecycle; a v1 Admin could also
# reset/disable a *peer Admin* per target under the study dominance rule, so
# ``accounts.manage_admin`` is the closest legacy capability. A v1 Admin could
# never appoint/demote roles (Owner-only), create studies (Owner-only) or delete
# accounts (no such v1 entry), so ``accounts.create_admin``,
# ``accounts.delete_admin`` and ``study.create`` stay Owner-only.
LEGACY_PLATFORM_ADMIN = frozenset({'accounts.view', 'accounts.create_user',
                                   'accounts.manage_user', 'accounts.manage_admin'})


def allowed_platform(user, action, *, version=None, instance=None, policy=None):
    """Version-routed platform decision for account/governance entries.

    Version 1 keeps the legacy capability map above (the per-target study
    dominance guard stays in the caller on that route). Version 2 uses the
    canonical stored policy and fails closed when it cannot be built.
    """
    resolved = _resolved_version(version, instance)
    if resolved == 1:
        if not _active_account(user):
            return False
        if action not in ACTIONS_PLATFORM:
            return False
        if is_instance_owner(user):
            return True
        return effective_role(user) == 'admin' and action in LEGACY_PLATFORM_ADMIN
    if resolved != 2:
        return False
    if not _active_account(user):
        return False
    if action not in authorization.PLATFORM_ACTIONS:
        return False
    if policy is None:
        if version is not None:
            return False
        try:
            policy = canonical_policy(user, instance=instance)
        except authorization.PolicyError:
            return False
    return action in authorization.platform_actions(policy)


ACTIONS_PLATFORM = frozenset(authorization.PLATFORM_ACTIONS)


def assignable_actions(actor, study, *, version=None, instance=None, policy=None):
    """Version-routed set of actions the actor may assign on one study.

    Version 1 keeps the legacy delegable contract. Version 2 uses the kernel
    (view + configure on that study, and never more than the actor's own
    effective set) and fails closed when the canonical policy cannot be built.
    """
    resolved = _resolved_version(version, instance)
    if resolved == 1:
        return {action for study_id, action in delegable_authority(actor) if study_id == study.pk}
    if resolved != 2:
        return set()
    if not _active_account(actor):
        return set()
    if policy is None:
        if version is not None:
            return set()
        try:
            policy = canonical_policy(actor, instance=instance)
        except authorization.PolicyError:
            return set()
    return set(authorization.assignable_study_actions(policy, study.pk))


def project_policy(policy, *, role=None, platform_overrides=None,
                   study_overrides=None, future_study_actions=...):
    """A derivative canonical policy for a *pending* whole-account change.

    Used by lifecycle takeover comparisons (reset, enable, role change, ...)
    before anything is written; the values are validated by the kernel exactly
    like stored ones.
    """
    replacements = {}
    if role is not None:
        replacements['role'] = role
    if platform_overrides is not None:
        replacements['platform_overrides'] = platform_overrides
    if study_overrides is not None:
        replacements['study_overrides'] = study_overrides
    if future_study_actions is not ...:
        replacements['future_study_actions'] = future_study_actions
    return dataclasses.replace(policy, **replacements)


def with_study_override(policy, study_uuid, actions):
    """The policy with one study set to a complete explicit override list."""
    overrides = dict(policy.study_overrides)
    overrides[str(study_uuid)] = sorted(actions)
    return dataclasses.replace(policy, study_overrides=overrides)


def takeover_allowed(actor, target, *, instance=None, after=None):
    """Whole-account takeover guard for lifecycle entries (version routed).

    Version 1 keeps the legacy study-scoped dominance contract. Version 2
    compares the target's stored platform and study permissions before and after
    the operation (including the future default) with the canonical kernel, over
    every study, so using an action never equals managing it.
    """
    version = authorization_version(instance)
    if version == 1:
        return dominates(actor, target)
    if version != 2:
        return False
    try:
        actor_policy = canonical_policy(actor, instance=instance)
        before = canonical_policy(target, instance=instance)
    except authorization.PolicyError:
        return False
    study_uuids = [str(pk) for pk in Study.objects.values_list('pk', flat=True)]
    return authorization.can_take_over(actor_policy, before, after if after is not None else before,
                                       study_uuids)


def takeover_creation_allowed(actor, after_policy, *, instance=None):
    """Creation guard: the actor must dominate the new account's whole policy.

    Used before writing an Admin invitation (and by the importers), so the new
    account can never start with platform or study permissions the creator was
    excluded from.
    """
    try:
        actor_policy = canonical_policy(actor, instance=instance)
    except authorization.PolicyError:
        return False
    empty = authorization.SubjectPolicy('user')
    study_uuids = [str(pk) for pk in Study.objects.values_list('pk', flat=True)]
    return authorization.can_take_over(actor_policy, empty, after_policy, study_uuids)


def platform_actions_of(policy):
    """Effective v2 platform actions of an already-canonical policy.

    Pure (no database access); callers that already hold a canonical policy use
    it for display hints, and the write entries keep re-reading stored rows.
    """
    if policy is None:
        return frozenset()
    return authorization.platform_actions(policy)


def takeover_allowed_from(actor_policy, target_before, after, study_uuids):
    """Pure whole-account comparison for callers that already hold policies.

    Used only for read-only display hints (for example whether the red delete
    entry is offered); it never authorizes a write by itself.
    """
    if actor_policy is None or target_before is None:
        return False
    return authorization.can_take_over(actor_policy, target_before, after, study_uuids)


def policy_snapshot(policy, *, studies=None):
    """Redacted complete policy description for previews and audits.

    Contains no username, password or grant row: only the role, the full
    platform set, the future default and the complete per-study action sets.
    """
    if studies is None:
        studies = list(Study.objects.order_by('pk'))
    return {'role': policy.role,
            'is_owner': policy.is_instance_owner,
            'platform': sorted(authorization.platform_actions(policy)),
            'study_default': sorted(authorization.default_study_actions(policy)),
            'studies': {str(study.pk): sorted(authorization.study_actions(policy, study.pk))
                        for study in studies}}


def validate_selection(actions):
    """Submitted complete selection: empty, view-only, or view plus actions."""
    return set(authorization.validate_selection(actions))


def validate_platform_overrides(overrides):
    """A finite boolean platform override map (unknown actions refuse)."""
    return dict(authorization.validate_platform_overrides(overrides))


def _study_keys_with(policy, needed):
    """Existing study UUIDs whose *effective* kernel actions contain ``needed``.

    Every key is produced by the same per-study kernel call
    :func:`allowed` uses, so the complete-override precedence is applied once:
    an explicit override (including an explicit empty list) fully covers the
    role default and the legacy grant rows and can never be unioned back into a
    listing, and the Owner/denied-state gates stay identical to the single
    object decision.
    """
    return {str(pk) for pk in Study.objects.values_list('pk', flat=True)
            if needed <= authorization.study_actions(policy, pk)}


def manageable_study_ids(user, *, version=None, instance=None, policy=None):
    """Study UUIDs where the subject holds view + configure (any route).

    v1 keeps the explicit grant-pair contract used by the study picker; v2 uses
    the kernel so an Admin's default (and an explicit override) is included and
    a study the subject cannot see is never named.
    """
    resolved = _resolved_version(version, instance)
    if resolved == 1:
        if not _active_account(user):
            return set()
        view = set(Grant.objects.filter(user=user, action='study.view').values_list('study_id', flat=True))
        configure = set(Grant.objects.filter(user=user, action='study.configure').values_list('study_id', flat=True))
        return {str(study_id) for study_id in view & configure}
    if resolved != 2:
        return set()
    if not _active_account(user):
        return set()
    if policy is None:
        if version is not None:
            return set()
        try:
            policy = canonical_policy(user, instance=instance)
        except authorization.PolicyError:
            return set()
    return _study_keys_with(policy, {authorization.VIEW, authorization.CONFIGURE})


def validate_account_bound(bound):
    """Validate/normalize one stored invitation bound; unknown input refuses.

    A bound always carries a role, finite platform overrides, complete per-study
    action lists and an explicit future list (never ``NULL``): relying on the
    role default is exactly what a restricted Admin creation must not do.
    """
    if not isinstance(bound, Mapping):
        raise authorization.PolicyError('invalid_bound', type(bound).__name__)
    role = bound.get('role')
    if role not in authorization.ROLES:
        raise authorization.PolicyError('unknown_role', repr(role))
    platform_overrides = dict(authorization.validate_platform_overrides(bound.get('platform_overrides')))
    study_overrides = {key: sorted(actions) for key, actions in
                       authorization.validate_study_overrides(bound.get('study_overrides')).items()}
    future = bound.get('future_study_actions')
    if future is None:
        raise authorization.PolicyError('bound_future_required')
    return {'role': role, 'platform_overrides': platform_overrides,
            'study_overrides': study_overrides,
            'future_study_actions': sorted(authorization.validate_future_study_actions(future))}


def bound_subject_policy(bound):
    """The :class:`authorization.SubjectPolicy` of a stored finite bound."""
    normalized = validate_account_bound(bound)
    return authorization.SubjectPolicy(
        normalized['role'], platform_overrides=normalized['platform_overrides'],
        study_overrides=normalized['study_overrides'],
        future_study_actions=normalized['future_study_actions'])


def takeover_bound_allowed(actor, bound, *, instance=None):
    """Does the actor's *current* canonical policy still dominate this bound?

    Used at activation: an issuer who lost manage/view/configure authority (or
    had the bound narrowed since the invitation) can no longer activate it.
    """
    try:
        actor_policy = canonical_policy(actor, instance=instance)
        target_policy = bound_subject_policy(bound)
    except authorization.PolicyError:
        return False
    study_uuids = [str(pk) for pk in Study.objects.values_list('pk', flat=True)]
    return authorization.can_take_over(actor_policy, target_policy, target_policy, study_uuids)


def conservative_admin_bound(issuer, *, instance=None):
    """Explicit finite Admin bound for a non-Owner issuer.

    Every existing study is fixed as a complete explicit override: the issuer's
    own effective actions where the issuer holds ``study.view`` and
    ``study.configure`` (using it is not the same as managing it), and an
    explicit empty list everywhere else. The future default is the issuer's own
    explicit bound, so the new Admin can never obtain, through the role default,
    permissions the issuer was excluded from. The caller compares it with
    :func:`takeover_allowed` before writing the invitation.
    """
    policy = canonical_policy(issuer, instance=instance)
    overrides = {}
    for study in Study.objects.all():
        actions = set(authorization.study_actions(policy, study.pk))
        if not {authorization.VIEW, authorization.CONFIGURE} <= actions:
            actions = set()
        overrides[str(study.pk)] = sorted(actions)
    return {'role': 'admin', 'platform_overrides': {},
            'study_overrides': overrides,
            'future_study_actions': sorted(authorization.configured_default_study_actions(policy))}


def viewable_studies(user, *, version=None, instance=None, policy=None):
    """The one visible-study algorithm shared by every listing entry.

    Version 1 keeps the explicit ``study.view`` grant contract. Version 2 uses
    the kernel per study, so an Admin's default actions (and any explicit
    override) are never missed, a study without ``study.view`` is never listed,
    and an explicit override that hides a study is never unioned back by the
    role default or by legacy grant rows. Returns a ``Study`` queryset-like
    iterable of objects in both routes.
    """
    resolved = _resolved_version(version, instance)
    if resolved == 1:
        if not _active_account(user):
            return Study.objects.none()
        return Study.objects.filter(grant__user=user, grant__action='study.view').distinct()
    if resolved != 2:
        return Study.objects.none()
    if not _active_account(user):
        return Study.objects.none()
    if policy is None:
        if version is not None:
            return Study.objects.none()
        try:
            policy = canonical_policy(user, instance=instance)
        except authorization.PolicyError:
            return Study.objects.none()
    return Study.objects.filter(pk__in=_study_keys_with(policy, {authorization.VIEW}))


def ensure_principal(user):
    """The stable identity row for an account; created once and never guessed."""
    principal = Principal.objects.filter(user_id=user.pk).first()
    if principal is None:
        principal = Principal.objects.create(user=user)
    return principal


def delegable_actions(user, study=None):
    grants = Grant.objects.filter(user=user, delegable=True)
    if study is not None:
        grants = grants.filter(study=study)
    return set(grants.values_list('action', flat=True))


def target_privileges(user):
    """Every study-scoped privilege the target holds, including nondelegable grants."""
    return set(Grant.objects.filter(user=user).values_list('study_id', 'action'))


def delegable_authority(user):
    """Study-scoped authority the actor may delegate: delegable grants whose
    study.view prerequisite holds on the same study."""
    grants = set(Grant.objects.filter(user=user, delegable=True).values_list('study_id', 'action'))
    visible = {study_id for study_id, action in grants if action == 'study.view'}
    return {(study_id, action) for study_id, action in grants if action == 'study.view' or study_id in visible}


def dominates(actor, target):
    """Takeover guard: study-scoped (study_id, action) authority comparison.

    The actor must be authorized on the same study and able to delegate every
    target privilege whose control would be acquired, including target grants
    that are nondelegable. The instance Owner bypasses the comparison; a missing
    profile or instance role never contributes study authority.
    """
    if is_instance_owner(actor):
        return True
    return delegable_authority(actor) >= target_privileges(target)


def conflicts():
    """(user_id, study_id, conflicting_actions) for grants that lack study.view.

    Grants of a study whose deletion mark committed are excluded: those rows are
    about to be removed with the study and must never be turned into a new
    permission preview or a reconciled ``study.view`` grant.
    """
    deleting = set(StudyDeletion.objects.values_list('study_uuid', flat=True))
    grouped = {}
    for user_id, study_id, action in Grant.objects.values_list('user_id', 'study_id', 'action'):
        if study_id in deleting:
            continue
        grouped.setdefault((user_id, study_id), set()).add(action)
    return sorted((user_id, study_id, sorted(actions - {'study.view'})) for (user_id, study_id), actions in grouped.items() if actions - {'study.view'} and 'study.view' not in actions)


def authority_actions(user, study, *, version=None, instance=None, policy=None):
    """Actions the actor may assign on this study: effective AND delegable.

    Version 1 keeps the legacy delegable contract. Version 2 takes the pure
    kernel's assignable set: the actor's own effective actions while holding
    ``study.view`` and ``study.configure`` there, so an Admin can never grant
    more than they hold and a read-only Admin cannot delegate at all. An
    unsupported stored version denies everything.
    """
    resolved = _resolved_version(version, instance)
    if resolved == 1:
        return {action for study_id, action in delegable_authority(user) if study_id == study.pk}
    if resolved != 2 or not _active_account(user) or policy is None:
        return set()
    return set(authorization.assignable_study_actions(policy, study.pk))


def manageable_actions(actor, study):
    """Owner may assign any catalog action; Admin is limited to delegable_authority."""
    if is_instance_owner(actor):
        return set(ACTIONS)
    return authority_actions(actor, study)


def visible_studies(user):
    """Studies where the user holds at least one explicit grant (matrix scope)."""
    return Study.objects.filter(grant__user=user).distinct()


def guard(user, study, action):
    require(allowed(user, study, action), 'forbidden', 403)
