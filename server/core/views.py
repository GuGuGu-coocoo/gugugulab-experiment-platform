from functools import wraps
from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ObjectDoesNotExist
from .protocol import Rejected, require, parse, MAX_EXPORT_ARRAY, MAX_EXPORT_BYTES
from .models import Session, Export
from .services import (admit_request, context, receive, finish, authorize_session, completion_status, recover,
                       redeem_recovery_code, recover_named, RECOVERY_CODE_CAPABILITY, RECOVERY_NAMED_CAPABILITY)
from .access import guard
from . import exports as export_core
from . import deletion


def endpoint(fn):
    @wraps(fn)
    def wrapped(request,*args,**kwargs):
        try:
            response=fn(request,*args,**kwargs)
        except Rejected as error:
            body={'code':error.code,'retryable':error.status in (429,503),'outcome_unknown':False}
            detail=getattr(error,'detail',None)
            if isinstance(detail,dict):
                body.update(detail)
            response=JsonResponse(body,status=error.status)
        except (ObjectDoesNotExist, ValueError, KeyError, TypeError):
            response=JsonResponse({'code':'invalid_request','retryable':False,'outcome_unknown':False},status=400)
        response['Cache-Control']='no-store'
        response['X-Content-Type-Options']='nosniff'
        return response
    return wrapped


def bearer(request):
    auth=request.headers.get('Authorization','')
    return auth[7:] if auth.startswith('Bearer ') else ''


@csrf_exempt
@endpoint
def participant(request, session_id=None, action=None):
    require(request.get_host().split(':')[0] in (settings.EXPERIMENT_HOST,'127.0.0.1','testserver'), 'wrong_host',403)
    require(request.method == ('GET' if action in ('status','context') else 'POST'), 'method',405)
    if session_id is None:
        data=parse(request.body)
        from .throttle import check
        check('admit:'+str(data.get('study_id'))+':'+str(data.get('participant_code',data.get('operation_id'))))
        session,token=admit_request(data)
        return JsonResponse(dict(context(session), token=token))
    if action=='recover':
        data=parse(request.body)
        return JsonResponse(recover(session_id,data['proof'],data['permit']))
    if action=='event-batches':
        return JsonResponse(receive(session_id,bearer(request),parse(request.body)))
    if action=='completion':
        return JsonResponse(finish(session_id,bearer(request),parse(request.body)))
    session=Session.objects.select_related('release','participant').filter(pk=session_id).first()
    if session is None:
        deletion.session_refusal(session_id,bearer(request))
    if not deletion.active_study(session.release.study):
        deletion.session_refusal(session_id,bearer(request))
    authorize_session(session,bearer(request))
    require(action in ('status','context'),'unknown_action',404)
    return JsonResponse(context(session) if action=='context' else completion_status(session))


@csrf_exempt
@endpoint
def recovery(request):
    """Versioned same-device recovery: six-digit code or named credentials.

    The adapter submits one explicit capability; an unknown or missing
    capability fails closed instead of being routed to a guessed protocol.
    """
    require(request.get_host().split(':')[0] in (settings.EXPERIMENT_HOST,'127.0.0.1','testserver'), 'wrong_host',403)
    require(request.method=='POST','method',405)
    data=parse(request.body)
    client=request.META.get('REMOTE_ADDR','')
    capability=data.get('capability')
    if capability==RECOVERY_CODE_CAPABILITY:
        return JsonResponse(redeem_recovery_code(data,client))
    if capability==RECOVERY_NAMED_CAPABILITY:
        return JsonResponse(recover_named(data,client))
    raise Rejected('unsupported_capability',409)


@endpoint
def exports(request, export_id=None):
    # Same source of truth as gui.admin_host: the configured admin origin plus the
    # explicit local compatibility hosts, never every ALLOWED_HOSTS entry.
    require(request.get_host().split(':')[0] in (settings.ADMIN_HOST,'localhost','testserver'), 'wrong_host',403)
    if export_id:
        require(request.method=='GET','method',405)
        item=Export.objects.select_related('study').filter(pk=export_id).first()
        # A deleted study's export object is gone for everyone, including an
        # actor who once had authority: the same 404 as a random export UUID.
        if item is None or not deletion.active_study(item.study):
            deletion.export_unavailable()
        # Every format re-checks the permission set frozen with the export; a
        # revoked grant refuses the whole export instead of degrading it.
        for action in export_core.required_actions(item):
            guard(request.user,item.study,action)
        fmt=request.GET.get('format','jsonl')
        require(fmt in ('jsonl','csv','metadata','zip'),'export_format')
        response=export_core.render_download(item,fmt)
        # The download file name is always the immutable export UUID plus the
        # fixed extension; no title or other request value can reach it.
        extension='metadata.json' if fmt=='metadata' else fmt
        response['Content-Disposition']=f'attachment; filename="{item.id}.{extension}"'
        return response
    require(request.method=='POST','method',405)
    # The export application parses under its own bounded envelope; every other
    # route keeps the unchanged participant protocol bounds.
    data=parse(request.body,max_bytes=MAX_EXPORT_BYTES,max_array=MAX_EXPORT_ARRAY)
    # New applications name their version explicitly; a missing field keeps the
    # unchanged v1 contract for old clients, and anything else fails closed.
    version=data.get('format_version','1')
    require(version in ('1','2'),'export_format_version',400)
    if version=='1':
        item=export_core.create_legacy_export(request.user,data)
        return JsonResponse({'export_id':str(item.id)},status=201)
    view=data.get('view');language=data.get('language')
    require(view in export_core.VIEWS,'export_view',400)
    require(language in export_core.LANGUAGES,'export_language',400)
    item=export_core.create_v2_export(request.user,data['study_id'],view=view,language=language,
                                      session_ids=data.get('session_ids'))
    return JsonResponse({'export_id':str(item.id),'format_version':'2','view':view,'language':language,
                         'counts':item.snapshot['counts']},status=201)
