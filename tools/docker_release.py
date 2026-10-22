"""Create a Docker-only deployment bundle from an already built Linux image.

No registry push or GitHub Release creation occurs. No source, data or private
configuration is collected into the deployment archive.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import quote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def copy_guide_images(output):
    # Explicit tutorial assets only, never screenshots from local evidence.
    for language in ('zh', 'en'):
        target = output / 'docs' / 'images' / language
        target.mkdir(parents=True)
        for name in ('01_studies', '02_participation', '03_roster', '04_web_publish',
                     '05_recruitment', '06_participant_portal', '07_sessions',
                     '08_exports', '09_permissions', '10_permission_details'):
            shutil.copy2(ROOT / 'docs' / 'images' / language / (name + '.png'), target / (name + '.png'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--caddy-image', default='gep-caddy:2.11.7-go1.26.10')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'build') or output.exists():
        parser.error('Choose a new directory below build/.')
    details = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.image], text=True))[0]
    if details['Os'] != 'linux':
        parser.error('GEP uses Linux containers, including on Windows Docker Desktop.')
    proxy = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.caddy_image], text=True))[0]
    if proxy['Os'] != 'linux' or proxy['Architecture'] != details['Architecture']:
        parser.error('Build the patched Caddy image for the same Linux architecture first.')
    output.mkdir(parents=True)
    for relative in ('compose.yaml', 'compose.public.yaml', '.env.example', 'deploy/windows.ps1',
                     'deploy/start_windows.cmd', 'deploy/linux.sh', 'deploy/Caddyfile', 'LICENSE',
                     'README.md', 'README_ZH.md'):
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    public_compose = output / 'compose.public.yaml'
    public_compose.write_text(public_compose.read_text(encoding='utf-8').replace(
        '${GEP_CADDY_IMAGE:-gep-caddy:2.11.7-go1.26.10}', '${GEP_CADDY_IMAGE:-' + args.caddy_image + '}'), encoding='utf-8')
    # Only current user guides: no internal documents or local evidence.
    (output / 'docs').mkdir()
    for name in ('deployment.md', 'deployment_en.md', 'researcher.md', 'researcher_en.md',
                 'gec_godot.md', 'gec_godot_en.md', 'moss_duel_gec.md', 'moss_duel_gec_en.md',
                 'protocol.md', 'security.md', 'SECURITY_AUDIT.md'):
        guide = ROOT / 'docs' / name
        shutil.copy2(guide, output / 'docs' / guide.name)
    copy_guide_images(output)
    (output / 'third_party').mkdir()
    shutil.copy2(ROOT / 'third_party' / 'godot_sqlite_license.md',
                 output / 'third_party' / 'godot_sqlite_license.md')
    # Keep packaged guides readable offline. Links to source-only examples
    # point back to the current source, not nonexistent ZIP paths or old tags
    # that predate the connected example.
    for guide in [output / 'README.md', output / 'README_ZH.md', *(output / 'docs').glob('*.md')]:
        def link(match):
            label, target = match.groups()
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path or target.startswith('/'):
                return match.group(0)
            local = (guide.parent / parsed.path).resolve()
            if local.exists():
                return match.group(0)
            relative = local.relative_to(output).as_posix()
            kind = 'tree' if (ROOT / relative).is_dir() else 'blob'
            suffix = '#' + parsed.fragment if parsed.fragment else ''
            return f'[{label}](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/{kind}/main/{quote(relative)}{suffix})'
        guide.write_text(re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, guide.read_text(encoding='utf-8')), encoding='utf-8')
    subprocess.run(['docker', 'image', 'save', '--output', str(output / 'gep-image.tar'), args.image, args.caddy_image], check=True)
    (output / 'docker-release.json').write_text(json.dumps({
        'image': args.image, 'image_id': details['Id'], 'os': details['Os'], 'architecture': details['Architecture'],
        'caddy_image': args.caddy_image, 'caddy_image_id': proxy['Id'],
        'version': '0.1.0', 'delivery': 'docker-only',
    }, indent=2) + '\n')
    archive = output.parent / (output.name + '.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                bundle.write(path, output.name + '/' + path.relative_to(output).as_posix())
    print(f'Docker deployment bundle: {archive}')


if __name__ == '__main__':
    main()
