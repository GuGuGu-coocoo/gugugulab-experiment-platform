"""Bounded XLSX template downloads and preview-bound import operations.

Split out of :mod:`core.gui_accounts` so the permission-matrix pages stay
importable (and runnable) without the Excel dependency, and so the workbook
surface lives next to its own authorization entry points.
"""
from django.http import HttpResponse
from django.shortcuts import redirect

from . import excel, importers, permissions, ui
from .access import allowed_platform, guard
from .gui import admin_host, admin_origin
from .models import Study
from .protocol import Rejected, require
from .views import endpoint

IMPORT_OPS = ('import_users_preview', 'import_users_commit', 'import_roster_preview', 'import_roster_commit')


def _upload(request):
    upload = request.FILES.get('file')
    require(upload is not None, 'file_required', 400)
    require((upload.name or '').lower().endswith('.xlsx'), 'invalid_xlsx')
    require(upload.size <= excel.MAX_COMPRESSED, 'file_too_large', 413)
    return upload.read(excel.MAX_COMPRESSED + 1)


def apply_import(request, op):
    """Resolve one XLSX import operation; the caller owns error rendering."""
    lang = ui.lang_of(request)
    password = request.POST.get('password', '')
    if op == 'import_users_preview':
        preview = importers.preview_users(request.user, _upload(request))
        return {'preview': permissions.preview_payload(preview, lang), 'commit_op': 'import_users_commit'}
    if op == 'import_users_commit':
        # The link origin is validated before the import commits: a broken
        # ADMIN_ORIGIN must fail closed without creating accounts/invitations
        # and without consuming the preview, so the actor can fix the
        # configuration and retry the same preview. Invitation links are built
        # once here, from the origin that was just validated.
        origin = admin_origin(request)
        outcome = importers.commit_users(request.user, password, request.POST.get('preview_id'))
        result = outcome.get('result', outcome)
        notice = ui.notice(
            lang,
            f"账号导入完成：邀请 {len(result.get('invited', []))}，权限更新 {len(result.get('updated', []))}，停用 {len(result.get('disabled', []))}，启用 {len(result.get('enabled', []))}。",
            f"Account import completed: {len(result.get('invited', []))} invitations, {len(result.get('updated', []))} permission updates, {len(result.get('disabled', []))} disabled, {len(result.get('enabled', []))} enabled.")
        invitations = [{'username': item['username'],
                        'link': origin + '/activate-account?token=' + item['token']}
                       for item in outcome.get('invitation_tokens', [])]
        return {'notice': notice, 'invitation_tokens': invitations, 'import_result': result}
    if op == 'import_roster_preview':
        study = Study.objects.filter(pk=request.POST.get('study_id')).first()
        require(study is not None, 'study_missing', 404)
        preview = importers.preview_roster(request.user, study, _upload(request))
        return {'preview': permissions.preview_payload(preview, lang), 'commit_op': 'import_roster_commit'}
    if op == 'import_roster_commit':
        result = importers.commit_roster(request.user, password, request.POST.get('preview_id'))
        return {'notice': ui.notice(lang, f"名单导入完成：{result['study']} 新增 {result['added']} 个 ID。",
                                    f"Roster import completed: {result['added']} new IDs in {result['study']}.")}
    raise Rejected('unknown_operation', 400)


@endpoint
def template_download(request, kind):
    """Bounded XLSX templates; they never contain existing passwords or hashes."""
    admin_host(request)
    if not request.user.is_authenticated:
        return redirect('/login')
    require(allowed_platform(request.user, 'accounts.view'), 'forbidden', 403)
    if kind == 'users':
        payload, filename = excel.users_template_bytes(), 'gep_users_template.xlsx'
    elif kind == 'roster':
        study = Study.objects.filter(pk=request.GET.get('study')).first()
        require(study is not None, 'study_missing', 404)
        guard(request.user, study, 'study.configure')
        payload, filename = excel.roster_template_bytes(study.mode), 'gep_roster_template.xlsx'
    else:
        raise Rejected('unknown_operation', 404)
    response = HttpResponse(payload, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'no-store'
    return response
