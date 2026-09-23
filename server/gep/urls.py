from django.urls import path
from core import views
urlpatterns = [
    path('v1/participant/sessions',views.participant),
    path('v1/participant/sessions/<uuid:session_id>/<str:action>',views.participant),
    path('v1/participant/recovery',views.recovery),
    path('v1/admin/exports',views.exports),
    path('v1/admin/exports/<uuid:export_id>/download',views.exports),
]
from core import gui
from core import portal
from core import ui as gep_ui
urlpatterns += [
    path('', portal.site_root), path('login',gui.signin), path('logout',gui.signout), path('activate',gui.activate),
    path('prefs',gep_ui.preferences),
    path('studies/<uuid:study_id>',gui.study_page), path('studies/<uuid:study_id>/<slug:module>',gui.study_page),
    path('releases/<uuid:release_id>/config',gui.config),
    path('releases/<uuid:release_id>/artifact',gui.artifact),
    path('releases/<uuid:release_id>/artifact/<path:member>',gui.artifact_member),
]
urlpatterns += [path('join/<uuid:study_id>',portal.entry)]
from core.hosting import resource
urlpatterns += [path('run/<uuid:release_id>/<path:resource_path>',resource)]

from core.hosting import preview
urlpatterns += [path("preview/<str:token>/<path:resource_path>",preview)]

from core import gui_accounts
from core import gui_imports
urlpatterns += [
    path('users',gui_accounts.users_page),
    path('users/templates/<str:kind>',gui_imports.template_download),
    path('account/password',gui_accounts.password_page),
    path('activate-account',gui_accounts.activate_account_page),
]

# Fixed public browser assets (exact whitelist; no directory serving).
from core import assets
urlpatterns += [path('static/<path:asset_path>',assets.asset)]
