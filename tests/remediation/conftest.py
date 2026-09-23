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
import select
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_BASE = REPO_ROOT / 'local_data' / 'phase03_remediation_20260923'
TOKEN_DRAIN_LIMIT = 5.0


def _drain_tokens(read_fd, limit=TOKEN_DRAIN_LIMIT):
    """Bounded read of the token channel: EOF or a short deadline, never a hang.

    The direct child is already gone when this runs and the parent closed its
    own write end, so EOF arrives immediately. A re-parented grandchild could
    still hold a duplicate write descriptor, so the wait is bounded by ``limit``
    instead of trusting EOF alone.
    """
    deadline = time.monotonic() + limit
    chunks = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        ready, _, _ = select.select([read_fd], [], [], remaining)
        if not ready:
            break
        chunk = os.read(read_fd, 8192)
        if not chunk:
            break
        chunks.append(chunk)
    return b''.join(chunks).decode('utf-8', 'replace')



@pytest.fixture(scope='session')
def evidence_session_roots():
    """Task name -> this session's evidence root (created on first use)."""
    return {}


def ensure_evidence_root(evidence_session_roots, task):
    """The session-unique evidence root for one task, created on first use.

    One root per task and session even when module-scoped fixtures (a real
    isolated instance, for example) and function-scoped tests both need it.
    """
    if task not in evidence_session_roots:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        root = EVIDENCE_BASE / task / f'{stamp}-{secrets.token_hex(4)}'
        root.mkdir(parents=True, exist_ok=False)
        evidence_session_roots[task] = root
    return evidence_session_roots[task]


@pytest.fixture(scope='session')
def evidence_root_for(evidence_session_roots):
    """Task name -> this session's unique evidence root, for wider fixtures."""
    return lambda task: ensure_evidence_root(evidence_session_roots, task)


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
    return ensure_evidence_root(evidence_session_roots, task)


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

    Every exit mode is bounded and closes only the descriptors created here:
    normal exit, non-zero exit and a child that writes no token at all all
    return; a real timeout kills the child and raises ``TimeoutExpired`` after
    the token channel is drained with a short deadline. The parent drops its own
    copy of the write end before waiting, so it can never be the reason a read
    blocks.
    """
    def run(script, env, timeout=240):
        read_fd, write_fd = os.pipe()
        os.set_inheritable(write_fd, True)
        process = None
        try:
            process = subprocess.Popen(['node', '--input-type=module', '-e', script],
                                       cwd=REPO_ROOT, env=dict(env, GEP_TOKEN_FD=str(write_fd)),
                                       pass_fds=(write_fd,), stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            os.close(write_fd)
            write_fd = None
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(process.args, timeout, output=stdout, stderr=stderr)
            reported = _drain_tokens(read_fd)
        finally:
            if write_fd is not None:
                os.close(write_fd)
            os.close(read_fd)
        result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        return result, [line for line in reported.splitlines() if line]
    return run
