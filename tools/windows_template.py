"""Pinned official Godot 4.7.2 Windows x86_64 export template.

The Windows release template is pinned by exact size and SHA-256 to the member
``templates/windows_release_x86_64.exe`` of the official Godot 4.7.2 stable
export-templates archive (``Godot_v4.7.2-stable_export_templates.tpz``, published
by the Godot Engine project with its ``SHA512-SUMS.txt``). The digest can be
re-verified from that public release asset: unpack the archive and hash the
member; a name or a version directory alone never proves the bytes. The local
preparation keeps its own download record and member comparison in the ignored
``local_data/`` handoff area, which is intentionally not part of this public
module.

A file name or a version directory alone never proves official bytes: callers
must resolve the template through :func:`find_windows_template`, which checks
size and digest before anything is copied or exported. A present but mismatched
``GEP_GODOT_TEMPLATES`` override is rejected loudly instead of being silently
skipped in favour of another directory.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import NamedTuple


class TemplatePin(NamedTuple):
    """The exact official bytes a platform template must match."""

    name: str
    sha256: str
    size: int


class TemplateError(RuntimeError):
    """The Windows export template is missing or does not match the pinned bytes."""


OFFICIAL_WINDOWS_TEMPLATE = TemplatePin(
    'windows_release_x86_64.exe',
    'd34d36f3be1a6c49c56525ae86469b92e4f417ddf0b43cf00dd80c385c4b0562',
    109268480,
)


def template_directories(env=None):
    """Candidate template directories, the explicit override first."""
    env = os.environ if env is None else env
    candidates = []
    if env.get('GEP_GODOT_TEMPLATES'):
        candidates.append(Path(env['GEP_GODOT_TEMPLATES']))
    candidates += [
        Path.home() / 'Library/Application Support/Godot/export_templates/4.7.2.stable',
        Path.home() / '.local/share/godot/export_templates/4.7.2.stable',
        Path(env.get('APPDATA', '')) / 'Godot/export_templates/4.7.2.stable',
    ]
    return candidates


def verify_windows_template(path, pin=OFFICIAL_WINDOWS_TEMPLATE):
    """Return the SHA-256 of ``path`` or raise when it is not the pinned template."""
    path = Path(path)
    if not path.is_file():
        raise TemplateError(f'Windows export template not found: {path}')
    size = path.stat().st_size
    if size != pin.size:
        raise TemplateError(f'{path} is {size} bytes, the pinned official template is {pin.size} bytes')
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    digest = digest.hexdigest()
    if digest != pin.sha256:
        raise TemplateError(f'{path} SHA-256 {digest} does not match the pinned official digest {pin.sha256}')
    return digest


def find_windows_template(pin=OFFICIAL_WINDOWS_TEMPLATE, env=None):
    """Resolve and verify the pinned Windows template, override first.

    A directory without the template falls through to the next candidate. The
    first candidate that carries the file must match the pin: a mismatched
    override (or a corrupted standard installation) raises instead of exporting
    with unverified bytes.
    """
    for directory in template_directories(env):
        path = directory / pin.name
        if not path.is_file():
            continue
        verify_windows_template(path, pin)
        return path
    raise TemplateError(f'Official Windows 4.7.2 export template not found; '
                        f'set GEP_GODOT_TEMPLATES ({pin.name})')
