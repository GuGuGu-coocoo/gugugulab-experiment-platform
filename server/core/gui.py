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
from .models import Study, Grant, Instance, Participant, Build, Release, Session, Audit, Invitation, Export
from .access import guard, ACTIONS
from .protocol import require, parse, Rejected
from .views import endpoint
from .services import digest, completion_status
from .packages import validate_package, descriptor_valid, MAX_ARCHIVE


def admin_host(request):
    require(request.get_host().split(':')[0] in ('admin.localhost','localhost','testserver'),'wrong_host',403)


def connection_config(release):
    return {'config_version':'1','protocol_version':'gep/1','sdk_version':'0.1.0','api_url':os.environ.get('GEP_PUBLIC_API','http://experiment.localhost:8000'),'instance_id':str(Instance.objects.get(pk=1).instance_id),'study_id':str(release.study_id),'release_id':str(release.id),'build_id':str(release.build_id),'purpose':'synthetic'}


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
    return render(request,'core/home.html',{'studies':studies})


@endpoint
def signin(request):
    admin_host(request)
    message=''
    if request.method=='POST':
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
def study_page(request,study_id):
    admin_host(request)
    study=Study.objects.get(pk=study_id)
    guard(request.user,study,'study.view')
    notice=''
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
                rows=request.POST['roster'].splitlines();require(len(rows)<=1000,'roster_limit')
                seen=set()
                for row in rows:
                    parts=row.split('\t');code=parts[0]
                    require(0<len(code)<=128 and code not in seen and not Participant.objects.filter(study=study,code=code).exists(),'duplicate_or_invalid_code')
                    require(len(parts)==(2 if study.mode=='password' else 1),'roster_columns')
                    seen.add(code)
                    require(study.mode!='password' or len(parts[1])>=12,'password_too_short')
                    Participant.objects.create(study=study,code=code,password_hash=make_password(parts[1]) if len(parts)==2 else '')
            elif op=='native':
                guard(request.user,study,'build.upload')
                descriptor=parse(request.POST['descriptor'].encode());descriptor_valid(descriptor)
                require(descriptor['platform']=='macos_arm64','native_platform')
                build,_=Build.objects.get_or_create(study=study,digest=descriptor['program_sha256'],defaults={'descriptor':descriptor})
                require(build.descriptor==descriptor,'build_conflict',409)
            elif op=='upload':
                guard(request.user,study,'build.upload')
                upload=request.FILES['package'];require(upload.size<=MAX_ARCHIVE,'archive_limit',413)
                raw=upload.read(MAX_ARCHIVE+1);descriptor,sha=validate_package(raw)
                root=settings.DATA_DIR/'packages';root.mkdir(mode=0o700,exist_ok=True)
                path=root/(sha+'.zip')
                if not path.exists():
                    temp=root/(secrets.token_hex(16)+'.tmp')
                    with temp.open('xb') as stream:
                        stream.write(raw);stream.flush();os.fsync(stream.fileno())
                    temp.replace(path)
                build,_=Build.objects.get_or_create(study=study,digest=sha,defaults={'descriptor':descriptor,'package_path':path.name})
            elif op=='approve':
                guard(request.user,study,'release.approve_pilot')
                build=Build.objects.get(pk=request.POST['build_id'],study=study)
                Release.objects.create(study=study,build=build,approved=True,config={'purpose':'synthetic','mode':study.mode,'max_sessions':study.max_sessions,'offline_policy':'continue_local','recovery':'trial_boundary_v1'})
            elif op=='recruitment':
                guard(request.user,study,'recruitment.manage')
                state=request.POST['state'];require(state in ('open','paused','closed'),'state')
                study.recruitment=state;study.save()
            elif op=='invite':
                guard(request.user,study,'member.manage');guard(request.user,study,'permission.delegate')
                actions=request.POST.getlist('actions')
                require(bool(actions) and set(actions)<=ACTIONS,'actions')
                require(set(actions)<=set(Grant.objects.filter(user=request.user,study=study,delegable=True).values_list('action',flat=True)),'delegation_forbidden',403)
                username=request.POST['username'];require(not get_user_model().objects.filter(username=username).exists(),'account_exists',409)
                token=secrets.token_urlsafe(32)
                Invitation.objects.create(study=study,issuer=request.user,username=username,actions=actions,token_hash=digest(token),expires_at=timezone.now()+timedelta(hours=24))
                notice='邀请密钥（请通过可信渠道交付）：'+token
            elif op=='revoke_member':
                guard(request.user,study,'member.manage');guard(request.user,study,'permission.delegate')
                grants=Grant.objects.filter(study=study,user_id=request.POST['user_id'])
                mine=set(Grant.objects.filter(user=request.user,study=study,delegable=True).values_list('action',flat=True))
                require(set(grants.values_list('action',flat=True))<=mine,'higher_privilege_target',403)
                require(str(request.user.id)!=request.POST['user_id'],'self_revoke_use_other_owner',409)
                grants.delete()
            else:
                raise Rejected('unknown_operation')
            Audit.objects.create(study=study,actor=request.user,action=op,target=str(study.id))
        if not notice:return redirect('/studies/'+str(study.id))
    sessions=[{'id':s.id,'state':completion_status(s)['state']} for s in Session.objects.filter(release__study=study)]
    return render(request,'core/study.html',{'study':study,'builds':Build.objects.filter(study=study),'releases':Release.objects.filter(study=study),'sessions':sessions,'members':Grant.objects.filter(study=study,action='study.view').select_related('user'),'actions':sorted(ACTIONS),'notice':notice})

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
            password=request.POST['password'];require(len(password)>=16,'password_too_short')
            user=get_user_model().objects.create_user(invite.username,password=password)
            Grant.objects.bulk_create([Grant(study=invite.study,user=user,action=a) for a in invite.actions])
            invite.consumed=True;invite.save()
            Audit.objects.create(study=invite.study,actor=invite.issuer,action='invite.accepted',target=str(user.id))
        return redirect('/login')
    return render(request,'core/activate.html')
