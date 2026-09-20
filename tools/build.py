"""Build the same synthetic task for the requested platforms with pinned tools.

Usage: ``python tools/build.py [web] [native] [windows]`` - without arguments the
historical default builds Web and macOS. Every target is exported with the pinned
local Godot 4.7.2 and packaged by ``tools/package_build.py``; the scientific
``task.gd`` is never modified by a platform choice.

The Windows target cross-exports with the official Windows x86_64 release
template from the local Godot 4.7.2 export templates (``GEP_GODOT_TEMPLATES`` or
the standard per-user template directory). The template is resolved and verified
against the pinned official digest (``tools/windows_template.py``) before it is
copied into the project's ``.godot/templates`` directory, because the committed
export preset references it with a ``res://`` path: a file name or a version
directory alone never proves official bytes, so a mismatched override fails the
build instead of being exported. A missing template or native SQLite runtime
fails the build instead of producing an incomplete program.
"""
import shutil
import subprocess
import sys
from pathlib import Path

from windows_template import TemplateError, find_windows_template, verify_windows_template

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'examples/synthetic_experiment'
TEMPLATES = PROJECT / '.godot' / 'templates'
WINDOWS_TEMPLATE = 'windows_release_x86_64.exe'
TARGETS = {
    'web': ('Web', ROOT / 'build/web/index.html'),
    'native': ('macOS', ROOT / 'build/native/synthetic.zip'),
    'windows': ('Windows', ROOT / 'build/windows/GEP Synthetic Experiment/GEP Synthetic Experiment.exe'),
}


def windows_template():
    """The verified official Windows template path (SystemExit when unpinned)."""
    try:
        return find_windows_template()
    except TemplateError as error:
        raise SystemExit(str(error))


def main():
    version = subprocess.check_output(['godot', '--version'], text=True).strip()
    assert version.startswith('4.7.2.stable.'), 'Godot 4.7.2 is required'
    targets = sys.argv[1:] or ['web', 'native']
    unknown = sorted(set(targets) - set(TARGETS))
    assert not unknown, f'Unknown build target(s): {", ".join(unknown)}'
    for platform in ('web', 'native'):
        (ROOT / 'build' / platform).mkdir(parents=True, exist_ok=True)
    (ROOT / 'build/web/gec').mkdir(exist_ok=True)
    for name in ('sdk.js', 'bridge.js', 'inputs.js', 'shell.js'):
        shutil.copy2(ROOT / 'packages/gec_web' / name, ROOT / 'build/web/gec' / name)
    if 'windows' in targets:
        TARGETS['windows'][1].parent.mkdir(parents=True, exist_ok=True)
        TEMPLATES.mkdir(parents=True, exist_ok=True)
        template = windows_template()
        digest = verify_windows_template(template)
        print(f'Using pinned official Windows template {template} sha256={digest}')
        shutil.copy2(template, TEMPLATES / WINDOWS_TEMPLATE)
    for target in dict.fromkeys(targets):
        preset, output = TARGETS[target]
        subprocess.run(['godot', '--headless', '--path', str(PROJECT), '--export-release', preset, str(output)], check=True)
        subprocess.run([str(ROOT / '.venv/bin/python'), str(ROOT / 'tools/package_build.py'), target], check=True)


if __name__ == '__main__':
    main()
