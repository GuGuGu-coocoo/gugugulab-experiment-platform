#!/usr/bin/env python3
"""03F designer environment and frozen participant packages (engineering only).

``--prepare`` creates a NEW, empty synthetic designer environment under
``local_data/phase03_20260920/designer_<stamp>/`` and freezes the *authentic*
platform downloads of that environment under ``build/phase03_20260920/<stamp>/``:

* the environment owns its isolated instance (own data directory, database,
  secret and loopback port >= 8040), initialized by a guarded initializer that
  refuses to overwrite anything and refuses reserved/protected roots;
* every frozen package is downloaded through the real platform lifecycle of
  *this* instance - a Web build uploaded through the researcher GUI, and complete
  macOS/Windows native packages registered, uploaded, approved and downloaded
  through the authorized release endpoints - with the frozen ``connection.json``
  and ``artifact_manifest.json`` sidecars and the license/notices members
  verified against the packaged members and the database record. Nothing is
  re-packaged or hand-edited, and no credential ever enters a package;
* the owner password stays in a separate ``0600`` file outside the frozen kit;
* ``service.py`` + ``START-HERE.command`` (double-click, no commands typed by the
  designer) start the service on the frozen port and open Chrome on the correct
  local admin URL. The service helper verifies the running instance identity and
  refuses an occupied port or a reused PID instead of opening the wrong
  instance; it never executes text from ``service.env``.

``--verify`` re-checks the frozen kit (checksums, recursive member scans, real
content digests, config/binding and generated references) as a **launch-blocking
preflight gate**: any failure or parse exception writes an explicit ``NOT_READY``
readiness document and returns non-zero *before* anything is started. Only a
clean preflight starts the service through the *generated* START-HERE path from
a stopped state, proves the read-only health/instance identity, runs the frozen
macOS package through the real API/database/export using only the packaged
configuration, and proves a real Chrome login. Human designer QA and independent
T17 stay ``NOT_RUN`` in every artifact this tool writes: it prepares an
environment, it never claims human acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from roster_import_client import preview_and_commit, roster_csv  # noqa: E402  (tools/ path above)
PHASE_ROOT = ROOT / "local_data" / "phase03_20260920"
FREEZE_ROOT = ROOT / "build" / "phase03_20260920"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
GUNICORN = ROOT / ".venv" / "bin" / "gunicorn"
SETUP_DRIVER = ROOT / "tests" / "browser" / "phase03_acceptance_setup.mjs"
CHECK_DRIVER = ROOT / "tests" / "browser" / "phase03_designer_check.mjs"
PROTECTED_DB = ROOT / "local_data" / "gep.sqlite3"
PROTECTED_VOLUME = ROOT / "local_data" / "independent_acceptance_20260912"
RESERVED_ROOTS = (ROOT, ROOT / "local_data", ROOT / "build", PHASE_ROOT, FREEZE_ROOT,
                  PROTECTED_VOLUME, PROTECTED_DB.parent)
PLATFORM_SOURCES = {
    "web": {"archive": ROOT / "build" / "synthetic_web.zip", "mode": "anonymous", "kind": "web"},
    "macos-arm64": {"archive": ROOT / "build" / "native" / "synthetic.zip",
                    "descriptor": ROOT / "build" / "native" / "descriptor.json",
                    "mode": "id", "kind": "native"},
    "windows-x64": {"archive": ROOT / "build" / "windows" / "synthetic_windows.zip",
                    "descriptor": ROOT / "build" / "windows" / "descriptor.json",
                    "mode": "password", "kind": "native"},
}
PLATFORM_LABELS = {"web": "Web（Godot Web 导出）", "macos-arm64": "macOS arm64 完整包",
                   "windows-x64": "Windows x64 完整包"}
ROSTER_ID = "001"
ROSTER_PASSWORD = "synthetic-designer-roster-password"
# The frozen complete packages downloaded from the platform carry their own
# LICENSE / THIRD_PARTY_NOTICES members (verified below); the freeze directory
# also keeps the project-side license sources so the handoff is self-contained.
LICENSES = (("LICENSE", ROOT / "LICENSE"),
            ("godot_sqlite_license.md", ROOT / "third_party" / "godot_sqlite_license.md"),
            ("godot_engine_notices.json", ROOT / "server" / "core" / "data" / "godot_engine_notices.json"))
SIDECAR_MEMBERS = ("artifact_manifest.json", "connection.json", "LICENSE", "THIRD_PARTY_NOTICES.txt")
FORBIDDEN_MEMBER = re.compile(r"(^|/)([^/]*\.(sqlite|sqlite3|db|jsonl|sqlite-wal|sqlite-shm|sqlite3-wal|sqlite3-shm))$",
                              re.IGNORECASE)
FORBIDDEN_MEMBER_NAME = re.compile(r"(queue|writer|session|participant)", re.IGNORECASE)
# Generic credential patterns are only meaningful in data/text members. Program
# sources and assets legitimately contain identifiers such as
# ``password:'gec-input-password'``, so they are checked for PEM keys and for the
# known private values instead of being refused on a keyword.
GENERIC_SCAN_SUFFIXES = (".json", ".txt", ".yaml", ".yml", ".env", ".ini", ".cfg", ".conf",
                         ".csv", ".log", ".md", ".xml", ".toml")
SAMPLE_STUDY_LABEL = "03F 设计者体验示例（可自行新建研究）"
HUMAN_STATUS = {"designer_autonomous_qa": "NOT_RUN", "independent_t17": "NOT_RUN"}


class DesignerKitError(Exception):
    """The preparation itself is defective (never a human-acceptance claim)."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_freeze_manifest(freeze):
    """The frozen kit manifest, or ``(None, reason)`` when absent/unreadable.

    A missing, non-JSON or non-object manifest is a pre-launch failure of the
    readiness gate (never an empty manifest that quietly passes).
    """
    path = Path(freeze) / "manifest.json"
    if not path.is_file():
        return None, f"manifest.json missing: {path}"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, f"manifest.json unreadable: {error!r}"
    if not isinstance(document, dict) or not document:
        return None, "manifest.json is not a non-empty object"
    return document, None


def atomic_write_private(path, text, mode=0o600):
    """Create/replace a credential-bearing file with the final mode in one step."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp{secrets.token_hex(6)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def restrict_private(path, record=None):
    """Restrict an already-created task-owned credential copy (never protected data)."""
    path = Path(path)
    if not path.is_file():
        return False
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        os.chmod(path, 0o600)
        if record is not None:
            record(True, "已存在的工作文件权限收紧为 0600", str(path))
    return True


def protected_digest():
    """Real content digest of the protected local data (never a size summary)."""
    digest = hashlib.sha256()
    for candidate in (PROTECTED_DB, Path(str(PROTECTED_DB) + "-wal"), Path(str(PROTECTED_DB) + "-shm")):
        if candidate.is_file():
            digest.update(candidate.name.encode())
            digest.update(sha256_file(candidate).encode())
    if PROTECTED_VOLUME.is_dir():
        for path in sorted(PROTECTED_VOLUME.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(PROTECTED_VOLUME).as_posix().encode())
                digest.update(sha256_file(path).encode())
    return digest.hexdigest()


def scan_secrets(path, limit=8 * 1024 * 1024):
    import phase03_windows_kit as windows_kit
    return windows_kit.scan_secrets(path, limit=limit)


def walk_files(root):
    return [path for path in sorted(Path(root).rglob("*")) if path.is_file()]


def display_path(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def uuid_text(raw):
    """Django's ``<uuid:...>`` URL converter needs the dashed canonical form."""
    if isinstance(raw, bytes):
        raw = raw.decode()
    text = str(raw)
    if len(text) == 32 and "-" not in text:
        return f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"
    return text


def archive_findings(path, secrets_values=(), member_limit=20000, byte_limit=1 << 30, scan_limit=8 << 20):
    """Bounded recursive checks of a ZIP's *members* (not just the outer bytes).

    A frozen package is refused when a member carries credential-like content, a
    member path looks like a session store/export, the archive exceeds the
    bounded member/byte budget, or a member cannot be read. Each member is
    scanned up to ``scan_limit`` bytes; the budget stops a zip-bomb-shaped
    archive before it is read. ``findings`` is a list of precise strings; an
    empty list means the archive passed.
    """
    findings = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.filename.endswith("/")]
    except (OSError, zipfile.BadZipFile) as error:
        return [f"archive unreadable: {error}"]
    if len(infos) > member_limit:
        return [f"archive member count {len(infos)} exceeds {member_limit}"]
    if sum(info.file_size for info in infos) > byte_limit:
        return [f"archive uncompressed size exceeds {byte_limit}"]
    for info in infos:
        if FORBIDDEN_MEMBER.search(info.filename) or (
                FORBIDDEN_MEMBER_NAME.search(Path(info.filename).name)
                and Path(info.filename).suffix.lower() in (".sqlite", ".sqlite3", ".db", ".jsonl", ".json")):
            findings.append(f"member looks like a session store/export: {info.filename}")
            continue
        if info.file_size == 0:
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                with archive.open(info.filename) as stream:
                    raw = stream.read(scan_limit)
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            findings.append(f"member unreadable ({info.filename}): {error}")
            continue
        for pattern in (rb"BEGIN (RSA|OPENSSH|EC|PRIVATE) PRIVATE KEY",):
            if re.search(pattern, raw, re.IGNORECASE):
                findings.append(f"member carries credential-like content: {info.filename}")
                break
        else:
            if Path(info.filename).suffix.lower() in GENERIC_SCAN_SUFFIXES:
                for pattern in (rb"password\s*[:=]\s*[\"'][^\"'\s]{4,}",
                                rb"token\s*[:=]\s*[\"'][A-Za-z0-9_\-]{12,}",
                                rb"secret\s*[:=]\s*[\"'][A-Za-z0-9_\-]{8,}"):
                    if re.search(pattern, raw, re.IGNORECASE):
                        findings.append(f"member carries credential-like content: {info.filename}")
                        break
        for value in secrets_values:
            if value and value.encode("utf-8") in raw:
                findings.append(f"member carries a private account value: {info.filename}")
                break
    return findings


def required_members(platform, manifest_member=None):
    if platform == "web":
        return ("manifest.json", "web/index.html")
    return ("artifact_manifest.json", manifest_member or "connection.json", "LICENSE", "THIRD_PARTY_NOTICES.txt")


def sh_quote(value):
    """POSIX single-quote quoting; spaces and apostrophes survive both sh and the parser."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def parse_env_text(text):
    """Parse the simple KEY='value' env file without executing any of it."""
    document = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, raw = stripped.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            continue
        value = raw.strip()
        if value.startswith("'"):
            if not value.endswith("'"):
                continue
            inner = value[1:-1]
            value = inner.replace("'\\''", "'")
        elif value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        document[key.strip()] = value
    return document


# ------------------------------------------------------------------- lifecycle
class PlatformClient:
    """The real platform lifecycle of one isolated instance (no mocks)."""

    def __init__(self, port, db_path):
        import phase03_windows_kit as windows_kit
        self.http = windows_kit.HttpClient(port)
        self.port = port
        self.db_path = Path(db_path)
        self.password = None

    def login(self, username, password):
        self.http.login(username, password)
        # The study-page roster commit re-authenticates with the operator's own
        # password, so the kit keeps it for that step only (never written to
        # evidence or logs).
        self.password = password

    def rows(self, sql, params=()):
        connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=20)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def study_operation(self, study_id, fields, expect=(200, 302)):
        status, payload, _ = self.http.post_form(f"/studies/{study_id}", fields, expect_redirect=False)
        if status not in expect:
            raise DesignerKitError(f"study op {fields[0][1]!r} returned {status}: "
                                   f"{payload[:300].decode('utf-8', 'replace')}")
        return status, payload

    def create_study(self, title):
        status, _payload, record = self.http.post_form("/", [("title", title)])
        location = record.get("location") or ""
        match = re.search(r"/studies/([0-9a-f-]{36})", location)
        if status != 302 or not match:
            raise DesignerKitError(f"study creation failed: HTTP {status} {location!r}")
        return match.group(1)

    def configure(self, study_id, mode, max_sessions=8):
        self.study_operation(study_id, [("op", "configure"), ("mode", mode), ("max_sessions", str(max_sessions))])

    def roster(self, study_id, mode):
        """Create the labelled sample participant through the real study entry.

        The batch is encoded as real CSV rows by the shared
        ``roster_import_client.roster_csv`` (``csv.writer``, exact values, no
        hand-joined tabs) and then previewed and committed with the operator's
        own password (the study-page ``roster-import`` flow); the legacy direct
        write entry is refused by the platform and is never used here.
        """
        if mode == "anonymous":
            return
        if not self.password:
            raise DesignerKitError("roster import needs the logged-in operator password for confirmation")
        rows = [(ROSTER_ID, ROSTER_PASSWORD)] if mode == "password" else [(ROSTER_ID,)]
        added = preview_and_commit(self.http, study_id, roster_csv(rows), self.password)
        if added != 1:
            raise DesignerKitError(f"roster import reported {added} new IDs instead of 1")
        codes = [row[0] for row in self.rows("select code from core_participant where study_id=?",
                                             [study_id.replace("-", "")])]
        if codes != [ROSTER_ID]:
            raise DesignerKitError(f"roster import did not create the expected account: {codes}")

    def upload_web(self, study_id, archive):
        raw = Path(archive).read_bytes()
        status, payload, _ = self.http.post_multipart(
            f"/studies/{study_id}", [("op", "upload")], "package", Path(archive).name, raw)
        if status not in (200, 302):
            raise DesignerKitError(f"web upload returned {status}: {payload[:300].decode('utf-8', 'replace')}")
        rows = self.rows("select id, digest, package_path from core_build where study_id=? order by rowid desc limit 1",
                         [study_id.replace("-", "")])
        if not rows or not rows[0][2]:
            raise DesignerKitError("web upload did not bind the archive to a build")
        return {"id": uuid_text(rows[0][0]), "digest": rows[0][1], "package_path": rows[0][2]}

    def upload_native(self, study_id, descriptor_path, archive):
        descriptor = read_json(descriptor_path)
        self.study_operation(study_id, [("op", "native"), ("descriptor", json.dumps(descriptor))])
        rows = self.rows("select id from core_build where study_id=? and digest=?",
                         [study_id.replace("-", ""), descriptor["program_sha256"]])
        if not rows:
            raise DesignerKitError("the native descriptor was not registered as a build")
        build_id = uuid_text(rows[0][0])
        raw = Path(archive).read_bytes()
        status, payload, _ = self.http.post_multipart(
            f"/studies/{study_id}", [("op", "native_archive"), ("build_id", build_id)],
            "package", Path(archive).name, raw)
        if status not in (200, 302):
            raise DesignerKitError(f"native upload returned {status}: {payload[:300].decode('utf-8', 'replace')}")
        rows = self.rows("select id, digest, package_path from core_build where study_id=? and digest=?",
                         [study_id.replace("-", ""), descriptor["program_sha256"]])
        if not rows or not rows[0][2]:
            raise DesignerKitError("native upload did not bind the archive to a build")
        return {"id": uuid_text(rows[0][0]), "digest": rows[0][1], "package_path": rows[0][2], "descriptor": descriptor}

    def approve(self, study_id, build_id, native):
        self.study_operation(study_id, [("op", "approve"), ("build_id", build_id)])
        rows = self.rows("select id, approved, artifact_digest, artifact_size, artifact_path, config, build_id, study_id "
                         "from core_release where study_id=? order by rowid desc limit 1",
                         [study_id.replace("-", "")])
        if not rows:
            raise DesignerKitError("approval did not create a release")
        release_id, approved, digest, size, artifact_path, config, release_build, release_study = rows[0]
        config = json.loads(config) if isinstance(config, str) else config
        record = {"release_id": uuid_text(release_id), "approved": bool(approved), "digest": digest, "size": size,
                  "artifact_path": artifact_path, "config": config,
                  "build_id": uuid_text(release_build), "study_id": uuid_text(release_study)}
        if not approved:
            raise DesignerKitError("release was not approved")
        if native:
            if not digest or not artifact_path or config.get("artifact_format_version") != "gep-artifact/v1":
                raise DesignerKitError("native approval did not freeze a complete artifact")
        return record

    def open_recruitment(self, study_id):
        self.study_operation(study_id, [("op", "recruitment"), ("state", "open")])

    def export_config(self, release_id):
        status, payload, _ = self.http.get(f"/releases/{release_id}/config")
        if status != 200:
            raise DesignerKitError(f"config export returned {status}")
        return payload

    def download_artifact(self, release_id, target):
        status, payload, headers = self.http.get(f"/releases/{release_id}/artifact")
        if status != 200:
            raise DesignerKitError(f"artifact download returned {status}")
        Path(target).write_bytes(payload)
        return {"sha256": sha256_file(target), "size": len(payload),
                "header": headers["headers"].get("x-artifact-sha256")}

    def download_sidecar(self, release_id, member, target):
        from urllib.parse import quote as urlquote
        encoded = urlquote(member, safe="/")
        status, payload, headers = self.http.get(f"/releases/{release_id}/artifact/{encoded}")
        if status != 200:
            raise DesignerKitError(f"sidecar {member} download returned {status}")
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return {"sha256": sha256_file(destination), "size": len(payload),
                "header": headers["headers"].get("x-artifact-member-sha256")}

    def export_jsonl(self, study_id, target):
        body = json.dumps({"study_id": study_id}, separators=(",", ":")).encode("utf-8")
        status, payload, _ = self.http.request(
            "POST", "/v1/admin/exports", host="admin.localhost", body=body,
            headers={"Content-Type": "application/json", "X-CSRFToken": self.http.csrf},
            expect_redirect=False)
        if status != 201:
            raise DesignerKitError(f"authorized export creation returned {status}: {payload[:200]!r}")
        export_id = json.loads(payload)["export_id"]
        status, payload, _ = self.http.get(f"/v1/admin/exports/{export_id}/download?format=jsonl")
        if status != 200:
            raise DesignerKitError(f"authorized export download returned {status}")
        Path(target).write_bytes(payload)
        return Path(target)


def freeze_authentic_packages(root, verifier, seed, target, stamp, record, quiet=False):
    """Download every frozen package through the real lifecycle of this instance."""
    target = Path(target)
    if target.exists():
        raise DesignerKitError(f"freeze directory already exists, refusing to overwrite: {target}")
    packages = target / "packages"
    sidecars_root = packages / "sidecars"
    packages.mkdir(parents=True)
    (target / "licenses").mkdir(parents=True)
    client = PlatformClient(verifier.port, verifier.db_path)
    client.login("synthetic_owner", verifier.owner_password)
    members = {}
    seeds = {"web_sample_study": seed}
    seed_accounts = {}
    bindings = {}
    # The Web sample study (real GUI upload through the setup driver) is the Web seed.
    web_study = seed.get("study_id")
    web_rows = client.rows("select id, digest, package_path from core_build where study_id=? order by rowid desc limit 1",
                           [str(web_study).replace("-", "")])
    if not web_rows or not web_rows[0][2]:
        raise DesignerKitError("the Web sample study has no uploaded build to freeze")
    web_build = {"id": uuid_text(web_rows[0][0]), "digest": web_rows[0][1], "package_path": web_rows[0][2]}
    web_release = client.rows("select id, approved, config from core_release where study_id=? order by rowid desc limit 1",
                              [str(web_study).replace("-", "")])
    if not web_release or not web_release[0][1]:
        raise DesignerKitError("the Web sample study has no approved release to freeze")
    web_release_id = uuid_text(web_release[0][0])

    for platform, source in PLATFORM_SOURCES.items():
        if platform == "web":
            study_id, build, release = str(web_study), web_build, {"release_id": web_release_id}
        else:
            study_id = client.create_study(f"03F 设计者示例 · {PLATFORM_LABELS[platform]}")
            client.configure(study_id, source["mode"])
            client.roster(study_id, source["mode"])
            build = client.upload_native(study_id, source["descriptor"], source["archive"])
            if build["descriptor"].get("program_sha256") != sha256_file(source["archive"]):
                raise DesignerKitError(f"{platform}: registered descriptor does not describe the uploaded archive")
            release = client.approve(study_id, build["id"], native=True)
            client.open_recruitment(study_id)
        label = f"{platform}-{study_id}"
        package = packages / f"{label}.zip"
        if platform == "web":
            stored = verifier.data / "packages" / web_build["package_path"]
            if not stored.is_file():
                raise DesignerKitError(f"the platform-stored Web archive is missing: {stored}")
            shutil.copy2(stored, package)
            digest = sha256_file(package)
            if digest != web_build["digest"]:
                raise DesignerKitError("the frozen Web archive does not match the build digest")
            config_bytes = client.export_config(web_release_id)
            config_path = packages / f"{label}.connection.json"
            config_path.write_bytes(config_bytes)
            sidecars = {"connection.json": {"sha256": sha256_file(config_path), "size": config_path.stat().st_size}}
            manifest = {"platform": "godot_web", "study_id": study_id, "release_id": web_release_id,
                        "build_id": web_build["id"]}
            required = required_members("web")
        else:
            info = client.download_artifact(release["release_id"], package)
            if info["sha256"] != release["digest"] or info["size"] != release["size"]:
                raise DesignerKitError(f"{platform}: downloaded artifact does not match the database record")
            if info["header"] != release["digest"]:
                raise DesignerKitError(f"{platform}: download header digest does not match the record")
            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read("artifact_manifest.json"))
            if manifest.get("platform") not in ("macos_arm64", "windows_x64"):
                raise DesignerKitError(f"{platform}: package manifest platform {manifest.get('platform')!r}")
            if manifest.get("artifact_format_version") != "gep-artifact/v1":
                raise DesignerKitError(f"{platform}: package is not a complete artifact")
            if manifest.get("program_sha256") != sha256_file(source["archive"]):
                raise DesignerKitError(f"{platform}: package was frozen from a different program archive")
            sidecars = {}
            for member in ("artifact_manifest.json", manifest["config_member"], "LICENSE", "THIRD_PARTY_NOTICES.txt"):
                sidecar = sidecars_root / platform / PurePosixPath(member)
                sidecars[member] = client.download_sidecar(release["release_id"], member, sidecar)
                if sidecars[member]["header"] != sidecars[member]["sha256"]:
                    raise DesignerKitError(f"{platform}: sidecar {member} header digest mismatch")
            with zipfile.ZipFile(package) as archive:
                bundled = archive.read("artifact_manifest.json")
            if bundled != (sidecars_root / platform / "artifact_manifest.json").read_bytes():
                raise DesignerKitError(f"{platform}: packaged manifest differs from the platform sidecar")
            required = required_members(platform, manifest["config_member"])
        secret_value = (verifier.data / "secret").read_text(encoding="utf-8").strip()
        findings = archive_findings(package, secrets_values=[verifier.owner_password, secret_value, ROSTER_PASSWORD])
        if findings:
            raise DesignerKitError(f"{platform}: frozen package member scan failed: {findings[:3]}")
        with zipfile.ZipFile(package) as archive:
            names = {info.filename for info in archive.infolist() if not info.filename.endswith("/")}
        missing = [name for name in required if name not in names]
        if missing:
            raise DesignerKitError(f"{platform}: frozen package is missing required members: {missing}")
        config_member = "connection.json" if platform == "web" else manifest["config_member"]
        if platform == "web":
            config_document = json.loads(config_bytes)
        else:
            with zipfile.ZipFile(package) as archive:
                config_document = json.loads(archive.read(config_member))
        if (config_document.get("instance_id") != verifier.instance_id
                or config_document.get("study_id") != study_id
                or config_document.get("release_id") != (web_release_id if platform == "web" else release["release_id"])):
            raise DesignerKitError(f"{platform}: frozen configuration is not bound to this instance/study/release")
        if config_document.get("mode") != source["mode"]:
            raise DesignerKitError(f"{platform}: frozen configuration mode is not the study mode")
        if {"password", "participants", "roster", "token", "secret"} & set(config_document):
            raise DesignerKitError(f"{platform}: frozen configuration carries credential-like fields")
        members[package.relative_to(target).as_posix()] = {
            "sha256": sha256_file(package), "size": package.stat().st_size,
            "source": "platform authorized download", "platform": platform,
            "study_id": study_id,
            "release_id": web_release_id if platform == "web" else release["release_id"],
            "mode": source["mode"], "config_member": config_member,
            "required_members": list(required)}
        for member, info in sidecars.items():
            path = (packages / f"{label}.connection.json") if (platform == "web" and member == "connection.json") \
                else sidecars_root / platform / PurePosixPath(member)
            members[path.relative_to(target).as_posix()] = {
                "sha256": info["sha256"], "size": info["size"],
                "source": f"platform sidecar: {member}", "platform": platform}
        bindings[platform] = {"study_id": study_id, "mode": source["mode"], "config_member": config_member,
                              "release_id": web_release_id if platform == "web" else release["release_id"],
                              "build_id": web_build["id"] if platform == "web" else build["id"],
                              "package": package.relative_to(target).as_posix(),
                              "package_sha256": sha256_file(package)}
        if platform != "web":
            seeds[f"{platform}_study"] = {"study_id": study_id, "mode": source["mode"],
                                          "release_id": release["release_id"], "roster_code": ROSTER_ID}
            seed_accounts[platform] = {"study_id": study_id, "mode": source["mode"],
                                       "code": ROSTER_ID,
                                       "password": ROSTER_PASSWORD if source["mode"] == "password" else None}
        record(True, f"{platform}：真实授权下载的完整包/程序归档已冻结（未重新打包）",
               {"package": package.name, "mode": source["mode"]})
    for label, source in LICENSES:
        if source.is_file():
            destination = target / "licenses" / label
            shutil.copy2(source, destination)
            members[destination.relative_to(target).as_posix()] = {
                "sha256": sha256_file(destination), "size": destination.stat().st_size,
                "source": str(source.relative_to(ROOT))}
    return target, members, bindings, seeds, seed_accounts


# ----------------------------------------------------------------- documents
def write_freeze_documents(target, members, stamp, record, bindings):
    """Bilingual self-directed checklist and structured results template."""
    environment = f"local_data/phase03_20260920/designer_{stamp}"
    checklist = f"""# 03F 原设计者自主体验清单 / Designer self-directed checklist

状态 / Status: **设计者自主体验 = NOT_RUN；独立 T17 = NOT_RUN**。
本清单由工程准备生成；它描述"可以怎么体验"，不代表任何人已经体验或通过。

## 准备 / Preparation
1. 交付时**同时**需要两个目录：本冻结包目录与设计者环境目录
   `{environment}/`（两者都在本机项目根目录下，环境目录里才有 `START-HERE.command`）。
2. macOS：在项目根目录下双击 `{environment}/START-HERE.command`。
   它会启动隔离服务、确认实例身份后再打开浏览器；设计者不需要输入任何命令。
3. Windows：先由维护者在准备机执行一次
   `.venv/bin/python tools/phase03_designer_kit.py --serve --root {environment}`
   （工程侧作用域隧道，不改 DNS/防火墙/证书信任），然后在 Windows 上双击本目录的
   `OPEN-ADMIN-WINDOWS.cmd` 打开后台；同样不需要输入命令。
4. 账号与口令在 `{environment}/private/`（0600，不随包分发）。

## 冻结包 / Frozen packages（均为平台真实授权下载，未手工改写）
{chr(10).join(f"- `{name}`（{entry.get('platform')}，模式 {entry.get('mode')}）" for name, entry in sorted(members.items()) if name.startswith('packages/') and name.endswith('.zip'))}

## 体验建议（可自由调整顺序）/ Suggested walkthrough
- [ ] 用提供的 Owner 账号登录本地后台（地址以浏览器打开的页面为准，端口 >= 8040）。
- [ ] 查看示例研究的三种参与模式、名单导入（CSV，前导零与引号）、构建与发行、招募状态与当前发行。
- [ ] 从零新建一个自己的研究：配置模式 → 上传/登记构建 → 批准发行 → 设置当前发行 → 打开招募。
- [ ] 用 Web 入口真实参与一次（开始、两个试次、断网继续、恢复、完成），确认界面中英双语与提示是否清楚。
- [ ] 用 macOS 完整包真实运行一次；Windows 设计者用 Windows 完整包（需维护者先建立作用域隧道）。
- [ ] 查看状态/会话/名单查询/导出（JSONL 固定快照、CSV、metadata）与权限页面。
- [ ] 记录任何让你犹豫、需要猜测或需要维护者解释的地方——这些是最有价值的反馈。

## 边界 / Boundaries
- 只使用本目录提供的隔离实例与合成数据；不要连接旧验收库（`local_data/gep.sqlite3`、`independent_acceptance_20260912`）。
- 不修改产品边界、研究协议、评分、排除、同意、数据用途或部署配置。
- 结果只写入 `RESULTS_TEMPLATE.md` 的副本；未完成的项保持"未运行"。
"""
    template = f"""# 03F 原设计者自主体验结果模板 / Designer self-directed results template

- 体验日期 / Date:
- 体验者 / Designer:
- 使用的环境 / Environment: `designer_{stamp}`（端口 >= 8040）
- 使用的包 / Packages: web / macOS / Windows（列出实际使用的 sha256 前缀）
- 状态 / Status: **原设计者自主体验 = NOT_RUN**；**独立 T17 = NOT_RUN**

## 结构化结果 / Structured results

| 项目 | 结果（通过 / 需要帮助 / 失败 / 未运行） | 证据或原话 |
|---|---|---|
| 登录与语言切换 | 未运行 | |
| 示例研究浏览 | 未运行 | |
| 从零新建研究（模式/名单/构建/发行/当前发行/招募） | 未运行 | |
| Web 真实参与（两试次、断网、恢复、完成） | 未运行 | |
| macOS 完整包真实运行 | 未运行 | |
| Windows 完整包真实运行（WN01–WN06） | 未运行 | |
| 状态/会话/名单查询与分页 | 未运行 | |
| 三种导出（JSONL/CSV/metadata） | 未运行 | |
| 权限与账号页面 | 未运行 | |
| 主观体验（是否愿意交给他人使用） | 未运行 | |

## 需要维护者解释的步骤 / Steps that needed help
- （逐条记录；需要帮助不等于通过）

## 已知限制 / Known limitations
- 本地篡改的 PCK 不会被程序运行时自动拒绝（运行时不做包完整性校验）。
- kit/包的哈希清单只是传输完整性，不是签名。
- Windows 实机 WN01–WN06 需要真实 Windows x64 电脑；缺失时保持未运行。

## 结论 / Conclusion
- [ ] 我确认功能与体验已可交给未参与开发的人做独立 T17（勾选前请不要默认通过）。
"""
    (target / "CHECKLIST.zh-CN.md").write_text(checklist, encoding="utf-8")
    (target / "RESULTS_TEMPLATE.md").write_text(template, encoding="utf-8")
    members["CHECKLIST.zh-CN.md"] = {"sha256": sha256_file(target / "CHECKLIST.zh-CN.md"),
                                     "size": (target / "CHECKLIST.zh-CN.md").stat().st_size, "source": "generated"}
    members["RESULTS_TEMPLATE.md"] = {"sha256": sha256_file(target / "RESULTS_TEMPLATE.md"),
                                      "size": (target / "RESULTS_TEMPLATE.md").stat().st_size, "source": "generated"}
    record("NOT_RUN" in checklist and "NOT_RUN" in template, "清单与模板明确标注人工状态 NOT_RUN")
    record(environment in checklist, "清单引用真实存在的设计者环境相对路径", environment)


def write_freeze_manifest(target, members, record, stamp, bindings, seeds, environment, build=None):
    (target / "SHA256SUMS.txt").write_text(
        "".join(f"{entry['sha256']}  {name}\n" for name, entry in sorted(members.items())), encoding="utf-8")
    readme = ("# 03F 冻结包 / Frozen packages\n\n"
              "本目录由 `tools/phase03_designer_kit.py --prepare` 生成：每个包都是本实例平台上真实授权下载的产物，\n"
              "不重新打包、不手工改写配置。哈希清单是传输完整性检查，不是签名。\n\n"
              "## 包说明 / What each package is\n\n"
              "- `web-*.zip`：平台实际保存的已上传 Web 构建字节（与构建摘要一致），附 `*.connection.json` 导出。\n"
              "- `macos-arm64-*.zip`：macOS arm64 完整包（含 connection.json、artifact_manifest.json、许可证与第三方声明）。\n"
              "- `windows-x64-*.zip`：Windows x64 完整包（同上）；实机运行需要维护者建立作用域隧道。\n"
              "- `packages/sidecars/`：平台 sidecar 原始字节（清单/配置/许可证/声明）。\n"
              "- `licenses/`：项目许可证、godot-sqlite 上游许可与冻结的引擎声明来源。\n"
              "- `OPEN-ADMIN-WINDOWS.cmd`：Windows 上双击即可打开后台（无命令输入、不改 DNS/防火墙）。\n\n"
              "状态：设计者自主体验 = NOT_RUN；独立 T17 = NOT_RUN。\n")
    (target / "README.md").write_text(readme, encoding="utf-8")
    members["README.md"] = {"sha256": sha256_file(target / "README.md"),
                            "size": (target / "README.md").stat().st_size, "source": "generated"}
    port = environment.get("port")
    (target / "OPEN-ADMIN-WINDOWS.cmd").write_text(
        "@echo off\r\n"
        "rem 设计者无需输入命令：本文件只打开由工程侧作用域隧道暴露的后台地址。\r\n"
        "rem 不改 DNS、防火墙或证书信任；若未打开，请让维护者先运行 --serve。\r\n"
        f"start \"\" http://localhost:{port}/login\r\n",
        encoding="utf-8")
    members["OPEN-ADMIN-WINDOWS.cmd"] = {"sha256": sha256_file(target / "OPEN-ADMIN-WINDOWS.cmd"),
                                         "size": (target / "OPEN-ADMIN-WINDOWS.cmd").stat().st_size,
                                         "source": "generated"}
    (target / "SHA256SUMS.txt").write_text(
        "".join(f"{entry['sha256']}  {name}\n" for name, entry in sorted(members.items())), encoding="utf-8")
    manifest = {"format": "gep-phase03-designer-kit/v2", "stamp": stamp,
                "environment": environment.get("path"), "port": port,
                "instance_id": environment.get("instance_id"),
                "packages": members, "bindings": bindings, "seed_objects": seeds,
                "build": build or {},
                "tunnel": environment.get("tunnel"),
                "human_status": HUMAN_STATUS,
                "notes": ["本工具只准备环境与包；不产生人工验收结论。",
                          "每个包都来自本实例的真实上传/批准/授权下载，未重新打包。",
                          "示例研究是明确标注的种子对象，设计者仍可从零新建。",
                          "哈希清单只是传输完整性检查，不是签名。",
                          "Windows 原生实机运行仍需真实 Windows 电脑与维护者建立的作用域隧道。"]}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    record(True, "冻结包哈希清单与说明已写入", display_path(target))
    return manifest


# ------------------------------------------------------------------ environment
def guard_environment_root(root):
    """Refuse reserved/protected/existing targets before any write."""
    root = Path(root).resolve()
    for reserved in RESERVED_ROOTS:
        if root == Path(reserved).resolve():
            raise DesignerKitError(f"refusing reserved root: {root}")
    if root.exists():
        raise DesignerKitError(f"designer environment already exists, refusing to overwrite: {root}")
    if root.parent != PHASE_ROOT.resolve() and PHASE_ROOT.resolve() not in root.parents:
        raise DesignerKitError(f"designer environment must live under {PHASE_ROOT}: {root}")
    return root


def last_json(text):
    """The last JSON object printed on one line, or None."""
    for line in reversed((text or "").splitlines()):
        try:
            document = json.loads(line)
        except ValueError:
            continue
        if isinstance(document, dict):
            return document
    return None


def run_generated_entry(entry, timeout=180):
    """Execute one generated double-click entry exactly as the designer would."""
    entry = Path(entry)
    if not entry.is_file():
        return {"path": str(entry), "exit": None, "error": "entry missing", "payload": None}
    try:
        result = subprocess.run([str(entry)], cwd=str(entry.parent), capture_output=True, text=True,
                                timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as problem:
        return {"path": str(entry), "exit": None, "error": repr(problem), "payload": None}
    return {"path": str(entry), "exit": result.returncode, "error": None,
            "payload": last_json(result.stdout),
            "stdout": (result.stdout or "")[-400:], "stderr": (result.stderr or "")[-400:]}


def entry_ready(result, expected_url):
    """A generated entry is ready only when it really opened the expected instance."""
    payload = result.get("payload") or {}
    return bool(result.get("exit") == 0 and not result.get("error")
                and not payload.get("refused")
                and payload.get("opened") == expected_url
                and (payload.get("status") or {}).get("ready") is True)


def entry_detail(result):
    payload = result.get("payload") or {}
    return {"exit": result.get("exit"), "error": result.get("error"),
            "opened": payload.get("opened"), "refused": payload.get("refused"),
            "reason": payload.get("reason"), "ready": (payload.get("status") or {}).get("ready"),
            "stderr": result.get("stderr")}


def run_node_driver(driver, job, log_path, timeout):
    """Run one Node driver with the credential-bearing job file kept at 0600."""
    job_path = atomic_write_private(job["path"], json.dumps(job["document"]), mode=0o600)
    try:
        with open(log_path, "w") as stream:
            result = subprocess.run(["node", str(driver), str(job_path)], cwd=ROOT,
                                    stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        payload = None
        for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
        return result.returncode, payload
    finally:
        try:
            job_path.unlink()
        except OSError:
            pass


def create_designer_environment(stamp, record, quiet=False):
    """Fresh empty synthetic environment with a guarded initializer."""
    import phase03_verify_shell as shell_verify

    root = guard_environment_root(PHASE_ROOT / f"designer_{stamp}")
    root.mkdir(parents=True)
    (root / "private").mkdir()
    (root / "evidence").mkdir()
    verifier = shell_verify.Verify(root / "instance")
    verifier.init_instance()
    verifier.start_server()
    try:
        job = {"admin_url": f"http://admin.localhost:{verifier.port}",
               "experiment_url": f"http://experiment.localhost:{verifier.port}",
               "credentials": {"username": "synthetic_owner", "password": verifier.owner_password},
               "web_zip": str(ROOT / "build" / "synthetic_web.zip"),
               "connection_out": str(root / "connection.json"),
               "study_url_file": str(root / "study_url.txt"),
               "run_url_file": str(root / "run_url.txt")}
        code, payload = run_node_driver(SETUP_DRIVER,
                                        {"path": root / "evidence" / "setup_job.json", "document": job},
                                        root / "evidence" / "setup.log", 900)
        record(code == 0 and bool(payload and payload.get("ok")),
               "示例研究（真实 Web 发行）已建立", (payload or {}).get("error"))
        if code != 0 or not payload or not payload.get("ok"):
            raise DesignerKitError("sample study setup failed; see evidence/setup.log")
        return root, verifier, payload["evidence"]
    finally:
        verifier.stop_server()


SERVICE_HELPER = '''#!/usr/bin/env python3
"""Designer service helper: start/stop/status/open-browser for one frozen instance.

Generated by tools/phase03_designer_kit.py. It never executes text from
service.env (it only parses KEY='value' lines), never touches a foreign process,
and refuses an occupied port or a reused PID instead of opening the wrong
instance.
"""
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / "private" / "service.env"
PID_FILE = HERE / "service.pid"
LOG_FILE = HERE / "evidence" / "gunicorn.log"
ROOT = Path(__file__).resolve().parents[3]


def parse_env(path):
    document = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, raw = stripped.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            continue
        value = raw.strip()
        if value.startswith("'"):
            if not value.endswith("'"):
                continue
            value = value[1:-1].replace("'\\\\''", "'")
        elif value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        document[key.strip()] = value
    return document


def gunicorn_command(env):
    return [str(ROOT / ".venv" / "bin" / "gunicorn"), "gep.wsgi:application",
            "--bind", "127.0.0.1:" + env["GEP_DESIGNER_PORT"], "--workers", "1", "--threads", "4",
            "--access-logfile", "-"]


def server_env(env):
    return {**os.environ, "GEP_DATA_DIR": env["GEP_DATA_DIR"], "GEP_SECRET_KEY": env["GEP_SECRET_KEY"],
            "GEP_EXPECTED_INSTANCE": env["GEP_EXPECTED_INSTANCE"], "GEP_PUBLIC_API": env["GEP_PUBLIC_API"],
            "PYTHONPATH": str(ROOT / "server"), "DJANGO_SETTINGS_MODULE": "gep.settings"}


def port_answers(port, timeout=1.5):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def process_command(pid):
    result = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return (result.stdout or "").strip()


def command_matches(recorded, live):
    """The recorded argv tokens must appear in the live command line in order."""
    import shlex
    try:
        wanted = shlex.split(recorded)
        have = shlex.split(live)
    except ValueError:
        return False
    if not wanted:
        return False
    position = 0
    for token in have:
        if position < len(wanted) and token == wanted[position]:
            position += 1
    return position == len(wanted)


def recorded_pid(env):
    if not PID_FILE.is_file():
        return None
    try:
        document = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"pid": None, "outcome": "refused", "reason": "PID file is not readable JSON; refusing to reuse it"}
    if not isinstance(document, dict):
        return {"pid": None, "outcome": "refused", "reason": "PID file is not an object; refusing to reuse it"}
    try:
        pid = int(document.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0:
        return {"pid": None, "outcome": "refused", "reason": "PID record has no valid pid; refusing to reuse it"}
    recorded = str(document.get("command") or "").strip()
    if not recorded:
        return {"pid": pid, "outcome": "refused",
                "reason": "PID record has no recorded command line; refusing to reuse it"}
    command = process_command(pid)
    if not command:
        return None
    if not command_matches(recorded, command):
        return {"pid": pid, "outcome": "refused", "reason": "command line changed"}
    return {"pid": pid, "outcome": "verified", "command": command}


def instance_identity(env):
    expected = env.get("GEP_EXPECTED_INSTANCE")
    instance_file = HERE / "instance" / "data" / "instance"
    frozen = instance_file.read_text(encoding="utf-8").strip() if instance_file.is_file() else None
    database = HERE / "instance" / "data" / "gep.sqlite3"
    stored = None
    if database.is_file():
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
        try:
            row = connection.execute("select instance_id from core_instance").fetchone()
            stored = str(row[0]) if row else None
        finally:
            connection.close()
    if stored and len(stored) == 32:
        stored = f"{stored[0:8]}-{stored[8:12]}-{stored[12:16]}-{stored[16:20]}-{stored[20:]}"
    return {"expected": expected, "file": frozen, "database": stored,
            "matches": bool(expected) and expected == frozen == stored}


def status(env):
    port = int(env["GEP_DESIGNER_PORT"])
    record = recorded_pid(env)
    alive = bool(record and record.get("outcome") == "verified")
    return {"port": port, "pid": record.get("pid") if record else None,
            "pid_verified": alive, "pid_detail": record,
            "port_answers": port_answers(port), "identity": instance_identity(env),
            "ready": bool(alive and port_answers(port) and instance_identity(env)["matches"])}


def terminate_own(pid, timeout=15):
    """Stop only the process this helper started (never a foreign PID)."""
    if not process_command(pid):
        return False
    os.kill(int(pid), 15)
    deadline = time.time() + timeout
    while time.time() < deadline and process_command(int(pid)):
        time.sleep(0.3)
    if process_command(int(pid)):
        os.kill(int(pid), 9)
    return True


def start(env):
    port = int(env["GEP_DESIGNER_PORT"])
    current = recorded_pid(env)
    if current and current.get("outcome") == "refused":
        return {"started": False, "refused": True, "status": current,
                "reason": f"recorded PID refused ({current.get('reason')}); refusing to reuse it"}
    if current and current.get("outcome") == "verified":
        state = status(env)
        if state["ready"]:
            return {"started": False, "already_running": True, "status": state}
        return {"started": False, "refused": True, "status": state,
                "reason": "recorded service is alive but is not the frozen ready instance; refusing to open it"}
    if port_answers(port):
        return {"started": False, "refused": True,
                "reason": f"port {port} is already served by another process; refusing to open the wrong instance"}
    if PID_FILE.exists():
        PID_FILE.unlink()
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    command = gunicorn_command(env)
    with open(LOG_FILE, "a") as stream:
        process = subprocess.Popen(command, cwd=str(ROOT), env=server_env(env),
                                   stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    PID_FILE.write_text(json.dumps({"pid": process.pid, "command": " ".join(command)}), encoding="utf-8")
    deadline = time.time() + 60
    while time.time() < deadline:
        if port_answers(port):
            break
        if process.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            return {"started": False, "refused": True, "reason": "service exited early; see evidence/gunicorn.log"}
        time.sleep(0.3)
    else:
        terminate_own(process.pid)
        PID_FILE.unlink(missing_ok=True)
        return {"started": False, "refused": True, "reason": "service did not answer within 60s"}
    identity = instance_identity(env)
    if not identity["matches"]:
        terminate_own(process.pid)
        PID_FILE.unlink(missing_ok=True)
        return {"started": False, "refused": True,
                "reason": "served instance identity does not match the frozen instance"}
    return {"started": True, "status": status(env)}


def stop(env):
    record = recorded_pid(env)
    if not record:
        if PID_FILE.exists():
            PID_FILE.unlink()
        return {"stopped": False, "reason": "no live recorded service"}
    if record.get("outcome") != "verified":
        return {"stopped": False, "refused": True, "reason": record.get("reason")}
    terminate_own(record["pid"])
    PID_FILE.unlink(missing_ok=True)
    return {"stopped": True, "pid": record["pid"]}


def open_browser(env):
    url = env["GEP_DESIGNER_ADMIN"] + "/login"
    started = start(env)
    if started.get("refused") or started.get("error"):
        return {"opened": None, "refused": True,
                "reason": started.get("reason") or started.get("error"), "start": started}
    state = status(env)
    if not state["ready"]:
        return {"opened": None, "refused": True, "status": state,
                "reason": "service is not a ready frozen instance; refusing to open the browser on a wrong instance"}
    opened = subprocess.run(["open", "-a", "Google Chrome", url], check=False)
    evidence = {"url": url, "open_exit": opened.returncode, "opened": opened.returncode == 0,
                "already_running": bool(started.get("already_running")), "status": state}
    try:
        (HERE / "evidence").mkdir(parents=True, exist_ok=True)
        (HERE / "evidence" / "open-browser.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    if opened.returncode != 0:
        return {"opened": url, "opened_ok": False, "refused": True, "status": state,
                "reason": "Google Chrome could not be opened by this machine"}
    return {"opened": url, "opened_ok": True, "status": state}


def main(argv):
    env = parse_env(ENV_FILE)
    command = argv[1] if len(argv) > 1 else "status"
    if command == "start":
        result = start(env)
    elif command == "stop":
        result = stop(env)
    elif command == "status":
        result = status(env)
    elif command == "open-browser":
        result = open_browser(env)
    else:
        result = {"error": f"unknown command {command!r}"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not result.get("refused") and not result.get("error") else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


def write_environment_files(root, verifier, seed, freeze, stamp, record, quiet=False, seed_accounts=None):
    """Credentials (0600), service env (0600), launchers and the local handoff."""
    private = root / "private"
    atomic_write_private(private / "owner_password.txt", verifier.owner_password + "\n")
    atomic_write_private(private / "owner_credentials.json", json.dumps(
        {"username": "synthetic_owner", "password": verifier.owner_password,
         "note": "Synthetic local designer account; never distributed, never committed."},
        ensure_ascii=False))
    atomic_write_private(private / "designer_seed_accounts.json", json.dumps(
        {"format": "gep-designer-seed-accounts/v1", "synthetic_only": True,
         "accounts": seed_accounts or {},
         "note": "Synthetic roster accounts of the labelled sample studies; private, never distributed."},
        ensure_ascii=False, indent=2))
    secret = (verifier.data / "secret").read_text(encoding="utf-8").strip()
    instance_id = (verifier.data / "instance").read_text(encoding="utf-8").strip()
    service_env = "\n".join([
        f"GEP_DATA_DIR={sh_quote(verifier.data)}",
        f"GEP_SECRET_KEY={sh_quote(secret)}",
        f"GEP_EXPECTED_INSTANCE={sh_quote(instance_id)}",
        f"GEP_PUBLIC_API={sh_quote(f'http://experiment.localhost:{verifier.port}')}",
        f"GEP_DESIGNER_PORT={sh_quote(verifier.port)}",
        f"GEP_DESIGNER_ADMIN={sh_quote(f'http://admin.localhost:{verifier.port}')}",
        ""])
    atomic_write_private(private / "service.env", service_env)
    atomic_write_private(root / "service.py", SERVICE_HELPER)
    os.chmod(root / "service.py", 0o755)
    record(all(stat.S_IMODE(path.stat().st_mode) == 0o600
               for path in (private / "owner_password.txt", private / "owner_credentials.json",
                            private / "service.env", private / "designer_seed_accounts.json")),
           "私有口令/服务环境/种子账号文件权限为 0600", str(private.relative_to(ROOT)))

    start = root / "START-HERE.command"
    start.write_text("\n".join([
        "#!/bin/sh",
        "# 双击即可：启动隔离服务并打开正确的本地后台（无需输入命令）。",
        "set -e",
        f"cd {sh_quote(ROOT)}",
        'HERE="$(cd "$(dirname "$0")" && pwd)"',
        f"exec {sh_quote(VENV_PYTHON)} \"$HERE/service.py\" open-browser",
        "",
    ]), encoding="utf-8")
    start.chmod(0o755)
    browser = root / "open-browser.command"
    browser.write_text("\n".join([
        "#!/bin/sh",
        "# 双击即可：确保隔离服务就绪后再打开正确的本地后台（无需输入命令）。",
        "set -e",
        f"cd {sh_quote(ROOT)}",
        'HERE="$(cd "$(dirname "$0")" && pwd)"',
        f"exec {sh_quote(VENV_PYTHON)} \"$HERE/service.py\" open-browser",
        "",
    ]), encoding="utf-8")
    browser.chmod(0o755)
    record(True, "双击启动脚本与浏览器助手已生成（无需人工输入命令）",
           {"start": str(start.relative_to(ROOT)), "port": verifier.port})

    handoff = root / "HANDOFF.md"
    handoff.write_text("\n".join([
        "# 03F 设计者环境交接（本地，不提交）/ Local handoff",
        "",
        f"- 交付给设计者的两个目录（必须一起交付）：本环境目录 `{root.relative_to(ROOT)}/` 与冻结包目录 "
        f"`build/phase03_20260920/{stamp}/`（冻结包目录没有 `START-HERE.command`）。",
        f"- 冻结包：`build/phase03_20260920/{stamp}/`（平台真实授权下载的 Web / macOS / Windows + sidecar + 哈希清单）",
        f"- 隔离实例数据目录：`{verifier.data.relative_to(ROOT)}`（新库，不写旧验收库）",
        f"- 私有账号：`{private.relative_to(ROOT)}/owner_credentials.json` 与 "
        f"`{private.relative_to(ROOT)}/owner_password.txt`（均 0600）",
        f"- 示例研究的名单账号（合成，0600，不随包分发）：`{private.relative_to(ROOT)}/designer_seed_accounts.json`",
        f"- 服务启动（macOS）：双击 `{start.relative_to(ROOT)}`；端口 {verifier.port}（>= 8040）",
        f"- Windows 入口：先由维护者执行 `tools/phase03_designer_kit.py --serve --root {root.relative_to(ROOT)}`，",
        f"  再在 Windows 双击 `build/phase03_20260920/{stamp}/OPEN-ADMIN-WINDOWS.cmd`；不改 DNS/防火墙/证书信任。",
        f"- 示例研究（可自行新建）：`{seed.get('study_url')}`（study_id {seed.get('study_id')}）",
        f"- 连接配置：`connection.json`；Web 入口：`{seed.get('run_url')}`",
        "- 待上传队列查看：登录后进入研究的会话模块查看状态；本地原生队列在程序数据目录的 queue.sqlite。",
        "- 人工状态：原设计者自主体验 = NOT_RUN；独立 T17 = NOT_RUN。",
        "",
        "## 维护者核对顺序 / Maintainer order",
        "1. `.venv/bin/python tools/phase03_designer_kit.py --verify --root <本目录>`（冻结包/成员扫描/真实 "
        "START-HERE 与 open-browser 入口/Chrome 登录）。",
        "2. 把本环境目录与 `build/phase03_20260920/<stamp>/` **一起**交给设计者；设计者双击 "
        "`<本目录>/START-HERE.command` 自行体验。",
        "3. Windows 设计者：先 `--serve`，再双击冻结目录的 `OPEN-ADMIN-WINDOWS.cmd`。",
        "4. 设计者结果只填 RESULTS_TEMPLATE.md 副本；维护者不代操作、不逐步指导。",
        "5. 设计者确认可交付后，才邀请未参与开发的人执行独立 T17。",
        "",
    ]), encoding="utf-8")
    return handoff


# Path-like tokens found in the generated documents. Every one of them must
# resolve to a real file/directory (or to a real frozen package member).
DOCUMENT_PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_./-])("
    r"(?:local_data|build|tools|tests|examples|docs|server|deploy)/[A-Za-z0-9_./\u4e00-\u9fff-]+"
    r"|[A-Za-z0-9_-]+\.(?:command|cmd|py|json|md|txt|zip)"
    r")")
ENTRY_EXEC = "exec "


def entry_point_targets(entry, root):
    """The interpreter/cwd targets a generated double-click entry really uses."""
    import shlex
    problems = []
    entry = Path(entry)
    if not entry.is_file():
        return [f"{entry.name}: entry missing"]
    for number, line in enumerate(entry.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("cd "):
            try:
                target = shlex.split(stripped[3:])[0]
            except (ValueError, IndexError):
                problems.append(f"{entry.name}:{number}: unreadable cd target")
                continue
            if not Path(target).is_dir():
                problems.append(f"{entry.name}:{number}: cd target is not a directory: {target}")
        elif stripped.startswith(ENTRY_EXEC):
            try:
                parts = shlex.split(stripped[len(ENTRY_EXEC):])
            except ValueError:
                problems.append(f"{entry.name}:{number}: unreadable exec line")
                continue
            if len(parts) < 2 or parts[1] != "$HERE/service.py":
                problems.append(f"{entry.name}:{number}: exec does not run $HERE/service.py: {parts[:2]}")
                continue
            interpreter = Path(parts[0])
            if not interpreter.is_file():
                problems.append(f"{entry.name}:{number}: interpreter missing: {parts[0]}")
            elif not os.access(interpreter, os.X_OK):
                problems.append(f"{entry.name}:{number}: interpreter not executable: {parts[0]}")
            if not (Path(root) / "service.py").is_file():
                problems.append(f"{entry.name}:{number}: service.py missing next to the entry")
    return problems


def document_reference_problems(texts, root, freeze, manifest):
    """Resolve every path-like token the generated documents reference."""
    root = Path(root)
    freeze = Path(freeze)
    members = set((manifest.get("packages") or {}).keys())
    problems = []
    for origin, text in texts:
        for match in DOCUMENT_PATH_TOKEN.finditer(text):
            token = match.group(1).rstrip("/")
            if not token or "<" in token or ">" in token:
                continue
            parts = PurePosixPath(token).parts
            if ".." in parts:
                problems.append(f"{origin}: parent-relative path is not a real handover path: {token}")
                continue
            candidates = []
            if token.startswith("packages/"):
                candidates = [freeze / token, root / token]
            elif token.split("/")[0] in ("local_data", "build", "tools", "tests", "examples",
                                         "docs", "server", "deploy"):
                candidates = [ROOT / token]
                # An environment token names this environment: resolve it against
                # the environment root even when it lives outside the project root.
                if token.startswith("local_data/phase03_20260920/") and root.name in parts:
                    index = parts.index(root.name)
                    if index + 1 < len(parts):
                        candidates.append(root.joinpath(*parts[index + 1:]))
            else:
                candidates = [freeze / token, root / token]
            if any(candidate.exists() for candidate in candidates):
                continue
            if token in members:
                continue
            if package_member(token, freeze, members):
                continue
            problems.append(f"{origin}: referenced path does not exist: {token}")
    return problems


def package_member(token, freeze, members):
    """True when a bare member name really exists inside one frozen package."""
    if "/" in token:
        return False
    for name in members:
        if not str(name).endswith(".zip"):
            continue
        try:
            with zipfile.ZipFile(Path(freeze) / name) as archive:
                if any(PurePosixPath(info.filename).name == token for info in archive.infolist()):
                    return True
        except (OSError, zipfile.BadZipFile):
            continue
    return False


def generated_reference_problems(root, freeze, manifest):
    """Every reference the generated documents/entries need, without raising.

    Returns the missing reference names, the double-click entry problems and the
    unresolved document paths. ``--verify`` uses this as part of its pre-launch
    gate, so a broken reference can never reach a service start or a program run.
    """
    root = Path(root).resolve()
    freeze = Path(freeze).resolve()
    references = {
        "START-HERE.command": root / "START-HERE.command",
        "open-browser.command": root / "open-browser.command",
        "service.py": root / "service.py",
        "private/service.env": root / "private" / "service.env",
        "private/owner_credentials.json": root / "private" / "owner_credentials.json",
        "private/owner_password.txt": root / "private" / "owner_password.txt",
        "private/designer_seed_accounts.json": root / "private" / "designer_seed_accounts.json",
        "instance/data/instance": root / "instance" / "data" / "instance",
        "instance/data/gep.sqlite3": root / "instance" / "data" / "gep.sqlite3",
        "freeze/manifest.json": freeze / "manifest.json",
        "freeze/SHA256SUMS.txt": freeze / "SHA256SUMS.txt",
        "freeze/CHECKLIST.zh-CN.md": freeze / "CHECKLIST.zh-CN.md",
        "freeze/RESULTS_TEMPLATE.md": freeze / "RESULTS_TEMPLATE.md",
        "freeze/README.md": freeze / "README.md",
        "freeze/OPEN-ADMIN-WINDOWS.cmd": freeze / "OPEN-ADMIN-WINDOWS.cmd",
    }
    for name, entry in (manifest.get("packages") or {}).items():
        references[f"freeze/{name}"] = freeze / name
    missing = sorted(name for name, path in references.items() if not path.is_file())
    entry_problems = []
    for name in ("START-HERE.command", "open-browser.command"):
        entry_problems.extend(entry_point_targets(root / name, root))
    texts = []
    for name in ("CHECKLIST.zh-CN.md", "RESULTS_TEMPLATE.md", "README.md"):
        candidate = freeze / name
        if candidate.is_file():
            texts.append((f"freeze/{name}", candidate.read_text(encoding="utf-8")))
    handoff = root / "HANDOFF.md"
    if handoff.is_file():
        texts.append(("HANDOFF.md", handoff.read_text(encoding="utf-8")))
    document_problems = document_reference_problems(texts, root, freeze, manifest)
    return {"missing": missing, "entries": entry_problems, "documents": document_problems,
            "references": len(references)}


def check_generated_references(root, freeze, manifest, record):
    """Every path the generated documents and entry points reference must exist."""
    problems = generated_reference_problems(root, freeze, manifest)
    record(not problems["missing"], "生成的每个引用都指向真实文件",
           problems["missing"] or {"references": problems["references"]})
    if problems["missing"]:
        raise DesignerKitError(f"generated references point at missing files: {problems['missing']}")
    record(not problems["entries"], "生成的双击入口引用真实解释器与脚本（可含空格/单引号路径）",
           problems["entries"][:5] or None)
    record(not problems["documents"], "文档内的每个路径都解析到真实目标", problems["documents"][:5] or None)
    if problems["entries"] or problems["documents"]:
        raise DesignerKitError(
            f"generated documents/entries reference missing targets: {problems['entries'] + problems['documents']}")
    return problems["references"]


def run_frozen_macos_package(root, freeze, manifest, verifier, record, quiet=False):
    """Run the frozen macOS package with only its packaged configuration.

    The package is unpacked unchanged, started with the bundled
    ``connection.json`` (no ``--config`` replacement), and its local store is
    reconciled against the bound instance database and the authorized JSONL
    export created through the real admin API.
    """
    bindings = manifest.get("bindings") or {}
    macos = bindings.get("macos-arm64")
    if not macos:
        raise DesignerKitError("the freeze has no macOS binding to run")
    package = Path(freeze) / macos["package"]
    run_root = Path(root) / "evidence" / "macos_run"
    unpacked = run_root / "unpacked"
    if run_root.exists():
        shutil.rmtree(run_root)
    unpacked.mkdir(parents=True)
    with zipfile.ZipFile(package) as archive:
        for item in archive.infolist():
            if item.filename.endswith("/"):
                continue
            destination = unpacked / item.filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(item))
            destination.chmod((item.external_attr >> 16) & 0o7777 or 0o644)
    with zipfile.ZipFile(package) as archive:
        document = json.loads(archive.read("artifact_manifest.json"))
    binary = unpacked / Path(document["entry"])
    record(binary.is_file() and os.access(binary, os.X_OK), "冻结 macOS 包解包后程序可执行",
           str(binary.relative_to(unpacked)))
    config = unpacked / PurePosixPath(document["config_member"])
    record(config.is_file(), "冻结包自带 connection.json（无需替换）", str(config.relative_to(unpacked)))
    frozen_config = json.loads(config.read_text(encoding="utf-8"))
    if frozen_config.get("instance_id") != verifier.instance_id:
        raise DesignerKitError("frozen macOS configuration does not belong to this environment")
    storage = run_root / "storage"
    storage.mkdir(parents=True)
    arguments = ["--synthetic-auto"]
    seed_accounts = {}
    accounts_file = Path(root) / "private" / "designer_seed_accounts.json"
    if accounts_file.is_file():
        seed_accounts = (json.loads(accounts_file.read_text(encoding="utf-8")).get("accounts") or {})
    account = seed_accounts.get("macos-arm64") or {}
    if macos.get("mode") and macos["mode"] != "anonymous" and account.get("code"):
        arguments.append(f"--participant-code={account['code']}")
    if macos.get("mode") == "password" and account.get("password"):
        arguments.append(f"--password={account['password']}")
    environment = {**os.environ, "GEP_SYNTHETIC_STORAGE": str(storage)}
    process = subprocess.Popen([str(binary), "--headless", "--", *arguments], cwd=str(unpacked),
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    session = None
    observed = None
    deadline = time.time() + 180
    while time.time() < deadline:
        if process.poll() is not None:
            break
        candidate = _local_session(storage)
        if candidate and candidate.get("records"):
            observed = candidate
            if candidate.get("completion") and not candidate.get("pending"):
                session = candidate
                break
        time.sleep(0.2)
    output, _ = process.communicate(timeout=180)
    (run_root / "program_output.txt").write_text(output or "", encoding="utf-8")
    record(process.returncode == 0 and "SYNTHETIC_DONE" in output,
           "冻结 macOS 程序用自带配置完成同一合成任务",
           {"exit": process.returncode, "tail": (output or "")[-300:]})
    record("--config" not in output, "未通过命令行替换连接配置")
    session = session or observed
    tombstone = _local_session(storage, kinds=("cleaned",))
    if session is None and tombstone is not None:
        # The program completed and cleaned up before the local record could be
        # captured; the tombstone keeps the session identity for reconciliation.
        session = tombstone
    if session is None:
        raise DesignerKitError("the frozen macOS run left no local session or tombstone to reconcile")
    session_id = session.get("id")
    local = [event for event in (session.get("records") or []) if isinstance(event, dict)]
    if local:
        record(True, "冻结包本地记录已捕获（完成前）", {"records": len(local)})
    else:
        record(True, "冻结包本地清理墓碑保留同一会话标识", {"session_id": session_id, "kind": session.get("kind")})
    client = PlatformClient(verifier.port, verifier.db_path)
    client.login("synthetic_owner", verifier.owner_password)
    export_path = client.export_jsonl(macos["study_id"], run_root / "authorized_export.jsonl")
    rows = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    exported = [row["record"] for row in rows
                if row.get("release_id") == macos["release_id"]
                and row.get("record", {}).get("session_id") == session_id]
    events = _db_events(verifier.db_path, session_id)
    record(bool(exported) and {event["event_id"] for event in exported} == {event["event_id"] for event in events},
           "冻结包运行的实例数据库与授权 JSONL 导出按事件 ID 一致",
           {"database": len(events), "exported": len(exported)})
    if local:
        record({event["event_id"] for event in exported} == {event["event_id"] for event in local},
               "冻结包本地记录与授权 JSONL 导出按事件 ID 一致",
               {"local": len(local), "exported": len(exported)})
        record({event.get("event_id") for event in events} == {event.get("event_id") for event in local},
               "冻结包本地记录与实例数据库按事件 ID 一致",
               {"database": len(events), "local": len(local)})
    (run_root / "summary.json").write_text(json.dumps(
        {"session_id": session_id, "local_records": len(local), "exported": len(exported),
         "database": len(events), "exit": process.returncode, "kind": session.get("kind")},
        ensure_ascii=False, indent=2))
    return {"session_id": session_id, "records": len(local), "exported": len(exported)}


def _local_session(storage, kinds=("session",)):
    path = Path(storage) / "queue.sqlite"
    if not path.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            rows = [json.loads(row[0]) for row in connection.execute("select value from sessions")]
        finally:
            connection.close()
    except (sqlite3.Error, ValueError):
        return None
    sessions = [document for document in rows if document.get("kind") in kinds]
    return sessions[0] if sessions else None


def _db_events(db_path, session_id):
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20)
    try:
        rows = connection.execute("select envelope from core_event where session_id=?",
                                  [str(session_id).replace("-", "")]).fetchall()
    finally:
        connection.close()
    return [json.loads(row[0]) for row in rows]


def prepare(stamp, record, quiet=False):
    root, verifier, seed = create_designer_environment(stamp, record, quiet=quiet)
    freeze = FREEZE_ROOT / stamp
    try:
        verifier.start_server(port=verifier.port)
        target, members, bindings, seeds, seed_accounts = freeze_authentic_packages(
            root, verifier, seed, freeze, stamp, record, quiet=quiet)
        verifier.stop_server()
    except BaseException:
        verifier.stop_server()
        raise
    environment = {"path": str(root.relative_to(ROOT)), "port": verifier.port,
                   "instance_id": verifier.instance_id,
                   "tunnel": {"listen_port": verifier.port, "target_port": verifier.port,
                              "direction": "reverse", "windows_alias": None,
                              "note": "维护者用 --serve 建立作用域反向隧道；Windows 侧监听端口与冻结端口一致，"
                                      "无需修改包内配置，也不改 DNS/防火墙/证书信任。"}}
    write_freeze_documents(target, members, stamp, record, bindings)
    manifest = write_freeze_manifest(target, members, record, stamp, bindings, seeds, environment,
                                     build=current_build_binding())
    handoff = write_environment_files(root, verifier, seed, target, stamp, record, quiet=quiet,
                                      seed_accounts=seed_accounts)
    check_generated_references(root, target, manifest, record)
    manifest["environment"] = str(root.relative_to(ROOT))
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    record(True, "设计者冻结包与环境准备完成",
           {"freeze": str(target.relative_to(ROOT)), "environment": str(root.relative_to(ROOT)),
            "seed_objects": sorted(seeds)})
    return {"freeze": target, "environment": root, "manifest": manifest, "handoff": handoff, "seed": seed}


def freeze_hash_mismatches(freeze, members=None):
    """Problems with the frozen SHA256SUMS list: format, duplicates, coverage, bytes.

    An empty list, a malformed or duplicate line, a missing file, a digest
    mismatch and (when ``members`` is given) a manifest member without a hash
    line - or a hash line without a manifest member - are all reported. A
    missing list is reported as ``SHA256SUMS.txt``.
    """
    sums_path = Path(freeze) / "SHA256SUMS.txt"
    problems = []
    if not sums_path.is_file():
        return ["SHA256SUMS.txt"]
    entries = {}
    for number, line in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            problems.append(f"line {number}: malformed hash line")
            continue
        digest, name = match.groups()
        name = name.strip()
        if not name or name.startswith("/") or ".." in PurePosixPath(name).parts:
            problems.append(f"line {number}: unsafe member name {name!r}")
            continue
        if name in entries:
            problems.append(f"line {number}: duplicate entry {name}")
            continue
        entries[name] = digest
        path = Path(freeze) / name
        if not path.is_file() or sha256_file(path) != digest:
            problems.append(name)
    if not entries and not problems:
        problems.append("SHA256SUMS.txt: empty hash list")
    if members is not None:
        expected = set(members)
        problems.extend(f"missing hash line for manifest member {name}"
                        for name in sorted(expected - set(entries)))
        problems.extend(f"hash line has no manifest member {name}"
                        for name in sorted(set(entries) - expected))
    return problems


def current_build_binding():
    """The current build/source facts a frozen kit must correspond to."""
    import phase03_windows_kit as windows_kit
    binding = {"program_source_digest": windows_kit.program_source_digest(),
               "archives": {}, "descriptors": {}}
    for platform, source in PLATFORM_SOURCES.items():
        binding["archives"][platform] = {"path": source["archive"].relative_to(ROOT).as_posix(),
                                         "sha256": sha256_file(source["archive"])}
        if source.get("descriptor"):
            document = read_json(source["descriptor"])
            binding["descriptors"][platform] = {"path": source["descriptor"].relative_to(ROOT).as_posix(),
                                                "sha256": sha256_file(source["descriptor"]),
                                                "program_sha256": document.get("program_sha256")}
    return binding


def freeze_binding_problems(manifest, current=None, freeze=None):
    """The frozen packages/programs must correspond to the *current* build and sources.

    A frozen zip whose bytes still match its own manifest is not accepted when
    the program inside it was built from an older archive/source: the recorded
    build binding, the current platform descriptors and the package's own
    ``artifact_manifest.json`` are compared with the current workspace.
    """
    current = current or current_build_binding()
    freeze = Path(freeze) if freeze else None
    recorded = manifest.get("build") or {}
    problems = []
    if not recorded:
        problems.append("冻结 manifest 未记录构建/源码绑定")
    elif recorded.get("program_source_digest") != current["program_source_digest"]:
        problems.append("冻结记录的程序源码摘要与当前工作区不一致（旧源码证据）")
    for platform, entry in (recorded.get("archives") or {}).items():
        live = (current.get("archives") or {}).get(platform)
        if not live or live.get("sha256") != entry.get("sha256"):
            problems.append(f"{platform}: 冻结记录的程序归档摘要与当前构建不一致")
    for platform, entry in (recorded.get("descriptors") or {}).items():
        live = (current.get("descriptors") or {}).get(platform)
        if (not live or live.get("sha256") != entry.get("sha256")
                or live.get("program_sha256") != entry.get("program_sha256")):
            problems.append(f"{platform}: 冻结记录的描述与当前描述不一致")
    for name, entry in (manifest.get("packages") or {}).items():
        if not name.endswith(".zip") or freeze is None:
            continue
        platform = entry.get("platform")
        live = (current.get("archives") or {}).get(platform, {}).get("sha256")
        if platform == "web":
            if live and entry.get("sha256") != live:
                problems.append(f"{name}: 冻结 Web 归档字节与当前构建不一致")
            continue
        try:
            with zipfile.ZipFile(freeze / name) as archive:
                document = json.loads(archive.read("artifact_manifest.json"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
            problems.append(f"{name}: 包内 artifact_manifest.json 不可读（{error}）")
            continue
        if live and document.get("program_sha256") != live:
            problems.append(f"{name}: 包内程序摘要与当前构建不一致（自报哈希一致也不接受陈旧构建）")
        descriptor = (current.get("descriptors") or {}).get(platform) or {}
        if descriptor.get("program_sha256") and document.get("program_sha256") != descriptor["program_sha256"]:
            problems.append(f"{name}: 包内程序摘要与当前描述记录不一致")
    return problems


def member_scan_problems(freeze, manifest, known_values):
    """Recursive member scan plus the platform-required members of every package.

    The required members are derived from the platform and from the package's
    own manifest, so deleting ``required_members`` from the freeze manifest can
    never bypass the platform-required manifest/config members.
    """
    freeze = Path(freeze)
    problems = []
    for name, entry in sorted((manifest.get("packages") or {}).items()):
        if not name.endswith(".zip"):
            continue
        package = freeze / name
        problems.extend(f"{name}: {problem}" for problem in archive_findings(package, secrets_values=known_values))
        platform = entry.get("platform")
        if platform not in ("web", "macos-arm64", "windows-x64"):
            problems.append(f"{name}: 未知平台 {platform!r}")
            continue
        try:
            with zipfile.ZipFile(package) as archive:
                members = {info.filename for info in archive.infolist() if not info.filename.endswith("/")}
        except (OSError, zipfile.BadZipFile) as error:
            problems.append(f"{name}: 归档不可读（{error}）")
            continue
        required = {member for member in (entry.get("required_members") or []) if member}
        if platform == "web":
            required |= set(required_members("web"))
        else:
            try:
                with zipfile.ZipFile(package) as archive:
                    document = json.loads(archive.read("artifact_manifest.json"))
            except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
                problems.append(f"{name}: 包内清单不可读（{error}）")
                continue
            config_member = document.get("config_member")
            if not config_member:
                problems.append(f"{name}: 包内清单缺少 config_member")
            required |= set(required_members(platform, config_member))
        missing = sorted(member for member in required if member not in members)
        problems.extend(f"{name}: missing required member {member}" for member in missing)
    return problems


def verify(root, record, quiet=False):
    """Readiness: a launch-blocking preflight, then the real entries/Chrome/macOS run.

    All manifest/hash/member/config/build-binding/reference checks are collected
    *before* anything is started. Any failure or parsing exception writes an
    explicit ``NOT_READY`` readiness document and returns immediately: no service
    is stopped or started, no browser is opened and no program is run. Only a
    clean preflight may proceed to the generated double-click entries, the real
    Chrome login and the frozen macOS package run.
    """
    root = Path(root).resolve()
    freeze = FREEZE_ROOT / root.name.replace("designer_", "")
    checks = []
    launched = {"done": False}

    def display(candidate):
        try:
            return Path(candidate).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return str(candidate)

    def check(ok, label, detail=None):
        checks.append({"ok": bool(ok), "label": label, "detail": detail})
        record(ok, label, detail)
        return bool(ok)

    def finish(status=None):
        failed = len([entry for entry in checks if not entry["ok"]])
        readiness = {"format": "gep-phase03-designer-readiness/v2",
                     "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "environment": display(root), "freeze": display(freeze),
                     "launch_performed": launched["done"],
                     "checks": checks, "checks_failed": failed,
                     "human_status": HUMAN_STATUS,
                     "status": status or ("READY" if not failed and launched["done"] else "NOT_READY")}
        evidence = root / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "readiness.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2))
        return readiness

    # ------------------------------------------------------- pre-launch gate
    # Nothing below this gate (service stop/start, browser, program) may run
    # when any preflight check fails or throws; the failure evidence is still
    # written so a broken kit is inspectable instead of silently executed.
    try:
        manifest, manifest_problem = load_freeze_manifest(freeze)
        check(manifest is not None, "冻结 manifest 存在且可解析", manifest_problem)
        if manifest is None:
            manifest = {}
        sums_path = freeze / "SHA256SUMS.txt"
        check(sums_path.is_file(), "冻结包哈希清单存在", str(freeze))
        mismatched = freeze_hash_mismatches(freeze, members=manifest.get("packages") or {})
        check(not mismatched, "冻结包哈希清单格式/覆盖/逐字节一致", mismatched[:5] or None)
        findings = []
        for path in walk_files(freeze):
            hits = scan_secrets(path)
            if hits:
                findings.append(path.relative_to(freeze).as_posix())
        check(not findings, "冻结包不含凭据样式内容", findings[:5] or None)
        known_values = [ROSTER_PASSWORD]
        owner_password = root / "private" / "owner_password.txt"
        if owner_password.is_file():
            known_values.append(owner_password.read_text(encoding="utf-8").strip())
        seed_accounts = root / "private" / "designer_seed_accounts.json"
        if seed_accounts.is_file():
            for account in (json.loads(seed_accounts.read_text(encoding="utf-8")).get("accounts") or {}).values():
                if account.get("password"):
                    known_values.append(str(account["password"]))
        secret_file = root / "instance" / "data" / "secret"
        if secret_file.is_file():
            known_values.append(secret_file.read_text(encoding="utf-8").strip())
        member_findings = member_scan_problems(freeze, manifest, known_values)
        check(not member_findings, "冻结包成员扫描（秘密/会话数据/平台必需成员）通过", member_findings[:5] or None)
        forbidden = [path.relative_to(freeze).as_posix() for path in walk_files(freeze)
                     if path.suffix in (".sqlite3", ".sqlite", ".jsonl", ".db")]
        check(not forbidden, "冻结包不含会话/导出数据（无未来数据）", forbidden[:5] or None)
        template = (freeze / "RESULTS_TEMPLATE.md").read_text(encoding="utf-8") if (freeze / "RESULTS_TEMPLATE.md").is_file() else ""
        checklist = (freeze / "CHECKLIST.zh-CN.md").read_text(encoding="utf-8") if (freeze / "CHECKLIST.zh-CN.md").is_file() else ""
        check("NOT_RUN" in template and "NOT_RUN" in checklist, "模板与清单明确人工状态 NOT_RUN")
        readme_text = (freeze / "README.md").read_text(encoding="utf-8") if (freeze / "README.md").is_file() else ""
        check("不是签名" in readme_text, "冻结说明明确哈希清单不是签名")
        for platform in ("web", "macos-arm64", "windows-x64"):
            check(any((entry.get("platform") == platform) for entry in (manifest.get("packages") or {}).values()),
                  f"冻结包含 {platform} 平台包")
        binding_problems = []
        for name, entry in (manifest.get("packages") or {}).items():
            if not name.endswith(".zip"):
                continue
            config_member = entry.get("config_member") or "connection.json"
            if entry.get("platform") == "web":
                sidecar = freeze / (name[:-len(".zip")] + ".connection.json")
                if not sidecar.is_file():
                    binding_problems.append(f"{name}: exported connection.json sidecar missing")
                    continue
                document = json.loads(sidecar.read_text(encoding="utf-8"))
            else:
                with zipfile.ZipFile(freeze / name) as archive:
                    if config_member not in {info.filename for info in archive.infolist()}:
                        binding_problems.append(f"{name}: config member {config_member} missing")
                        continue
                    document = json.loads(archive.read(config_member))
            if document.get("instance_id") != manifest.get("instance_id"):
                binding_problems.append(f"{name}: instance binding")
            if document.get("study_id") != entry.get("study_id") or document.get("release_id") != entry.get("release_id"):
                binding_problems.append(f"{name}: study/release binding")
            if document.get("mode") != entry.get("mode"):
                binding_problems.append(f"{name}: mode binding")
        check(not binding_problems, "冻结配置绑定本实例/研究/发行/模式", binding_problems[:5] or None)
        try:
            build_problems = freeze_binding_problems(manifest, freeze=freeze)
        except Exception as problem:  # a broken binding must fail readiness, never crash it
            build_problems = [f"构建绑定核对失败：{problem!r}"]
        check(not build_problems, "冻结包与当前构建/源码记录一致（先核对绑定再启动）", build_problems[:5] or None)
        reference_problems = generated_reference_problems(root, freeze, manifest)
        check(not reference_problems["missing"], "生成的每个引用都指向真实文件",
              reference_problems["missing"][:5] or {"references": reference_problems["references"]})
        check(not reference_problems["entries"], "生成的双击入口引用真实解释器与脚本（可含空格/单引号路径）",
              reference_problems["entries"][:5] or None)
        check(not reference_problems["documents"], "文档内的每个路径都解析到真实目标",
              reference_problems["documents"][:5] or None)
    except Exception as problem:  # a parse/read failure must be NOT_READY, never a launch
        check(False, "启动前预检在完成前失败（不启动任何服务/浏览器/程序）", repr(problem))
    if [entry for entry in checks if not entry["ok"]]:
        return finish("NOT_READY")

    # ------------------------------------------------------------ launch phase
    launched["done"] = True
    # Already-created task-owned credential copies are restricted here (never protected data).
    for candidate in (root / "evidence" / "setup_job.json", root / "evidence" / "check_job.json"):
        if restrict_private(candidate, record):
            check(stat.S_IMODE(candidate.stat().st_mode) == 0o600, f"已存在工作文件为 0600：{candidate.name}")
    baseline = protected_digest()
    import phase03_verify_shell as shell_verify
    verifier = shell_verify.Verify(root / "instance")
    verifier.port = int(parse_env_text((root / "private" / "service.env").read_text(encoding="utf-8"))
                        ["GEP_DESIGNER_PORT"])
    verifier.instance_id = (verifier.data / "instance").read_text(encoding="utf-8").strip()
    verifier.secret_key = (verifier.data / "secret").read_text(encoding="utf-8").strip()
    verifier.owner_password = (root / "private" / "owner_password.txt").read_text(encoding="utf-8").strip()
    # The real double-click path: stop any service, then execute the generated
    # START-HERE.command and open-browser.command exactly as the designer would.
    helper = [str(VENV_PYTHON), str(root / "service.py")]
    admin_base = f"http://admin.localhost:{verifier.port}"
    admin_url = admin_base + "/login"
    stop_result = subprocess.run(helper + ["stop"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    stop_payload = last_json(stop_result.stdout)
    stop_ok = stop_result.returncode == 0 and not (stop_payload or {}).get("refused")
    check(stop_ok, "从停止状态开始验证（生成的 stop 明确停止或报告无自有服务）",
          {"exit": stop_result.returncode, "payload": stop_payload})
    check(verifier.port >= 8040, "隔离实例端口 >= 8040（不使用默认 8000）", verifier.port)
    log_path = root / "evidence" / "gunicorn.log"
    log_size = log_path.stat().st_size if log_path.is_file() else 0
    entries_ok = False
    if stop_ok:
        start_entry = run_generated_entry(root / "START-HERE.command")
        start_ok = check(entry_ready(start_entry, admin_url),
                         "真实执行生成的 START-HERE.command：启动本实例并打开正确地址",
                         entry_detail(start_entry))
        if start_ok:
            browser_entry = run_generated_entry(root / "open-browser.command")
            browser_ok = check(entry_ready(browser_entry, admin_url),
                               "真实执行生成的 open-browser.command：就绪后打开正确地址",
                               entry_detail(browser_entry))
        else:
            browser_ok = False
            check(False, "真实执行生成的 open-browser.command：启动入口未通过，拒绝继续打开浏览器",
                  entry_detail(start_entry))
        entries_ok = start_ok and browser_ok
        entry_evidence = root / "evidence" / "open-browser.json"
        try:
            document = json.loads(entry_evidence.read_text(encoding="utf-8")) if entry_evidence.is_file() else {}
        except ValueError:
            document = {}
        check(bool(document.get("opened")) and document.get("url") == admin_url
              and (document.get("status") or {}).get("ready") is True,
              "生成入口自己记录的打开证据指向本实例", {"url": document.get("url"),
                                                        "opened": document.get("opened")})
        status_result = subprocess.run(helper + ["status"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        status_document = last_json(status_result.stdout) or {}
        check(bool(status_document.get("ready")), "只读健康检查：自有 PID + 端口应答 + 实例身份一致",
              {"pid": status_document.get("pid"), "identity": status_document.get("identity")})
        new_requests = b""
        if log_path.is_file():
            with open(log_path, "rb") as stream:
                stream.seek(log_size)
                new_requests = stream.read()
        record(bool(new_requests), "入口执行后实例访问日志有新增请求（真实浏览器打开的证据）",
               {"bytes": len(new_requests)})
    else:
        check(False, "生成的 stop 被拒绝：不继续启动服务或打开浏览器",
              {"exit": stop_result.returncode, "payload": stop_payload})
        check(False, "真实双击入口未执行（stop 未通过，避免打开错误实例）")
    try:
        if not entries_ok:
            record(False, "双击入口未通过：不继续对可疑实例执行 Chrome 登录或运行冻结包",
                   {"stop_ok": stop_ok})
        else:
            code, payload = run_node_driver(CHECK_DRIVER,
                                            {"path": root / "evidence" / "check_job.json",
                                             "document": {"admin_url": admin_base,
                                                          "username": "synthetic_owner",
                                                          "password": (root / "private" / "owner_password.txt")
                                                          .read_text(encoding="utf-8").strip()}},
                                            root / "evidence" / "chrome_check.log", 600)
            check(code == 0 and bool(payload and payload.get("ok")),
                  "真实 Chrome 登录隔离实例成功", (payload or {}).get("error") or (payload or {}).get("title"))
            if manifest.get("bindings", {}).get("macos-arm64"):
                run_frozen_macos_package(root, freeze, manifest, verifier, check, quiet=quiet)
    finally:
        subprocess.run(helper + ["stop"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    check(protected_digest() == baseline, "旧验收库与 dev 库内容摘要未变化")
    return finish()


def request_shutdown_on_signals():
    """Turn SIGINT/SIGTERM into a clean shutdown request; return the previous handlers.

    A serve process launched non-interactively (or in the background) inherits
    SIGINT as *ignored*, so Python never installs its default handler and the
    signal is a silent no-op. Installing an explicit handler for both signals
    keeps the only stop left to the caller from being SIGKILL, which would orphan
    the scoped ssh child and leave the Windows-side forward bound.
    """
    def request(_signum, _frame):
        raise KeyboardInterrupt

    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    for signum in previous:
        signal.signal(signum, request)
    return previous


def restore_signals(previous):
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def wait_for_shutdown(poll=1.0, ready=None):
    """Block until SIGINT/SIGTERM asks for shutdown, then return (cleanup stays with the caller).

    ``ready``, when given, is called once the stop handlers are installed, so a
    caller that must not signal before the stop path is armed gets a real
    handshake instead of guessing with a sleep; a signal delivered from then on
    is caught by this function.
    """
    previous = request_shutdown_on_signals()
    try:
        if ready is not None:
            ready()
        while True:
            time.sleep(poll)
    except KeyboardInterrupt:
        return "signal"
    finally:
        restore_signals(previous)


def serve(root, record, foreground=True, quiet=False):
    """Engineer-managed route: service + scoped reverse tunnel for the Windows entry."""
    import phase03_verify_windows_native as native

    root = Path(root).resolve()
    env = parse_env_text((root / "private" / "service.env").read_text(encoding="utf-8"))
    helper = [str(VENV_PYTHON), str(root / "service.py")]
    started = subprocess.run(helper + ["start"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    if started.returncode != 0:
        raise DesignerKitError(f"designer service did not start: {(started.stdout or started.stderr)[-200:]}")
    alias = native.Preparation().ssh_alias()[0]
    alias = alias[0] if alias else None
    spec = {"listen_port": int(env["GEP_DESIGNER_PORT"]), "target_port": int(env["GEP_DESIGNER_PORT"]),
            "windows_alias": alias}
    tunnel = native.Tunnel(spec, log=(lambda message: None) if quiet else (lambda message: record(True, message)))
    try:
        tunnel.start()
        if not tunnel.wait_ready(timeout=30):
            raise DesignerKitError(f"scoped tunnel not ready: {tunnel.detail}; "
                                   "do not change DNS/firewall/TLS trust")
        record(True, "作用域反向隧道就绪（Windows 侧端口与冻结端口一致）", spec)
        if not foreground:
            return {"port": spec["listen_port"], "tunnel": tunnel.detail}
        wait_for_shutdown()
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.stop()
        subprocess.run(helper + ["stop"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    return {"port": spec["listen_port"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="03F designer environment and frozen packages")
    parser.add_argument("--prepare", action="store_true", help="freeze the authentic platform downloads and create a new environment")
    parser.add_argument("--verify", action="store_true", help="readiness checks for an existing designer environment")
    parser.add_argument("--serve", action="store_true", help="engineer-managed service + scoped tunnel for the Windows entry")
    parser.add_argument("--root", default=None, help="designer environment directory (--verify/--serve)")
    parser.add_argument("--stamp", default=None, help="explicit stamp (--prepare; defaults to now)")
    parser.add_argument("--quiet", action="store_true", help="only print the final line")
    args = parser.parse_args(argv)
    if not (args.prepare or args.verify or args.serve):
        parser.error("pass --prepare, --verify or --serve")

    def record(ok, label, detail=None):
        if not args.quiet:
            print(("ok   " if ok else "FAIL ") + label + (f" :: {detail}" if detail is not None else ""), flush=True)
        return ok

    try:
        if args.prepare:
            stamp = args.stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            summary = prepare(stamp, record, quiet=args.quiet)
            print(f"PHASE03_DESIGNER_KIT_OK freeze={summary['freeze']} environment={summary['environment']}")
            return 0
        if not args.root:
            parser.error("--verify/--serve require --root <designer environment>")
        if args.serve:
            serve(args.root, record, quiet=args.quiet)
            return 0
        readiness = verify(args.root, record, quiet=args.quiet)
        print(f"PHASE03_DESIGNER_READINESS {readiness['status']} "
              f"checks_failed={readiness['checks_failed']} env={readiness['environment']}")
        return 0 if readiness["status"] == "READY" else 1
    except DesignerKitError as error:
        print(f"PHASE03_DESIGNER_KIT_FAILED {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
