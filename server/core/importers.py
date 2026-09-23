"""Preview-bound, bounded XLSX imports (users/permissions and roster).

Both imports parse a bounded workbook with :mod:`core.excel`, normalize it into
per-row errors plus a private staged intent, and expose the same single-use
preview identity as the permission matrix: the commit re-locks the instance and
actor, re-checks the binding digest and re-checks *each staged operation's*
current authority, so a revoked delegation, a moved Owner pointer or a changed
study mode is refused as a whole with zero partial writes.
"""
import csv
import io

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from . import access, accounts, excel, permissions
from .access import (ACTIONS, ROLES, dominates, is_instance_owner,
                     manageable_actions)
from .models import AccountInvitation, AccountProfile, Grant, Participant, Study
from .protocol import Rejected, require

USERS_KIND = permissions.USERS_KIND
ROSTER_KIND = permissions.ROSTER_KIND


# --------------------------------------------------------------------------- user import

def _users_binding(actor, instance, ops):
    usernames = {op['username'] for op in ops}
    pairs = {(op['user_id'], op['study_id']) for op in ops if op.get('user_id') and op.get('study_id')}
    return permissions._digest({
        'kind': USERS_KIND, 'actor': actor.pk, 'revision': instance.governance_revision,
        'ops': ops, 'users': permissions._account_digest({op['user_id'] for op in ops if op.get('user_id')}),
        'invitations': permissions._invitation_digest(usernames), 'grants': permissions._grant_digest(pairs),
        'studies': permissions._study_digest({op['study_id'] for op in ops if op.get('study_id')}),
    })


def _row_error(errors, row, code, message, message_en):
    errors.append({'row': row, 'code': code, 'message': message, 'message_en': message_en})


def _normalize_users(actor, raw):
    version = access.authorization_version()
    # Exact version routing: the two supported stored versions only; an unknown
    # version is refused before any row is read or normalized.
    require(version in (1, 2), 'unsupported_version', 409)
    rows = excel.read_rows(raw, excel.USERS_HEADERS)
    errors = []
    ops = []
    seen_create = {}
    seen_study = set()
    for item in rows:
        number = item['row']
        values = item['values']
        username, username_ok = excel.text_cell(values['username'])
        operation, operation_ok = excel.text_cell(values['operation'])
        if not username_ok or not operation_ok:
            _row_error(errors, number, 'numeric_cell', '用户名与操作必须是文本，不能是数字或日期。', 'Username and operation must be text, not numbers or dates.')
            continue
        if not username:
            _row_error(errors, number, 'username', '用户名不能为空。', 'The username cannot be empty.')
            continue
        if operation not in excel.OPERATIONS:
            _row_error(errors, number, 'operation', 'operation 必须是 create / update / disable / enable。', 'operation must be create / update / disable / enable.')
            continue
        revision, revision_ok = excel.integer_cell(values['revision'])
        target = get_user_model().objects.filter(username=username).first()
        profile = permissions._profile(target.pk) if target is not None else None
        if operation == 'create':
            role, role_ok = excel.text_cell(values['role'])
            role = role or 'user'
            if not role_ok or role not in ROLES:
                _row_error(errors, number, 'role', 'role 必须是 user 或 admin。', 'role must be user or admin.')
                continue
            bound = None
            if version == 2:
                action = 'accounts.create_admin' if role == 'admin' else 'accounts.create_user'
                if not access.allowed_platform(actor, action):
                    _row_error(errors, number,
                               'admin_appointment_owner_only' if role == 'admin' else 'forbidden',
                               '当前账号没有创建该角色的平台权限。' if role == 'admin' else '当前账号没有创建账号的平台权限。',
                               'This actor does not hold the platform action to create that role.')
                    continue
                if role == 'admin' and not is_instance_owner(actor):
                    # Restricted Admin creation: the finite bound is computed at
                    # preview time and must stay inside the issuer's current
                    # policy (re-verified at commit and again at activation).
                    bound = access.conservative_admin_bound(actor)
                    if not access.takeover_creation_allowed(actor, access.bound_subject_policy(bound)):
                        _row_error(errors, number, 'higher_privilege_target',
                                   '无法支配该受限 Admin 的拟生效策略，已拒绝。',
                                   'The issuer does not dominate the restricted Admin policy; the row was refused.')
                        continue
            elif role == 'admin' and not is_instance_owner(actor):
                _row_error(errors, number, 'admin_appointment_owner_only', '只有 Owner 可以创建 Admin 账号。', 'Only the Owner can create Admin accounts.')
                continue
            if target is not None:
                _row_error(errors, number, 'account_exists', '账号已存在；导入不会覆盖或重置已有密码，请使用 update/disable/enable。', 'The account already exists; imports never overwrite or reset passwords — use update/disable/enable.')
                continue
            if permissions._invitation_digest({username}):
                _row_error(errors, number, 'invitation_active', '该账号已有未使用的邀请。', 'This account already has an unused invitation.')
                continue
            seen_create.setdefault(username, number)
            if seen_create[username] != number:
                _row_error(errors, number, 'duplicate_identifier', '同一用户名在导入中出现多次。', 'The same username appears more than once in this import.')
                continue
            ops.append({'row': number, 'operation': 'create', 'username': username, 'role': role,
                        'bound_policy': bound})
            continue
        if target is None:
            _row_error(errors, number, 'account_missing', '目标账号不存在；导入不会按显示名猜测创建。', 'The target account does not exist; imports never guess or create from a display name.')
            continue
        if not revision_ok:
            _row_error(errors, number, 'revision', 'update / disable / enable 必须填写目标账号当前版本（整数）。', 'update / disable / enable must carry the target account revision (an integer).')
            continue
        if profile is None or profile.revision != revision:
            _row_error(errors, number, 'revision_mismatch', '目标账号版本已变化；请刷新后重新导出模板再试。', 'The target account revision changed; refresh and export the template again.')
            continue
        if target.pk == actor.pk:
            _row_error(errors, number, 'self_target', '不能对自己的账号执行此操作。', 'This action cannot target your own account.')
            continue
        if is_instance_owner(target):
            _row_error(errors, number, 'owner_protected', 'Owner 账号不可通过账号管理修改。', 'The Owner account cannot be changed through account management.')
            continue
        if version == 1:
            if not is_instance_owner(actor) and not dominates(actor, target):
                _row_error(errors, number, 'higher_privilege_target', '目标账号拥有操作者无法支配的研究权限。', 'The target holds study privileges this actor cannot dominate.')
                continue
        else:
            required = accounts.lifecycle_platform_actions('manage', target_role=profile.role if profile else 'user')
            if not all(access.allowed_platform(actor, action) for action in required):
                _row_error(errors, number, 'forbidden', '当前账号没有维护该目标角色的平台权限。', 'This actor lacks the platform action to manage that target role.')
                continue
            if not access.takeover_allowed(actor, target):
                _row_error(errors, number, 'higher_privilege_target', '目标账号拥有操作者无法支配的研究权限。', 'The target holds study privileges this actor cannot dominate.')
                continue
        if operation in ('disable', 'enable'):
            want_active = operation == 'enable'
            if target.is_active == want_active:
                _row_error(errors, number, 'no_change', '目标状态没有变化。', 'The target already has that state.')
                continue
            ops.append({'row': number, 'operation': operation, 'username': username, 'user_id': target.pk})
            continue
        study_id, study_ok = excel.text_cell(values['study_id'])
        if not study_ok or not study_id:
            _row_error(errors, number, 'study_id', 'update 必须填写研究 UUID。', 'update must carry the study UUID.')
            continue
        study = Study.objects.filter(pk=study_id).first() if _uuid_ok(study_id) else None
        if study is None:
            _row_error(errors, number, 'study_missing', '研究不存在。', 'The study does not exist.')
            continue
        actions_text, actions_ok = excel.text_cell(values['actions'])
        if not actions_ok:
            _row_error(errors, number, 'actions', 'actions 必须是文本。', 'actions must be text.')
            continue
        catalog = set(access.STUDY_V2_ACTIONS) if version == 2 else ACTIONS
        actions = [action.strip() for action in actions_text.split(';') if action.strip()]
        if not actions or not set(actions) <= catalog:
            _row_error(errors, number, 'actions', 'actions 必须是用分号分隔的已知动作代码。', 'actions must be a semicolon-separated list of known action codes.')
            continue
        if 'study.view' not in actions:
            _row_error(errors, number, 'visibility_required', '显式动作必须同时包含 study.view。', 'Explicit actions must include study.view.')
            continue
        if not is_instance_owner(actor):
            manageable = (manageable_actions(actor, study) if version == 1
                          else access.assignable_actions(actor, study))
            if not set(actions) <= manageable:
                _row_error(errors, number, 'delegation_forbidden', '包含操作者无权委派的动作或未授权研究。', 'The row includes actions this actor cannot delegate, or an unauthorized study.')
                continue
        if version == 2:
            try:
                after = access.with_study_override(access.canonical_policy(target), study.pk, actions)
            except ValueError:
                _row_error(errors, number, 'unsupported_policy_version', '账号存储的策略版本不受支持。', 'The stored policy version is not supported.')
                continue
            if not access.takeover_allowed(actor, target, after=after):
                _row_error(errors, number, 'higher_privilege_target', '目标账号拥有操作者无法支配的研究权限。', 'The target holds study privileges this actor cannot dominate.')
                continue
        key = (target.pk, study.pk)
        if key in seen_study:
            _row_error(errors, number, 'duplicate_identifier', '同一账号与研究在导入中出现多次。', 'The same account and study appear more than once in this import.')
            continue
        seen_study.add(key)
        selected = {action: False for action in actions}
        if version == 1:
            for action, delegable in Grant.objects.filter(user=target, study=study).values_list('action', 'delegable'):
                if action in selected and delegable:
                    selected[action] = True
        ops.append({'row': number, 'operation': 'update', 'username': username, 'user_id': target.pk,
                    'study_id': str(study.pk), 'target': selected})
    return rows, ops, errors


def _uuid_ok(value):
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def preview_users(actor, raw):
    require(access.allowed_platform(actor, 'accounts.view'), 'forbidden', 403)
    with transaction.atomic():
        instance = accounts._locked_instance()
        locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
        require(locked.is_active, 'auth_required', 403)
        require(access.allowed_platform(locked, 'accounts.view', instance=instance), 'forbidden', 403)
        rows, ops, errors = _normalize_users(locked, raw)
        summary = {'rows': len(rows), 'operations': [{'row': op['row'], 'operation': op['operation'],
                                                     'username': op['username'], 'user_id': op.get('user_id'),
                                                     'role': op.get('role', ''),
                                                     'study_id': str(op.get('study_id', '')),
                                                     'actions': sorted(op.get('target', {})),
                                                     'bound': op.get('bound_policy')} for op in ops],
                   'invitations': sorted(op['username'] for op in ops if op['operation'] == 'create')}
        row = permissions._preview(locked, USERS_KIND, '', summary, errors,
                                   _users_binding(locked, instance, ops), {'ops': ops}, instance.governance_revision)
    return row


def _reauthorize_user_op(actor, op, version):
    """Per-operation authority for one staged user-import row, read fresh."""
    require(version in (1, 2), 'unsupported_version', 409)
    if op['operation'] == 'create':
        if version == 2:
            action = 'accounts.create_admin' if op.get('role') == 'admin' else 'accounts.create_user'
            require(access.allowed_platform(actor, action), 'preview_stale', 403)
            if op.get('bound_policy') is not None:
                require(access.takeover_bound_allowed(actor, op['bound_policy']), 'preview_stale', 409)
            return None
        require(op.get('role') != 'admin' or is_instance_owner(actor), 'admin_appointment_owner_only', 403)
        return None
    target = get_user_model().objects.select_for_update().get(pk=op['user_id'])
    require(not is_instance_owner(target), 'preview_stale', 409)
    require(target.pk != actor.pk, 'preview_stale', 409)
    if version == 1:
        require(is_instance_owner(actor) or dominates(actor, target), 'preview_stale', 409)
    else:
        profile = permissions._profile(target.pk)
        required = accounts.lifecycle_platform_actions('manage',
                                                       target_role=profile.role if profile else 'user')
        require(all(access.allowed_platform(actor, action) for action in required), 'preview_stale', 403)
        require(access.takeover_allowed(actor, target), 'preview_stale', 409)
    return target


def commit_users(actor, password, preview_id):
    require(bool(preview_id), 'preview_required', 400)
    with transaction.atomic():
        instance, locked, row, replay = permissions._gate(actor, password, preview_id, USERS_KIND)
        if replay:
            return row.result
        version = access.authorization_version(instance)
        require(version in (1, 2), 'unsupported_version', 409)
        require(not row.errors, 'preview_errors', 409)
        ops = row.staged['ops']
        require(_users_binding(locked, instance, ops) == row.binding, 'preview_stale', 409)
        tokens = []
        for op in ops:
            if op['operation'] == 'create':
                _reauthorize_user_op(locked, op, version)
                require(not get_user_model().objects.filter(username=op['username']).exists(), 'preview_stale', 409)
                token = accounts._rand_password()
                invitation = AccountInvitation.objects.create(
                    issuer=locked, username=op['username'], role=op['role'],
                    token_hash=accounts.digest(token), bound_policy=op.get('bound_policy'),
                    expires_at=timezone.now() + accounts.INVITATION_TTL)
                accounts.audit(locked, 'account.invite_issued', invitation.id,
                               after={'username': op['username'], 'role': op['role'],
                                      'bound_policy': op.get('bound_policy')})
                tokens.append({'username': op['username'], 'token': token})
                continue
            target = _reauthorize_user_op(locked, op, version)
            if op['operation'] in ('disable', 'enable'):
                profile = accounts._profile_locked(target)
                accounts.apply_account_active(locked, target, profile, op['operation'] == 'enable')
                continue
            study = Study.objects.get(pk=op['study_id'])
            target_actions = {action: bool(flag) for action, flag in op['target'].items()}
            require(_matrix_authorize_import(locked, target, study, target_actions, version), 'preview_stale', 409)
            if version == 2:
                # Complete explicit selection for this study; legacy grant rows
                # stay untouched and are shadowed.
                profile = AccountProfile.objects.select_for_update().filter(user_id=target.pk).first()
                require(profile is not None, 'preview_stale', 409)
                try:
                    stored_before = {action: False for action in access.configured_study_actions(
                        access.canonical_policy(target), study.pk)}
                except ValueError:
                    raise Rejected('preview_stale', 409) from None
                overrides = dict(profile.study_overrides or {})
                overrides[str(study.pk)] = sorted(target_actions)
                profile.study_overrides = overrides
                profile.revision += 1
                profile.save(update_fields=['study_overrides', 'revision'])
            else:
                current = {action: delegable for action, delegable in
                           Grant.objects.filter(user=target, study=study).values_list('action', 'delegable')}
                for action in sorted(current):
                    if action not in target_actions:
                        Grant.objects.filter(user=target, study=study, action=action).delete()
                for action in sorted(target_actions):
                    if current.get(action) != target_actions[action]:
                        Grant.objects.update_or_create(user=target, study=study, action=action,
                                                       defaults={'delegable': target_actions[action]})
            before, after = permissions.grant_diff(
                stored_before if version == 2 else current, target_actions)
            accounts.audit(locked, 'permission.import_grants', f'{target.pk}:{study.pk}',
                           before={'actions': before}, after={'actions': after})
        result = {'invited': sorted(op['username'] for op in ops if op['operation'] == 'create'),
                  'updated': sorted(op['username'] for op in ops if op['operation'] == 'update'),
                  'disabled': sorted(op['username'] for op in ops if op['operation'] == 'disable'),
                  'enabled': sorted(op['username'] for op in ops if op['operation'] == 'enable')}
        permissions._finish(row, instance, result)
    return {'result': result, 'invitation_tokens': tokens}


def _matrix_authorize_import(actor, target_user, study, target, version=1):
    if version == 2:
        if is_instance_owner(target_user) or target_user.pk == actor.pk:
            return False
        profile = permissions._profile(target_user.pk)
        required = accounts.lifecycle_platform_actions('manage',
                                                       target_role=profile.role if profile else 'user')
        if not all(access.allowed_platform(actor, action) for action in required):
            return False
        if not set(target) <= access.assignable_actions(actor, study):
            return False
        try:
            after = access.with_study_override(access.canonical_policy(target_user), study.pk, target)
        except ValueError:
            return False
        return access.takeover_allowed(actor, target_user, after=after)
    if is_instance_owner(actor):
        return not is_instance_owner(target_user) and target_user.pk != actor.pk
    return (target_user.pk != actor.pk and not is_instance_owner(target_user)
            and dominates(actor, target_user)
            and set(target) <= manageable_actions(actor, study))


# --------------------------------------------------------------------------- roster import

# Capacity bound only: participant passwords have no strength requirement, but a
# single credential cannot be unbounded inside a bounded workbook/request.
MAX_ROSTER_PASSWORD = 1024
MAX_ROSTER_TEXT = 256000


def _roster_binding(actor, study, staged_rows, revision):
    """Bind the complete normalized staging, including credential hashes, to the
    study mode and governance revision; the digest never leaves the database.

    Only coded roster entries participate: an anonymous participant (a NULL
    code) is not a roster identity and must neither break the digest nor count
    as an existing ID.
    """
    existing = sorted(Participant.objects.filter(study=study, code__isnull=False)
                      .values_list('code', flat=True))
    return permissions._digest({'kind': ROSTER_KIND, 'actor': actor.pk, 'study': str(study.pk),
                                'mode': study.mode, 'revision': revision, 'existing': existing,
                                'rows': [{'code': row['code'], 'password_hash': row['password_hash']}
                                         for row in staged_rows]})


def _roster_csv_rows(raw, headers):
    """Parse pasted CSV text into the same row shape the workbook parser yields.

    Values stay exactly as written (the CSV reader never trims), a malformed
    quote is refused, and the row/column capacity matches the workbook path.
    A truly empty record (no text in any field) is ignored like a blank
    workbook row; a record whose fields carry whitespace is kept so the
    normalizer can refuse the ID per row with its real line number.
    """
    require(len(raw) <= MAX_ROSTER_TEXT, 'roster_limit', 413)
    try:
        parsed = list(csv.reader(io.StringIO(raw, newline=''), delimiter=',', strict=True))
    except csv.Error:
        raise Rejected('roster_columns')
    rows = []
    for index, fields in enumerate(parsed):
        if not any(field != '' for field in fields):
            continue
        values = {name: (fields[position] if position < len(fields) else None)
                  for position, name in enumerate(headers)}
        rows.append({'row': index + 1, 'values': values,
                     'columns': len(fields) == len(headers)})
    require(len(rows) <= excel.MAX_ROWS, 'row_limit', 413)
    return rows


def normalize_roster_rows(rows, mode, existing):
    """One shared CSV/XLSX text contract for participant credentials.

    IDs and passwords keep their exact text: no trimming, no silent rewriting.
    An ID must be 1-128 characters and is refused when empty or whitespace-only
    (never trimmed into something else); a password only has to be non-empty and
    inside the capacity bound. Ambiguous values (leading/trailing whitespace)
    are accepted exactly as written and reported through secret-free hints.

    Callers pass the real source row numbers; the parsers drop only records with
    no text at all (blank CSV lines, unfilled workbook tails), so a row that
    carries whitespace is refused per row instead of disappearing.
    """
    errors = []
    staged_rows = []
    seen = set()
    padded_ids = 0
    padded_passwords = 0
    for item in rows:
        number = item['row']
        values = item['values']
        if item.get('columns') is False:
            _row_error(errors, number, 'roster_columns',
                       '每行必须是 ID；密码模式再跟一列密码，不要多余列。',
                       'Each row must be an ID, plus exactly one password column in password mode.')
            continue
        code, code_ok = excel.exact_text_cell(values.get('id'))
        if not code_ok:
            _row_error(errors, number, 'numeric_identifier',
                       'ID 必须是文本；数字形式的 ID 会被拒绝，不会猜测前导零。',
                       'Identifiers must be text; numeric IDs are rejected because leading zeros cannot be guessed.')
            continue
        if code == '':
            _row_error(errors, number, 'id', 'ID 不能为空。', 'The ID cannot be empty.')
            continue
        if not code.strip():
            _row_error(errors, number, 'id_whitespace',
                       'ID 不能全是空白字符；空白不会被自动去掉。',
                       'The ID cannot be only whitespace; whitespace is never trimmed.')
            continue
        if len(code) > 128:
            _row_error(errors, number, 'id', 'ID 过长（最多 128 个字符）。',
                       'The ID is too long (at most 128 characters).')
            continue
        if code in seen:
            _row_error(errors, number, 'duplicate_identifier',
                       '本次导入中 ID 重复。',
                       'The same ID appears more than once in this import.')
            continue
        if code in existing:
            _row_error(errors, number, 'existing_identifier',
                       '名单中已有该 ID；追加导入不会覆盖。',
                       'The roster already contains that ID; append imports never overwrite.')
            continue
        seen.add(code)
        if code != code.strip():
            padded_ids += 1
        password_hash = ''
        if mode == 'password':
            password, password_ok = excel.exact_text_cell(values.get('password'))
            if not password_ok:
                _row_error(errors, number, 'password', '密码必须是文本。',
                           'The password must be text.')
                continue
            if password == '':
                _row_error(errors, number, 'password_required',
                           '密码不能为空；密码模式要求每行都有密码（不设强度要求）。',
                           'The password cannot be empty; password mode requires one for every row (no strength rule).')
                continue
            if len(password) > MAX_ROSTER_PASSWORD:
                _row_error(errors, number, 'password_too_long',
                           f'密码超过 {MAX_ROSTER_PASSWORD} 个字符的容量上限。',
                           f'The password exceeds the {MAX_ROSTER_PASSWORD}-character capacity limit.')
                continue
            if password != password.strip():
                padded_passwords += 1
            password_hash = make_password(password)
        staged_rows.append({'code': code, 'password_hash': password_hash})
    hints = []
    if padded_ids:
        hints.append({'code': 'id_padded', 'count': padded_ids,
                      'message': f'{padded_ids} 个 ID 含首尾空白，将按原样保存（不会自动去除）。',
                      'message_en': f'{padded_ids} ID(s) carry leading/trailing whitespace and are stored exactly as written (never trimmed).'})
    if padded_passwords:
        hints.append({'code': 'password_padded', 'count': padded_passwords,
                      'message': f'{padded_passwords} 个密码含首尾空白，将按原值验证（不会自动去除）。',
                      'message_en': f'{padded_passwords} password(s) carry leading/trailing whitespace and are verified exactly as written (never trimmed).'})
    return staged_rows, errors, hints


def preview_roster(actor, study, raw=None, text=None):
    """Preview one bounded roster import from an XLSX workbook or CSV text.

    Authorization is the study's own configuration scope (``study.view`` +
    ``study.configure``): an ordinary study creator previews without any
    instance account permission. Both sources share :func:`normalize_roster_rows`,
    so 1/1, 001, quoted commas and Unicode normalize identically; the preview
    carries row errors, secret-free hints and a digest binding, never a
    password or a hash.
    """
    access.guard(actor, study, 'study.configure')
    headers = excel.ROSTER_HEADERS if study.mode == 'password' else ('id',)
    if raw is not None:
        source = 'xlsx'
        rows = excel.read_rows(raw, headers, roster=True)
    elif text is not None:
        source = 'csv'
        rows = _roster_csv_rows(text, headers)
    else:
        raise Rejected('roster_source', 400)
    require(bool(rows), 'roster_empty', 400)
    with transaction.atomic():
        instance = accounts._locked_instance()
        locked = get_user_model().objects.select_for_update().get(pk=actor.pk)
        require(locked.is_active, 'auth_required', 403)
        study = Study.objects.get(pk=study.pk)
        access.guard(locked, study, 'study.configure')
        existing = set(Participant.objects.filter(study=study, code__isnull=False)
                       .values_list('code', flat=True))
        staged_rows, errors, hints = normalize_roster_rows(rows, study.mode, existing)
        summary = {'study': study.title, 'study_id': str(study.pk), 'mode': study.mode,
                   'source': source, 'rows': len(rows), 'new_ids': len(staged_rows),
                   'passwords_set': study.mode == 'password', 'hints': hints,
                   'fingerprint': permissions._digest([row['code'] for row in staged_rows])}
        row = permissions._preview(locked, ROSTER_KIND, str(study.pk), summary, errors,
                                   _roster_binding(locked, study, staged_rows, instance.governance_revision),
                                   {'rows': staged_rows}, instance.governance_revision)
    return row


def commit_roster(actor, password, preview_id, study_id):
    """Commit one roster preview under its own study scope, atomically.

    The gate locks instance -> actor -> study -> preview and re-checks the
    actor's current ``study.view`` + ``study.configure`` on that exact study
    (never an instance account permission) plus the actor's own password. The
    complete binding digest - study mode, existing codes, governance revision
    and the private staged rows - is re-checked before any row is written, and
    a consumed preview replays its stored result without a second write only
    while that same authority still holds.
    """
    require(bool(preview_id), 'preview_required', 400)
    require(bool(study_id), 'study_missing', 404)
    with transaction.atomic():
        instance, locked, row, study, replay = permissions._study_gate(
            actor, password, preview_id, ROSTER_KIND, study_id)
        if replay:
            return row.result
        require(not row.errors, 'preview_errors', 409)
        access.guard(locked, study, 'study.configure')
        staged_rows = row.staged['rows']
        require(_roster_binding(locked, study, staged_rows, instance.governance_revision) == row.binding, 'preview_stale', 409)
        for staged in staged_rows:
            Participant.objects.create(study=study, code=staged['code'], password_hash=staged['password_hash'])
            accounts.audit(locked, 'roster.imported', str(study.pk), after={'code': staged['code']})
        result = {'study': study.title, 'added': len(staged_rows)}
        permissions._finish(row, instance, result)
    return result


# --------------------------------------------------------------------------- replay

def replay_authorize(locked, row):
    """Re-check a consumed import preview's current per-operation authority.

    Called from :func:`core.permissions._replay_authorize`; uses only the
    secret-free summary recorded at preview time and routes by the stored
    authorization version.
    """
    version = access.authorization_version()
    require(version in (1, 2), 'unsupported_version', 409)
    if row.kind == USERS_KIND:
        summary = row.summary or {}
        ops = summary.get('operations')
        require(isinstance(ops, list), 'preview_invalid', 404)
        for op in ops:
            operation = op.get('operation')
            if operation == 'create':
                require('role' in op, 'preview_invalid', 404)
                if version == 2:
                    action = 'accounts.create_admin' if op['role'] == 'admin' else 'accounts.create_user'
                    require(access.allowed_platform(locked, action), 'preview_stale', 403)
                    if op.get('bound_policy') is not None:
                        require(access.takeover_bound_allowed(locked, op['bound_policy']), 'preview_stale', 409)
                else:
                    require(op['role'] != 'admin' or is_instance_owner(locked), 'admin_appointment_owner_only', 403)
                continue
            target = get_user_model().objects.filter(pk=op.get('user_id')).first()
            require(target is not None, 'preview_stale', 409)
            require(not is_instance_owner(target), 'preview_stale', 409)
            require(target.pk != locked.pk, 'preview_stale', 409)
            if version == 1:
                require(is_instance_owner(locked) or dominates(locked, target), 'preview_stale', 409)
            else:
                profile = permissions._profile(target.pk)
                required = accounts.lifecycle_platform_actions('manage',
                                                               target_role=profile.role if profile else 'user')
                require(all(access.allowed_platform(locked, action) for action in required), 'preview_stale', 403)
                require(access.takeover_allowed(locked, target), 'preview_stale', 409)
            if operation == 'update':
                study = Study.objects.filter(pk=op.get('study_id')).first()
                require(study is not None, 'preview_stale', 409)
                if version == 1:
                    require(is_instance_owner(locked) or set(op.get('actions') or []) <= manageable_actions(locked, study),
                            'preview_stale', 409)
                else:
                    require(is_instance_owner(locked)
                            or set(op.get('actions') or []) <= access.assignable_actions(locked, study),
                            'preview_stale', 409)
        return
    if row.kind == ROSTER_KIND:
        study = Study.objects.filter(pk=row.scope).first()
        require(study is not None, 'preview_invalid', 404)
        require(study.mode == (row.summary or {}).get('mode'), 'preview_stale', 409)
        from .access import guard
        guard(locked, study, 'study.configure')
        return
    raise Rejected('preview_invalid', 404)
