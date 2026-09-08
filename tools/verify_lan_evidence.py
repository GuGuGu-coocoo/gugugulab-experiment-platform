"""Compare explicitly saved synthetic LAN exports with the guarded local instance.

Read-only for study records. Writes a bounded summary, never credentials or payloads.
This verifies saved evidence; it cannot replace physical-device operation observations.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser()
p.add_argument('--study',required=True)
p.add_argument('--evidence-dir',type=Path,required=True)
p.add_argument('--ssh-host',help='Existing, explicitly authorized SSH alias for Windows environment metadata')
a=p.parse_args()
os.chdir(root)
sys.path.insert(0,str(root/'server'))
os.environ['GEP_EXPECTED_INSTANCE']=(root/'local_data/instance').read_text().strip()
from gep.runtime import configure,verify
configure()
import django
django.setup();verify()
from core.models import Study,Session,Event
from core.services import completion_status
study=Study.objects.get(pk=a.study)
report={'study_id':str(study.id),'scope':'saved synthetic exports and server records; physical actions require separate observation','samples':[]}
for path in sorted(a.evidence_dir.glob('*.json')):
    if path.stat().st_size>4*1024*1024:continue
    try:data=json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError,UnicodeError):continue
    if not isinstance(data,dict) or data.get('format_version')!=1 or data.get('binding',{}).get('study_id')!=str(study.id):continue
    session=Session.objects.get(pk=data['session_id'],release__study=study)
    records=data['records'];ids=[e['event_id'] for e in records]
    assert len(ids)==len(set(ids)),f'Duplicate local ID in {path.name}'
    server={str(e.event_id):e.envelope for e in Event.objects.filter(session=session)}
    assert all(server.get(e['event_id'])==e for e in records),f'Record mismatch in {path.name}'
    if data.get('completion'):
        assert set(data['completion']['event_ids'])==set(ids),f'Incomplete declaration in {path.name}'
        assert set(server)==set(ids),f'Server set mismatch in {path.name}'
        assert completion_status(session)['state']=='complete',f'Incomplete server session in {path.name}'
    report['samples'].append({'file':path.name,'session_id':str(session.id),'local_count':len(ids),'server_count':len(server),'pending_at_export':len(data['pending']),'values_match':True,'complete_export':bool(data.get('completion'))})
assert report['samples'],'No matching recovery exports found'
if a.ssh_host:
    code='''$ProgressPreference='SilentlyContinue'; $ErrorActionPreference='Stop'; $os=Get-CimInstance Win32_OperatingSystem; $paths=@('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe','C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe'); [PSCustomObject]@{OS=$os.Caption;Build=$os.BuildNumber;Architecture=$os.OSArchitecture;Chrome=($paths | Where-Object {Test-Path $_} | ForEach-Object {(Get-Item $_).VersionInfo.ProductVersion})} | ConvertTo-Json'''
    encoded=base64.b64encode(code.encode('utf-16-le')).decode()
    output=subprocess.check_output(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',a.ssh_host,'powershell.exe -NoProfile -EncodedCommand '+encoded],timeout=20)
    report['windows_environment']=json.loads(output.decode('utf-8-sig'))
path=a.evidence_dir/'automated_evidence_report.json'
path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(f'Compared {len(report["samples"])} saved exports; report: {path}')
