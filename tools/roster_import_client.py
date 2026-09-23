"""Shared HTTP roster import for the Phase 03 acceptance tools.

One place owns the study-page participant roster flow: preview the CSV batch
through ``POST /studies/<id>/roster-import`` and then commit that preview with
the operator's own password. The helper speaks only the public HTTP API the
researcher UI uses - it never re-implements server-side validation, never
bypasses the preview, and never writes the database directly, so the kits
verify the real entry instead of a side door.

Usage (the kits' ``HttpClient`` already carries the login cookie and CSRF
token)::

    import roster_import_client
    added = roster_import_client.preview_and_commit(
        client.http, study_id, roster_import_client.roster_csv(rows), password)
"""
import csv
import io
import re

ENTRY_SUFFIX = '/roster-import'
PREVIEW_OP = 'import_roster_preview'
COMMIT_OP = 'import_roster_commit'
PREVIEW_ID_RE = re.compile(r'data-preview-id="([0-9a-fA-F-]{36})"')
ADDED_RE = re.compile(r'新增\s+(\d+)\s+个\s+ID|(\d+)\s+new\s+IDs')


class RosterImportError(RuntimeError):
    """The real HTTP roster entry refused the preview or the confirmation."""


def _text(payload):
    return payload.decode('utf-8', 'replace')


def roster_csv(rows):
    """Encode one participant batch as the CSV text the study entry reads.

    ``csv.writer`` owns the encoding, so every field is quoted exactly as
    needed and never trimmed, replaced or re-joined by hand: commas, quotes,
    newlines, Unicode and surrounding whitespace travel byte-exact. Each row is
    one participant - the ID, plus the password in password mode - and the
    caller decides which columns its study mode requires.
    """
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    for row in rows:
        writer.writerow(list(row))
    return stream.getvalue()


def preview_and_commit(http, study_id, text, password, host='admin.localhost'):
    """Preview one CSV roster batch and commit it with the operator password.

    Returns the number of new IDs the commit page reported. Any refusal raises
    :class:`RosterImportError` with the real status and a bounded body excerpt,
    so a kit can never mistake a refusal for a successful import.
    """
    path = '/studies/%s%s' % (study_id, ENTRY_SUFFIX)
    status, payload, _ = http.post_form(path, [('op', PREVIEW_OP), ('roster', text)],
                                        host=host, expect_redirect=False)
    body = _text(payload)
    if status != 200:
        raise RosterImportError('roster preview returned HTTP %s: %s' % (status, body[:300]))
    match = PREVIEW_ID_RE.search(body)
    if match is None:
        raise RosterImportError('roster preview reported errors instead of a confirm form: %s'
                                % body[:400])
    preview_id = match.group(1)
    status, payload, _ = http.post_form(path, [('op', COMMIT_OP), ('preview_id', preview_id),
                                               ('password', password)],
                                        host=host, expect_redirect=False)
    body = _text(payload)
    if status != 200 or 'roster-confirm' in body:
        raise RosterImportError('roster commit was refused with HTTP %s: %s' % (status, body[:400]))
    added = ADDED_RE.search(body)
    if added is None:
        raise RosterImportError('roster commit returned no completion notice: %s' % body[:400])
    return int(added.group(1) or added.group(2))
