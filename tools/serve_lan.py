"""Serve an existing synthetic instance over directly verified LAN TLS.

Requires caller-provided certificates; does not modify certificate trust or firewall.
The experiment IP has no admin access. Existing loopback service remains separate.
"""
import argparse
import ipaddress
import os
from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser()
p.add_argument('--ip',required=True)
p.add_argument('--cert',type=Path,required=True)
p.add_argument('--key',type=Path,required=True)
p.add_argument('--port',type=int,default=8443)
a=p.parse_args()
ip=ipaddress.IPv4Address(a.ip)
if not ip.is_private or ip.is_loopback:p.error('Use a private LAN IPv4 address')
cert=a.cert.resolve();key=a.key.resolve()
if not cert.is_file() or not key.is_file():p.error('Certificate and private key must exist')
os.chdir(root)
os.environ.update(GEP_EXPECTED_INSTANCE=(root/'local_data/instance').read_text().strip(),PYTHONPATH=str(root/'server'),GEP_EXPERIMENT_HOST=str(ip),GEP_PUBLIC_API=f'https://{ip}:{a.port}',GEP_HTTPS='1')
os.execvpe(str(root/'.venv/bin/gunicorn'),['gunicorn','gep.wsgi:application','--bind',f'{ip}:{a.port}','--certfile',str(cert),'--keyfile',str(key),'--workers','1','--threads','4','--access-logfile','/dev/null'],os.environ)
