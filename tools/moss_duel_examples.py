"""Package the clean and GEC-connected Moss Duel projects as two complete ZIPs.

Uses existing project sources and the generic GEC dependency builder. No server
configuration, research data or existing output is collected or overwritten.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from build_gec_addon import source_members, sqlite_members
from moss_duel_delivery import copy_sources, engine_notice, game_member, zip_tree

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_FILES = ('project.godot', 'export_presets.cfg', 'README.md', 'readme_en.md',
                     'gec_entry.gd', 'gec_entry.tscn', 'gec_descriptor.json')


def rewrite_readme_links(project, source):
    repository = source.parents[1]
    for name in ('README.md', 'readme_en.md'):
        guide = project / name
        if not guide.exists():
            continue

        def link(match):
            label, target = match.groups()
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path or (project / parsed.path).is_file():
                return match.group(0)
            relative = (source / parsed.path).resolve().relative_to(repository).as_posix()
            fragment = '#' + parsed.fragment if parsed.fragment else ''
            return f'[{label}](https://github.com/GuGuGu-coocoo/gugugulab-experiment-platform/blob/main/{relative}{fragment})'

        guide.write_text(re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link, guide.read_text()))


def prepare(output, sqlite_archive):
    raw = output.absolute()
    if any(part.is_symlink() for part in (raw, *raw.parents)):
        raise ValueError('Output path links refused')
    output = raw.resolve()
    if ROOT / 'build' not in output.parents or output.exists():
        raise ValueError('Use a new directory below repository build/')
    dependencies = {name: data for name, data in source_members().items()
                    if name not in ('README.md', 'gec_descriptor.example.json')}
    dependencies.update(sqlite_members(sqlite_archive))
    output.mkdir(parents=True)
    for name in ('moss_duel', 'moss_duel_gec'):
        project = output / name
        copy_sources(ROOT / 'examples/moss_duel', project, game_member)
        if name == 'moss_duel_gec':
            for filename in INTEGRATION_FILES:
                shutil.copy2(ROOT / 'examples/moss_duel_gec' / filename, project / filename)
            for filename, data in dependencies.items():
                target = project / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        shutil.copy2(ROOT / 'LICENSE', project / 'LICENSE')
        (project / 'GODOT_NOTICES.txt').write_text(engine_notice())
        rewrite_readme_links(project, ROOT / 'examples' / name)
        archive = output / ('moss_duel_standalone.zip' if name == 'moss_duel' else 'moss_duel_gec.zip')
        zip_tree(project, archive)
    result = {'standalone': 'moss_duel_standalone.zip', 'gec_connected': 'moss_duel_gec.zip',
              'program_version': json.loads((output / 'moss_duel_gec/gec_descriptor.json').read_text())['version']}
    (output / 'examples.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sqlite-archive', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output, args.sqlite_archive), indent=2))


if __name__ == '__main__':
    main()
