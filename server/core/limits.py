from django.http import JsonResponse

from .packages import MAX_NATIVE_ARCHIVE

class RequestLimits:
    """Reject excessive bodies before CSRF/multipart parsing allocates upload storage."""
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if request.method in ('POST','PUT','PATCH'):
            raw=request.META.get('CONTENT_LENGTH','')
            if not raw.isdigit():return JsonResponse({'code':'content_length_required'},status=411)
            # Study uploads may carry one bounded complete native program archive
            # (256 MiB); the Web package keeps its own tighter 128 MiB check.
            limit=MAX_NATIVE_ARCHIVE+65536 if request.path.startswith('/studies/') else (2*1024*1024+65536 if request.path.startswith('/users') else 262144)
            if int(raw)>limit:return JsonResponse({'code':'body_limit'},status=413)
        return self.get_response(request)
