from django.contrib.auth import get_user_model

from . import authorization
from .models import AccountProfile, Grant, Instance, Principal, Study
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
    """Single study decision entry.

    Version 1 (the default while ``authorization_version`` is unset or exactly
    1) keeps the explicit-grant contract; version 2 uses the canonical policy.
    Unknown actions are never granted by either side, and an unsupported stored
    version grants nothing at all.
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
    if action not in authorization.STUDY_ACTIONS or policy is None:
        return False
    return action in authorization.study_actions(policy, study.pk)


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
    """(user_id, study_id, conflicting_actions) for grants that lack study.view."""
    grouped = {}
    for user_id, study_id, action in Grant.objects.values_list('user_id', 'study_id', 'action'):
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
