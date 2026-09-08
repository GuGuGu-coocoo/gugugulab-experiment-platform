from django.urls import path
from core import views
urlpatterns = [
    path('v1/participant/sessions',views.participant),
    path('v1/participant/sessions/<uuid:session_id>/<str:action>',views.participant),
    path('v1/admin/exports',views.exports),
    path('v1/admin/exports/<uuid:export_id>/download',views.exports),
]
from core import gui
urlpatterns += [
    path('',gui.home), path('login',gui.signin), path('logout',gui.signout), path('activate',gui.activate),
    path('studies/<uuid:study_id>',gui.study_page), path('releases/<uuid:release_id>/config',gui.config),
]
