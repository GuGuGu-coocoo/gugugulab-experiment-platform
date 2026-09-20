"""Server-side account gate.

Runs after AuthenticationMiddleware on every protected endpoint: a disabled
account, a session whose auth_version is stale, or an account that must change
its password is stopped here, not only in the UI.
"""
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect

from .models import AccountProfile

OPEN_PATHS = ('/account/password', '/logout')


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
