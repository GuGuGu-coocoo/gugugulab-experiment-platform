"""Pure v2 authorization policy kernel (R02 milestone 1).

This module has no Django import and never reads the database. The caller
(``access.py``) passes validated canonical values and stable study UUIDs, and
the fixed catalogs below are the expected side, so the v2 computation can be
verified before the R02 storage fields are migrated in.

Semantics follow the R00 contract (2026-09-23, section A):

- the study and platform catalogs are finite and fixed; unknown actions are
  rejected and never granted, and no wildcard can open a future catalog entry;
- ``member.manage`` / ``permission.delegate`` keep historical rows only and
  have no v2 business meaning;
- Owner derives only from ``Instance.owner``: no role string, staff flag or
  stored grant becomes an owner here;
- unauthenticated, inactive, must-change-password or deleted subjects get
  nothing;
- otherwise platform = role default + explicit boolean overrides, and a study
  is the complete explicit override when present, else the Admin future bound
  / fixed template, else the user's explicit grants; without ``study.view`` the
  result is empty;
- a study is editable only with the actor's own ``study.view`` and
  ``study.configure``; a whole-account takeover must also dominate the target's
  stored platform and study permissions before and after the operation,
  including the future default of studies without an explicit override.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

# --- fixed catalogs (v2) ---------------------------------------------------

STUDY_ACTIONS = (
    'study.view',
    'study.configure',
    'build.upload',
    'build.preview',
    'release.approve_pilot',
    'recruitment.manage',
    'data.export_raw',
    'identity_mapping.read',
    'session.view',
    'session.recover',
    'audit.view',
    'study.delete',
)

PLATFORM_ACTIONS = (
    'study.create',
    'accounts.view',
    'accounts.create_user',
    'accounts.manage_user',
    'accounts.create_admin',
    'accounts.manage_admin',
    'accounts.delete_admin',
)

VIEW = 'study.view'
CONFIGURE = 'study.configure'

# Owner-controlled Admin lifecycle switches. An Admin holds one only when the
# Owner explicitly sets it and can never pass it on to another account.
OWNER_PLATFORM_SWITCHES = frozenset({
    'accounts.create_admin',
    'accounts.manage_admin',
    'accounts.delete_admin',
})

# Historical v1 actions without any v2 meaning.
LEGACY_ONLY_ACTIONS = frozenset({'member.manage', 'permission.delegate'})

ROLES = ('user', 'admin')

# Platform defaults per role; Owner holds every catalog entry.
ROLE_PLATFORM_DEFAULTS = {
    'user': frozenset({'study.create'}),
    'admin': frozenset({
        'study.create',
        'accounts.view',
        'accounts.create_user',
        'accounts.manage_user',
    }),
}

# Study template per role. Admins default to every business action; users hold
# only explicitly granted actions.
ROLE_STUDY_TEMPLATE = {
    'user': frozenset(),
    'admin': frozenset(STUDY_ACTIONS),
}


class PolicyError(ValueError):
    """A canonical v2 policy input violates the finite contract."""

    def __init__(self, code, detail=''):
        self.code = code
        self.detail = detail
        super().__init__(f'{code}: {detail}' if detail else code)


def _study_key(study_uuid):
    if isinstance(study_uuid, uuid.UUID):
        return str(study_uuid)
    if not isinstance(study_uuid, str):
        raise PolicyError('invalid_study', repr(study_uuid))
    try:
        return str(uuid.UUID(study_uuid))
    except (AttributeError, TypeError, ValueError):
        raise PolicyError('invalid_study', study_uuid) from None


def validate_actions(actions, catalog, *, label='actions'):
    """A finite iterable of catalog strings; unknown or mistyped -> reject."""
    if isinstance(actions, (str, bytes)) or isinstance(actions, Mapping) or not isinstance(actions, Iterable):
        raise PolicyError('invalid_actions', label)
    known = set(catalog)
    result = set()
    for action in actions:
        if not isinstance(action, str):
            raise PolicyError('invalid_action', f'{label}: {action!r}')
        if action not in known:
            raise PolicyError('unknown_action', f'{label}: {action}')
        result.add(action)
    return frozenset(result)


def validate_selection(actions, *, label='selection'):
    """A submitted selection: empty, ``study.view`` alone, or view plus actions.

    Invisible sub-actions are contradictory input and are refused; hiding a
    study returns an explicit empty list instead, and restoring visibility
    sends ``study.view`` alone without reviving old sub-actions.
    """
    result = validate_actions(actions, STUDY_ACTIONS, label=label)
    if result - {VIEW} and VIEW not in result:
        raise PolicyError('invisible_subaction', label)
    return result


def validate_platform_overrides(overrides):
    if overrides is None:
        return MappingProxyType({})
    if not isinstance(overrides, Mapping):
        raise PolicyError('invalid_platform_overrides', type(overrides).__name__)
    result = {}
    for action, enabled in overrides.items():
        if action not in PLATFORM_ACTIONS:
            raise PolicyError('unknown_action', f'platform_overrides: {action}')
        if not isinstance(enabled, bool):
            raise PolicyError('invalid_override_value', f'platform_overrides: {action}')
        result[action] = enabled
    return MappingProxyType(result)


def validate_study_overrides(overrides, *, label='study_overrides'):
    """Stored complete overrides: unknown actions are rejected.

    A stored list that lacks ``study.view`` but keeps sub-actions is not a
    write-time error here: the computation fails closed to an empty result, so
    old or hand-edited data can never widen access.
    """
    if overrides is None:
        return MappingProxyType({})
    if not isinstance(overrides, Mapping):
        raise PolicyError('invalid_study_actions', type(overrides).__name__)
    result = {}
    for key, actions in overrides.items():
        result[_study_key(key)] = validate_actions(actions, STUDY_ACTIONS, label=label)
    return MappingProxyType(result)


def validate_future_study_actions(actions):
    """The Owner-set Admin bound for studies without an explicit override."""
    if actions is None:
        return None
    result = validate_actions(actions, STUDY_ACTIONS, label='future_study_actions')
    if result - {VIEW} and VIEW not in result:
        raise PolicyError('invisible_subaction', 'future_study_actions')
    return result


def validate_grants(grants, *, label='grants'):
    """Explicit per-study actions derived from Grant rows.

    Only the finite v2 catalog is kept: historical rows such as
    ``member.manage`` or ``permission.delegate`` grant nothing in v2 and are
    ignored instead of invalidating the account.
    """
    if grants is None:
        return MappingProxyType({})
    if not isinstance(grants, Mapping):
        raise PolicyError('invalid_study_actions', label)
    known = set(STUDY_ACTIONS)
    result = {}
    for key, actions in grants.items():
        if isinstance(actions, (str, bytes)) or isinstance(actions, Mapping) or not isinstance(actions, Iterable):
            raise PolicyError('invalid_actions', label)
        result[_study_key(key)] = frozenset(
            action for action in actions if isinstance(action, str) and action in known)
    return MappingProxyType(result)


@dataclass(frozen=True)
class SubjectPolicy:
    """Validated canonical v2 inputs for one subject.

    ``is_instance_owner`` must be derived by the caller from ``Instance.owner``;
    nothing in this dataclass can turn a role string or a grant into an owner.
    Denial flags describe the account state (unauthenticated, inactive,
    must-change-password, deleted) and gate every effective computation.
    """

    role: str
    is_instance_owner: bool = False
    authenticated: bool = True
    active: bool = True
    must_change_password: bool = False
    deleted: bool = False
    platform_overrides: Mapping = field(default_factory=dict)
    study_overrides: Mapping = field(default_factory=dict)
    future_study_actions: frozenset | None = None
    grants: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.role, str) or self.role not in ROLES:
            raise PolicyError('unknown_role', repr(self.role))
        object.__setattr__(self, 'is_instance_owner', bool(self.is_instance_owner))
        object.__setattr__(self, 'authenticated', bool(self.authenticated))
        object.__setattr__(self, 'active', bool(self.active))
        object.__setattr__(self, 'must_change_password', bool(self.must_change_password))
        object.__setattr__(self, 'deleted', bool(self.deleted))
        object.__setattr__(self, 'platform_overrides', validate_platform_overrides(self.platform_overrides))
        object.__setattr__(self, 'study_overrides', validate_study_overrides(self.study_overrides))
        object.__setattr__(self, 'future_study_actions', validate_future_study_actions(self.future_study_actions))
        object.__setattr__(self, 'grants', validate_grants(self.grants))


def is_denied(policy):
    """Account state gate: denied subjects get no effective action at all."""
    return (not policy.authenticated or not policy.active
            or policy.must_change_password or policy.deleted)


# --- configured (stored) permissions --------------------------------------

def configured_platform_actions(policy):
    """Stored platform permissions regardless of the account state gate.

    Used by takeover / preview comparisons: re-enabling or resetting an
    inactive account must dominate what that account holds, not what it may use
    while it is inactive.
    """
    if policy.is_instance_owner:
        return frozenset(PLATFORM_ACTIONS)
    actions = set(ROLE_PLATFORM_DEFAULTS[policy.role])
    for action, enabled in policy.platform_overrides.items():
        if enabled:
            actions.add(action)
        else:
            actions.discard(action)
    return frozenset(actions)


def configured_default_study_actions(policy):
    """Stored actions on a study without an explicit per-subject override."""
    if policy.is_instance_owner:
        return frozenset(STUDY_ACTIONS)
    if policy.role != 'admin':
        return frozenset()
    bound = ROLE_STUDY_TEMPLATE['admin'] if policy.future_study_actions is None else policy.future_study_actions
    return frozenset(bound) if VIEW in bound else frozenset()


def configured_study_actions(policy, study_uuid):
    """Stored study actions regardless of the account state gate."""
    if policy.is_instance_owner:
        return frozenset(STUDY_ACTIONS)
    key = _study_key(study_uuid)
    if key in policy.study_overrides:
        selected = policy.study_overrides[key]
    elif policy.role == 'admin':
        selected = configured_default_study_actions(policy)
    else:
        selected = policy.grants.get(key, frozenset())
    return frozenset(selected) if VIEW in selected else frozenset()


# --- effective permissions -------------------------------------------------

def platform_actions(policy):
    """Effective platform actions: role default + explicit overrides."""
    if is_denied(policy):
        return frozenset()
    return configured_platform_actions(policy)


def default_study_actions(policy):
    """Effective actions on a study that has no explicit override yet."""
    if is_denied(policy):
        return frozenset()
    return configured_default_study_actions(policy)


def study_actions(policy, study_uuid):
    """Effective actions on one study.

    A complete explicit override wins; otherwise Admins follow the future bound
    / fixed template and users follow their explicit grants. Any source that
    lacks ``study.view`` yields the empty set.
    """
    if is_denied(policy):
        return frozenset()
    return configured_study_actions(policy, study_uuid)


def allows(policy, study_uuid, action):
    """One action decision on one study; unknown actions are never granted."""
    return isinstance(action, str) and action in study_actions(policy, study_uuid)


def allows_platform(policy, action):
    """One platform decision; unknown actions are never granted."""
    return isinstance(action, str) and action in platform_actions(policy)


# --- management and takeover guards ---------------------------------------

def can_manage_study(policy, study_uuid):
    """Editing another account's actions here needs view and configure."""
    actions = study_actions(policy, study_uuid)
    return VIEW in actions and CONFIGURE in actions


def assignable_study_actions(policy, study_uuid):
    """Actions the actor may assign on a study: only their own effective set,
    and only while they hold ``study.view`` and ``study.configure`` there."""
    if is_denied(policy):
        return frozenset()
    if policy.is_instance_owner:
        return frozenset(STUDY_ACTIONS)
    if not can_manage_study(policy, study_uuid):
        return frozenset()
    return study_actions(policy, study_uuid)


def assignable_platform_actions(policy):
    """Platform actions the actor may grant: Owner everything, an Admin never
    the three Owner-controlled Admin lifecycle switches."""
    actions = platform_actions(policy)
    if policy.is_instance_owner:
        return actions
    return actions - OWNER_PLATFORM_SWITCHES


def writable_permission_target(*, target_is_self, target_is_owner):
    """Permission rows for oneself and for the instance Owner are read-only."""
    return not target_is_self and not target_is_owner


def can_take_over(actor, target_before, target_after, study_uuids):
    """Whole-account takeover guard (reset, enable, role change, delete, ...).

    The actor must dominate the target's stored platform and study permissions
    before and after the operation, including the target's future default for
    studies without an explicit override; ``study.create`` and every other
    platform action count. A study the target holds anything on also requires
    the actor's own ``study.view`` + ``study.configure`` there, so using an
    action is never the same as managing it. A non-Owner actor may not change
    the Owner-controlled Admin switches in either direction.
    """
    if is_denied(actor):
        return False
    if actor.is_instance_owner:
        return True
    actor_platform = configured_platform_actions(actor)
    before_platform = configured_platform_actions(target_before)
    after_platform = configured_platform_actions(target_after)
    if not before_platform <= actor_platform or not after_platform <= actor_platform:
        return False
    if (before_platform ^ after_platform) & OWNER_PLATFORM_SWITCHES:
        return False
    actor_default = configured_default_study_actions(actor)
    if not configured_default_study_actions(target_before) <= actor_default:
        return False
    if not configured_default_study_actions(target_after) <= actor_default:
        return False
    for study_uuid in study_uuids:
        need = configured_study_actions(target_before, study_uuid) | configured_study_actions(target_after, study_uuid)
        if not need:
            continue
        actor_actions = configured_study_actions(actor, study_uuid)
        if not need <= actor_actions:
            return False
        if VIEW not in actor_actions or CONFIGURE not in actor_actions:
            return False
    return True
