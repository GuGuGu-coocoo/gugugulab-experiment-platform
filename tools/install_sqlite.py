"""Install only the verified macOS SQLite runtime from an official release archive."""
import hashlib
import sys
import zipfile
from pathlib import Path
archive=Path(sys.argv[1])
assert hashlib.sha256(archive.read_bytes()).hexdigest()=='26966044757cf86a223a8027f8bc88c49c289ab047dcf8138bb591d7632e580e','Archive digest mismatch'
root=Path(__file__).resolve().parents[1]/'examples/synthetic_experiment/addons/godot-sqlite'
with zipfile.ZipFile(archive) as z:
    for name in z.namelist():
        prefix='demo/addons/godot-sqlite/'
        if name.startswith(prefix+'bin/libgdsqlite.macos.') and not name.endswith('/'):
            target=root/name.removeprefix(prefix)
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(z.read(name))
    config=z.read('demo/addons/godot-sqlite/gdsqlite.gdextension').decode()
    lines=config.splitlines()
    selected=['[configuration]','entry_symbol = "sqlite_library_init"','compatibility_minimum = "4.5"','[libraries]']+[line for line in lines if line.startswith('macos.')]
    (root/'gdsqlite.gdextension').write_text('\n'.join(selected)+'\n')
print('Installed verified Godot SQLite v4.7 macOS runtime.')
