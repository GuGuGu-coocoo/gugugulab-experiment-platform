import json
import mimetypes
import zipfile
from django.conf import settings
from django.http import HttpResponse
from .models import Release
from .gui import connection_config, public_api_url
from .protocol import require
from .views import endpoint
from . import publication

@endpoint
def resource(request,release_id,resource_path):
    require(request.get_host().split(':')[0]==settings.EXPERIMENT_HOST,'wrong_host',403)
    require(request.method=='GET','method',405)
    release=Release.objects.select_related('build','study').get(pk=release_id)
    # Only a real Web release is served here: a complete native release carries a
    # program archive without any web/ path and must never be presented as one.
    require(publication.release_kind(release)=='web','not_published',404)
    require(resource_path.startswith('web/') and '..' not in resource_path.split('/'),'path',404)
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
           and study.recruitment=='open' and release.approved and bool(release.build.package_path))
    if not valid:
        return False
    return {'expected_release_id':str(release.id),'expected_revision':int(raw_revision)}


def entry_stale_page(study_id):
    """Refusal page for a start click whose observed entry is no longer current."""
    body=('<!doctype html><html lang="zh"><meta charset="utf-8">'
          '<meta name="viewport" content="width=device-width,initial-scale=1">'
          '<title>研究入口已更新</title><body><main><h1>研究入口已更新</h1>'
          '<p>当前参与版本或招募状态已变化，本次没有开始新的参与会话。</p>'
          f'<p><a href="/join/{study_id}">返回研究入口，刷新后重新开始</a></p>'
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
    response['Content-Security-Policy']="default-src 'self' blob:; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
    response['Cross-Origin-Opener-Policy']='same-origin'
    return response

@endpoint
def preview(request,token,resource_path):
    from django.core import signing
    from django.contrib.auth import get_user_model
    from .models import Build,Instance
    from .access import guard
    require(request.get_host().split(':')[0]==settings.EXPERIMENT_HOST,'wrong_host',403)
    require(request.method=='GET' and resource_path.startswith('web/') and '..' not in resource_path.split('/'),'path',404)
    try: claims=signing.loads(token,salt='preview',max_age=900)
    except signing.BadSignature:
        from .protocol import Rejected
        raise Rejected('preview_expired',403)
    build=Build.objects.get(pk=claims['build']);user=get_user_model().objects.get(pk=claims['user'])
    guard(user,build.study,'build.preview')
    config={'config_version':'1','protocol_version':'gep/1','sdk_version':'0.1.0','purpose':'synthetic','api_url':public_api_url(),'instance_id':str(Instance.objects.get(pk=1).instance_id),'study_id':str(build.study_id),'release_id':'preview','build_id':str(build.id),'preview':True}
    return serve(build,config,resource_path)
