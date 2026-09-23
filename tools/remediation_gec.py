#!/usr/bin/env python3
"""GEC three-platform preparation and frozen-package 403 compatibility checks.

``--verify-local`` (macOS) really executes the new clients with real HTTP and
then proves the frozen old packages stay safe on a real 403:

* the new macOS native shell (real Godot 4.7.2, real SQLite, real HTTP) through
  the failure surface, the legal export, the reopen states and the deletion
  terminal (``tests/native/remediation_shell_harness.gd``);
* the new Web shell: a fresh Godot Web export of the current sources, frozen
  under ``build/phase03_remediation_20260923/<unique>/web/`` and driven in real
  Chrome by ``tests/browser/remediation_shell.spec.js`` (the real unpatched
  bridge and panel);
* the frozen old Web package (``build/independent_acceptance_20260912``) in real
  Chrome: the shipped old bridge/SDK must stop after the first refused batch,
  keep the local records and never claim success;
* the frozen old macOS native program against the same refusal: it must stop
  retrying, keep its local queue and never report done.

``--verify-windows`` runs the same client strategy on a new complete Windows
package and refuses to run anywhere but Windows, because a cross-compile or a
local command is not a Windows pass (R11 executes it on the real host). The
provided package is a read-only source: the tool copies it once into the run
root, resolves the expected program from ``--program`` or the package manifest
(never from the first ``*.exe``), replaces the synthetic connection
configuration only inside the copy, and re-checks every source digest
afterwards. A compatibility-config run over a copy is not an end-to-end run of
the original frozen package; both evidence classes stay separate.

Evidence stays under
``local_data/phase03_remediation_20260923/p03r09c_gec/<UTC stamp>-<random>/``
(one fresh root per run, never reused); the new build freezes under
``build/phase03_remediation_20260923/<unique>/``. The old frozen artifacts are
read-only: their digests are re-checked after the run. Nothing outside those
roots is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'examples' / 'synthetic_experiment'
FAULT_APP = ROOT / 'tests' / 'remediation' / 'delivery_fault_app.py'
HARNESS = ROOT / 'tests' / 'native' / 'remediation_shell_harness.gd'
SPEC = 'tests/browser/remediation_shell.spec.js'
OLD_ROOT = ROOT / 'build' / 'independent_acceptance_20260912'
OLD_WEB_ZIP = OLD_ROOT / 'web_experiment.zip'
OLD_NATIVE_ZIP = OLD_ROOT / 'native_program.zip'
EVIDENCE_BASE = ROOT / 'local_data' / 'phase03_remediation_20260923' / 'p03r09c_gec'
FREEZE_BASE = ROOT / 'build' / 'phase03_remediation_20260923'
VENV_PYTHON = ROOT / '.venv' / 'bin' / 'python'
PLAYWRIGHT = ROOT / 'node_modules' / '.bin' / 'playwright'
DEFAULT_WINDOWS_MANIFEST = 'gec_package_manifest.json'

OLD_WEB_DRIVER = r'''
import {chromium} from '@playwright/test';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
/* Owned by tools/remediation_gec.py: drives the frozen old Web package in real
 * Chrome against the real fault server. The old bridge/SDK bytes come straight
 * from the extracted frozen zip; the 403 must stop the retries and keep the
 * local queue. Only observations are written to the run root. */
const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const web = path.join(job.package_dir, 'web');
const checks = [];
const failures = [];
const check = (ok, label, detail) => { const entry = {label, ok: !!ok, detail: detail ?? null}; checks.push(entry); if (!ok) failures.push(entry); };
const MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.wasm': 'application/wasm', '.pck': 'application/octet-stream', '.png': 'image/png'};
const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
  if (pathname === '/' || pathname === '/index.html') {
    const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
    response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'});
    response.end(html.replace('<head>', `<head><script>globalThis.GEP_CONTEXT=${JSON.stringify(job.context).replace(/</g, '\\u003c')};</script>`));
    return;
  }
  const file = path.join(web, pathname.slice(1));
  if (fs.existsSync(file) && fs.statSync(file).isFile()) {
    response.writeHead(200, {'Content-Type': MIME[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store'});
    response.end(fs.readFileSync(file));
    return;
  }
  response.writeHead(404); response.end();
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({channel: 'chrome', headless: true});
const page = await browser.newPage();
page.on('pageerror', error => check(false, 'uncaught old-package page error: ' + error.message));
try {
  await page.goto(origin + '/index.html');
  await page.waitForFunction(() => globalThis.GECBridge !== undefined, null, {timeout: 120000});
  await page.waitForFunction(() => globalThis.GECBridge.status().state === 'ready', null, {timeout: 120000});
  const call = (op, args) => page.evaluate(async ({op, args}) => new Promise((resolve, reject) => {
    const key = 'old-' + Math.random().toString(36).slice(2);
    const deadline = Date.now() + 60000;
    const attempt = () => {
      if (!globalThis.GECBridge) { if (Date.now() > deadline) reject(new Error('bridge timeout')); setTimeout(attempt, 20); return; }
      globalThis.GECBridge.call(key, op, JSON.stringify(args));
      const poll = () => { const value = globalThis.GECBridge.take(key); if (value != null) resolve(JSON.parse(value)); else if (Date.now() > deadline) reject(new Error('bridge timeout ' + op)); else setTimeout(poll, 10); };
      poll();
    };
    attempt();
  }), {op, args});
  const started = await call('start', [{}]);
  check(!!started.session_id, 'the frozen old Web client admitted a session: ' + JSON.stringify(started));
  const sessionId = started.session_id;
  const events = [
    ['exp.rt', {trial_id: 't1', choice: 'left', rt_ms: 321.5, response_status: 'responded'}, {id: 'rt', version: '1'}],
    ['exp.interaction', {action: 'revise', selection: ['shape_a', 'shape_c'], confidence: null, nested: {changes: [{from: null, to: '001'}], confirmed: false}}, {id: 'interaction', version: '1'}],
  ];
  for (const [kind, payload, schema] of events) {
    const recorded = await call('record', [kind, payload, schema, null]);
    check(!!recorded.event_id, 'the frozen old Web client recorded ' + kind);
  }
  const committed = await call('commit', [null]);
  check(!!committed && !committed.error, 'the frozen old Web client committed locally: ' + JSON.stringify(committed));
  const finished = await call('finish', []);
  check(!!finished && !finished.error, 'the frozen old Web client declared its completion set: ' + JSON.stringify(finished));
  // The old bridge never answers a successful flush (undefined reply); the
  // refused batch is observed on the real server and in the durable store.
  await page.evaluate((args) => { globalThis.GECBridge.call('old-flush-' + Math.random().toString(36).slice(2), 'flush', JSON.stringify(args)); }, [true]);
  const faultState = async () => (await fetch(job.api_url + '/control/state')).json();
  const deadline = Date.now() + 90000;
  let session = {};
  while (Date.now() < deadline) {
    session = (await faultState()).sessions?.[sessionId] ?? {};
    if (session.batch_requests >= 1) break;
    await page.waitForTimeout(250);
  }
  check(session.batch_requests >= 1, 'the refused batch really reached the server: ' + JSON.stringify(session));
  await page.waitForTimeout(6000);
  session = (await faultState()).sessions?.[sessionId] ?? {};
  check(session.batch_requests === 1, 'the frozen old Web client stopped after the first 403: ' + session.batch_requests);
  check(session.completion_requests === 0, 'the frozen old Web client never sent a completion after the 403: ' + session.completion_requests);
  const rows = await page.evaluate(() => new Promise((resolve, reject) => {
    const r = indexedDB.open('gec-1');
    r.onsuccess = () => { const db = r.result; const tx = db.transaction('sessions'); const q = tx.objectStore('sessions').getAll(); tx.oncomplete = () => { resolve(q.result); db.close(); }; tx.onerror = () => reject(tx.error); };
    r.onerror = () => reject(r.error);
  }));
  const row = rows.find(entry => entry.id === sessionId);
  check(!!row && row.kind === 'session', 'the frozen old Web client kept a live session, not a tombstone: ' + JSON.stringify(row?.kind));
  check((row?.pending?.length ?? 0) === 2 && (row?.records?.length ?? 0) === 2, 'the frozen old Web client kept its unconfirmed records: ' + JSON.stringify([row?.pending?.length, row?.records?.length]));
  check(!row?.complete_ack && row?.paused === true, 'the frozen old Web client paused without a receipt: ' + JSON.stringify([row?.complete_ack, row?.paused]));
  const state = await page.evaluate(() => globalThis.GECBridge.status().state);
  check(state !== 'remote_acknowledged', 'the frozen old Web client never claimed success: ' + state);
} finally {
  const document = {checks, failures};
  fs.writeFileSync(path.join(job.evidence_dir, 'old_web_summary.json'), JSON.stringify(document, null, 2));
  await browser.close().catch(() => {});
  await new Promise(resolve => server.close(resolve));
}
if (failures.length) throw new Error('old Web checks failed: ' + JSON.stringify(failures.map(entry => entry.label)));
'''


class VerificationError(Exception):
    pass


def unique_dir(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = base / f'{stamp}-{secrets.token_hex(4)}'
    target.mkdir(parents=False, exist_ok=False)
    return target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def godot_binary() -> str:
    candidate = os.environ.get('GEP_GODOT_BIN') or shutil.which('godot')
    if candidate is None and Path('/Applications/Godot.app/Contents/MacOS/Godot').is_file():
        candidate = '/Applications/Godot.app/Contents/MacOS/Godot'
    if candidate is None or not Path(candidate).exists():
        raise VerificationError('official local Godot engine not found (GEP_GODOT_BIN, PATH or /Applications/Godot.app)')
    version = subprocess.run([candidate, '--version'], capture_output=True, text=True, timeout=60).stdout.strip()
    if not version.startswith('4.7.2.stable.'):
        raise VerificationError(f'the pinned Godot 4.7.2 is required, found: {version}')
    return candidate


class FaultServer:
    """A real fault server subprocess with a bounded, cross-platform handshake.

    The ready line is read by a daemon reader thread and awaited with a real
    deadline, because a process stdout pipe cannot be waited on with
    ``select`` on Windows. Every failure path kills only this run's owned child
    and reaps it with ``wait`` before the error is raised, so neither a child
    process nor a stalled reader survives the constructor.
    """

    def __init__(self, cwd: Path, timeout: float = 30.0, command: list | None = None):
        self.timeout = timeout
        self._reader = None
        self.process = subprocess.Popen(command or [sys.executable, str(FAULT_APP), '--port', '0'],
                                        cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.port = self._await_ready_line(timeout)
        except BaseException:
            self._shutdown()
            raise
        self.url = f'http://127.0.0.1:{self.port}'

    def _await_ready_line(self, timeout: float) -> int:
        holder: dict = {}

        def read_line():
            try:
                holder['line'] = self.process.stdout.readline()
            except Exception as error:  # the stream may be closed during shutdown
                holder['error'] = error

        self._reader = threading.Thread(target=read_line, name='gec-fault-ready', daemon=True)
        self._reader.start()
        self._reader.join(timeout)
        if self._reader.is_alive():
            raise VerificationError(
                f'the synthetic fault server did not report its port within {timeout:g}s (pid {self.process.pid})')
        if 'error' in holder:
            raise VerificationError(
                f'the fault server ready line could not be read: {holder["error"]} (pid {self.process.pid})')
        line = (holder.get('line') or '').strip()
        if not line:
            raise VerificationError(f'the fault server exited before reporting its port (pid {self.process.pid})')
        try:
            return int(json.loads(line)['port'])
        except (ValueError, KeyError, TypeError):
            raise VerificationError(
                f'the fault server reported an unusable line: {line!r} (pid {self.process.pid})') from None

    def _shutdown(self, graceful: bool = False) -> None:
        """Stop only this run's owned child, wait for it and drop its streams."""
        if self.process.poll() is None:
            if graceful:
                self.process.terminate()
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            else:
                self.process.kill()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def post(self, path, body):
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode('utf-8'),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))

    def script(self, sessions):
        return self.post('/control/script', {'sessions': sessions})

    def state(self):
        with urllib.request.urlopen(self.url + '/control/state', timeout=15) as response:
            return json.loads(response.read().decode('utf-8'))

    def stop(self):
        self._shutdown(graceful=True)


def package_digests(root: Path) -> dict:
    """Package-relative path -> SHA-256 for every file in the source tree."""
    digests = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            digests[str(path.relative_to(root)).replace(os.sep, '/')] = sha256(path)
    return digests


def copy_package_for_run(source: Path, run_root: Path, name: str = 'package_copy') -> Path:
    """One unique byte copy of the read-only source package for a single run.

    ``source`` is input only. The copy is where a synthetic target
    configuration may be replaced for the compatibility run; the source tree is
    never written and its digests are re-checked after the run.
    """
    destination = run_root / name
    shutil.copytree(source, destination, symlinks=True)
    return destination


def resolve_windows_program(root: Path, explicit: str | None,
                            manifest_name: str = DEFAULT_WINDOWS_MANIFEST) -> Path:
    """The expected experiment program, never guessed from the first ``*.exe``.

    The program must be named by ``--program`` or by the package manifest; a
    missing, ambiguous, non-``.exe`` or out-of-package choice is refused instead
    of silently picking a console or helper executable.
    """
    root = root.resolve()
    if explicit:
        candidate = Path(explicit)
        candidate = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    else:
        manifest = root / manifest_name
        if not manifest.is_file():
            raise VerificationError(
                f'no --program was given and the package has no {manifest_name} naming the expected program')
        try:
            document = json.loads(manifest.read_text(encoding='utf-8'))
        except ValueError as error:
            raise VerificationError(f'the package manifest is not valid JSON: {error}') from None
        if not isinstance(document, dict):
            raise VerificationError('the package manifest is not a JSON object')
        programs = document.get('programs')
        if programs is None:
            programs = [document['program']] if isinstance(document.get('program'), str) else None
        if not isinstance(programs, list) or len(programs) != 1 or not isinstance(programs[0], str) or not programs[0]:
            raise VerificationError(
                f'the package manifest must name exactly one program; refusing to guess among {programs!r}')
        candidate = (root / programs[0]).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise VerificationError(f'the named program is outside the package root: {candidate}') from None
    if not candidate.is_file():
        raise VerificationError(f'the named program does not exist: {candidate}')
    if candidate.suffix.lower() != '.exe':
        raise VerificationError(f'the named program is not a Windows executable: {candidate}')
    return candidate


class Run:
    def __init__(self, root: Path):
        self.root = root
        self.checks = []
        self.failures = []
        self.artifacts = {}

    def record(self, ok, label, detail=None):
        entry = {'label': label, 'ok': bool(ok), 'detail': detail}
        self.checks.append(entry)
        if not ok:
            self.failures.append(entry)
        print(('ok   ' if ok else 'FAIL ') + label + (f' :: {detail}' if detail is not None else ''), flush=True)
        return bool(ok)

    def require(self, ok, label, detail=None):
        if not self.record(ok, label, detail):
            raise VerificationError(label)

    def write(self, name, payload):
        path = self.root / name
        if self.root.resolve() not in path.resolve().parents:
            raise VerificationError(f'evidence name escapes the run root: {name!r}')
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return path


def run_process(command, timeout, env=None, cwd=ROOT):
    return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **(env or {})})


def harness_phases(run: Run, server: FaultServer, storage: Path, godot: str, instance: str, study: str, build: str):
    outputs = {}
    for phase in ('main', 'reopen_cleaned', 'reopen_pending'):
        result = run_process([godot, '--headless', '--path', str(PROJECT), '--script', str(HARNESS)], timeout=420,
                             env={'GEP_DELIVERY_FAULT_URL': server.url, 'GEP_SYNTHETIC_STORAGE': str(storage),
                                  'GEP_SHELL_PHASE': phase, 'GEP_SHELL_INSTANCE': instance, 'GEP_SHELL_STUDY': study,
                                  'GEP_SHELL_BUILD': build})
        run.write(f'native_{phase}_stdout.json', {'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:], 'exit': result.returncode})
        run.require(result.returncode == 0 and 'P03R09C_SHELL_VERIFIED' in result.stdout,
                    f'the new macOS native shell phase {phase} verified', result.stdout[-2000:] + result.stderr[-1000:])
        outputs[phase] = result.stdout
    state = server.state()
    run.write('native_fault_state.json', state)
    sessions = state.get('sessions', {})
    cleaned = [entry for entry in sessions.values() if entry['batch_requests'] == 5]
    run.require(len(cleaned) == 1 and len(cleaned[0]['events']) == 2 and cleaned[0]['completion_requests'] == 1,
                'the new native client completed exactly one received session', cleaned)
    deleted_match = re.search(r'P03R09C_DELETED (\S+)', outputs['main'])
    pending_match = re.search(r'P03R09C_PENDING (\S+)', outputs['main'])
    run.require(bool(deleted_match and pending_match), 'the new native harness reported its terminal and pending sessions')
    deleted = sessions[deleted_match.group(1)]
    run.require(deleted['batch_requests'] == 1 and deleted['completion_requests'] == 0 and deleted['events'] == [],
                'the new native deletion terminal sent exactly one refused batch', deleted)
    pending = sessions[pending_match.group(1)]
    run.require(pending['batch_requests'] == 4 and pending['completion_requests'] == 0 and pending['events'] == [],
                'the new native unfinished session kept its four counted failures', pending)


def build_new_web(run: Run, godot: str) -> Path:
    freeze = unique_dir(FREEZE_BASE)
    web = freeze / 'web'
    web.mkdir()
    (web / 'gec').mkdir()
    for name in ('sdk.js', 'bridge.js', 'inputs.js', 'shell.js'):
        shutil.copy2(ROOT / 'packages' / 'gec_web' / name, web / 'gec' / name)
    result = run_process([godot, '--headless', '--path', str(PROJECT), '--export-release', 'Web', str(web / 'index.html')], timeout=300)
    run.require(result.returncode == 0 and (web / 'index.wasm').is_file(), 'the new Web build exported with the pinned Godot',
                result.stdout[-2000:] + result.stderr[-1000:])
    run.artifacts['new_web_build'] = str(web)
    run.write('new_web_build.json', {'freeze': str(freeze), 'web': str(web), 'digests': {
        name: sha256(web / name) for name in ('index.html', 'index.wasm', 'index.pck', 'gec/sdk.js', 'gec/bridge.js', 'gec/shell.js')}})
    return web


def chrome_spec(run: Run, server: FaultServer, web: Path, godot: str):
    context = {
        'config_version': '1', 'protocol_version': 'gep/1', 'sdk_version': '0.1.0', 'purpose': 'synthetic', 'locale': 'zh',
        'api_url': server.url, 'instance_id': secrets.token_hex(16), 'study_id': secrets.token_hex(16),
        'release_id': 'release-1', 'build_id': secrets.token_hex(16), 'mode': 'anonymous',
    }
    chrome = run.root / 'chrome'
    chrome.mkdir()
    job_path = chrome / 'job.json'
    job_path.write_text(json.dumps({'package_dir': str(web), 'evidence_dir': str(chrome),
                                    'api_url': server.url, 'context': context}), encoding='utf-8')
    run.require(PLAYWRIGHT.exists(), 'the pinned Playwright runner is required (pnpm install --frozen-lockfile)')
    report = run_process([str(PLAYWRIGHT), 'test', SPEC, '--reporter=json'], timeout=900, env={'GEP_REMEDIATION_JOB': str(job_path)})
    document = None
    start = report.stdout.find('{')
    if start >= 0:
        try:
            document = json.loads(report.stdout[start:])
        except ValueError:
            document = None
    run.write('playwright_report.json', document or {'stdout': report.stdout[-4000:], 'stderr': report.stderr[-2000:]})
    run.require(report.returncode == 0 and document is not None, 'the real Chrome shell spec ran',
                report.stdout[-2000:] + report.stderr[-1000:])
    stats = document.get('stats', {})
    run.require(stats.get('expected') == 1 and stats.get('unexpected') == 0 and stats.get('skipped') == 0,
                'the real Chrome shell spec passed without skips', stats)
    summary = json.loads((chrome / 'summary.json').read_text())
    run.write('chrome_summary.json', summary)
    run.require(summary['failures'] == [] and len(summary['checks']) >= 35,
                'the real Web shell evidence is complete', summary['failures'])


def old_web_check(run: Run, server: FaultServer, old_zip: Path):
    extract = run.root / 'old_web'
    extract.mkdir()
    with zipfile.ZipFile(old_zip) as archive:
        archive.extractall(extract)
    driver = run.root / 'old_web_driver.mjs'
    driver.write_text(OLD_WEB_DRIVER, encoding='utf-8')
    job_path = run.root / 'old_web_job.json'
    context = {'config_version': '1', 'protocol_version': 'gep/1', 'sdk_version': '0.1.0', 'purpose': 'synthetic',
               'api_url': server.url, 'instance_id': secrets.token_hex(16), 'study_id': secrets.token_hex(16),
               'release_id': 'release-1', 'build_id': secrets.token_hex(16)}
    job_path.write_text(json.dumps({'package_dir': str(extract), 'evidence_dir': str(run.root), 'api_url': server.url,
                                    'context': context}), encoding='utf-8')
    server.script({'*': {'events': [{'kind': 'fail', 'status': 403, 'code': 'study_deleted', 'retryable': False}]}})
    result = run_process(['node', str(driver), str(job_path)], timeout=300)
    run.write('old_web_driver_stdout.json', {'stdout': result.stdout[-6000:], 'stderr': result.stderr[-4000:], 'exit': result.returncode})
    run.require(result.returncode == 0, 'the frozen old Web package stopped safely on the 403',
                result.stdout[-2000:] + result.stderr[-1500:])
    summary = json.loads((run.root / 'old_web_summary.json').read_text())
    run.require(summary['failures'] == [], 'the frozen old Web checks all passed', summary['failures'])


def old_native_check(run: Run, server: FaultServer, old_zip: Path):
    extract = run.root / 'old_native'
    extract.mkdir()
    with zipfile.ZipFile(old_zip) as archive:
        archive.extractall(extract)
    app = next(extract.glob('*.app'))
    binary = app / 'Contents' / 'MacOS' / app.stem
    os.chmod(binary, 0o755)
    config = {'config_version': '1', 'protocol_version': 'gep/1', 'sdk_version': '0.1.0', 'purpose': 'synthetic',
              'api_url': server.url, 'instance_id': secrets.token_hex(16), 'study_id': secrets.token_hex(16),
              'release_id': 'release-1', 'build_id': secrets.token_hex(16)}
    (extract / 'connection.json').write_text(json.dumps(config), encoding='utf-8')
    storage = run.root / 'old_native_storage'
    storage.mkdir()
    server.script({'*': {'events': [{'kind': 'fail', 'status': 403, 'code': 'study_deleted', 'retryable': False}]}})
    try:
        result = subprocess.run([str(binary), '--headless', '--', '--synthetic-auto'], cwd=str(extract),
                                env={**os.environ, 'GEP_SYNTHETIC_STORAGE': str(storage)},
                                capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        run.write('old_native_stdout.json', {'stdout': (error.stdout or '')[-4000:], 'timeout': True})
        run.require(False, 'the frozen old native program stopped by itself after the 403', 'timeout')
        return
    run.write('old_native_stdout.json', {'stdout': result.stdout[-6000:], 'stderr': result.stderr[-3000:], 'exit': result.returncode})
    run.require('SYNTHETIC_DONE' not in result.stdout, 'the frozen old native program never claimed done', result.stdout[-2000:])
    run.require('SYNTHETIC_TIMEOUT' in result.stdout and 'http_403' in result.stdout,
                'the frozen old native program surfaced the 403 as a generic error', result.stdout[-2000:])
    state = server.state()
    run.write('old_native_fault_state.json', state)
    matching = [entry for entry in state.get('sessions', {}).values()
                if entry.get('instance_id') == config['instance_id'] and entry.get('study_id') == config['study_id']]
    run.require(len(matching) == 1, 'the frozen old native program made exactly one observed session', matching)
    session = matching[0]
    run.require(session['batch_requests'] == 1 and session['completion_requests'] == 0 and session['events'] == [],
                'the frozen old native program stopped after the first 403', session)
    connection = sqlite3.connect(storage / 'queue.sqlite')
    try:
        rows = [json.loads(value) for _, value in connection.execute('SELECT id, value FROM sessions')]
    finally:
        connection.close()
    live = [row for row in rows if row.get('kind') == 'session']
    run.require(len(live) == 1, 'the frozen old native program kept one live session, not a tombstone',
                [row.get('kind') for row in rows])
    row = live[0]
    run.require(len(row.get('pending', [])) == 4 and len(row.get('records', [])) == 4 and row.get('paused') is True
                and row.get('complete_ack') is None,
                'the frozen old native program kept its unconfirmed records and paused',
                {'kind': row.get('kind'), 'paused': row.get('paused'), 'attempts': row.get('attempts'),
                 'pending': len(row.get('pending', [])), 'records': len(row.get('records', []))})


def verify_local() -> int:
    run = Run(unique_dir(EVIDENCE_BASE))
    print(f'evidence root: {run.root}', flush=True)
    try:
        godot = godot_binary()
        run.require(VENV_PYTHON.exists(), 'the project virtualenv python exists')
        run.require(PLAYWRIGHT.exists(), 'the pinned Playwright runner exists')
        run.require(OLD_WEB_ZIP.is_file() and OLD_NATIVE_ZIP.is_file(), 'the frozen old acceptance packages exist')
        old_digests_before = {'web': sha256(OLD_WEB_ZIP), 'native': sha256(OLD_NATIVE_ZIP)}
        server = FaultServer(ROOT)
        try:
            storage = run.root / 'native' / 'storage'
            storage.mkdir(parents=True)
            harness_phases(run, server, storage, godot, secrets.token_hex(16), secrets.token_hex(16), secrets.token_hex(16))
            web = build_new_web(run, godot)
            chrome_spec(run, server, web, godot)
            old_web_check(run, server, OLD_WEB_ZIP)
            old_native_check(run, server, OLD_NATIVE_ZIP)
        finally:
            server.stop()
        old_digests_after = {'web': sha256(OLD_WEB_ZIP), 'native': sha256(OLD_NATIVE_ZIP)}
        run.require(old_digests_before == old_digests_after, 'the frozen old packages were not modified',
                    {'before': old_digests_before, 'after': old_digests_after})
        windows = {'ran': False, 'reason': 'a local macOS command is not a Windows pass',
                   'r11_command': 'python tools/remediation_gec.py --verify-windows --package <new complete Windows package directory> '
                                  '[--program <expected program relative to the package root>] [--web-package <archive>]',
                   'r11_source_policy': 'the given package directory is a read-only source; the tool copies it to a unique run '
                                        'directory, replaces only that copy for the compatibility run and re-checks every source '
                                        'file digest afterwards, so an end-to-end run of the original frozen package is still required'}
        run.write('windows_not_run.json', windows)
        document = {'verify': 'local', 'ok': not run.failures, 'checks': run.checks, 'failures': run.failures,
                    'artifacts': run.artifacts, 'windows': windows}
        run.write('summary.json', document)
        print(f"local verification: {'PASS' if not run.failures else 'FAIL'} ({len(run.checks)} checks, {len(run.failures)} failed)", flush=True)
        print('Windows: NOT RUN locally; R11 must execute --verify-windows on the real host', flush=True)
        return 0 if not run.failures else 1
    except VerificationError as error:
        document = {'verify': 'local', 'ok': False, 'checks': run.checks, 'failures': run.failures,
                    'artifacts': run.artifacts, 'error': str(error)}
        run.write('summary.json', document)
        print(f'local verification: FAIL :: {error}', flush=True)
        return 1


def verify_windows(package: str | None, web_package: str | None, program: str | None) -> int:
    if not sys.platform.startswith('win'):
        print('--verify-windows must run on Windows: a cross-compile or a local command is not a Windows pass', flush=True)
        return 2
    run = Run(unique_dir(EVIDENCE_BASE))
    print(f'evidence root: {run.root}', flush=True)
    root = None
    source_digests = {}
    error = None
    try:
        root = Path(package).expanduser().resolve() if package else None
        run.require(root is not None and root.is_dir(), 'the new complete Windows package directory was provided')
        # The provided package is a read-only source: hash every file, never
        # write it, copy it once for the run and re-check the digests afterwards.
        source_digests = package_digests(root)
        run.require(bool(source_digests), 'the source package contains files', list(source_digests))
        program_source = resolve_windows_program(root, program)
        run.record(True, 'the expected program was named explicitly or by the manifest', str(program_source))
        copy_root = copy_package_for_run(root, run.root)
        copy_digests = package_digests(copy_root)
        run.require(copy_digests == source_digests, 'the unique run copy is byte-identical to the read-only source',
                    {'source_files': len(source_digests), 'copy_files': len(copy_digests)})
        program_copy = copy_root / program_source.relative_to(root)
        config = {'config_version': '1', 'protocol_version': 'gep/1', 'sdk_version': '0.1.0', 'purpose': 'synthetic',
                  'api_url': '', 'instance_id': secrets.token_hex(16), 'study_id': secrets.token_hex(16),
                  'release_id': 'release-1', 'build_id': secrets.token_hex(16)}
        storage = run.root / 'storage'
        storage.mkdir()
        server = FaultServer(ROOT)
        try:
            config['api_url'] = server.url
            config_path = program_copy.parent / 'connection.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')
            server.script({'*': {'events': [{'kind': 'fail', 'status': 403, 'code': 'study_deleted', 'retryable': False}]}})
            try:
                result = subprocess.run([str(program_copy), '--headless', '--', '--synthetic-auto'], cwd=str(copy_root),
                                        env={**os.environ, 'GEP_SYNTHETIC_STORAGE': str(storage)},
                                        capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired as error_object:
                run.write('native_stdout.json', {'stdout': (error_object.stdout or '')[-4000:], 'timeout': True})
                run.require(False, 'the new Windows program stopped by itself after the 403', 'timeout')
                result = None
            if result is not None:
                run.write('native_stdout.json', {'stdout': result.stdout[-6000:], 'stderr': result.stderr[-3000:], 'exit': result.returncode})
                run.require('SYNTHETIC_DONE' not in result.stdout, 'the new Windows program never claimed done', result.stdout[-2000:])
                run.require('SYNTHETIC_TIMEOUT' in result.stdout and 'http_403' in result.stdout,
                            'the new Windows program surfaced the 403 as a generic error', result.stdout[-2000:])
            state = server.state()
            run.write('fault_state.json', state)
            matching = [entry for entry in state.get('sessions', {}).values()
                        if entry.get('instance_id') == config['instance_id'] and entry.get('study_id') == config['study_id']]
            run.require(len(matching) == 1, 'the new Windows program made exactly one observed session', matching)
            session = matching[0]
            run.require(session['batch_requests'] == 1 and session['completion_requests'] == 0,
                        'the new Windows program stopped after the first 403', session)
        finally:
            server.stop()
        connection = sqlite3.connect(storage / 'queue.sqlite')
        try:
            rows = [json.loads(value) for _, value in connection.execute('SELECT id, value FROM sessions')]
        finally:
            connection.close()
        live = [row for row in rows if row.get('kind') == 'session']
        run.require(len(live) == 1 and live[0].get('paused') is True and live[0].get('complete_ack') is None,
                    'the new Windows program kept its unconfirmed records and paused', rows)
        changed = sorted(name for name, digest in package_digests(copy_root).items() if copy_digests.get(name) != digest)
        run.require(changed == [str(config_path.relative_to(copy_root))],
                    'only the synthetic connection configuration changed, and only in the run copy',
                    {'changed': changed, 'config_in_run_copy': str(config_path.relative_to(copy_root))})
        run.write('package_evidence.json', {
            'source_package': str(root), 'source_read_only': True,
            'run_copy': str(copy_root), 'program_source': str(program_source), 'program_run_copy': str(program_copy),
            'config_replaced_in_run_copy': str(config_path.relative_to(copy_root)),
            'evidence_classes': {
                'source_integrity': 'the original package files are hashed before and after the run; any change fails here',
                'compatible_config_run': 'the synthetic connection configuration was replaced only inside the unique run copy; '
                                         'this is a compatibility run over a copy, not an end-to-end run of the original frozen '
                                         'connection package'},
            'original_frozen_package_end_to_end_run': 'still required on the real host (R11)'})
        if web_package:
            run.require(Path(web_package).is_file(), 'the new Windows Web package archive exists')
            run.record(True, 'the Windows Web package was provided; R11 WN01-WN06 still owns the real Web run')
        else:
            run.record(False, 'the new Windows Web package was not provided; R11 WN01-WN06 must still run it')
    except VerificationError as failure:
        error = str(failure)
    finally:
        if root is not None:
            try:
                unchanged = package_digests(root) == source_digests
                run.record(unchanged, 'the read-only source package stayed byte-identical', {'files': len(source_digests)})
            except OSError as digest_failure:
                run.record(False, 'the read-only source package digest check could not confirm safety', str(digest_failure))
    document = {'verify': 'windows', 'ok': not run.failures, 'checks': run.checks, 'failures': run.failures,
                'package': str(root) if root is not None else None, 'error': error,
                'source_package_read_only': True,
                'evidence_note': 'the source-package integrity check and the compatible-config run copy are separate '
                                 'evidence classes; this summary never claims an end-to-end run of the original frozen package'}
    run.write('summary.json', document)
    if error is not None and not run.failures:
        print(f'Windows verification: FAIL :: {error}', flush=True)
        return 1
    print(f"Windows verification: {'PASS' if not run.failures else 'FAIL'}", flush=True)
    return 0 if not run.failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--verify-local', action='store_true', help='run the macOS/Web clients and the frozen-package 403 checks')
    mode.add_argument('--verify-windows', action='store_true', help='run the same strategy on a new complete Windows package (Windows only)')
    parser.add_argument('--package', help='the new complete Windows package directory (--verify-windows)')
    parser.add_argument('--program', help='the expected experiment program inside the package, relative to its root (--verify-windows; without it the package manifest must name it)')
    parser.add_argument('--web-package', help='the new Windows Web package archive, when available (--verify-windows)')
    args = parser.parse_args()
    if args.verify_windows:
        return verify_windows(args.package, args.web_package, args.program)
    return verify_local()


if __name__ == '__main__':
    sys.exit(main())
