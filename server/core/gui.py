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
from urllib.parse import urlparse
from jsonschema.exceptions import SchemaError
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.http import JsonResponse, HttpResponse, FileResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from .models import Study, Grant, Instance, Participant, Build, Release, Session, Audit, Invitation, Export, RecoveryPermit, AccountProfile
from .access import guard, allowed, ACTIONS, authority_actions
from .protocol import require, parse, Rejected
from .views import endpoint
from .services import digest, completion_status, issue_recovery_code, SHELL_CAPABILITY
from .packages import validate_package, descriptor_valid, native_program_valid, MAX_ARCHIVE, MAX_NATIVE_ARCHIVE, NATIVE_PLATFORMS
from . import publication, artifacts, ui, workbench


def _revision_ok(raw, current):
    try:
        return int(raw) == current
    except (TypeError, ValueError):
        return False


def _bump(instance):
    instance.governance_revision += 1
    instance.save(update_fields=['governance_revision'])


def admin_host(request):
    require(request.get_host().split(':')[0] in (settings.ADMIN_HOST,'localhost','testserver'),'wrong_host',403)


def admin_origin(request):
    """The absolute admin origin for one-time links (never a request Host echo).

    An explicitly configured ``settings.ADMIN_ORIGIN`` wins: it must be a bare
    ``http``/``https`` origin whose hostname is the approved admin entry (or an
    explicit local compatibility entry), without credentials, path, query or
    fragment, and with a valid port (1..65535, or none for the scheme default).
    Without an explicit origin, the approved request hostname is combined with
    the server-owned ``SERVER_PORT`` the request really reached this server on:
    the port of the client-supplied ``Host`` header and ``X-Forwarded-*``
    headers are never trusted, and a reverse proxy that publishes a different
    external port must configure ``ADMIN_ORIGIN``. Any unusable configuration,
    including ``urlparse``/``parsed.port`` errors, fails closed with a
    controlled rejection instead of an attacker-shaped or broken link.
    """
    configured=(getattr(settings,'ADMIN_ORIGIN','') or '').strip()
    if configured:
        try:
            parsed=urlparse(configured)
        except ValueError:
            raise Rejected('admin_origin',409)
        require(parsed.scheme in ('http','https') and bool(parsed.netloc) and '@' not in parsed.netloc
                and not parsed.path and not parsed.query and not parsed.fragment
                and not parsed.netloc.endswith(':'),'admin_origin',409)
        try:
            authority=(parsed.hostname,parsed.port)
        except ValueError:
            raise Rejected('admin_origin',409)
        require(authority[0] in (settings.ADMIN_HOST,'localhost','testserver'),'admin_origin',409)
        require(authority[1] is None or 1 <= authority[1] <= 65535,'admin_origin',409)
        return configured.rstrip('/')
    host=request.get_host().split(':')[0]
    require(host in (settings.ADMIN_HOST,'localhost','testserver'),'wrong_host',403)
    try:
        port=int(request.get_port())
    except (KeyError, TypeError, ValueError):
        raise Rejected('admin_origin',409)
    require(1 <= port <= 65535,'admin_origin',409)
    return ('https' if request.is_secure() else 'http')+'://'+host+':'+str(port)


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
    'identity_mapping.read': '身份映射查看：可查看具体名单身份与会话对应关系（不包含答案）',
    'session.view': '会话查看：查看具体会话状态与对应名单',
}

ACTION_LABELS_EN = {
    'study.view': 'Study visibility: view the study overview',
    'study.configure': 'Configure study: change participation settings and roster',
    'build.upload': 'Upload build: register experiment resources',
    'build.preview': 'Preview build: open the isolated trial run',
    'release.approve_pilot': 'Approve synthetic release: allow this version for synthetic participation',
    'recruitment.manage': 'Recruitment: open, pause or close new participation',
    'data.export_raw': 'Raw data export: download raw study records; grant with care',
    'session.recover': 'Session recovery: issue same-device recovery permits',
    'member.manage': 'Member management: invite or revoke study members',
    'permission.delegate': 'Permission delegation: pass on delegable permissions; grant with care',
    'audit.view': 'Audit: view administrative action records',
    'identity_mapping.read': 'Identity mapping: view concrete roster identities and session links (no answers)',
    'session.view': 'Session view: view concrete session states and their roster links',
}


def action_label(action, lang='zh'):
    """Generic action label in the active language; the action code never changes."""
    labels = ACTION_LABELS_EN if lang == 'en' else ACTION_LABELS
    return labels.get(action, action)


def study_context(request, study, notice='', module='overview'):
    lang=ui.lang_of(request)
    permissions={action for action in ACTIONS if allowed(request.user,study,action)}
    can_manage={'member.manage','permission.delegate'} <= permissions
    releases=list(Release.objects.filter(study=study).select_related('build'))
    for release in releases:
        release.artifact_ready=bool(release.approved and release.artifact_digest and release.artifact_path)
        release.program_bound=bool(release.build.package_path)
        release.artifact_config_member=artifacts.config_member(release.build.descriptor)
        descriptor=release.build.descriptor if isinstance(release.build.descriptor,dict) else {}
        release.platform=descriptor.get('platform') or ''
        release.version=descriptor.get('version') or ''
    return {
        'study':study, 'notice':notice, 'public_api_url':public_api_url(),
        'native_platforms':NATIVE_PLATFORMS,
        'revision':Instance.objects.get(pk=1).governance_revision,
        'study_revision':study.revision,
        'current_release_id':study.current_release_id,
        'current_release':study.current_release,
        'builds':Build.objects.filter(study=study),
        'releases':releases,
        'members':Grant.objects.filter(study=study,action='study.view').exclude(user_id=Instance.objects.get(pk=1).owner_id).select_related('user') if can_manage else [],
        'invitations':Invitation.objects.filter(study=study,consumed=False,revoked=False) if can_manage else [],
        'actions':[{'code':a,'label':action_label(a,lang)} for a in sorted(permissions & set(Grant.objects.filter(user=request.user,study=study,delegable=True).values_list('action',flat=True)))],
        'can_configure':'study.configure' in permissions,
        'can_upload':'build.upload' in permissions,
        'can_preview':'build.preview' in permissions,
        'can_approve':'release.approve_pilot' in permissions,
        'can_recruit':'recruitment.manage' in permissions,
        'can_export':'data.export_raw' in permissions,
        'can_recover':'session.recover' in permissions,
        'can_manage':can_manage,
        'module':module,
        'module_template':'core/modules/%s.html' % module,
        'module_links':{name:workbench.module_url(study,name) for name in workbench.MODULES},
        'module_available':{name:workbench.module_allowed(request,study,name) for name in workbench.MODULES},
        'study_nav':workbench.nav_items(request,study),
        'module_url':workbench.module_url(study,module),
        'nav_current':'study',
        'server_timezone':workbench.timezone_label(),
        'lang':ui.lang_of(request),
    }


STUDY_MESSAGES = {
    'policy_frozen_after_release':'已有批准发行，参与政策已冻结。请创建新研究以使用不同政策。',
    'duplicate_or_invalid_code':'名单存在重复、已有或无效 ID；本次未导入任何行。',
    'roster_columns':'名单列数不正确；密码模式请填写 ID 与密码两列。本次未导入任何行。',
    'password_too_short':'密码长度不足，请检查后重新提交。',
    'forbidden':'当前账号没有此操作权限，未执行更改。',
    'revision_conflict':'研究发布版本已变化，请刷新页面后重试；未执行任何更改。',
    'no_change':'目标状态没有变化，未写入任何更改。',
    'policy_field':'公开信息超出长度上限，未写入任何更改。',
    'release_not_found':'所选发行不存在或不属于本研究，未执行任何更改。',
    'release_unapproved':'只能把已批准的发行设为当前发行。',
    'release_unavailable':'该发行没有可用的已发布资源（Web 包或平台完整原生包），不能作为当前发行。',
    # Upload / descriptor / native program rejection reasons (bilingual via
    # ui.ERRORS_EN). Every message states the reason and that nothing changed;
    # none of them echoes a file path or other server-side location.
    'archive_limit':'文件超过上传大小上限，已整体拒绝，未登记任何构建。',
    'file_count':'压缩包文件数量超出上限，已整体拒绝。',
    'unsafe_path':'压缩包内存在不安全的成员路径（绝对路径、越级目录、反斜杠或冒号），已整体拒绝。',
    'duplicate_path':'压缩包内存在重复成员路径（大小写或 Unicode 折叠后相同），已整体拒绝。',
    'non_regular_file':'压缩包包含链接或非普通文件，已整体拒绝。',
    'reserved_path':'压缩包成员位置不受支持（Web 包只允许 web/ 与 manifest.json）。',
    'expanded_limit':'压缩包展开后超过大小上限，已整体拒绝。',
    'compression_ratio':'压缩包压缩比异常，已整体拒绝。',
    'entry_missing':'Web 包缺少入口 web/index.html。',
    'program_digest_mismatch':'程序摘要与描述不一致，文件可能被修改，已整体拒绝。',
    'invalid_archive':'文件不是有效的 ZIP 压缩包，已整体拒绝。',
    'invalid_json':'描述或 manifest 不是有效 JSON。',
    'schema_object':'schema 必须是 JSON 对象。',
    'schema_definition':'schema 定义不合法：每项必须包含 id、version 与合法 schema。',
    'schema_references_unsupported':'schema 不接受 $ref/$dynamicRef/$id 引用。',
    'schema_limit':'schema 数量必须在 1–16 之间。',
    'schema_invalid':'schema 不是合法的 JSON Schema 定义。',
    'descriptor_fields':'构建描述字段不完整或包含未知字段，已整体拒绝。',
    'incompatible_build':'构建的协议、SDK 或引擎版本与本平台不兼容。',
    'unsupported_platform':'构建平台不在受支持平台列表内。',
    'build_version':'版本字符串不合法（1–64 字符）。',
    'program_digest':'程序摘要必须是 64 位小写十六进制。',
    'codebook_required':'构建描述必须包含非空 codebook。',
    'package_metadata':'程序包元数据（根目录、入口、依赖、扩展清单）不合法，已整体拒绝。',
    'native_platform':'该构建不是受支持的独立原生平台。',
    'version_content_conflict':'同一版本与平台已登记不同的程序摘要，本次未登记任何构建。',
    'build_conflict':'已存在同摘要但描述或程序包不同的不可变构建，本次未做更改。',
    'web_preview_only':'该构建没有可预览的程序包。',
    'native_program_missing':'该原生构建还没有完整程序包，不能批准发行。',
    'unsafe_script':'程序包包含可执行脚本，已整体拒绝。',
    'undeclared_executable':'程序包包含未声明的可执行映像，已整体拒绝。',
    'multiple_bundles':'程序包包含多个根目录或应用包，已整体拒绝。',
    'missing_binary':'macOS 包缺少 Contents/MacOS 下的可执行二进制。',
    'missing_info_plist':'macOS 包缺少 Info.plist。',
    'missing_pck':'程序包缺少 PCK 资源文件。',
    'missing_entry':'程序包缺少描述声明的入口文件。',
    'missing_dependencies':'程序包缺少描述声明的原生依赖。',
    'invalid_entry':'入口程序不是匹配架构的 x86-64 可执行文件。',
    'invalid_dependency':'声明的依赖不是匹配架构的 x86-64 DLL。',
    'invalid_image':'程序映像不是有效的 PE 文件。',
    'wrong_architecture':'程序映像不是 x86-64 架构，已整体拒绝。',
    'invalid_pck':'PCK 文件不合法，已整体拒绝。',
    'unsupported_pck':'PCK 格式不受支持，已整体拒绝。',
    'engine_version_mismatch':'PCK 记录的引擎版本与描述不符（本平台为 4.7.2）。',
    'invalid_extension':'GDExtension 清单不是有效的 UTF-8 文本清单。',
    'extension_configuration_missing':'GDExtension 清单缺少 [configuration] 段。',
    'extension_entry_symbol':'GDExtension 清单的 entry_symbol 不合法。',
    'extension_libraries_missing':'GDExtension 清单缺少 [libraries] 段。',
    'extension_library_missing':'GDExtension 清单缺少 windows.release.x86_64 的 res:// 引用。',
    'extension_dependency_missing':'GDExtension 清单引用的库不是描述声明的依赖。',
    'frozen_path_conflict':'程序包成员与冻结生成的配置或清单路径冲突，已整体拒绝。',
    'admin_origin':'管理地址配置无效，未生成任何链接。',
}


def study_error_message(request, code):
    """Chinese by default; known codes get their English translation."""
    fallback = STUDY_MESSAGES.get(code, ui.tr(ui.lang_of(request), 'error_invalid_request'))
    return ui.error_message(code, fallback, ui.lang_of(request))


def connection_config(release):
    """Frozen public configuration for one release.

    ``mode`` and ``shell_capability`` are copied from the release's own frozen
    config, never from the study's current policy, so a release approved before a
    policy change keeps admitting by the mode it was approved with. A legacy
    release without a frozen mode keeps the pre-shell contract: the keys are
    omitted and the shell falls back to the legacy field set. A frozen mode the
    shell cannot understand fails closed instead of being published. No roster,
    password or other credential ever enters this public document.
    """
    config={'config_version':'1','protocol_version':'gep/1','sdk_version':'0.1.0','api_url':public_api_url(),'instance_id':str(Instance.objects.get(pk=1).instance_id),'study_id':str(release.study_id),'release_id':str(release.id),'build_id':str(release.build_id),'purpose':'synthetic'}
    frozen=release.config if isinstance(release.config,dict) else {}
    mode=frozen.get('mode')
    if mode is not None and mode not in ('anonymous','id','password'):
        raise Rejected('unsupported_capability',409)
    if mode is not None:
        config['mode']=mode
        config['shell_capability']=SHELL_CAPABILITY
    return config


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
    lang=ui.lang_of(request)
    studies=Study.objects.filter(grant__user=request.user,grant__action='study.view').distinct().order_by('title','id')
    cards=[]
    for study in studies:
        cards.append({
            'study':study,
            'recruitment_label':ui.tr(lang,{'open':'home_recruit_open','paused':'home_recruit_paused','closed':'home_recruit_closed'}.get(study.recruitment,'home_recruit_paused')),
            'mode_label':ui.tr(lang,{'anonymous':'home_mode_anonymous','id':'home_mode_id','password':'home_mode_password'}.get(study.mode,'home_mode_anonymous')),
            'release_label':workbench.release_label(study.current_release) if study.current_release_id else ui.tr(lang,'home_card_none'),
            'has_current':study.current_release_id is not None,
            'sessions_total':Session.objects.filter(release__study=study).count(),
            'participants_total':Participant.objects.filter(study=study).count(),
        })
    return render(request,'core/home.html',{'cards':cards,'can_create':Instance.objects.filter(owner=request.user).exists(),'nav_current':'home'})


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
            profile=AccountProfile.objects.filter(user_id=user.pk).first()
            request.session['gep_auth_version']=profile.auth_version if profile is not None else None
            return redirect('/')
        message=ui.tr(ui.lang_of(request), 'login_failed')
    return render(request,'core/login.html',{'message':message})

@endpoint
def signout(request):
    admin_host(request)
    require(request.method=='POST','method',405)
    logout(request)
    return redirect('/login')


@endpoint
def study_page(request,study_id,module='overview'):
    admin_host(request)
    require(module in workbench.MODULES,'not_found',404)
    study=Study.objects.get(pk=study_id)
    guard(request.user,study,'study.view')
    lang=ui.lang_of(request)
    stored_notice=request.session.pop('roster_notice:'+str(study.id),'')
    if isinstance(stored_notice,dict):
        notice=stored_notice.get(lang) or stored_notice.get('zh','')
    else:
        notice=stored_notice
    try:
      require(workbench.module_allowed(request,study,module),'forbidden',403)
      if request.method=='POST':
        op=request.POST.get('op')
        audited=False
        target=''
        with transaction.atomic():
            instance=Instance.objects.select_for_update().get(pk=1)
            study=Study.objects.select_for_update().get(pk=study_id)
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
                request.session['roster_notice:'+str(study.id)]={
                    'zh':f'名单导入成功：新增 {len(rows)} 个 ID。',
                    'en':f'Roster imported: {len(rows)} new IDs.'}
            elif op=='native':
                guard(request.user,study,'build.upload')
                descriptor=parse(request.POST['descriptor'].encode());descriptor_valid(descriptor)
                require(descriptor['platform'] in NATIVE_PLATFORMS,'native_platform')
                require(not Build.objects.filter(study=study,descriptor__version=descriptor['version'],descriptor__platform=descriptor['platform']).exclude(digest=descriptor['program_sha256']).exists(),'version_content_conflict',409)
                build,_=Build.objects.get_or_create(study=study,digest=descriptor['program_sha256'],defaults={'descriptor':descriptor})
                require(build.descriptor==descriptor,'build_conflict',409)
            elif op=='native_archive':
                guard(request.user,study,'build.upload')
                build=Build.objects.get(pk=request.POST['build_id'],study=study)
                require(build.descriptor.get('platform') in NATIVE_PLATFORMS,'native_platform')
                upload=request.FILES['package'];require(upload.size<=MAX_NATIVE_ARCHIVE,'archive_limit',413)
                raw=upload.read(MAX_NATIVE_ARCHIVE+1)
                summary=native_program_valid(raw,build.descriptor)
                path=artifacts.programs_root()/(summary['digest']+'.zip')
                artifacts.store_program_archive(path,raw)
                if build.package_path!=path.name:
                    require(not build.package_path,'build_conflict',409)
                    build.package_path=path.name;build.save(update_fields=['package_path'])
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
                config={'purpose':'synthetic','mode':study.mode,'max_sessions':study.max_sessions,'offline_policy':'continue_local','recovery':'trial_boundary_v1'}
                if build.descriptor.get('platform') in NATIVE_PLATFORMS and build.package_path:
                    release=Release.objects.create(study=study,build=build,approved=False,
                                                   config={**config,'artifact_format_version':artifacts.ARTIFACT_FORMAT_VERSION})
                    artifacts.publish_complete_artifact(release,actor=request.user)
                    release.approved=True;release.save(update_fields=['approved'])
                    audited=True
                else:
                    release=Release.objects.create(study=study,build=build,approved=True,config=config)
                target=workbench.module_url(study,'builds')+'#release-'+str(release.id)
            elif op=='recruitment':
                guard(request.user,study,'recruitment.manage')
                state=request.POST['state'];require(state in ('open','paused','closed'),'state')
                study.recruitment=state;study.save()
            elif op=='publication':
                guard(request.user,study,'study.configure')
                publication.update_policy(request.user,study,request.POST.get('study_revision'),{
                    'public':request.POST.get('public')=='1',
                    'public_summary':request.POST.get('public_summary',''),
                    'public_duration':request.POST.get('public_duration',''),
                    'public_device_requirements':request.POST.get('public_device_requirements',''),
                    'show_closed_summary':request.POST.get('show_closed_summary')=='1'})
                audited=True
            elif op=='current_release':
                publication.select_current_release(request.user,study,request.POST.get('study_revision'),request.POST.get('release_id',''))
                audited=True
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
                notice=ui.notice(lang, '同设备恢复：会话 '+str(session.id)+'；15 分钟一次性许可：'+token,
                                 'Same-device recovery: session '+str(session.id)+'; one-time 15-minute permit: '+token)
            elif op=='recover_code':
                guard(request.user,study,'session.recover')
                session=Session.objects.get(pk=request.POST['session_id'],release__study=study)
                issued=issue_recovery_code(request.user,session.id)
                notice=ui.notice(lang, '同设备六位恢复码（5 分钟、最多 5 次尝试，仅本次有效）：'+issued['code'],
                                 'Same-device six-digit recovery code (5 minutes, at most 5 attempts, valid once): '+issued['code'])
            elif op=='invite':
                guard(request.user,study,'member.manage');guard(request.user,study,'permission.delegate')
                require(_revision_ok(request.POST.get('revision'),instance.governance_revision),'revision_conflict',409)
                actions=request.POST.getlist('actions')
                require(bool(actions) and set(actions)<=ACTIONS,'actions')
                require('study.view' in actions,'visibility_required',409)
                require(set(actions)<=authority_actions(request.user,study),'delegation_forbidden',403)
                username=request.POST['username'];require(0<len(username)<=150,'username')
                token=secrets.token_urlsafe(32)
                Invitation.objects.create(study=study,issuer=request.user,username=username,actions=actions,token_hash=digest(token),expires_at=timezone.now()+timedelta(hours=24))
                notice=ui.notice(lang, '邀请密钥（请通过可信渠道交付）：'+token,
                                 'Invitation key (deliver through a trusted channel): '+token)
                _bump(instance)
            elif op=='revoke_invite':
                guard(request.user,study,'member.manage');guard(request.user,study,'permission.delegate')
                require(_revision_ok(request.POST.get('revision'),instance.governance_revision),'revision_conflict',409)
                invitation=Invitation.objects.get(pk=request.POST['invitation_id'],study=study)
                require(set(invitation.actions)<=authority_actions(request.user,study),'delegation_forbidden',403)
                invitation.revoked=True;invitation.save(update_fields=['revoked'])
                _bump(instance)
            elif op=='revoke_member':
                guard(request.user,study,'member.manage');guard(request.user,study,'permission.delegate')
                require(_revision_ok(request.POST.get('revision'),instance.governance_revision),'revision_conflict',409)
                require(not Instance.objects.filter(owner_id=request.POST['user_id']).exists(),'owner_protected',403)
                grants=Grant.objects.filter(study=study,user_id=request.POST['user_id'])
                require(set(grants.values_list('action',flat=True))<=authority_actions(request.user,study),'higher_privilege_target',403)
                require(str(request.user.id)!=request.POST['user_id'],'self_revoke_use_other_owner',409)
                grants.delete()
                _bump(instance)
            else:
                raise Rejected('unknown_operation')
            if not audited:
                Audit.objects.create(study=study,actor=request.user,action=op,target=str(study.id))
      context=study_context(request,study,notice,module)
      context.update(workbench.module_context(request,study,module))
      if request.method=='POST' and not notice:
        return redirect(target or workbench.module_url(study,module))
      return render(request,'core/study.html',context)
    except (Rejected,ObjectDoesNotExist,ValueError,KeyError,TypeError,csv.Error,SchemaError) as error:
      code=(error.code if isinstance(error,Rejected)
            else 'schema_invalid' if isinstance(error,SchemaError)
            else 'invalid_request')
      status=error.status if isinstance(error,Rejected) else 400
      accept=request.headers.get('Accept','')
      if 'text/html' not in accept:
        if 'application/json' in accept:
          return JsonResponse({'code':code,'error':study_error_message(request,code)},status=status)
        raise
      context=study_context(request,study,notice,module)
      context.update(error=study_error_message(request,code),error_code=code,module_error=True)
      return render(request,'core/study.html',context,status=status)

@endpoint
def config(request,release_id):
    admin_host(request)
    release=Release.objects.get(pk=release_id)
    guard(request.user,release.study,'study.configure');require(release.approved,'not_approved',409)
    response=JsonResponse(connection_config(release))
    response['Content-Disposition']='attachment; filename="connection.json"'
    return response


def _artifact_release(request, release_id):
    admin_host(request)
    require(request.method=='GET','method',405)
    release=Release.objects.select_related('build','study').get(pk=release_id)
    guard(request.user,release.study,'build.upload')
    return release


@endpoint
def artifact(request,release_id):
    """Download one released complete package, byte-identical on every request.

    The build scope is re-checked on each request and the stored bytes are
    verified against the recorded digest before any of them are served, so a
    tampered or missing file is refused instead of distributed.
    """
    release=_artifact_release(request,release_id)
    path=artifacts.artifact_file(release)
    digest,size=artifacts.digest_file(path)
    require(digest==release.artifact_digest and size==release.artifact_size,'artifact_tampered',409)
    response=FileResponse(open(path,'rb'),as_attachment=True,filename=f'gep-{release.id}.zip',content_type='application/zip')
    response['X-Artifact-SHA256']=release.artifact_digest
    response['ETag']=f'"{release.artifact_digest}"'
    return response


@endpoint
def artifact_member(request,release_id,member):
    """One bounded sidecar member of a released artifact (never the program bundle)."""
    release=_artifact_release(request,release_id)
    entry,raw=artifacts.sidecar_bytes(release,member)
    content_type='application/json' if member.endswith('.json') else 'text/plain; charset=utf-8'
    response=HttpResponse(raw,content_type=content_type)
    response['X-Artifact-Member-SHA256']=entry['sha256']
    return response

@endpoint
def activate(request):
    admin_host(request)
    if request.method=='POST':
        with transaction.atomic():
            invite=Invitation.objects.get(token_hash=digest(request.POST['token']))
            require(not invite.consumed and not invite.revoked and invite.expires_at>timezone.now(),'invitation_inactive',403)
            guard(invite.issuer,invite.study,'member.manage');guard(invite.issuer,invite.study,'permission.delegate')
            require('study.view' in invite.actions,'delegation_changed',403)
            require(set(invite.actions)<=authority_actions(invite.issuer,invite.study),'delegation_changed',403)
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
