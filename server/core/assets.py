"""Fixed public asset route for the v2 accounts page.

The application ships a very small, fixed whitelist of browser assets. Serving
them through the URLconf keeps the path the running application really uses
identical to the path every test exercises (no test-only static handler and no
``DEBUG``-dependent behaviour), while a request for anything that is not an
exact whitelist key is a 404: there is no directory listing, no pattern match
that could escape the asset tree, and no path parameter is ever joined onto the
filesystem without the resolved-path containment check below.
"""
from pathlib import Path

from django.http import FileResponse, Http404
from django.views.decorators.http import require_http_methods

ASSET_ROOT = Path(__file__).resolve().parent / 'static'

# Exact URL suffix -> (file below ASSET_ROOT, response content type).
ASSETS = {
    'core/permissions.js': ('core/permissions.js', 'text/javascript'),
}


@require_http_methods(['GET', 'HEAD'])
def asset(request, asset_path):
    """Serve one whitelisted asset; anything else fails closed with 404."""
    entry = ASSETS.get(asset_path)
    if entry is None:
        raise Http404('asset_missing')
    relative, content_type = entry
    path = (ASSET_ROOT / relative).resolve()
    if ASSET_ROOT not in path.parents or not path.is_file():
        raise Http404('asset_missing')
    response = FileResponse(path.open('rb'), content_type=content_type)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-cache'
    # A marker so a test can prove this application route served the asset and
    # not a test wrapper or an unrelated static handler.
    response['X-GEP-Asset'] = asset_path
    return response
