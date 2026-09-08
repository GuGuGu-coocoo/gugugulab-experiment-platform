import json
from functools import wraps
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from .protocol import Rejected, require, parse
from .models import Release, Session, Study, Event, Export
from .services import admit, context, receive, finish, authorize_session, completion_status
from .access import guard


def endpoint(fn):
    @wraps(fn)
    def wrapped(request,*args,**kwargs):
        try:
            response=fn(request,*args,**kwargs)
        except Rejected as error:
            response=JsonResponse({'code':error.code,'retryable':error.status in (429,503),'outcome_unknown':False},status=error.status)
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
    require(request.get_host().split(':')[0] in ('experiment.localhost','127.0.0.1','testserver'), 'wrong_host',403)
    require(request.method == ('GET' if action in ('status','context') else 'POST'), 'method',405)
    if session_id is None:
        data=parse(request.body)
        release=Release.objects.select_related('study','build').get(pk=data['release_id'])
        session,token=admit(release,data)
        return JsonResponse(dict(context(session), token=token))
    if action=='event-batches':
        return JsonResponse(receive(session_id,bearer(request),parse(request.body)))
    if action=='completion':
        return JsonResponse(finish(session_id,bearer(request),parse(request.body)))
    session=Session.objects.select_related('release','participant').get(pk=session_id)
    authorize_session(session,bearer(request))
    require(action in ('status','context'),'unknown_action',404)
    return JsonResponse(context(session) if action=='context' else completion_status(session))


@endpoint
def exports(request, export_id=None):
    require(request.get_host().split(':')[0] in ('admin.localhost','localhost','testserver'), 'wrong_host',403)
    if export_id:
        require(request.method=='GET','method',405)
        item=Export.objects.get(pk=export_id)
        guard(request.user,item.study,'data.export_raw')
        response=HttpResponse(''.join(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n' for row in item.snapshot['records']),content_type='application/x-ndjson')
        response['Content-Disposition']=f'attachment; filename="{item.id}.jsonl"'
        return response
    require(request.method=='POST','method',405)
    data=parse(request.body)
    with transaction.atomic():
        study=Study.objects.get(pk=data['study_id'])
        guard(request.user,study,'data.export_raw')
        records=[]
        for event in Event.objects.filter(session__release__study=study).select_related('session__release__build').order_by('id'):
            release=event.session.release
            records.append({'study_id':str(study.id),'release_id':str(release.id),'build_id':str(release.build_id),'record':event.envelope})
        item=Export.objects.create(study=study,snapshot={'records':records})
    return JsonResponse({'export_id':str(item.id)},status=201)
