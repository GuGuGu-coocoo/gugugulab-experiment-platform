import os
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
os.chdir(root)
os.environ['GEP_EXPECTED_INSTANCE']=(root/'local_data/instance').read_text().strip()
os.environ['PYTHONPATH']=str(root/'server')
os.execvpe(str(root/'.venv/bin/gunicorn'),['gunicorn','gep.wsgi:application','--bind','127.0.0.1:8000','--workers','1','--threads','4','--access-logfile','/dev/null'],os.environ)
