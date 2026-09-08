import hashlib
import json
import sys
import zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
d=json.loads((root/'examples/synthetic_experiment/descriptor.json').read_text())
if sys.argv[1]=='native':
    archive=root/'build/native/synthetic.zip'
    d['platform']='macos_arm64';d['program_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
    (root/'build/native/descriptor.json').write_text(json.dumps(d))
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            target=root/'build/native'/entry.filename
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(entry))
            target.chmod((entry.external_attr>>16)&0o777 or 0o644)
else:
    files=sorted(p for p in (root/'build/web').rglob('*') if p.is_file())
    h=hashlib.sha256()
    for p in files:h.update(p.relative_to(root/'build').as_posix().encode());h.update(p.read_bytes())
    d['program_sha256']=h.hexdigest()
    with zipfile.ZipFile(root/'build/synthetic_web.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',json.dumps(d))
        for p in files:z.write(p,p.relative_to(root/'build'))
print('Packaged '+sys.argv[1]+' build with separate public configuration.')
