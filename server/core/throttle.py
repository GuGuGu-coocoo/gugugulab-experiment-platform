import hashlib
import time
from django.db import transaction
from .models import Throttle
from .protocol import Rejected

def check(label,limit=20):
    key=hashlib.sha256(label.encode()).hexdigest();window=int(time.time()//60)
    with transaction.atomic():
        row,_=Throttle.objects.get_or_create(key=key,defaults={'window':window})
        if row.window!=window:row.window=window;row.count=0
        row.count+=1;row.save()
        denied=row.count>limit
    if denied:raise Rejected('rate_limited',429)
