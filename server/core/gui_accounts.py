"""Instance account pages: /users, /account/password, /activate-account, templates.

Server-side authorization lives in core.accounts / core.access / core.permissions;
these views only resolve inputs, render errors and show one-time secrets (never
stored). Every permission change is preview-bound and committed with the actor's
own password; the study page keeps its legacy routes but points account
operations at /users.
"""
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect, render
from django.utils import timezone

from . import access, accounts, governance_migration, permissions, ui
from .access import allowed_platform, is_instance_owner
from .gui import ACTION_LABELS, admin_host, admin_origin
from .models import AccountInvitation, AccountProfile, Instance
from .protocol import Rejected, require
from .throttle import check
from .views import endpoint

MESSAGES = {
    'reauth_failed': '重新认证失败：操作者密码不正确，未执行任何更改。',
    'revision_conflict': '治理版本已变化，请刷新页面后重试；未执行任何更改。',
    'auth_required': '请先登录。',
    'forbidden': '当前账号没有实例治理权限。',
    'owner_protected': 'Owner 账号不可通过账号管理修改。',
    'admin_appointment_owner_only': '只有 Owner 可以任命或降级 Admin。',
    'owner_only': '只有 Owner 可以执行此操作。',
    'self_target': '不能对自己的账号执行此操作；请使用修改密码。',
    'higher_privilege_target': '目标账号拥有操作者无法支配的研究权限（含不可委派权限），不能重置密码、停用、启用、调整其权限或永久删除。',
    'password_change_required': '必须先修改临时密码，才能执行账号治理操作。',
    'password_reused': '新密码不能与当前密码相同。',
    'role': '角色不合法。',
    'username': '用户名不合法。',
    'account_exists': '账号已存在，或已有同名的有效邀请。',
    'invitation_active': '该账号已有未使用的邀请，请先撤销或等待过期。',
    'account_missing': '目标账号不存在。',
    'no_change': '目标状态没有变化。',
    'delete_confirm_mismatch': '确认用户名与目标账号不一致，永久删除未执行，账号保持原状。',
    'no_conflicts': '当前没有需要收敛的授权矛盾。',
    'choice': '请选择处理方式。',
    'current_password_wrong': '当前密码不正确。',
    # One text for the unified four-class researcher rule (U07); the account
    # entries never raise the legacy length-only code any more, so no page shows
    # the old 16-character threshold.
    'password_weak': '密码至少 6 位，且至少各含一个 ASCII 大写字母、小写字母、数字与可见标点符号；空格不算符号，首尾空白不会被去掉。',
    'password_mismatch': '两次输入的密码不一致。',
    'invitation_inactive': '邀请已使用、已撤销或已失效。',
    'activation_failed': '激活失败：邀请无效、已使用、已撤销或已过期。',
    'rate_limited': '请求过于频繁，请稍后再试。',
    'unknown_operation': '未知操作。',
    'preview_required': '缺少预览标识，请重新预览后再提交。',
    'preview_invalid': '预览不存在、已过期或不属于当前账号，请重新预览。',
    'preview_expired': '预览已过期，请重新预览后再提交；未执行任何更改。',
    'preview_stale': '预览后相关账号、研究或权限已变化，请重新预览；未执行任何更改。',
    'preview_errors': '预览仍有逐行错误，整批未执行；请修正后重新上传。',
    'visibility_children_contradiction': '取消研究可见性时不能保留子权限；本次未执行任何更改。',
    'delegation_forbidden': '包含当前账号没有有效且可委派的权限，或目标研究超出授权范围；未执行任何更改。',
    'file_required': '请选择要上传的 XLSX 文件。',
    'file_too_large': '文件超过 2 MiB 压缩上限，未导入任何行。',
    'expanded_too_large': '文件展开后超过 10 MiB 上限，未导入任何行。',
    'invalid_xlsx': '文件不是有效的 .xlsx 工作簿，未导入任何行。',
    'macro_rejected': '文件包含宏，已整体拒绝，未导入任何行。',
    'external_link_rejected': '文件包含公式或外部链接，已整体拒绝，未导入任何行。',
    'formula_rejected': '文件包含公式，已整体拒绝，未导入任何行。',
    'template_headers': '表头与模板不一致，请下载最新模板填写。',
    'column_limit': '列数超过 32 列上限。',
    'row_limit': '行数超过 1000 行上限。',
    'sheet_cells': '工作表的单元格总数超过上限，已整体拒绝。',
    'numeric_cell': '该单元格必须是文本，不能是数字或日期。',
    'numeric_identifier': 'ID 必须是文本；数字形式的 ID 会被拒绝，不会猜测前导零。',
    'duplicate_identifier': '同一标识在导入中重复。',
    'existing_identifier': '名单中已有该 ID；追加导入不会覆盖已有行。',
    'revision': 'update / disable / enable 必须填写目标账号当前版本（整数）。',
    'revision_mismatch': '目标账号版本已变化，请刷新后重新填写；整批未执行。',
    'study_id': 'update 必须填写研究 UUID。',
    'study_missing': '研究不存在或不可用。',
    'actions': 'actions 必须是用分号分隔的已知动作代码。',
    'visibility_required': '显式动作必须同时包含 study.view。',
    'operation': 'operation 必须是 create / update / disable / enable。',
    'password': '密码列必须是文本。',
    'id': 'ID 不合法。',
    'admin_origin': '管理地址配置无效，未生成任何链接；本次未创建邀请。',
    'already_enabled': '实例已按 v2 授权运行，重复提交不会重置任何例外；本次未写入更改。',
    'unsupported_version': '实例或账号存储的授权/策略版本不是受支持的 1 或 2，已拒绝并保持原状。',
    'unsupported_policy_version': '账号存储的策略版本不是受支持的 1 或 2，已拒绝并保持原状。',
    'unknown_choice_required': '未知项必须逐条给出明确选择后才能启用；本次未写入任何更改。',
    'unknown_choice_invalid': '未知项的选择值不受支持；本次未写入任何更改。',
    'unknown_choice_unknown': '提交中包含预览之外的未知项选择；本次未写入任何更改。',
}


def message_for(code, lang='zh'):
    return ui.error_message(code, MESSAGES.get(code, ui.tr(lang, 'error_invalid_request')), lang)


COMMIT_OPS = {'matrix': 'matrix_commit', 'reconcile': 'reconcile_commit',
              'users_import': 'import_users_commit', 'roster_import': 'import_roster_commit',
              'migration_enable': 'migration_confirm', 'platform': 'platform_commit'}
IMPORT_OPS = ('import_users_preview', 'import_users_commit', 'import_roster_preview', 'import_roster_commit')


def _target(username):
    user = get_user_model().objects.filter(username=(username or '').strip()).first()
    require(user is not None, 'account_missing', 404)
    return user


def _apply(request):
    op = request.POST.get('op')
    lang = ui.lang_of(request)
    password = request.POST.get('password', '')
    revision = request.POST.get('revision')
    username = request.POST.get('username', '')
    if op in IMPORT_OPS:
        from . import gui_imports
        return gui_imports.apply_import(request, op)
    if op == 'invite_account':
        # The link origin is validated before the invitation is written: a
        # broken ADMIN_ORIGIN must fail closed without leaving a one-time token
        # that can never be shown again.
        origin = admin_origin(request)
        result = accounts.invite_account(request.user, password, revision, username, request.POST.get('role', 'user'))
        return {'notice': ui.notice(lang, f"已创建账号邀请：{result['username']}（角色 {result['role']}）。",
                                    f"Account invitation created: {result['username']} (role {result['role']})."),
                'invitation_link': origin + '/activate-account?token=' + result['token'],
                'invitation_username': result['username']}
    if op == 'create_temp':
        result = accounts.create_temporary_account(request.user, password, revision, username)
        return {'notice': ui.notice(lang, f"已创建临时密码账号：{result['username']}。",
                                    f"Temporary-password account created: {result['username']}."),
                'secret': result['temporary_password'], 'secret_username': result['username']}
    if op == 'reset_password':
        result = accounts.reset_temporary_password(request.user, password, revision, _target(username).pk)
        return {'notice': ui.notice(lang, f"已为 {result['username']} 生成新的临时密码。",
                                    f"A new temporary password was generated for {result['username']}."),
                'secret': result['temporary_password'], 'secret_username': result['username']}
    if op in ('disable', 'enable'):
        result = accounts.set_account_active(request.user, password, revision, _target(username).pk, op == 'enable')
        if result['is_active']:
            notice = ui.notice(lang, f"已启用账号：{result['username']}。", f"Account enabled: {result['username']}.")
        else:
            notice = ui.notice(lang, f"已停用账号：{result['username']}。", f"Account disabled: {result['username']}.")
        return {'notice': notice}
    if op == 'set_role':
        result = accounts.set_account_role(request.user, password, revision, _target(username).pk, request.POST.get('role', ''))
        return {'notice': ui.notice(lang, f"已将 {result['username']} 的角色设为 {result['role']}。",
                                    f"Role of {result['username']} set to {result['role']}.")}
    if op == 'delete':
        # Permanent, irreversible: the red entry posts the typed username and
        # the actor password; the service re-checks everything atomically.
        target = _target(username)
        result = accounts.delete_account(request.user, password, revision, target.pk,
                                         request.POST.get('confirm_username', ''))
        return {'notice': ui.notice(
                    lang,
                    f"已永久删除账号 {result['username']}（稳定主体 {result['principal']}）。登录、邀请、预览与恢复签发均已失效；研究、被试、会话数据与历史审计保留。",
                    f"Account {result['username']} was permanently deleted (stable subject {result['principal']}). Its login, invitations, previews and recovery issuances no longer work; studies, participants, session data and the audit history are kept."),
                'deleted_principal': result['principal'], 'deleted_username': result['username']}
    if op == 'revoke_invitation':
        result = accounts.revoke_invitation(request.user, password, revision, request.POST.get('invitation_id'))
        return {'notice': ui.notice(lang, f"已撤销账号邀请：{result['username']}。",
                                    f"Account invitation revoked: {result['username']}.")}
    if op in ('reconcile', 'reconcile_preview'):
        preview = permissions.preview_reconcile(request.user, request.POST.get('choice', ''))
        return {'preview': permissions.preview_payload(preview, lang), 'commit_op': 'reconcile_commit'}
    if op == 'reconcile_commit':
        resolved = permissions.commit_reconcile(request.user, password, request.POST.get('preview_id'))
        return {'notice': ui.notice(lang, f"已完成 {len(resolved)} 组授权矛盾收敛。",
                                    f"Resolved {len(resolved)} authorization conflict group(s).")}
    if op == 'matrix_preview':
        preview = permissions.preview_matrix(request.user, request.POST)
        return {'preview': permissions.preview_payload(preview, lang), 'commit_op': 'matrix_commit'}
    if op == 'matrix_commit':
        result = permissions.commit_matrix(request.user, password, request.POST.get('preview_id'))
        if lang == 'en':
            shown = ', '.join(result['actions']) or 'no explicit actions'
            notice = f"Permission matrix updated: {result['username']} · {result['study']} → {shown}."
        else:
            shown = '、'.join(result['actions']) or '无显式动作'
            notice = f"权限矩阵已更新：{result['username']} · {result['study']} → {shown}。"
        return {'notice': notice}
    if op == 'platform_preview':
        preview = permissions.preview_platform(request.user, request.POST)
        return {'preview': permissions.preview_payload(preview, lang), 'commit_op': 'platform_commit'}
    if op == 'platform_commit':
        result = permissions.commit_platform(request.user, password, request.POST.get('preview_id'))
        if lang == 'en':
            notice = f"Platform permissions updated: {result['username']} → {', '.join(result['platform'])}."
        else:
            notice = f"平台权限已更新：{result['username']} → {'、'.join(result['platform'])}。"
        return {'notice': notice}
    if op == 'migration_diff':
        # Read-only, Owner-only: no preview row, no permission write, no
        # version change. An ordinary Admin never receives the instance-wide
        # difference.
        require(is_instance_owner(request.user), 'owner_only', 403)
        return {'migration_diff': governance_migration.diff_payload()}
    if op == 'migration_preview':
        choices = {key.split(':', 1)[1]: value for key, value in request.POST.items()
                   if key.startswith('unknown:')}
        row = governance_migration.preview_enablement(request.user, choices)
        return {'preview': permissions.preview_payload(row, lang), 'commit_op': 'migration_confirm'}
    if op == 'migration_confirm':
        result = governance_migration.confirm_enablement(request.user, password, revision,
                                                         request.POST.get('preview_id'))
        if lang == 'en':
            notice = (f"Authorization version 2 enabled (revision {result['revision']}); "
                      f"{len(result['overrides'])} explicit override(s) and "
                      f"{len(result['future_defaults'])} future default(s) written.")
        else:
            notice = (f"已启用 v2 授权（治理版本 {result['revision']}）；"
                      f"已写入 {len(result['overrides'])} 项显式完整覆盖与 "
                      f"{len(result['future_defaults'])} 项未来默认。")
        return {'notice': notice}
    raise Rejected('unknown_operation', 400)


def _users_context(request):
    lang = ui.lang_of(request)
    matrix = permissions.matrix_page(request.user, request.GET.get('q', ''),
                                     request.GET.get('page', '1'), lang)
    owned = is_instance_owner(request.user)
    if owned:
        conflicts = permissions.conflict_page(request.GET.get('cpage', '1'),
                                              keep={'q': matrix['search'], 'page': matrix['page']})
    else:
        # Ordinary Admins cannot reconcile conflicts, so the page never loads them.
        conflicts = {'rows': [], 'total': 0, 'page': 1, 'pages': 1, 'page_links': []}
    return {'rows': matrix['rows'], 'owner_username': Instance.objects.values_list('owner__username', flat=True).get(pk=1),
            'is_owner': owned,
            'invitations': AccountInvitation.objects.filter(consumed=False, revoked=False, expires_at__gt=timezone.now()).order_by('username'),
            'conflicts': conflicts, 'revision': accounts.instance_revision(),
            'matrix': matrix, 'action_labels': ACTION_LABELS,
            # Bounded account-lifecycle audit view: a deleted actor is rendered
            # as its stable principal id, never as a reused username.
            'account_audit': accounts.recent_account_audits(),
            # Owner-only, read-only v1 -> v2 enablement difference (nothing is
            # written here); an ordinary Admin never sees or can trigger it.
            'migration': governance_migration.page_state() if owned else None,
            'study_choice': permissions.configure_studies_page(request.user, request.GET.get('study_q', ''))}


@endpoint
def users_page(request):
    admin_host(request)
    if not request.user.is_authenticated:
        return redirect('/login')
    # One version-routed governance gate: v1 keeps the Owner/Admin boundary,
    # v2 needs the finite accounts.view platform action from the stored policy.
    require(allowed_platform(request.user, 'accounts.view'), 'forbidden', 403)
    extra = {'notice': '', 'error': '', 'secret': None, 'secret_username': '',
             'invitation_username': '', 'preview': None, 'invitation_tokens': [], 'import_result': None,
             'deleted_principal': '', 'deleted_username': ''}
    status = 200
    if request.method == 'POST':
        try:
            extra.update(_apply(request))
        except (Rejected, ObjectDoesNotExist, ValueError) as error:
            code = error.code if isinstance(error, Rejected) else 'invalid_request'
            extra['error'] = message_for(code, ui.lang_of(request))
            status = error.status if isinstance(error, Rejected) else 400
            # A rejected confirmation (for example a wrong own password) keeps the
            # still-valid preview visible so the actor can retry it.
            pending = permissions.pending_preview(request.user, request.POST.get('preview_id'))
            if pending is not None and permissions.preview_retry_authorized(request.user, pending):
                extra['preview'] = permissions.preview_payload(pending, ui.lang_of(request))
                extra['commit_op'] = COMMIT_OPS.get(pending.kind, '')
    context = _users_context(request)
    context.update(extra)
    context['nav_current'] = 'users'
    response = render(request, 'core/users.html', context, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@endpoint
def password_page(request):
    admin_host(request)
    if not request.user.is_authenticated:
        return redirect('/login')
    must_change = AccountProfile.objects.filter(user_id=request.user.pk, must_change_password=True).exists()
    review = {'error': '', 'must_change': must_change, 'nav_current': 'password'}
    if request.method == 'POST':
        try:
            profile = accounts.change_own_password(request.user, request.POST.get('current', ''), request.POST.get('new', ''), request.POST.get('confirm', ''))
        except Rejected as exc:
            review['error'] = message_for(exc.code, ui.lang_of(request))
            return render(request, 'core/password.html', review, status=exc.status)
        request.user.refresh_from_db()
        update_session_auth_hash(request, request.user)
        request.session['gep_auth_version'] = profile.auth_version
        return redirect('/')
    return render(request, 'core/password.html', review)


@endpoint
def activate_account_page(request):
    admin_host(request)
    lang = ui.lang_of(request)
    if request.method == 'POST':
        check('account_activation:' + request.META.get('REMOTE_ADDR', ''), 5)
        try:
            user = accounts.activate_account(request.POST.get('token', ''), request.POST.get('password', ''), request.POST.get('confirm', ''))
        except Rejected as exc:
            return render(request, 'core/activate_account.html', {'error': message_for(exc.code, lang), 'token': '', 'done': False}, status=exc.status)
        return render(request, 'core/activate_account.html', {'error': '', 'token': '', 'done': True, 'username': user.username})
    return render(request, 'core/activate_account.html', {'error': '', 'token': request.GET.get('token', ''), 'done': False})
