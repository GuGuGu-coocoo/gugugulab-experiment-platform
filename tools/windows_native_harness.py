#!/usr/bin/env python3
"""Windows x64 native engineering harness (03F).

This script runs on the real Windows machine. It never asks a question and never
waits for a human: a missing prerequisite is recorded as ``BLOCKED``/``NOT_RUN``
with the exact condition, never as a pass and never as a silent skip. It is
standard-library only, so the pinned test-machine Python 3.12 is enough.

It runs the *real exported Windows program* headless-of-human (the program's own
``--synthetic-auto`` automation input, the same submission path a real button
press takes) against the real API of the bound isolated instance, with the real
native SQLite store (``queue.sqlite`` plus its ``writer.sqlite`` lock), and
reconciles the results against the authorized JSONL export of the platform.

Modes:

``--doctor``
    Host, kit, accounts-ACL and layout checks without executing the program. It
    never claims a WN01 result: preparation-only evidence stays preparation-only.

``--run``
    Full engineering pass. WN01 launches the real executable in the interactive
    session without waiting for the program to end, verifies the window of *that*
    PID and stops only that PID after re-checking its full identity. WN02 drives
    the three frozen modes (wrong credentials and wrong binding refused).
    WN03/WN04/WN05 use a scoped fault proxy (the frozen port on this machine) to
    inject offline commits, a lost acknowledgement, a process kill, short-code
    recovery, expiry, a shared writer and data-only recovery, and reconcile the
    local store, the API and the authorized JSONL export by event id and raw
    value.

``--designer-launch``
    Prepared launcher for the designer's own manual experience: extracts one
    frozen mode next to its ``connection.json`` and starts the real program with a
    per-mode local store. It never records an engineering result.

``--fault-proxy``
    Internal scoped fault-injection proxy (loopback only): it owns the frozen
    port and forwards to the scoped SSH tunnel, and can drop event-batch
    requests, lose one acknowledgement, or refuse every connection.

Exit code 0 only when every executed check passed. A run without the runtime
prerequisite (the scoped tunnel) records the exact blocker and exits non-zero.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

HARNESS_FORMAT = "gep-windows-run/v1"
KIT_VERSION = "gep-windows-kit/v2"
RUNTIME_FORMAT = "gep-windows-runtime/v1"
ACCOUNTS_FORMAT = "gep-windows-accounts/v1"
SIDECAR_NAME = "integrity.json"
MODES = ("anonymous", "id", "password")
CASE_TITLES = {
    "WN01": "真实 Windows x64 交互启动（自有 PID、窗口、路径、架构）",
    "WN02": "三种冻结模式真实准入、错误凭据与错误绑定拒绝",
    "WN03": "本地保存、断网继续、重连补传、丢 ACK、进程终止与重开",
    "WN04": "检查点恢复、新 epoch、短码+设备证明、重放/过期/共享写锁/清理/仅数据恢复",
    "WN05": "失败数据导出无秘密与本地/API/授权导出逐 ID 逐值对账",
    "WN06": "篡改/权限/错平台/缺依赖与旧发行兼容（平台侧 + 客户端侧）",
}
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED = "BLOCKED"
PE_MACHINE = {0x8664: "x86_64", 0xAA64: "arm64", 0x14C: "i386"}
SECRET_PATTERNS = (re.compile(rb"BEGIN (RSA|OPENSSH|EC|PRIVATE) PRIVATE KEY"),
                   re.compile(rb"password\s*[:=]\s*[\"'][^\"'\s]{4,}", re.IGNORECASE),
                   re.compile(rb"token\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}", re.IGNORECASE))
RAW_VALUES = (321.5, 217.25)
BROAD_PRINCIPALS = ("everyone", "builtin\\users", "users", "authenticated users", "nt authority\\authenticated users")
EXPECTED_EVENTS_PER_SESSION = 4


class HarnessError(Exception):
    """A real prerequisite or the program itself failed (never a silent skip)."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def canonical_json(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def redact(arguments):
    """Arguments as recorded evidence: credentials never enter the report."""
    redacted = []
    for value in arguments:
        text = str(value)
        for flag in ("--password=", "--permit=", "--short-code="):
            if text.startswith(flag):
                text = flag + "***"
        redacted.append(text)
    return redacted


def pe_summary(raw):
    if len(raw) < 0x40 or raw[:2] != b"MZ":
        raise HarnessError("missing MZ header")
    offset = struct.unpack_from("<I", raw, 0x3C)[0]
    if not 0x40 <= offset or offset + 24 > len(raw) or raw[offset:offset + 4] != b"PE\x00\x00":
        raise HarnessError("missing PE signature")
    machine = struct.unpack_from("<H", raw, offset + 4)[0]
    characteristics = struct.unpack_from("<H", raw, offset + 22)[0]
    magic = struct.unpack_from("<H", raw, offset + 24)[0]
    return {"machine": PE_MACHINE.get(machine, hex(machine)), "magic": magic,
            "executable": bool(characteristics & 0x0002), "dll": bool(characteristics & 0x2000)}


def pck_version(raw):
    if len(raw) < 20 or raw[:4] != b"GDPC":
        raise HarnessError("missing GDPC PCK magic")
    return [struct.unpack_from("<I", raw, 8 + 4 * index)[0] for index in range(3)]


def run_powershell_command(command, timeout=120):
    """Run one PowerShell command and read its output as UTF-8.

    A redirected PowerShell 5.1 host writes in the console output encoding, which
    corrupts non-ASCII paths and window titles on a machine whose locale cannot
    represent them; forcing UTF-8 on both sides keeps Chinese/space paths exact
    (the Windows acceptance requires a real Chinese path).
    """
    wrapped = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + command
    return subprocess.run(["powershell", "-NoProfile", "-Command", wrapped],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def ps_quote(value):
    """One PowerShell single-quoted literal; apostrophes are doubled."""
    return "'" + str(value).replace("'", "''") + "'"


def cmd_path_quote(value):
    """One quoted path for a ``.cmd`` redirection target.

    The wrapper is a command file (never a PowerShell script file), so the
    redirection target follows ``cmd`` rules: quote the path when it contains a
    separator or any character the shell would interpret.
    """
    text = str(value)
    if any(character in text for character in ' \t()&^<>|,"'):
        return '"' + text.replace('"', '""') + '"'
    return text


def active_console_session():
    """The real interactive console session id, or None when nobody is signed in."""
    try:
        result = subprocess.run(["query", "session"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (result.stdout or "").splitlines():
        tokens = line.split()
        if len(tokens) < 4:
            continue
        name, state = tokens[0].lstrip(">"), tokens[3]
        if name.lower() == "console" and state.lower() == "active":
            try:
                return int(tokens[2])
            except ValueError:
                continue
    return None


# ------------------------------------------------------------------ HTTP client
class Api:
    """Minimal authenticated HTTP client against the bound isolated instance."""

    def __init__(self, port, timeout=60):
        self.port = port
        self.timeout = timeout
        self.cookies = {}
        self.csrf = ""
        self.records = []

    def _remember(self, response):
        for header, value in response.getheaders():
            if header.lower() == "set-cookie":
                pair = value.split(";", 1)[0]
                if "=" in pair:
                    name, _, cookie = pair.partition("=")
                    self.cookies[name.strip()] = cookie.strip()
                    if name.strip() == "csrftoken":
                        self.csrf = cookie.strip()

    def request(self, method, path, host="admin.localhost", body=None, headers=None, timeout=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout or self.timeout)
        merged = {"Host": host, "Accept": "application/json"}
        if self.cookies:
            merged["Cookie"] = "; ".join(f"{name}={value}" for name, value in sorted(self.cookies.items()))
        if self.csrf:
            # Session-authenticated POSTs (the authorized export) pass Django's
            # CSRF middleware; this is the same token the browser form carries.
            merged["X-CSRFToken"] = self.csrf
        if headers:
            merged.update(headers)
        try:
            connection.request(method, path, body=body, headers=merged)
            response = connection.getresponse()
            payload = response.read()
            self._remember(response)
            self.records.append({"method": method, "path": path, "host": host, "status": response.status})
            return response.status, payload, {name.lower(): value for name, value in response.getheaders()}
        finally:
            connection.close()

    def login(self, username, password):
        status, payload, _ = self.request("GET", "/login")
        if status != 200:
            raise HarnessError(f"login page unavailable (HTTP {status})")
        match = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', payload)
        if match:
            self.csrf = match.group(1).decode()
        form = f"username={_urlquote(username)}&password={_urlquote(password)}&csrfmiddlewaretoken={_urlquote(self.csrf)}"
        status, payload, _ = self.request("POST", "/login", body=form.encode(),
                                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        if status not in (301, 302, 303) or "gep_admin" not in self.cookies:
            raise HarnessError(f"login failed for {username!r} (HTTP {status})")

    def post_form(self, path, fields):
        body = "&".join(f"{_urlquote(name)}={_urlquote(value)}" for name, value in fields)
        body += f"&csrfmiddlewaretoken={_urlquote(self.csrf)}"
        return self.request("POST", path, body=body.encode(),
                            headers={"Content-Type": "application/x-www-form-urlencoded"})

    def export_jsonl(self, study_id):
        status, payload, _ = self.request("POST", "/v1/admin/exports", body=canonical_json({"study_id": study_id}),
                                          headers={"Content-Type": "application/json"})
        if status != 201:
            raise HarnessError(f"export creation returned {status}: {payload[:200].decode('utf-8', 'replace')}")
        export_id = json.loads(payload)["export_id"]
        status, payload, _ = self.request("GET", f"/v1/admin/exports/{export_id}/download?format=jsonl")
        if status != 200:
            raise HarnessError(f"export download returned {status}")
        return export_id, payload


def _urlquote(value):
    from urllib.parse import quote
    return quote(str(value), safe="")


# ------------------------------------------------------------------ evidence
class Case:
    def __init__(self, case_id, title):
        self.id = case_id
        self.title = title
        self.checks = []
        self.evidence = {}
        self.status = STATUS_NOT_RUN
        self.reason = ""

    def check(self, ok, label, detail=None):
        entry = {"ok": bool(ok), "label": label, "detail": detail}
        self.checks.append(entry)
        print(("ok   " if ok else "FAIL ") + f"{self.id} " + label
              + (f" :: {detail}" if detail is not None else ""), flush=True)
        return bool(ok)

    def require(self, ok, label, detail=None):
        if not self.check(ok, label, detail):
            self.status = STATUS_FAIL
            raise HarnessError(f"{self.id}: {label}")
        return True

    def block(self, reason):
        self.status = STATUS_BLOCKED
        self.reason = reason
        return self

    def finish(self):
        if self.status in (STATUS_BLOCKED, STATUS_FAIL):
            return self.status
        failures = [check for check in self.checks if not check["ok"]]
        self.status = STATUS_FAIL if failures else (STATUS_PASS if self.checks else STATUS_NOT_RUN)
        if self.status == STATUS_NOT_RUN:
            self.reason = self.reason or "no check was executed"
        return self.status

    def document(self):
        return {"id": self.id, "title": self.title, "status": self.status, "reason": self.reason,
                "checks": self.checks, "evidence": self.evidence}


# ------------------------------------------------------------------ local store
def store_paths(storage):
    """The explicit verified native store paths of one program data directory."""
    return Path(storage) / "queue.sqlite", Path(storage) / "writer.sqlite"


def read_store(storage):
    """Read the native store read-only; fixed SQL, bound parameters only."""
    queue, writer = store_paths(storage)
    result = {"queue": str(queue), "writer": str(writer), "exists": queue.is_file(),
              "writer_exists": writer.is_file(), "rows": [], "sessions": []}
    if not queue.is_file():
        return result
    uri = queue.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=20)
    try:
        for (value,) in connection.execute("SELECT value FROM sessions ORDER BY id"):
            result["rows"].append(value)
    finally:
        connection.close()
    for raw in result["rows"]:
        try:
            document = json.loads(raw)
        except ValueError:
            continue
        if isinstance(document, dict):
            result["sessions"].append(document)
    return result


def store_records(document):
    """Every recorded event envelope of one stored session row."""
    records = document.get("records")
    return [record for record in records if isinstance(record, dict)] if isinstance(records, list) else []


def store_event_ids(document):
    return [record.get("event_id") for record in store_records(document)]


def raw_values(document):
    values = []
    for record in store_records(document):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if isinstance(payload.get("rt_ms"), (int, float)):
            values.append(float(payload["rt_ms"]))
    return values


# ------------------------------------------------------------------ fault proxy
class FaultProxy:
    """Scoped loopback fault injection between the program and the SSH tunnel."""

    def __init__(self, listen_port, target_port, mode="pass", log_path=None):
        self.listen_port = listen_port
        self.target_port = target_port
        self.mode = mode
        self.log_path = Path(log_path) if log_path else None
        self.events = []
        self._server = None
        self._thread = None
        self._lost_ack = False

    def log(self, message):
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "message": message, "mode": self.mode}
        self.events.append(entry)
        print(f"[fault-proxy] {message}", flush=True)
        if self.log_path:
            self.log_path.write_text(json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self):
        proxy = self

        class Handler(__import__("socketserver").BaseRequestHandler):
            def handle(self):
                proxy.serve(self.request)

        import socketserver

        class ReusableServer(socketserver.ThreadingTCPServer):
            # The harness starts and stops the proxy many times on the same
            # frozen port during one run; without this a rebind can fail.
            allow_reuse_address = True
            daemon_threads = True

        self._server = ReusableServer(("127.0.0.1", self.listen_port), Handler, bind_and_activate=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.log(f"listening 127.0.0.1:{self.listen_port} -> 127.0.0.1:{self.target_port} mode={self.mode}")
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self.log("stopped")

    @staticmethod
    def _read_head(stream):
        head = bytearray()
        while b"\r\n\r\n" not in head:
            chunk = stream.recv(1)
            if not chunk:
                return bytes(head)
            head.extend(chunk)
            if len(head) > 65536:
                raise HarnessError("oversized request head")
        return bytes(head)

    def serve(self, connection):
        try:
            head = self._read_head(connection)
            if not head:
                return
            request_line = head.split(b"\r\n", 1)[0].decode("utf-8", "replace")
            parts = request_line.split(" ")
            path = parts[1] if len(parts) > 1 else ""
            if self.mode == "offline":
                self.log(f"refused {request_line}")
                connection.close()
                return
            if self.mode == "drop-events" and "/event-batches" in path:
                self.log(f"dropped {request_line}")
                connection.close()
                return
            if self.mode == "drop-completion" and path.endswith("/completion"):
                self.log(f"dropped {request_line}")
                connection.close()
                return
            upstream = socket.create_connection(("127.0.0.1", self.target_port), timeout=30)
            upstream.sendall(head)
            content_length = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    content_length = int(line.split(b":", 1)[1].strip())
            remaining = content_length
            while remaining > 0:
                chunk = connection.recv(min(65536, remaining))
                if not chunk:
                    break
                upstream.sendall(chunk)
                remaining -= len(chunk)
            lose = self.mode == "lose-first-ack" and "/event-batches" in path and not self._lost_ack
            if lose:
                self._lost_ack = True
                self.log(f"losing acknowledgement of {request_line}")
                upstream.settimeout(30)
                try:
                    while upstream.recv(65536):
                        pass
                except OSError:
                    pass
                upstream.close()
                connection.close()
                return
            self.log(f"relayed {request_line}")
            self._pump(connection, upstream)
        except (OSError, HarnessError) as error:
            self.log(f"proxy error: {type(error).__name__}: {error}")
        finally:
            try:
                connection.close()
            except OSError:
                pass

    @staticmethod
    def _pump(left, right):
        import select
        sockets = [left, right]
        try:
            while True:
                readable, _, _ = select.select(sockets, [], [], 60)
                if not readable:
                    break
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (right if source is left else left).sendall(data)
        except OSError:
            return


def fault_proxy_main(args):
    proxy = FaultProxy(args.listen_port, args.target_port, mode=args.proxy_mode, log_path=args.fault_log)
    proxy.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        proxy.stop()
    return 0


# ------------------------------------------------------------------ harness
class Launch:
    """One owned program launch.

    ``start_program`` is non-blocking: it starts the launcher and waits only for
    the launch record (pid, executable path, console session, start time, window
    title). The launcher keeps running while the program runs, so the harness
    holds it here together with the scheduled task and the script file it owns;
    ``close_launch`` releases exactly those objects after the program has ended
    or after a clear failure.
    """

    def __init__(self, label, entry, storage, arguments, record_path, exit_path,
                 out_path, err_path, script_path, interactive):
        self.label = label
        self.entry = str(entry)
        self.storage = str(storage)
        self.arguments = list(arguments)
        self.record_path = Path(record_path)
        self.exit_path = Path(exit_path)
        self.out_path = Path(out_path)
        self.err_path = Path(err_path)
        self.script_path = Path(script_path)
        self.wrapper_path = None
        self.interactive = interactive
        self.record = {}
        self.launcher = None
        self.task_name = None
        self.launch_exit = None
        self.closed = False


class Harness:
    def __init__(self, kit=None, runtime=None, accounts=None, run_root=None, json_out=None,
                 local_data=None, mode=None, tunnel_port=None, wait_seconds=30, expiry_wait=None,
                 designer=False):
        self.kit = Path(kit).resolve() if kit else None
        self.runtime_path = Path(runtime).resolve() if runtime else None
        self.accounts_path = Path(accounts).resolve() if accounts else None
        self.run_root = Path(run_root).resolve() if run_root else None
        self.json_out = Path(json_out).resolve() if json_out else None
        self.local_data = Path(local_data).resolve() if local_data else None
        self.mode = mode
        self.tunnel_port = tunnel_port
        self.wait_seconds = wait_seconds
        self.expiry_wait = expiry_wait
        self.designer = designer
        self.cases = {}
        self.artifacts = {}
        self.notes = []
        self.host = {}
        self.runtime = {}
        self.accounts = {}
        self.releases = {}
        self.expected = {}
        self.api = None
        self.proxy = None
        self.launches = []
        self.run_id = uuid.uuid4().hex[:12]

    # ------------------------------------------------------------------ setup
    def log(self, message):
        print(f"[windows-native-harness] {message}", flush=True)

    def case(self, case_id):
        if case_id not in self.cases:
            self.cases[case_id] = Case(case_id, CASE_TITLES[case_id])
        return self.cases[case_id]

    def evidence_dir(self, name):
        target = self.run_root / "evidence" / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    def rel(self, path):
        """Evidence paths travel with the run directory, never as Windows absolutes."""
        try:
            return Path(path).resolve().relative_to(self.run_root).as_posix()
        except ValueError:
            return str(path)

    def snapshot_store(self, label, storage):
        """Preserve the raw store bytes and derive a self-contained snapshot.

        The store runs in WAL mode, so the main database file alone can miss
        schema and rows that still live in the ``-wal`` file (a killed program
        never checkpoints). Two artifacts are kept strictly apart:

        * the **raw copies** of every store file (including ``-wal``/``-shm``)
          are written byte for byte into the evidence directory and digested, so
          the original evidence can never be replaced by a derived artifact;
        * the **derived snapshot** the strict gate reads is produced with the
          SQLite backup API from a scratch copy of those bytes, so it is
          transaction consistent and self-contained (no ``-wal`` sidecar) even
          though the live store was never checkpointed.

        A locked, unreadable or corrupt store raises instead of yielding a
        snapshot that silently lost committed rows: the raw copies stay in place
        for inspection and the case fails. The live store itself is only read.
        """
        target = self.evidence_dir(label)
        working = target / "working"
        if working.exists():
            shutil.rmtree(working)
        working.mkdir(parents=True)
        copies = {"derivation": "sqlite-backup", "originals": {}}
        for name in ("queue.sqlite", "queue.sqlite-wal", "queue.sqlite-shm", "writer.sqlite"):
            source = Path(storage) / name
            if not source.is_file():
                continue
            original = target / name
            shutil.copy2(source, original)
            copies["originals"][name] = {"path": self.rel(original),
                                         "sha256": sha256_file(original),
                                         "size": original.stat().st_size}
            shutil.copy2(source, working / name)
        queue = working / "queue.sqlite"
        if not queue.is_file():
            raise HarnessError(f"native store snapshot source missing: {storage}/queue.sqlite")
        snapshot = target / "queue.snapshot.sqlite"
        if snapshot.exists():
            snapshot.unlink()
        try:
            source = sqlite3.connect(str(queue), timeout=30)
            try:
                state = source.execute("PRAGMA integrity_check").fetchone()
                if not state or str(state[0]).lower() != "ok":
                    raise HarnessError(f"store copy failed integrity_check: {state}")
                rows_source = [row[0] for row in source.execute("SELECT value FROM sessions ORDER BY id")]
                derived = sqlite3.connect(str(snapshot))
                try:
                    source.backup(derived)
                    derived.execute("PRAGMA journal_mode=DELETE")
                    derived.commit()
                    state = derived.execute("PRAGMA integrity_check").fetchone()
                    if not state or str(state[0]).lower() != "ok":
                        raise HarnessError(f"derived snapshot failed integrity_check: {state}")
                    rows_derived = [row[0] for row in derived.execute("SELECT value FROM sessions ORDER BY id")]
                finally:
                    derived.close()
            finally:
                source.close()
        except sqlite3.Error as error:
            raise HarnessError(f"consistent snapshot derivation failed for {storage} "
                               f"({type(error).__name__}: {error}); the preserved raw copies stay for inspection") from error
        if rows_derived != rows_source:
            raise HarnessError("derived snapshot does not reproduce the preserved store rows")
        shutil.rmtree(working, ignore_errors=True)
        copies["queue.sqlite"] = self.rel(snapshot)
        copies["snapshot"] = {"path": self.rel(snapshot), "sha256": sha256_file(snapshot),
                              "size": snapshot.stat().st_size, "journal_mode": "delete",
                              "integrity_check": "ok"}
        return copies

    def scoped_export(self, payload, session_id, target):
        """Preserve the whole authorized download, then derive the session subset.

        The platform export endpoint is study-scoped, while the strict gate
        validates one session's rows (event ids, raw values, reconciliation). The
        full download is therefore written byte for byte next to the derived file
        (``<name>-full<suffix>``) and digested; the derived file carries exactly
        the downloaded lines of the session under test, in download order. Nothing
        is rewritten or synthesized, and the derivation stays re-checkable because
        the original download is preserved.
        """
        target = Path(target)
        full = target.with_name(f"{target.stem}-full{target.suffix}")
        full.write_bytes(payload)
        kept, selected = [], []
        for line in payload.decode("utf-8").splitlines():
            if not line.strip():
                continue
            document = json.loads(line)
            if (document.get("record") or {}).get("session_id") == session_id:
                kept.append(line)
                selected.append(document)
        target.write_bytes(("\n".join(kept) + ("\n" if kept else "")).encode("utf-8"))
        info = {"path": self.rel(full), "sha256": sha256_file(full), "size": full.stat().st_size,
                "rows_total": len([line for line in payload.decode("utf-8").splitlines() if line.strip()]),
                "rows_kept": len(kept), "subset_path": self.rel(target),
                "subset_sha256": sha256_file(target)}
        return selected, info

    def host_facts(self):
        version = sys.getwindowsversion() if hasattr(sys, "getwindowsversion") else None
        facts = {"platform": sys.platform,
                 "processor_architecture": os.environ.get("PROCESSOR_ARCHITECTURE", "unknown"),
                 "python": sys.version.split()[0], "user": os.environ.get("USERNAME", "unknown"),
                 "session_name": os.environ.get("SESSIONNAME", ""),
                 "windows_build": f"{version.major}.{version.minor}.{version.build}" if version else "unknown",
                 "interactive": bool(os.environ.get("SESSIONNAME", "").startswith("Console")),
                 "console_session": active_console_session(), "run_id": self.run_id}
        if sys.platform == "win32":
            result = run_powershell_command(
                "$os=Get-CimInstance Win32_OperatingSystem;$cs=Get-CimInstance Win32_ComputerSystem;"
                "[pscustomobject]@{Caption=$os.Caption;Version=$os.Version;Build=$os.BuildNumber;"
                "Arch=$os.OSArchitecture;System=$cs.SystemType}|ConvertTo-Json -Compress")
            if result.returncode == 0:
                for line in (result.stdout or "").splitlines():
                    try:
                        facts["machine"] = json.loads(line)
                        break
                    except ValueError:
                        continue
        return facts

    def load_inputs(self):
        if self.kit is None or not self.kit.is_dir():
            raise HarnessError("kit directory missing")
        if self.runtime_path is None or not self.runtime_path.is_file():
            raise HarnessError("--runtime (operator runtime.json) is required")
        self.runtime = read_json(self.runtime_path)
        if self.runtime.get("format") != RUNTIME_FORMAT:
            raise HarnessError(f"runtime fixture format {self.runtime.get('format')!r} is not {RUNTIME_FORMAT!r}")
        self.releases = read_json(self.kit / "releases.json")
        if self.releases.get("kit_format") != KIT_VERSION:
            raise HarnessError(f"kit format {self.releases.get('kit_format')!r} is not {KIT_VERSION!r}")
        if self.releases.get("instance_id") != self.runtime.get("instance_id"):
            raise HarnessError("kit and runtime fixture belong to different instances")
        if self.accounts_path is not None:
            self.accounts = read_json(self.accounts_path)
            if self.accounts.get("format") != ACCOUNTS_FORMAT:
                raise HarnessError(f"accounts format {self.accounts.get('format')!r} is not {ACCOUNTS_FORMAT!r}")
            if self.accounts.get("instance_id") != self.runtime.get("instance_id"):
                raise HarnessError("accounts and runtime fixture belong to different instances")
        self.expected = self.runtime.get("expected", {})
        self.api_port = int(self.runtime.get("port"))
        tunnel = self.runtime.get("tunnel") or {}
        # The kit freezes the scoped endpoint contract; the CLI override exists only
        # for an explicitly different verified route.
        self.tunnel_port = int(self.tunnel_port or tunnel.get("listen_port")
                               or tunnel.get("tunnel_port") or self.api_port + 100)
        if tunnel.get("target_port") is not None and int(tunnel["target_port"]) != self.api_port:
            raise HarnessError("runtime tunnel target does not match the frozen API port")
        self.api = Api(self.api_port)

    def authenticate_member(self, case):
        """The scoped member session is required for exports and short codes."""
        member = (self.accounts or {}).get("member") or {}
        if not member.get("username") or not member.get("password"):
            case.check(False, "私有账号产物提供受限成员凭据（授权导出/短码签发）", None)
            return False
        try:
            self.api.login(member["username"], member["password"])
        except HarnessError as error:
            case.check(False, "受限成员真实登录成功（授权导出/短码签发）", str(error))
            return False
        case.check(True, "受限成员真实登录成功（授权导出/短码签发）", member["username"])
        return True

    # ------------------------------------------------------------- kit checks
    def check_kit_integrity(self, case):
        sidecar_path = self.kit / SIDECAR_NAME
        if not sidecar_path.is_file():
            raise HarnessError(f"kit integrity file missing: {SIDECAR_NAME}")
        sidecar = read_json(sidecar_path)
        case.check(sidecar.get("format") == KIT_VERSION, "kit 完整性格式版本正确", sidecar.get("format"))
        actual = {}
        for path in sorted(self.kit.rglob("*")):
            if path.is_file() and path.name != SIDECAR_NAME:
                actual[path.relative_to(self.kit).as_posix()] = {"sha256": sha256_file(path),
                                                                "size": path.stat().st_size}
        case.check(set(actual) == set(sidecar.get("members", {})), "完整性清单覆盖全部 kit 成员",
                   {"actual": len(actual), "listed": len(sidecar.get("members", {}))})
        mismatched = [name for name, entry in sidecar.get("members", {}).items()
                      if name not in actual or actual[name]["sha256"] != entry["sha256"]
                      or actual[name]["size"] != entry["size"]]
        case.check(not mismatched, "kit 成员逐字节匹配完整性清单", mismatched[:5] or None)
        case.evidence["kit_integrity_sha256"] = sha256_file(sidecar_path)
        return sidecar

    def check_delivery(self, case):
        """Every frozen mode: real package bytes, manifest members, layout, PE."""
        for mode in MODES:
            info = self.releases["modes"].get(mode)
            if not isinstance(info, dict):
                raise HarnessError(f"kit has no release for mode {mode}")
            package = self.kit / info["delivery"]
            if not package.is_file():
                raise HarnessError(f"kit package missing for mode {mode}: {info['delivery']}")
            digest = sha256_file(package)
            case.check(digest == info["artifact_sha256"] == self.runtime["studies"][mode]["package_sha256"],
                       f"{mode}：kit 内完整包摘要等于平台记录", digest)
            case.check(package.stat().st_size == info["artifact_size"],
                       f"{mode}：kit 内完整包大小等于平台记录", package.stat().st_size)
            target = self.extract(package, mode)
            manifest = read_json(target / "artifact_manifest.json")
            sidecar_manifest = read_json(package.parent / "artifact_manifest.json")
            case.check((target / "artifact_manifest.json").read_bytes() == (package.parent / "artifact_manifest.json").read_bytes(),
                       f"{mode}：包内清单与平台 sidecar 逐字节一致")
            members = {entry["path"]: entry for entry in manifest["members"]}
            with zipfile.ZipFile(package) as archive:
                names = [item.filename for item in archive.infolist() if not item.filename.endswith("/")]
                case.check(set(names) == set(members) | {"artifact_manifest.json"},
                           f"{mode}：包成员集合与清单一致", {"archive": len(names), "manifest": len(members)})
                for name in names:
                    if name == "artifact_manifest.json":
                        continue
                    entry = members[name]
                    info_zip = archive.getinfo(name)
                    hasher, size = hashlib.sha256(), 0
                    with archive.open(name) as stream:
                        while chunk := stream.read(1 << 20):
                            hasher.update(chunk)
                            size += len(chunk)
                    case.check(size == entry["size"] and hasher.hexdigest() == entry["sha256"],
                               f"{mode}：成员逐字节匹配清单：{name}")
                    case.check(f"{((info_zip.external_attr >> 16) & 0o7777):04o}" == entry["mode"],
                               f"{mode}：成员模式保持：{name}")
            entry_path = target / Path(manifest["entry"])
            case.check(entry_path.is_file(), f"{mode}：声明的入口存在于解压目录", manifest["entry"])
            summary = pe_summary(entry_path.read_bytes()[: 4 << 20])
            case.check(summary["machine"] == "x86_64" and summary["magic"] == 0x20B and summary["executable"],
                       f"{mode}：入口是真实 PE32+ x86-64 映像", summary)
            for dependency in manifest.get("dependencies", []):
                path = target / Path(dependency)
                case.check(path.is_file(), f"{mode}：声明的原生依赖随包解压", dependency)
                if path.is_file():
                    dependency_summary = pe_summary(path.read_bytes()[: 4 << 20])
                    case.check(dependency_summary["dll"] and dependency_summary["machine"] == "x86_64",
                               f"{mode}：依赖是真实 x86-64 DLL", {"dependency": dependency, **dependency_summary})
            config_path = target / Path(manifest["config_member"])
            case.check(config_path.is_file() and config_path.parent == entry_path.parent,
                       f"{mode}：冻结 connection.json 位于 EXE 同级", manifest["config_member"])
            config = read_json(config_path)
            case.check(config.get("purpose") == "synthetic" and config.get("mode") == mode,
                       f"{mode}：冻结配置为合成用途且模式一致", config.get("mode"))
            case.check(config.get("release_id") == info["release_id"]
                       and config.get("instance_id") == self.runtime["instance_id"]
                       and config.get("api_url") == self.runtime["api_url"],
                       f"{mode}：冻结配置绑定本实例与本次发行",
                       {"api_url": config.get("api_url"), "release_id": config.get("release_id")})
            self.releases["modes"][mode]["_target"] = str(target)
            self.releases["modes"][mode]["_config"] = config
            self.releases["modes"][mode]["_manifest"] = manifest
        alternate = (self.runtime.get("alternates") or {}).get("password")
        if alternate:
            alternate_package = self.kit / "delivery" / "password-alternate" / f"gep-{alternate['release_id']}.zip"
            case.check(alternate_package.is_file(), "WN06：kit 内含第二个发行完整包（旧发行兼容输入）",
                       alternate["release_id"])
            if alternate_package.is_file():
                case.check(sha256_file(alternate_package) == alternate["package_sha256"],
                           "WN06：第二个发行完整包摘要等于运行时夹具记录",
                           sha256_file(alternate_package))
        case.evidence["modes"] = {mode: {"package_sha256": self.releases["modes"][mode]["artifact_sha256"],
                                         "extract_root": self.rel(self.releases["modes"][mode]["_target"])}
                                  for mode in MODES}
        return True

    def extract(self, package, mode):
        target = self.run_root / f"GEP 原生测试 中文 {mode} {self.run_id}"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        with zipfile.ZipFile(package) as archive:
            archive.extractall(target)
        return target

    def check_accounts_acl(self, case):
        if self.accounts_path is None:
            case.check(False, "私有账号产物已提供（--accounts）", None)
            return
        target = self.run_root / "private" / "runtime_accounts.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.accounts_path, target)
        if os.name != "nt":
            case.check(False, "账号 ACL 检查只在真实 Windows 上有效", os.name)
            return
        before = self.acl_report(target)
        broad = [principal for principal in before["broad"]]
        if broad:
            self.harden_acl(target)
            after = self.acl_report(target)
            case.check(not after["broad"], "私有账号文件 ACL 已收紧到当前用户",
                       {"before": broad, "after": after["broad"]})
            case.evidence["acl"] = {"before": before, "after": after, "hardened": True}
        else:
            case.check(True, "私有账号文件 ACL 仅限当前用户/系统/管理员",
                       {"principals": before["principals"]})
            case.evidence["acl"] = {"before": before, "after": before, "hardened": False}
        text = target.read_text(encoding="utf-8")
        case.check("never distributed" in text.lower() or "synthetic_only" in text.lower(),
                   "账号文件声明仅本地合成使用")
        case.evidence["accounts_sha256"] = sha256_file(target)

    def acl_report(self, path):
        result = run_powershell_command(f"icacls {ps_quote(path)}")
        principals, broad = [], []
        for line in (result.stdout or "").splitlines():
            match = re.match(r"^(.+?):((?:\(.+?\))+)\s*$", line.strip())
            if not match:
                continue
            principal = match.group(1).strip()
            rights = match.group(2)
            principals.append({"principal": principal, "rights": rights})
            if principal.casefold() in BROAD_PRINCIPALS and re.search(r"\([RMWF]", rights):
                broad.append(principal)
        return {"exit": result.returncode, "principals": principals, "broad": broad,
                "raw": (result.stdout or "").strip()[-800:]}

    @staticmethod
    def harden_acl(path):
        run_powershell_command(
            f"$acl=Get-Acl {ps_quote(path)};$acl.SetAccessRuleProtection($true,$false);"
            f"$rule=New-Object System.Security.AccessControl.FileSystemAccessRule("
            f"[System.Security.Principal.WindowsIdentity]::GetCurrent().Name,'FullControl','Allow');"
            f"$acl.SetAccessRule($rule);Set-Acl -Path {ps_quote(path)} -AclObject $acl")

    # ------------------------------------------------------------ launch/run
    def preflight_tunnel(self, case):
        """The scoped tunnel must be reachable; otherwise the runtime stays blocked."""
        try:
            with socket.create_connection(("127.0.0.1", self.tunnel_port), timeout=5):
                case.check(True, "作用域隧道端口可达（运行前置条件）", self.tunnel_port)
                return True
        except OSError as error:
            case.block(f"runtime prerequisite missing: TCP 127.0.0.1:{self.tunnel_port} unreachable "
                       f"({type(error).__name__}); start the prepared scoped SSH tunnel (operator/tunnel.cmd)")
            case.check(False, "作用域隧道端口可达（运行前置条件）",
                       f"127.0.0.1:{self.tunnel_port} {type(error).__name__}")
            return False

    def launch_script(self, entry, workdir, storage, arguments, out_path, err_path, record_path, exit_path):
        """One PowerShell launch script; paths never round-trip through a command line."""
        arg_string = " ".join(f'"{value}"' for value in ("--", *arguments))
        script = "\n".join([
            "$ErrorActionPreference='Stop'",
            f"$env:GEP_SYNTHETIC_STORAGE={ps_quote(storage)}",
            f"$p=Start-Process -FilePath {ps_quote(entry)} -WorkingDirectory {ps_quote(workdir)} "
            f"-ArgumentList {ps_quote(arg_string)} -PassThru "
            f"-RedirectStandardOutput {ps_quote(out_path)} -RedirectStandardError {ps_quote(err_path)}",
            # A program that is refused can exit within a second, and
            # ``Start-Process -PassThru`` only reports a real exit code when
            # event raising was enabled while the process was still alive. So it
            # is the very first statement after the start; a failure here is not
            # fatal, the record below reports an unknown code honestly instead.
            "try{$p.EnableRaisingEvents=$true}catch{}",
            "$proc=$null",
            "try{$proc=Get-Process -Id $p.Id -ErrorAction Stop}catch{$proc=$null}",
            "$identity=[ordered]@{pid=$p.Id;path='';session_id=$null;start_time='';window_title='';"
            "session_name=$env:SESSIONNAME}",
            "if($proc -ne $null){",
            "  $identity.path=$proc.Path;$identity.session_id=$proc.SessionId;"
            "$identity.start_time=$proc.StartTime.ToString('o')",
            # The window title can only be enumerated from the session that owns
            # the window, so the launcher (running in the interactive session)
            # waits briefly for the real title; an already-exited program simply
            # records no title.
            "  $deadline=(Get-Date).AddSeconds(30)",
            "  while(-not $proc.HasExited -and -not $proc.MainWindowTitle -and (Get-Date) -lt $deadline)"
            "{Start-Sleep -Milliseconds 500;$proc.Refresh()}",
            "  if(-not $proc.HasExited){$identity.window_title=$proc.MainWindowTitle}",
            "}",
            f"$identity|ConvertTo-Json -Compress|Set-Content -Encoding UTF8 {ps_quote(record_path)}",
            # The exit record is always written: a program that ended before the
            # identity could be read, a gone process or an unavailable exit code
            # must never leave the harness waiting for a record that cannot come.
            "$code=$null",
            "try{$p.WaitForExit()}catch{}",
            "try{$code=$p.ExitCode}catch{$code=$null}",
            "if($code -eq $null){try{$code=(Get-Process -Id $p.Id -ErrorAction Stop).ExitCode}catch{$code=$null}}",
            f"[pscustomobject]@{{pid=$p.Id;exit=$code;exited=$true}}|ConvertTo-Json -Compress|"
            f"Set-Content -Encoding UTF8 {ps_quote(exit_path)}",
            "",
        ])
        script_path = self.run_root / f"launch_{uuid.uuid4().hex[:8]}.ps1"
        script_path.write_text("\ufeff" + script, encoding="utf-8")
        return script_path

    def spawn_launcher(self, script_path, out_path=None, err_path=None):
        """Start our own launcher script without waiting for the program it starts.

        The launcher writes the launch record as soon as the program exists and
        then waits for it; the harness never waits for the whole experiment. The
        launcher's own streams are captured next to the program's evidence, so a
        generator/launcher failure is visible instead of silent.
        """
        stdout = open(out_path, "ab") if out_path else subprocess.DEVNULL
        stderr = open(err_path, "ab") if err_path else subprocess.DEVNULL
        try:
            return subprocess.Popen(self.launcher_command(script_path), stdout=stdout, stderr=stderr)
        finally:
            for stream in (stdout, stderr):
                if hasattr(stream, "close"):
                    stream.close()

    def launcher_command(self, script_path):
        """The real launcher invocation for one generated script body.

        The generated body travels as an encoded command instead of a ``-File``
        script file: a default Windows client runs with the PowerShell script
        policy ``Restricted``, and the harness must not change the machine policy
        or pass an execution-policy override. ``-EncodedCommand`` is a command,
        not a script file, so the same owned launcher runs on a default machine;
        the generated ``.ps1`` stays as evidence of exactly what ran.
        """
        script = Path(script_path).read_text(encoding="utf-8-sig")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]

    def task_command_path(self, script_path, out_path=None, err_path=None):
        """One short ``.cmd`` vehicle for the scheduled-task action.

        ``schtasks /tr`` is limited to 261 characters, so the encoded launcher
        cannot travel in the action itself. The wrapper is a plain command file
        (never a PowerShell script file) and carries the same owned command; the
        launcher's own streams are redirected next to the program's evidence so a
        generator/launcher failure is visible instead of silent.
        """
        wrapper = Path(script_path).with_suffix(".cmd")
        command = " ".join(self.launcher_command(script_path))
        if out_path is not None and err_path is not None:
            command += f" > {cmd_path_quote(out_path)} 2> {cmd_path_quote(err_path)}"
        wrapper.write_text("@echo off\r\n" + command + "\r\n", encoding="utf-8")
        return wrapper

    @staticmethod
    def release_scripts(launch):
        """Remove only the generated launcher/wrapper files of this launch."""
        for path in (launch.script_path, launch.wrapper_path):
            if path is None:
                continue
            try:
                Path(path).unlink()
            except OSError:
                pass

    def run_os(self, command, timeout=60):
        """One owned OS command (schtasks); the seam for failure-path tests."""
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)

    def delete_own_task(self, launch):
        """End and delete only the scheduled task this launch created."""
        if not launch.task_name or not launch.task_name.startswith("GEP-Native-WN-"):
            return
        name = launch.task_name
        launch.task_name = None
        self.run_os(["schtasks", "/end", "/tn", name])
        self.run_os(["schtasks", "/delete", "/tn", name, "/f"])

    def close_launch(self, launch):
        """Release our own launcher/task/script once the program has ended.

        Only objects created by this launch are touched; a launcher that still
        runs is our own child process, so terminating it is scoped too.
        """
        if launch.closed:
            return launch
        if launch.launcher is not None:
            try:
                launch.launch_exit = launch.launcher.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    launch.launcher.terminate()
                    launch.launch_exit = launch.launcher.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        launch.launcher.kill()
                        launch.launcher.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
        self.delete_own_task(launch)
        self.release_scripts(launch)
        launch.record["launch_exit"] = launch.launch_exit
        launch.record["launcher_closed"] = True
        launch.closed = True
        if launch in self.launches:
            self.launches.remove(launch)
        return launch

    def abort_launch(self, launch):
        """Clear failure before the launch record exists: clean only our objects.

        No identity record exists yet, so no process may be stopped by PID here;
        only our own launcher child, our own scheduled task and our own script
        are released, and the exact condition is returned in the error.
        """
        if launch.launcher is not None and launch.launcher.poll() is None:
            try:
                launch.launcher.terminate()
                launch.launcher.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    launch.launcher.kill()
                    launch.launcher.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if launch.launcher is not None:
            launch.launch_exit = launch.launcher.returncode
        self.delete_own_task(launch)
        self.release_scripts(launch)
        launch.closed = True
        if launch in self.launches:
            self.launches.remove(launch)
        return launch

    def start_program(self, entry, workdir, storage, arguments, label, timeout):
        """Start the real program; wait only until its launch record exists.

        The program may run for the whole experiment, so this never waits for it
        to end. The returned Launch owns the exact launcher process, scheduled
        task and script; callers close it after the program has ended (or after
        the timeout/failure path has been recorded).
        """
        evidence = self.evidence_dir(label)
        out_path = evidence / "program.out.txt"
        err_path = evidence / "program.err.txt"
        record_path = evidence / "launch.json"
        exit_path = evidence / "exit.json"
        launcher_out = evidence / "launcher.out.txt"
        launcher_err = evidence / "launcher.err.txt"
        for path in (record_path, exit_path):
            if path.exists():
                path.unlink()
        script_path = self.launch_script(entry, workdir, storage, arguments, out_path, err_path,
                                         record_path, exit_path)
        interactive = os.environ.get("SESSIONNAME", "").startswith("Console")
        launch = Launch(label, entry, storage, arguments, record_path, exit_path, out_path, err_path,
                        script_path, interactive)
        self.launches.append(launch)
        try:
            if interactive:
                launch.launcher = self.spawn_launcher(script_path, launcher_out, launcher_err)
            else:
                session_id = active_console_session()
                if session_id is None:
                    raise HarnessError("no active interactive console session; a visible window cannot be verified")
                launch.task_name = f"GEP-Native-WN-{uuid.uuid4().hex[:8]}"
                launch.wrapper_path = self.task_command_path(script_path, launcher_out, launcher_err)
                create = self.run_os(["schtasks", "/create", "/tn", launch.task_name, "/tr",
                                      f'cmd /c "{launch.wrapper_path}"',
                                      "/sc", "once", "/st", "00:00", "/it", "/f"])
                if create.returncode != 0:
                    raise HarnessError(f"scheduled task creation failed: "
                                       f"{(create.stderr or create.stdout or '')[-200:]}")
                run = self.run_os(["schtasks", "/run", "/tn", launch.task_name])
                if run.returncode != 0:
                    raise HarnessError(f"scheduled task start failed: {(run.stderr or run.stdout or '')[-200:]}")
            if (launch.launcher is not None and launch.launcher.poll() is not None
                    and not record_path.is_file()):
                raise HarnessError(f"launcher exited before writing the launch record "
                                   f"(exit {launch.launcher.returncode})")
            record = self.wait_for(record_path, timeout=60)
            if record is None:
                raise HarnessError(f"launch record missing (interactive={interactive}, "
                                   f"launcher exit {launch.launcher.poll() if launch.launcher else None})")
        except HarnessError:
            self.abort_launch(launch)
            raise
        launch.record = dict(record)
        launch.record.update({"launch_exit": None, "interactive": interactive, "arguments": redact(arguments),
                              "storage": str(storage), "label": label})
        return launch

    def wait_for(self, path, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if Path(path).is_file():
                try:
                    return read_json(path)
                except ValueError:
                    pass
            time.sleep(0.5)
        return None

    def wait_for_marker(self, out_path, markers, timeout):
        deadline = time.time() + timeout
        text = ""
        while time.time() < deadline:
            if Path(out_path).is_file():
                text = Path(out_path).read_text(encoding="utf-8", errors="replace")
                for marker in markers:
                    if marker in text:
                        return marker, text
            time.sleep(0.5)
        return None, text

    def wait_for_marker_or_exit(self, out_path, exit_path, markers, timeout):
        """Wait for one marker or for the owned program's real exit record.

        A program that refuses before the boundary (a real API refusal, a damaged
        configuration) exits instead of printing the marker; waiting out the whole
        timeout would hide the reason and waste the run. The exit record is
        written by our own launcher, so its presence means this program ended.
        Returns ``(marker, text, exit_code)`` with ``exit_code`` None while the
        program is still running.
        """
        deadline = time.time() + timeout
        text = ""
        while time.time() < deadline:
            if Path(out_path).is_file():
                text = Path(out_path).read_text(encoding="utf-8", errors="replace")
                for marker in markers:
                    if marker in text:
                        return marker, text, None
            if Path(exit_path).is_file():
                try:
                    document = read_json(exit_path)
                except ValueError:
                    document = None
                if document is not None:
                    if Path(out_path).is_file():
                        text = Path(out_path).read_text(encoding="utf-8", errors="replace")
                    for marker in markers:
                        if marker in text:
                            return marker, text, None
                    return None, text, document.get("exit")
            time.sleep(0.5)
        return None, text, None

    def verify_identity(self, record):
        """Re-read the live process and compare the complete recorded identity.

        A reused PID or a replaced executable is detected here; a record without
        path/session/start time can never authorize a stop.
        """
        pid = record.get("pid") if isinstance(record, dict) else None
        if pid in (None, ""):
            return {"outcome": "unverifiable", "live": None, "problems": ["no recorded pid"]}
        live = self.process_identity(pid)
        if live is None:
            return {"outcome": "gone", "live": None, "problems": []}
        problems = []
        expected_path = str(record.get("path") or "")
        live_path = str(live.get("path") or "")
        if (not expected_path or not live_path
                or Path(live_path).resolve().as_posix().casefold()
                != Path(expected_path).resolve().as_posix().casefold()):
            problems.append("executable path")
        expected_session = record.get("session_id")
        live_session = live.get("session_id")
        if (expected_session is None or live_session is None
                or int(live_session) != int(expected_session)):
            problems.append("console session")
        if not record.get("start_time") or str(live.get("start_time") or "") != str(record["start_time"]):
            problems.append("start time")
        return {"outcome": "verified" if not problems else "mismatch", "live": live, "problems": problems}

    def stop_owned(self, record, case=None):
        """Stop exactly the process this harness started; never a reused PID.

        The complete recorded identity (executable path, console session and
        start time) is re-verified in Python and again inside the stop command
        before Stop-Process runs. A mismatch, or a record that cannot be
        verified, refuses the stop and reports the exact reason.
        """
        verdict = self.verify_identity(record)
        outcome = verdict["outcome"]
        if outcome in ("mismatch", "unverifiable"):
            outcome = "refused"
        result = {"outcome": outcome, "pid": record.get("pid") if isinstance(record, dict) else None,
                  "problems": list(verdict["problems"]), "live": verdict["live"]}
        if outcome == "verified":
            pid = int(record["pid"])
            command = (
                f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue;"
                f"if(-not $p){{'gone'}}else{{"
                f"if($p.Path -eq {ps_quote(record.get('path'))} "
                f"-and $p.SessionId -eq {int(record.get('session_id'))} "
                f"-and $p.StartTime.ToString('o') -eq {ps_quote(record.get('start_time'))}){{"
                f"Stop-Process -Id {pid} -Force;'stopped'}}else{{'refused'}}}}")
            run = run_powershell_command(command)
            text = (run.stdout or "").strip()
            if "stopped" in text:
                outcome = "stopped"
            elif "gone" in text:
                outcome = "gone"
            else:
                outcome = "refused"
                result["problems"].append("the stop command refused the live identity")
            result["outcome"] = outcome
        if isinstance(record, dict):
            record["stopped_owned_pid"] = outcome == "stopped"
        if case is not None:
            case.check(outcome == "stopped", "仅终止身份一致的自有 PID",
                       {"outcome": outcome, "problems": result["problems"]})
        return result

    def process_identity(self, pid):
        result = run_powershell_command(
            f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue;"
            f"if($p){{[pscustomobject]@{{pid=$p.Id;path=$p.Path;session_id=$p.SessionId;"
            f"start_time=$p.StartTime.ToString('o');window_title=$p.MainWindowTitle}}|ConvertTo-Json -Compress}}else{{'null'}}")
        text = (result.stdout or "").strip()
        if not text or text == "null":
            return None
        for line in text.splitlines():
            try:
                return json.loads(line)
            except ValueError:
                continue
        return None

    def verify_owned(self, case, record, entry, require_window=True, label=""):
        """Prove the process really is this launch, then inspect only that PID.

        The live identity is read from this process (path, console session, start
        time). The window title is observed by the launcher from the interactive
        session that owns the window; when the live read cannot enumerate it
        (a harness running over SSH), the launcher's observation is used.
        """
        pid = record.get("pid")
        identity = self.process_identity(pid) if pid else None
        live = identity or record
        path = str(live.get("path") or record.get("path") or "")
        case.check(path and Path(path).name == Path(entry).name
                   and str(Path(path).resolve()).casefold() == str(Path(entry).resolve()).casefold(),
                   f"{label}自有 PID 的可执行文件路径等于本次解压入口", path)
        session_id = live.get("session_id") if live.get("session_id") is not None else record.get("session_id")
        console = active_console_session()
        case.check(session_id is not None and console is not None and int(session_id) == int(console),
                   f"{label}自有 PID 运行在真实交互控制台会话", {"process_session": session_id, "console": console})
        start_time = str(live.get("start_time") or record.get("start_time") or "")
        case.check(bool(start_time), f"{label}自有 PID 记录真实启动时间", start_time)
        title = str((live or {}).get("window_title") or "")
        if not title.strip():
            title = str(record.get("window_title") or "")
        if require_window:
            case.check(bool(title.strip()), f"{label}自有 PID 拥有真实可见窗口标题（非标题无关进程）", title or None)
        case.evidence.setdefault("processes", []).append({"pid": pid, "path": path, "session_id": session_id,
                                                          "start_time": start_time, "window_title": title})
        return live

    def run_program(self, case, mode, storage, arguments, label, timeout, expect_exit=0, entry=None,
                    target=None, manifest=None):
        info = self.releases["modes"][mode]
        target = Path(target) if target is not None else Path(info["_target"])
        manifest = manifest if manifest is not None else info["_manifest"]
        entry = entry or str(target / Path(manifest["entry"]))
        launch = self.start_program(entry, str(target), storage, arguments, label, timeout)
        record = launch.record
        self.verify_owned(case, record, entry, require_window=False, label=label)
        exit_document = self.wait_for(launch.exit_path, timeout=timeout)
        pid = record.get("pid")
        if exit_document is None:
            stopped = self.stop_owned(record)
            self.close_launch(launch)
            case.check(False, f"{label}进程在 {timeout}s 内自行结束",
                       {"pid": pid, "stop": stopped["outcome"], "problems": stopped["problems"]})
            return {"record": record, "exit": None, "stdout": Path(launch.out_path).read_text(encoding="utf-8", errors="replace")
                    if Path(launch.out_path).is_file() else "", "timed_out": True}
        stdout = Path(launch.out_path).read_text(encoding="utf-8", errors="replace") if Path(launch.out_path).is_file() else ""
        stderr = Path(launch.err_path).read_text(encoding="utf-8", errors="replace") if Path(launch.err_path).is_file() else ""
        self.close_launch(launch)
        code = exit_document.get("exit")
        case.check(code == expect_exit, f"{label}真实进程退出码为 {expect_exit}", code)
        case.evidence.setdefault("runs", []).append({"label": label, "mode": mode, "pid": pid,
                                                     "exit": code, "storage": self.rel(storage),
                                                     "arguments": redact(arguments),
                                                     "stdout_tail": stdout[-400:], "stderr_tail": stderr[-200:]})
        (self.evidence_dir(label) / "stdout.txt").write_text(stdout, encoding="utf-8")
        return {"record": record, "exit": code, "stdout": stdout, "stderr": stderr, "timed_out": False,
                "stdout_path": self.rel(self.evidence_dir(label) / "stdout.txt")}

    def kill_program(self, case, mode, storage, arguments, label, timeout, marker, target=None, manifest=None):
        """Run until a boundary marker, then hard-kill only the verified owned PID.

        A program that was refused before the boundary (or that exited for any
        other reason) ends the wait immediately: the real exit code and the
        program's own output tail are recorded instead of an anonymous timeout.
        """
        info = self.releases["modes"][mode]
        target = Path(target) if target is not None else Path(info["_target"])
        manifest = manifest if manifest is not None else info["_manifest"]
        entry = str(target / Path(manifest["entry"]))
        launch = self.start_program(entry, str(target), storage, arguments, label, timeout)
        record = launch.record
        self.verify_owned(case, record, entry, require_window=False, label=label)
        found, text, exit_code = self.wait_for_marker_or_exit(launch.out_path, launch.exit_path, (marker,), timeout)
        case.check(found == marker, f"{label}程序到达边界标记 {marker}",
                   {"found": found, "exit": exit_code, "stdout_tail": text[-400:]})
        killed = self.stop_owned(record)
        case.check(killed["outcome"] == "stopped", f"{label}仅终止身份一致的自有 PID",
                   {"pid": record.get("pid"), "outcome": killed["outcome"], "problems": killed["problems"]})
        exit_document = self.wait_for(launch.exit_path, timeout=15)
        self.close_launch(launch)
        case.evidence.setdefault("kills", []).append({"label": label, "pid": record.get("pid"),
                                                      "marker": found, "stopped": killed["outcome"] == "stopped",
                                                      "stop_problems": killed["problems"],
                                                      "exit": (exit_document or {}).get("exit"),
                                                      "stdout_tail": text[-400:],
                                                      "stdout_path": self.rel(self.evidence_dir(label) / "program.stdout.txt")})
        (self.evidence_dir(label) / "program.stdout.txt").write_text(text, encoding="utf-8")
        return {"record": record, "stdout": text}

    # ------------------------------------------------------------------- cases
    def case_wn01(self):
        case = self.case("WN01")
        try:
            info = self.releases["modes"]["anonymous"]
            target = Path(info["_target"])
            entry = str(target / Path(info["_manifest"]["entry"]))
            launch = self.start_program(
                entry, str(target), self.run_root / "wn01-storage",
                ["--synthetic-auto", "--stop-after-trial"], "WN01", 60)
            record = launch.record
            live = self.verify_owned(case, record, entry, require_window=True, label="WN01 ")
            session_name = str(record.get("session_name") or "")
            case.check(session_name.startswith("Console") or active_console_session() is not None,
                       "WN01 启动发生在交互会话", record.get("session_name"))
            case.check(not record.get("timed_out"), "WN01 启动记录在等待窗口内产生")
            stopped = self.stop_owned(record)
            case.check(stopped["outcome"] == "stopped", "WN01 只终止身份一致的自有 PID",
                       {"pid": record.get("pid"), "outcome": stopped["outcome"],
                        "problems": stopped["problems"]})
            self.close_launch(launch)
            case.evidence["launch"] = {key: value for key, value in record.items() if key != "arguments"}
            # The launcher observes the real title from the interactive session that
            # owns the window; the harness's own live read can come back empty when
            # it runs over SSH, so an empty re-read never erases the observed title.
            observed = str((live or {}).get("window_title") or "").strip()
            case.evidence["launch"]["window_title"] = observed or str(record.get("window_title") or "")
            case.evidence["launch"]["executable_path"] = (live or {}).get("path") or record.get("path")
            case.evidence["launch"]["console_session"] = active_console_session()
        except HarnessError as error:
            case.check(False, "WN01 交互启动流程", str(error))
        return case.finish()

    def mode_arguments(self, case, mode, code=None):
        info = self.releases["modes"][mode]
        arguments = ["--synthetic-auto"]
        if mode == "anonymous":
            # The anonymous release has no roster identity at all.
            return arguments
        code = code or info["participant_codes"][0]
        arguments.append(f"--participant-code={code}")
        if mode == "password":
            password = self.accounts.get("participants", {}).get(mode, {}).get(code, {}).get("password")
            case.require(bool(password), f"{mode}：私有账号产物提供名单口令", None)
            arguments.append(f"--password={password}")
        return arguments

    def complete_mode_session(self, case, mode, label, storage):
        """Admission + trial boundary + resume + completion + authorized export."""
        info = self.releases["modes"][mode]
        boundary = self.kill_program(case, mode, storage, self.mode_arguments(case, mode) + ["--stop-after-trial"],
                                     f"{label}-boundary", 180, "SYNTHETIC_BOUNDARY_SAVED")
        store = read_store(storage)
        sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
        case.require(len(sessions) == 1, f"{label}：试次边界后本地恰好一条会话", len(sessions))
        session = sessions[0]
        case.check(len(store_records(session)) == 2, f"{label}：第一个试次边界已本地提交",
                   len(store_records(session)))
        case.check(raw_values(session) == [321.5], f"{label}：本地原始 rt_ms 值正确", raw_values(session))
        boundary_copy = self.snapshot_store(f"{label}-boundary", storage)
        if mode == "password":
            resume = self.mode_arguments(case, mode)
        else:
            short_code = self.issue_short_code(info["study_id"], session.get("id"))
            case.require(bool(short_code), f"{label}：受限成员签发六位短码", None)
            resume = ["--synthetic-auto", f"--short-code={short_code}"]
        completed = self.run_program(case, mode, storage, resume, f"{label}-complete", 240)
        case.check("SYNTHETIC_DONE" in completed["stdout"], f"{label}：恢复后完成并远端确认",
                   completed["stdout"][-200:])
        case.check("SYNTHETIC_ERROR" not in completed["stdout"], f"{label}：完成过程没有错误标记")
        store = read_store(storage)
        sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
        cleaned = [document for document in store["sessions"] if document.get("kind") == "cleaned"]
        case.check(not sessions and bool(cleaned), f"{label}：完成后本地写入已清理墓碑（载荷删除）",
                   {"sessions": len(sessions), "cleaned": len(cleaned)})
        export_id, payload = self.api.export_jsonl(info["study_id"])
        target = self.evidence_dir(label) / "export.jsonl"
        # The platform export is study-scoped and the study may already carry
        # other sessions (WN01 runs first in the anonymous study); the case
        # evidence keeps the full download and carries exactly this session's
        # downloaded rows in the derived file.
        lines, export_full = self.scoped_export(payload, session.get("id"), target)
        export_events = [entry["record"] for entry in lines]
        export_ids = [event.get("event_id") for event in export_events]
        export_values = sorted(float(event["payload"]["rt_ms"]) for event in export_events
                               if isinstance(event.get("payload", {}).get("rt_ms"), (int, float)))
        case.check(len(lines) == EXPECTED_EVENTS_PER_SESSION, f"{label}：授权导出收到完整四个事件", len(lines))
        case.check(len(set(export_ids)) == len(export_ids), f"{label}：授权导出无重复事件")
        case.check(set(store_event_ids(session)) <= set(export_ids),
                   f"{label}：边界前本地事件 ID 全部出现在授权导出中")
        case.check(export_values == sorted(RAW_VALUES), f"{label}：授权导出原始 rt_ms 值正确", export_values)
        case.check(all(entry.get("release_id") == info["release_id"] for entry in lines),
                   f"{label}：授权导出绑定本发行")
        return {"session_id": session.get("id"), "boundary_event_ids": store_event_ids(session),
                "boundary_raw_values": raw_values(session), "export_ids": export_ids,
                "export_values": export_values, "export_id": export_id,
                "export_path": self.rel(target), "export_sha256": sha256_file(target),
                "export_full": export_full,
                "store_copy": boundary_copy, "cleaned": bool(cleaned),
                "stdout": self.rel(self.evidence_dir(f"{label}-complete") / "stdout.txt")}

    def case_wn02(self):
        case = self.case("WN02")
        try:
            for mode in MODES:
                info = self.releases["modes"][mode]
                storage = self.run_root / f"wn02-{mode}"
                evidence = self.complete_mode_session(case, mode, f"WN02-{mode}", storage)
                case.evidence.setdefault("modes", {})[mode] = evidence
                case.check(bool(info["admission_session_id"]), f"{mode}：准备阶段真实准入会话已记录",
                           info["admission_session_id"])
            self._wrong_credentials(case)
            self._wrong_binding(case)
        except HarnessError as error:
            case.check(False, "WN02 流程", str(error))
        return case.finish()

    def _wrong_credentials(self, case):
        mode = "password"
        info = self.releases["modes"][mode]
        storage = self.run_root / "wn02-wrong-password"
        arguments = ["--synthetic-auto", f"--participant-code={info['participant_codes'][0]}",
                     "--password=wrong-" + uuid.uuid4().hex[:12]]
        run = self.run_program(case, mode, storage, arguments, "WN02-wrong-password", 120, expect_exit=2)
        case.check("http_403" in run["stdout"] or "admission_denied" in run["stdout"]
                   or "SYNTHETIC_ERROR" in run["stdout"],
                   "错误口令被程序就地拒绝并报告（不进入任务）", run["stdout"][-200:])
        store = read_store(storage)
        sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
        case.check(not sessions, "错误口令没有在本地创建会话", len(sessions))

    def _wrong_binding(self, case):
        mode = "anonymous"
        info = self.releases["modes"][mode]
        scratch = self.run_root / "wn02-wrong-binding"
        if scratch.exists():
            shutil.rmtree(scratch)
        shutil.copytree(Path(info["_target"]), scratch)
        config_path = scratch / Path(info["_manifest"]["config_member"])
        config = read_json(config_path)
        config["release_id"] = str(uuid.uuid4())
        config_path.write_text(json.dumps(config), encoding="utf-8")
        storage = self.run_root / "wn02-wrong-binding-storage"
        entry = str(scratch / Path(info["_manifest"]["entry"]))
        launch = self.start_program(entry, str(scratch), storage, ["--synthetic-auto"],
                                    "WN02-wrong-binding", 120)
        record = launch.record
        self.verify_owned(case, record, entry, require_window=False, label="WN02-wrong-binding ")
        exit_document = self.wait_for(launch.exit_path, 120)
        stdout = Path(launch.out_path).read_text(encoding="utf-8", errors="replace") if Path(launch.out_path).is_file() else ""
        self.close_launch(launch)
        case.check(exit_document is not None and exit_document.get("exit") == 2,
                   "错误发行绑定的启动被拒绝（退出码 2）", (exit_document or {}).get("exit"))
        case.check("SYNTHETIC_DONE" not in stdout, "错误绑定没有进入任务", stdout[-200:])
        store = read_store(storage)
        sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
        case.check(not sessions, "错误绑定没有在本地创建会话", len(sessions))

    def case_wn03(self):
        case = self.case("WN03")
        mode = "password"
        info = self.releases["modes"][mode]
        code = info["participant_codes"][1] if len(info["participant_codes"]) > 1 else info["participant_codes"][0]
        storage = self.run_root / "wn03-storage"
        try:
            named = self.mode_arguments(case, mode, code)
            # (1) admitted, offline local commit, then a hard process kill.
            self.set_proxy("drop-events")
            killed = self.kill_program(case, mode, storage, named + ["--stop-after-trial"],
                                       "WN03-offline-kill", 180, "SYNTHETIC_BOUNDARY_SAVED")
            self.set_proxy("pass")
            store = read_store(storage)
            sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
            case.require(len(sessions) == 1, "WN03：断网期间只有一条本地会话", len(sessions))
            session = sessions[0]
            case.check(len(store_records(session)) == 2, "WN03：断网期间一个试次边界已本地提交",
                       len(store_records(session)))
            case.check(bool(session.get("pending")), "WN03：本地待上传队列保留未确认事件",
                       len(session.get("pending", [])))
            case.check(session.get("checkpoint", {}).get("next_trial") == 1,
                       "WN03：本地检查点停在试次边界", session.get("checkpoint", {}).get("next_trial"))
            case.check(raw_values(session) == [321.5], "WN03：断网本地原始值正确", raw_values(session))
            offline_ids = store_event_ids(session)
            offline_copy = self.snapshot_store("WN03-offline", storage)
            case.evidence["offline"] = {"session_id": session.get("id"), "event_ids": offline_ids,
                                        "pending": session.get("pending"), "raw_values": raw_values(session),
                                        "killed": killed["record"].get("pid"),
                                        "store_copy": offline_copy, "stdout_tail": killed["stdout"][-200:]}
            # (2) reconnect: the same session resumes and uploads the same ids.
            #     The completion acknowledgement is held back first: the retained
            #     local state (records kept, pending cleared, completion declared,
            #     no tombstone) is the real reconnect evidence. The retry then
            #     confirms the completion and writes the cleaned tombstone.
            self.set_proxy("drop-completion")
            resumed = self.run_program(case, mode, storage, named, "WN03-reconnect", 240, expect_exit=3)
            self.set_proxy("pass")
            case.check("SYNTHETIC_TIMEOUT" in resumed["stdout"],
                       "WN03：重连补传后完成声明如实等待确认（不假成功）", resumed["stdout"][-200:])
            store = read_store(storage)
            sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
            case.require(len(sessions) == 1, "WN03：重连后保留同一条本地会话", len(sessions))
            session = sessions[0]
            case.check(not session.get("pending"), "WN03：重连后待上传队列清空")
            case.check(set(offline_ids) <= set(store_event_ids(session)),
                       "WN03：重连后事件 ID 保持（不重新生成）")
            case.check(session.get("completion") is not None, "WN03：本地声明完成")
            case.check(not [document for document in store["sessions"] if document.get("kind") == "cleaned"],
                       "WN03：完成声明未被确认前不写清理墓碑")
            case.evidence["reconnect"] = {"session_id": session.get("id"), "event_ids": store_event_ids(session),
                                          "segments": session.get("segments"),
                                          "store_copy": self.snapshot_store("WN03-reconnect", storage)}
            completed = self.run_program(case, mode, storage, named + ["--await-upload"],
                                         "WN03-reconnect-complete", 240)
            case.check("SYNTHETIC_DATA_ONLY_DONE" in completed["stdout"],
                       "WN03：重试的完成被确认并写入清理墓碑", completed["stdout"][-200:])
            store = read_store(storage)
            case.check(any(document.get("kind") == "cleaned" for document in store["sessions"]),
                       "WN03：确认完成后本地写入已清理墓碑")
            export_id, payload = self.api.export_jsonl(info["study_id"])
            target = self.evidence_dir("WN03") / "export.jsonl"
            session_id = case.evidence["offline"]["session_id"]
            lines, export_full = self.scoped_export(payload, session_id, target)
            export_ids = [entry["record"].get("event_id") for entry in lines]
            case.check(len(export_ids) == len(set(export_ids)), "WN03：重连补传没有产生重复事件（丢 ACK 去重）")
            case.check(set(offline_ids) <= set(export_ids), "WN03：断网期间的事件 ID 在授权导出中保持")
            case.evidence["export"] = {"export_id": export_id, "path": self.rel(target),
                                       "sha256": sha256_file(target), "event_ids": export_ids,
                                       "session_id": session_id, "export_full": export_full,
                                       "values": sorted(float(entry["record"]["payload"]["rt_ms"]) for entry in lines
                                                        if isinstance(entry["record"].get("payload", {}).get("rt_ms"), (int, float)))}
            # (3) lost acknowledgement on a fresh session: the same batch is retried.
            self.set_proxy("lose-first-ack")
            dropped = self.proxy
            lost = self.run_program(case, mode, storage, named, "WN03-lost-ack", 240)
            self.set_proxy("pass")
            case.check("SYNTHETIC_DONE" in lost["stdout"], "WN03：丢 ACK 后重试仍完成",
                       lost["stdout"][-200:])
            case.check(any("losing acknowledgement" in entry["message"] for entry in (dropped.events if dropped else [])),
                       "WN03：故障注入真实丢弃了一次 ACK")
            lost_export_id, lost_payload = self.api.export_jsonl(info["study_id"])
            lost_lines = [json.loads(line) for line in lost_payload.decode("utf-8").splitlines() if line.strip()]
            lost_ids = [entry["record"].get("event_id") for entry in lost_lines]
            case.check(len(lost_ids) == len(set(lost_ids)),
                       "WN03：丢 ACK 后同一批事件在服务端只保留一条")
            case.evidence["lost_ack"] = {"export_id": lost_export_id, "events": len(lost_lines),
                                         "unique": len(set(lost_ids))}
        except HarnessError as error:
            case.check(False, "WN03 流程", str(error))
        finally:
            self.set_proxy("pass")
        return case.finish()

    def case_wn04(self):
        case = self.case("WN04")
        mode = "password"
        info = self.releases["modes"][mode]
        code = info["participant_codes"][0]
        password = self.accounts.get("participants", {}).get(mode, {}).get(code, {}).get("password")
        storage = self.run_root / "wn04-storage"
        named = ["--synthetic-auto", f"--participant-code={code}", f"--password={password or ''}"]
        try:
            case.require(bool(password), "WN04：私有账号产物提供名单口令", None)
            self._wn04_short_code(case, mode, storage, named)
            self._wn04_expiry(case, mode, storage, named)
            self._wn04_shared_writer(case, mode, storage, named)
            self._wn04_cleanup_and_data_only(case, mode, storage, named)
        except HarnessError as error:
            case.check(False, "WN04 流程", str(error))
        return case.finish()

    def _wn04_short_code(self, case, mode, storage, named):
        info = self.releases["modes"][mode]
        boundary = self.kill_program(case, mode, storage, named + ["--stop-after-trial"],
                                     "WN04-boundary", 180, "SYNTHETIC_BOUNDARY_SAVED")
        case.check("SYNTHETIC_BOUNDARY_SAVED" in boundary["stdout"], "WN04：试次边界已本地提交",
                   boundary["stdout"][-160:])
        store = read_store(storage)
        sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
        case.require(len(sessions) == 1, "WN04：边界后本地保留一条会话", len(sessions))
        session_id = sessions[0].get("id")
        session_code = self.issue_short_code(info["study_id"], session_id)
        case.check(bool(session_code), "WN04：受限成员通过真实后台操作签发六位短码", session_code and "******")
        # The completion acknowledgement is held back first, so the recovered
        # state (same session, new segment, no replayed trial, declared
        # completion) is still in the real local store when it is read; the
        # confirmed retry then writes the cleaned tombstone.
        self.set_proxy("drop-completion")
        resumed = self.run_program(case, mode, storage, ["--synthetic-auto", f"--short-code={session_code}"],
                                   "WN04-short-code", 240, expect_exit=3)
        self.set_proxy("pass")
        case.check("SYNTHETIC_TIMEOUT" in resumed["stdout"], "WN04：短码恢复后完成声明如实等待确认",
                   resumed["stdout"][-200:])
        store = read_store(storage)
        sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
        case.require(len(sessions) == 1, "WN04：恢复后本地保留同一会话", len(sessions))
        session = sessions[0]
        case.check(session.get("id") == session_id, "WN04：恢复的是同一会话（未新建）", session.get("id"))
        case.check(len(session.get("segments", [])) >= 2, "WN04：恢复产生新 epoch/segment",
                   session.get("segments"))
        case.check(len(store_records(session)) == EXPECTED_EVENTS_PER_SESSION,
                   "WN04：检查点恢复后未重放已完成试次", len(store_records(session)))
        replay = self.run_program(case, mode, storage, ["--synthetic-auto", f"--short-code={session_code}"],
                                  "WN04-replay", 120, expect_exit=2)
        case.check("recovery_denied" in replay["stdout"] or "SYNTHETIC_ERROR" in replay["stdout"],
                   "WN04：重复使用已消费短码被拒绝", replay["stdout"][-200:])
        case.evidence["short_code"] = {"session_id": session_id, "segments": session.get("segments"),
                                       "event_ids": store_event_ids(session),
                                       "replay_exit": replay["exit"],
                                       "store_copy": self.snapshot_store("WN04-short-code", storage)}
        completed = self.run_program(case, mode, storage, named + ["--await-upload"],
                                     "WN04-short-code-complete", 240)
        case.check("SYNTHETIC_DATA_ONLY_DONE" in completed["stdout"],
                   "WN04：短码恢复的完成被确认并写入清理墓碑", completed["stdout"][-200:])
        store = read_store(storage)
        case.check(any(document.get("kind") == "cleaned" for document in store["sessions"]),
                   "WN04：短码恢复完成后写入已清理墓碑")

    def issue_short_code(self, study_id, session_id):
        status, payload, _ = self.api.post_form(f"/studies/{study_id}",
                                                [("op", "recover_code"), ("session_id", session_id)])
        text = payload.decode("utf-8", "replace")
        match = re.search(r"恢复码[^：:]*[：:]\s*(\d{6})", text) or re.search(r"(\d{6})", text)
        return match.group(1) if match else ""

    def _wn04_expiry(self, case, mode, storage, named):
        info = self.releases["modes"][mode]
        storage = self.run_root / "wn04-expiry"
        boundary = self.kill_program(case, mode, storage, named + ["--stop-after-trial"],
                                     "WN04-expiry-boundary", 180, "SYNTHETIC_BOUNDARY_SAVED")
        case.check("SYNTHETIC_BOUNDARY_SAVED" in boundary["stdout"], "WN04：过期检查前建立本地会话",
                   boundary["stdout"][-160:])
        store = read_store(storage)
        sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
        if not sessions:
            case.check(False, "WN04：过期短码需要本机会话", None)
            return
        session_id = sessions[0].get("id")
        code = self.issue_short_code(info["study_id"], session_id)
        case.require(bool(code), "WN04：为过期检查签发短码", None)
        wait = self.expiry_wait if self.expiry_wait is not None else 310
        self.log(f"等待 {wait}s 让短码过期（真实 5 分钟 TTL）")
        time.sleep(wait)
        expired = self.run_program(case, mode, storage, ["--synthetic-auto", f"--short-code={code}"],
                                   "WN04-expired", 120, expect_exit=2)
        case.check("recovery_denied" in expired["stdout"] or "SYNTHETIC_ERROR" in expired["stdout"],
                   "WN04：过期短码被拒绝", expired["stdout"][-200:])
        case.evidence["expiry"] = {"waited_seconds": wait, "exit": expired["exit"]}

    def _wn04_shared_writer(self, case, mode, storage, named):
        info = self.releases["modes"][mode]
        target = Path(info["_target"])
        entry = str(target / Path(info["_manifest"]["entry"]))
        shared = self.run_root / "wn04-shared-writer"
        holder = self.start_program(entry, str(target), shared, named + ["--stop-after-trial"],
                                    "WN04-shared-holder", 180)
        self.wait_for_marker(holder.out_path, ("SYNTHETIC_BOUNDARY_SAVED", "SYNTHETIC_ERROR"), 120)
        second = self.run_program(case, mode, shared, named, "WN04-shared-second", 120, expect_exit=2)
        case.check("SYNTHETIC_DONE" not in second["stdout"], "WN04：共享设备第二个实例未进入任务",
                   second["stdout"][-200:])
        stopped = self.stop_owned(holder.record)
        self.close_launch(holder)
        case.evidence["shared_writer"] = {"holder_pid": holder.record.get("pid"),
                                          "holder_stop": stopped["outcome"], "second_exit": second["exit"]}

    def _wn04_cleanup_and_data_only(self, case, mode, storage, named):
        """Declared-but-unacknowledged completion: failure export, data-only, cleanup."""
        info = self.releases["modes"][mode]
        code = info["participant_codes"][0]
        password = self.accounts.get("participants", {}).get(mode, {}).get(code, {}).get("password")
        case.require(bool(password), "WN04：私有账号产物提供名单口令", None)
        storage = self.run_root / "wn04-data-only"
        named = self.mode_arguments(case, mode, code)
        self.set_proxy("drop-completion")
        declared = self.run_program(case, mode, storage, named, "WN04-unacked-completion", 240, expect_exit=3)
        self.set_proxy("pass")
        case.check("SYNTHETIC_TIMEOUT" in declared["stdout"],
                   "WN04：完成声明未被确认时程序如实等待（不假成功）", declared["stdout"][-200:])
        store = read_store(storage)
        sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
        case.check(len(sessions) == 1 and sessions[0].get("completion") is not None,
                   "WN04：本地保留完成声明与未确认数据", {"sessions": len(sessions)})
        case.check(not [document for document in store["sessions"] if document.get("kind") == "cleaned"],
                   "WN04：未确认完成前不写清理墓碑")
        export_path = self.run_root / "wn04-recovery-export.json"
        if export_path.exists():
            export_path.unlink()
        resumed = self.run_program(case, mode, storage,
                                   named + [f"--export-recovery={export_path}"],
                                   "WN04-data-only", 240, expect_exit=2)
        case.check("SYNTHETIC_DATA_ONLY" in resumed["stdout"], "WN04：已结束会话只允许仅数据恢复",
                   resumed["stdout"][-200:])
        case.check("SYNTHETIC_RECOVERY_EXPORTED" in resumed["stdout"],
                   "WN04：程序真实写出失败数据导出文件", resumed["stdout"][-200:])
        store = read_store(storage)
        retained = [document for document in store["sessions"] if document.get("kind") == "session"]
        case.check(bool(retained) and retained[0].get("completion") is not None,
                   "WN04：失败数据导出不删除未确认队列", {"sessions": len(retained)})
        case.evidence["data_only"] = {"exit": resumed["exit"], "stdout_tail": resumed["stdout"][-200:],
                                      "stdout_path": resumed.get("stdout_path"),
                                      "store_copy": self.snapshot_store("WN04-data-only", storage)}
        self._wn04_export_binding(case, export_path, mode)
        finished = self.run_program(case, mode, storage, named + ["--await-upload"],
                                    "WN04-data-only-complete", 240)
        case.check("SYNTHETIC_DATA_ONLY_DONE" in finished["stdout"],
                   "WN04：仅数据补传完成后写入已清理墓碑", finished["stdout"][-200:])
        store = read_store(storage)
        cleaned = [document for document in store["sessions"] if document.get("kind") == "cleaned"]
        case.check(bool(cleaned), "WN04：仅数据恢复补传完成后写入已清理墓碑",
                   [document.get("id") for document in cleaned])
        if cleaned:
            keys = set(cleaned[0])
            case.check(not ({"records", "pending", "checkpoint", "completion", "context", "proof"} & keys),
                       "WN04：墓碑不含记录/凭据/检查点", sorted(keys))

    def _wn04_export_binding(self, case, export_path, mode):
        case.check(export_path.is_file(), "WN04：失败数据导出文件存在",
                   str(export_path) if export_path.is_file() else None)
        if not export_path.is_file():
            return
        document = read_json(export_path)
        binding = document.get("binding", {})
        config = self.releases["modes"][mode]["_config"]
        case.check(binding.get("instance_id") == config.get("instance_id")
                   and binding.get("study_id") == config.get("study_id")
                   and binding.get("release_id") == config.get("release_id")
                   and binding.get("build_id") == config.get("build_id"),
                   "WN04：失败数据导出绑定本实例/研究/发行/构建", binding)
        raw = export_path.read_text(encoding="utf-8")
        case.check(not any(pattern.search(raw.encode()) for pattern in SECRET_PATTERNS),
                   "WN04：失败数据导出不含凭据/令牌样式内容")
        case.check("token" not in raw.lower() and "proof" not in json.dumps(document.get("records", [])).lower(),
                   "WN04：失败数据导出记录不含会话令牌或恢复证明")
        case.check(bool(document.get("records")), "WN04：失败数据导出包含未确认记录",
                   len(document.get("records", [])))
        case.evidence["recovery_export"] = {"path": self.rel(export_path), "binding": binding,
                                            "records": len(document.get("records", [])),
                                            "sha256": sha256_file(export_path)}

    def case_wn05(self):
        case = self.case("WN05")
        try:
            for mode in MODES:
                evidence = self.cases["WN02"].evidence.get("modes", {}).get(mode)
                if not evidence:
                    case.check(False, f"{mode}：缺少 WN02 的本地/导出对账输入", None)
                    continue
                export_path = self.run_root / evidence["export_path"]
                store_path = self.run_root / evidence["store_copy"]["queue.sqlite"]
                case.check(export_path.is_file() and store_path.is_file(),
                           f"{mode}：对账输入文件存在（本地快照与授权导出）",
                           {"export": export_path.is_file(), "store": store_path.is_file()})
                lines = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines()
                         if line.strip()]
                store = read_store(store_path.parent)
                sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
                local_ids = [event_id for document in sessions for event_id in store_event_ids(document)]
                local_values = sorted(value for document in sessions for value in raw_values(document))
                remote_ids = [entry["record"].get("event_id") for entry in lines]
                remote_values = sorted(float(entry["record"]["payload"]["rt_ms"]) for entry in lines
                                       if isinstance(entry["record"].get("payload", {}).get("rt_ms"), (int, float)))
                case.check(sorted(local_ids) == sorted(evidence["boundary_event_ids"]),
                           f"{mode}：快照可独立读回本地事件 ID", {"local": len(local_ids)})
                case.check(set(local_ids) <= set(remote_ids) and len(remote_ids) == EXPECTED_EVENTS_PER_SESSION,
                           f"{mode}：本地事件 ID 全部出现在授权导出且导出完整",
                           {"local": len(local_ids), "remote": len(remote_ids)})
                case.check(local_values == evidence["boundary_raw_values"] == [321.5]
                           and remote_values == sorted(RAW_VALUES),
                           f"{mode}：本地/导出原始值逐值一致",
                           {"local": local_values, "remote": remote_values})
                case.check(all(entry["record"].get("payload", {}).get("trial_id") in ("t1", "t2")
                               for entry in lines if entry["record"].get("event_type") == "exp.rt"),
                           f"{mode}：授权导出保留原始 trial_id")
                case.check(not any(pattern.search(export_path.read_bytes()) for pattern in SECRET_PATTERNS),
                           f"{mode}：授权导出不含凭据样式内容")
                case.evidence.setdefault("modes", {})[mode] = {
                    "local_event_ids": local_ids, "remote_event_ids": remote_ids,
                    "local_values": local_values, "remote_values": remote_values,
                    "export_sha256": evidence["export_sha256"], "export_path": evidence["export_path"]}
            recovery = self.cases["WN04"].evidence.get("recovery_export")
            case.check(bool(recovery), "失败数据导出记录已采集", bool(recovery))
            case.evidence["export_requests"] = [record for record in self.api.records
                                                if "/v1/admin/exports" in record["path"]]
            case.evidence["api_requests"] = self.api.records
        except HarnessError as error:
            case.check(False, "WN05 流程", str(error))
        return case.finish()

    def case_wn06(self):
        case = self.case("WN06")
        try:
            case.evidence["platform_side"] = "PREP_EVIDENCE"
            case.evidence["known_limitation"] = ("本地副本被篡改的 PCK 不会被程序运行时自动拒绝：运行时不做包完整性校验，"
                                                 "不冒充通过。")
            old = self.releases["modes"]["password"]
            current = (self.runtime.get("alternates") or {}).get("password")
            if not current:
                case.check(False, "WN06：运行时夹具缺少第二个发行（旧发行成为非当前发行的输入）", None)
            else:
                package = self.kit / old["delivery"]
                case.require(package.is_file(), "WN06：kit 内含旧发行完整包", old["release_id"])
                target = self.extract(package, "password-old-release")
                manifest = read_json(target / "artifact_manifest.json")
                config = read_json(target / Path(manifest["config_member"]))
                case.check(config.get("release_id") == old["release_id"],
                           "WN06：旧发行的冻结配置绑定其发行 id", config.get("release_id"))
                case.check(config.get("release_id") != current["release_id"],
                           "WN06：该发行已不是当前发行（当前为第二个发行）",
                           {"old": old["release_id"], "current": current["release_id"]})
                storage = self.run_root / "wn06-storage"
                arguments = self.mode_arguments(case, "password")
                self.kill_program(case, "password", storage, arguments + ["--stop-after-trial"],
                                  "WN06-old-release-boundary", 180, "SYNTHETIC_BOUNDARY_SAVED",
                                  target=target, manifest=manifest)
                store = read_store(storage)
                sessions = [document for document in store["sessions"] if document.get("kind") == "session"]
                case.require(len(sessions) == 1, "WN06：旧发行试次边界后本地恰好一条会话", len(sessions))
                session_id = sessions[0].get("id")
                boundary_ids = store_event_ids(sessions[0])
                case.check(len(boundary_ids) == 2, "WN06：旧发行边界前两个事件已本地提交", len(boundary_ids))
                boundary_copy = self.snapshot_store("WN06-old-release-boundary", storage)
                completed = self.run_program(case, "password", storage, arguments, "WN06-old-release", 240,
                                             target=target, manifest=manifest)
                case.check("SYNTHETIC_DONE" in completed["stdout"],
                           "WN06：旧发行（已不是当前发行）仍可准入、上传并完成", completed["stdout"][-200:])
                store = read_store(storage)
                cleaned = [document for document in store["sessions"] if document.get("kind") == "cleaned"]
                case.check(bool(cleaned), "WN06：旧发行完成后写入已清理墓碑", len(cleaned))
                export_id, payload = self.api.export_jsonl(old["study_id"])
                export_path = self.evidence_dir("WN06") / "export.jsonl"
                lines, export_full = self.scoped_export(payload, session_id, export_path)
                old_ids = [entry["record"].get("event_id") for entry in lines]
                case.check(len(lines) == EXPECTED_EVENTS_PER_SESSION,
                           "WN06：旧发行在授权导出中保留完整四个事件", len(lines))
                case.check(len(set(old_ids)) == len(old_ids), "WN06：旧发行导出没有重复事件")
                case.check(set(boundary_ids) <= set(old_ids),
                           "WN06：旧发行本地事件 ID 与授权导出一致")
                case.evidence["old_release"] = {
                    "release_id": old["release_id"], "study_id": old["study_id"],
                    "session_id": session_id, "event_ids": boundary_ids,
                    "export_id": export_id, "export_path": self.rel(export_path),
                    "export_sha256": sha256_file(export_path), "export_event_ids": old_ids,
                    "export_full": export_full,
                    "store_copy": boundary_copy,
                    "stdout": self.rel(self.evidence_dir("WN06-old-release") / "stdout.txt")}
        except HarnessError as error:
            case.check(False, "WN06 流程", str(error))
        return case.finish()

    # ------------------------------------------------------------------ driver
    def set_proxy(self, mode):
        if self.proxy is not None:
            self.proxy.stop()
            self.proxy = None
        if mode == "pass" and not self.tunnel_required():
            return
        self.proxy = FaultProxy(self.api_port, self.tunnel_port, mode=mode,
                                log_path=self.run_root / "fault-proxy.json").start()

    def tunnel_required(self):
        return True

    def tunnel_serves(self, timeout=15):
        """End-to-end read-only check of the scoped tunnel, from the device side.

        A live listener on the forwarded port is not enough: a dead reverse
        session can keep the Windows-side port bound while nothing answers. The
        only honest prerequisite check is a real HTTP round trip through the
        tunnel to the prepared instance's login page (read-only, no token), so a
        broken tunnel becomes a precise blocked reason instead of per-case
        ``http_0`` evidence.
        """
        import http.client
        try:
            connection = http.client.HTTPConnection("127.0.0.1", int(self.tunnel_port), timeout=timeout)
            try:
                connection.request("GET", "/login", headers={"Host": "admin.localhost"})
                response = connection.getresponse()
                response.read(4096)
                if response.status == 200:
                    return True, f"HTTP {response.status}"
                return False, f"HTTP {response.status}"
            finally:
                connection.close()
        except OSError as error:
            return False, f"{type(error).__name__}: {error}"

    def prerequisite_lost(self, case_id):
        """Block one case when the scoped tunnel stopped serving mid-run."""
        try:
            with socket.create_connection(("127.0.0.1", self.tunnel_port), timeout=5):
                pass
        except OSError as error:
            ok, detail = False, f"TCP {type(error).__name__}"
        else:
            ok, detail = self.tunnel_serves()
        if ok:
            return False
        case = self.case(case_id)
        case.block(f"runtime prerequisite lost: 127.0.0.1:{self.tunnel_port} no longer serves the "
                   f"prepared instance ({detail}); the scoped tunnel must stay up for the whole run")
        case.evidence["runtime_prerequisite"] = {"port": self.tunnel_port, "detail": detail}
        self.log(f"{case_id}：{case.reason}")
        return True

    def doctor(self):
        """Host/kit/ACL readiness without executing the frozen program.

        Doctor evidence is preparation-only: it never claims a WN01 result, and it
        never substitutes for the full engineering run.
        """
        case = self.case("WN01")
        self.check_kit_integrity(case)
        if sys.platform == "win32":
            self.check_delivery(case)
            self.check_accounts_acl(case)
        else:
            case.check(False, "doctor 需要真实 Windows 主机（准备机只做 --verify-preparation）", sys.platform)
        case.evidence["host"] = self.host
        case.status = STATUS_NOT_RUN
        case.reason = "doctor 只检查准备；不执行冻结程序，也不产生 WN01 结论"
        return {"doctor": True}

    def run(self):
        if sys.platform != "win32":
            raise HarnessError("the full engineering run is only valid on Windows")
        for case_id in CASE_TITLES:
            self.case(case_id)
        if self.local_data is not None:
            # Optional read-only inspection of an extra native data directory
            # (the explicit queue.sqlite/writer.sqlite contract, never a glob).
            extra = read_store(self.local_data)
            self.cases["WN01"].evidence["extra_local_data"] = {
                "path": self.rel(self.local_data), "queue_exists": extra["exists"],
                "writer_exists": extra["writer_exists"],
                "sessions": len(extra["sessions"]), "event_ids": [event_id for document in extra["sessions"]
                                                                  for event_id in store_event_ids(document)]}
            self.log(f"附加本地存储只读检查：{self.local_data}")
        blocked = not self.preflight_tunnel(self.cases["WN01"])
        for case_id in ("WN02", "WN03", "WN04", "WN05"):
            if blocked:
                self.cases[case_id].block(self.cases["WN01"].reason)
        if not blocked:
            self.set_proxy("pass")
        self.check_kit_integrity(self.cases["WN01"])
        self.check_delivery(self.cases["WN01"])
        self.check_accounts_acl(self.cases["WN01"])
        # A prerequisite failure (kit integrity, delivery, ACL) is never erased by a
        # later successful launch: passing preflight checks are dropped so WN01
        # reports the interactive launch, failures stay and stop the runtime cases.
        failures = [check for check in self.cases["WN01"].checks if not check["ok"]]
        self.cases["WN01"].checks = failures
        if blocked or failures:
            self.cases["WN01"].finish()
            for case_id in ("WN02", "WN03", "WN04", "WN05"):
                self.cases[case_id].block("kit/ACL 前置检查失败，未执行运行时用例")
            return
        self.authenticate_member(self.cases["WN02"])
        self.cases["WN01"].status = STATUS_NOT_RUN
        self.case_wn01()
        for case_id, runner in (("WN02", self.case_wn02), ("WN03", self.case_wn03), ("WN04", self.case_wn04),
                                ("WN05", self.case_wn05), ("WN06", self.case_wn06)):
            if self.prerequisite_lost(case_id):
                continue
            runner()

    def wait_owned_program(self, launch, timeout=None, poll=2.0):
        """Wait until the program this harness started has exited.

        An interactive launch owns its launcher process, so its exit is the
        program exit. A scheduled launch owns no launcher handle: the PowerShell
        script writes the same ``exit.json`` record when the program ends, so the
        harness waits for that owned record instead of closing the task (and the
        fault proxy that holds the frozen port) while the program is still
        running. Only our own objects are observed; no foreign process is probed
        or stopped.
        """
        if launch.launcher is not None:
            launch.launch_exit = launch.launcher.wait()
            return True
        deadline = None if timeout is None else time.time() + timeout
        while deadline is None or time.time() < deadline:
            if Path(launch.exit_path).is_file():
                document = None
                try:
                    document = read_json(launch.exit_path)
                except ValueError:
                    document = None
                launch.launch_exit = (document or {}).get("exit")
                return True
            time.sleep(poll)
        return False

    def designer_launch(self):
        """Start the designer's real interactive program through the verified route.

        The scoped tunnel is a runtime prerequisite: without it the frozen loopback
        port cannot reach the preparation host, so the launcher refuses precisely
        instead of starting a program that cannot connect. The fault proxy that owns
        the frozen port lives exactly as long as the program - it is started before
        the program and stopped when the program closes - and only our own
        launcher/task/script are released afterwards. This is test-environment
        preparation, never a WN01 result and never human QA.
        """
        if sys.platform != "win32":
            raise HarnessError("the prepared launcher only runs on Windows")
        case = self.case("WN01")
        if not self.preflight_tunnel(case):
            self.log(f"外部前置未满足：{case.reason}")
            case.finish()
            return {"designer": False, "runtime_prerequisite": "PENDING", "reason": case.reason}
        mode = self.mode or "anonymous"
        info = self.releases["modes"][mode]
        target = self.extract(self.kit / info["delivery"], mode)
        storage = self.run_root / f"designer-{mode}"
        entry = str(target / Path(info["_manifest"]["entry"]))
        self.set_proxy("pass")
        launch = None
        try:
            launch = self.start_program(entry, str(target), storage, [], f"designer-{mode}", 60)
            record = launch.record
            hint = f"账号见 {self.accounts_path}" if self.accounts_path else "账号见私有账号产物"
            self.log(f"已启动 {mode} 模式真实程序 pid={record.get('pid')}；本地数据目录 {storage}；{hint}")
            # The helper lifetime spans the whole program use, in both launch
            # branches: interactive launches wait for their own launcher process,
            # scheduled launches wait for the owned exit record the script writes
            # when the program ends.
            if not self.wait_owned_program(launch):
                self.log("未在时限内观察到自有程序退出记录；释放自有资源前请确认程序已关闭")
        finally:
            if launch is not None:
                self.close_launch(launch)
            if self.proxy is not None:
                self.proxy.stop()
                self.proxy = None
        case.status = STATUS_NOT_RUN
        case.reason = "designer 启动只准备人工体验；不执行 WN01 工程结论"
        return {"designer": True, "mode": mode, "pid": launch.record.get("pid") if launch else None,
                "storage": str(storage), "accounts_path": str(self.accounts_path) if self.accounts_path else None}

    def close_owned_launches(self):
        """Release only the launches this harness created; never a foreign program."""
        for launch in list(self.launches):
            try:
                if launch.record:
                    self.close_launch(launch)
                else:
                    self.abort_launch(launch)
            except OSError as error:  # releasing our own objects must not mask the run result
                self.log(f"释放自有启动器失败：{error}")

    # ------------------------------------------------------------------ report
    def finish(self, mode_name, error=None):
        documents = [self.cases[case_id].document() for case_id in CASE_TITLES if case_id in self.cases]
        failures = [check for document in documents for check in document["checks"] if not check["ok"]]
        statuses = {document["id"]: document["status"] for document in documents}
        acceptance = STATUS_PASS if documents and all(status == STATUS_PASS for status in statuses.values()) \
            else (STATUS_NOT_RUN if not documents else
                  (STATUS_BLOCKED if any(status == STATUS_BLOCKED for status in statuses.values()) else STATUS_FAIL))
        report = {
            "format": HARNESS_FORMAT,
            "task": "P0307WR",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode": mode_name,
            "run_id": self.run_id,
            "host": self.host,
            "binding": {"instance_id": self.runtime.get("instance_id"), "api_url": self.runtime.get("api_url"),
                        "api_port": self.runtime.get("port"), "tunnel_port": self.tunnel_port,
                        "kit_format": self.releases.get("kit_format"),
                        "studies": self.runtime.get("studies", {})},
            "expected": self.expected,
            "kit_integrity_sha256": sha256_file(self.kit / SIDECAR_NAME) if self.kit and (self.kit / SIDECAR_NAME).is_file() else None,
            "harness_sha256": sha256_file(Path(__file__)),
            "cases": documents,
            "checks_total": sum(len(document["checks"]) for document in documents),
            "checks_failed": len(failures),
            "failed": [{"case": document["id"], "label": check["label"], "detail": check["detail"]}
                       for document in documents for check in document["checks"] if not check["ok"]],
            "runtime_acceptance": acceptance,
            "designer_autonomous_qa": "NOT_RUN",
            "independent_t17": "NOT_RUN",
            "notes": ["本 harness 只产生工程证据；不等于原设计者自主体验或独立 T17。",
                      "平台侧 WN06 契约由准备门槛执行；客户端旧发行兼容仍需实机第二条发行血脉。",
                      "本地副本被篡改的 PCK 不被运行时完整性校验拒绝，属已知限制。"],
            "artifacts": self.artifacts,
            "fault_proxy": self.proxy.events if self.proxy else [],
        }
        if error is not None:
            report["error"] = error
        target = self.json_out or (self.run_root / "run.json" if self.run_root else None)
        if target is not None:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"mode": mode_name, "checks_total": report["checks_total"],
                          "checks_failed": report["checks_failed"], "runtime_acceptance": acceptance},
                         ensure_ascii=False))
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Windows x64 native engineering harness (03F)")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser.add_argument("--doctor", action="store_true", help="host/kit/accounts checks without executing the program")
    parser.add_argument("--run", action="store_true", help="full engineering pass (real program, real API, real store)")
    parser.add_argument("--designer-launch", action="store_true", help="prepared launcher for the designer's manual run")
    parser.add_argument("--fault-proxy", action="store_true", help="internal scoped fault-injection proxy")
    parser.add_argument("--kit", default=None, help="kit directory (gep-windows-kit/v2)")
    parser.add_argument("--runtime", default=None, help="operator runtime.json (no secrets)")
    parser.add_argument("--accounts", default=None, help="private runtime_accounts.json (usable accounts)")
    parser.add_argument("--run-root", default=None, help="fresh directory for this run's evidence")
    parser.add_argument("--json-out", default=None, help="machine-readable report path")
    parser.add_argument("--local-data", default=None, help="extra native data directory to inspect read-only")
    parser.add_argument("--mode", default=None, choices=MODES, help="single mode for --designer-launch")
    parser.add_argument("--tunnel-port", type=int, default=None, help="scoped tunnel port (defaults to api port + 100)")
    parser.add_argument("--expiry-wait", type=int, default=None, help="seconds to wait for the short code to expire")
    parser.add_argument("--listen-port", type=int, default=None, help="--fault-proxy listen port")
    parser.add_argument("--target-port", type=int, default=None, help="--fault-proxy upstream port")
    parser.add_argument("--proxy-mode", default="pass",
                        choices=("pass", "drop-events", "drop-completion", "lose-first-ack", "offline"))
    parser.add_argument("--fault-log", default=None, help="--fault-proxy event log")
    args = parser.parse_args(argv)
    if args.fault_proxy:
        return fault_proxy_main(args)
    if not (args.doctor or args.run or args.designer_launch):
        parser.error("pass --doctor, --run, --designer-launch or --fault-proxy")
    run_root = Path(args.run_root).resolve() if args.run_root else Path.cwd() / "gep-windows-run"
    run_root.mkdir(parents=True, exist_ok=True)
    harness = Harness(kit=args.kit, runtime=args.runtime, accounts=args.accounts, run_root=run_root,
                      json_out=args.json_out, local_data=args.local_data, mode=args.mode,
                      tunnel_port=args.tunnel_port, expiry_wait=args.expiry_wait,
                      designer=args.designer_launch)
    harness.host = harness.host_facts()
    mode_name = "doctor" if args.doctor else ("designer-launch" if args.designer_launch else "run")
    error = None
    try:
        harness.load_inputs()
        if args.doctor:
            harness.doctor()
        elif args.designer_launch:
            harness.designer_launch()
        else:
            harness.run()
    except HarnessError as harness_error:
        error = str(harness_error)
        harness.log(f"harness aborted: {error}")
        for case_id in CASE_TITLES:
            case = harness.case(case_id)
            if not case.checks:
                case.check(False, "harness 前置条件", error)
    except Exception as unexpected:  # unexpected: keep the evidence, fail loudly
        error = repr(unexpected)
        harness.log(f"unexpected harness failure: {error}")
        for case_id in CASE_TITLES:
            case = harness.case(case_id)
            if not case.checks:
                case.check(False, "harness 前置条件", error)
    finally:
        if harness.proxy is not None:
            harness.proxy.stop()
        harness.close_owned_launches()
    report = harness.finish(mode_name, error=error)
    if mode_name == "run":
        return 0 if report["runtime_acceptance"] == STATUS_PASS else 1
    # A doctor/launcher run is preparation-only: it exits 0 only when every
    # executed check passed, and it never claims a WN01 result.
    return 0 if report["checks_failed"] == 0 and error is None else 1


if __name__ == "__main__":
    sys.exit(main())
