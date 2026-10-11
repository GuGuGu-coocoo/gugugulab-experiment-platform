"""Package the independent game only; do not install or assemble GEC.

Run: .venv/bin/python tools/moss_duel_standalone.py --output build/<new-directory>
The generic GEC download is prepared separately with build_gec_addon.py.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from moss_duel_delivery import copy_sources, digest, engine_notice, game_member, zip_tree

ROOT = Path(__file__).resolve().parents[1]


def prepare(output: Path):
    output = output.absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError('Output path links refused')
    output = output.resolve()
    if ROOT / 'build' not in output.parents or output.exists():
        raise ValueError('Use a new directory below repository build/')
    output.mkdir(parents=True)
    project = output / 'standalone'
    copy_sources(ROOT / 'examples/moss_duel', project, game_member)
    shutil.copy2(ROOT / 'LICENSE', project / 'LICENSE')
    (project / 'GODOT_NOTICES.txt').write_text(engine_notice())
    archive = output / 'moss_duel_standalone.zip'
    zip_tree(project, archive)
    manifest = {archive.name: {'size': archive.stat().st_size, 'sha256': digest(archive)}}
    (output / 'delivery_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), indent=2))


if __name__ == '__main__':
    main()
