"""T26/P0302 Excel evidence: bounded XLSX templates and imports.

Real HTTP uploads of real openpyxl workbooks against the test database; every
assertion is an independently specified expectation (limits, textual 001,
formula/macro/link refusal, per-row errors, whole-set rollback, invitation
default, password staging without leakage, digest/expiry/idempotency).
"""
import io
import re
import time
import zipfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from openpyxl import Workbook, load_workbook

from core import accounts, excel, permissions
from core.models import (AccountInvitation, AccountProfile, Audit, Grant, Instance, Participant,
                         PermissionPreview, Study)

OWNER_PASSWORD = 'synthetic-test-password'
NEW_PASSWORD = 'synthetic-new-password-2026'
THIRD_PASSWORD = 'synthetic-third-password-2026'
PREVIEW_RE = re.compile(r'(?:name="preview_id" value="|data-preview-id=")([0-9a-fA-F-]{36})')


def csrf_token(client, path='/users'):
    page = client.get(path)
    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode())
    assert match, f'no CSRF token rendered at {path}'
    return match.group(1)


def login(client, username, password):
    return client.post('/login', {'username': username, 'password': password})


def owner_client():
    client = Client()
    assert login(client, 'synthetic_owner', OWNER_PASSWORD).status_code == 302
    return client


def preview_id(response):
    match = PREVIEW_RE.search(response.content.decode())
    assert match, response.content[:400]
    return match.group(1)


def workbook_bytes(rows, headers=excel.USERS_HEADERS, title='data'):
    book = Workbook()
    sheet = book.active
    sheet.title = title
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    return book_bytes(book)


def book_bytes(book):
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def upload(client, op, payload, filename='data.xlsx', extra=None):
    upload_file = SimpleUploadedFile(filename, payload, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    return client.post('/users', {'op': op, 'file': upload_file, **(extra or {})})


def commit(client, op, preview, password):
    return client.post('/users', {'op': op, 'preview_id': preview, 'password': password})


def import_rows(*rows):
    return workbook_bytes(rows)


def test_templates_are_bounded_and_contain_no_secrets(setup):
    existing = get_user_model().objects.create_user('synthetic_template_holder', password=THIRD_PASSWORD)
    AccountProfile.objects.create(user=existing, role='admin', must_change_password=False, auth_version=1, revision=0)
    owner = owner_client()

    response = owner.get('/users/templates/users')
    assert response.status_code == 200
    assert response['Content-Disposition'] == 'attachment; filename="gep_users_template.xlsx"'
    assert 'attachment' in response['Content-Disposition']
    assert response['Cache-Control'] == 'no-store'
    book = load_workbook(io.BytesIO(response.content))
    headers = set(book.worksheets[0].iter_rows(min_row=1, max_row=1, values_only=True).__next__())
    assert set(excel.USERS_HEADERS) <= headers
    payload = response.content
    assert THIRD_PASSWORD.encode() not in payload
    assert existing.password.encode() not in payload
    assert accounts.digest(THIRD_PASSWORD).encode() not in payload

    # Roster templates: the password column exists only in password mode.
    Grant.objects.create(user=setup['owner'], study=setup['study'], action='study.view', delegable=True)
    Grant.objects.create(user=setup['owner'], study=setup['study'], action='study.configure', delegable=True)
    response = owner.get(f"/users/templates/roster?study={setup['study'].id}")
    assert response.status_code == 200
    headers = set(load_workbook(io.BytesIO(response.content)).worksheets[0].iter_rows(min_row=1, max_row=1, values_only=True).__next__())
    assert headers == {'id'}
    password_study = Study.objects.create(title='Synthetic password study', mode='password')
    Grant.objects.create(user=setup['owner'], study=password_study, action='study.view', delegable=True)
    Grant.objects.create(user=setup['owner'], study=password_study, action='study.configure', delegable=True)
    response = owner.get(f"/users/templates/roster?study={password_study.id}")
    headers = set(load_workbook(io.BytesIO(response.content)).worksheets[0].iter_rows(min_row=1, max_row=1, values_only=True).__next__())
    assert headers == {'id', 'password'}

    ordinary = Client()
    assert login(ordinary, 'synthetic_template_holder', THIRD_PASSWORD).status_code == 302
    # Admin without study.configure cannot fetch the roster template.
    assert ordinary.get(f"/users/templates/roster?study={setup['study'].id}").status_code == 403
    assert ordinary.get('/users/templates/users').status_code == 200
    anonymous = Client()
    assert anonymous.get('/users/templates/users').status_code == 302


def test_users_import_invites_by_default_and_never_resets_passwords(setup):
    owner = owner_client()
    existing = get_user_model().objects.create_user('synthetic_import_existing', password=THIRD_PASSWORD)
    profile = AccountProfile.objects.create(user=existing, role='user', must_change_password=False, auth_version=1, revision=0)

    response = upload(owner, 'import_users_preview', import_rows(
        ('synthetic_import_new', 'create', '', 'user', '', ''),
        ('synthetic_import_existing', 'create', '', 'user', '', '')))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'synthetic_import_new' in body and 'synthetic_import_existing' in body
    assert '账号已存在' in body  # per-row error, no upsert by display name
    preview = preview_id(response)
    assert AccountProfile.objects.filter(user=existing).count() == 1

    # Any row error rejects the whole batch, zero writes.
    response = commit(owner, 'import_users_commit', preview, OWNER_PASSWORD)
    assert response.status_code == 409 and '整批未执行' in response.content.decode()
    assert not AccountInvitation.objects.filter(username='synthetic_import_new').exists()
    assert AccountProfile.objects.get(user=existing).revision == 0

    # A clean create row defaults to a one-time invitation, never a password.
    response = upload(owner, 'import_users_preview', import_rows(('synthetic_import_new', 'create', '', 'user', '', '')))
    assert response.status_code == 200
    preview = preview_id(response)
    response = commit(owner, 'import_users_commit', preview, OWNER_PASSWORD)
    assert response.status_code == 200
    assert '一次性' in response.content.decode() or '导入邀请' in response.content.decode()
    invitation = AccountInvitation.objects.get(username='synthetic_import_new')
    assert invitation.role == 'user' and invitation.consumed is False
    token = re.search(r'/activate-account\?token=([A-Za-z0-9_\-]+)', response.content.decode())
    assert token, 'one-time invitation token must be shown once'
    assert not get_user_model().objects.filter(username='synthetic_import_new').exists()
    assert token.group(1) not in str(list(Audit.objects.values()))

    # Replaying the consumed preview is idempotent: no second invitation.
    response = commit(owner, 'import_users_commit', preview, OWNER_PASSWORD)
    assert response.status_code == 200
    assert AccountInvitation.objects.filter(username='synthetic_import_new').count() == 1
    assert response.content.decode().count('/activate-account?token=') == 0

    # An update row requires the explicit current revision and never touches passwords.
    revision = str(AccountProfile.objects.get(user=existing).revision)
    update_row = ('synthetic_import_existing', 'update', revision, 'user', str(setup['study'].id), 'study.view;data.export_raw')
    stale_row = ('synthetic_import_existing', 'update', '9999', 'user', str(setup['study'].id), 'study.view')
    response = upload(owner, 'import_users_preview', import_rows(stale_row, update_row))
    body = response.content.decode()
    assert '版本已变化' in body
    assert commit(owner, 'import_users_commit', preview_id(response), OWNER_PASSWORD).status_code == 409
    assert not Grant.objects.filter(user=existing).exists()
    existing.refresh_from_db()
    assert existing.check_password(THIRD_PASSWORD)

    response = upload(owner, 'import_users_preview', import_rows(update_row))
    preview = preview_id(response)
    response = commit(owner, 'import_users_commit', preview, OWNER_PASSWORD)
    assert response.status_code == 200
    assert sorted(Grant.objects.filter(user=existing, study=setup['study']).values_list('action', flat=True)) == ['data.export_raw', 'study.view']
    existing.refresh_from_db()
    assert existing.check_password(THIRD_PASSWORD)  # import never resets an existing password
    audit = Audit.objects.get(action='permission.import_grants')
    assert THIRD_PASSWORD not in str(audit.before) + str(audit.after)


def test_users_import_rejects_duplicates_mixed_studies_and_rolls_back(setup):
    owner = owner_client()
    admin = get_user_model().objects.create_user('synthetic_import_admin', password=NEW_PASSWORD)
    AccountProfile.objects.create(user=admin, role='admin', must_change_password=False, auth_version=1, revision=0)
    for action in ('study.view', 'data.export_raw', 'study.configure'):
        Grant.objects.create(user=admin, study=setup['study'], action=action, delegable=True)
    other = Study.objects.create(title='Synthetic import study B')
    client = Client()
    assert login(client, 'synthetic_import_admin', NEW_PASSWORD).status_code == 302

    target = get_user_model().objects.create_user('synthetic_import_target', password=THIRD_PASSWORD)
    profile = AccountProfile.objects.create(user=target, role='user', must_change_password=False, auth_version=1, revision=0)
    revision = str(profile.revision)
    mixed = workbook_bytes([
        ('synthetic_import_target', 'update', revision, 'user', str(setup['study'].id), 'study.view;data.export_raw'),
        ('synthetic_import_target', 'update', revision, 'user', str(other.id), 'study.view'),
    ])
    response = upload(client, 'import_users_preview', mixed)
    body = response.content.decode()
    assert '未授权研究' in body
    assert commit(client, 'import_users_commit', preview_id(response), NEW_PASSWORD).status_code == 409
    assert not Grant.objects.filter(user=target).exists()

    duplicate = workbook_bytes([
        ('synthetic_import_dup', 'create', '', 'user', '', ''),
        ('synthetic_import_dup', 'create', '', 'user', '', ''),
    ])
    response = upload(client, 'import_users_preview', duplicate)
    assert '多次' in response.content.decode()
    assert commit(client, 'import_users_commit', preview_id(response), NEW_PASSWORD).status_code == 409
    assert not AccountInvitation.objects.filter(username='synthetic_import_dup').exists()

    # Admin cannot create an admin through the template either.
    promote = workbook_bytes([('synthetic_import_promote', 'create', '', 'admin', '', '')])
    response = upload(client, 'import_users_preview', promote)
    assert '只有 Owner' in response.content.decode()
    assert commit(client, 'import_users_commit', preview_id(response), NEW_PASSWORD).status_code == 409
    assert not AccountInvitation.objects.filter(username='synthetic_import_promote').exists()


def test_roster_import_text_ids_numeric_and_password_staging(setup):
    owner = owner_client()
    study = Study.objects.create(title='Synthetic roster study', mode='password')
    researcher = get_user_model().objects.create_user('synthetic_roster_researcher', password=NEW_PASSWORD)
    AccountProfile.objects.create(user=researcher, role='admin', must_change_password=False, auth_version=1, revision=0)
    for action in ('study.view', 'study.configure'):
        Grant.objects.create(user=researcher, study=study, action=action, delegable=True)
    client = Client()
    assert login(client, 'synthetic_roster_researcher', NEW_PASSWORD).status_code == 302

    payload = workbook_bytes([('001', 'synthetic-roster-password-001'), ('002', 'synthetic-roster-password-002')],
                             headers=excel.ROSTER_HEADERS, title='roster')
    response = upload(client, 'import_roster_preview', payload, extra={'study_id': str(study.id)})
    assert response.status_code == 200
    body = response.content.decode()
    assert 'synthetic-roster-password' not in body
    assert 'synthetic-roster-password-001' not in client.cookies.get('gep_admin').value
    assert Participant.objects.filter(study=study).count() == 0
    assert '追加 2 个' in body or 'new_ids' in body
    preview = preview_id(response)
    staged = PermissionPreview.objects.get(pk=preview)
    assert 'synthetic-roster-password-001' not in str(staged.summary)
    assert 'synthetic-roster-password-001' not in str(staged.staged)

    # Tampering with the private staging invalidates the immutable digest.
    original = staged.staged
    staged.staged = {'rows': [{'code': '999', 'password_hash': original['rows'][0]['password_hash']}]}
    staged.save(update_fields=['staged'])
    assert commit(client, 'import_roster_commit', preview, NEW_PASSWORD).status_code == 409
    assert Participant.objects.filter(study=study).count() == 0
    staged.staged = original
    staged.save(update_fields=['staged'])

    response = commit(client, 'import_roster_commit', preview, NEW_PASSWORD)
    assert response.status_code == 200
    codes = sorted(Participant.objects.filter(study=study).values_list('code', flat=True))
    assert codes == ['001', '002']
    participant = Participant.objects.get(study=study, code='001')
    assert check_password('synthetic-roster-password-001', participant.password_hash)
    assert 'synthetic-roster-password-001' not in response.content.decode()

    # Numeric IDs are refused instead of guessing leading zeros; existing IDs never overwrite.
    numeric = workbook_bytes([(1, 'synthetic-roster-password-long')], headers=excel.ROSTER_HEADERS, title='roster')
    response = upload(client, 'import_roster_preview', numeric, extra={'study_id': str(study.id)})
    assert response.status_code == 200 and '数字形式' in response.content.decode()
    assert commit(client, 'import_roster_commit', preview_id(response), NEW_PASSWORD).status_code == 409
    existing = workbook_bytes([('001', 'synthetic-roster-password-other')], headers=excel.ROSTER_HEADERS, title='roster')
    response = upload(client, 'import_roster_preview', existing, extra={'study_id': str(study.id)})
    assert '已有该 ID' in response.content.decode()
    assert commit(client, 'import_roster_commit', preview_id(response), NEW_PASSWORD).status_code == 409
    assert check_password('synthetic-roster-password-001', Participant.objects.get(study=study, code='001').password_hash)


def test_roster_preview_binding_expiry_and_scope(setup):
    owner = owner_client()
    study = Study.objects.create(title='Synthetic roster binding', mode='password')
    Grant.objects.create(user=setup['owner'], study=study, action='study.view', delegable=True)
    Grant.objects.create(user=setup['owner'], study=study, action='study.configure', delegable=True)
    payload = workbook_bytes([('007', 'synthetic-roster-binding-7')], headers=excel.ROSTER_HEADERS, title='roster')

    response = upload(owner, 'import_roster_preview', payload, extra={'study_id': str(study.id)})
    preview = preview_id(response)
    # A legacy writer adds the same code without a revision bump: refused whole.
    Participant.objects.create(study=study, code='007', password_hash='')
    assert commit(owner, 'import_roster_commit', preview, OWNER_PASSWORD).status_code == 409
    assert Participant.objects.get(study=study, code='007').password_hash == ''

    response = upload(owner, 'import_roster_preview', payload, extra={'study_id': str(study.id)})
    expired = preview_id(response)
    PermissionPreview.objects.filter(pk=expired).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert commit(owner, 'import_roster_commit', expired, OWNER_PASSWORD).status_code == 409
    assert Participant.objects.filter(study=study).count() == 1

    # An actor without study.configure cannot preview a roster for that study.
    stranger = get_user_model().objects.create_user('synthetic_roster_stranger', password=NEW_PASSWORD)
    AccountProfile.objects.create(user=stranger, role='admin', must_change_password=False, auth_version=1, revision=0)
    client = Client()
    assert login(client, 'synthetic_roster_stranger', NEW_PASSWORD).status_code == 302
    response = upload(client, 'import_roster_preview', payload, extra={'study_id': str(study.id)})
    assert response.status_code == 403

    # Too-short passwords are per-row errors, and the batch is refused.
    weak = workbook_bytes([('008', 'short')], headers=excel.ROSTER_HEADERS, title='roster')
    response = upload(owner, 'import_roster_preview', weak, extra={'study_id': str(study.id)})
    assert '12 个字符' in response.content.decode()
    assert commit(owner, 'import_roster_commit', preview_id(response), OWNER_PASSWORD).status_code == 409
    assert not Participant.objects.filter(study=study, code='008').exists()


def test_xlsx_security_limits_and_formula_rejection(setup):
    owner = owner_client()
    study = Study.objects.create(title='Synthetic limits study')
    Grant.objects.create(user=setup['owner'], study=study, action='study.view', delegable=True)
    Grant.objects.create(user=setup['owner'], study=study, action='study.configure', delegable=True)

    formula = workbook_bytes([('001', '=SUM(A1:A2)')], headers=excel.ROSTER_HEADERS, title='roster')
    response = upload(owner, 'import_roster_preview', formula, extra={'study_id': str(study.id)})
    assert response.status_code == 415 and '公式' in response.content.decode()
    assert Participant.objects.filter(study=study).count() == 0

    oversized = b'PK\x03\x04' + b'0' * (2 * 1024 * 1024)
    response = upload(owner, 'import_users_preview', oversized)
    assert response.status_code == 413 and '2 MiB' in response.content.decode()

    expanded = workbook_bytes([('import_limits_user', 'create', '', 'user', '', '')])
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(expanded)) as source, zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr('xl/large.bin', b'0' * (11 * 1024 * 1024))
    response = upload(owner, 'import_users_preview', buffer.getvalue())
    assert response.status_code == 413 and '10 MiB' in response.content.decode()

    macros = workbook_bytes([('import_limits_user', 'create', '', 'user', '', '')])
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(macros)) as source, zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr('xl/vbaProject.bin', b'active')
    response = upload(owner, 'import_users_preview', buffer.getvalue())
    assert response.status_code == 415 and '宏' in response.content.decode()

    external = workbook_bytes([('import_limits_user', 'create', '', 'user', '', '')])
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(external)) as source, zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr('xl/externalLinks/externalLink1.xml', b'<externalLink/>')
    response = upload(owner, 'import_users_preview', buffer.getvalue())
    assert response.status_code == 415 and '外部链接' in response.content.decode()

    response = upload(owner, 'import_users_preview', workbook_bytes([('import_limits_user', 'create', '', 'user', '', '')]), filename='data.xlsm')
    assert response.status_code == 422 and 'xlsx' in response.content.decode()

    many_rows = workbook_bytes([(f'import_row_{index}', 'create', '', 'user', '', '') for index in range(1001)])
    response = upload(owner, 'import_users_preview', many_rows)
    assert response.status_code == 413 and '1000' in response.content.decode()

    wide_headers = tuple(excel.USERS_HEADERS) + tuple(f'extra{index}' for index in range(27))
    many_columns = workbook_bytes([tuple(f'c{index}' for index in range(len(wide_headers)))], headers=wide_headers)
    response = upload(owner, 'import_users_preview', many_columns)
    assert response.status_code == 413 and '32' in response.content.decode()


def test_modified_state_and_expired_user_import_is_rejected(setup):
    owner = owner_client()
    target = get_user_model().objects.create_user('synthetic_import_race', password=THIRD_PASSWORD)
    profile = AccountProfile.objects.create(user=target, role='user', must_change_password=False, auth_version=1, revision=0)
    row = ('synthetic_import_race', 'update', str(profile.revision), 'user', str(setup['study'].id), 'study.view;data.export_raw')
    response = upload(owner, 'import_users_preview', workbook_bytes([row]))
    preview = preview_id(response)

    # A legacy writer changes target grants after the preview (no revision bump).
    Grant.objects.create(user=target, study=setup['study'], action='build.upload', delegable=True)
    revision = accounts.instance_revision()
    assert commit(owner, 'import_users_commit', preview, OWNER_PASSWORD).status_code == 409
    assert sorted(Grant.objects.filter(user=target).values_list('action', flat=True)) == ['build.upload']
    assert accounts.instance_revision() == revision

    # Expiry refuses the commit with zero mutations.
    response = upload(owner, 'import_users_preview', workbook_bytes([row]))
    preview = preview_id(response)
    PermissionPreview.objects.filter(pk=preview).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert commit(owner, 'import_users_commit', preview, OWNER_PASSWORD).status_code == 409
    assert sorted(Grant.objects.filter(user=target).values_list('action', flat=True)) == ['build.upload']

    # A modified upload after preview: tampering with the parsed intent stored for
    # the commit no longer matches the preview digest, so it is refused whole.
    response = upload(owner, 'import_users_preview', workbook_bytes([row]))
    preview = preview_id(response)
    staged = PermissionPreview.objects.get(pk=preview)
    tampered = [dict(staged.staged['ops'][0])]
    tampered[0]['target'] = {'study.view': True}
    staged.staged = {'ops': tampered}
    staged.save(update_fields=['staged'])
    assert commit(owner, 'import_users_commit', preview, OWNER_PASSWORD).status_code == 409
    assert sorted(Grant.objects.filter(user=target).values_list('action', flat=True)) == ['build.upload']

    # A different administrator cannot spend the preview (actor binding).
    admin = get_user_model().objects.create_user('synthetic_import_other_admin', password=NEW_PASSWORD)
    AccountProfile.objects.create(user=admin, role='admin', must_change_password=False, auth_version=1, revision=0)
    for action in ('study.view', 'study.configure'):
        Grant.objects.create(user=admin, study=setup['study'], action=action, delegable=True)
    other = Client()
    assert login(other, 'synthetic_import_other_admin', NEW_PASSWORD).status_code == 302
    response = upload(owner, 'import_users_preview', workbook_bytes([row]))
    preview = preview_id(response)
    assert commit(other, 'import_users_commit', preview, NEW_PASSWORD).status_code == 404
    assert sorted(Grant.objects.filter(user=target).values_list('action', flat=True)) == ['build.upload']


def test_upload_rejects_non_xlsx_and_requires_authorized_actor(setup):
    owner = owner_client()
    response = upload(owner, 'import_users_preview', b'not a workbook', filename='data.xlsx')
    assert response.status_code == 422 and '工作簿' in response.content.decode()

    stranger = get_user_model().objects.create_user('synthetic_upload_stranger', password=NEW_PASSWORD)
    client = Client()
    assert login(client, 'synthetic_upload_stranger', NEW_PASSWORD).status_code == 302
    assert client.get('/users').status_code == 403
    response = upload(client, 'import_users_preview', workbook_bytes([]))
    assert response.status_code == 403


# --------------------------------------------------------------------------- P0302R

def test_roster_staged_hash_binding_and_bounded_purge(setup):
    """Changing only the private staged password hash must refuse the whole
    commit; consumed/expired staging drops the hash through a bounded cleanup
    while the protected acceptance volume is never touched."""
    owner = owner_client()
    study = Study.objects.create(title='Synthetic roster hash', mode='password')
    for action in ('study.view', 'study.configure'):
        Grant.objects.create(user=setup['owner'], study=study, action=action, delegable=True)
    payload = workbook_bytes([('101', 'synthetic-roster-hash-001')], headers=excel.ROSTER_HEADERS, title='roster')

    response = upload(owner, 'import_roster_preview', payload, extra={'study_id': str(study.id)})
    assert response.status_code == 200
    preview = preview_id(response)
    staged = PermissionPreview.objects.get(pk=preview)
    assert staged.staged['rows'][0]['password_hash']
    assert staged.staged['rows'][0]['password_hash'] not in response.content.decode()

    # Only the stored hash changes: the bound digest must refuse with zero writes.
    original = staged.staged
    staged.staged = {'rows': [{'code': '101', 'password_hash': make_password('synthetic-roster-tamper-002')}]}
    staged.save(update_fields=['staged'])
    response = commit(owner, 'import_roster_commit', preview, OWNER_PASSWORD)
    assert response.status_code == 409 and '未执行任何更改' in response.content.decode()
    assert Participant.objects.filter(study=study).count() == 0

    # The study mode is part of the binding too.
    staged.staged = original
    staged.save(update_fields=['staged'])
    Study.objects.filter(pk=study.pk).update(mode='anonymous')
    assert commit(owner, 'import_roster_commit', preview, OWNER_PASSWORD).status_code == 409
    assert Participant.objects.filter(study=study).count() == 0
    Study.objects.filter(pk=study.pk).update(mode='password')

    # The untouched preview still commits, and the consumed row keeps no hash.
    assert commit(owner, 'import_roster_commit', preview, OWNER_PASSWORD).status_code == 200
    participant = Participant.objects.get(study=study, code='101')
    assert check_password('synthetic-roster-hash-001', participant.password_hash)
    consumed = PermissionPreview.objects.get(pk=preview)
    assert consumed.consumed is True and consumed.staged is None

    # Replay is idempotent and still does not retain credential hashes.
    audited = Audit.objects.filter(action='roster.imported').count()
    assert commit(owner, 'import_roster_commit', preview, OWNER_PASSWORD).status_code == 200
    assert Audit.objects.filter(action='roster.imported').count() == audited
    assert Participant.objects.filter(study=study).count() == 1

    # Expired staging is cleared by the bounded purge; an unexpired preview keeps
    # its staging and still fails closed (preview_expired) instead of exposing it.
    live = preview_id(upload(owner, 'import_roster_preview',
                             workbook_bytes([('102', 'synthetic-roster-live-002')], headers=excel.ROSTER_HEADERS, title='roster'),
                             extra={'study_id': str(study.id)}))
    dead_a = preview_id(upload(owner, 'import_roster_preview',
                               workbook_bytes([('103', 'synthetic-roster-dead-003')], headers=excel.ROSTER_HEADERS, title='roster'),
                               extra={'study_id': str(study.id)}))
    dead_b = preview_id(upload(owner, 'import_roster_preview',
                               workbook_bytes([('104', 'synthetic-roster-dead-004')], headers=excel.ROSTER_HEADERS, title='roster'),
                               extra={'study_id': str(study.id)}))
    PermissionPreview.objects.filter(pk__in=[dead_a, dead_b]).update(expires_at=timezone.now() - timedelta(seconds=1))

    assert permissions.purge_sensitive_staging(limit=1) == 1
    assert PermissionPreview.objects.filter(pk__in=[dead_a, dead_b], staged__isnull=False).count() == 1
    assert permissions.purge_sensitive_staging() >= 1
    assert PermissionPreview.objects.filter(pk__in=[dead_a, dead_b], staged__isnull=False).count() == 0
    assert PermissionPreview.objects.get(pk=live).staged is not None
    assert Participant.objects.filter(study=study, code__in=['102', '103', '104']).count() == 0
    assert commit(owner, 'import_roster_commit', dead_a, OWNER_PASSWORD).status_code == 409
    assert Participant.objects.filter(study=study, code__in=['102', '103', '104']).count() == 0


def test_roster_replay_rechecks_study_configure(setup):
    owner = owner_client()
    study = Study.objects.create(title='Synthetic roster replay', mode='password')
    admin = get_user_model().objects.create_user('synthetic_roster_replay_admin', password=NEW_PASSWORD)
    AccountProfile.objects.create(user=admin, role='admin', must_change_password=False, auth_version=1, revision=0)
    for action in ('study.view', 'study.configure'):
        Grant.objects.create(user=admin, study=study, action=action, delegable=True)
    client = Client()
    assert login(client, admin.username, NEW_PASSWORD).status_code == 302
    payload = workbook_bytes([('201', 'synthetic-roster-replay-201')], headers=excel.ROSTER_HEADERS, title='roster')

    response = upload(client, 'import_roster_preview', payload, extra={'study_id': str(study.id)})
    preview = preview_id(response)
    assert commit(client, 'import_roster_commit', preview, NEW_PASSWORD).status_code == 200
    audited = Audit.objects.filter(action='roster.imported').count()
    assert commit(client, 'import_roster_commit', preview, NEW_PASSWORD).status_code == 200
    assert Audit.objects.filter(action='roster.imported').count() == audited

    # Revoking the study.configure grant must refuse the replay and read no old result.
    Grant.objects.filter(user=admin, study=study, action='study.configure').delete()
    response = commit(client, 'import_roster_commit', preview, NEW_PASSWORD)
    assert response.status_code == 403 and '名单导入完成' not in response.content.decode()
    assert Audit.objects.filter(action='roster.imported').count() == audited
    assert Participant.objects.filter(study=study).count() == 1


def test_users_import_commit_rechecks_owner_pointer(setup):
    """The Owner-only Admin invitation is re-checked per operation at commit, so a
    legacy Owner-pointer write without a revision bump refuses the whole batch."""
    # The previewing account keeps the Admin instance role after the pointer moves;
    # log in after the profile exists so the session auth version is current.
    AccountProfile.objects.create(user=setup['owner'], role='admin', must_change_password=False, auth_version=1, revision=0)
    owner = owner_client()
    response = upload(owner, 'import_users_preview', workbook_bytes([
        ('synthetic_owner_recheck_user', 'create', '', 'user', '', ''),
        ('synthetic_owner_recheck_admin', 'create', '', 'admin', '', ''),
    ]))
    assert response.status_code == 200
    preview = preview_id(response)

    revision = accounts.instance_revision()
    usurper = get_user_model().objects.create_user('synthetic_owner_usurper', password=NEW_PASSWORD)
    Instance.objects.filter(pk=1).update(owner_id=usurper.pk)
    assert accounts.instance_revision() == revision  # legacy write: no revision bump

    response = commit(owner, 'import_users_commit', preview, OWNER_PASSWORD)
    assert response.status_code == 403 and '只有 Owner' in response.content.decode()
    assert not AccountInvitation.objects.filter(username__startswith='synthetic_owner_recheck').exists()
    assert Audit.objects.filter(action='account.invite_issued').count() == 0
    assert PermissionPreview.objects.get(pk=preview).consumed is False


def test_users_import_replay_rechecks_delegation(setup):
    owner = owner_client()
    admin = get_user_model().objects.create_user('synthetic_import_replay_admin', password=NEW_PASSWORD)
    AccountProfile.objects.create(user=admin, role='admin', must_change_password=False, auth_version=1, revision=0)
    target = get_user_model().objects.create_user('synthetic_import_replay_target', password=THIRD_PASSWORD)
    AccountProfile.objects.create(user=target, role='user', must_change_password=False, auth_version=1, revision=0)
    for action in ('study.view', 'data.export_raw'):
        Grant.objects.create(user=admin, study=setup['study'], action=action, delegable=True)
    client = Client()
    assert login(client, admin.username, NEW_PASSWORD).status_code == 302

    row = ('synthetic_import_replay_target', 'update', str(AccountProfile.objects.get(user=target).revision), 'user',
           str(setup['study'].id), 'study.view;data.export_raw')
    preview = preview_id(upload(client, 'import_users_preview', workbook_bytes([row])))
    assert commit(client, 'import_users_commit', preview, NEW_PASSWORD).status_code == 200
    audits = Audit.objects.filter(action='permission.import_grants').count()
    assert commit(client, 'import_users_commit', preview, NEW_PASSWORD).status_code == 200
    assert Audit.objects.filter(action='permission.import_grants').count() == audits

    # The actor keeps the Admin role but loses the delegable authority.
    Grant.objects.filter(user=admin, study=setup['study']).update(delegable=False)
    response = commit(client, 'import_users_commit', preview, NEW_PASSWORD)
    assert response.status_code == 409 and '未执行任何更改' in response.content.decode()
    assert Audit.objects.filter(action='permission.import_grants').count() == audits
    assert sorted(Grant.objects.filter(user=target, study=setup['study']).values_list('action', flat=True)) == ['data.export_raw', 'study.view']


def rewrite_template(replacements):
    """Rewrite the valid generated template's data sheet with hostile XML."""
    source = excel.users_template_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as origin, zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in origin.infolist():
            payload = origin.read(info)
            if info.filename == 'xl/worksheets/sheet1.xml':
                for old, new in replacements.items():
                    assert old in payload, old
                    payload = payload.replace(old, new)
            target.writestr(info, payload)
    return buffer.getvalue()


def external_link_workbook():
    source = excel.users_template_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as origin, zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in origin.infolist():
            payload = origin.read(info)
            if info.filename == 'xl/worksheets/sheet1.xml':
                payload = payload.replace(b'</worksheet>', b'<hyperlinks><hyperlink ref="A1" r:id="rId1"/></hyperlinks></worksheet>')
            target.writestr(info, payload)
        target.writestr('xl/worksheets/_rels/sheet1.xml.rels',
                        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"'
                        b' Target="https://example.invalid/harvest" TargetMode="External"/></Relationships>')
    return buffer.getvalue()


def test_xlsx_sparse_dimension_header_formula_and_relationship_rejection(setup):
    """Malicious tiny workbooks are refused from the raw XML/dimension before
    openpyxl expands them, including header/other-sheet formulas and external
    relationship links; the generated templates and textual IDs stay valid."""
    owner = owner_client()
    study = Study.objects.create(title='Synthetic hostile XLSX')
    for action in ('study.view', 'study.configure'):
        Grant.objects.create(user=setup['owner'], study=study, action=action, delegable=True)

    # A far row under an oversized declared dimension is the sparse-padding DoS
    # vector; the raw XML bound refuses the tiny workbook immediately.
    padding = rewrite_template({
        b'<dimension ref="A1:F1" />': b'<dimension ref="A1:XFD1048576" />',
        b'</sheetData>': b'<row r="999999"><c r="A1" t="inlineStr"><is><t>x</t></is></c></row></sheetData>',
    })
    started = time.monotonic()
    response = upload(owner, 'import_users_preview', padding)
    assert response.status_code == 413 and '32' in response.content.decode()
    assert time.monotonic() - started < 5

    # A valid-looking workbook with an oversized declared dimension: the raw XML
    # bound refuses it fast; an unguarded parser would materialize the dimension.
    sparse = rewrite_template({b'<dimension ref="A1:F1" />': b'<dimension ref="A1:XFD1048576" />'})
    started = time.monotonic()
    response = upload(owner, 'import_users_preview', sparse)
    assert response.status_code == 413 and '32' in response.content.decode()
    assert time.monotonic() - started < 5

    # A far remote cell reference (row 999999) is refused as a row-limit breach.
    remote = rewrite_template({b'</sheetData>': b'<row r="999999"><c r="A999999" t="inlineStr"><is><t>x</t></is></c></row></sheetData>'})
    response = upload(owner, 'import_users_preview', remote)
    assert response.status_code == 413 and '1000' in response.content.decode()

    # Declared empty rows beyond the envelope are refused too.
    empty = rewrite_template({b'<dimension ref="A1:F1" />': b'<dimension ref="A1:F5000" />'})
    response = upload(owner, 'import_users_preview', empty)
    assert response.status_code == 413 and '1000' in response.content.decode()

    # Positional cell flooding is bounded by the per-sheet cell budget.
    flooded = rewrite_template({b'</sheetData>': b'<row r="2">' + b'<c/>' * 40000 + b'</row></sheetData>'})
    response = upload(owner, 'import_users_preview', flooded)
    assert response.status_code == 413 and '单元格' in response.content.decode()

    # Formula in the header row of the data sheet.
    header_formula = Workbook()
    sheet = header_formula.active
    sheet.title = 'users'
    sheet.append(['username', 'operation', 'revision', 'role', 'study_id', '=SUM(1,1)'])
    response = upload(owner, 'import_users_preview', book_bytes(header_formula))
    assert response.status_code == 415 and '公式' in response.content.decode()

    # Formula hidden in another (instruction) sheet.
    other_sheet_formula = Workbook()
    sheet = other_sheet_formula.active
    sheet.title = 'users'
    sheet.append(list(excel.USERS_HEADERS))
    instructions = other_sheet_formula.create_sheet('说明')
    instructions.append(['正常说明行'])
    instructions.append(['=SUM(A1:A1)'])
    response = upload(owner, 'import_users_preview', book_bytes(other_sheet_formula))
    assert response.status_code == 415 and '公式' in response.content.decode()

    # External relationship link smuggled through a worksheet hyperlink.
    response = upload(owner, 'import_users_preview', external_link_workbook())
    assert response.status_code == 415 and '外部链接' in response.content.decode()

    # Generated templates and textual identifiers still pass the same parser.
    assert excel.read_rows(excel.users_template_bytes(), excel.USERS_HEADERS) == []
    assert excel.read_rows(excel.roster_template_bytes('password'), excel.ROSTER_HEADERS) == []
    ordinary = workbook_bytes([('001', 'synthetic-roster-long-001')], headers=excel.ROSTER_HEADERS, title='roster')
    rows = excel.read_rows(ordinary, excel.ROSTER_HEADERS)
    assert [row['values']['id'] for row in rows] == ['001']
    response = upload(owner, 'import_roster_preview', ordinary, extra={'study_id': str(study.id)})
    assert response.status_code == 200 and '追加 1 个' in response.content.decode()
