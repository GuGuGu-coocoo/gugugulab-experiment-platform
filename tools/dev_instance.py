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
from core import researcher_passwords
from core.models import AccountProfile, Instance, Principal
call_command('migrate',verbosity=0)
password=researcher_passwords.generate_temporary_password()
owner=get_user_model().objects.create_user('synthetic_owner',password=password)
# A new isolated volume is explicitly v2 and creates the Owner's stable principal.
instance=Instance.objects.create(instance_id=uuid.uuid4(),owner=owner,authorization_version=2)
Principal.objects.create(user=owner)
AccountProfile.objects.create(user=owner,role='user',must_change_password=False,auth_version=1,revision=0,policy_version=2)
for name,value in [('secret',secret),('instance',str(instance.instance_id)),('dev_credentials.json',json.dumps({'username':owner.username,'password':password}))]:
    fd=os.open(data/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(value)
print('Created isolated synthetic instance. Credentials are in local_data/dev_credentials.json (mode 0600).')
