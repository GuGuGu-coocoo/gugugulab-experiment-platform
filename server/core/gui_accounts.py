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

from . import accounts, permissions
from .access import allowed, conflicts, is_account_administrator, is_instance_owner
from .gui import ACTION_LABELS, admin_host
from .models import AccountInvitation, AccountProfile, Instance, Study
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
    'higher_privilege_target': '目标账号拥有操作者无法支配的研究权限（含不可委派权限），不能重置密码、停用、启用或调整其权限。',
    'password_change_required': '必须先修改临时密码，才能执行账号治理操作。',
    'password_reused': '新密码不能与当前密码相同。',
    'role': '角色不合法。',
    'username': '用户名不合法。',
    'account_exists': '账号已存在，或已有同名的有效邀请。',
    'invitation_active': '该账号已有未使用的邀请，请先撤销或等待过期。',
    'account_missing': '目标账号不存在。',
    'no_change': '目标状态没有变化。',
    'no_conflicts': '当前没有需要收敛的授权矛盾。',
    'choice': '请选择处理方式。',
    'current_password_wrong': '当前密码不正确。',
    'password_too_short': '密码至少 16 个字符。',
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
}


def message_for(code):
    return MESSAGES.get(code, '操作未完成，请检查输入或权限后重试。')


COMMIT_OPS = {'matrix': 'matrix_commit', 'reconcile': 'reconcile_commit'}


def _target(username):
    user = get_user_model().objects.filter(username=(username or '').strip()).first()
    require(user is not None, 'account_missing', 404)
    return user


def _apply(request):
    op = request.POST.get('op')
    password = request.POST.get('password', '')
    revision = request.POST.get('revision')
    username = request.POST.get('username', '')
    if op == 'invite_account':
        result = accounts.invite_account(request.user, password, revision, username, request.POST.get('role', 'user'))
        return {'notice': f"已创建账号邀请：{result['username']}（角色 {result['role']}）。", 'invitation_token': result['token'], 'invitation_username': result['username']}
    if op == 'create_temp':
        result = accounts.create_temporary_account(request.user, password, revision, username)
        return {'notice': f"已创建临时密码账号：{result['username']}。", 'secret': result['temporary_password'], 'secret_username': result['username']}
    if op == 'reset_password':
        result = accounts.reset_temporary_password(request.user, password, revision, _target(username).pk)
        return {'notice': f"已为 {result['username']} 生成新的临时密码。", 'secret': result['temporary_password'], 'secret_username': result['username']}
    if op in ('disable', 'enable'):
        result = accounts.set_account_active(request.user, password, revision, _target(username).pk, op == 'enable')
        return {'notice': f"已{'启用' if result['is_active'] else '停用'}账号：{result['username']}。"}
    if op == 'set_role':
        result = accounts.set_account_role(request.user, password, revision, _target(username).pk, request.POST.get('role', ''))
        return {'notice': f"已将 {result['username']} 的角色设为 {result['role']}。"}
    if op == 'revoke_invitation':
        result = accounts.revoke_invitation(request.user, password, revision, request.POST.get('invitation_id'))
        return {'notice': f"已撤销账号邀请：{result['username']}。"}
    if op in ('reconcile', 'reconcile_preview'):
        preview = permissions.preview_reconcile(request.user, request.POST.get('choice', ''))
        return {'preview': permissions.preview_payload(preview), 'commit_op': 'reconcile_commit'}
    if op == 'reconcile_commit':
        resolved = permissions.commit_reconcile(request.user, password, request.POST.get('preview_id'))
        return {'notice': f"已完成 {len(resolved)} 组授权矛盾收敛。"}
    if op == 'matrix_preview':
        preview = permissions.preview_matrix(request.user, request.POST)
        return {'preview': permissions.preview_payload(preview), 'commit_op': 'matrix_commit'}
    if op == 'matrix_commit':
        result = permissions.commit_matrix(request.user, password, request.POST.get('preview_id'))
        shown = '、'.join(result['actions']) or '无显式动作'
        return {'notice': f"权限矩阵已更新：{result['username']} · {result['study']} → {shown}。"}
    raise Rejected('unknown_operation', 400)


def _users_context(request):
    User = get_user_model()
    profiles = {profile.user_id: profile for profile in AccountProfile.objects.all()}
    rows = []
    for user in User.objects.order_by('id'):
        profile = profiles.get(user.pk)
        rows.append({'id': user.pk, 'username': user.username, 'is_owner': is_instance_owner(user),
                     'role': profile.role if profile is not None else 'user', 'is_active': user.is_active,
                     'must_change_password': profile.must_change_password if profile is not None else False})
    conflict_rows = []
    for user_id, study_id, actions in conflicts():
        conflict_rows.append({'username': User.objects.filter(pk=user_id).values_list('username', flat=True).first() or str(user_id),
                              'study': Study.objects.filter(pk=study_id).values_list('title', flat=True).first() or str(study_id),
                              'actions': '、'.join(actions)})
    configure_studies = [study for study in Study.objects.order_by('title')
                         if is_instance_owner(request.user) or allowed(request.user, study, 'study.configure')]
    return {'rows': rows, 'owner_username': Instance.objects.get(pk=1).owner.username, 'is_owner': is_instance_owner(request.user),
            'invitations': AccountInvitation.objects.filter(consumed=False, revoked=False, expires_at__gt=timezone.now()).order_by('username'),
            'conflicts': conflict_rows, 'revision': accounts.instance_revision(),
            'matrix': permissions.matrix_rows(request.user), 'action_labels': ACTION_LABELS,
            'configure_studies': configure_studies}


@endpoint
def users_page(request):
    admin_host(request)
    if not request.user.is_authenticated:
        return redirect('/login')
    require(is_account_administrator(request.user), 'forbidden', 403)
    extra = {'notice': '', 'error': '', 'secret': None, 'invitation_token': None, 'secret_username': '',
             'invitation_username': '', 'preview': None, 'invitation_tokens': [], 'import_result': None}
    status = 200
    if request.method == 'POST':
        try:
            extra.update(_apply(request))
        except (Rejected, ObjectDoesNotExist, ValueError) as error:
            code = error.code if isinstance(error, Rejected) else 'invalid_request'
            extra['error'] = message_for(code)
            status = error.status if isinstance(error, Rejected) else 400
            # A rejected confirmation (for example a wrong own password) keeps the
            # still-valid preview visible so the actor can retry it.
            pending = permissions.pending_preview(request.user, request.POST.get('preview_id'))
            if pending is not None:
                extra['preview'] = permissions.preview_payload(pending)
                extra['commit_op'] = COMMIT_OPS.get(pending.kind, '')
    context = _users_context(request)
    context.update(extra)
    response = render(request, 'core/users.html', context, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@endpoint
def password_page(request):
    admin_host(request)
    if not request.user.is_authenticated:
        return redirect('/login')
    error = ''
    must_change = AccountProfile.objects.filter(user_id=request.user.pk, must_change_password=True).exists()
    if request.method == 'POST':
        try:
            profile = accounts.change_own_password(request.user, request.POST.get('current', ''), request.POST.get('new', ''), request.POST.get('confirm', ''))
        except Rejected as error:
            return render(request, 'core/password.html', {'error': message_for(error.code), 'must_change': must_change}, status=error.status)
        request.user.refresh_from_db()
        update_session_auth_hash(request, request.user)
        request.session['gep_auth_version'] = profile.auth_version
        return redirect('/')
    return render(request, 'core/password.html', {'error': error, 'must_change': must_change})


@endpoint
def activate_account_page(request):
    admin_host(request)
    if request.method == 'POST':
        check('account_activation:' + request.META.get('REMOTE_ADDR', ''), 5)
        try:
            user = accounts.activate_account(request.POST.get('token', ''), request.POST.get('password', ''), request.POST.get('confirm', ''))
        except Rejected as error:
            return render(request, 'core/activate_account.html', {'error': message_for(error.code), 'token': '', 'done': False}, status=error.status)
        return render(request, 'core/activate_account.html', {'error': '', 'token': '', 'done': True, 'username': user.username})
    return render(request, 'core/activate_account.html', {'error': '', 'token': request.GET.get('token', ''), 'done': False})
