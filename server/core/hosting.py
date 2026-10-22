import json
import mimetypes
import zipfile
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect
from .models import Release
from .gui import connection_config, public_api_url
from .protocol import require
from .views import endpoint
from . import publication
from .origins import api_origin, isolated_host, isolated_origin, known_host, portal_origin, remember

@endpoint
def resource(request,release_id,resource_path):
    host=request.get_host().split(':')[0]
    require(host in (settings.EXPERIMENT_HOST,isolated_host('r',release_id)),'wrong_host',403)
    require(request.method=='GET','method',405)
    release=Release.objects.select_related('build','study').filter(pk=release_id).first()
    # Only a real Web release is served here: a complete native release carries a
    # program archive without any web/ path and must never be presented as one.
    # A deleted or random release id gets the same 404, and a study under
    # deletion is no longer a published resource.
    if release is None or publication.release_kind(release)!='web' or release.study.lifecycle!='active':
        if resource_path=='web/index.html' and known_host(host):
            from .legacy_queue import local_data
            response=local_data(request)
            response.status_code=404
            return response
        require(False,'not_published',404)
    require(resource_path.startswith('web/') and '..' not in resource_path.split('/'),'path',404)
    if resource_path=='web/index.html':
        remember('r',release.id)
    if host==settings.EXPERIMENT_HOST:
        # Never execute an uploaded program where old shared-origin queues live.
        # The trusted index interstitial can offer pending-data handling first.
        if resource_path=='web/index.html':
            from .legacy_queue import legacy_entry
            return legacy_entry(request,release)
        suffix='?'+request.META['QUERY_STRING'] if request.META.get('QUERY_STRING') else ''
        return redirect(f'{isolated_origin("r",release.id)}/run/{release.id}/{resource_path}'+suffix)
    config=connection_config(release)
    if resource_path=='web/index.html':
        binding=entry_binding(request,release)
        if binding is False:
            return entry_stale_page(release.study_id)
        if binding:
            config.update(binding)
    return serve(release.build,config,resource_path)


ENTRY_RELEASE_PARAM='entry_release'
ENTRY_REVISION_PARAM='entry_revision'


def entry_binding(request,release):
    """Resolve the stable-entry binding carried by a start click.

    ``None`` means the frozen legacy direct-release path: the request did not come
    from a stable study entry and keeps its original admission contract. A request
    that carries the observed release and publication revision is only served
    while that binding is still the study's current, open and resource-located
    release, so a page loaded before a researcher switch never starts the
    superseded materials. The validated binding is injected into the observed
    application context and checked again atomically at admission.
    """
    raw_release=request.GET.get(ENTRY_RELEASE_PARAM)
    raw_revision=request.GET.get(ENTRY_REVISION_PARAM)
    if raw_release is None and raw_revision is None:
        return None
    study=release.study
    valid=(raw_release==str(release.id) and raw_revision is not None and raw_revision.isdigit()
           and int(raw_revision)==study.revision and study.current_release_id==release.id
           and study.recruitment=='open' and study.lifecycle=='active' and release.approved and bool(release.build.package_path))
    if not valid:
        return False
    return {'expected_release_id':str(release.id),'expected_revision':int(raw_revision)}


def entry_stale_page(study_id):
    """Refusal page for a start click whose observed entry is no longer current."""
    body=('<!doctype html><html lang="zh"><meta charset="utf-8">'
          '<meta name="viewport" content="width=device-width,initial-scale=1">'
          '<title>研究入口已更新</title><body><main><h1>研究入口已更新</h1>'
          '<p>当前参与版本或招募状态已变化，本次没有开始新的参与会话。</p>'
          f'<p><a href="{portal_origin()}/join/{study_id}">返回研究入口，刷新后重新开始</a></p>'
          '</main></body></html>')
    response=HttpResponse(body,content_type='text/html; charset=utf-8',status=409)
    response['Cache-Control']='no-store'
    return response

def serve(build,config,resource_path):
    with zipfile.ZipFile(settings.DATA_DIR/'packages'/build.package_path) as archive:
        try:raw=archive.read(resource_path)
        except KeyError:return HttpResponse(status=404)
    if resource_path=='web/index.html':
        context=json.dumps(config).replace('<','\\u003c')
        raw=raw.replace(b'<head>',b'<head><script>globalThis.GEP_CONTEXT='+context.encode()+b';</script>',1)
    mime=mimetypes.guess_type(resource_path)[0] or 'application/octet-stream'
    response=HttpResponse(raw,content_type=mime)
    response['Content-Security-Policy']=("default-src 'self' blob:; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' "+api_origin()+"; img-src 'self' data: blob:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
    response['Cross-Origin-Opener-Policy']='same-origin'
    response['Cross-Origin-Resource-Policy']='same-origin'
    response['Origin-Agent-Cluster']='?1'
    return response

@endpoint
def preview(request,token,resource_path):
    from django.core import signing
    from django.contrib.auth import get_user_model
    from .models import Build,Instance
    from .access import guard
    from .services import SHELL_CAPABILITY
    host=request.get_host().split(':')[0]
    require(request.method=='GET' and resource_path.startswith('web/') and '..' not in resource_path.split('/'),'path',404)
    try: claims=signing.loads(token,salt='preview',max_age=900)
    except signing.BadSignature:
        from .protocol import Rejected
        raise Rejected('preview_expired',403)
    build=Build.objects.select_related('study').filter(pk=claims['build']).first()
    user=get_user_model().objects.filter(pk=claims['user']).first()
    # A deleted build, account or study is the same 404 as a random preview
    # target: the preview never leaks whether the study ever existed.
    from .protocol import require as _require
    _require(build is not None and user is not None and build.study.lifecycle=='active','not_published',404)
    require(host in (settings.EXPERIMENT_HOST,isolated_host('p',build.id)),'wrong_host',403)
    guard(user,build.study,'build.preview')
    require(build.descriptor.get('platform')=='godot_web' and bool(build.package_path),'web_preview_only',404)
    if resource_path=='web/index.html':
        remember('p',build.id)
    if host==settings.EXPERIMENT_HOST:
        suffix='?'+request.META['QUERY_STRING'] if request.META.get('QUERY_STRING') else ''
        return redirect(f'{isolated_origin("p",build.id)}/preview/{token}/{resource_path}'+suffix)
    config={'config_version':'1','protocol_version':'gep/1','sdk_version':'0.1.0','purpose':'synthetic','api_url':public_api_url(),'instance_id':str(Instance.objects.get(pk=1).instance_id),'study_id':str(build.study_id),'release_id':'preview','build_id':str(build.id),'preview':True,'mode':build.study.mode,'shell_capability':SHELL_CAPABILITY}
    return serve(build,config,resource_path)
