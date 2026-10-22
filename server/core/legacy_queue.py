"""Trusted compatibility surface for old shared-origin pending data.

No uploaded program runs here, no queues are moved, and there is no recovery or
new-admission action. The existing SDK may send already-persisted records only.
"""
from pathlib import Path

from django.conf import settings
from django.http import FileResponse
from django.shortcuts import render

from . import ui
from .models import Instance
from .origins import api_origin, known_host, portal_origin, release_url
from .protocol import require
from .views import endpoint


def _page(request, forward=''):
    language = ui.lang_of(request)
    english = language == 'en'
    labels = {
        'title': 'Previous local records' if english else '旧版本本机记录',
        'note': ('Saved records stay on this browser. Upload them here without running the old experiment. '
                 'This does not resume a task or move records to another release.' if english else
                 '已保存记录仍留在此浏览器，可在这里补传，不运行旧实验、不续接任务，也不把记录移到其他版本。'),
        'inspect': 'Check saved records' if english else '检查本机记录',
        'upload': 'Upload saved data' if english else '补传已保存数据',
        'continue': 'Continue to the isolated experiment' if english else '前往独立实验入口',
        'portal': 'Back to studies' if english else '返回研究门户',
        'empty': 'No previous local records were found.' if english else '未发现旧版本本机记录。',
        'found': ('Previous local records remain. Unfinished tasks cannot be resumed here; contact the researcher.'
                  if english else '检测到旧版本本机记录。未结束的任务不能在此续接，请联系研究者。'),
        'done': 'Upload round completed. The status below reflects local pending data.' if english else '本轮补传已完成；下方显示本机待传情况。',
        'waiting': 'Pending data remains. Retry later under the existing retry schedule; paused sessions need the researcher.'
                   if english else '仍有待传数据，请按原重试安排稍后重试；已暂停的会话需联系研究者。',
        'failed': 'Upload was not completed. Local data is retained; retry later or contact the researcher.'
                  if english else '补传未完成，本机数据保留。请稍后重试或联系研究者。',
        'busy': 'Another experiment tab is using local records. Close that tab first.'
                if english else '其他实验标签页正在使用本机记录，请先关闭该标签页。',
        'count': 'Saved sessions / pending events' if english else '已保存会话／待传记录',
    }
    config = {'api_url': api_origin(), 'instance_id': str(Instance.objects.get(pk=1).instance_id),
              'forward': forward, 'labels': labels}
    response = render(request, 'core/legacy_queue.html', {
        'copy': labels, 'queue_config': config, 'forward': forward, 'portal_url': portal_origin() + '/',
    })
    response['Cache-Control'] = 'no-store'
    # May differ for an explicitly configured loopback API address. The value
    # is validated deployment config, never a URL taken from local stored rows.
    response['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        f"connect-src 'self' {api_origin()}; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    return response


def legacy_entry(request, release):
    suffix = '?' + request.META['QUERY_STRING'] if request.META.get('QUERY_STRING') else ''
    return _page(request, release_url(release) + suffix)


@endpoint
def local_data(request):
    host = request.get_host().split(':')[0]
    require(request.method == 'GET' and (host == settings.EXPERIMENT_HOST or known_host(host)),
            'wrong_host', 403)
    return _page(request)


@endpoint
def sdk(request):
    host = request.get_host().split(':')[0]
    require(request.method == 'GET' and (host == settings.EXPERIMENT_HOST or known_host(host)),
            'wrong_host', 403)
    # An exact public source file, also explicitly copied into Docker. Not a
    # dynamic filesystem route and never a member of an uploaded archive.
    path = Path(settings.BASE_DIR) / 'GEC_SDK' / 'gec_web' / 'sdk.js'
    response = FileResponse(path.open('rb'), content_type='text/javascript')
    response['Cache-Control'] = 'no-store'
    response['Cross-Origin-Resource-Policy'] = 'same-origin'
    return response
