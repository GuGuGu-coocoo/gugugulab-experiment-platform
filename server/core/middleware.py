"""Response security headers and server-side account gate.

Runs after AuthenticationMiddleware on every protected endpoint: a disabled
account, a session whose auth_version is stale, or an account that must change
its password is stopped here, not only in the UI.
"""
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from secrets import token_urlsafe

from .models import AccountProfile

OPEN_PATHS = ('/account/password', '/logout')


class ResponseSecurity:
    """Protect application HTML and early refusals without changing Godot's CSP.

    This outer middleware performs no database or file access. Published Web
    packages retain their own wasm-compatible policy; application templates use
    a per-response nonce instead of allowing arbitrary inline JavaScript.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        package_or_api = request.path.startswith(('/run/', '/preview/', '/v1/', '/static/'))
        request.csp_nonce = '' if package_or_api else token_urlsafe(18)
        response = self.get_response(request)
        response['X-Frame-Options'] = 'DENY'
        # Keep same-origin POST provenance for Django's CSRF checks. A blanket
        # no-referrer policy can make browsers submit Origin: null.
        response['Referrer-Policy'] = 'same-origin'
        response.setdefault('X-Content-Type-Options', 'nosniff')
        if response.status_code >= 400:
            response['Cache-Control'] = 'no-store'
        if response.get('Content-Type', '').startswith('text/html') and 'Content-Security-Policy' not in response:
            scripts = "'self'" + (f" 'nonce-{request.csp_nonce}'" if request.csp_nonce else '')
            response['Content-Security-Policy'] = (
                f"default-src 'self'; script-src {scripts}; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; "
                "form-action 'self'; frame-ancestors 'none'")
        return response


class AccountGate:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            if request.path not in OPEN_PATHS and not request.path.startswith('/static/'):
                if not user.is_active:
                    return self._deny(request, 'account_inactive', logout_first=True, redirect_to='/login')
                profile = AccountProfile.objects.filter(user_id=user.pk).first()
                if profile is not None:
                    if request.session.get('gep_auth_version') != profile.auth_version:
                        return self._deny(request, 'session_stale', logout_first=True, redirect_to='/login')
                    if profile.must_change_password:
                        return self._deny(request, 'password_change_required', redirect_to='/account/password')
        return self.get_response(request)

    def _deny(self, request, code, logout_first=False, redirect_to=None):
        if logout_first:
            logout(request)
        if redirect_to is None or request.path.startswith('/v1/') or 'application/json' in request.headers.get('Accept', ''):
            return JsonResponse({'code': code, 'retryable': False, 'outcome_unknown': False}, status=403)
        response = redirect(redirect_to)
        response['Cache-Control'] = 'no-store'
        return response
