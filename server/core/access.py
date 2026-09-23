from . import authorization
from .models import AccountProfile, Grant, Instance, Study
from .protocol import require

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


def authorization_version(instance=None):
    """Stored instance authorization version, conservative 1 while unset.

    The v2 marker (``Instance.authorization_version``) arrives with the next R02
    task; until it exists every database resolves to the legacy v1 rules, so no
    route silently adopts the new defaults.
    """
    if instance is None:
        instance = Instance.objects.first()
    if instance is None:
        return 1
    version = getattr(instance, 'authorization_version', 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        return 1
    return 2 if version >= 2 else 1


def _resolved_version(version=None, instance=None):
    if version is None:
        return authorization_version(instance)
    try:
        version = int(version)
    except (TypeError, ValueError):
        return 1
    return 2 if version >= 2 else 1


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
    data. Routes keep version 1 until the R02C switch.
    """
    if _resolved_version(version, instance) < 2:
        return _legacy_study_actions(user, study)
    if not _active_account(user) or policy is None:
        return frozenset()
    return authorization.study_actions(policy, study.pk)


def resolve_platform_actions(user, *, version=None, instance=None, policy=None):
    """Single platform-effective resolver.

    Version 1 returns nothing on purpose: the legacy role helpers
    (``effective_role``, ``is_account_administrator``, ...) stay authoritative
    until the R02C switch, and translating them into v2 actions here would
    change the old boundary. Version 2 delegates to the pure kernel and fails
    closed without a canonical policy.
    """
    if _resolved_version(version, instance) < 2:
        return frozenset()
    if not _active_account(user) or policy is None:
        return frozenset()
    return authorization.platform_actions(policy)


def allowed(user, study, action, *, version=None, instance=None, policy=None):
    """Single study decision entry.

    Version 1 (the default while ``authorization_version`` is unset) keeps the
    explicit-grant contract; version 2 uses the canonical policy. Unknown
    actions are never granted by either side.
    """
    if _resolved_version(version, instance) < 2:
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
    more than they hold and a read-only Admin cannot delegate at all.
    """
    if _resolved_version(version, instance) < 2:
        return {action for study_id, action in delegable_authority(user) if study_id == study.pk}
    if not _active_account(user) or policy is None:
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
