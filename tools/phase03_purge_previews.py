"""Bounded cleanup of private staged intent on expired permission previews.

Operator entry point for :func:`core.permissions.purge_sensitive_staging`, which
is also invoked opportunistically on every new preview. Consumed previews drop
their staging at commit time; this path clears previews that were prepared but
never confirmed, so no credential hash survives its TTL.

Use it only against the database selected by GEP_DATA_DIR: point that at a
synthetic copy or a fresh phase folder, never at the protected acceptance volume.
"""
import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = PROJECT_ROOT / 'server'
sys.path.insert(0, str(SERVER_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gep.settings')

import django  # noqa: E402

django.setup()

from core import permissions  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Clear staged secrets on expired permission previews.')
    parser.add_argument('--limit', type=int, default=permissions.PURGE_LIMIT,
                        help=f'maximum previews cleared per batch (default {permissions.PURGE_LIMIT})')
    parser.add_argument('--repeat', action='store_true', help='clear batches until no expired staging remains')
    args = parser.parse_args()
    total = 0
    while True:
        cleared = permissions.purge_sensitive_staging(limit=args.limit)
        total += cleared
        if not args.repeat or cleared == 0:
            break
    print(f'purged_staged_previews={total}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
