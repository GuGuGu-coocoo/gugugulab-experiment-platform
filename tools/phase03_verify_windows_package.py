#!/usr/bin/env python3
"""Phase 03 Windows x64 complete native package verification (03D).

Owns one temporary synthetic instance (its own data directory, its own database
and a port >= 8060), cross-builds the real Windows program with the pinned
installed Godot and the official 4.7.2 Windows x86_64 export templates, then
drives the real researcher GUI and API through the complete distribution
artifact:

* export the actual Windows x64 program from ``examples/synthetic_experiment``
  (the scientific ``task.gd`` is never changed), package it with
  ``tools/package_build.py windows`` and inspect the produced bytes
  independently: PE32+ x86-64 machine for the entry and for the bundled
  godot-sqlite dependency, a standalone PCK whose header records the pinned
  engine version 4.7.2, and the packaged GDExtension manifest's effective
  ``windows.release.x86_64`` entry resolving to that declared DLL (its own
  declaration only: the PCK interior is not inspected);
* register the descriptor, upload the real program archive, approve the release
  through the real GUI and let the platform freeze the public configuration,
  schema, codebook, project license, third-party notices and member manifest;
  the frozen engine notices are compared against a fresh export of the official
  local engine's own notice interface;
* download that package through the real authenticated GUI link (twice) plus its
  sidecars, verify every member hash/mode against the manifest, every program
  byte against the uploaded archive, and the extraction layout the program reads
  (executable, PCK, dependency and ``connection.json`` inside the program root);
* publish the study and make the Windows release current, then check the real
  portal and stable entry: the release is a controlled independent program with
  no fabricated Web path and no session is created by browsing it;
* tamper with the stored artifact and prove the platform serves neither the
  package nor a sidecar and refuses admission without creating a session, then
  restore the bytes and prove the same download is served again.

Windows execution is **not** run here and is reported as ``NOT_RUN``: only a real
Windows host can execute the frozen program (the separate real-device task).
This tool never substitutes a mock binary for the real cross-built program, so a
green run proves cross-build and frozen-package engineering only.

Every Godot subprocess is gated on its real exit code: an export or engine-notice
run that writes plausible files and then exits non-zero is rejected, never
accepted because its outputs exist. On macOS the first editor process over a
fresh import cache can abort during its own shutdown *after* finishing its work
(Godot 4.7.2); the tool keeps that crash evidence, warms the cache with a
separate supported ``--import`` step that must exit 0, and only accepts a final
export that exits 0. The Windows template itself is resolved and verified
against the pinned official digest (``tools/windows_template.py``) before it is
copied, so a mismatched ``GEP_GODOT_TEMPLATES`` override fails instead of being
exported.

Evidence stays under ``local_data/phase03_20260920/windows_package_verify/<stamp>/``.
Missing tooling fails loudly instead of skipping.
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
import struct
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

from windows_template import TemplateError, find_windows_template, verify_windows_template

ROOT = Path(__file__).resolve().parents[1]
PHASE_ROOT = ROOT / "local_data" / "phase03_20260920"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
GUNICORN = ROOT / ".venv" / "bin" / "gunicorn"
PROJECT = ROOT / "examples" / "synthetic_experiment"
TEMPLATES = PROJECT / ".godot" / "templates"
WINDOWS_TEMPLATE = "windows_release_x86_64.exe"
PROGRAM_ROOT = ROOT / "build" / "windows" / "GEP Synthetic Experiment"
PROGRAM_ARCHIVE = ROOT / "build" / "windows" / "synthetic_windows.zip"
DESCRIPTOR = ROOT / "build" / "windows" / "descriptor.json"
ENTRY = "GEP Synthetic Experiment.exe"
PCK = "GEP Synthetic Experiment.pck"
DEPENDENCY = "libgdsqlite.windows.template_release.x86_64.dll"
EXTENSION = "addons/godot-sqlite/gdsqlite.gdextension"
DRIVER = ROOT / "tests" / "browser" / "phase03_package_verify.mjs"
LICENSE = ROOT / "LICENSE"
GODOT_SQLITE_LICENSE = ROOT / "third_party" / "godot_sqlite_license.md"
ENGINE_NOTICES = ROOT / "server" / "core" / "data" / "godot_engine_notices.json"
ENGINE_NOTICES_SCRIPT = ROOT / "tests" / "native" / "engine_notices_export.gd"
NOTICES_MEMBER = "THIRD_PARTY_NOTICES.txt"
HOST_VERSION = (4, 7, 2)
WINDOWS_MACHINE = 0x8664
WINDOWS_MAGIC = 0x20b
NOT_RUN = "NOT_RUN"
MEMBER_USERNAME = "synthetic_windows_reader"
# Fixed synthetic value for the invitation activation page (U07): one ASCII
# uppercase letter, lowercase letter, digit and visible symbol each; it is not a
# real credential and never leaves this synthetic run.
MEMBER_PASSWORD = "Synthetic-windows-reader-2026!"


class VerificationError(Exception):
    pass


def pe_headers(raw):
    """Independent PE header parse of one executable image (never executes it)."""
    if len(raw) < 0x40 or raw[:2] != b"MZ":
        raise VerificationError("missing MZ header")
    offset = struct.unpack_from("<I", raw, 0x3c)[0]
    if not 0x40 <= offset or offset + 24 > len(raw) or raw[offset:offset + 4] != b"PE\x00\x00":
        raise VerificationError("missing PE signature")
    machine, sections = struct.unpack_from("<HH", raw, offset + 4)
    optional_size = struct.unpack_from("<H", raw, offset + 20)[0]
    characteristics = struct.unpack_from("<H", raw, offset + 22)[0]
    if optional_size < 64 or offset + 24 + optional_size > len(raw):
        raise VerificationError("truncated optional header")
    magic = struct.unpack_from("<H", raw, offset + 24)[0]
    return {"machine": machine, "magic": magic, "sections": sections,
            "executable": bool(characteristics & 0x0002), "dll": bool(characteristics & 0x2000)}


def pck_version(raw):
    if len(raw) < 20 or raw[:4] != b"GDPC":
        raise VerificationError("missing GDPC PCK magic")
    pack_format = struct.unpack_from("<I", raw, 4)[0]
    if not 2 <= pack_format <= 4:
        raise VerificationError(f"unsupported PCK format {pack_format}")
    return tuple(struct.unpack_from("<III", raw, 8))


def gdextension_release_library(path: Path):
    """Parse the packaged GDExtension manifest's effective Windows release entry.

    Independent of the server validator: whole-line ``;``/``#`` comments are
    ignored, a later assignment wins, and the required ``[configuration]`` entry
    symbol plus the quoted ``[libraries]`` ``windows.release.x86_64`` reference
    are read from the file Godot ships next to the executable. This proves the
    manifest's own declaration only; the PCK interior is not inspected.
    """
    sections, current = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#")):
            continue
        if stripped.startswith("["):
            if not stripped.endswith("]"):
                raise VerificationError("malformed GDExtension section")
            current = stripped[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if "=" not in stripped or current is None:
            raise VerificationError("malformed GDExtension line")
        key, _, value = stripped.partition("=")
        sections[current][key.strip()] = value.strip()
    symbol = sections.get("configuration", {}).get("entry_symbol", "")
    if len(symbol) < 3 or symbol[0] != '"' or symbol[-1] != '"':
        raise VerificationError("missing or unquoted GDExtension entry_symbol")
    reference = sections.get("libraries", {}).get("windows.release.x86_64", "")
    if len(reference) < 3 or reference[0] != '"' or reference[-1] != '"' or not reference[1:-1].startswith("res://"):
        raise VerificationError("missing or unquoted Windows release library entry")
    return symbol[1:-1], reference[1:-1]


class Verify:
    def __init__(self, root: Path):
        self.root = root
        self.data = root / "data"
        self.evidence = root / "evidence"
        self.db_path = self.data / "gep.sqlite3"
        self.owner_password = secrets.token_urlsafe(24)
        self.instance_id = str(uuid.uuid4())
        self.secret_key = secrets.token_urlsafe(48)
        self.port = None
        self.server = None
        self.godot = None
        self.template = None
        self.godot_runs = []
        self.checks = []
        self.artifacts = {}
        self.summary_lines = []

    # ------------------------------------------------------------------ helpers
    def log(self, message):
        line = f"[phase03-windows] {message}"
        print(line, flush=True)
        self.summary_lines.append(line)

    def record(self, ok, label, detail=None):
        entry = {"ok": bool(ok), "label": label, "detail": detail}
        self.checks.append(entry)
        self.log(("ok   " if ok else "FAIL ") + label + (f" :: {detail}" if detail is not None else ""))
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

    # ------------------------------------------------------------- environment
    def godot_binary(self):
        candidate = os.environ.get("GEP_GODOT_BIN") or shutil.which("godot")
        if candidate is None and Path("/Applications/Godot.app/Contents/MacOS/Godot").is_file():
            candidate = "/Applications/Godot.app/Contents/MacOS/Godot"
        return candidate

    def windows_template(self):
        """Resolve the shared pinned official Windows template.

        Raises :class:`VerificationError` when no template is installed or when
        the first candidate that carries the file does not match the pinned
        official bytes (a mismatched override is never silently skipped).
        """
        try:
            return find_windows_template()
        except TemplateError as error:
            raise VerificationError(str(error))

    def preconditions(self):
        missing = []
        for label, path in (("project virtualenv", VENV_PYTHON), ("gunicorn", GUNICORN),
                            ("license", LICENSE), ("godot-sqlite license", GODOT_SQLITE_LICENSE),
                            ("frozen engine notices", ENGINE_NOTICES),
                            ("engine notices export script", ENGINE_NOTICES_SCRIPT),
                            ("browser driver", DRIVER), ("Windows export preset project", PROJECT / "export_presets.cfg")):
            if not path.exists():
                missing.append(f"{label} missing: {path}")
        if shutil.which("node") is None:
            missing.append("node executable not on PATH (Playwright driver required)")
        self.godot = self.godot_binary()
        if self.godot is None or not Path(self.godot).exists():
            missing.append("official local Godot engine not found (GEP_GODOT_BIN, PATH or /Applications/Godot.app)")
        try:
            self.template = self.windows_template()
        except VerificationError as error:
            self.template = None
            missing.append(str(error))
        if missing:
            raise VerificationError("missing tool environment: " + "; ".join(missing))

    def run_godot(self, label, arguments, timeout, extra_env=None):
        """Run one Godot subprocess, keep its real exit code and its full log."""
        env = {**os.environ, **(extra_env or {})}
        result = subprocess.run([self.godot, *arguments], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=timeout)
        log = self.evidence / f"{label}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        output_text = (result.stdout or "") + (result.stderr or "")
        log.write_text(output_text)
        self.godot_runs.append({"label": label, "arguments": arguments, "exit": result.returncode,
                                "log": str(log), "tail": output_text[-300:]})
        self.log(f"godot {label} 退出码 {result.returncode}")
        return result

    def warm_import_cache(self):
        """Warm the project import cache with the supported headless import step.

        On macOS the first editor process over a fresh cache can abort during
        its own shutdown *after* the import itself completed (Godot 4.7.2). That
        first non-zero exit is kept as crash evidence and never treated as a
        successful step: a separate import retry must exit 0 before any export
        is attempted.
        """
        first = self.run_godot("godot_import_1", ["--headless", "--path", str(PROJECT), "--import"], 900)
        if first.returncode == 0:
            return
        second = self.run_godot("godot_import_2", ["--headless", "--path", str(PROJECT), "--import"], 900)
        self.require(second.returncode == 0, "冷缓存导入重试成功退出",
                     {"first_exit": first.returncode, "second_exit": second.returncode})

    def export_release(self, output):
        """Export the Windows release; only a subprocess that exits 0 is accepted."""
        arguments = ["--headless", "--path", str(PROJECT), "--export-release", "Windows", str(output)]
        first = self.run_godot("godot_export_1", arguments, 1800)
        if first.returncode == 0:
            return first
        # A crashed export wrote outputs that are never accepted. Keep its log,
        # warm the cache with a separate supported import step and retry one
        # full export that must exit 0; there is no third attempt.
        self.log(f"导出第 1 次退出码 {first.returncode}，其产物不被接受")
        self.warm_import_cache()
        return self.run_godot("godot_export_2", arguments, 1800)

    def verify_notice_provenance(self):
        """Re-export the official local engine's own notices and compare."""
        exported = self.root / "engine_notices_export.json"
        result = self.run_godot("engine_notices_export", ["--headless", "--script", str(ENGINE_NOTICES_SCRIPT)],
                                180, extra_env={"GEP_NOTICE_OUTPUT": str(exported)})
        self.require(result.returncode == 0, "官方本地引擎声明导出子进程成功退出",
                     {"exit": result.returncode})
        self.require(exported.is_file(), "官方本地引擎可导出自有许可/版权声明",
                     {"tail": (result.stdout + result.stderr)[-200:]})
        frozen = json.loads(ENGINE_NOTICES.read_text())
        self.require(json.loads(exported.read_text()) == frozen, "冻结的引擎声明等于官方本地引擎导出的声明")
        self.require(frozen["engine_version"]["string"] == "4.7.2-stable (official)",
                     "冻结声明对应描述契约固定的引擎版本", frozen["engine_version"]["string"])

    # -------------------------------------------------------------- cross-build
    def cross_build(self):
        """Export the real Windows x64 program with the pinned tools and package it."""
        template = self.template or self.windows_template()
        try:
            template_digest = verify_windows_template(template)
        except TemplateError as error:
            raise VerificationError(str(error))
        self.record(True, "Windows 导出模板字节等于固定官方摘要",
                    {"template": str(template), "sha256": template_digest,
                     "bytes": template.stat().st_size})
        TEMPLATES.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template, TEMPLATES / WINDOWS_TEMPLATE)
        version = subprocess.check_output([self.godot, "--version"], text=True).strip()
        self.require(version.startswith("4.7.2.stable."), "交叉构建使用固定的官方 Godot 4.7.2", version)
        output = PROGRAM_ROOT / ENTRY
        if PROGRAM_ROOT.exists():
            shutil.rmtree(PROGRAM_ROOT)
        PROGRAM_ROOT.mkdir(parents=True, exist_ok=True)
        # Only a subprocess that exits 0 counts: an export that writes plausible
        # files and then fails (for example the known fresh-cache macOS editor
        # shutdown abort) is rejected, its evidence kept, and only a separate
        # successful retry may proceed. Outputs are written into an empty
        # program root, so the accepted run provably produced them.
        self.warm_import_cache()
        export = self.export_release(output)
        self.require(export.returncode == 0, "官方 Windows 模板导出子进程成功退出",
                     {"exit": export.returncode})
        self.require(output.is_file(), "官方 Windows 模板导出真实 EXE",
                     {"exit": export.returncode, "tail": (export.stdout + export.stderr)[-300:]})
        pack = subprocess.run([str(VENV_PYTHON), str(ROOT / "tools/package_build.py"), "windows"],
                              cwd=ROOT, capture_output=True, text=True, timeout=900)
        self.require(PROGRAM_ARCHIVE.is_file() and pack.returncode == 0, "真实 Windows 程序归档完成",
                     {"exit": pack.returncode, "tail": (pack.stdout + pack.stderr)[-300:]})
        descriptor = json.loads(DESCRIPTOR.read_text())
        self.require(descriptor["platform"] == "windows_x64", "描述声明 windows_x64 平台", descriptor["platform"])
        package = descriptor["package"]
        self.require(package["root"] == PROGRAM_ROOT.name and package["entry"] == ENTRY,
                     "描述显式声明程序根与入口", package)
        self.require(package["dependencies"] == [DEPENDENCY] and package["extension"] == EXTENSION,
                     "描述显式声明依赖与 GDExtension 清单", package)

        entry_bytes = (PROGRAM_ROOT / ENTRY).read_bytes()
        entry_headers = pe_headers(entry_bytes[:4096])
        self.require(entry_headers["machine"] == WINDOWS_MACHINE and entry_headers["magic"] == WINDOWS_MAGIC,
                     "真实入口为 PE32+ x86-64", entry_headers)
        self.require(entry_headers["executable"] and not entry_headers["dll"], "入口具备可执行映像标志", entry_headers)
        dependency_headers = pe_headers((PROGRAM_ROOT / DEPENDENCY).read_bytes()[:4096])
        self.require(dependency_headers["machine"] == WINDOWS_MACHINE and dependency_headers["magic"] == WINDOWS_MAGIC
                     and dependency_headers["dll"], "真实 SQLite 依赖为 PE32+ x86-64 DLL", dependency_headers)
        pck = (PROGRAM_ROOT / PCK).read_bytes()[:20]
        self.require(pck_version(pck) == HOST_VERSION, "真实 PCK 头部记录固定引擎版本 4.7.2", pck_version(pck))
        # The manifest's effective declaration, not a comment or another platform
        # key: the Windows release x86_64 entry must name the packaged DLL.
        symbol, reference = gdextension_release_library(PROGRAM_ROOT / EXTENSION)
        self.require(symbol == "sqlite_library_init", "GDExtension 配置声明有效 entry_symbol", symbol)
        self.require(reference.rsplit("/", 1)[-1] == DEPENDENCY and (PROGRAM_ROOT / DEPENDENCY).is_file(),
                     "GDExtension 的 Windows release x86_64 项解析到随包 SQLite DLL",
                     {"reference": reference, "packaged": DEPENDENCY})
        digest = self.sha256_file(PROGRAM_ARCHIVE)
        self.require(digest == descriptor["program_sha256"], "程序归档摘要等于描述中的程序摘要", digest)
        self.require(not (PROGRAM_ROOT / "connection.json").exists(),
                     "程序归档不含连接配置（冻结时才写入）")
        self.require((ROOT / "examples/synthetic_experiment/task.gd").is_file(),
                     "科学任务文件仍为同一工程文件")
        self.artifacts["build"] = {"godot": version, "entry_bytes": output.stat().st_size,
                                   "dependency_bytes": (PROGRAM_ROOT / DEPENDENCY).stat().st_size,
                                   "archive_bytes": PROGRAM_ARCHIVE.stat().st_size, "program_sha256": digest,
                                   "pck_version": list(HOST_VERSION)}
        return descriptor

    # ---------------------------------------------------------------- instance
    def free_port(self):
        for candidate in range(8060, 8130):
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", candidate))
                except OSError:
                    continue
            return candidate
        raise VerificationError("no free port >= 8060")

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

    def run_python(self, code, extra_env=None, timeout=180):
        env = self.server_env()
        if extra_env:
            env.update(extra_env)
        return subprocess.run([str(VENV_PYTHON), "-c", code], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=timeout)

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
            fd = os.open(self.data / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(value)

    def start_server(self):
        if self.port is None:
            self.port = self.free_port()
        # The public API of this run is the real HTTP origin; the isolated server
        # binds it on loopback so the frozen configuration points at this instance.
        log = open(self.root / "gunicorn.log", "w")
        self.server = subprocess.Popen(
            [str(GUNICORN), "gep.wsgi:application", "--bind", f"127.0.0.1:{self.port}",
             "--workers", "1", "--threads", "4", "--timeout", "1800", "--access-logfile", "-"],
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

    # ----------------------------------------------------------------- database
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

    # ------------------------------------------------------------------- driver
    def driver(self, command, job):
        job_path = self.root / f"job_{command}.json"
        job_path.write_text(json.dumps(job))
        result = subprocess.run(["node", str(DRIVER), command, str(job_path)], cwd=ROOT,
                                capture_output=True, text=True, timeout=2400)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise VerificationError(f"browser driver {command} produced no JSON (exit {result.returncode})\n"
                                    f"stdout:{result.stdout[-2000:]}\nstderr:{result.stderr[-2000:]}")
        payload["_exit"] = result.returncode
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
                "run_dir": str(self.evidence), "title": "Windows package verification", "max_sessions": 2,
                "platform": "windows_x64",
                "sidecar_members": ["artifact_manifest.json", f"{PROGRAM_ROOT.name}/connection.json",
                                    "LICENSE", NOTICES_MEMBER],
                "descriptor": str(DESCRIPTOR), "program": str(PROGRAM_ARCHIVE)}

    # --------------------------------------------------------- artifact checks
    def verify_downloaded_package(self, package: Path, release_id, sidecars):
        record = self.release_record(release_id)
        digest = self.sha256_file(package)
        self.require(digest == record["digest"], "下载的完整包摘要等于数据库外层摘要", digest)
        self.require(package.stat().st_size == record["size"], "完整包大小与数据库记录一致", record["size"])
        second = package.parent / "complete_package_second.zip"
        self.require(self.sha256_file(second) == digest, "重复授权下载逐字节一致")

        descriptor = json.loads(DESCRIPTOR.read_text())
        expected_schemas = descriptor["schemas"]
        with zipfile.ZipFile(package) as archive, zipfile.ZipFile(PROGRAM_ARCHIVE) as program:
            names = {item.filename for item in archive.infolist() if not item.filename.endswith("/")}
            manifest = json.loads(archive.read("artifact_manifest.json"))
            self.require(manifest["artifact_format_version"] == "gep-artifact/v1", "完整包清单格式版本固定",
                         manifest["artifact_format_version"])
            self.require(manifest["platform"] == "windows_x64" and manifest["app"] == PROGRAM_ROOT.name,
                         "清单声明 windows_x64 程序根", {"platform": manifest["platform"], "app": manifest["app"]})
            self.require(manifest["entry"] == f"{PROGRAM_ROOT.name}/{ENTRY}", "清单声明显式入口", manifest["entry"])
            self.require(manifest["dependencies"] == [f"{PROGRAM_ROOT.name}/{DEPENDENCY}"],
                         "清单声明原生依赖", manifest["dependencies"])
            self.require(manifest["extension"] == f"{PROGRAM_ROOT.name}/{EXTENSION}",
                         "清单声明 GDExtension 清单", manifest["extension"])
            self.require(manifest["config_member"] == f"{PROGRAM_ROOT.name}/connection.json",
                         "清单记录的冻结配置位于 EXE 同级", manifest["config_member"])
            self.require(manifest["program_sha256"] == descriptor["program_sha256"],
                         "清单程序摘要等于上传的程序归档摘要")
            self.require(not ({"artifact_digest", "artifact_sha256", "self_sha256"} & set(manifest)),
                         "清单不含自身外层摘要（非循环）")
            members = {entry["path"]: entry for entry in manifest["members"]}
            self.require(len(members) == len(manifest["members"]) and set(members) | {"artifact_manifest.json"} == names,
                         "清单成员集合与实际成员完全一致", len(members))
            for name, entry in members.items():
                member_digest, size = self.member_hash(archive, name)
                info = archive.getinfo(name)
                self.require(member_digest == entry["sha256"] and size == entry["size"],
                             f"成员内容与清单一致：{name}")
                self.require(f"{((info.external_attr >> 16) & 0o7777):04o}" == entry["mode"],
                             f"成员模式保持：{name}", entry["mode"])
            program_names = {item.filename for item in program.infolist() if not item.filename.endswith("/")}
            self.require(program_names <= set(names), "完整包含全部程序成员", len(program_names))
            # The generated configuration is an added member, never a replacement
            # of a program member the export itself produced.
            self.require(manifest["config_member"] not in program_names,
                         "冻结配置是新增成员而非覆盖程序字节", manifest["config_member"])
            unchanged = 0
            for name in program_names:
                source, copied = program.getinfo(name), archive.getinfo(name)
                source_hash, source_size = self.member_hash(program, name)
                copied_hash, copied_size = self.member_hash(archive, name)
                if source_hash == copied_hash and source_size == copied_size \
                        and (source.external_attr >> 16) & 0o7777 == (copied.external_attr >> 16) & 0o7777:
                    unchanged += 1
            self.require(unchanged == len(program_names), "可执行文件、PCK 与原生依赖逐字节未改", f"{unchanged}/{len(program_names)}")
            bundled_config = archive.read(manifest["config_member"])
            self.require(bundled_config == Path(sidecars[manifest["config_member"]]).read_bytes(),
                         "GUI 导出的 connection.json 与包内一致")
            config = json.loads(bundled_config)
            self.require(config["mode"] == "anonymous" and config["purpose"] == "synthetic",
                         "冻结公开配置含冻结模式与用途", config["mode"])
            self.require(config["api_url"] == f"http://experiment.localhost:{self.port}",
                         "冻结公开配置指向本次隔离实例的参与链路", config["api_url"])
            self.require(not ({"password", "participants", "roster", "token", "secret"} & set(config)),
                         "公开配置不含密码、名单或凭据")
            self.require(manifest["config_sha256"] == hashlib.sha256(bundled_config).hexdigest(), "清单记录冻结配置摘要")
            for key, definition in expected_schemas.items():
                self.require(json.loads(archive.read(f"schemas/{key}.json")) == definition, f"schema 冻结一致：{key}")
            self.require(json.loads(archive.read("metadata/codebook.json")) == descriptor["codebook"], "codebook 冻结一致")
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
                                         "manifest": manifest}
        return manifest

    def unpack_and_inspect(self, package: Path, manifest):
        """Extract the frozen package and prove the layout the Windows program reads.

        The program itself is never executed here: only a real Windows host can do
        that, and the run reports it as NOT_RUN.
        """
        target = self.root / "unpacked"
        if target.exists():
            shutil.rmtree(target)
        with zipfile.ZipFile(package) as archive:
            for item in archive.infolist():
                if item.filename.endswith("/"):
                    continue
                destination = target / item.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(item))
                destination.chmod((item.external_attr >> 16) & 0o7777 or 0o644)
        program_dir = target / PROGRAM_ROOT.name
        binary = program_dir / ENTRY
        self.require(binary.is_file(), "解包后程序根目录与入口存在", str(binary.relative_to(target)))
        self.require((program_dir / PCK).is_file(), "解包后 PCK 与入口同级")
        self.require((program_dir / DEPENDENCY).is_file(), "解包后 SQLite 依赖与入口同级")
        self.require((program_dir / EXTENSION).is_file(), "解包后 GDExtension 清单随程序分发")
        self.require((program_dir / "connection.json").is_file(),
                     "外置冻结配置位于 EXE 同级（程序直接读取，无需替换文件）")
        headers = pe_headers(binary.read_bytes()[:4096])
        self.require(headers["machine"] == WINDOWS_MACHINE and headers["magic"] == WINDOWS_MAGIC,
                     "冻结包内入口仍为 PE32+ x86-64", headers)
        self.require(pck_version((program_dir / PCK).read_bytes()[:20]) == HOST_VERSION,
                     "冻结包内 PCK 仍记录 4.7.2")
        symbol, reference = gdextension_release_library(program_dir / EXTENSION)
        self.require(symbol == "sqlite_library_init"
                     and reference.rsplit("/", 1)[-1] == DEPENDENCY
                     and (program_dir / DEPENDENCY).is_file(),
                     "冻结包内 GDExtension 的 Windows release x86_64 项解析到同一 DLL",
                     {"reference": reference, "packaged": DEPENDENCY})
        # Real Windows execution needs a real Windows host: never simulated here.
        self.record(True, f"Windows 实机执行：{NOT_RUN}（交叉构建与冻结包本机工程验证通过）",
                    {"reason": "no Windows host in this environment", "entry": binary.name})
        self.artifacts["unpacked"] = {"entry_sha256": self.sha256_file(binary),
                                      "pck_version": list(HOST_VERSION), "execution": NOT_RUN}
        return program_dir

    # ------------------------------------------------------------------ admission
    def admission_success(self, study_id, release_id):
        """The frozen Windows release really admits through the real HTTP API.

        The program itself is not executed here; this proves the server-side
        admission contract of the frozen release against the temporary database
        and the real participant endpoint.
        """
        release_row, build_row = self.release_ids(study_id)
        before = self.session_count(study_id)
        operation = str(uuid.uuid4())
        body = json.dumps({"operation_id": operation, "proof": "p" * 48, "instance_id": self._instance_uuid(),
                           "study_id": study_id, "release_id": release_row, "build_id": build_row}).encode()
        request = urlrequest.Request(f"http://127.0.0.1:{self.port}/v1/participant/sessions", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urlrequest.urlopen(request, timeout=30) as response:
                status, payload_body = response.status, response.read()
        except urlerror.HTTPError as error:
            status, payload_body = error.code, error.read()
        self.require(status == 200, "冻结的 Windows 发行经真实 HTTP 准入创建会话",
                     {"status": status, "body": payload_body[:160].decode(errors="replace")})
        admission = json.loads(payload_body)
        self.require(admission.get("session_id") and admission.get("token") and admission.get("release_id") == release_row,
                     "准入响应返回真实会话与冻结发行绑定",
                     {"session_id": admission.get("session_id"), "release_id": admission.get("release_id")})
        self.require(self.session_count(study_id) == before + 1, "准入会话写入真实数据库",
                     {"before": before, "after": self.session_count(study_id)})
        self.artifacts["admission"] = {"operation_id": operation, "status": status,
                                       "session_id": admission.get("session_id"), "sessions": before + 1}

    # ------------------------------------------------------------------ tamper
    def tamper_phase(self, release_id, release_url, study_id, digest):
        stored = self.data / "artifacts" / self.release_record(release_id)["path"]
        original = stored.read_bytes()
        (self.evidence / "package_before_tamper.zip").write_bytes(original)
        tampered = bytearray(original)
        tampered[len(tampered) // 2] = (tampered[len(tampered) // 2] + 1) % 256
        stored.write_bytes(bytes(tampered))
        self.require(self.sha256_file(stored) != digest, "已篡改存储中的完整包字节")
        job = {"admin_url": f"http://admin.localhost:{self.port}",
               "credentials": {"username": "synthetic_owner", "password": self.owner_password},
               "run_dir": str(self.evidence),
               "items": [{"url": f"http://admin.localhost:{self.port}{release_url}"},
                         {"url": f"http://admin.localhost:{self.port}{release_url}/{PROGRAM_ROOT.name}/connection.json"},
                         {"url": f"http://admin.localhost:{self.port}{release_url}/artifact_manifest.json"},
                         {"url": f"http://admin.localhost:{self.port}{release_url}/{NOTICES_MEMBER}"}]}
        payload = self.driver("downloads", job)
        downloads = payload["evidence"]["downloads"]
        self.require(all(entry["status"] == 409 for entry in downloads),
                     "篡改后完整包与全部 sidecar 均拒绝下载（409）", [entry["status"] for entry in downloads])

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
                     "篡改后准入在创建会话前失败关闭",
                     {"status": status, "body": payload_body[:120].decode(errors="replace")})
        self.require(self.session_count(study_id) == before, "篡改后没有创建新会话",
                     {"before": before, "after": self.session_count(study_id)})

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
    def verify(self):
        self.preconditions()
        self.record(True, "固定工具链存在", {"godot": self.godot, "templates": str(self.template)})
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.verify_notice_provenance()
        self.cross_build()
        self.init_instance()
        self.port = self.free_port()
        self.start_server()
        self.record(True, "隔离实例与真实服务器就绪", {"port": self.port, "data": str(self.data)})
        try:
            payload = self.driver("package", self.job())
            self.require(not payload.get("failures"), "真实浏览器 GUI 流程全部通过", len(payload.get("failures", [])))
            flow = payload["evidence"]["package"]
            self.require(self.session_count(flow["study_id"]) == 0,
                         "门户与稳定入口浏览 Windows 当前发行时未创建任何会话")
            release_id = flow["release_id"]
            package = Path(flow["artifact_path"])
            manifest = self.verify_downloaded_package(package, release_id, flow["sidecars"])
            self.unpack_and_inspect(package, manifest)
            self.admission_success(flow["study_id"], release_id)
            self.tamper_phase(release_id, flow["release_url"], flow["study_id"], self.release_record(release_id)["digest"])
        finally:
            self.stop_server()
        failures = [check for check in self.checks if not check["ok"]]
        self.artifacts["godot_subprocesses"] = self.godot_runs
        (self.root / "evidence.json").write_text(json.dumps({"checks": self.checks, "artifacts": self.artifacts},
                                                            indent=2, default=str))
        (self.root / "summary.txt").write_text("\n".join(self.summary_lines) + "\n")
        return not failures


def main():
    parser = argparse.ArgumentParser(description="Phase 03 Windows x64 complete package verification")
    parser.add_argument("--verify", action="store_true", help="cross-build and verify, exit non-zero on failure")
    parser.add_argument("--root", default=None, help="evidence root (defaults to a new stamp under windows_package_verify)")
    args = parser.parse_args()
    if not args.verify:
        parser.error("only --verify is supported")
    PHASE_ROOT.mkdir(parents=True, exist_ok=True)
    root = Path(args.root) if args.root else PHASE_ROOT / "windows_package_verify" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    root.mkdir(parents=True, exist_ok=True)
    verify = Verify(root)
    try:
        ok = verify.verify()
    except VerificationError as error:
        verify.log(f"verification aborted: {error}")
        (root / "summary.txt").write_text("\n".join(verify.summary_lines) + "\n")
        (root / "evidence.json").write_text(json.dumps({"checks": verify.checks, "artifacts": verify.artifacts,
                                                        "godot_subprocesses": verify.godot_runs,
                                                        "error": str(error)}, indent=2, default=str))
        print(f"PHASE03_WINDOWS_VERIFY_FAILED {root}")
        return 1
    except Exception as error:  # unexpected: keep the evidence and fail loudly
        verify.log(f"unexpected failure: {error!r}")
        (root / "summary.txt").write_text("\n".join(verify.summary_lines) + "\n")
        (root / "evidence.json").write_text(json.dumps({"checks": verify.checks, "artifacts": verify.artifacts,
                                                        "godot_subprocesses": verify.godot_runs,
                                                        "error": repr(error)}, indent=2, default=str))
        print(f"PHASE03_WINDOWS_VERIFY_ERROR {root} :: {error!r}")
        return 1
    finally:
        verify.stop_server()
    print(("PHASE03_WINDOWS_VERIFY_OK " if ok else "PHASE03_WINDOWS_VERIFY_FAILED ") + str(root))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
