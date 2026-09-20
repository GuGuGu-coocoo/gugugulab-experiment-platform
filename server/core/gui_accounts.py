"""Instance account pages: /users, /account/password, /activate-account.

Server-side authorization lives in core.accounts and core.access; these views
only resolve inputs, render errors and show one-time secrets (never stored).
"""
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect, render
from django.utils import timezone

from . import accounts
from .access import conflicts, is_account_administrator, is_instance_owner
from .gui import admin_host
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
    'higher_privilege_target': '目标账号拥有操作者无法支配的研究权限（含不可委派权限），不能重置密码、停用或启用。',
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
}


def message_for(code):
    return MESSAGES.get(code, '操作未完成，请检查输入或权限后重试。')


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
    if op == 'reconcile':
        resolved = accounts.reconcile_conflicts(request.user, password, revision, request.POST.get('choice', ''))
        return {'notice': f"已完成 {len(resolved)} 组授权矛盾收敛。"}
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
    return {'rows': rows, 'owner_username': Instance.objects.get(pk=1).owner.username, 'is_owner': is_instance_owner(request.user),
            'invitations': AccountInvitation.objects.filter(consumed=False, revoked=False, expires_at__gt=timezone.now()).order_by('username'),
            'conflicts': conflict_rows, 'revision': accounts.instance_revision()}


@endpoint
def users_page(request):
    admin_host(request)
    if not request.user.is_authenticated:
        return redirect('/login')
    require(is_account_administrator(request.user), 'forbidden', 403)
    extra = {'notice': '', 'error': '', 'secret': None, 'invitation_token': None, 'secret_username': '', 'invitation_username': ''}
    status = 200
    if request.method == 'POST':
        try:
            extra.update(_apply(request))
        except (Rejected, ObjectDoesNotExist, ValueError) as error:
            code = error.code if isinstance(error, Rejected) else 'invalid_request'
            extra['error'] = message_for(code)
            status = error.status if isinstance(error, Rejected) else 400
    context = _users_context(request)
    context.update(extra)
    return render(request, 'core/users.html', context, status=status)


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
