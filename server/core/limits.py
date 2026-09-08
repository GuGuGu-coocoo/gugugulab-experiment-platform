from django.http import JsonResponse

class RequestLimits:
    """Reject excessive bodies before CSRF/multipart parsing allocates upload storage."""
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        if request.method in ('POST','PUT','PATCH'):
            raw=request.META.get('CONTENT_LENGTH','')
            if not raw.isdigit():return JsonResponse({'code':'content_length_required'},status=411)
            limit=128*1024*1024+65536 if request.path.startswith('/studies/') else 262144
            if int(raw)>limit:return JsonResponse({'code':'body_limit'},status=413)
        return self.get_response(request)
