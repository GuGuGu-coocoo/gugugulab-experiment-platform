"""Study deletion lifecycle (R00 §D).

One durable job drives everything: the mark transaction stops new business
writes and creates irreversible rejection tombstones, and the explicit
``cleanup_deleted_studies`` maintenance command advances the job in bounded,
re-entrant batches until the database rows and the private files are verified
clean.

Facts this module fixes:

* the mark shares the transaction boundary of admission, event upload,
  completion, recovery issuance/redemption, publication and export creation:
  whichever transaction commits first wins and the other observes it;
* a submitted token/proof is digested and domain-separated-HMACed before a
  constant-time comparison against the tombstone. A match can only refuse with
  ``study_deleted``; it can never restore a read, a write or a re-issued
  credential;
* unknown or forged bindings return the same un-enumerable refusal as a random
  object (``session_unavailable`` / ``recovery_unavailable`` /
  ``admission_unavailable`` / 404), and never a study name, roster or state;
* the cleanup job is explicit and resumable: state, cursor and the file manifest
  are persisted before the corresponding database rows are removed, a file
  failure leaves ``failed`` with the error code, and ``complete`` is only
  written after the database rows and the private files are verified gone;
* each batch removes at most ``batch_size`` real files - an export ZIP and every
  interrupted ``.<uuid>.<16 hex>.tmp`` file or link is one unit - a spool that
  is not completely removed stays in the persisted manifest, and a file-only
  batch that really removed something continues the run even though the
  database cursor no longer moves;
* shared bytes (a package digest used by another study's build, an artifact
  digest used by another release) are removed only after the last reference is
  gone; every file path is re-checked against its fixed private root and a
  symlink is never followed.
"""
import hashlib
import hmac
import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import DatabaseError, IntegrityError, transaction

from .access import authorization_version, ensure_principal, guard, is_instance_owner
from .models import (Audit, Build, DeletedSession, Event, Export, Instance,
                     Invitation, Participant, PermissionPreview, Principal,
                     RecoveryCode, RecoveryPermit, Release, Session, Study,
                     StudyDeletion)
from .protocol import Rejected, require

TOKEN_DOMAIN = 'gep-deleted-session-token/v1'
PROOF_DOMAIN = 'gep-deleted-session-proof/v1'
OPERATION_DOMAIN = 'gep-deleted-session-operation/v1'
DELETION_STATES = ('marked', 'cleaning', 'failed', 'complete')
# Ordered database cleanup steps. The cursor is an index into this tuple; a
# step is only left behind once it is fully processed, so a killed process
# resumes at exactly the persisted position.
CLEANUP_STEPS = ('credentials', 'current_release', 'events', 'exports', 'sessions',
                 'participants', 'releases', 'builds', 'grants', 'audits', 'study')
FILE_ROOTS = {'packages': 'packages', 'artifacts': 'artifacts', 'exports': 'exports'}
# One interrupted spool file of one export: ``.<export uuid>.<16 hex>.tmp``.
EXPORT_TEMP_PATTERN = r'\.{export_id}\.[0-9a-f]{{16}}\.tmp'
DEFAULT_BATCH_SIZE = 200


class DeletionError(Exception):
    """A persisted, resumable cleanup failure (never a silent success)."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


# --- secret tombstones ------------------------------------------------------

def _sha256_hex(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _server_hmac(domain, value):
    return hmac.new(settings.SECRET_KEY.encode(), f'{domain}:{value}'.encode(), hashlib.sha256).hexdigest()


def token_tombstone(raw_token):
    """HMAC(server secret, domain-separated stored token hash)."""
    return _server_hmac(TOKEN_DOMAIN, _sha256_hex(raw_token))


def proof_tombstone(raw_proof):
    """HMAC(server secret, domain-separated stored proof hash)."""
    return _server_hmac(PROOF_DOMAIN, _sha256_hex(raw_proof))


def operation_tombstone(operation, binding):
    """Dedicated-domain HMAC binding one original operation to its request.

    The canonical string carries the operation and the instance/study/release
    identity; the build UUID is verified separately from the stored column
    whenever the interface can still resolve it. A fresh operation, a different
    release or another instance therefore never produces the stored value.
    """
    canonical = ':'.join(str(binding.get(key) or '') for key in
                         ('instance_id', 'study_id', 'release_id'))
    return _server_hmac(OPERATION_DOMAIN, f'{operation}:{canonical}')


def _constant_time_match(stored, candidate):
    return isinstance(stored, str) and isinstance(candidate, str) and hmac.compare_digest(stored, candidate)


def matching_tombstone(*, session_uuid=None, study_uuid=None, token=None, proof=None):
    """The tombstone matched by the submitted secret, or ``None``.

    A session-scoped lookup never falls back to another session; a study-scoped
    lookup matches only a full proof and only inside that study. The comparison
    itself is constant-time.
    """
    if session_uuid is not None:
        candidate = DeletedSession.objects.filter(session_uuid=session_uuid).first()
        if candidate is None:
            return None
        if token is not None and _constant_time_match(candidate.token_hmac, token_tombstone(token)):
            return candidate
        if proof is not None and _constant_time_match(candidate.proof_hmac, proof_tombstone(proof)):
            return candidate
        return None
    if study_uuid is not None and proof is not None:
        wanted = proof_tombstone(proof)
        for candidate in DeletedSession.objects.filter(study_uuid=study_uuid, proof_hmac=wanted)[:4]:
            if _constant_time_match(candidate.proof_hmac, wanted):
                return candidate
    return None


def _binding_matches_tombstone(tombstone, binding):
    """Whether every provided binding key matches the stored tombstone.

    A missing key is not compared (a retry after the release row is gone still
    carries the operation identity); a provided key must match exactly.
    """
    if binding is None:
        return True
    pairs = (('instance_id', tombstone.instance_uuid), ('study_id', tombstone.study_uuid),
             ('release_id', tombstone.release_uuid), ('build_id', tombstone.build_uuid))
    for key, stored in pairs:
        provided = binding.get(key)
        if provided is not None and str(provided) != str(stored):
            return False
    return True


def study_is_deleting(study_uuid):
    """True once the deletion mark committed for this study UUID (forever)."""
    return StudyDeletion.objects.filter(study_uuid=study_uuid).exists()


def deletion_for(study_uuid):
    return StudyDeletion.objects.filter(study_uuid=study_uuid).first()


# --- un-enumerable refusals -------------------------------------------------

def session_refusal(session_id, token):
    """Refuse a session-scoped request after the session is gone or deleting.

    A tombstone match is the documented permanent ``study_deleted``; anything
    else (unknown session, forged token, session created without a tombstone) is
    the same ``session_unavailable`` and carries no study or roster fact.
    """
    if matching_tombstone(session_uuid=session_id, token=token) is not None:
        raise Rejected('study_deleted', 403)
    raise Rejected('session_unavailable', 403)


def recovery_refusal(*, session_id=None, study_uuid=None, proof, binding=None):
    """Refuse recovery of a deleted study: exact binding -> ``study_deleted``."""
    tombstone = matching_tombstone(session_uuid=session_id, study_uuid=study_uuid, proof=proof)
    if tombstone is not None and _binding_matches_tombstone(tombstone, binding):
        raise Rejected('study_deleted', 403)
    raise Rejected('recovery_unavailable', 403)


def admission_refusal(*, study_uuid, proof, operation=None, binding=None):
    """Refuse admission into a deleted study.

    Only the original operation together with the exact request binding and the
    original proof receives the permanent ``study_deleted``. A fresh operation,
    a mismatched release/build or a forged proof is the same un-enumerable
    ``admission_unavailable`` as a random study, so nothing can be enumerated.
    """
    if operation is not None and isinstance(binding, dict):
        wanted = operation_tombstone(operation, binding)
        wanted_proof = proof_tombstone(proof)
        candidates = DeletedSession.objects.filter(study_uuid=study_uuid, operation_hmac=wanted)[:4]
        for tombstone in candidates:
            if not _constant_time_match(tombstone.operation_hmac, wanted):
                continue
            if not _constant_time_match(tombstone.proof_hmac, wanted_proof):
                continue
            if not _binding_matches_tombstone(tombstone, binding):
                continue
            raise Rejected('study_deleted', 403)
    raise Rejected('admission_unavailable', 403)


def export_unavailable():
    """Deleted/unknown export object: one 404 for both, no enumeration."""
    raise Rejected('export_unavailable', 404)


def study_unavailable():
    raise Rejected('study_unavailable', 404)


def active_study(study):
    """True only for a live study row that has no deletion job."""
    return study is not None and getattr(study, 'lifecycle', 'active') == 'active' \
        and not study_is_deleting(study.pk)


@contextmanager
def live_export_lock(export_id):
    """Cross-process generation boundary for one export spool file.

    The caller publishes its file inside this block and nowhere else. The
    project runs SQLite with ``transaction_mode='IMMEDIATE'``, so entering the
    block takes the real database write lock before the liveness read: a cleanup
    transaction can either commit before the lock is taken (this read then sees
    the row gone and the caller refuses without publishing) or after this block
    commits (the cleanup then removes the just-published file by ownership).
    There is no interleaving in which a new file is published after deletion,
    and a process killed inside the block leaves the row untouched plus at most
    one file that the persisted manifest removes on the next cleanup run.
    """
    with transaction.atomic():
        row = Export.objects.filter(pk=export_id).only('study_id').first()
        if row is None:
            export_unavailable()
        study = Study.objects.filter(pk=row.study_id).only('lifecycle').first()
        if study is None or not active_study(study):
            export_unavailable()
        yield


# --- mark -------------------------------------------------------------------

def dependency_counts(study):
    """Real counts of everything the deletion will remove (UI + audit)."""
    sessions = Session.objects.filter(release__study=study)
    return {
        'sessions': sessions.count(),
        'participants': Participant.objects.filter(study=study).count(),
        'events': Event.objects.filter(session__in=sessions).count(),
        'exports': Export.objects.filter(study=study).count(),
        'releases': Release.objects.filter(study=study).count(),
        'builds': Build.objects.filter(study=study).count(),
        'grants': study.grant_set.count(),
        'invitations': Invitation.objects.filter(study=study).count(),
        'recovery_credentials': (RecoveryCode.objects.filter(study=study).count()
                                 + RecoveryPermit.objects.filter(session__release__study=study).count()),
    }


def has_study_data(counts):
    """Whether the confirmation dialog must show real counts and the export link."""
    return any(counts.get(key, 0) for key in ('sessions', 'participants', 'events', 'exports',
                                              'releases', 'builds'))


def _create_tombstones(study):
    """One rejection tombstone per session, in the mark transaction."""
    instance_uuid = Instance.objects.get(pk=1).instance_id
    rows = []
    sessions = (Session.objects.filter(release__study=study)
                .select_related('release').iterator(chunk_size=500))
    for session in sessions:
        binding = {'instance_id': str(instance_uuid), 'study_id': str(study.pk),
                   'release_id': str(session.release_id), 'build_id': str(session.release.build_id)}
        rows.append(DeletedSession(
            session_uuid=session.pk, study_uuid=study.pk, release_uuid=session.release_id,
            build_uuid=session.release.build_id, instance_uuid=instance_uuid,
            token_hmac=_server_hmac(TOKEN_DOMAIN, session.token_hash),
            proof_hmac=_server_hmac(PROOF_DOMAIN, session.proof_hash),
            operation_hmac=operation_tombstone(session.operation, binding)))
    if rows:
        DeletedSession.objects.bulk_create(rows, batch_size=500, ignore_conflicts=True)


def mark_study_deletion(actor, study, password):
    """Stop a study and create the durable deletion job, atomically.

    Requires the v2 ``study.delete`` authority and the actor's own password,
    re-read from locked rows. The mark, the tombstones, the lifecycle change and
    the audit commit together: a failure of any of them (including the audit)
    leaves the study exactly as it was. A repeated mark is refused.
    """
    with transaction.atomic():
        instance = Instance.objects.select_for_update().get(pk=1)
        require(authorization_version(instance) == 2, 'authorization_upgrade_required', 409)
        locked_actor = get_user_model().objects.select_for_update().filter(pk=getattr(actor, 'pk', None)).first()
        require(locked_actor is not None and locked_actor.is_active, 'auth_required', 403)
        require(bool(password) and check_password(password, locked_actor.password), 'reauth_failed', 403)
        locked = Study.objects.select_for_update().filter(pk=study.pk).first()
        require(locked is not None, 'study_missing', 404)
        guard(locked_actor, locked, 'study.delete')
        require(locked.lifecycle == 'active', 'already_deleting', 409)
        require(not study_is_deleting(locked.pk), 'already_deleting', 409)
        counts = dependency_counts(locked)
        deletion = StudyDeletion.objects.create(
            study_uuid=locked.pk, principal=ensure_principal(locked_actor),
            state='marked', counts=counts, file_manifest=[])
        _create_tombstones(locked)
        locked.lifecycle = 'deleting'
        locked.save(update_fields=['lifecycle'])
        Audit.objects.create(study=locked, actor=locked_actor,
                             actor_principal=ensure_principal(locked_actor),
                             action='study.deletion_marked', target=str(locked.pk),
                             after={'counts': counts})
    return {'deletion_id': str(deletion.id), 'study_uuid': str(locked.pk),
            'state': deletion.state, 'counts': counts}


def operator_allowed(user, deletion):
    """Only the deletion requester or the instance Owner may watch the job."""
    if deletion is None:
        return False
    if is_instance_owner(user):
        return True
    if deletion.principal_id is None or not getattr(user, 'pk', None):
        return False
    return Principal.objects.filter(pk=deletion.principal_id, user_id=user.pk).exists()


def status_payload(deletion):
    """Minimal job progress: state/counts/time/error only, never study content."""
    return {'study_uuid': str(deletion.study_uuid), 'state': deletion.state,
            'requested_at': deletion.requested_at, 'counts': deletion.counts,
            'error_code': deletion.error_code,
            'files_remaining': len(deletion.file_manifest or [])}


# --- cleanup ----------------------------------------------------------------

def _bounded_delete(queryset, budget):
    """Delete at most ``budget`` rows; return (deleted, step_finished)."""
    if budget <= 0:
        return 0, False
    ids = list(queryset.values_list('pk', flat=True)[:budget])
    if not ids:
        return 0, True
    queryset.model.objects.filter(pk__in=ids).delete()
    return len(ids), len(ids) < budget


def _manifest_add(manifest, entry):
    if entry not in manifest:
        manifest.append(entry)


def _staged_mentions(staged, study_text):
    """Whether one staged preview JSON references the study UUID."""
    try:
        return study_text in json.dumps(staged, ensure_ascii=False)
    except (TypeError, ValueError):
        return True


def _scan_previews(deletion, budget):
    """Bounded, resumable cross-scope staged-preview scan.

    Pages are ordered by primary key and the last scanned key is persisted on
    the job, so a killed process resumes exactly there. A staged preview scoped
    to the study, or whose staged JSON mentions it, is invalidated; every other
    preview (another study, another account) stays untouched. Returns
    ``(scanned, exhausted)``.
    """
    if budget <= 0:
        return 0, False
    study_text = str(deletion.study_uuid)
    queryset = PermissionPreview.objects.filter(staged__isnull=False)
    if deletion.preview_cursor is not None:
        queryset = queryset.filter(pk__gt=deletion.preview_cursor)
    rows = list(queryset.order_by('pk')[:budget])
    if not rows:
        return 0, True
    for preview in rows:
        if preview.scope == study_text or _staged_mentions(preview.staged, study_text):
            PermissionPreview.objects.filter(pk=preview.pk).update(staged=None)
        deletion.preview_cursor = preview.pk
    return len(rows), len(rows) < budget


def _step_credentials(deletion, manifest, budget):
    """Recovery credentials, invitations and preview staged values first."""
    study_uuid = deletion.study_uuid
    deleted = 0
    for queryset in (
            RecoveryCode.objects.filter(study_id=study_uuid),
            RecoveryPermit.objects.filter(session__release__study_id=study_uuid),
            Invitation.objects.filter(study_id=study_uuid)):
        count, finished = _bounded_delete(queryset, budget - deleted)
        deleted += count
        if not finished:
            return deleted, False
    # A study-scoped preview carries the roster passwords in ``staged``; clear
    # it before any row is removed. Every other preview is scanned in bounded,
    # persisted pages, so a cross-scope preview beyond the first batch is still
    # found while unrelated previews are preserved.
    scanned, exhausted = _scan_previews(deletion, budget - deleted)
    return deleted + scanned, exhausted


def _step_current_release(deletion, manifest, budget):
    Study.objects.filter(pk=deletion.study_uuid, current_release__isnull=False).update(current_release=None)
    return 0, True


def _step_events(deletion, manifest, budget):
    return _bounded_delete(Event.objects.filter(session__release__study_id=deletion.study_uuid), budget)


def _step_exports(deletion, manifest, budget):
    queryset = Export.objects.filter(study_id=deletion.study_uuid)
    for export_id in queryset.values_list('pk', flat=True)[:budget]:
        # Ownership of the whole spool (the ZIP and any interrupted
        # ``.<id>.<nonce>.tmp``) is persisted in the same transaction that
        # removes the Export row, so a generation killed in between is still
        # covered by the file step.
        _manifest_add(manifest, {'root': 'exports', 'export': str(export_id)})
    return _bounded_delete(queryset, budget)


def _step_sessions(deletion, manifest, budget):
    return _bounded_delete(Session.objects.filter(release__study_id=deletion.study_uuid), budget)


def _step_participants(deletion, manifest, budget):
    return _bounded_delete(Participant.objects.filter(study_id=deletion.study_uuid), budget)


def _step_releases(deletion, manifest, budget):
    queryset = Release.objects.filter(study_id=deletion.study_uuid)
    for name in queryset.exclude(artifact_path='').values_list('artifact_path', flat=True)[:budget]:
        if name:
            _manifest_add(manifest, {'root': 'artifacts', 'name': name})
    return _bounded_delete(queryset, budget)


def _step_builds(deletion, manifest, budget):
    queryset = Build.objects.filter(study_id=deletion.study_uuid)
    for name in queryset.exclude(package_path='').values_list('package_path', flat=True)[:budget]:
        if name:
            _manifest_add(manifest, {'root': 'packages', 'name': name})
    return _bounded_delete(queryset, budget)


def _step_grants(deletion, manifest, budget):
    # Grants are reached through the study's own related manager; this module
    # never reads a grant row as an authorization fact.
    study = Study.objects.filter(pk=deletion.study_uuid).first()
    if study is None:
        return 0, True
    return _bounded_delete(study.grant_set.all(), budget)


def _step_audits(deletion, manifest, budget):
    """Detach the study link and strip raw before/after in bounded pages.

    Every processed row keeps the minimal ``study_uuid`` association, so each
    retained action stays attributable to its own study after the foreign key is
    gone; the deletion-mark row keeps its minimal counts (the only raw value that
    is the audit fact itself); every other before/after of this study is cleared.
    Only a bounded page of rows is updated per batch, so a large audit history
    stays resumable exactly like the other steps. A row already detached before
    this field existed keeps NULL (unknown, never guessed).
    """
    queryset = Audit.objects.filter(study_id=deletion.study_uuid).order_by('pk')
    ids = list(queryset.values_list('pk', flat=True)[:budget])
    if not ids:
        return 0, True
    Audit.objects.filter(pk__in=ids).exclude(action='study.deletion_marked').update(
        study=None, study_uuid=deletion.study_uuid, before=None, after=None)
    Audit.objects.filter(pk__in=ids, action='study.deletion_marked').update(
        study=None, study_uuid=deletion.study_uuid, before=None)
    return len(ids), len(ids) < budget


def _step_study(deletion, manifest, budget):
    Study.objects.filter(pk=deletion.study_uuid).delete()
    return 1, True


_STEP_FUNCTIONS = {
    'credentials': _step_credentials, 'current_release': _step_current_release,
    'events': _step_events, 'exports': _step_exports, 'sessions': _step_sessions,
    'participants': _step_participants, 'releases': _step_releases, 'builds': _step_builds,
    'grants': _step_grants, 'audits': _step_audits, 'study': _step_study,
}


def _db_step(deletion, batch_size):
    """One bounded database step inside the caller's transaction."""
    manifest = list(deletion.file_manifest or [])
    budget = batch_size
    index = deletion.cursor
    progressed = False
    while index < len(CLEANUP_STEPS) and budget > 0:
        function = _STEP_FUNCTIONS[CLEANUP_STEPS[index]]
        work, finished = function(deletion, manifest, budget)
        if work:
            progressed = True
            budget -= work
        if not finished:
            break
        index += 1
    deletion.cursor = index
    deletion.file_manifest = manifest
    return progressed


def _root_path(root):
    return Path(settings.DATA_DIR) / root


def _private_root(root_name):
    """One fixed private root; a symlinked data/root component is refused."""
    require(root_name in FILE_ROOTS, 'invalid_manifest')
    base = Path(settings.DATA_DIR)
    root = _root_path(root_name)
    require(not base.is_symlink() and not root.is_symlink(), 'unsafe_file_root')
    require(root.parent == base, 'unsafe_file_root')
    return root


def _unlink_private(path):
    """Remove one private file; a symlink itself is unlinked, never followed."""
    if path.is_symlink():
        path.unlink()
        return
    if not path.exists():
        return
    require(path.is_file(), 'unsafe_file')
    path.unlink()


def _delete_private_file(entry):
    """Delete one manifest file inside its fixed private root, never a symlink target."""
    name = entry.get('name')
    require(isinstance(name, str) and name and name == os.path.basename(name), 'invalid_manifest')
    root = _private_root(entry.get('root'))
    path = root / name
    require(path.parent == root, 'invalid_manifest')
    _unlink_private(path)


def _delete_export_spool(entry, budget):
    """Remove one export's own spool within ``budget`` real removals.

    The manifest entry names only the export UUID, so exactly the
    ``<uuid>.zip`` and the ``.<uuid>.<16 hex>.tmp`` interrupted-write files are
    removed. ``budget`` counts actual removals (the ZIP and every temp file or
    link is one unit); the directory is walked with ``os.scandir`` so it is
    never materialized in memory, and only the matching names are inspected.
    Unknown names, another export's files and symlink targets are never touched.
    Returns ``(removed, complete)``: ``complete`` is False when the budget was
    spent before the whole spool could be verified gone, so the caller keeps the
    entry in the persisted manifest and the next run resumes.
    """
    export_id = entry.get('export')
    require(isinstance(export_id, str) and export_id, 'invalid_manifest')
    try:
        parsed = uuid.UUID(export_id)
    except (ValueError, AttributeError, TypeError):
        raise DeletionError('invalid_manifest')
    require(str(parsed) == export_id, 'invalid_manifest')
    root = _private_root('exports')
    if not root.exists():
        return 0, True
    require(root.is_dir(), 'unsafe_file_root')
    zip_name = f'{export_id}.zip'
    zip_path = root / zip_name
    removed = 0
    if zip_path.is_symlink() or zip_path.exists():
        _unlink_private(zip_path)
        removed += 1
    if removed >= budget:
        return removed, False
    pattern = re.compile(EXPORT_TEMP_PATTERN.format(export_id=re.escape(export_id)))
    with os.scandir(root) as entries:
        for path in entries:
            if path.name == zip_name or not pattern.fullmatch(path.name):
                continue
            _unlink_private(Path(path.path))
            removed += 1
            if removed >= budget:
                return removed, False
    return removed, True


def _file_referenced(entry):
    """Whether another live row still owns these exact bytes."""
    if 'export' in entry:
        # The spool belongs to one export row; while that row is still live the
        # bytes are its cache and are never removed.
        return Export.objects.filter(pk=entry.get('export')).exists()
    root = entry.get('root')
    if root == 'packages':
        return Build.objects.filter(package_path=entry.get('name')).exists()
    if root == 'artifacts':
        return Release.objects.filter(artifact_path=entry.get('name')).exists()
    if root == 'exports':
        name = entry.get('name') or ''
        return Export.objects.filter(pk=name[:-len('.zip')]).exists()
    return True


def _file_step(deletion, batch_size):
    """Delete now-unreferenced manifest files within a real per-file budget.

    ``batch_size`` counts actual removals: one export ZIP, one interrupted
    ``.<uuid>.<16 hex>.tmp`` file or link, and one exact-name manifest file each
    cost one unit. Shared bytes stay with their owner and leave this job's
    manifest. An export spool that could not be completely removed stays in the
    persisted manifest, so a killed process or a later run resumes instead of
    claiming success. Returns ``(removed, remaining)``.
    """
    remaining = []
    budget = batch_size
    removed = 0
    for entry in list(deletion.file_manifest or []):
        if not isinstance(entry, dict):
            raise DeletionError('invalid_manifest')
        if budget <= 0:
            remaining.append(entry)
            continue
        if _file_referenced(entry):
            # Another study/release/export still references these bytes: the
            # entry leaves this job's manifest and the file stays.
            continue
        if 'export' in entry:
            gone, complete = _delete_export_spool(entry, budget)
            removed += gone
            budget -= gone
            if complete:
                continue
            remaining.append(entry)
        else:
            _delete_private_file(entry)
            removed += 1
            budget -= 1
    deletion.file_manifest = remaining
    return removed, len(remaining)


def _staged_previews_clear(study_uuid):
    """No staged preview may still reference the deleted study.

    The credentials step already scanned and cleared every staged preview in
    bounded pages; this independent completion check re-reads the small preview
    table so ``complete`` is never claimed while one remains.
    """
    study_text = str(study_uuid)
    for preview in (PermissionPreview.objects.filter(staged__isnull=False)
                    .only('scope', 'staged').iterator(chunk_size=500)):
        if preview.scope == study_text or _staged_mentions(preview.staged, study_text):
            return False
    return True


def _db_cleared(study_uuid):
    # A live study row means every PROTECTed dependency is still reachable; once
    # the row is gone its CASCADE grants are gone with it. The explicit checks
    # below re-verify the rest instead of trusting the delete order.
    if Study.objects.filter(pk=study_uuid).exists():
        return False
    return not (
        Session.objects.filter(release__study_id=study_uuid).exists()
        or Participant.objects.filter(study_id=study_uuid).exists()
        or Event.objects.filter(session__release__study_id=study_uuid).exists()
        or Export.objects.filter(study_id=study_uuid).exists()
        or Release.objects.filter(study_id=study_uuid).exists()
        or Build.objects.filter(study_id=study_uuid).exists()
        or Audit.objects.filter(study_id=study_uuid).exists()
        or not _staged_previews_clear(study_uuid))


def _persist_failed(deletion_id, code):
    with transaction.atomic():
        row = StudyDeletion.objects.select_for_update().filter(pk=deletion_id).first()
        if row is not None and row.state != 'complete':
            row.state = 'failed'
            row.error_code = code
            row.save(update_fields=['state', 'error_code'])


def _cleanup_batch(deletion_id, batch_size):
    """One re-entrant step: database transaction, then private files, then check.

    ``progressed`` is true when either the database step or the file step really
    advanced, so a file-only job (the database cursor already at the end) keeps
    running to completion instead of stopping after one batch.
    """
    try:
        with transaction.atomic():
            row = StudyDeletion.objects.select_for_update().filter(pk=deletion_id).first()
            if row is None or row.state == 'complete':
                return 'complete', False
            progressed = _db_step(row, batch_size)
            if progressed and row.state in ('marked', 'failed'):
                # A resumed job that really advances leaves the failed state.
                row.state = 'cleaning'
            row.error_code = '' if progressed else row.error_code
            row.save(update_fields=['state', 'cursor', 'preview_cursor', 'file_manifest', 'error_code'])
            has_files = bool(row.file_manifest)
    except (DatabaseError, IntegrityError, Rejected, ValueError, KeyError, TypeError) as error:
        _persist_failed(deletion_id, getattr(error, 'code', type(error).__name__))
        return 'failed', False

    if has_files:
        try:
            with transaction.atomic():
                row = StudyDeletion.objects.select_for_update().get(pk=deletion_id)
                removed, _remaining = _file_step(row, batch_size)
                if removed:
                    progressed = True
                    if row.state in ('marked', 'failed'):
                        row.state = 'cleaning'
                    row.error_code = ''
                row.save(update_fields=['state', 'error_code', 'file_manifest'])
        except (DeletionError, OSError, DatabaseError, Rejected) as error:
            _persist_failed(deletion_id, getattr(error, 'code', type(error).__name__))
            return 'failed', False

    with transaction.atomic():
        row = StudyDeletion.objects.select_for_update().filter(pk=deletion_id).first()
        if row is None:
            return 'complete', False
        if row.state == 'complete':
            return 'complete', False
        if _db_cleared(row.study_uuid) and not row.file_manifest:
            row.state = 'complete'
            row.error_code = ''
            row.file_manifest = []
            row.save(update_fields=['state', 'error_code', 'file_manifest'])
            return 'complete', progressed
        return row.state, progressed


def run_cleanup(deletion_id, *, batch_size=DEFAULT_BATCH_SIZE, max_batches=None):
    """Advance one job until it is complete/failed or the batch budget is spent."""
    batches = 0
    while True:
        state, progressed = _cleanup_batch(deletion_id, batch_size)
        batches += 1
        if state in ('complete', 'failed') or not progressed:
            break
        if max_batches is not None and batches >= max_batches:
            break
    row = StudyDeletion.objects.filter(pk=deletion_id).first()
    if row is None:
        return {'deletion_id': str(deletion_id), 'state': 'complete', 'cursor': 0, 'batches': batches,
                'error_code': '', 'files_remaining': 0}
    return {'deletion_id': str(row.pk), 'study_uuid': str(row.study_uuid), 'state': row.state,
            'cursor': row.cursor, 'batches': batches, 'error_code': row.error_code,
            'files_remaining': len(row.file_manifest or [])}


def cleanup_pending(*, batch_size=DEFAULT_BATCH_SIZE, max_batches=None, study_uuid=None):
    """Every unfinished job, oldest first; each result states the real outcome."""
    queryset = StudyDeletion.objects.exclude(state='complete').order_by('requested_at')
    if study_uuid is not None:
        queryset = queryset.filter(study_uuid=study_uuid)
    return [run_cleanup(row.pk, batch_size=batch_size, max_batches=max_batches)
            for row in queryset.iterator()]
