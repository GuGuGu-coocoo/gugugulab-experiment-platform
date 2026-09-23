"""Study publication policy and current-release selection (03C).

Public listing and the current release are explicit researcher decisions:

* a study is only listed when it is explicitly ``public``, recruiting is open
  and the current release is approved and resource-located; mode and recruitment
  never publish a study on their own;
* the current release is only set to a same-study, approved release whose build
  has a published package; the migration leaves it NULL and nothing guesses the
  first or latest release;
* every policy or current-release change runs in one transaction that locks the
  study row, re-checks the actor's authority on the locked row, compares the
  submitted publication revision, bumps it and writes a structured before/after
  audit without secrets;
* selecting a current release changes the target of *new* stable-entry sessions
  only. Existing sessions, old run URLs, configs, exports and package bytes keep
  their original binding and are never rewritten.
"""
from django.contrib.auth import get_user_model
from django.db import transaction

from .access import allowed, guard
from .models import Audit, Release, Study
from .protocol import require
from . import deletion

POLICY_FIELDS = ('public_summary', 'public_duration', 'public_device_requirements')
POLICY_LIMITS = {'public_summary': 280, 'public_duration': 80, 'public_device_requirements': 160}
POLICY_CHANGED = 'publication.policy_changed'
RELEASE_CHANGED = 'publication.current_release_changed'
SELECT_ACTIONS = ('study.configure', 'recruitment.manage')
NATIVE_PLATFORMS = ('macos_arm64', 'windows_x64')


def _revision_matches(raw, current):
    try:
        return int(raw) == current
    except (TypeError, ValueError):
        return False


def policy_state(study):
    return {'public': study.public, 'public_summary': study.public_summary,
            'public_duration': study.public_duration,
            'public_device_requirements': study.public_device_requirements,
            'show_closed_summary': study.show_closed_summary}


def public_snapshot(study):
    """Fields the public portal may render; never roster, history or policy ids."""
    return {'title': study.title, 'summary': study.public_summary, 'duration': study.public_duration,
            'device_requirements': study.public_device_requirements}


def release_platform(release):
    """The registered platform of one release; '' when the build has none."""
    descriptor = release.build.descriptor if isinstance(release.build.descriptor, dict) else {}
    platform = descriptor.get('platform')
    return platform if isinstance(platform, str) else ''


def release_kind(release):
    """What kind of current release this is: ``web``, ``native`` or ``None``.

    A Web release keeps the 03C contract: approved with a stored Web package.
    A native release (macOS or Windows) is only usable through its frozen
    complete artifact; a descriptor-only native registration stays an external
    distribution record and never pretends to be a runnable Web package.
    """
    if release is None or not release.approved:
        return None
    if release_platform(release) in NATIVE_PLATFORMS:
        return 'native' if (bool(release.artifact_path) and bool(release.artifact_digest)) else None
    return 'web' if release.build.package_path else None


def release_available(release):
    """Whether the current release can actually be offered to participants now.

    The Web contract is unchanged (approved with a stored Web package). A native
    release additionally requires its frozen complete artifact to be present and
    byte-intact, so a missing or tampered program is never advertised; this is
    presentation fail-closed only and never rewrites an old binding.
    """
    kind = release_kind(release)
    if kind == 'web':
        return True
    if kind == 'native':
        from . import artifacts
        return artifacts.stored_artifact_intact(release)
    return False


def entry_state(study):
    """Platform-aware presentation state for one study's stable entry.

    ``kind`` is ``web``/``native`` ('' when there is nothing to present) and
    ``available`` says whether the platform can really offer it right now. A
    native release with a missing or tampered artifact is reported as
    ``native``/unavailable instead of silently falling back to a Web start.
    """
    release = study.current_release
    if release is None or study.recruitment != 'open':
        return {'kind': '', 'available': False}
    kind = release_kind(release)
    if kind is None:
        kind = 'native' if release_platform(release) in NATIVE_PLATFORMS else ''
        return {'kind': kind, 'available': False}
    return {'kind': kind, 'available': release_available(release)}


def bound_release(study, release_id):
    """The requested current release, validating same-study approval and resources.

    An empty ``release_id`` clears the current release so the portal stops
    offering a start without touching recruitment or existing sessions. A release
    is selectable when it has a real presentation contract: a stored Web package
    or a frozen complete native artifact. Selection is a research-level decision
    about new sessions only; whether the artifact is currently byte-intact is a
    presentation-time check that fails closed without rewriting the binding.
    """
    if not release_id:
        return None
    release = Release.objects.select_related('build').filter(pk=release_id, study=study).first()
    require(release is not None, 'release_not_found', 404)
    require(release.approved, 'release_unapproved', 409)
    require(release_kind(release) is not None, 'release_unavailable', 409)
    return release


def _current_actor(actor):
    """Re-read the acting account inside the write transaction.

    A cached Python object's ``is_active`` (or a deleted row) must never be the
    final authority for a policy change: the account can be deactivated between
    the request that resolved it and the transaction that writes.
    """
    if not getattr(actor, 'pk', None):
        return actor
    return get_user_model().objects.filter(pk=actor.pk).first()


def update_policy(actor, study, revision, values):
    """Update explicit publication fields under the study publication revision."""
    cleaned = {}
    for field in POLICY_FIELDS:
        value = str(values.get(field, '')).strip()
        require(len(value) <= POLICY_LIMITS[field], 'policy_field')
        cleaned[field] = value
    public = bool(values.get('public'))
    show_closed = bool(values.get('show_closed_summary'))
    with transaction.atomic():
        actor = _current_actor(actor)
        guard(actor, study, 'study.configure')
        locked = Study.objects.select_for_update().get(pk=study.pk)
        # Final lifecycle check inside the locking transaction: a request that
        # read the study before the deletion mark committed is still refused.
        require(deletion.active_study(locked), 'study_deleted', 403)
        guard(actor, locked, 'study.configure')
        require(_revision_matches(revision, locked.revision), 'revision_conflict', 409)
        before = policy_state(locked)
        target = {'public': public, 'show_closed_summary': show_closed, **cleaned}
        require(target != before, 'no_change', 409)
        locked.public = public
        locked.show_closed_summary = show_closed
        for field, value in cleaned.items():
            setattr(locked, field, value)
        locked.revision += 1
        locked.save(update_fields=['public', 'show_closed_summary', *POLICY_FIELDS, 'revision'])
        Audit.objects.create(study=locked, actor=actor, action=POLICY_CHANGED, target=str(locked.id),
                             before=before, after=policy_state(locked))
    return {'revision': locked.revision, 'before': before, 'after': policy_state(locked)}


def select_current_release(actor, study, revision, release_id):
    """Bind (or clear) the current release for new stable-entry sessions.

    Requires the study-configuration or recruitment authority, the current
    publication revision, and an approved same-study release with a published
    package. The previous release is not touched in any way.
    """
    with transaction.atomic():
        actor = _current_actor(actor)
        locked = Study.objects.select_for_update().get(pk=study.pk)
        # Final lifecycle check inside the locking transaction, before any
        # current-release write.
        require(deletion.active_study(locked), 'study_deleted', 403)
        require(any(allowed(actor, locked, action) for action in SELECT_ACTIONS), 'forbidden', 403)
        require(_revision_matches(revision, locked.revision), 'revision_conflict', 409)
        target = bound_release(locked, release_id)
        before = {'current_release': str(locked.current_release_id) if locked.current_release_id else None}
        after = {'current_release': str(target.id) if target is not None else None}
        require(before != after, 'no_change', 409)
        locked.current_release = target
        locked.revision += 1
        locked.save(update_fields=['current_release', 'revision'])
        Audit.objects.create(study=locked, actor=actor, action=RELEASE_CHANGED, target=str(locked.id),
                             before=before, after=after)
    return {'revision': locked.revision, 'before': before, 'after': after}
