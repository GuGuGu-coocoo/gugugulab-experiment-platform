from django.http import JsonResponse

from .packages import MAX_NATIVE_ARCHIVE

# Body bounds are applied before CSRF/multipart parsing allocates upload
# storage, so each entry is an envelope, not the final validation.
DEFAULT_BODY_LIMIT = 262144
# One bounded XLSX workbook (2 MiB compressed) plus multipart overhead. This is
# the same envelope the account import already uses; the study-page roster
# entry reuses it instead of the native-program envelope.
XLSX_BODY_LIMIT = 2 * 1024 * 1024 + 65536
# The study page may carry one bounded complete native program archive.
STUDY_BODY_LIMIT = MAX_NATIVE_ARCHIVE + 65536
# Must match core.gui_imports.ROSTER_IMPORT_PATH_SUFFIX; kept as a literal here
# so this middleware never imports the Excel surface.
ROSTER_IMPORT_SUFFIX = '/roster-import'


class RequestLimits:
    """Reject excessive bodies before CSRF/multipart parsing allocates upload storage."""
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if request.method in ('POST','PUT','PATCH'):
            raw=request.META.get('CONTENT_LENGTH','')
            if not raw.isdigit():return JsonResponse({'code':'content_length_required'},status=411)
            path=request.path
            if path.startswith('/studies/') and path.endswith(ROSTER_IMPORT_SUFFIX):
                # The study's own bounded roster workbook/CSV entry: bounded like
                # the account XLSX import, never widened to the study page's
                # native-program envelope.
                limit=XLSX_BODY_LIMIT
            elif path.startswith('/studies/'):
                # Study uploads may carry one bounded complete native program
                # archive (256 MiB); the Web package keeps its own tighter
                # 128 MiB check.
                limit=STUDY_BODY_LIMIT
            elif path.startswith('/users'):
                limit=XLSX_BODY_LIMIT
            else:
                limit=DEFAULT_BODY_LIMIT
            if int(raw)>limit:return JsonResponse({'code':'body_limit'},status=413)
        return self.get_response(request)
