"""Shared fixtures for the Phase 03 remediation suites.

Evidence policy: each remediation module run creates one fresh, unique evidence
root under ``local_data/phase03_remediation_20260923/<task>/<UTC stamp>-<random>/``
and never writes to an existing root, so a failed attempt keeps its files.
Fixtures here are synthetic only and state the current authorization semantics
explicitly (experiments with ``authorization_version`` belong to the R02 suites).
"""
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_BASE = REPO_ROOT / 'local_data' / 'phase03_remediation_20260923'


@pytest.fixture(scope='session', autouse=True)
def browser_static_urls(django_test_environment):
    """Django's live-server static wrapper requires string URL prefixes."""
    from django.conf import settings
    before = (settings.STATIC_URL, settings.MEDIA_URL)
    settings.STATIC_URL = '/static/'
    settings.MEDIA_URL = '/media/'
    yield
    settings.STATIC_URL, settings.MEDIA_URL = before


@pytest.fixture(scope='session')
def evidence_session_roots():
    """Task name -> this session's evidence root (created on first use)."""
    return {}


@pytest.fixture
def evidence_root(request, evidence_session_roots):
    """This session's unique evidence root for the requesting module's task.

    The task directory comes from the test module name (``test_p03r02a`` ->
    ``p03r02a``), so every remediation module writes its own
    ``<task>/<UTC stamp>-<random>/`` tree and no run overwrites another one. The
    stamp is created once per task and session because pytest-django reorders
    database tests across modules, which can rebuild module-scoped fixtures.
    """
    task = request.module.__name__.rsplit('.', 1)[-1]
    if task.startswith('test_'):
        task = task[len('test_'):]
    if task not in evidence_session_roots:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        root = EVIDENCE_BASE / task / f'{stamp}-{secrets.token_hex(4)}'
        root.mkdir(parents=True, exist_ok=False)
        evidence_session_roots[task] = root
    return evidence_session_roots[task]


@pytest.fixture
def evidence(evidence_root):
    """Write one evidence file in this run's root; return the path written."""
    def write(name, payload):
        path = evidence_root / name
        if isinstance(payload, (bytes, bytearray)):
            path.write_bytes(bytes(payload))
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return path
    return write
