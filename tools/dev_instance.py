"""Create isolated synthetic credentials once, never print them or reuse an existing directory."""
import json
import os
import secrets
import sys
import uuid
from pathlib import Path
root=Path(__file__).resolve().parents[1]
data=root/'local_data'
if data.exists():
    raise SystemExit('Refusing to overwrite existing local_data')
data.mkdir(mode=0o700)
sys.path.insert(0,str(root/'server'))
os.environ['DJANGO_SETTINGS_MODULE']='gep.settings'
os.environ['GEP_DATA_DIR']=str(data)
secret=secrets.token_urlsafe(48)
os.environ['GEP_SECRET_KEY']=secret
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from core.models import Instance
call_command('migrate',verbosity=0)
password=secrets.token_urlsafe(24)
owner=get_user_model().objects.create_user('synthetic_owner',password=password)
instance=Instance.objects.create(instance_id=uuid.uuid4(),owner=owner)
for name,value in [('secret',secret),('instance',str(instance.instance_id)),('dev_credentials.json',json.dumps({'username':owner.username,'password':password}))]:
    fd=os.open(data/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(value)
print('Created isolated synthetic instance. Credentials are in local_data/dev_credentials.json (mode 0600).')
