"""Run dependent release preparation before independent executable acceptance tests."""
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
for path in ('build/native/descriptor.json','build/synthetic_web.zip','local_data/dev_credentials.json'):
    if not (root/path).exists():raise SystemExit('Initialize the synthetic instance, start the service, and build both packages first.')
subprocess.run(['pnpm','exec','playwright','test','tests/browser/native_release.spec.js','tests/browser/web_release.spec.js'],cwd=root,check=True)
files=[str(p.relative_to(root)) for p in sorted((root/'tests/browser').glob('*.spec.js')) if p.name not in ('native_release.spec.js','web_release.spec.js')]
subprocess.run(['pnpm','exec','playwright','test',*files],cwd=root,check=True)
