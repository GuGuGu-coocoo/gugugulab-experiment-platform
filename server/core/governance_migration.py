"""Owner-bound v1 -> v2 policy enablement (R02 milestone 2, P03R02BR corrections).

This module is the only server-side entry that may switch an instance from the
legacy authorization rules to the v2 kernel. It never accepts a caller-supplied
owner flag: the canonical policy is always built from the authenticated actor
plus the stored ``Instance.owner``, and the enablement transaction only writes
when that same account is still the Owner and re-authenticates with its own
password.

Contract (R00, section B, 2026-09-23; P03R02BR review corrections):

- the read-only difference is conservative: it never assumes that a missing
  legacy Grant accepts the v2 role default, and it applies only audit-proven
  revocations as exceptions;
- audit state is computed from the newest *complete boolean* before/after pair
  per (subject, study, action): ``true -> false`` proves a revocation,
  ``false -> true`` clears it again, ``true -> true`` stays granted and
  ``false -> false`` is not evidence. Malformed or missing evidence, and a
  legacy ``revoke_member`` row without the structured pair, are listed as
  unknown items instead of being guessed into a specific revocation;
- every legacy Admin study where v2 would add actions without an explicit
  grant gets a subject/study-traceable unknown item with an explicit choice
  between keeping the old effective set (stored as a finite complete override)
  and adopting the fixed v2 set; the Admin future default needs the same
  explicit choice (fixed v2 template or conservative empty set);
- the preview computes the *final* strategy after the Owner's choices and the
  audit exceptions, per subject / study / action and per platform action, and
  the binding digest covers the instance UUID, the Owner, the stable subject
  principals, the complete observed state, the revision, the choices and the
  final projection. Confirmation re-computes all of it from storage inside one
  transaction and applies exactly that projection;
- a repeated confirm never re-applies or resets the stored policy: an
  already-enabled instance is refused before any write.

The scope comparison helpers in :mod:`core.authorization` are not lifecycle
authorizations; the later all-entry switch (R02C) must combine them with the
role/platform target eligibility rules.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from . import access
from .access import (ACTIONS, ADMIN_STUDY_TEMPLATE, PLATFORM_V2_ACTIONS,
                     STUDY_V2_ACTIONS, VIEW_ACTION)
from .models import (AccountInvitation, AccountProfile, Audit, Grant, Instance,
                     Invitation, PermissionPreview, Principal, Study)
from .protocol import Rejected, require

ENABLEMENT_KIND = 'migration_enable'
ENABLEMENT_TTL = timedelta(minutes=10)
INSTANCE_PK = 1

# Audit actions that may carry structured evidence about one permission. Every
# other audit row is history, not evidence about a specific (subject, study,
# action) grant.
REVOCATION_ACTIONS = ('permission.matrix_changed', 'access.conflict_resolved', 'revoke_member')

# Explicit Owner choices for unknown items. The first listed choice of every
# item kind is the conservative one, so a client that submits the first value
# never silently adopts the v2 default.
CHOICE_KEEP_LEGACY = 'keep_legacy'
CHOICE_ADOPT_V2 = 'adopt_v2'
CHOICE_KEEP_EMPTY = 'keep_empty'
CHOICE_KEEP_NULL = 'keep_null'
CHOICE_DROP_LEGACY = 'drop_legacy'
CHOICE_ACKNOWLEDGE = 'acknowledge_unknown'

# The v2 platform actions that already had a v1 counterpart. The v1 Owner
# controls the whole instance (including Admin appointment and study creation);
# a v1 Admin could view accounts, invite ordinary users and manage ordinary
# user lifecycle. A v1 Admin could *also* reset or disable a peer Admin, but only
# per target and only under the study dominance rule: that conditional capability
# is represented by ``accounts.manage_admin`` here (the closest finite mapping)
# and is reported as *removed* when the v2 default does not carry it. It is not a
# blanket switch, and appointment/demotion (``accounts.create_admin``) and
# deletion (``accounts.delete_admin``) never existed for a v1 Admin, nor could an
# Admin create studies (``study.create``).
LEGACY_PLATFORM_ADMIN = frozenset({'accounts.view', 'accounts.create_user',
                                   'accounts.manage_user', 'accounts.manage_admin'})
LEGACY_ADMIN_PLATFORM_NOTE = (
    'legacy v1 peer-Admin reset/disable was allowed per target under the study dominance rule; '
    'accounts.manage_admin is the closest finite mapping and is not a blanket v2 switch; '
    'appointment/demotion (accounts.create_admin) and deletion (accounts.delete_admin) did not exist '
    'for a v1 Admin, and study.create was Owner-only')


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def instance_version(instance):
    """Exact stored version; a pre-0010 row without the field is legacy 1."""
    return access.exact_policy_version(getattr(instance, 'authorization_version', 1))


def _locked_instance():
    return Instance.objects.select_for_update().get(pk=INSTANCE_PK)


def _revision_matches(raw, current):
    try:
        return int(raw) == current
    except (TypeError, ValueError):
        return False


def _audit(actor, action, target, before=None, after=None, study=None):
    principal = Principal.objects.filter(user_id=actor.pk).first()
    return Audit.objects.create(study=study, actor=actor, actor_principal=principal,
                                action=action, target=str(target), before=before, after=after)


def canonical_policy(user, *, instance=None, profile=None):
    """Single kernel entry for stored v2 policies (see :func:`access.canonical_policy`)."""
    return access.canonical_policy(user, instance=instance, profile=profile)


# --- audit evidence ---------------------------------------------------------

def _parse_pair_target(target):
    """``user_pk:study_uuid`` -> (user_id, study_key); anything else is None."""
    try:
        user_text, _, study_text = (target or '').partition(':')
        return int(user_text), str(uuid.UUID(study_text))
    except (AttributeError, TypeError, ValueError):
        return None, None


def _granted(entry):
    """A stored boolean state: only a real ``bool`` counts, never a truthy stand-in."""
    if not isinstance(entry, Mapping):
        return False, False
    value = entry.get('granted')
    if not isinstance(value, bool):
        return False, False
    return True, value


def _audit_state(users, studies):
    """Newest reliable audit state per (subject, study, action).

    Returns ``(state, evidence, unproven)`` where ``state`` maps a key to
    ``True`` (granted), ``False`` (revoked) or ``None`` (unknown), and
    ``unproven`` lists rows that cannot be tied to a specific action at all.
    """
    state, evidence, unproven = {}, {}, []
    rows = Audit.objects.filter(action__in=REVOCATION_ACTIONS).order_by('pk').values_list(
        'id', 'action', 'target', 'before', 'after', 'study_id')
    for row_id, action_name, target, before, after, study_id in rows:
        user_id, study_key = _parse_pair_target(target)
        if study_key is None and study_id is not None:
            candidate = str(study_id)
            study_key = candidate if candidate in studies else None
        tie = user_id in users and study_key in studies
        if action_name == 'access.conflict_resolved' and tie:
            chosen = (before or {}).get('choice')
            before_actions, after_actions = (before or {}).get('actions'), (after or {}).get('actions')
            if (chosen in (None, 'remove_conflicting', 'grant_view')
                    and isinstance(before_actions, list) and isinstance(after_actions, list)
                    and all(isinstance(action, str) for action in before_actions + after_actions)):
                # Complete snapshot lists: the after list is the whole truth.
                kept = set(after_actions)
                for action in sorted(set(before_actions) | kept):
                    if action not in STUDY_V2_ACTIONS:
                        continue
                    key = (user_id, study_key, action)
                    state[key] = action in kept
                    evidence[key] = (row_id, action_name)
                continue
            unproven.append({'audit_id': row_id, 'audit_action': action_name, 'pair': None})
            continue
        before, after = before or {}, after or {}
        before_actions, after_actions = before.get('actions'), after.get('actions')
        if tie and isinstance(before_actions, Mapping) and isinstance(after_actions, Mapping):
            for action in sorted(set(before_actions) | set(after_actions)):
                if action not in STUDY_V2_ACTIONS:
                    continue
                before_ok, before_value = _granted(before_actions.get(action))
                after_ok, after_value = _granted(after_actions.get(action))
                key = (user_id, study_key, action)
                if not (before_ok and after_ok):
                    state[key] = None
                elif before_value and not after_value:
                    state[key] = False
                elif not before_value and after_value:
                    state[key] = True
                elif before_value and after_value:
                    state[key] = True
                else:  # false -> false is not evidence of anything
                    continue
                evidence[key] = (row_id, action_name)
            continue
        if action_name == 'revoke_member' and not tie:
            # The legacy handler deleted grants and wrote no per-action detail;
            # which action was revoked is not provable from this row.
            unproven.append({'audit_id': row_id, 'audit_action': action_name,
                             'pair': None,
                             'user_id': user_id if user_id in users else None,
                             'study': study_key if study_key in studies else None})
            continue
        if tie:
            # A structured governance mutation whose before/after pair is
            # unusable stays a pair-level unknown; never a guessed revocation.
            unproven.append({'audit_id': row_id, 'audit_action': action_name,
                             'pair': (user_id, study_key)})
    return state, evidence, unproven


def _audit_evidence(users, studies):
    """Reliable revocation exceptions plus unknown items for unusable evidence."""
    state, evidence, unproven = _audit_state(users, studies)
    exceptions, unknown = [], []
    for (user_id, study_key, action), granted in sorted(state.items()):
        row_id, action_name = evidence[(user_id, study_key, action)]
        if granted is None:
            unknown.append({'id': f'audit:{user_id}:{study_key}:{action}', 'kind': 'audit_unknown',
                            'subject': users[user_id].username, 'user_id': user_id,
                            'study': study_key, 'study_title': studies[study_key].title,
                            'action': action,
                            'detail': 'the newest audit evidence for this permission is incomplete or '
                                      'malformed; it is not treated as a revocation',
                            'choices': [CHOICE_ACKNOWLEDGE]})
        elif granted is False:
            exceptions.append({'id': f'revoked:{user_id}:{study_key}:{action}',
                               'kind': 'audit_revocation',
                               'subject': users[user_id].username, 'user_id': user_id,
                               'study': study_key, 'study_title': studies[study_key].title,
                               'action': action,
                               'evidence': {'audit_id': row_id, 'audit_action': action_name},
                               'choices': []})
    for row in unproven:
        pair = row.get('pair')
        if pair:
            user_id, study_key = pair
            item_id = f'audit:{row["audit_id"]}:{user_id}:{study_key}'
            subject, study, title = users[user_id].username, study_key, studies[study_key].title
        else:
            user_id, study_key = row.get('user_id'), row.get('study')
            item_id = f'audit:{row["audit_id"]}'
            subject = users[user_id].username if user_id in users else None
            study = study_key
            title = studies[study_key].title if study_key in studies else None
        unknown.append({'id': item_id, 'kind': 'audit_unknown', 'subject': subject,
                        'user_id': user_id, 'study': study, 'study_title': title,
                        'action': None,
                        'detail': f'{row["audit_action"]} audit #{row["audit_id"]} has no complete '
                                  'before/after evidence and cannot prove which action was revoked',
                        'choices': [CHOICE_ACKNOWLEDGE]})
    exceptions.sort(key=lambda item: item['id'])
    unknown.sort(key=lambda item: item['id'])
    return exceptions, unknown


# --- observed state ---------------------------------------------------------

def _legacy_grant_set(rows):
    """Version-1 stored actions for one subject/study pair, view-gated.

    State-independent on purpose: the migration compares *stored* policies, and
    an inactive account is already denied by the v2 state gate.
    """
    actions = frozenset(action for action in rows if action in ACTIONS)
    return actions if VIEW_ACTION in actions else frozenset()


def _legacy_platform_actions(policy):
    """The v1 platform capabilities of this subject (Owner: the whole instance)."""
    if policy.is_instance_owner:
        return frozenset(PLATFORM_V2_ACTIONS)
    if policy.role == 'admin':
        return LEGACY_PLATFORM_ADMIN
    return frozenset()


def _collect(instance):
    """Read-only snapshot: subjects, studies, grants, audit evidence, unknowns."""
    User = get_user_model()
    users = {user.pk: user for user in User.objects.order_by('pk')}
    studies = {str(study.pk): study for study in Study.objects.order_by('title', 'id')}
    principals = {principal.user_id: principal for principal in Principal.objects.filter(user_id__in=users)}
    profiles = {profile.user_id: profile for profile in AccountProfile.objects.filter(user_id__in=users)}
    raw_grants = {}
    for user_id, study_id, action in Grant.objects.filter(user_id__in=users).values_list(
            'user_id', 'study_id', 'action'):
        raw_grants.setdefault(user_id, {}).setdefault(str(study_id), set()).add(action)
    exceptions, unknown = _audit_evidence(users, studies)

    subjects = []
    for user_id, user in sorted(users.items()):
        policy = access.canonical_policy(user, instance=instance)
        profile = profiles.get(user_id)
        principal = principals.get(user_id)
        legacy, overrides = {}, {}
        for key in studies:
            legacy[key] = _legacy_grant_set(raw_grants.get(user_id, {}).get(key, set()))
            override = policy.study_overrides.get(key)
            if override is not None:
                overrides[key] = frozenset(override)
        subjects.append({'user_id': user_id, 'username': user.username, 'role': policy.role,
                         'policy': policy, 'is_owner': policy.is_instance_owner,
                         'is_active': user.is_active, 'must_change_password': policy.must_change_password,
                         'principal': principal, 'legacy': legacy, 'overrides': overrides,
                         'future_stored': policy.future_study_actions,
                         # Complete observed subject state: the preview binding
                         # and the confirmation re-check both cover these facts,
                         # not merely the projected action sets.
                         'state': {
                             'role': policy.role,
                             'is_owner': policy.is_instance_owner,
                             'is_active': bool(user.is_active),
                             'must_change_password': policy.must_change_password,
                             'deleted': policy.deleted,
                             'policy_version': profile.policy_version if profile is not None else None,
                             'auth_version': profile.auth_version if profile is not None else None,
                             'revision': profile.revision if profile is not None else None,
                             'principal': str(principal.pk) if principal is not None else None,
                             'principal_deleted_at': (principal.deleted_at.isoformat()
                                                      if principal is not None and principal.deleted_at else None),
                             'stored_platform_overrides': dict(profile.platform_overrides or {}) if profile else {},
                             'stored_study_overrides': dict(profile.study_overrides or {}) if profile else {},
                             'stored_future_study_actions': profile.future_study_actions if profile else None,
                         },
                         'missing_keys': set()})

    for subject in subjects:
        if subject['is_owner'] or subject['role'] != 'admin':
            continue
        policy = subject['policy']
        for key in sorted(studies):
            if key in subject['overrides']:
                continue
            v2_default = access.configured_study_actions(policy, key)
            added = set(v2_default) - set(subject['legacy'][key])
            if not added:
                continue
            subject['missing_keys'].add(key)
            unknown.append({'id': f'missing:{subject["user_id"]}:{key}',
                            'kind': 'admin_missing_permissions',
                            'subject': subject['username'], 'user_id': subject['user_id'],
                            'study': key, 'study_title': studies[key].title, 'action': None,
                            'added': sorted(added),
                            'detail': 'this legacy Admin has no explicit Grant for '
                                      f'{len(added)} action(s) that the v2 default would add; '
                                      'the missing Grant is not treated as accepting the default',
                            'choices': [CHOICE_KEEP_LEGACY, CHOICE_ADOPT_V2]})
        if policy.future_study_actions is None and ADMIN_STUDY_TEMPLATE:
            unknown.append({'id': f'future:{subject["user_id"]}', 'kind': 'admin_future_default',
                            'subject': subject['username'], 'user_id': subject['user_id'],
                            'study': None, 'study_title': None, 'action': None,
                            'future_actions': sorted(ADMIN_STUDY_TEMPLATE),
                            'detail': 'the v2 future default would give this Admin the fixed template '
                                      'on later studies; choose the fixed set or a conservative empty set',
                            'choices': [CHOICE_KEEP_EMPTY, CHOICE_ADOPT_V2]})

    for study_key, study in sorted(studies.items()):
        if study.creator_principal_id is None:
            unknown.append({'id': f'creator:{study_key}', 'kind': 'legacy_creator',
                            'subject': None, 'user_id': None, 'study': study_key,
                            'study_title': study.title, 'action': None,
                            'detail': 'no provable creator evidence; creator_principal stays null',
                            'choices': [CHOICE_KEEP_NULL]})
    for user_id, rows in sorted(raw_grants.items()):
        for study_key, actions in sorted(rows.items()):
            for action in sorted(actions):
                if action not in STUDY_V2_ACTIONS:
                    unknown.append({'id': f'legacy:{user_id}:{study_key}:{action}',
                                    'kind': 'legacy_action',
                                    'subject': users[user_id].username, 'user_id': user_id,
                                    'study': study_key, 'study_title': studies[study_key].title,
                                    'action': action,
                                    'detail': 'stored v1 action has no v2 meaning and is dropped '
                                              'unless acknowledged',
                                    'choices': [CHOICE_DROP_LEGACY]})
    unknown.sort(key=lambda item: item['id'])
    revoked = {}
    for item in exceptions:
        revoked.setdefault((item['user_id'], item['study']), set()).add(item['action'])
    return {'instance': instance, 'users': users, 'studies': studies,
            'subjects': subjects, 'unknown': unknown, 'exceptions': exceptions, 'revoked': revoked}


def _conservative_choices(unknown):
    """What a cautious Owner would submit: never expand anything by default."""
    return {item['id']: item['choices'][0] for item in unknown if item['choices']}


def _row(study_key, title, legacy, actions, *, choice, basis, revoked, frozen):
    legacy, actions = set(legacy), set(actions)
    return {'study': study_key, 'title': title, 'choice': choice, 'basis': basis,
            'legacy': sorted(legacy), 'actions': sorted(actions), 'revoked': sorted(revoked),
            'added': sorted(actions - legacy), 'removed': sorted(legacy - actions),
            'preserved': sorted(legacy & actions), 'frozen': frozen}


def _projection(state, choices):
    """Final strategy rows after the given choices and the audit exceptions.

    ``frozen`` marks a subject/study whose final set must be stored as an
    explicit complete override; ``future.write`` is not ``None`` when the
    future default must be written as an explicit bound.
    """
    instance = state['instance']
    revoked_map = state['revoked']
    unknown_ids = {item['id'] for item in state['unknown']}
    subjects = []
    for subject in state['subjects']:
        user_id, role = subject['user_id'], subject['role']
        policy = subject['policy']
        if subject['is_owner']:
            platform_legacy = platform_final = frozenset(PLATFORM_V2_ACTIONS)
            future = {'choice': None, 'actions': sorted(STUDY_V2_ACTIONS), 'source': 'owner',
                      'write': None}
            # The "before" column is the *real* v1 route: an Owner without an
            # explicit Grant could not view or manage a study either, so v2
            # access there is newly added, never "preserved".
            rows = [_row(key, state['studies'][key].title, subject['legacy'][key], STUDY_V2_ACTIONS,
                         choice=None, basis='owner', revoked=[], frozen=False)
                    for key in sorted(state['studies'])]
        else:
            platform_final = access.configured_platform_actions(policy)
            platform_legacy = _legacy_platform_actions(policy)
            future_id = f'future:{user_id}'
            future_choice = choices.get(future_id) if future_id in unknown_ids else None
            if role == 'admin':
                if subject['future_stored'] is not None:
                    future = {'choice': None, 'actions': sorted(subject['future_stored']),
                              'source': 'stored_bound', 'write': None}
                elif future_choice == CHOICE_KEEP_EMPTY:
                    future = {'choice': future_choice, 'actions': [], 'source': 'conservative_empty',
                              'write': []}
                else:
                    future = {'choice': future_choice, 'actions': sorted(ADMIN_STUDY_TEMPLATE),
                              'source': 'v2_default', 'write': None}
            else:
                future = {'choice': None, 'actions': [], 'source': 'role_default', 'write': None}
            freeze_existing = (role == 'admin' and subject['future_stored'] is None
                               and future_choice == CHOICE_KEEP_EMPTY)
            rows = []
            for key in sorted(state['studies']):
                legacy = subject['legacy'][key]
                legacy_v2 = set(legacy) & set(STUDY_V2_ACTIONS)
                revoked_here = set(revoked_map.get((user_id, key), set()))
                choice = choices.get(f'missing:{user_id}:{key}') if key in subject['missing_keys'] else None
                v2_default = access.configured_study_actions(policy, key)
                if key in subject['overrides']:
                    base, basis = set(subject['overrides'][key]), 'existing_override'
                elif role == 'admin':
                    if choice == CHOICE_ADOPT_V2:
                        base, basis = set(ADMIN_STUDY_TEMPLATE), 'owner_choice_adopt_v2'
                    elif choice == CHOICE_KEEP_LEGACY:
                        base, basis = set(legacy_v2), 'owner_choice_keep_legacy'
                    elif key in subject['missing_keys']:
                        base, basis = set(legacy_v2), 'conservative_keep_legacy'
                    else:
                        base, basis = set(v2_default), 'v2_default'
                else:
                    base, basis = set(legacy_v2), 'legacy_grants'
                removed_here = revoked_here & base
                final = base - revoked_here
                if VIEW_ACTION not in final:
                    final = set()
                if choice is not None:
                    frozen = True
                elif key in subject['overrides']:
                    frozen = final != base
                elif role == 'admin':
                    frozen = final != set(v2_default) or freeze_existing
                else:
                    frozen = final != legacy_v2
                rows.append(_row(key, state['studies'][key].title, legacy, final, choice=choice,
                                 basis=basis, revoked=sorted(removed_here), frozen=frozen))
        counts = {'platform_added': len(platform_final - platform_legacy),
                  'platform_removed': len(platform_legacy - platform_final),
                  'platform_preserved': len(platform_legacy & platform_final),
                  'study_added': sum(len(row['added']) for row in rows),
                  'study_removed': sum(len(row['removed']) for row in rows),
                  'study_preserved': sum(len(row['preserved']) for row in rows),
                  'frozen_overrides': sum(1 for row in rows if row['frozen']),
                  'future_defaults': 1 if future['write'] is not None else 0}
        subjects.append({'user_id': user_id, 'username': subject['username'], 'role': role,
                         'is_owner': subject['is_owner'],
                         'principal': str(subject['principal'].pk) if subject['principal'] else None,
                         'state': subject['state'],
                         # The legacy Admin peer-lifecycle mapping is conditional
                         # and not one-to-one with the v2 switches; state it.
                         'platform_note': (LEGACY_ADMIN_PLATFORM_NOTE
                                           if role == 'admin' and not subject['is_owner'] else None),
                         'platform': {'added': sorted(platform_final - platform_legacy),
                                      'removed': sorted(platform_legacy - platform_final),
                                      'preserved': sorted(platform_legacy & platform_final)},
                         'studies': rows, 'future': future, 'counts': counts})
    totals = {'subjects': len(subjects), 'unknown': len(state['unknown']),
              'exceptions': len(state['exceptions'])}
    for key in ('platform_added', 'platform_removed', 'platform_preserved', 'study_added',
                'study_removed', 'study_preserved', 'frozen_overrides', 'future_defaults'):
        totals[key] = sum(subject['counts'][key] for subject in subjects)
    projection = {'instance_id': str(instance.instance_id), 'revision': instance.governance_revision,
                  'subjects': subjects, 'counts': totals}
    projection['digest'] = _digest({key: value for key, value in projection.items() if key != 'digest'})
    return projection


def _owner_principal(state):
    owner_id = state['instance'].owner_id
    for subject in state['subjects']:
        if subject['user_id'] == owner_id and subject['principal'] is not None:
            return str(subject['principal'].pk)
    return None


def _build_diff(state):
    """Read-only payload with the conservative projection and its digest."""
    projection = _projection(state, _conservative_choices(state['unknown']))
    instance = state['instance']
    subjects = [{'user_id': row['user_id'], 'username': row['username'], 'role': row['role'],
                 'is_owner': row['is_owner'], 'principal': row['principal'],
                 'state': row['state'], 'platform_note': row['platform_note'],
                 'platform': row['platform'], 'studies': row['studies'], 'future': row['future']}
                for row in projection['subjects']]
    diff = {'authorization_version': instance_version(instance),
            'revision': instance.governance_revision,
            'instance_id': str(instance.instance_id),
            'owner': {'user_id': instance.owner_id, 'principal': _owner_principal(state)},
            'subjects': subjects, 'unknown': state['unknown'], 'exceptions': state['exceptions'],
            'counts': {**projection['counts'], 'subjects': len(subjects),
                       'unknown': len(state['unknown']), 'exceptions': len(state['exceptions'])},
            'conservative_projection_digest': projection['digest']}
    diff['digest'] = _digest({key: value for key, value in diff.items() if key != 'digest'})
    return diff


def read_diff(*, instance=None):
    """Read-only conservative v1 -> v2 difference for every subject and study.

    Nothing is written and nothing is enabled. A missing Grant is never treated
    as accepting the v2 default; the projection shown here is the conservative
    one (keep the legacy effective sets, keep future studies empty), while the
    preview recomputes the final strategy from the Owner's explicit choices.
    """
    if instance is None:
        instance = Instance.objects.first()
    require(instance is not None, 'instance_missing', 409)
    return _build_diff(_collect(instance))


# --- Owner choices and enablement -------------------------------------------

def normalize_choices(choices, diff):
    """Exactly one supported choice per unknown item; nothing more, nothing less."""
    if choices is None:
        choices = {}
    if not isinstance(choices, dict):
        raise Rejected('unknown_choice_invalid', 409)
    known = {item['id']: item for item in diff['unknown']}
    missing = sorted(set(known) - set(choices))
    if missing:
        raise Rejected('unknown_choice_required', 409)
    extra = sorted(key for key in choices if key not in known)
    if extra:
        raise Rejected('unknown_choice_unknown', 409)
    normalized = {}
    for key in sorted(known):
        value = choices[key]
        if value not in known[key]['choices']:
            raise Rejected('unknown_choice_invalid', 409)
        normalized[key] = value
    return normalized


def _binding(instance, diff, choices, projection_digest):
    return _digest({'kind': ENABLEMENT_KIND, 'instance': str(instance.instance_id),
                    'owner': instance.owner_id, 'revision': instance.governance_revision,
                    'version': instance_version(instance), 'diff': diff['digest'],
                    'choices': choices, 'projection': projection_digest})


def _require_owner(instance, locked):
    require(instance.owner_id == locked.pk, 'owner_only', 403)
    require(not (AccountProfile.objects.filter(user_id=locked.pk, must_change_password=True).exists()),
            'password_change_required', 403)


def _enablement_state(instance):
    state = _collect(instance)
    diff = _build_diff(state)
    return state, diff


def page_state():
    """Cheap read-only state for the Owner page; never writes and never enables."""
    instance = Instance.objects.first()
    if instance is None:
        return {'state': 'missing'}
    version = instance_version(instance)
    if version == 2:
        return {'state': 'enabled', 'revision': instance.governance_revision}
    if version is None:
        return {'state': 'unsupported', 'stored': repr(getattr(instance, 'authorization_version', None))}
    if AccountProfile.objects.exclude(policy_version__in=access.SUPPORTED_POLICY_VERSIONS).exists():
        return {'state': 'invalid'}
    return {'state': 'available', 'revision': instance.governance_revision}


def diff_payload():
    """The read-only per-subject/study/action difference (no writes at all)."""
    instance = Instance.objects.first()
    require(instance is not None, 'instance_missing', 409)
    diff = read_diff(instance=instance)
    return {'diff': diff, 'digest': diff['digest'], 'revision': diff['revision'],
            'counts': diff['counts'], 'unknown': diff['unknown'],
            'exceptions': diff['exceptions']}


def preview_enablement(actor, choices):
    """Read-only final strategy plus a persisted, single-use confirmation identity.

    The preview computes the exact strategy that confirmation would apply
    (choices plus audit exceptions) and stores the Owner's explicit choices
    together with the digest of the observed difference, the projection and the
    current governance revision.
    """
    require(getattr(actor, 'is_authenticated', False) and getattr(actor, 'pk', None), 'auth_required', 403)
    with transaction.atomic():
        instance = _locked_instance()
        locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
        require(locked.is_active, 'auth_required', 403)
        _require_owner(instance, locked)
        version = instance_version(instance)
        if version is None:
            raise Rejected('unsupported_version', 409)
        require(version == 1, 'already_enabled', 409)
        state, diff = _enablement_state(instance)
        normalized = normalize_choices(choices, diff)
        state['choices'] = normalized
        projection = _projection(state, normalized)
        row = PermissionPreview.objects.create(
            actor=locked, kind=ENABLEMENT_KIND, scope='', summary=_summary(state, diff, projection),
            errors=[], binding=_binding(instance, diff, normalized, projection['digest']),
            base_revision=instance.governance_revision,
            staged={'choices': normalized, 'diff_digest': diff['digest'],
                    'projection_digest': projection['digest']},
            expires_at=timezone.now() + ENABLEMENT_TTL)
    return row


def _summary(state, diff, projection):
    return {'authorization_version': 1, 'revision': state['instance'].governance_revision,
            'instance_id': str(state['instance'].instance_id),
            'owner': {'user_id': state['instance'].owner_id,
                      'principal': _owner_principal(state)},
            'digest': projection['digest'],
            'diff_digest': diff['digest'], 'projection_digest': projection['digest'],
            'counts': projection['counts'], 'choices': state['choices'],
            'subjects': projection['subjects'], 'unknown': diff['unknown'],
            'exceptions': diff['exceptions']}


def _apply(actor, instance, state, projection):
    """Freeze the final strategy as explicit stored policy; one audit per write."""
    applied = {'overrides': [], 'future_defaults': []}
    studies = state['studies']
    for subject in projection['subjects']:
        if subject['is_owner']:
            continue
        rows = [row for row in subject['studies'] if row['frozen']]
        write_future = subject['future']['write'] is not None
        if not rows and not write_future:
            continue
        user_id = subject['user_id']
        locked = get_user_model().objects.select_for_update().get(pk=user_id)
        profile = AccountProfile.objects.select_for_update().filter(user_id=user_id).first()
        if profile is None:
            profile = AccountProfile.objects.create(user=locked, role='user')
        original = dict(profile.study_overrides or {})
        stored = dict(original)
        fields = []
        for row in rows:
            key = row['study']
            value = list(row['actions'])
            if stored.get(key) == value:
                continue
            _audit(actor, 'policy.v2_override_frozen', f'{user_id}:{key}',
                   before={'actions': row['legacy'], 'stored': sorted(stored.get(key) or [])},
                   after={'actions': value, 'choice': row['choice'], 'basis': row['basis'],
                          'revoked': row['revoked']},
                   study=studies.get(key))
            stored[key] = value
            applied['overrides'].append({'user_id': user_id, 'study': key, 'actions': value,
                                         'choice': row['choice'], 'revoked': row['revoked']})
        if stored != original:
            profile.study_overrides = stored
            fields.append('study_overrides')
        if write_future:
            profile.future_study_actions = list(subject['future']['write'])
            fields.append('future_study_actions')
            _audit(actor, 'policy.v2_future_default_set', str(user_id),
                   before={'future_study_actions': None},
                   after={'future_study_actions': subject['future']['write'],
                          'choice': subject['future']['choice']})
            applied['future_defaults'].append({'user_id': user_id,
                                               'actions': subject['future']['write'],
                                               'choice': subject['future']['choice']})
        if fields:
            profile.save(update_fields=fields)
    return applied


def _invalidate_old_credentials():
    now = timezone.now()
    return {
        'account_invitations_revoked': AccountInvitation.objects.filter(consumed=False, revoked=False).update(revoked=True),
        'study_invitations_revoked': Invitation.objects.filter(consumed=False, revoked=False).update(revoked=True),
        'previews_invalidated': PermissionPreview.objects.filter(consumed=False, expires_at__gt=now).update(expires_at=now, staged=None),
    }


def confirm_enablement(actor, password, revision, preview_id):
    """Owner-confirmed v1 -> v2 switch of exactly the previewed final strategy."""
    require(bool(preview_id), 'preview_required', 400)
    with transaction.atomic():
        instance = _locked_instance()
        locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
        require(locked.is_active, 'auth_required', 403)
        _require_owner(instance, locked)
        require(bool(password) and check_password(password, locked.password), 'reauth_failed', 403)
        require(_revision_matches(revision, instance.governance_revision), 'revision_conflict', 409)
        version = instance_version(instance)
        if version is None:
            raise Rejected('unsupported_version', 409)
        require(version == 1, 'already_enabled', 409)
        try:
            row = PermissionPreview.objects.select_for_update().get(pk=preview_id)
        except (PermissionPreview.DoesNotExist, ValidationError, ValueError):
            raise Rejected('preview_invalid', 404) from None
        require(row.actor_id == locked.pk and row.kind == ENABLEMENT_KIND and row.scope == '', 'preview_invalid', 404)
        require(not row.consumed, 'preview_invalid', 404)
        require(row.expires_at > timezone.now(), 'preview_expired', 409)
        state, diff = _enablement_state(instance)
        staged = row.staged or {}
        require(staged.get('diff_digest') == diff['digest'], 'preview_stale', 409)
        choices = normalize_choices(staged.get('choices') or {}, diff)
        state['choices'] = choices
        projection = _projection(state, choices)
        require(staged.get('projection_digest') == projection['digest'], 'preview_stale', 409)
        require(_binding(instance, diff, choices, projection['digest']) == row.binding, 'preview_stale', 409)

        applied = _apply(locked, instance, state, projection)
        invalidated = _invalidate_old_credentials()

        before = {'authorization_version': 1, 'revision': instance.governance_revision}
        instance.authorization_version = 2
        instance.governance_revision += 1
        instance.save(update_fields=['authorization_version', 'governance_revision'])
        # Audit inside the same transaction: a failed audit write rolls the
        # version, the stored policy and the invalidations back together.
        _audit(locked, 'policy.v2_enabled', instance.pk, before=before,
               after={'authorization_version': 2, 'revision': instance.governance_revision,
                      'acknowledged_unknown': choices, 'applied': applied,
                      'invalidated': invalidated})
        result = {'authorization_version': 2, 'revision': instance.governance_revision,
                  'overrides': applied['overrides'], 'future_defaults': applied['future_defaults'],
                  'invalidated': invalidated, 'acknowledged_unknown': choices}
        row.consumed = True
        row.result = result
        row.staged = None
        row.save(update_fields=['consumed', 'result', 'staged'])
    return result
