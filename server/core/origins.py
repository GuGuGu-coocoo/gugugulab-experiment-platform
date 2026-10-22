"""Release/build browser origins; no DNS calls and no uploaded code on the portal.

The API keeps its frozen address for native clients and old pending queues.
Browser CORS is exact-origin and release-scoped, never a bearer substitute.
"""
import os
import re
import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.cache import patch_vary_headers

from .models import BrowserOrigin, Build, DeletedSession, Release
from .protocol import Rejected, require


def api_origin():
    value = os.environ.get('GEP_PUBLIC_API', 'http://experiment.localhost:8000').rstrip('/')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname in (settings.EXPERIMENT_HOST, '127.0.0.1')
                 and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment)
                 and (port is None or 1 <= port <= 65535))
    except ValueError:
        valid = False
    require(valid, 'origin_configuration', 409)
    authority = parsed.hostname
    if port is not None and port != (443 if parsed.scheme == 'https' else 80):
        authority += ':' + str(port)
    return f'{parsed.scheme}://{authority}'


def portal_origin():
    parsed = urlsplit(api_origin())
    return f'{parsed.scheme}://{settings.EXPERIMENT_HOST}' + (f':{parsed.port}' if parsed.port else '')


def isolated_host(kind, identifier):
    require(kind in ('r', 'p'), 'origin_configuration', 409)
    return f'{kind}-{uuid.UUID(str(identifier)).hex}.{settings.EXPERIMENT_HOST}'


def remember(kind, identifier):
    label = isolated_host(kind, identifier).split('.')[0]
    BrowserOrigin.objects.get_or_create(label=label)


def known_host(host):
    scope = match_host(host)
    return scope if scope and BrowserOrigin.objects.filter(label=host.split('.')[0]).exists() else None


def isolated_origin(kind, identifier):
    parsed = urlsplit(portal_origin())
    return f'{parsed.scheme}://{isolated_host(kind, identifier)}' + (f':{parsed.port}' if parsed.port else '')


def match_host(host):
    matched = re.fullmatch(r'([rp])-([0-9a-f]{32})\.' + re.escape(settings.EXPERIMENT_HOST), host)
    return (matched[1], uuid.UUID(hex=matched[2])) if matched else None


def release_url(release):
    return f'{isolated_origin("r", release.id)}/run/{release.id}/web/index.html'


def preview_url(build, token):
    remember('p', build.id)
    return f'{isolated_origin("p", build.id)}/preview/{token}/web/index.html'


def browser_release(request):
    """None for native/same-portal callers; otherwise the exact runtime release.

    Matching a domain suffix alone is not enough. Scheme, port, canonical name
    and a current Web release (or its registered, data-free retired namespace)
    have to match. The latter can only reach normal authenticated refusals or
    remaining session contracts, never restore deleted data. Previews get no CORS.
    """
    origin = request.headers.get('Origin')
    if origin is None:
        return None
    if origin in (api_origin(), portal_origin()):
        return None
    try:
        parsed = urlsplit(origin)
        scope = match_host(parsed.hostname or '')
    except ValueError:
        scope = None
    require(scope is not None and scope[0] == 'r', 'origin_denied', 403)
    require(origin == isolated_origin('r', scope[1]), 'origin_denied', 403)
    release = Release.objects.select_related('build', 'study').filter(pk=scope[1]).first()
    if release is not None and release.study.lifecycle == 'active':
        require(release.approved and release.build.descriptor.get('platform') == 'godot_web'
                and bool(release.build.package_path), 'origin_denied', 403)
        return release
    # An already registered origin can still receive the original permanent
    # deletion refusal. The label alone authorizes no data or program access.
    require(known_host(parsed.hostname), 'origin_denied', 403)
    return SimpleNamespace(id=scope[1], pk=scope[1], study_id=None)


def require_body_scope(request, data):
    release = getattr(request, 'gep_browser_release', None)
    if release is not None:
        requested = data.get('release_id', data.get('expected_release_id'))
        require(requested == str(release.id), 'origin_binding', 403)
        if release.study_id is not None:
            require(data.get('study_id') == str(release.study_id), 'origin_binding', 403)


def require_session_scope(request, session_id):
    release = getattr(request, 'gep_browser_release', None)
    if release is not None:
        from .models import Session
        require(Session.objects.filter(pk=session_id, release_id=release.pk).exists()
                or DeletedSession.objects.filter(pk=session_id, release_uuid=release.pk).exists(),
                'origin_binding', 403)


class ParticipantCORS:
    """Only the participant API opts into CORS, and never sends admin cookies."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/v1/participant/'):
            return self.get_response(request)
        origin = request.headers.get('Origin')
        cors_allowed = False
        try:
            require(request.get_host().split(':')[0] in (settings.EXPERIMENT_HOST, '127.0.0.1', 'testserver'),
                    'wrong_host', 403)
            request.gep_browser_release = browser_release(request)
            cors_allowed = origin is not None
            if request.method == 'OPTIONS':
                require(origin is not None, 'origin_denied', 403)
                require(request.headers.get('Access-Control-Request-Method') in ('GET', 'POST'), 'method', 405)
                headers = {part.strip().lower() for part in request.headers.get(
                    'Access-Control-Request-Headers', '').split(',') if part.strip()}
                require(headers <= {'authorization', 'content-type'}, 'cors_headers', 403)
                response = HttpResponse(status=204)
            else:
                response = self.get_response(request)
        except Rejected as error:
            response = JsonResponse({'code': error.code, 'retryable': False, 'outcome_unknown': False},
                                    status=error.status)
        if origin is not None:
            patch_vary_headers(response, ('Origin',))
            if cors_allowed:
                response['Access-Control-Allow-Origin'] = origin
                response['Access-Control-Allow-Methods'] = 'GET, POST'
                response['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
                response['Access-Control-Expose-Headers'] = 'Retry-After'
        response['Cache-Control'] = 'no-store'
        return response


def tls_allow(request):
    """Private Caddy ask hook: indexed allowlist, not arbitrary certificate minting.

    The public Caddy routes block /_internal/*, and the backend has no public
    port. No network/DNS call, secret or participant data is used by this hook.
    """
    allowed = False
    if request.method == 'GET' and request.get_host().split(':')[0] == 'gep':
        scope = match_host(request.GET.get('domain', ''))
        if scope:
            kind, identifier = scope
            if kind == 'r':
                allowed = Release.objects.filter(pk=identifier, approved=True, study__lifecycle='active',
                    build__descriptor__platform='godot_web').exclude(build__package_path='').exists()
            else:
                allowed = Build.objects.filter(pk=identifier, study__lifecycle='active',
                    descriptor__platform='godot_web').exclude(package_path='').exists()
            allowed = allowed or BrowserOrigin.objects.filter(label=f'{kind}-{identifier.hex}').exists()
    return HttpResponse(status=204 if allowed else 403)
