"""Shared fixtures for the Phase 03 remediation suites.

Evidence policy: each remediation module run creates one fresh, unique evidence
root under ``local_data/phase03_remediation_20260923/<task>/<UTC stamp>-<random>/``
and never writes to an existing root, so a failed attempt keeps its files.
Fixtures here are synthetic only and state the current authorization semantics
explicitly (experiments with ``authorization_version`` belong to the R02 suites).

Secrets policy: one-time invitation tokens stay in process memory. The
``run_chrome_tokens`` helper returns the real token(s) a browser script minted
through an anonymous pipe (never through a file or stdout), so a test can scan
its evidence and every captured diagnostic for leaks.
"""
import json
import os
import secrets
import subprocess
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
        # Containment: an evidence name must stay inside this run's own root.
        if evidence_root.resolve() not in path.resolve().parents:
            raise AssertionError(f'evidence name escapes its run root: {name!r}')
        if isinstance(payload, (bytes, bytearray)):
            path.write_bytes(bytes(payload))
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return path
    return write


@pytest.fixture(scope='session')
def run_chrome_tokens():
    """Run one Node/Playwright script with an in-memory one-time token channel.

    The script writes one token per line to the inherited fd named in
    ``GEP_TOKEN_FD``. The tokens never touch a file, stdout or stderr; they are
    returned so the test can independently scan evidence and captured browser
    output for leaks. Anything the script prints must already be redacted.
    """
    def run(script, env, timeout=240):
        read_fd, write_fd = os.pipe()
        os.set_inheritable(write_fd, True)
        try:
            result = subprocess.run(['node', '--input-type=module', '-e', script],
                                    cwd=REPO_ROOT, env=dict(env, GEP_TOKEN_FD=str(write_fd)),
                                    pass_fds=(write_fd,), capture_output=True, text=True,
                                    timeout=timeout)
            reported = os.read(read_fd, 8192).decode('utf-8', 'replace')
        finally:
            os.close(write_fd)
            os.close(read_fd)
        return result, [line for line in reported.splitlines() if line]
    return run
