#!/usr/bin/env python3
"""Phase 03 shell verification (03D).

Owns one temporary synthetic instance (its own data directory, its own database
and a port >= 8040) and drives the real exported Godot Web and macOS arm64
builds through the reusable GEC shell: admission in all three frozen modes, the
six-digit and named same-device recovery paths with explicit continuation, long
permits, offline/ACK-loss/reopen/trial-checkpoint, cleaned tombstones and the
single-writer boundary.

Every participant-side step goes through the shell UI (browser DOM panel or the
exported native program); the researcher-side setup and export use the real
admin GUI. Local stores, the server database and the authorized JSONL export are
compared by original event identity and value. Evidence stays under
``local_data/phase03_20260920/shell_verify/<stamp>/`` and nothing outside it is
read or written. Missing tooling fails loudly instead of skipping.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

ROOT = Path(__file__).resolve().parents[1]
PHASE_ROOT = ROOT / "local_data" / "phase03_20260920"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
GODOT = shutil.which("godot")
BINARY = ROOT / "build" / "native" / "GEP Synthetic Experiment.app" / "Contents" / "MacOS" / "GEP Synthetic Experiment"
WEB_ZIP = ROOT / "build" / "synthetic_web.zip"
NATIVE_DESCRIPTOR = ROOT / "build" / "native" / "descriptor.json"
DRIVER = ROOT / "tests" / "browser" / "phase03_shell_verify.mjs"
ROSTER_ID = "001"
ROSTER_PASSWORD = "synthetic-password"


class VerificationError(Exception):
    pass


class LineReader:
    """Non-blocking line collector for a child process."""

    def __init__(self, stream):
        self.lines = []
        self._stream = stream
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        for line in self._stream:
            self.lines.append(line.rstrip("\n"))

    def text(self):
        return "\n".join(self.lines)

    def wait_for(self, marker, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if marker in self.text():
                return True
            time.sleep(0.2)
        return False


class Verify:
    def __init__(self, root: Path):
        # Absolute paths only: the exported program is launched with a config
        # path, and a relative evidence root would make it unreadable to the
        # child process (which resolves its own working directory).
        self.root = Path(root).resolve()
        self.data = self.root / "data"
        self.evidence = self.root / "evidence"
        self.native_root = self.root / "native"
        self.db_path = self.data / "gep.sqlite3"
        self.owner_password = secrets.token_urlsafe(24)
        self.instance_id = str(uuid.uuid4())
        self.secret_key = secrets.token_urlsafe(48)
        self.port = None
        self.server = None
        self.checks = []
        self.artifacts = {}
        self.studies = {}
        self.summary_lines = []

    # ------------------------------------------------------------------ helpers
    def log(self, message):
        line = f"[phase03-shell] {message}"
        print(line, flush=True)
        self.summary_lines.append(line)

    def record(self, ok, label, detail=None):
        entry = {"ok": bool(ok), "label": label, "detail": detail}
        self.checks.append(entry)
        state = "ok  " if ok else "FAIL"
        self.log(f"{state} {label}" + (f" :: {detail}" if detail is not None else ""))
        return bool(ok)

    def require(self, ok, label, detail=None):
        if not self.record(ok, label, detail):
            raise VerificationError(label)

    def preconditions(self):
        missing = []
        if GODOT is None:
            missing.append("godot executable not on PATH (Godot 4.7.2 required)")
        if not VENV_PYTHON.exists():
            missing.append(f"project virtualenv missing: {VENV_PYTHON}")
        if not DRIVER.exists():
            missing.append(f"browser driver missing: {DRIVER}")
        if not BINARY.exists():
            missing.append(f"exported macOS build missing: {BINARY} (run tools/build.py first)")
        if not WEB_ZIP.exists():
            missing.append(f"exported Web archive missing: {WEB_ZIP} (run tools/build.py first)")
        if not NATIVE_DESCRIPTOR.exists():
            missing.append(f"native descriptor missing: {NATIVE_DESCRIPTOR} (run tools/build.py first)")
        if shutil.which("node") is None:
            missing.append("node executable not on PATH (Playwright driver required)")
        if missing:
            raise VerificationError("missing tool environment: " + "; ".join(missing))

    def free_port(self):
        for candidate in range(8040, 8100):
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", candidate))
                except OSError:
                    continue
            return candidate
        raise VerificationError("no free port >= 8040")

    def server_env(self):
        return {
            **os.environ,
            "GEP_DATA_DIR": str(self.data),
            "GEP_SECRET_KEY": self.secret_key,
            "GEP_EXPECTED_INSTANCE": self.instance_id,
            "GEP_PUBLIC_API": f"http://experiment.localhost:{self.port}",
            "PYTHONPATH": str(ROOT / "server"),
            "DJANGO_SETTINGS_MODULE": "gep.settings",
        }

    def run_python(self, code, extra_env=None, timeout=120):
        env = self.server_env()
        if extra_env:
            env.update(extra_env)
        return subprocess.run([str(VENV_PYTHON), "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)

    # ----------------------------------------------------------------- instance
    def init_instance(self):
        self.data.mkdir(parents=True, exist_ok=True)
        code = (
            "import os, uuid, django\n"
            "django.setup()\n"
            "from django.core.management import call_command\n"
            "from django.contrib.auth import get_user_model\n"
            "from core.models import AccountProfile, Instance\n"
            "call_command('migrate', verbosity=0)\n"
            "owner = get_user_model().objects.create_user('synthetic_owner', password=os.environ['GEP_OWNER_PASSWORD'])\n"
            "Instance.objects.create(instance_id=uuid.UUID(os.environ['GEP_INSTANCE_ID']), owner=owner)\n"
            "AccountProfile.objects.create(user=owner, role='user', must_change_password=False, auth_version=1, revision=0)\n"
            "print('INSTANCE_READY')\n"
        )
        result = self.run_python(code, {"GEP_OWNER_PASSWORD": self.owner_password, "GEP_INSTANCE_ID": self.instance_id})
        if "INSTANCE_READY" not in result.stdout:
            raise VerificationError(f"temporary instance initialization failed: {result.stdout}\n{result.stderr}")
        for name, value in (("secret", self.secret_key), ("instance", self.instance_id)):
            path = self.data / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(value)

    def start_server(self, port=None):
        # An explicit port lets a prepared environment keep its frozen loopback
        # endpoint (designer readiness); the default still picks a fresh >= 8040.
        self.port = int(port) if port else self.free_port()
        log = open(self.root / "gunicorn.log", "w")
        self.server = subprocess.Popen(
            [str(ROOT / ".venv" / "bin" / "gunicorn"), "gep.wsgi:application", "--bind", f"127.0.0.1:{self.port}",
             "--workers", "1", "--threads", "4", "--access-logfile", "-"],
            cwd=ROOT, env=self.server_env(), stdout=log, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return
            except OSError:
                if self.server.poll() is not None:
                    raise VerificationError(f"temporary server exited early, see {self.root / 'gunicorn.log'}")
                time.sleep(0.3)
        raise VerificationError("temporary server did not become ready")

    def stop_server(self):
        if self.server and self.server.poll() is None:
            self.server.send_signal(signal.SIGTERM)
            try:
                self.server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.server.kill()

    # ------------------------------------------------------------------ browser
    def driver(self, command, job):
        job_path = self.root / f"job_{command}.json"
        job_path.write_text(json.dumps(job))
        result = subprocess.run(["node", str(DRIVER), command, str(job_path)], cwd=ROOT, capture_output=True, text=True, timeout=900)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise VerificationError(f"browser driver {command} produced no JSON (exit {result.returncode})\nstdout:{result.stdout[-2000:]}\nstderr:{result.stderr[-2000:]}")
        payload["_exit"] = result.returncode
        payload["_stderr"] = result.stderr[-4000:]
        (self.root / f"driver_{command}.json").write_text(json.dumps(payload, indent=2))
        return payload

    def driver_job(self):
        return {
            "admin_url": f"http://admin.localhost:{self.port}",
            "experiment_url": f"http://experiment.localhost:{self.port}",
            "credentials": {"username": "synthetic_owner", "password": self.owner_password},
            "run_dir": str(self.evidence),
            "roster_id": ROSTER_ID,
            "roster_password": ROSTER_PASSWORD,
            "study_specs": [
                {"key": "anonymous", "title": "Shell anonymous", "mode": "anonymous", "max_sessions": 4,
                 "upload_web": str(WEB_ZIP), "native_descriptor": str(NATIVE_DESCRIPTOR),
                 "approve": ["godot_web", "macos_arm64"], "current": "web"},
                {"key": "id", "title": "Shell id", "mode": "id", "max_sessions": 5, "roster": ROSTER_ID,
                 "upload_web": str(WEB_ZIP), "native_descriptor": str(NATIVE_DESCRIPTOR),
                 "approve": ["godot_web", "macos_arm64"], "current": "web"},
                {"key": "password", "title": "Shell password", "mode": "password", "max_sessions": 16,
                 "roster": f"{ROSTER_ID},{ROSTER_PASSWORD}",
                 "upload_web": str(WEB_ZIP), "native_descriptor": str(NATIVE_DESCRIPTOR),
                 "approve": ["godot_web", "macos_arm64"], "current": "web"},
            ],
        }

    # ------------------------------------------------------------------- native
    def native_launch(self, storage, config, args, env_extra=None):
        storage.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GEP_SYNTHETIC_STORAGE": str(storage)}
        if env_extra:
            env.update(env_extra)
        proc = subprocess.Popen([str(BINARY), "--headless", "--", f"--config={config}", *args],
                                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        reader = LineReader(proc.stdout)
        return proc, reader

    def native_run(self, storage, config, args, timeout=120):
        proc, reader = self.native_launch(storage, config, args)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise VerificationError(f"native run timed out: {args}\n{reader.text()[-2000:]}")
        return code, reader.text()

    def native_store(self, storage, timeout=20):
        path = storage / "queue.sqlite"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20)
                try:
                    return [json.loads(row[0]) for row in connection.execute("select value from sessions")]
                finally:
                    connection.close()
            except sqlite3.Error:
                time.sleep(0.3)
        raise VerificationError(f"native queue not readable: {path}")

    def wait_boundary(self, storage, proc, reader, timeout=90):
        """Wait for the first committed trial boundary in the real local store.

        Godot buffers stdout while it is a pipe, so a running process is observed
        through its durable store (as the existing native acceptance specs do)
        instead of through an unflushed marker line.
        """
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            try:
                record = self.live_session(storage)
            except VerificationError as error:
                # Godot may still be starting (extension load, slow disk); a not
                # yet readable store is retried until the deadline, and the exact
                # reason plus the program output is reported if it never appears.
                last_error, record = error, None
            if record and record.get("checkpoint") and int(record["checkpoint"].get("next_trial", 0)) >= 1:
                return record
            if proc.poll() is not None:
                raise VerificationError(f"native program exited before the trial boundary (exit {proc.returncode}): {reader.text()[-1000:]}")
            time.sleep(0.4)
        raise VerificationError(f"native trial boundary not observed within {timeout}s ({last_error}): "
                                f"{reader.text()[-1000:]}")

    def native_partial(self, storage, config, args, timeout=90):
        proc, reader = self.native_launch(storage, config, args)
        try:
            return self.wait_boundary(storage, proc, reader, timeout)
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ------------------------------------------------------------------ database
    def db_events(self, session_id):
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=20)
        try:
            rows = connection.execute("select envelope from core_event where session_id=?", [session_id.replace("-", "")]).fetchall()
        finally:
            connection.close()
        return [json.loads(row[0]) for row in rows]

    def db_session_count(self, study_id):
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=20)
        try:
            return connection.execute(
                "select count(*) from core_session join core_release on core_session.release_id=core_release.id where core_release.study_id=?",
                [study_id.replace("-", "")]).fetchone()[0]
        finally:
            connection.close()

    def expire_session(self, session_id):
        """Expire one session in the verifier's own temporary database."""
        connection = sqlite3.connect(f"file:{self.db_path}", uri=True, timeout=20)
        try:
            connection.execute("update core_session set expires_at=? where id=?",
                               ["2000-01-01 00:00:00", session_id.replace("-", "")])
            connection.commit()
        finally:
            connection.close()

    def session_expired(self, session_id):
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=20)
        try:
            row = connection.execute("select expires_at from core_session where id=?", [session_id.replace("-", "")]).fetchone()
        finally:
            connection.close()
        return bool(row) and str(row[0]) < "2020-01-01"

    def token_refused(self, session_id, token):
        request = urlrequest.Request(f"http://127.0.0.1:{self.port}/v1/participant/sessions/{session_id}/status",
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urlrequest.urlopen(request, timeout=15) as response:
                return response.status != 200
        except urlerror.HTTPError as error:
            return error.code in (401, 403)

    def wait_db_events(self, session_id, count, timeout=40):
        deadline = time.time() + timeout
        events = []
        while time.time() < deadline:
            events = self.db_events(session_id)
            if len(events) >= count:
                return events
            time.sleep(0.5)
        return events

    # -------------------------------------------------------------- comparisons
    @staticmethod
    def canonical(records):
        keys = ("event_id", "sequence", "segment_id", "event_type", "payload", "observed_time")
        return sorted(json.dumps({key: record.get(key) for key in keys}, sort_keys=True) for record in records)

    def compare_records(self, label, local, remote, exported):
        self.require(len(local) > 0, f"{label}: local records present", len(local))
        self.require(self.canonical(local) == self.canonical(remote), f"{label}: server database matches local by event id/value",
                     {"local": len(local), "remote": len(remote)})
        self.require(self.canonical(local) == self.canonical(exported), f"{label}: authorized JSONL matches local by event id/value",
                     {"local": len(local), "exported": len(exported)})

    def export_study(self, key, study):
        out = self.evidence / f"export_{key}.jsonl"
        result = self.driver("export", {"study_url": study["study_url"], "out": str(out),
                                        "admin_url": f"http://admin.localhost:{self.port}",
                                        "credentials": {"username": "synthetic_owner", "password": self.owner_password}})
        if not out.exists():
            raise VerificationError(f"JSONL export missing for {key}: {result.get('error') or result}")
        records = []
        for line in out.read_text().strip().splitlines():
            row = json.loads(line)
            records.append(row["record"])
        return records

    # --------------------------------------------------------------------- main
    def verify(self):
        self.preconditions()
        self.record(True, "pinned toolchain present", {"godot": GODOT})
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.native_root.mkdir(parents=True, exist_ok=True)
        self.init_instance()
        self.start_server()
        self.record(True, "temporary instance and server ready", {"port": self.port})
        try:
            self.browser_phase()
            self.native_phase()
            self.compare_phase()
        finally:
            for proxy in getattr(self, "proxies", []):
                proxy.stop()
            self.stop_server()
        failures = [check for check in self.checks if not check["ok"]]
        (self.root / "evidence.json").write_text(json.dumps({"checks": self.checks, "artifacts": self.artifacts}, indent=2, default=str))
        (self.root / "summary.txt").write_text("\n".join(self.summary_lines) + "\n")
        return not failures

    def browser_phase(self):
        result = self.driver("all", self.driver_job())
        self.require(not result.get("error"), "browser driver completed", result.get("error"))
        self.studies = result["evidence"]["studies"]
        for entry in result["checks"]:
            self.record(entry["ok"], "web: " + entry["label"], entry.get("detail"))
        self.artifacts["web"] = {
            "anonymous": result["evidence"]["anonymous"],
            "id": result["evidence"]["id"],
            "password": result["evidence"]["password"],
        }

    def live_session(self, storage):
        for record in self.native_store(storage):
            if record.get("kind") == "session":
                return record
        return None

    def native_await_completion(self, storage, config, args, timeout=150, require_pending=False):
        """Run the exported program until it declared a local completion while a
        proxy keeps dropping ACKs, then keep the real local store. With
        ``require_pending`` the retained batch must still be unreceived."""
        proc, reader = self.native_launch(storage, config, args)
        try:
            deadline = time.time() + timeout
            last_error = None
            while time.time() < deadline:
                try:
                    record = self.live_session(storage)
                except VerificationError as error:
                    # Slow first start under load must not be reported as a
                    # missing store; retry until the deadline with the reason.
                    last_error, record = error, None
                if record and record.get("completion") and len(record.get("records", [])) == 4:
                    if require_pending:
                        if record.get("pending"):
                            return record, reader.text()
                    elif not record.get("pending"):
                        return record, reader.text()
                if proc.poll() is not None:
                    raise VerificationError(f"native program exited before completion: {reader.text()[-2000:]}")
                time.sleep(0.4)
            raise VerificationError(f"native completion not observed within {timeout}s ({last_error}): "
                                    f"{reader.text()[-2000:]}")
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    def new_proxy(self, drop_markers=("/completion",), drop_before_forward=False):
        proxy = FaultProxy(self.port, drop_markers, drop_before_forward=drop_before_forward)
        self.proxies.append(proxy)
        return proxy

    def proxied_config(self, key, study, proxy, suffix="proxy"):
        """Public configuration whose API target is the fault proxy, so the
        session's own frozen config keeps pointing at it for later runs."""
        config = json.loads(Path(study["config_path"]).read_text())
        config["api_url"] = f"http://127.0.0.1:{proxy.port}"
        path = self.native_root / f"connection_{key}_{suffix}.json"
        path.write_text(json.dumps(config))
        return str(path)

    def native_phase(self):
        studies = self.studies
        self.proxies = []
        credentials = ["--participant-code=" + ROSTER_ID, "--password=" + ROSTER_PASSWORD]

        # Anonymous: the dropped completion ACK keeps the real local store
        # inspectable; a later run retransmits and cleans it up without duplicates.
        proxy = self.new_proxy()
        config = self.proxied_config("anonymous", studies["anonymous"], proxy)
        storage = self.native_root / "anonymous"
        snapshot, _output = self.native_await_completion(storage, config, ["--synthetic-auto"])
        self.require(len(snapshot["records"]) == 4, "native local store kept four records across the dropped ACK", len(snapshot["records"]))
        self.require(len(snapshot["pending"]) == 0, "native dropped ACK left no unacknowledged batch", len(snapshot["pending"]))
        proxy.drop_markers = ()
        code, output = self.native_run(storage, config, ["--synthetic-auto"])
        self.require(code == 0 and "SYNTHETIC_DONE" in output, "native retransmission after ACK loss completes", {"exit": code})
        self.require(any(s.get("kind") == "cleaned" and s.get("id") == snapshot["id"] for s in self.native_store(storage)),
                     "native cleaned tombstone after confirmed upload")
        self.require(len(self.db_events(snapshot["id"])) == 4, "native ACK-loss retransmission stored no duplicates")
        self.artifacts["native_anonymous"] = {"session_id": snapshot["id"], "records": snapshot["records"], "segments": snapshot["segments"]}

        # ID mode: unknown roster ID refused; long permit continues the boundary.
        id_proxy = self.new_proxy()
        id_config = self.proxied_config("id", studies["id"], id_proxy)
        id_storage = self.native_root / "id"
        code, output = self.native_run(id_storage, studies["id"]["config_path"], ["--synthetic-auto", "--participant-code=unknown"])
        self.require(code == 2 and "admission_denied" in output, "native unknown roster ID is refused", {"exit": code})
        self.require(not [s for s in self.native_store(id_storage) if s.get("kind") == "session"], "native refused admission wrote no session")
        partial = self.native_partial(id_storage, id_config, ["--synthetic-auto", "--participant-code=" + ROSTER_ID, "--stop-after-trial"])
        self.require(partial["checkpoint"]["next_trial"] == 1, "native checkpoint bound to the first trial")
        permit = self.issue_ticket(studies["id"]["study_url"], partial["id"], "签发一次性恢复许可")
        snapshot, _output = self.native_await_completion(
            id_storage, id_config, ["--synthetic-auto", "--participant-code=" + ROSTER_ID, "--recover=" + partial["id"], "--permit=" + permit])
        self.require(snapshot["id"] == partial["id"], "native long-permit recovery keeps the original session")
        self.require(len(snapshot["records"]) == 4 and len(snapshot["segments"]) == 2, "native long-permit recovery continues at the trial boundary",
                     {"records": len(snapshot["records"]), "segments": len(snapshot["segments"])})
        # The finished candidate keeps its unreceived completion: a plain named
        # start must not silently continue it or open a second session in id mode.
        code, output = self.native_run(id_storage, id_config, ["--synthetic-auto", "--participant-code=" + ROSTER_ID])
        self.require(code == 2 and "recovery_denied" in output, "native named start never silently continues a finished id-mode candidate", {"exit": code})
        self.require([s["id"] for s in self.native_store(id_storage) if s.get("kind") == "session"] == [partial["id"]],
                     "native refused named start created no new session")
        recovery_code = self.issue_ticket(studies["id"]["study_url"], partial["id"], "签发六位恢复码")
        id_proxy.drop_markers = ()
        code, output = self.native_run(id_storage, id_config, ["--synthetic-auto", "--short-code=" + recovery_code, "--await-upload"])
        self.require(code == 0 and "SYNTHETIC_DATA_ONLY" in output and "SYNTHETIC_DATA_ONLY_DONE" in output,
                     "native six-digit code continues a finished candidate as data only", {"exit": code})
        self.require(any(s.get("kind") == "cleaned" and s.get("id") == partial["id"] for s in self.native_store(id_storage)),
                     "native long-permit session reached the cleaned tombstone")
        self.artifacts["native_id"] = {"session_id": snapshot["id"], "records": snapshot["records"], "segments": snapshot["segments"]}

        # Password mode: named continuation with explicit choice, then a cleaned
        # device starts a fresh session instead of resuming anyone.
        password_proxy = self.new_proxy()
        password_config = self.proxied_config("password", studies["password"], password_proxy)
        password_storage = self.native_root / "password"
        named = self.native_partial(password_storage, password_config, ["--synthetic-auto", *credentials, "--stop-after-trial"])
        self.require(named["checkpoint"]["next_trial"] == 1, "native named candidate starts at the first boundary")
        snapshot, _output = self.native_await_completion(password_storage, password_config, ["--synthetic-auto", *credentials])
        self.require(snapshot["id"] == named["id"], "native named continuation keeps the original session")
        self.require(len(snapshot["records"]) == 4 and len(snapshot["segments"]) == 2, "native named continuation opened a second segment",
                     {"records": len(snapshot["records"]), "segments": len(snapshot["segments"])})
        self.require(len([e for e in snapshot["records"] if e["payload"].get("trial_id") == "t1"]) == 1, "native named continuation never replays trial 1")
        password_proxy.drop_markers = ()
        code, output = self.native_run(password_storage, password_config, ["--synthetic-auto", *credentials, "--await-upload"])
        self.require(code == 0 and "SYNTHETIC_DATA_ONLY" in output and "SYNTHETIC_DATA_ONLY_DONE" in output,
                     "native named recovery of a finished candidate is data only", {"exit": code})
        code, output = self.native_run(password_storage, password_config, ["--synthetic-auto", *credentials])
        self.require(code == 0 and "SYNTHETIC_DONE" in output, "native cleaned device starts a fresh session", {"exit": code})
        self.require("SYNTHETIC_NAMED cleaned" in output, "native same-study cleaned tombstone gives the neutral uploaded hint")
        tombstones = [s for s in self.native_store(password_storage) if s.get("kind") == "cleaned"]
        self.require(len(tombstones) == 2 and any(t["id"] == named["id"] for t in tombstones),
                     "native cleaned tombstone is preserved next to the new session", [t["id"] for t in tombstones])
        self.artifacts["native_password"] = {"session_id": snapshot["id"], "records": snapshot["records"], "segments": snapshot["segments"],
                                             "new_session": [t["id"] for t in tombstones if t["id"] != named["id"]]}

        # Six-digit code recovers a candidate that a newer participation locked.
        code_proxy = self.new_proxy()
        code_config = self.proxied_config("code", studies["password"], code_proxy)
        code_storage = self.native_root / "code"
        locked = self.native_partial(code_storage, code_config, ["--synthetic-auto", *credentials, "--stop-after-trial"])
        code_proxy.drop_markers = ()
        code, output = self.native_run(code_storage, code_config, ["--synthetic-auto", *credentials, "--auto-new-session"])
        self.require(code == 0 and "SYNTHETIC_DONE" in output, "native new participation completes and locks the older session", {"exit": code})
        locked_after = next(s for s in self.native_store(code_storage) if s.get("id") == locked["id"])
        self.require(locked_after.get("front_locked") is True, "native new participation front-locks the older session")
        code_proxy.drop_markers = ("/completion",)
        recovery_code = self.issue_ticket(studies["password"]["study_url"], locked["id"], "签发六位恢复码")
        snapshot, _output = self.native_await_completion(code_storage, code_config, ["--synthetic-auto", "--short-code=" + recovery_code])
        self.require(snapshot["id"] == locked["id"], "native six-digit code recovery keeps the original session")
        self.require(len(snapshot["records"]) == 4 and len(snapshot["segments"]) == 2, "native code recovery continues at the trial boundary",
                     {"records": len(snapshot["records"]), "segments": len(snapshot["segments"])})
        self.artifacts["native_code"] = {"session_id": snapshot["id"], "records": snapshot["records"], "segments": snapshot["segments"]}

        # A declared completion is not a receipt: the real SQLite queue is closed
        # and reopened, the named credentials are verified, and the candidate is
        # continued data only before the retained batch uploads and cleans.
        data_proxy = self.new_proxy(("/event-batches", "/completion"), drop_before_forward=True)
        data_config = self.proxied_config("named_data_only", studies["password"], data_proxy)
        data_storage = self.native_root / "named_data_only"
        snapshot, _output = self.native_await_completion(data_storage, data_config, ["--synthetic-auto", *credentials], require_pending=True)
        self.require(snapshot.get("completion") is not None and len(snapshot["pending"]) == 4,
                     "native reopened store keeps an unreceived completion and four pending records", {"pending": len(snapshot["pending"])})
        self.require(snapshot.get("complete_ack") is None, "native declared completion is not an acknowledgement")
        self.require(len(self.db_events(snapshot["id"])) == 0, "native unreceived batch never reached the server")
        self.require(len(snapshot["segments"]) == 1, "native finished candidate keeps its single segment", snapshot["segments"])
        data_proxy.drop_markers = ()
        code, output = self.native_run(data_storage, data_config, ["--synthetic-auto", *credentials, "--await-upload"])
        self.require(code == 0 and "SYNTHETIC_DATA_ONLY" in output and "SYNTHETIC_DATA_ONLY_DONE" in output,
                     "native named recovery continues a finished candidate as data only", {"exit": code})
        cleaned = next(s for s in self.native_store(data_storage) if s.get("id") == snapshot["id"])
        self.require(cleaned.get("kind") == "cleaned" and "records" not in cleaned,
                     "native data-only recovery ends in a cleaned tombstone", sorted(cleaned))
        self.require(len(self.db_events(snapshot["id"])) == 4, "native data-only recovery uploaded the retained records")
        self.artifacts["native_data_only"] = {"session_id": snapshot["id"], "records": snapshot["records"], "segments": snapshot["segments"]}

        # An expired session credential still allows named data continuation: the
        # server renews it after the same device proof and roster password.
        expired_proxy = self.new_proxy(())
        expired_config = self.proxied_config("named_expired", studies["password"], expired_proxy)
        expired_storage = self.native_root / "named_expired"
        partial = self.native_partial(expired_storage, expired_config, ["--synthetic-auto", *credentials, "--stop-after-trial"])
        self.require(partial["checkpoint"]["next_trial"] == 1, "native expired-token candidate starts at the first boundary")
        self.expire_session(partial["id"])
        self.require(self.session_expired(partial["id"]), "native session expiry persisted in the temporary database")
        self.require(self.token_refused(partial["id"], partial["context"]["token"]),
                     "the stored pre-expiry native token is refused by the API")
        expired_proxy.drop_markers = ("/completion",)
        snapshot, _output = self.native_await_completion(expired_storage, expired_config, ["--synthetic-auto", *credentials])
        self.require(snapshot["id"] == partial["id"] and len(snapshot["records"]) == 4 and len(snapshot["segments"]) == 2,
                     "native expired-token named continuation keeps the original session at the boundary",
                     {"records": len(snapshot["records"]), "segments": len(snapshot["segments"])})
        self.require(len([e for e in snapshot["records"] if e["payload"].get("trial_id") == "t1"]) == 1,
                     "native expired-token continuation never replays trial 1")
        self.require(len(self.db_events(snapshot["id"])) == 4, "native expired-token continuation uploaded both segments")
        self.artifacts["native_expired"] = {"session_id": snapshot["id"], "records": snapshot["records"], "segments": snapshot["segments"]}

        # A cleaned tombstone from another study on the same device is not evidence
        # about a named candidate here: no uploaded hint, and a new session starts.
        foreign_storage = self.native_root / "foreign_tombstone"
        code, output = self.native_run(foreign_storage, studies["anonymous"]["config_path"], ["--synthetic-auto"])
        self.require(code == 0 and "SYNTHETIC_DONE" in output, "native device completed the other study for the tombstone", {"exit": code})
        self.require(any(s.get("kind") == "cleaned" for s in self.native_store(foreign_storage)), "native other-study tombstone persisted")
        before_count = self.db_session_count(studies["password"]["study_id"])
        code, output = self.native_run(foreign_storage, studies["password"]["config_path"], ["--synthetic-auto", *credentials])
        self.require(code == 0 and "SYNTHETIC_DONE" in output, "native unrelated tombstone does not block a new study session", {"exit": code})
        self.require("SYNTHETIC_NAMED cleaned" not in output,
                     "native unrelated tombstone is not reported as this study's uploaded session")
        self.require("SYNTHETIC_NAMED none" in output, "native no-candidate path is the neutral one", output[-300:])
        self.require(self.db_session_count(studies["password"]["study_id"]) == before_count + 1,
                     "native unrelated tombstone never suppressed the new admission")

        # Single writer: a second process is refused while the first holds the store.
        writer_storage = self.native_root / "writer"
        first, reader = self.native_launch(writer_storage, studies["anonymous"]["config_path"], ["--synthetic-auto", "--stop-after-trial"])
        try:
            self.wait_boundary(writer_storage, first, reader, 60)
            self.record(True, "native writer lock holder reached its boundary")
            code, output = self.native_run(writer_storage, studies["anonymous"]["config_path"], ["--synthetic-auto"])
            self.require(code == 2 and "writer_busy" in output, "native second writer is refused", {"exit": code})
        finally:
            first.send_signal(signal.SIGTERM)
            try:
                first.wait(timeout=15)
            except subprocess.TimeoutExpired:
                first.kill()

        # A session without a declared recovery strategy recovers data only.
        strategy_storage = self.native_root / "no_strategy"
        strategy_config = self.proxied_config("strategy", studies["password"], self.new_proxy())
        target = self.native_partial(strategy_storage, strategy_config, ["--synthetic-auto", *credentials, "--stop-after-trial"])
        connection = sqlite3.connect(str(strategy_storage / "queue.sqlite"))
        try:
            value = json.loads(connection.execute("select value from sessions where id=?", [target["id"]]).fetchone()[0])
            value["checkpoint"].pop("strategy", None)
            connection.execute("update sessions set value=? where id=?", [json.dumps(value), target["id"]])
            connection.commit()
        finally:
            connection.close()
        permit = self.issue_ticket(studies["password"]["study_url"], target["id"], "签发一次性恢复许可")
        code, output = self.native_run(strategy_storage, strategy_config, ["--synthetic-auto", "--recover=" + target["id"], "--permit=" + permit])
        self.require(code == 2 and "SYNTHETIC_DATA_ONLY" in output, "no-strategy session recovers data only", {"exit": code})
        unchanged = self.live_session(strategy_storage)
        self.require(unchanged["records"] == target["records"] and len(unchanged["segments"]) == len(target["segments"]),
                     "data-only recovery does not replay or extend the session")

    def issue_ticket(self, study_url, session_id, button):
        result = self.driver("issue-code", {
            "admin_url": f"http://admin.localhost:{self.port}",
            "credentials": {"username": "synthetic_owner", "password": self.owner_password},
            "study_url": study_url, "session_id": session_id, "button": button,
        })
        token = result["evidence"].get("code")
        if not token:
            raise VerificationError(f"issuing {button} failed: {result.get('error') or result}")
        return token

    def compare_phase(self):
        exports = {key: self.export_study(key, study) for key, study in self.studies.items()}
        self.artifacts["exports"] = {key: len(records) for key, records in exports.items()}
        sessions = [
            ("web anonymous", "anonymous", self.artifacts["web"]["anonymous"]["session_id"], self.artifacts["web"]["anonymous"]["records"]),
            ("web id", "id", self.artifacts["web"]["id"]["session_id"], self.artifacts["web"]["id"]["records"]),
            ("web password code recovery", "password", self.artifacts["web"]["password"]["recovered"]["session_id"], self.artifacts["web"]["password"]["recovered"]["records"]),
            ("native anonymous", "anonymous", self.artifacts["native_anonymous"]["session_id"], self.artifacts["native_anonymous"]["records"]),
            ("native id", "id", self.artifacts["native_id"]["session_id"], self.artifacts["native_id"]["records"]),
            ("native password", "password", self.artifacts["native_password"]["session_id"], self.artifacts["native_password"]["records"]),
            ("native code", "password", self.artifacts["native_code"]["session_id"], self.artifacts["native_code"]["records"]),
            ("web password data-only recovery", "password", self.artifacts["web"]["password"]["data_only"]["session_id"],
             self.artifacts["web"]["password"]["data_only"]["records"]),
            ("native data-only recovery", "password", self.artifacts["native_data_only"]["session_id"], self.artifacts["native_data_only"]["records"]),
            ("native expired-token continuation", "password", self.artifacts["native_expired"]["session_id"],
             self.artifacts["native_expired"]["records"]),
        ]
        for label, key, session_id, local in sessions:
            exported = [record for record in exports[key] if record.get("session_id") == session_id]
            remote = self.wait_db_events(session_id, len(exported) or 1)
            self.compare_records(label, local, remote, exported)
        exported_ids = {key: {record["session_id"] for record in records} for key, records in exports.items()}
        for label, key, session_id, _local in sessions:
            self.require(session_id in exported_ids[key], f"{label}: session present in the authorized JSONL export")


class FaultProxy:
    """Forwards native HTTP to the temporary server and can keep dropping the
    response for chosen paths (an ACK loss) or refuse them before they reach the
    server (the data was never received), so the local store can be inspected
    while the server state stays known."""

    def __init__(self, upstream_port, drop_markers, drop_before_forward=False):
        self.upstream_port = upstream_port
        self.drop_markers = (drop_markers,) if isinstance(drop_markers, str) else tuple(drop_markers)
        self.drop_before_forward = drop_before_forward
        self.dropped = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _drop(self):
                outer.dropped += 1
                dropped = b'{"code":"dropped_ack","retryable":true}'
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(dropped)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(dropped)

            def _forward(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                dropping = bool(outer.drop_markers) and any(marker in self.path for marker in outer.drop_markers)
                if dropping and outer.drop_before_forward:
                    self._drop()
                    return
                headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
                if self.headers.get("Authorization"):
                    headers["Authorization"] = self.headers["Authorization"]
                request = urlrequest.Request(f"http://127.0.0.1:{outer.upstream_port}{self.path}", data=body, method=self.command, headers=headers)
                try:
                    with urlrequest.urlopen(request, timeout=30) as response:
                        payload, status = response.read(), response.status
                except urlerror.HTTPError as error:
                    payload, status = error.read(), error.code
                if dropping:
                    self._drop()
                    return
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)

            do_POST = _forward
            do_GET = _forward

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Phase 03 GEC shell verification")
    parser.add_argument("--verify", action="store_true", help="run the full verification and exit non-zero on failure")
    parser.add_argument("--root", default=None, help="evidence root (defaults to a new stamp under local_data/phase03_20260920/shell_verify)")
    args = parser.parse_args()
    if not args.verify:
        parser.error("only --verify is supported")
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    root = Path(args.root) if args.root else PHASE_ROOT / "shell_verify" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    root.mkdir(parents=True, exist_ok=True)
    verify = Verify(root)
    try:
        ok = verify.verify()
    except VerificationError as error:
        verify.log(f"verification aborted: {error}")
        (root / "summary.txt").write_text("\n".join(verify.summary_lines) + "\n")
        (root / "evidence.json").write_text(json.dumps({"checks": verify.checks, "artifacts": verify.artifacts, "error": str(error)}, indent=2, default=str))
        print(f"PHASE03_SHELL_VERIFY_FAILED {root}")
        return 1
    except Exception as error:  # unexpected: keep the evidence and fail loudly
        verify.log(f"unexpected failure: {error!r}")
        (root / "summary.txt").write_text("\n".join(verify.summary_lines) + "\n")
        (root / "evidence.json").write_text(json.dumps({"checks": verify.checks, "artifacts": verify.artifacts, "error": repr(error)}, indent=2, default=str))
        print(f"PHASE03_SHELL_VERIFY_ERROR {root} :: {error!r}")
        return 1
    finally:
        verify.stop_server()
    print(("PHASE03_SHELL_VERIFY_OK " if ok else "PHASE03_SHELL_VERIFY_FAILED ") + str(root))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
