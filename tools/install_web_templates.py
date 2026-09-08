import hashlib
import sys
import zipfile
from pathlib import Path
archive=Path(sys.argv[1])
hash=hashlib.sha256()
with archive.open('rb') as stream:
    while chunk:=stream.read(1024*1024):hash.update(chunk)
assert hash.hexdigest()=='f298490b8d44d934be425a5a65a51bf15f422428b229a06a6e11d9ffea248011','Official Godot 4.7.2 template digest mismatch'
root=Path(__file__).resolve().parents[1]/'examples/synthetic_experiment/.godot/templates'
root.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(archive) as z:
    for name in ['web_nothreads_debug.zip','web_nothreads_release.zip']:
        (root/name).write_bytes(z.read('templates/'+name))
print('Installed verified Godot 4.7.2 single-thread Web templates in the project cache.')
