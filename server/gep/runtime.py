"""Fail closed before serving if database, secret, or expected volume differs."""
import os
from pathlib import Path

def configure():
    root=Path(os.environ.get('GEP_DATA_DIR','local_data')).resolve()
    expected=os.environ.get('GEP_EXPECTED_INSTANCE')
    if not expected or (root/'initializing').exists() or not all((root/name).is_file() for name in ('instance','secret','gep.sqlite3')):
        raise RuntimeError('Expected instance and initialized volume are required')
    if (root/'instance').read_text().strip()!=expected:
        raise RuntimeError('Wrong volume instance marker')
    os.environ['GEP_SECRET_KEY']=(root/'secret').read_text().strip()
    os.environ['GEP_DATA_DIR']=str(root)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','gep.settings')

def verify():
    from core.models import Instance
    if str(Instance.objects.get(pk=1).instance_id)!=os.environ['GEP_EXPECTED_INSTANCE']:
        raise RuntimeError('Database instance does not match volume')
