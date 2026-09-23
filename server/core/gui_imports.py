"""Bounded XLSX/CSV roster import and bounded template downloads.

Split out of :mod:`core.gui_accounts` so the permission-matrix pages stay
importable (and runnable) without the Excel dependency, and so the workbook
surface lives next to its own authorization entry points.

Two distinct scopes live here:

- the instance account surface (``/users``): the researcher account template and
  the user/permission import, authorized by the finite ``accounts.view``
  platform action;
- the study surface (the participation module): the participant roster template,
  preview and atomic commit, authorized by the study's own ``study.view`` +
  ``study.configure``, so an ordinary study creator never needs an instance
  account permission. The legacy instance-page roster writes are refused
  explicitly, and the legacy roster template GET authorizes first and then
  redirects to the study entry.
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
# The study-page roster entries: template, upload/CSV preview and the
# password-confirmed commit.
STUDY_ROSTER_OPS = ('import_roster_preview', 'import_roster_commit')
STUDY_ROSTER_COMMIT_OP = 'import_roster_commit'
ROSTER_IMPORT_PATH_SUFFIX = '/roster-import'
ROSTER_TEMPLATE_PATH_SUFFIX = '/roster-template'


def _upload(request):
    upload = request.FILES.get('file')
    require(upload is not None, 'file_required', 400)
    require((upload.name or '').lower().endswith('.xlsx'), 'invalid_xlsx')
    require(upload.size <= excel.MAX_COMPRESSED, 'file_too_large', 413)
    return upload.read(excel.MAX_COMPRESSED + 1)


def _roster_source(request):
    """Exactly one bounded roster source: an XLSX upload or pasted CSV text."""
    upload = request.FILES.get('file')
    text = request.POST.get('roster')
    if upload is not None:
        require(text in (None, ''), 'roster_source', 400)
        require((upload.name or '').lower().endswith('.xlsx'), 'invalid_xlsx')
        require(upload.size <= excel.MAX_COMPRESSED, 'file_too_large', 413)
        return {'raw': upload.read(excel.MAX_COMPRESSED + 1)}
    require(text is not None, 'roster_source', 400)
    return {'text': text}


def apply_import(request, op):
    """Resolve one XLSX import operation; the caller owns error rendering."""
    lang = ui.lang_of(request)
    password = request.POST.get('password', '')
    if op in STUDY_ROSTER_OPS:
        # The roster import is a study-page entry now. The legacy instance-page
        # writes are refused explicitly instead of being re-interpreted through
        # the account-governance gate, so they can never bypass the study scope,
        # the error preview or the password-confirmed commit.
        raise Rejected('roster_import_moved', 409)
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
    raise Rejected('unknown_operation', 400)


def apply_study_roster_import(request, study, op):
    """Resolve one study-page roster operation; the caller renders the page.

    The study page already resolved the study from its URL; this entry only
    resolves the source, builds the preview or commits the confirmed preview.
    Every authorization decision lives in the preview/commit gate itself.
    """
    lang = ui.lang_of(request)
    password = request.POST.get('password', '')
    if op == 'import_roster_preview':
        preview = importers.preview_roster(request.user, study, **_roster_source(request))
        return {'preview': permissions.preview_payload(preview, lang),
                'commit_op': STUDY_ROSTER_COMMIT_OP}
    if op == 'import_roster_commit':
        result = importers.commit_roster(request.user, password, request.POST.get('preview_id'),
                                         study.pk)
        return {'notice': ui.notice(lang, f"名单导入完成：{result['study']} 新增 {result['added']} 个 ID。",
                                    f"Roster import completed: {result['added']} new IDs in {result['study']}.")}
    raise Rejected('unknown_operation', 400)


@endpoint
def roster_import(request, study_id):
    """The bounded study-page roster entry (own middleware XLSX body bound).

    A thin alias of the study page's participation module: the URL exists so the
    pre-parse body bound for an uploaded workbook is the bounded XLSX envelope
    rather than the study page's native-program envelope.
    """
    from . import gui
    return gui.study_page(request, study_id, 'participation')


@endpoint
def study_roster_template(request, study_id):
    """Bounded XLSX roster template for one study (its configuration scope)."""
    admin_host(request)
    if not request.user.is_authenticated:
        return redirect('/login')
    study = Study.objects.filter(pk=study_id).first()
    require(study is not None, 'study_missing', 404)
    guard(request.user, study, 'study.view')
    guard(request.user, study, 'study.configure')
    response = HttpResponse(excel.roster_template_bytes(study.mode),
                            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="gep_roster_template.xlsx"'
    response['Cache-Control'] = 'no-store'
    return response


@endpoint
def template_download(request, kind):
    """Bounded XLSX templates; they never contain existing passwords or hashes.

    The researcher account template stays on the instance page. The roster
    template is a study-page entry: an actor holding that study's view +
    configure scope is redirected to it, and everyone else is refused, so this
    legacy GET can never serve a roster workbook (or its password column)
    outside the study's own scope.
    """
    admin_host(request)
    if not request.user.is_authenticated:
        return redirect('/login')
    if kind == 'users':
        require(allowed_platform(request.user, 'accounts.view'), 'forbidden', 403)
        payload, filename = excel.users_template_bytes(), 'gep_users_template.xlsx'
    elif kind == 'roster':
        study = Study.objects.filter(pk=request.GET.get('study')).first()
        require(study is not None, 'study_missing', 404)
        guard(request.user, study, 'study.view')
        guard(request.user, study, 'study.configure')
        return redirect('/studies/%s%s' % (study.pk, ROSTER_TEMPLATE_PATH_SUFFIX))
    else:
        raise Rejected('unknown_operation', 404)
    response = HttpResponse(payload, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'no-store'
    return response
