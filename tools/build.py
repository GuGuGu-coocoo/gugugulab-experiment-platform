"""Build the same synthetic task for Web and macOS using pinned installed tools."""
import shutil
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
version=subprocess.check_output(['godot','--version'],text=True).strip()
assert version.startswith('4.7.2.stable.'),'Godot 4.7.2 is required'
for platform in ('web','native'):(root/'build'/platform).mkdir(parents=True,exist_ok=True)
(root/'build/web/gec').mkdir(exist_ok=True)
for name in ('sdk.js','bridge.js','inputs.js'):shutil.copy2(root/'packages/gec_web'/name,root/'build/web/gec'/name)
for preset,target in [('Web','../../build/web/index.html'),('macOS','../../build/native/synthetic.zip')]:
    subprocess.run(['godot','--headless','--path',str(root/'examples/synthetic_experiment'),'--export-release',preset,target],check=True)
for platform in ('web','native'):subprocess.run([str(root/'.venv/bin/python'),str(root/'tools/package_build.py'),platform],check=True)
