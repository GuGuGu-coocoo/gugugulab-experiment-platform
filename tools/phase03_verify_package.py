#!/usr/bin/env python3
"""Phase 03 complete native package verification (03D).

Owns one temporary synthetic instance (its own data directory, its own database
and a port >= 8040) and drives the real researcher GUI and the real exported
macOS arm64 program through the complete distribution artifact:

* register the native descriptor, upload the actual program archive, approve the
  release and let the platform freeze the public configuration, schema, codebook,
  project license, third-party notices and member manifest into one complete
  package; the frozen engine notices are compared against a fresh export of the
  official local engine's own notice interface;
* download that package through the real authenticated GUI link (twice), plus
  its sidecars, and verify on the local filesystem that the executable,
  resources, modes, configuration, schema, codebook, license, bundled
  third-party notices and member hashes are exactly the uploaded program and the
  frozen metadata;
* publish the study through the GUI and make the complete native release the
  current release, then check the real portal and stable study entry: the native
  release is presented as a controlled independent program with no fabricated
  Web path and no session is created by browsing it;
* grant a second member the build scope, prove the download works, revoke the
  member and prove artifact and sidecars are refused;
* unpack the downloaded package and start the real program with its bundled
  default ``connection.json`` (no ``--config`` replacement, no file editing),
  then compare the local SQLite queue, the server database and the authorized
  JSONL export by original event identity and value;
* tamper with the stored artifact and prove the platform serves neither the
  package nor a sidecar and refuses admission without creating a session, then
  restore the bytes and prove the same download is served again.

Evidence stays under ``local_data/phase03_20260920/package_verify/<stamp>/``.
Missing tooling fails loudly instead of skipping.

A bound invocation (``--binding`` plus the three explicit artifacts) verifies
brand-new bytes exported and packaged by this remediation round: the binding
must come from a unique root under ``build/phase03_remediation_20260923/`` and
carry the current program-source digest, and a missing, stale or mismatched
binding is refused before the instance is created. Without those options the
tool keeps its explicit legacy invocation against ``build/native``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
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
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
PHASE_ROOT = ROOT / "local_data" / "phase03_20260920"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
GUNICORN = ROOT / ".venv" / "bin" / "gunicorn"
NATIVE_ZIP = ROOT / "build" / "native" / "synthetic.zip"
NATIVE_DESCRIPTOR = ROOT / "build" / "native" / "descriptor.json"
NATIVE_BINARY = ROOT / "build" / "native" / "GEP Synthetic Experiment.app" / "Contents" / "MacOS" / "GEP Synthetic Experiment"
DRIVER = ROOT / "tests" / "browser" / "phase03_package_verify.mjs"
LICENSE = ROOT / "LICENSE"
GODOT_SQLITE_LICENSE = ROOT / "third_party" / "godot_sqlite_license.md"
ENGINE_NOTICES = ROOT / "server" / "core" / "data" / "godot_engine_notices.json"
ENGINE_NOTICES_SCRIPT = ROOT / "tests" / "native" / "engine_notices_export.gd"
NOTICES_MEMBER = "THIRD_PARTY_NOTICES.txt"
MEMBER_USERNAME = "synthetic_package_reader"
# Explicit artifact binding for this remediation round: the orchestrator
# exports and packages brand-new macOS bytes under one unique build root and
# records the program, descriptor and current program-source digests in an
# artifact_binding.json. A bound invocation refuses a missing binding, a stale
# source digest or bytes that do not match before it starts anything; the
# historical build/native defaults stay the explicit legacy invocation only.
BUILD_BASE = ROOT / "build" / "phase03_remediation_20260923"
ARTIFACT_BINDING_FORMAT = "gep-remediation-artifact-binding/v1"
BINDING_ARTIFACTS = ("native_zip", "native_descriptor", "native_binary")
BINDING_CHECK = "本轮构建绑定：完整包程序绑定本轮唯一构建与源码摘要"
# Fixed synthetic value for the invitation activation page (U07): it must carry
# one ASCII uppercase letter, lowercase letter, digit and visible symbol each.
# It is not a real credential and never leaves this synthetic run.
MEMBER_PASSWORD = "Synthetic-package-reader-2026!"


class VerificationError(Exception):
    pass


class RefusalError(Exception):
    """A pre-run refusal: no instance, server or program has been started."""


def guard_build_root(raw):
    """This round's brand-new unique build root, never build/ or build/native."""
    if not raw:
        raise RefusalError("the artifact binding declares no build root")
    import remediation_windows
    root = Path(os.path.abspath(Path(str(raw)).expanduser()))
    # Every component from the project root downwards is checked with lstat:
    # a link that redirects a parent directory is refused too, not only a
    # symlinked leaf (resolve() would hide the link and is not used).
    link = remediation_windows.symlinked_component(root, ROOT)
    if link is not None:
        raise RefusalError(f"the artifact binding build root path contains a symbolic link: {link}")
    if root.is_symlink():
        raise RefusalError(f"the artifact binding build root is a symbolic link: {root}")
    try:
        relative = root.relative_to(Path(os.path.abspath(BUILD_BASE)))
    except ValueError:
        raise RefusalError(f"the artifact binding build root is outside the remediation build base: {root}") from None
    if not relative.parts:
        raise RefusalError(f"the artifact binding build root must be a unique subdirectory: {root}")
    if not root.is_dir():
        raise RefusalError(f"the artifact binding build root is missing: {root}")
    return root


def bound_artifact(root, entry, key):
    import remediation_windows
    if not isinstance(entry, dict) or not entry.get("path"):
        raise RefusalError(f"the artifact binding has no {key} entry")
    path = Path(str(entry["path"])).expanduser()
    if not path.is_absolute():
        raise RefusalError(f"the bound {key} path is not absolute: {path}")
    path = Path(os.path.abspath(path))
    try:
        path.relative_to(root)
    except ValueError:
        raise RefusalError(f"the bound {key} is outside this round's build root: {path}") from None
    link = remediation_windows.symlinked_component(path, ROOT)
    if link is not None:
        raise RefusalError(f"the bound {key} path contains a symbolic link: {link}")
    if path.is_symlink():
        raise RefusalError(f"the bound {key} is a symbolic link: {path}")
    if not path.is_file():
        raise RefusalError(f"the bound {key} is missing: {path}")
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != entry.get("sha256") or path.stat().st_size != entry.get("size"):
        raise RefusalError(f"the bound {key} bytes do not match the binding: {path}")
    return path


def resolve_artifacts(args):
    """Explicit this-round artifacts, or the legacy unbound defaults.

    A bound invocation must name --binding, --native-zip, --native-descriptor
    and --native-binary together; the binding must be ok, come from this
    round's unique build root, carry the current program-source digest and
    match the actual bytes and sizes. Every refusal happens before anything is
    started.
    """
    explicit = (args.binding, args.native_zip, args.native_descriptor, args.native_binary)
    if all(value is None for value in explicit):
        return {"mode": "legacy-defaults", "binding": None, "native_zip": NATIVE_ZIP,
                "native_descriptor": NATIVE_DESCRIPTOR, "native_binary": NATIVE_BINARY}
    if not all(value is not None for value in explicit):
        raise RefusalError("explicit artifacts require --binding, --native-zip, --native-descriptor and "
                           "--native-binary together")
    try:
        document = json.loads(Path(args.binding).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RefusalError(f"the artifact binding is unreadable: {type(error).__name__}") from None
    if not isinstance(document, dict) or document.get("format") != ARTIFACT_BINDING_FORMAT \
            or document.get("verdict") != "ok":
        raise RefusalError("the artifact binding is missing or not ok")
    root = guard_build_root(document.get("build_root"))
    import remediation_windows
    if document.get("program_source_digest") != remediation_windows.program_source_digest():
        raise RefusalError("the artifact binding was produced from different program sources")
    entries = document.get("artifacts") or {}
    resolved = {}
    for key, value in zip(BINDING_ARTIFACTS, explicit[1:]):
        bound = bound_artifact(root, entries.get(key), key)
        requested = Path(os.path.abspath(Path(str(value)).expanduser()))
        if requested != bound:
            raise RefusalError(f"the requested {key} is not the artifact recorded in the binding: {requested}")
        resolved[key] = bound
    resolved.update({"mode": "bound", "binding": str(Path(args.binding).expanduser()),
                     "binding_document": document, "build_root": str(root)})
    return resolved


class FaultProxy:
    """Forwards participant traffic to the isolated server and can drop the
    response for chosen paths. Used to hold the downloaded program at the
    completion boundary (the server still receives the declaration) so the real
    local store can be compared while it still holds its records."""

    def __init__(self, upstream_port, drop_markers=("/completion",)):
        self.upstream_port = upstream_port
        self.drop_markers = tuple(drop_markers)
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
                headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
                if self.headers.get("Authorization"):
                    headers["Authorization"] = self.headers["Authorization"]
                request = urlrequest.Request(f"http://127.0.0.1:{outer.upstream_port}{self.path}", data=body,
                                             method=self.command, headers=headers)
                try:
                    with urlrequest.urlopen(request, timeout=30) as response:
                        payload, status = response.read(), response.status
                except urlerror.HTTPError as error:
                    payload, status = error.read(), error.code
                if any(marker in self.path for marker in outer.drop_markers):
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


class Verify:
    def __init__(self, root: Path, artifacts=None):
        # Absolute paths only: the downloaded program is launched with a cwd of
        # the unpack directory, so a relative evidence root would be resolved
        # again against that cwd and the executable would not be found.
        self.root = Path(root).resolve()
        self.data = self.root / "data"
        self.evidence = self.root / "evidence"
        self.db_path = self.data / "gep.sqlite3"
        # The artifacts this run verifies: the explicit this-round binding when
        # the orchestrator passes one, otherwise the legacy build/native
        # defaults for a deliberate standalone invocation.
        artifacts = artifacts or {}
        self.binding = artifacts.get("binding_document")
        self.binding_mode = artifacts.get("mode", "legacy-defaults")
        self.native_zip = Path(artifacts.get("native_zip", NATIVE_ZIP))
        self.native_descriptor = Path(artifacts.get("native_descriptor", NATIVE_DESCRIPTOR))
        self.native_binary = Path(artifacts.get("native_binary", NATIVE_BINARY))
        self.owner_password = secrets.token_urlsafe(24)
        self.instance_id = str(uuid.uuid4())
        self.secret_key = secrets.token_urlsafe(48)
        self.port = None
        self.server = None
        self.proxy = None
        self.godot = None
        self.checks = []
        self.artifacts = {}
        self.summary_lines = []

    # ------------------------------------------------------------------ helpers
    def log(self, message):
        line = f"[phase03-package] {message}"
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

    @staticmethod
    def sha256_file(path):
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def member_hash(archive, name):
        digest = hashlib.sha256()
        size = 0
        with archive.open(name) as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def canonical(records):
        keys = ("event_id", "sequence", "segment_id", "event_type", "payload", "observed_time")
        return sorted(json.dumps({key: record.get(key) for key in keys}, sort_keys=True) for record in records)

    def preconditions(self):
        missing = []
        for label, path in (("project virtualenv", VENV_PYTHON), ("gunicorn", GUNICORN),
                            ("exported macOS program archive", self.native_zip),
                            ("native descriptor", self.native_descriptor),
                            ("extracted macOS program", self.native_binary),
                            ("license", LICENSE), ("godot-sqlite license", GODOT_SQLITE_LICENSE),
                            ("frozen engine notices", ENGINE_NOTICES),
                            ("engine notices export script", ENGINE_NOTICES_SCRIPT),
                            ("browser driver", DRIVER)):
            if not path.exists():
                missing.append(f"{label} missing: {path}")
        if shutil.which("node") is None:
            missing.append("node executable not on PATH (Playwright driver required)")
        candidate = os.environ.get("GEP_GODOT_BIN") or shutil.which("godot")
        if candidate is None and Path("/Applications/Godot.app/Contents/MacOS/Godot").is_file():
            candidate = "/Applications/Godot.app/Contents/MacOS/Godot"
        if candidate is None or not Path(candidate).exists():
            missing.append("official local Godot engine not found (GEP_GODOT_BIN, PATH or /Applications/Godot.app)")
        else:
            self.godot = candidate
        if missing:
            raise VerificationError("missing tool environment: " + "; ".join(missing))

    def verify_notice_provenance(self):
        """Re-export the official local engine's own notices and compare.

        The frozen source the platform ships must be the byte-for-byte output of
        the engine that produced the program under test, and the vendored
        godot-sqlite notice must still be the upstream license file; nothing in
        the package is a hand-written license claim.
        """
        exported = self.root / "engine_notices_export.json"
        result = subprocess.run([self.godot, "--headless", "--script", str(ENGINE_NOTICES_SCRIPT)],
                                cwd=ROOT, env={**os.environ, "GEP_NOTICE_OUTPUT": str(exported)},
                                capture_output=True, text=True, timeout=180)
        self.require(exported.is_file(), "官方本地引擎可导出自有许可/版权声明",
                     {"tail": (result.stdout + result.stderr)[-200:]})
        frozen = json.loads(ENGINE_NOTICES.read_text())
        self.require(json.loads(exported.read_text()) == frozen, "冻结的引擎声明等于官方本地引擎导出的声明")
        version = frozen["engine_version"]["string"]
        self.require(version == "4.7.2-stable (official)", "冻结声明对应描述契约固定的引擎版本", version)
        self.require(GODOT_SQLITE_LICENSE.read_text().strip(), "godot-sqlite 上游许可文本存在",
                     GODOT_SQLITE_LICENSE.name)

    def free_port(self):
        for candidate in range(8040, 8100):
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", candidate))
                except OSError:
                    continue
            return candidate
        raise VerificationError("no free port >= 8040")

    def public_api(self):
        if self.proxy is not None:
            return f"http://127.0.0.1:{self.proxy.port}"
        return f"http://experiment.localhost:{self.port}"

    def server_env(self):
        return {
            **os.environ,
            "GEP_DATA_DIR": str(self.data),
            "GEP_SECRET_KEY": self.secret_key,
            "GEP_EXPECTED_INSTANCE": self.instance_id,
            "GEP_PUBLIC_API": self.public_api(),
            "PYTHONPATH": str(ROOT / "server"),
            "DJANGO_SETTINGS_MODULE": "gep.settings",
        }

    def run_python(self, code, extra_env=None, timeout=120):
        env = self.server_env()
        if extra_env:
            env.update(extra_env)
        return subprocess.run([str(VENV_PYTHON), "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)

    # ------------------------------------------------------------------ instance
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

    def start_server(self):
        if self.port is None:
            self.port = self.free_port()
        log = open(self.root / "gunicorn.log", "w")
        self.server = subprocess.Popen(
            [str(GUNICORN), "gep.wsgi:application", "--bind", f"127.0.0.1:{self.port}",
             "--workers", "1", "--threads", "4", "--timeout", "900", "--access-logfile", "-"],
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

    # ------------------------------------------------------------------ database
    def db_rows(self, sql, params=()):
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=20)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def release_record(self, release_id):
        rows = self.db_rows("select artifact_digest, artifact_path, artifact_size, config from core_release where id=?",
                            [release_id.replace("-", "")])
        if not rows:
            raise VerificationError(f"release {release_id} missing in the temporary database")
        return {"digest": rows[0][0], "path": rows[0][1], "size": rows[0][2], "config": json.loads(rows[0][3])}

    def _instance_uuid(self):
        return self._uuid_text(self.db_rows("select instance_id from core_instance")[0][0])

    @staticmethod
    def _uuid_text(raw):
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"

    def release_ids(self, study_id):
        rows = self.db_rows("select release.id, release.build_id from core_release release where release.study_id=?",
                            [study_id.replace("-", "")])
        if not rows:
            raise VerificationError(f"no release for study {study_id}")
        return self._uuid_text(rows[0][0]), self._uuid_text(rows[0][1])

    def session_count(self, study_id):
        return self.db_rows("select count(*) from core_session join core_release on core_session.release_id=core_release.id "
                            "where core_release.study_id=?", [study_id.replace("-", "")])[0][0]

    def db_events(self, session_id):
        rows = self.db_rows("select envelope from core_event where session_id=?", [session_id.replace("-", "")])
        return [json.loads(row[0]) for row in rows]

    # ------------------------------------------------------------------- driver
    def driver(self, command, job):
        job_path = self.root / f"job_{command}.json"
        job_path.write_text(json.dumps(job))
        result = subprocess.run(["node", str(DRIVER), command, str(job_path)], cwd=ROOT, capture_output=True, text=True, timeout=1800)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise VerificationError(f"browser driver {command} produced no JSON (exit {result.returncode})\n"
                                    f"stdout:{result.stdout[-2000:]}\nstderr:{result.stderr[-2000:]}")
        payload["_exit"] = result.returncode
        payload["_stderr"] = result.stderr[-4000:]
        (self.root / f"driver_{command}.json").write_text(json.dumps(payload, indent=2))
        for entry in payload.get("checks", []):
            self.record(entry["ok"], "gui: " + entry["label"], entry.get("detail"))
        if payload.get("error"):
            raise VerificationError(f"browser driver {command} failed: {payload['error'][:2000]}")
        return payload

    def job(self):
        return {"admin_url": f"http://admin.localhost:{self.port}",
                "experiment_url": f"http://experiment.localhost:{self.port}",
                "credentials": {"username": "synthetic_owner", "password": self.owner_password},
                "member": {"username": MEMBER_USERNAME, "password": MEMBER_PASSWORD},
                "run_dir": str(self.evidence), "title": "Complete package verification", "max_sessions": 2,
                "descriptor": str(self.native_descriptor), "program": str(self.native_zip)}

    # --------------------------------------------------------- artifact checks
    def verify_downloaded_package(self, package: Path, release_id, sidecars):
        record = self.release_record(release_id)
        digest = self.sha256_file(package)
        self.require(digest == record["digest"], "下载的完整包摘要等于数据库外层摘要", digest)
        self.require(package.stat().st_size == record["size"], "完整包大小与数据库记录一致", record["size"])
        second = package.parent / "complete_package_second.zip"
        self.require(self.sha256_file(second) == digest, "重复授权下载逐字节一致")

        descriptor = json.loads(self.native_descriptor.read_text())
        expected_schemas = descriptor["schemas"]
        with zipfile.ZipFile(package) as archive, zipfile.ZipFile(self.native_zip) as program:
            names = {item.filename for item in archive.infolist() if not item.filename.endswith("/")}
            manifest = json.loads(archive.read("artifact_manifest.json"))
            self.require(manifest["artifact_format_version"] == "gep-artifact/v1", "完整包清单格式版本固定", manifest["artifact_format_version"])
            self.require(manifest["platform"] == "macos_arm64" and manifest["app"].endswith(".app"), "清单声明 macOS 应用根",
                         {"platform": manifest["platform"], "app": manifest["app"]})
            self.require(manifest["program_sha256"] == descriptor["program_sha256"], "清单程序摘要等于上传的程序归档摘要")
            self.require(not ({"artifact_digest", "artifact_sha256", "self_sha256"} & set(manifest)), "清单不含自身外层摘要（非循环）")
            members = {entry["path"]: entry for entry in manifest["members"]}
            self.require(len(members) == len(manifest["members"]) and set(members) | {"artifact_manifest.json"} == names,
                         "清单成员集合与实际成员完全一致", len(members))
            for name, entry in members.items():
                digest, size = self.member_hash(archive, name)
                info = archive.getinfo(name)
                self.require(digest == entry["sha256"] and size == entry["size"], f"成员内容与清单一致：{name}")
                self.require(f"{((info.external_attr >> 16) & 0o7777):04o}" == entry["mode"], f"成员模式保持：{name}", entry["mode"])
            program_names = {item.filename for item in program.infolist() if not item.filename.endswith("/")}
            self.require(program_names <= set(names), "完整包含全部程序成员", len(program_names))
            unchanged = 0
            for name in program_names:
                source, copied = program.getinfo(name), archive.getinfo(name)
                source_hash, source_size = self.member_hash(program, name)
                copied_hash, copied_size = self.member_hash(archive, name)
                if source_hash == copied_hash and source_size == copied_size and (source.external_attr >> 16) & 0o7777 == (copied.external_attr >> 16) & 0o7777:
                    unchanged += 1
            self.require(unchanged == len(program_names), "可执行文件、资源与签名逐字节未改且模式保持", f"{unchanged}/{len(program_names)}")
            self.require(((archive.getinfo(manifest["entry"]).external_attr >> 16) & 0o111) != 0, "清单入口保持可执行位", manifest["entry"])
            bundled_config = archive.read("connection.json")
            self.require(bundled_config == Path(sidecars["connection.json"]).read_bytes(), "GUI 导出的 connection.json 与包内一致")
            config = json.loads(bundled_config)
            self.require(config["mode"] == "anonymous" and config["purpose"] == "synthetic", "冻结公开配置含冻结模式与用途", config["mode"])
            self.require(config["api_url"] == self.public_api(), "冻结公开配置指向本次隔离实例的参与链路", config["api_url"])
            self.require(not ({"password", "participants", "roster", "token", "secret"} & set(config)), "公开配置不含密码、名单或凭据")
            self.require(manifest["config_sha256"] == hashlib.sha256(bundled_config).hexdigest(), "清单记录冻结配置摘要")
            for key, definition in expected_schemas.items():
                self.require(json.loads(archive.read(f"schemas/{key}.json")) == definition, f"schema 冻结一致：{key}")
            codebook = json.loads(archive.read("metadata/codebook.json"))
            self.require(codebook == descriptor["codebook"], "codebook 冻结一致")
            self.require(archive.read("LICENSE") == LICENSE.read_bytes(), "随包许可证等于仓库许可证")
            notices_bytes = archive.read(NOTICES_MEMBER)
            notices_text = notices_bytes.decode("utf-8")
            engine = json.loads(ENGINE_NOTICES.read_text())
            self.require(notices_bytes == Path(sidecars[NOTICES_MEMBER]).read_bytes(),
                         "GUI 导出的第三方许可/版权声明与包内一致")
            self.require(engine["engine_license"].rstrip() in notices_text, "声明包含 Godot 引擎 MIT 许可全文")
            self.require(engine["engine_version"]["string"] in notices_text, "声明标明随包引擎版本",
                         engine["engine_version"]["string"])
            self.require(GODOT_SQLITE_LICENSE.read_text().rstrip() in notices_text, "声明包含 godot-sqlite 上游许可全文")
            self.require(archive.read("LICENSE") != notices_bytes and "Godot" in notices_text and "godot-sqlite" in notices_text,
                         "GEP 许可证与依赖声明分开冻结，不以项目许可证冒充依赖许可")
            self.artifacts["package"] = {"path": str(package), "digest": digest, "size": record["size"],
                                         "members": len(members), "program_members": len(program_names),
                                         "notices_sha256": hashlib.sha256(notices_bytes).hexdigest(),
                                         "manifest": manifest}
        # The advertised program digest still describes the extracted local program.
        for name in (self.native_binary,):
            self.require(name.exists(), "本机构建仍可用于对照", str(name))

    def unpack_and_run(self, package: Path):
        target = self.root / "unpacked"
        with zipfile.ZipFile(package) as archive:
            for item in archive.infolist():
                if item.filename.endswith("/"):
                    continue
                destination = target / item.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(item))
                destination.chmod((item.external_attr >> 16) & 0o7777 or 0o644)
        binary = target / self.native_binary.relative_to(self.native_zip.parent)
        # The whole unpacked runtime tree, not only the executable, must still
        # match the bound program archive: the .pck, dynamic libraries and
        # helper files are what the real program loads.
        import remediation_windows
        problems = remediation_windows.verify_extracted_archive(
            self.native_zip, target, label="unpacked downloaded package")
        self.require(not problems, "解包下载包的程序成员与绑定归档逐成员一致（含 .pck/动态库）",
                     {"problems": problems[:3]} if problems else {"archive": self.native_zip.name})
        self.require(binary.is_file() and os.access(binary, os.X_OK), "解包后程序存在且可执行", str(binary.relative_to(target)))
        self.require((target / "connection.json").is_file(), "外置冻结配置与 .app 同级（无需研究者替换）")
        storage = self.root / "native_store"
        storage.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GEP_SYNTHETIC_STORAGE": str(storage)}
        started = time.time()
        process = subprocess.Popen([str(binary), "--headless", "--", "--synthetic-auto"], cwd=target, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        snapshot = None
        deadline = time.time() + 120
        while time.time() < deadline:
            if process.poll() is not None:
                break
            candidate = self.try_session(storage)
            if candidate and candidate.get("records") and candidate.get("completion") and not candidate.get("pending"):
                snapshot = candidate
                break
            time.sleep(0.2)
        self.require(snapshot is not None, "下载包在完成边界保留可核对的本地记录",
                     {"records": len(snapshot.get("records", [])) if snapshot else 0})
        # The retained local records were acknowledged by the server; release the
        # held completion so the same process finishes and cleans up naturally.
        self.proxy.drop_markers = ()
        try:
            output, _ = process.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            raise VerificationError("downloaded program did not finish within 180s after the completion was acknowledged")
        self.require(process.returncode == 0 and "SYNTHETIC_DONE" in output,
                     "下载的 macOS 程序用自带配置完成同一合成任务",
                     {"exit": process.returncode, "seconds": round(time.time() - started, 1), "tail": output[-200:]})
        self.require("--config" not in output, "未通过命令行替换连接配置")
        (self.evidence / "native_output.txt").write_text(output)
        tombstone = self.try_session(storage, kinds=("cleaned",))
        self.require(bool(tombstone) and tombstone["id"] == snapshot["id"], "确认上传后本地留下同一会话的清理墓碑",
                     tombstone.get("id") if tombstone else None)
        self.artifacts["run"] = {"exit": process.returncode, "seconds": round(time.time() - started, 1), "session_id": snapshot["id"]}
        return storage, snapshot

    def try_session(self, storage: Path, kinds=("session",)):
        path = storage / "queue.sqlite"
        if not path.exists():
            return None
        rows = None
        for opener in (lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20),
                       lambda: sqlite3.connect(str(path), timeout=20)):
            try:
                connection = opener()
            except sqlite3.Error:
                continue
            try:
                rows = connection.execute("select value from sessions").fetchall()
            except sqlite3.Error:
                rows = None
            finally:
                connection.close()
            if rows is not None:
                break
        for row in rows or []:
            record = json.loads(row[0])
            if record.get("kind") in kinds:
                return record
        return None

    def local_session(self, storage: Path, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            record = self.try_session(storage)
            if record:
                return record
            time.sleep(0.4)
        raise VerificationError(f"local native queue has no session record: {storage / 'queue.sqlite'}")

    def compare_records(self, snapshot, export_path: Path):
        session_id = snapshot["id"]
        local = snapshot.get("records", [])
        remote = self.db_events(session_id)
        exported = [json.loads(line)["record"] for line in Path(export_path).read_text().strip().splitlines()]
        exported = [record for record in exported if record.get("session_id") == session_id]
        self.require(len(local) > 0, "下载包本地 SQLite 有真实事件记录", len(local))
        self.require(self.canonical(local) == self.canonical(remote), "本地记录与服务器数据库按事件 ID/值一致",
                     {"local": len(local), "remote": len(remote)})
        self.require(self.canonical(local) == self.canonical(exported), "本地记录与授权 JSONL 导出按事件 ID/值一致",
                     {"local": len(local), "exported": len(exported)})
        self.require(snapshot.get("completion") is not None, "下载包声明了任务完成")
        self.artifacts["comparison"] = {"session_id": session_id, "local": len(local), "remote": len(remote),
                                        "exported": len(exported), "segments": len(snapshot.get("segments", []))}

    # ------------------------------------------------------------------ tamper
    def tamper_phase(self, release_id, release_url, study_id, digest):
        stored = self.data / "artifacts" / self.release_record(release_id)["path"]
        original = stored.read_bytes()
        backup = self.evidence / "package_before_tamper.zip"
        backup.write_bytes(original)
        tampered = bytearray(original)
        tampered[len(tampered) // 2] = (tampered[len(tampered) // 2] + 1) % 256
        stored.write_bytes(bytes(tampered))
        self.require(self.sha256_file(stored) != digest, "已篡改存储中的完整包字节")
        job = {"admin_url": f"http://admin.localhost:{self.port}",
               "credentials": {"username": "synthetic_owner", "password": self.owner_password},
               "run_dir": str(self.evidence),
               "items": [{"url": f"http://admin.localhost:{self.port}{release_url}"},
                         {"url": f"http://admin.localhost:{self.port}{release_url}/connection.json"},
                         {"url": f"http://admin.localhost:{self.port}{release_url}/artifact_manifest.json"},
                         {"url": f"http://admin.localhost:{self.port}{release_url}/{NOTICES_MEMBER}"}]}
        payload = self.driver("downloads", job)
        downloads = payload["evidence"]["downloads"]
        self.require(all(entry["status"] == 409 for entry in downloads), "篡改后完整包与全部 sidecar 均拒绝下载（409）",
                     [entry["status"] for entry in downloads])

        before = self.session_count(study_id)
        release_row, build_row = self.release_ids(study_id)
        body = json.dumps({"operation_id": str(uuid.uuid4()), "proof": "z" * 48, "instance_id": self._instance_uuid(),
                           "study_id": study_id, "release_id": release_row, "build_id": build_row}).encode()
        request = urlrequest.Request(f"http://127.0.0.1:{self.port}/v1/participant/sessions", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urlrequest.urlopen(request, timeout=30) as response:
                status, payload_body = response.status, response.read()
        except urlerror.HTTPError as error:
            status, payload_body = error.code, error.read()
        self.require(status == 409 and json.loads(payload_body).get("code") == "release_unavailable",
                     "篡改后准入在创建会话前失败关闭", {"status": status, "body": payload_body[:120].decode(errors="replace")})
        self.require(self.session_count(study_id) == before, "篡改后没有创建新会话", {"before": before, "after": self.session_count(study_id)})

        stored.write_bytes(original)
        self.require(self.sha256_file(stored) == digest, "恢复原始字节后本地摘要一致")
        payload = self.driver("downloads", {"admin_url": f"http://admin.localhost:{self.port}",
                                            "credentials": {"username": "synthetic_owner", "password": self.owner_password},
                                            "run_dir": str(self.evidence),
                                            "items": [{"url": f"http://admin.localhost:{self.port}{release_url}",
                                                       "out": str(self.evidence / "complete_package_restored.zip")}]})
        entry = payload["evidence"]["downloads"][0]
        self.require(entry["status"] == 200 and entry["sha256"] == digest, "恢复后同一发行可再次逐字节下载",
                     {"status": entry["status"], "sha256": entry["sha256"]})

    # -------------------------------------------------------------------- main
    def binding_summary(self):
        if self.binding is None:
            return {"mode": "legacy-defaults", "source_digest": None}
        return {"mode": "bound", "build_root": self.binding.get("build_root"),
                "program_source_digest": self.binding.get("program_source_digest"),
                "artifacts": {key: (self.binding.get("artifacts") or {}).get(key, {}).get("sha256")
                              for key in BINDING_ARTIFACTS}}

    def verify(self):
        self.preconditions()
        self.record(True, "固定工具链存在", {"program": str(self.native_zip), "descriptor": str(self.native_descriptor)})
        self.record(True, BINDING_CHECK, self.binding_summary())
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.verify_notice_provenance()
        self.init_instance()
        self.port = self.free_port()
        self.proxy = FaultProxy(self.port, drop_markers=("/completion",))
        self.start_server()
        self.record(True, "隔离实例、服务器与完成边界故障代理就绪",
                    {"port": self.port, "proxy": self.proxy.port, "data": str(self.data)})
        try:
            payload = self.driver("package", self.job())
            self.require(not payload.get("failures"), "真实浏览器 GUI 流程全部通过", len(payload.get("failures", [])))
            flow = payload["evidence"]["package"]
            self.require(self.session_count(flow["study_id"]) == 0,
                         "门户与稳定入口浏览原生当前发行时未创建任何会话")
            release_id = flow["release_id"]
            package = Path(flow["artifact_path"])
            self.verify_downloaded_package(package, release_id, flow["sidecars"])
            storage, snapshot = self.unpack_and_run(package)
            export_path = Path(self.evidence / "authorized_export.jsonl")
            payload = self.driver("export", {"admin_url": f"http://admin.localhost:{self.port}",
                                             "credentials": {"username": "synthetic_owner", "password": self.owner_password},
                                             "run_dir": str(self.evidence), "study_url": flow["study_url"],
                                             "out": str(export_path)})
            self.require(not payload.get("failures"), "授权 JSONL 导出通过 GUI 完成", len(payload.get("failures", [])))
            self.compare_records(snapshot, export_path)
            self.tamper_phase(release_id, flow["release_url"], flow["study_id"], self.release_record(release_id)["digest"])
        finally:
            if self.proxy is not None:
                self.proxy.stop()
            self.stop_server()
        failures = [check for check in self.checks if not check["ok"]]
        (self.root / "evidence.json").write_text(json.dumps(
            {"checks": self.checks, "artifacts": self.artifacts, "binding": self.binding_summary()},
            indent=2, default=str))
        (self.root / "summary.txt").write_text("\n".join(self.summary_lines) + "\n")
        return not failures


def main():
    parser = argparse.ArgumentParser(description="Phase 03 complete native package verification")
    parser.add_argument("--verify", action="store_true", help="run the full verification and exit non-zero on failure")
    parser.add_argument("--root", default=None, help="evidence root (defaults to a new stamp under local_data/phase03_20260920/package_verify)")
    parser.add_argument("--binding", default=None,
                        help="this round's artifact_binding.json; requires the explicit artifacts below")
    parser.add_argument("--native-zip", default=None, help="macOS program archive recorded in the binding")
    parser.add_argument("--native-descriptor", default=None, help="native descriptor recorded in the binding")
    parser.add_argument("--native-binary", default=None, help="exported macOS program recorded in the binding")
    args = parser.parse_args()
    if not args.verify:
        parser.error("only --verify is supported")
    try:
        artifacts = resolve_artifacts(args)
    except RefusalError as error:
        print(f"PHASE03_PACKAGE_VERIFY_REFUSED {error}", flush=True)
        print(json.dumps({"verdict": "refused", "error": str(error)}, ensure_ascii=False))
        return 2
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    root = Path(args.root) if args.root else PHASE_ROOT / "package_verify" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    root.mkdir(parents=True, exist_ok=True)
    verify = Verify(root, artifacts)
    try:
        ok = verify.verify()
    except VerificationError as error:
        verify.log(f"verification aborted: {error}")
        (root / "summary.txt").write_text("\n".join(verify.summary_lines) + "\n")
        (root / "evidence.json").write_text(json.dumps(
            {"checks": verify.checks, "artifacts": verify.artifacts, "binding": verify.binding_summary(),
             "error": str(error)}, indent=2, default=str))
        print(f"PHASE03_PACKAGE_VERIFY_FAILED {root}")
        return 1
    except Exception as error:  # unexpected: keep the evidence and fail loudly
        verify.log(f"unexpected failure: {error!r}")
        (root / "summary.txt").write_text("\n".join(verify.summary_lines) + "\n")
        (root / "evidence.json").write_text(json.dumps(
            {"checks": verify.checks, "artifacts": verify.artifacts, "binding": verify.binding_summary(),
             "error": repr(error)}, indent=2, default=str))
        print(f"PHASE03_PACKAGE_VERIFY_ERROR {root} :: {error!r}")
        return 1
    finally:
        verify.stop_server()
    print(("PHASE03_PACKAGE_VERIFY_OK " if ok else "PHASE03_PACKAGE_VERIFY_FAILED ") + str(root))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
