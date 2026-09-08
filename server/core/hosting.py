import json
import mimetypes
import zipfile
from django.conf import settings
from django.http import HttpResponse
from .models import Release
from .gui import connection_config
from .protocol import require
from .views import endpoint

@endpoint
def resource(request,release_id,resource_path):
    require(request.get_host().split(':')[0]==settings.EXPERIMENT_HOST,'wrong_host',403)
    require(request.method=='GET','method',405)
    release=Release.objects.select_related('build').get(pk=release_id)
    require(release.approved and bool(release.build.package_path),'not_published',404)
    require(resource_path.startswith('web/') and '..' not in resource_path.split('/'),'path',404)
    return serve(release.build,connection_config(release),resource_path)

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
    config={'config_version':'1','protocol_version':'gep/1','sdk_version':'0.1.0','purpose':'synthetic','api_url':'http://experiment.localhost:8000','instance_id':str(Instance.objects.get(pk=1).instance_id),'study_id':str(build.study_id),'release_id':'preview','build_id':str(build.id),'preview':True}
    return serve(build,config,resource_path)
