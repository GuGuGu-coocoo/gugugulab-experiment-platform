import csv
from functools import wraps
from django.core.exceptions import ObjectDoesNotExist
import hashlib
import io
import json
import os
import secrets
import zipfile
from datetime import timedelta
from pathlib import Path
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.http import JsonResponse, HttpResponse, FileResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from .models import Study, Grant, Instance, Participant, Build, Release, Session, Audit, Invitation, Export, RecoveryPermit
from .access import guard, allowed, ACTIONS
from .protocol import require, parse, Rejected
from .views import endpoint
from .services import digest, completion_status
from .packages import validate_package, descriptor_valid, MAX_ARCHIVE


def admin_host(request):
    require(request.get_host().split(':')[0] in ('admin.localhost','localhost','testserver'),'wrong_host',403)


def public_api_url():
    return os.environ.get('GEP_PUBLIC_API','http://experiment.localhost:8000').rstrip('/')


ACTION_LABELS = {
    'study.view': '研究可见：查看研究概况',
    'study.configure': '配置研究：修改参与设置及名单',
    'build.upload': '上传构建：登记实验资源',
    'build.preview': '预览构建：打开隔离试运行',
    'release.approve_pilot': '批准合成发行：允许该版本用于合成参与',
    'recruitment.manage': '招募管理：开放、暂停或关闭新参与',
    'data.export_raw': '原始数据导出：可下载研究原始记录，请谨慎授予',
    'session.recover': '会话恢复：签发原设备恢复许可',
    'member.manage': '成员管理：邀请或撤销研究成员',
    'permission.delegate': '权限委派：可转授已获准委派的权限，请谨慎授予',
    'audit.view': '审计查看：查看管理操作记录',
}


def study_context(request, study, notice=''):
    permissions={action for action in ACTIONS if allowed(request.user,study,action)}
    can_manage={'member.manage','permission.delegate'} <= permissions
    return {
        'study':study, 'notice':notice, 'public_api_url':public_api_url(),
        'builds':Build.objects.filter(study=study),
        'releases':Release.objects.filter(study=study).select_related('build'),
        'sessions':[{'id':s.id,'state':completion_status(s)['state']} for s in Session.objects.filter(release__study=study)],
        'members':Grant.objects.filter(study=study,action='study.view').exclude(user_id=Instance.objects.get(pk=1).owner_id).select_related('user') if can_manage else [],
        'invitations':Invitation.objects.filter(study=study,consumed=False,revoked=False) if can_manage else [],
        'actions':[{'code':a,'label':ACTION_LABELS[a]} for a in sorted(permissions & set(Grant.objects.filter(user=request.user,study=study,delegable=True).values_list('action',flat=True)))],
        'can_configure':'study.configure' in permissions,
        'can_upload':'build.upload' in permissions,
        'can_preview':'build.preview' in permissions,
        'can_approve':'release.approve_pilot' in permissions,
        'can_recruit':'recruitment.manage' in permissions,
        'can_export':'data.export_raw' in permissions,
        'can_recover':'session.recover' in permissions,
        'can_manage':can_manage,
    }


def study_form_errors(fn):
    @wraps(fn)
    def wrapped(request, study_id):
        try:
            return fn(request, study_id)
        except (Rejected, ObjectDoesNotExist, ValueError, KeyError, TypeError, csv.Error) as error:
            if request.method!='POST' or 'text/html' not in request.headers.get('Accept',''):
                raise
            admin_host(request)
            study=Study.objects.get(pk=study_id)
            guard(request.user,study,'study.view')
            context=study_context(request,study)
            code=error.code if isinstance(error,Rejected) else 'invalid_request'
            messages={
                'policy_frozen_after_release':'已有批准发行，参与政策已冻结。请创建新研究以使用不同政策。',
                'duplicate_or_invalid_code':'名单存在重复、已有或无效 ID；本次未导入任何行。',
                'roster_columns':'名单列数不正确；密码模式请填写 ID 与密码两列。本次未导入任何行。',
                'password_too_short':'密码长度不足，请检查后重新提交。',
                'forbidden':'当前账号没有此操作权限，未执行更改。',
            }
            context.update(error=messages.get(code,'操作未完成，请检查输入或权限后重试。'),error_code=code)
            return render(request,'core/study.html',context,status=error.status if isinstance(error,Rejected) else 400)
    return wrapped


def connection_config(release):
    return {'config_version':'1','protocol_version':'gep/1','sdk_version':'0.1.0','api_url':public_api_url(),'instance_id':str(Instance.objects.get(pk=1).instance_id),'study_id':str(release.study_id),'release_id':str(release.id),'build_id':str(release.build_id),'purpose':'synthetic'}


@endpoint
def home(request):
    admin_host(request)
    if not request.user.is_authenticated:
        return redirect('/login')
    if request.method=='POST':
        require(Instance.objects.filter(owner=request.user).exists(),'create_forbidden',403)
        title=request.POST.get('title','').strip()
        require(0<len(title)<=160,'title')
        with transaction.atomic():
            study=Study.objects.create(title=title)
            Grant.objects.bulk_create([Grant(user=request.user,study=study,action=action,delegable=True) for action in ACTIONS])
        return redirect('/studies/'+str(study.id))
    studies=Study.objects.filter(grant__user=request.user,grant__action='study.view').distinct()
    return render(request,'core/home.html',{'studies':studies,'can_create':Instance.objects.filter(owner=request.user).exists()})


@endpoint
def signin(request):
    admin_host(request)
    message=''
    if request.method=='POST':
        from .throttle import check
        check('login:'+request.META.get('REMOTE_ADDR','')+':'+request.POST.get('username',''))
        user=authenticate(request,username=request.POST.get('username'),password=request.POST.get('password'))
        if user:
            login(request,user)
            return redirect('/')
        message='登录失败，请核对凭据。'
    return render(request,'core/login.html',{'message':message})

@endpoint
def signout(request):
    admin_host(request)
    require(request.method=='POST','method',405)
    logout(request)
    return redirect('/login')


@endpoint
@study_form_errors
def study_page(request,study_id):
    admin_host(request)
    study=Study.objects.get(pk=study_id)
    guard(request.user,study,'study.view')
    notice=request.session.pop('roster_notice:'+str(study.id),'')
    if request.method=='POST':
        op=request.POST.get('op')
        with transaction.atomic():
            study=Study.objects.get(pk=study_id)
            if op=='configure':
                guard(request.user,study,'study.configure')
                mode=request.POST['mode']; require(mode in ('anonymous','id','password'),'mode')
                require(not Release.objects.filter(study=study,approved=True).exists(),'policy_frozen_after_release',409)
                limit=int(request.POST['max_sessions']);require(1<=limit<=100,'participation_limit')
                study.mode=mode;study.max_sessions=limit;study.save()
            elif op=='roster':
                guard(request.user,study,'study.configure')
                raw=request.POST['roster'];require(len(raw)<=256000,'roster_limit',413)
                delimiter='\t' if request.POST.get('roster_format','legacy_tab')=='legacy_tab' else ','
                try:
                    rows=list(csv.reader(io.StringIO(raw,newline=''),delimiter=delimiter,strict=True))
                except csv.Error:
                    raise Rejected('roster_columns')
                require(0<len(rows)<=1000,'roster_limit')
                seen=set()
                for row in rows:
                    parts=row;require(bool(parts),'roster_columns');code=parts[0]
                    require(0<len(code)<=128 and code not in seen and not Participant.objects.filter(study=study,code=code).exists(),'duplicate_or_invalid_code')
                    require(len(parts)==(2 if study.mode=='password' else 1),'roster_columns')
                    seen.add(code)
                    require(study.mode!='password' or len(parts[1])>=12,'password_too_short')
                    Participant.objects.create(study=study,code=code,password_hash=make_password(parts[1]) if len(parts)==2 else '')
                request.session['roster_notice:'+str(study.id)]=f'名单导入成功：新增 {len(rows)} 个 ID。'
            elif op=='native':
                guard(request.user,study,'build.upload')
                descriptor=parse(request.POST['descriptor'].encode());descriptor_valid(descriptor)
                require(descriptor['platform']=='macos_arm64','native_platform')
                require(not Build.objects.filter(study=study,descriptor__version=descriptor['version'],descriptor__platform=descriptor['platform']).exclude(digest=descriptor['program_sha256']).exists(),'version_content_conflict',409)
                build,_=Build.objects.get_or_create(study=study,digest=descriptor['program_sha256'],defaults={'descriptor':descriptor})
                require(build.descriptor==descriptor,'build_conflict',409)
            elif op=='upload':
                guard(request.user,study,'build.upload')
                upload=request.FILES['package'];require(upload.size<=MAX_ARCHIVE,'archive_limit',413)
                raw=upload.read(MAX_ARCHIVE+1);descriptor,sha=validate_package(raw)
                require(not Build.objects.filter(study=study,descriptor__version=descriptor['version'],descriptor__platform=descriptor['platform']).exclude(digest=sha).exists(),'version_content_conflict',409)
                root=settings.DATA_DIR/'packages';root.mkdir(mode=0o700,exist_ok=True)
                path=root/(sha+'.zip')
                if not path.exists():
                    temp=root/(secrets.token_hex(16)+'.tmp')
                    with temp.open('xb') as stream:
                        stream.write(raw);stream.flush();os.fsync(stream.fileno())
                    temp.replace(path)
                build,_=Build.objects.get_or_create(study=study,digest=sha,defaults={'descriptor':descriptor,'package_path':path.name})
            elif op=='preview':
                guard(request.user,study,'build.preview')
                build=Build.objects.get(pk=request.POST['build_id'],study=study)
                require(bool(build.package_path),'web_preview_only')
                from django.core import signing
                token=signing.dumps({'build':str(build.id),'user':request.user.id},salt='preview')
                return redirect(public_api_url()+'/preview/'+token+'/web/index.html')
            elif op=='approve':
                guard(request.user,study,'release.approve_pilot')
                build=Build.objects.get(pk=request.POST['build_id'],study=study)
                Release.objects.create(study=study,build=build,approved=True,config={'purpose':'synthetic','mode':study.mode,'max_sessions':study.max_sessions,'offline_policy':'continue_local','recovery':'trial_boundary_v1'})
            elif op=='recruitment':
                guard(request.user,study,'recruitment.manage')
                state=request.POST['state'];require(state in ('open','paused','closed'),'state')
                study.recruitment=state;study.save()
            elif op=='revoke_session':
                guard(request.user,study,'study.configure')
                session=Session.objects.get(pk=request.POST['session_id'],release__study=study)
                session.revoked=True;session.save(update_fields=['revoked'])
            elif op=='recover':
                guard(request.user,study,'session.recover')
                session=Session.objects.get(pk=request.POST['session_id'],release__study=study)
                require(not session.revoked,'not_recoverable',409)
                token=secrets.token_urlsafe(32)
                RecoveryPermit.objects.create(session=session,issuer=request.user,token_hash=digest(token),expires_at=timezone.now()+timedelta(minutes=15))
                notice='同设备恢复：会话 '+str(session.id)+'；15 分钟一次性许可：'+token
            elif op=='invite':
                guard(request.user,study,'member.manage');guard(request.user,study,'permission.delegate')
                actions=request.POST.getlist('actions')
                require(bool(actions) and set(actions)<=ACTIONS,'actions')
                require(set(actions)<=set(Grant.objects.filter(user=request.user,study=study,delegable=True).values_list('action',flat=True)),'delegation_forbidden',403)
                username=request.POST['username'];require(0<len(username)<=150,'username')
                token=secrets.token_urlsafe(32)
                Invitation.objects.create(study=study,issuer=request.user,username=username,actions=actions,token_hash=digest(token),expires_at=timezone.now()+timedelta(hours=24))
                notice='邀请密钥（请通过可信渠道交付）：'+token
            elif op=='revoke_invite':
                guard(request.user,study,'member.manage');guard(request.user,study,'permission.delegate')
                invitation=Invitation.objects.get(pk=request.POST['invitation_id'],study=study)
                require(set(invitation.actions)<=set(Grant.objects.filter(user=request.user,study=study,delegable=True).values_list('action',flat=True)),'delegation_forbidden',403)
                invitation.revoked=True;invitation.save(update_fields=['revoked'])
            elif op=='revoke_member':
                guard(request.user,study,'member.manage');guard(request.user,study,'permission.delegate')
                require(not Instance.objects.filter(owner_id=request.POST['user_id']).exists(),'owner_protected',403)
                grants=Grant.objects.filter(study=study,user_id=request.POST['user_id'])
                mine=set(Grant.objects.filter(user=request.user,study=study,delegable=True).values_list('action',flat=True))
                require(set(grants.values_list('action',flat=True))<=mine,'higher_privilege_target',403)
                require(str(request.user.id)!=request.POST['user_id'],'self_revoke_use_other_owner',409)
                grants.delete()
            else:
                raise Rejected('unknown_operation')
            Audit.objects.create(study=study,actor=request.user,action=op,target=str(study.id))
        if not notice:return redirect('/studies/'+str(study.id))
    return render(request,'core/study.html',study_context(request,study,notice))

@endpoint
def config(request,release_id):
    admin_host(request)
    release=Release.objects.get(pk=release_id)
    guard(request.user,release.study,'study.configure');require(release.approved,'not_approved',409)
    response=JsonResponse(connection_config(release))
    response['Content-Disposition']='attachment; filename="connection.json"'
    return response

@endpoint
def activate(request):
    admin_host(request)
    if request.method=='POST':
        with transaction.atomic():
            invite=Invitation.objects.get(token_hash=digest(request.POST['token']))
            require(not invite.consumed and not invite.revoked and invite.expires_at>timezone.now(),'invitation_inactive',403)
            guard(invite.issuer,invite.study,'member.manage');guard(invite.issuer,invite.study,'permission.delegate')
            require(set(invite.actions)<=set(Grant.objects.filter(user=invite.issuer,study=invite.study,delegable=True).values_list('action',flat=True)),'delegation_changed',403)
            user=get_user_model().objects.filter(username=invite.username).first()
            if user:
                require(request.user.is_authenticated and request.user.pk==user.pk and user.is_active,'existing_account_login_required',403)
            else:
                password=request.POST.get('password','');require(len(password)>=16,'password_too_short')
                user=get_user_model().objects.create_user(invite.username,password=password)
            for action in invite.actions:
                Grant.objects.get_or_create(study=invite.study,user=user,action=action)
            invite.consumed=True;invite.save()
            Audit.objects.create(study=invite.study,actor=invite.issuer,action='invite.accepted',target=str(user.id))
        return redirect('/login')
    return render(request,'core/activate.html')
