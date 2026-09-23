#!/usr/bin/env python3
"""Build the Windows x64 readiness kit from the real platform lifecycle (03F).

This module owns the *preparation* half of the Windows native acceptance. It does
not invent a frozen package: it runs one isolated synthetic instance (its own data
directory, database, secret, instance id and loopback port) and drives the real
researcher lifecycle over authenticated HTTP exactly as the GUI does:

* create one synthetic study per participation mode (``anonymous``, ``id``,
  ``password``) and freeze a release for each through
  ``POST /studies/<id>`` ``op=native`` / ``op=native_archive`` / ``op=approve``,
  which runs the platform's own ``publish_complete_artifact`` freeze;
* upload the *same* immutable Windows x64 program archive to all three studies, so
  one program build serves every frozen mode, and prove the approved study mode
  cannot be edited afterwards (``policy_frozen_after_release``);
* create real participant accounts through the roster operation, invite and
  activate one scoped research member through the real invitation flow, and record
  real logins plus real HTTP admissions (one per mode, plus a wrong-credential
  refusal) against the frozen releases;
* download the frozen complete package and its sidecars through the authenticated
  release endpoints (never from a locally re-zipped copy) and compare every byte
  against the database record of the release;
* record the platform-side WN06 contracts (stored artifact tamper, sidecar tamper,
  missing dependency, wrong platform, revoked download permission) and restore the
  instance afterwards.

The kit carries only public material; the usable test accounts live in a separate
private artifact (mode ``0600``) that is never inside the kit, never logged and
never distributed with the participant package. Hash lists in the kit are
integrity checks, never signatures.

The isolated instance stays on disk so the strict runtime gate and a later real
Windows run can be bound to the exact same releases, and
``tools/phase03_verify_windows_native.py --serve-runtime`` can serve it again on
the frozen loopback port.
"""
from __future__ import annotations

import hashlib
import http.client
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
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote as urlquote

ROOT = Path(__file__).resolve().parents[1]
PHASE_ROOT = ROOT / "local_data" / "phase03_20260920"
RUN_ROOT = PHASE_ROOT / "p0308"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
GUNICORN = ROOT / ".venv" / "bin" / "gunicorn"
LICENSE = ROOT / "LICENSE"
PROGRAM_ARCHIVE = ROOT / "build" / "windows" / "synthetic_windows.zip"
DESCRIPTOR = ROOT / "build" / "windows" / "descriptor.json"
HARNESS = ROOT / "tools" / "windows_native_harness.py"
# The kit must be self-contained after copying kit/ plus the private account file
# to a fresh machine: every launcher target travels inside the kit itself.
KIT_HARNESS = "operator/harness/windows_native_harness.py"

MODES = ("anonymous", "id", "password")
MODE_LABELS = {"anonymous": "无需 ID", "id": "预发名单 ID", "password": "ID+密码"}
KIT_VERSION = "gep-windows-kit/v2"
RUNTIME_FORMAT = "gep-windows-runtime/v1"
ACCOUNTS_FORMAT = "gep-windows-accounts/v1"
OWNER_FORMAT = "gep-windows-owner/v1"
MEMBER_ACTIONS = ("study.view", "session.recover", "data.export_raw")
OWNER_USERNAME = "synthetic_owner"
MEMBER_USERNAME = "wn_engineering_member"
ID_CODES = ("001", "002")
MIN_PASSWORD = 12
ARTIFACT_FORMAT = "gep-artifact/v1"
_SECRET_PATTERNS = (
    re.compile(rb"BEGIN (RSA|OPENSSH|EC|PRIVATE) PRIVATE KEY"),
    re.compile(rb"password\s*[:=]\s*[\"'][^\"'\s]{4,}", re.IGNORECASE),
    re.compile(rb"token\s*[:=]\s*[\"'][A-Za-z0-9_\-]{12,}", re.IGNORECASE),
    re.compile(rb"secret\s*[:=]\s*[\"'][A-Za-z0-9_\-]{8,}", re.IGNORECASE),
)


class KitError(Exception):
    """The preparation itself is defective (not merely the external device)."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def researcher_temporary_password():
    """One synthetic researcher password from the platform's own pure module.

    Every researcher set-password entry enforces ``core.researcher_passwords``
    (U07): at least 6 characters with one ASCII uppercase letter, one lowercase
    letter, one digit and one visible punctuation mark each. The member password
    is set through the real legacy ``/activate`` route, so the kit must not
    invent its own weak generator: the shared module is imported lazily (it has
    no Django imports) and guarantees all four classes with at least 24
    characters by construction.
    """
    server = str(ROOT / "server")
    if server not in sys.path:
        sys.path.insert(0, server)
    from core import researcher_passwords
    return researcher_passwords.generate_temporary_password()


def canonical_json(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def scan_secrets(path, limit=8 * 1024 * 1024):
    """Return the credential-like patterns found in one file; never prints a match."""
    raw = Path(path).read_bytes()[:limit]
    return [pattern.pattern.decode("utf-8", "ignore") for pattern in _SECRET_PATTERNS if pattern.search(raw)]


def program_source_digest():
    """One deterministic digest of the program sources this kit was frozen from.

    The strict gate recomputes it from the current working tree, so evidence
    produced against older program sources is refused as stale.
    """
    project = ROOT / "examples" / "synthetic_experiment"
    digest = hashlib.sha256()
    files = []
    for path in sorted(project.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(project).as_posix()
        # Generated import metadata, compiled caches and human documentation are
        # not program sources; the digest tracks the files the program is built from.
        if relative.startswith(".godot/") or "__pycache__" in relative or path.suffix in (".pyc", ".md"):
            continue
        files.append((relative, path))
    for name in ("tools/build.py", "tools/package_build.py", "tools/windows_template.py"):
        files.append((name, ROOT / name))
    for relative, path in files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def detect_preparation_address(peer=None):
    """The preparation host address the Windows side can reach, from this machine.

    The address is only informational for the operator material; the actual
    scoped route is the reverse SSH connection the preparation host opens. A UDP
    ``connect`` performs no traffic, so inspecting the route changes nothing.
    """
    candidates = [peer] if peer else []
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    candidates.append("192.0.2.1")  # TEST-NET-1: routing lookup only, no packets
    seen = set()
    for target in candidates:
        if not target or target in seen:
            continue
        seen.add(target)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect((target, 9))
                address = probe.getsockname()[0]
        except OSError:
            continue
        if address and not address.startswith("127."):
            return address
    return ""


def windows_alias():
    """The documented Windows SSH alias of this machine, if one is configured."""
    config = Path.home() / ".ssh" / "config"
    if not config.is_file():
        return ""
    text = config.read_text(encoding="utf-8", errors="ignore")
    for block in re.finditer(r"^\s*Host\s+(.+)$", text, re.MULTILINE | re.IGNORECASE):
        for name in block.group(1).split():
            if any(token in name.lower() for token in ("win", "quant")):
                return name
    return ""


def ssh_resolved_hostname(alias):
    """Resolve one alias locally (``ssh -G`` performs no connection)."""
    if not alias:
        return None
    try:
        result = subprocess.run(
            [os.environ.get("GEP_SSH_BIN") or "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-G", alias],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        tokens = line.split(None, 1)
        if len(tokens) == 2 and tokens[0].lower() == "hostname":
            return tokens[1].strip()
    return None


def free_port(start=8060, stop=8140):
    for candidate in range(start, stop):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
        return candidate
    raise KitError("no free loopback port in the preparation range")


class HttpClient:
    """Minimal authenticated HTTP client for the isolated instance (stdlib only)."""

    def __init__(self, port, timeout=120):
        self.port = port
        self.timeout = timeout
        self.cookies = {}
        self.csrf = ""
        self.requests = []

    def _connection(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)

    def _headers(self, host, extra=None):
        headers = {"Host": host, "Accept": "application/json"}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in sorted(self.cookies.items()))
        if extra:
            headers.update(extra)
        return headers

    def _remember(self, response):
        for header, value in response.getheaders():
            if header.lower() == "set-cookie":
                pair = value.split(";", 1)[0]
                if "=" in pair:
                    name, _, cookie = pair.partition("=")
                    self.cookies[name.strip()] = cookie.strip()
                    # Django rotates the CSRF token on login; the rotated value is
                    # the only one the next POST may carry.
                    if name.strip() == "csrftoken":
                        self.csrf = cookie.strip()

    def request(self, method, path, host="admin.localhost", body=None, headers=None, expect_redirect=False):
        connection = self._connection()
        try:
            connection.request(method, path, body=body, headers=self._headers(host, headers))
            response = connection.getresponse()
            payload = response.read()
            self._remember(response)
            record = {"method": method, "path": path, "host": host, "status": response.status,
                      "bytes": len(payload), "location": response.getheader("Location"),
                      "headers": {name.lower(): value for name, value in response.getheaders()}}
            self.requests.append(record)
            if expect_redirect:
                if response.status not in (301, 302, 303):
                    raise KitError(f"{method} {path} expected a redirect, got {response.status}: "
                                   f"{payload[:200].decode('utf-8', 'replace')}")
            return response.status, payload, record
        finally:
            connection.close()

    def get(self, path, host="admin.localhost"):
        return self.request("GET", path, host=host)

    def post_form(self, path, fields, host="admin.localhost", expect_redirect=True):
        body = []
        for name, value in fields:
            body.append((name, value))
        if self.csrf and not any(name == "csrfmiddlewaretoken" for name, _ in body):
            body.append(("csrfmiddlewaretoken", self.csrf))
        encoded = "&".join(f"{urlquote(str(name))}={urlquote(str(value))}" for name, value in body)
        status, payload, record = self.request(
            "POST", path, host=host, body=encoded.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            expect_redirect=expect_redirect)
        return status, payload, record

    def post_multipart(self, path, fields, file_field, filename, data, host="admin.localhost",
                       expect_redirect=False):
        boundary = "----gep" + secrets.token_hex(12)
        chunks = []
        for name, value in fields:
            chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
        if self.csrf:
            chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrfmiddlewaretoken\"\r\n\r\n"
                          f"{self.csrf}\r\n".encode())
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                      f"filename=\"{filename}\"\r\nContent-Type: application/zip\r\n\r\n".encode())
        chunks.append(data)
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        return self.request("POST", path, host=host, body=body,
                            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                            expect_redirect=expect_redirect)

    def post_json(self, path, document, host="experiment.localhost"):
        body = canonical_json(document)
        return self.request("POST", path, host=host, body=body,
                            headers={"Content-Type": "application/json"}, expect_redirect=False)

    def login(self, username, password):
        status, payload, _ = self.get("/login")
        if status != 200:
            raise KitError(f"login page unavailable (HTTP {status})")
        match = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', payload)
        if match:
            self.csrf = match.group(1).decode()
        self.post_form("/login", [("username", username), ("password", password)], expect_redirect=False)
        if "gep_admin" not in self.cookies:
            raise KitError(f"login failed for {username!r} (no session cookie)")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def display_path(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


class Instance:
    """One isolated synthetic instance: own volume, database, secret and port."""

    def __init__(self, root: Path, quiet=False, log=print):
        self.root = Path(root)
        self.data = self.root / "instance"
        self.db = self.data / "gep.sqlite3"
        self.logs = self.root / "http"
        self.owner_password = secrets.token_urlsafe(24)
        self.instance_id = str(uuid.uuid4())
        self.secret_key = secrets.token_urlsafe(48)
        self.port = None
        self.server = None
        self.quiet = quiet
        self.log = log

    # ------------------------------------------------------------------ volume
    def env(self, public_api=None):
        return {
            **os.environ,
            "GEP_DATA_DIR": str(self.data),
            "GEP_SECRET_KEY": self.secret_key,
            "GEP_EXPECTED_INSTANCE": self.instance_id,
            "GEP_PUBLIC_API": public_api or f"http://127.0.0.1:{self.port}",
            "PYTHONPATH": str(ROOT / "server"),
            "DJANGO_SETTINGS_MODULE": "gep.settings",
        }

    def run_python(self, code, extra_env=None, timeout=600):
        env = self.env()
        if extra_env:
            env.update(extra_env)
        return subprocess.run([str(VENV_PYTHON), "-c", code], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=timeout)

    def initialize(self):
        self.data.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        code = (
            "import os, uuid, django\n"
            "django.setup()\n"
            "from django.core.management import call_command\n"
            "from django.contrib.auth import get_user_model\n"
            "from core.models import AccountProfile, Instance\n"
            "call_command('migrate', verbosity=0)\n"
            "owner = get_user_model().objects.create_user(os.environ['GEP_OWNER_USERNAME'],"
            " password=os.environ['GEP_OWNER_PASSWORD'])\n"
            "Instance.objects.create(instance_id=uuid.UUID(os.environ['GEP_INSTANCE_ID']), owner=owner)\n"
            "AccountProfile.objects.create(user=owner, role='user', must_change_password=False,"
            " auth_version=1, revision=0)\n"
            "print('INSTANCE_READY')\n"
        )
        result = self.run_python(code, {"GEP_OWNER_PASSWORD": self.owner_password,
                                        "GEP_INSTANCE_ID": self.instance_id,
                                        "GEP_OWNER_USERNAME": OWNER_USERNAME})
        if "INSTANCE_READY" not in result.stdout:
            raise KitError(f"isolated instance initialization failed: {result.stdout}\n{result.stderr}")
        for name, value in (("secret", self.secret_key), ("instance", self.instance_id)):
            fd = os.open(self.data / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(value)
        return self

    def start(self):
        if self.server is not None and self.server.poll() is None:
            return self
        log = open(self.logs / "gunicorn.log", "a")
        self.server = subprocess.Popen(
            [str(GUNICORN), "gep.wsgi:application", "--bind", f"127.0.0.1:{self.port}",
             "--workers", "1", "--threads", "4", "--timeout", "1800", "--access-logfile", "-"],
            cwd=ROOT, env=self.env(), stdout=log, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    return self
            except OSError:
                if self.server.poll() is not None:
                    raise KitError(f"isolated server exited early, see {self.logs / 'gunicorn.log'}")
                time.sleep(0.3)
        raise KitError("isolated server did not become ready")

    def stop(self):
        if self.server is not None and self.server.poll() is None:
            self.server.send_signal(signal.SIGTERM)
            try:
                self.server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.server.kill()
        self.server = None

    # ------------------------------------------------------------------- facts
    def db_rows(self, sql, params=()):
        connection = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=20)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def revision(self):
        return self.db_rows("select governance_revision from core_instance")[0][0]

    def release_record(self, release_id):
        rows = self.db_rows("select artifact_digest, artifact_path, artifact_size, config, approved, build_id "
                            "from core_release where id=?", [release_id.replace("-", "")])
        if not rows:
            raise KitError(f"release {release_id} missing in the isolated database")
        return {"digest": rows[0][0], "path": rows[0][1], "size": rows[0][2],
                "config": json.loads(rows[0][3]), "approved": bool(rows[0][4]),
                "build_id": _uuid_text(rows[0][5])}

    def build_record(self, study_id, version=None):
        sql = "select id, digest, package_path, descriptor from core_build where study_id=?"
        params = [study_id.replace("-", "")]
        if version is not None:
            sql += " and json_extract(descriptor,'$.version')=?"
            params.append(version)
        rows = self.db_rows(sql, params)
        if not rows:
            raise KitError(f"build {version!r} missing for study {study_id}")
        return {"id": _uuid_text(rows[0][0]), "digest": rows[0][1], "package_path": rows[0][2],
                "descriptor": json.loads(rows[0][3])}

    def study_id_of(self, release_id):
        rows = self.db_rows("select study_id from core_release where id=?", [release_id.replace("-", "")])
        return _uuid_text(rows[0][0])

    def session_rows(self, study_id):
        return self.db_rows(
            "select session.id, participant.code, release.id, session.revoked from core_session session "
            "join core_release release on session.release_id=release.id "
            "join core_participant participant on session.participant_id=participant.id "
            "where release.study_id=? order by session.created_at", [study_id.replace("-", "")])

    def participant_codes(self, study_id):
        return sorted(row[0] for row in self.db_rows(
            "select code from core_participant where study_id=?", [study_id.replace("-", "")]) if row[0])


def _uuid_text(raw):
    if isinstance(raw, bytes):
        raw = raw.decode()
    raw = str(raw)
    if len(raw) == 32:
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return raw


class KitBuilder:
    """Drives the real lifecycle and writes the kit plus the private artifacts."""

    def __init__(self, root: Path, record, require, quiet=False, log=print):
        self.root = Path(root).resolve()
        self.kit = self.root / "kit"
        self.private = self.root / "private"
        self.evidence = self.root / "evidence"
        self.record = record
        self.require = require
        self.quiet = quiet
        self.log = log
        self.instance = Instance(self.root / "instance-root", quiet=quiet, log=log)
        self.http = None
        self.owner = None
        self.member = None
        self.member_password = researcher_temporary_password()
        self.participant_passwords = {code: secrets.token_urlsafe(16) for code in ID_CODES}
        self.releases = {}
        self.alternates = {}
        self.artifacts = {}
        self.accounts = {"format": ACCOUNTS_FORMAT, "synthetic_only": True,
                         "instance_id": None, "member": None, "participants": {},
                         "note": "Synthetic test material for the isolated 03F instance. Never distributed with "
                                 "the participant package, never committed, never logged."}
        self.runtime = {"format": RUNTIME_FORMAT, "instance_id": None, "port": None, "api_url": None,
                        "tunnel": {}, "member": None, "studies": {}, "expected": {},
                        "private_accounts": "private/runtime_accounts.json",
                        "notes": [
                            "The frozen connection.json points at this loopback port; a scoped SSH tunnel "
                            "(127.0.0.1:<port> on Windows to 127.0.0.1:<port> on the preparation host) is the "
                            "runtime prerequisite, or an equivalent verified LAN endpoint decided at freeze time.",
                            "The kit carries no credential; usable accounts live in the private artifact.",
                        ]}

    # ------------------------------------------------------------------ helpers
    def study_operation(self, study_id, fields, expect=(200, 302)):
        # A successful op returns a redirect; a page-rendering op (roster,
        # recovery code, invitation) returns 200 with the notice. Both are
        # verified against the database right after the call.
        status, payload, record = self.http.post_form(f"/studies/{study_id}", fields, expect_redirect=False)
        if status not in expect:
            raise KitError(f"study op {fields[0][1]!r} returned {status}, expected {expect}: "
                           f"{payload[:300].decode('utf-8', 'replace')}")
        return status, payload, record

    def note(self, message):
        self.log(f"[windows-kit] {message}")

    # -------------------------------------------------------------------- steps
    def prepare(self):
        self.kit.mkdir(parents=True, exist_ok=True)
        self.private.mkdir(parents=True, exist_ok=True)
        self.evidence.mkdir(parents=True, exist_ok=True)
        self._prepare_instance()
        for mode in MODES:
            self._prepare_mode(mode)
        self._prepare_member()
        self._record_admissions()
        self._record_platform_negatives()
        self._write_kit_documents()
        self._write_private_artifacts()
        self._write_operator_material()
        self.check_relocatable_launchers()
        self.instance.stop()
        return {"kit": str(self.kit), "runtime": self.runtime, "releases": self.releases,
                "alternates": self.alternates, "artifacts": self.artifacts,
                "instance": str(self.instance.root)}

    def _prepare_instance(self):
        archive_digest = sha256_file(PROGRAM_ARCHIVE)
        descriptor = read_json(DESCRIPTOR)
        self.require(descriptor.get("program_sha256") == archive_digest,
                     "kit 使用的程序归档与描述记录一致", descriptor.get("program_sha256"))
        self.instance.initialize()
        self.instance.start()
        self.http = HttpClient(self.instance.port)
        self.http.login(OWNER_USERNAME, self.instance.owner_password)
        self.record(True, "隔离合成实例与真实服务器就绪（独立数据目录/数据库/密钥/端口）",
                    {"port": self.instance.port, "instance_id": self.instance.instance_id,
                     "data": display_path(self.instance.data)})
        self.accounts["instance_id"] = self.instance.instance_id
        self.runtime["instance_id"] = self.instance.instance_id
        self.runtime["port"] = self.instance.port
        self.runtime["api_url"] = f"http://127.0.0.1:{self.instance.port}"
        listen_port = self.instance.port + 100
        alias = windows_alias()
        address = detect_preparation_address(ssh_resolved_hostname(alias))
        pending = []
        if not alias:
            pending.append("~/.ssh/config 中没有可用的 Windows 主机别名")
        if not address:
            pending.append("未从当前环境检测到准备主机可被访问的地址")
        self.runtime["tunnel"] = {
            "listen": f"127.0.0.1:{listen_port}",
            "listen_port": listen_port,
            "target": f"127.0.0.1:{self.instance.port}",
            "target_port": self.instance.port,
            "tunnel_port": listen_port,
            "direction": "reverse",
            "preparation_host": socket.gethostname(),
            "preparation_address": address,
            "windows_alias": alias,
            "runtime_prerequisite": "PENDING" if pending else "READY",
            "pending_reason": "; ".join(pending) or None,
            "note": "Scoped SSH tunnel (no firewall, DNS or TLS-trust change): the preparation host opens the "
                    f"reverse connection to the Windows machine and forwards 127.0.0.1:{listen_port} there to "
                    f"127.0.0.1:{self.instance.port} on the preparation host. The frozen program keeps talking to "
                    f"127.0.0.1:{self.instance.port}; the harness forwards that frozen port to the tunnel, so fault "
                    "injection never changes the frozen configuration. Without the tunnel the runtime cases stay "
                    "BLOCKED; a PENDING prerequisite is never represented as ready.",
        }
        self.runtime["expected"]["program_sha256"] = archive_digest
        self.runtime["expected"]["descriptor_sha256"] = sha256_file(DESCRIPTOR)
        self.runtime["expected"]["program_source_digest"] = program_source_digest()
        return descriptor

    def _create_study(self, mode):
        status, payload, record = self.http.post_form("/", [("title", f"03F Windows 原生合成研究 · {MODE_LABELS[mode]}")])
        location = record.get("location") or ""
        match = re.search(r"/studies/([0-9a-f-]{36})", location)
        if status != 302 or not match:
            raise KitError(f"study creation failed for mode {mode}: HTTP {status} {location!r}")
        return match.group(1)

    def _prepare_mode(self, mode):
        study_id = self._create_study(mode)
        # The engineering cases create real sessions through the same roster code
        # more than once (a fresh local store has no candidate to continue, so
        # each admission is a new session). The frozen policy must leave room for
        # one complete WN01-WN06 pass *and* a diagnostic rerun of a single case on
        # the same instance; the measured budget of one full pass is 6 new
        # sessions for the same participant, so 8 would leave no headroom.
        self.study_operation(study_id, [("op", "configure"), ("mode", mode), ("max_sessions", "24")])
        self.record(True, f"{mode}：真实研究已创建并配置冻结模式",
                    {"study_id": study_id, "mode": mode, "max_sessions": 24})

        descriptor = read_json(DESCRIPTOR)
        self.study_operation(study_id, [("op", "native"), ("descriptor", json.dumps(descriptor))])
        build = self.instance.build_record(study_id, descriptor["version"])
        self.require(build["descriptor"] == descriptor, "平台登记的描述等于上传的描述", build["id"])

        if mode == "id":
            roster = "\n".join(ID_CODES)
        elif mode == "password":
            roster = "\n".join(f"{code}\t{self.participant_passwords[code]}" for code in ID_CODES)
        else:
            roster = ""
        if roster:
            self.study_operation(study_id, [("op", "roster"), ("roster_format", "legacy_tab"), ("roster", roster)],
                                 expect=(200, 302))
            codes = self.instance.participant_codes(study_id)
            self.require(codes == list(ID_CODES), f"{mode}：真实名单账号已创建",
                         {"codes": codes, "count": len(codes)})
            for code in ID_CODES:
                self.accounts["participants"].setdefault(mode, {})[code] = {
                    "code": code,
                    "password": self.participant_passwords[code] if mode == "password" else None,
                    "study_id": study_id, "release_id": None}

        raw = PROGRAM_ARCHIVE.read_bytes()
        status, payload, _ = self.http.post_multipart(
            f"/studies/{study_id}", [("op", "native_archive"), ("build_id", build["id"])],
            "package", PROGRAM_ARCHIVE.name, raw)
        if status not in (200, 302):
            raise KitError(f"{mode}: program upload returned {status}: {payload[:300].decode('utf-8', 'replace')}")
        build = self.instance.build_record(study_id, descriptor["version"])
        self.require(bool(build["package_path"]), f"{mode}：程序归档经真实上传绑定到构建", build["package_path"])

        self.study_operation(study_id, [("op", "approve"), ("build_id", build["id"])])
        rows = self.instance.db_rows("select id from core_release where study_id=?", [study_id.replace("-", "")])
        release_id = _uuid_text(rows[0][0])
        record = self.instance.release_record(release_id)
        self.require(record["approved"] and record["digest"] and record["path"],
                     f"{mode}：平台冻结并批准完整包", {"release_id": release_id, "digest": record["digest"]})
        self.require(record["config"].get("artifact_format_version") == ARTIFACT_FORMAT
                     and record["config"].get("mode") == mode,
                     f"{mode}：发行冻结了完整包格式与本研究模式", record["config"].get("mode"))

        # The approved study mode is immutable: a later policy edit must be refused.
        status, payload, _ = self.http.post_form(
            f"/studies/{study_id}", [("op", "configure"), ("mode", "password" if mode != "password" else "id"),
                                     ("max_sessions", "25")], expect_redirect=False)
        frozen_code = payload.decode("utf-8", "replace")
        self.require(status == 409 and "policy_frozen_after_release" in frozen_code,
                     f"{mode}：批准后参与模式不可再编辑（真实拒绝）",
                     {"status": status, "code": "policy_frozen_after_release"})
        current = self.instance.db_rows("select mode, max_sessions from core_study where id=?",
                                        [study_id.replace("-", "")])[0]
        self.require(current[0] == mode and current[1] == 24, f"{mode}：研究策略未被越权修改",
                     {"mode": current[0], "max_sessions": current[1]})

        self.study_operation(study_id, [("op", "recruitment"), ("state", "open")])
        self._publish(study_id, release_id, mode)
        _manifest, info = self._download_release(mode, mode, study_id, release_id, build["id"], record)
        info.update({"program_sha256": descriptor["program_sha256"],
                     "frozen_config_mode": record["config"].get("mode")})
        self.releases[mode] = info
        self.runtime["studies"][mode] = {key: value for key, value in info.items()
                                         if key in ("study_id", "release_id", "build_id", "package_sha256",
                                                    "package_size", "config_member", "entry", "participant_codes")}
        if mode == "password":
            self._prepare_alternate_release(study_id, build, descriptor)

    def _prepare_alternate_release(self, study_id, build, descriptor):
        """Freeze one more release of the same build and make it the current one.

        The device keeps running the earlier frozen release: after the study's
        current release moved on, the older approved release must still admit and
        upload (WN06 old-release compatibility), which the real device run proves.
        """
        self.study_operation(study_id, [("op", "approve"), ("build_id", build["id"])])
        rows = self.instance.db_rows("select id from core_release where study_id=? order by rowid desc limit 1",
                                     [study_id.replace("-", "")])
        alternate_id = _uuid_text(rows[0][0])
        record = self.instance.release_record(alternate_id)
        self.require(record["approved"] and record["digest"] and record["config"].get("mode") == "password",
                     "password：同一构建的第二个真实发行已冻结", {"release_id": alternate_id})
        _manifest, info = self._download_release("password-alternate", "password", study_id, alternate_id,
                                                 build["id"], record)
        revision = self.instance.db_rows("select revision from core_study where id=?",
                                         [study_id.replace("-", "")])[0][0]
        self.study_operation(study_id, [("op", "current_release"), ("study_revision", str(revision)),
                                        ("release_id", alternate_id)])
        current = self.instance.db_rows("select current_release_id from core_study where id=?",
                                        [study_id.replace("-", "")])[0][0]
        self.require(_uuid_text(current) == alternate_id, "password：第二个发行成为当前发行（旧发行不再当前）",
                     {"current": alternate_id, "previous": info and self.releases["password"]["release_id"]})
        self.alternates["password"] = info
        self.runtime["alternates"] = {"password": {key: value for key, value in info.items()
                                                   if key in ("study_id", "release_id", "build_id", "package_sha256",
                                                              "package_size", "config_member", "entry")}}
        self.runtime["expected"]["alternate_release_id"] = alternate_id
        return info

    def _publish(self, study_id, release_id, mode):
        revision = self.instance.db_rows("select revision from core_study where id=?",
                                         [study_id.replace("-", "")])[0][0]
        self.study_operation(study_id, [("op", "publication"), ("study_revision", str(revision)), ("public", "1"),
                                        ("public_summary", "03F 合成 Windows 原生研究"),
                                        ("public_duration", "约 5 分钟"),
                                        ("public_device_requirements", "Windows x64 桌面")])
        revision = self.instance.db_rows("select revision from core_study where id=?",
                                         [study_id.replace("-", "")])[0][0]
        self.study_operation(study_id, [("op", "current_release"), ("study_revision", str(revision)),
                                        ("release_id", release_id)])
        current = self.instance.db_rows("select current_release_id from core_study where id=?",
                                        [study_id.replace("-", "")])[0][0]
        self.require(_uuid_text(current) == release_id, f"{mode}：真实发行设为当前发行",
                     {"release_id": release_id})

    def _download_release(self, label, mode, study_id, release_id, build_id, record):
        target = self.kit / "delivery" / label
        target.mkdir(parents=True, exist_ok=True)
        status, payload, headers = self.http.get(f"/releases/{release_id}/artifact")
        if status != 200:
            raise KitError(f"{mode}: artifact download returned {status}")
        package = target / f"gep-{release_id}.zip"
        package.write_bytes(payload)
        digest = sha256_file(package)
        self.require(digest == record["digest"] and len(payload) == record["size"],
                     f"{mode}：真实授权下载的完整包等于数据库记录",
                     {"sha256": digest, "bytes": len(payload)})
        self.require(headers["headers"].get("x-artifact-sha256") == record["digest"],
                     f"{mode}：下载响应头记录同一外层摘要", headers["headers"].get("x-artifact-sha256"))
        sidecars = {}
        status, manifest_payload, _headers = self.http.get(
            f"/releases/{release_id}/artifact/artifact_manifest.json")
        if status != 200:
            raise KitError(f"{mode}: manifest sidecar download returned {status}")
        (target / "artifact_manifest.json").write_bytes(manifest_payload)
        manifest = json.loads(manifest_payload)
        sidecar_members = ["artifact_manifest.json", manifest["config_member"],
                           "LICENSE", "THIRD_PARTY_NOTICES.txt"]
        for name in sidecar_members:
            encoded = urlquote(name, safe="/")
            status, member, member_headers = self.http.get(f"/releases/{release_id}/artifact/{encoded}")
            if status != 200:
                raise KitError(f"{mode}: sidecar {name} download returned {status}")
            destination = target / PurePosixPath(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(member)
            sidecars[name] = {"sha256": sha256_file(destination), "size": len(member),
                              "header": member_headers["headers"].get("x-artifact-member-sha256")}
        self.require(sidecars["artifact_manifest.json"]["header"] == sha256_file(target / "artifact_manifest.json"),
                     f"{mode}：清单 sidecar 的响应头摘要与字节一致")
        self.require(manifest.get("artifact_format_version") == ARTIFACT_FORMAT
                     and manifest.get("platform") == "windows_x64",
                     f"{mode}：下载清单声明平台与格式", manifest.get("artifact_format_version"))
        self.require(manifest.get("study_id") == study_id and manifest.get("release_id") == release_id
                     and manifest.get("build_id") == build_id,
                     f"{mode}：下载清单绑定本研究/发行/构建",
                     {"study_id": manifest.get("study_id"), "release_id": manifest.get("release_id"),
                      "build_id": manifest.get("build_id")})
        self.require(manifest.get("program_sha256") == self.runtime["expected"]["program_sha256"],
                     f"{mode}：下载清单记录本次上传的程序摘要", manifest.get("program_sha256"))
        with zipfile.ZipFile(package) as archive:
            bundled = archive.read("artifact_manifest.json")
        self.require(bundled == (target / "artifact_manifest.json").read_bytes(),
                     f"{mode}：包内清单与平台 sidecar 逐字节一致（未被本工具改写）")
        config = json.loads((target / PurePosixPath(manifest["config_member"])).read_bytes())
        self.require(config.get("api_url") == f"http://127.0.0.1:{self.instance.port}"
                     and config.get("instance_id") == self.instance.instance_id
                     and config.get("study_id") == study_id and config.get("release_id") == release_id
                     and config.get("mode") == mode,
                     f"{mode}：冻结公开配置绑定本次实例/研究/发行/模式",
                     {"api_url": config.get("api_url"), "mode": config.get("mode")})
        self.require(not ({"password", "participants", "roster", "token", "secret"} & set(config)),
                     f"{mode}：冻结公开配置不含口令/名单/凭据")
        self.artifacts[label] = {"package": str(package.relative_to(self.root)), "sidecars": sidecars}
        info = {"study_id": study_id, "release_id": release_id, "build_id": build_id, "mode": mode,
                "package_sha256": record["digest"], "package_size": record["size"],
                "artifact_sha256": record["digest"], "artifact_size": record["size"],
                "delivery": f"delivery/{label}/gep-{release_id}.zip",
                "config_member": manifest.get("config_member"), "entry": manifest.get("entry"),
                "participant_codes": list(ID_CODES) if mode != "anonymous" else []}
        self.record(True, f"{label}：真实授权下载的完整包与 sidecar 已入 kit（未重新打包）",
                    {"package": package.name, "members": len(manifest.get("members", []))})
        return manifest, info

    def _prepare_member(self):
        username = MEMBER_USERNAME
        studies = {mode: info["study_id"] for mode, info in self.releases.items()}
        member_client = HttpClient(self.instance.port)
        for mode, study_id in studies.items():
            revision = self.instance.revision()
            fields = [("op", "invite"), ("revision", str(revision)), ("username", username)]
            fields.extend(("actions", action) for action in MEMBER_ACTIONS)
            status, payload, _ = self.study_operation(study_id, fields, expect=(200, 302))
            text = payload.decode("utf-8", "replace")
            match = re.search(r"邀请密钥（请通过可信渠道交付）：([A-Za-z0-9_\-]{16,})", text)
            if not match:
                raise KitError(f"invitation key for study {study_id} not found in the response")
            token = match.group(1)
            # An existing account must accept its own invitation while signed in
            # (the platform refuses to let another account reset it), so later
            # studies are activated by the member itself.
            existing = bool(self.instance.db_rows("select 1 from auth_user where username=?", [username]))
            client = member_client if existing else self.http
            if existing:
                member_client.login(username, self.member_password)
            status, payload, _ = client.post_form(
                "/activate", [("token", token), ("password", self.member_password)], expect_redirect=False)
            if status != 302:
                raise KitError(f"invitation activation failed for study {study_id}: HTTP {status}")
            self.record(True, f"{mode}：经真实邀请流程创建/复用受限成员（study.view/session.recover/data.export_raw）",
                        {"username": username, "study_id": study_id, "actions": list(MEMBER_ACTIONS),
                         "activated_by": "member" if existing else "owner"})
        grants = self.instance.db_rows(
            "select count(*) from core_grant grant join auth_user user on grant.user_id=user.id "
            "where user.username=?", [username])[0][0]
        self.require(grants == len(MEMBER_ACTIONS) * len(studies), "受限成员按邀请动作获得授权",
                     {"grants": grants, "expected": len(MEMBER_ACTIONS) * len(studies)})
        self.require(not self.instance.db_rows(
            "select count(*) from core_grant grant join auth_user user on grant.user_id=user.id "
            "where user.username=? and grant.action in ('release.approve_pilot','build.upload','study.configure')",
            [username])[0][0], "受限成员没有被授予批准/上传/配置权限")
        member = HttpClient(self.instance.port)
        member.login(username, self.member_password)
        self.member = member
        self.record(True, "受限成员真实登录成功（会话 cookie）", {"username": username})
        self.accounts["member"] = {"username": username, "password": self.member_password,
                                   "actions": list(MEMBER_ACTIONS)}
        self.runtime["member"] = {"username": username, "actions": list(MEMBER_ACTIONS)}

    def _record_admissions(self):
        for mode, info in self.releases.items():
            operation = str(uuid.uuid4())
            body = {"operation_id": operation, "proof": secrets.token_hex(32),
                    "instance_id": self.instance.instance_id, "study_id": info["study_id"],
                    "release_id": info["release_id"], "build_id": info["build_id"]}
            if mode != "anonymous":
                body["participant_code"] = ID_CODES[0]
            if mode == "password":
                body["password"] = self.participant_passwords[ID_CODES[0]]
            status, payload, _ = self.http.post_json("/v1/participant/sessions", body)
            if status != 200:
                raise KitError(f"{mode}: real admission returned {status}: {payload[:200].decode('utf-8', 'replace')}")
            admission = json.loads(payload)
            self.require(admission.get("session_id") and admission.get("token")
                         and admission.get("release_id") == info["release_id"]
                         and admission.get("instance_id") == self.instance.instance_id
                         and admission.get("config", {}).get("mode") == mode,
                         f"{mode}：真实 HTTP 准入创建会话并绑定本实例/发行",
                         {"session_id": admission.get("session_id")})
            rows = self.instance.session_rows(info["study_id"])
            self.require(len(rows) == 1 and _uuid_text(rows[0][0]) == admission["session_id"],
                         f"{mode}：准入会话写入真实数据库", {"sessions": len(rows)})
            info["admission_session_id"] = admission["session_id"]
            if mode != "anonymous":
                self.require(rows[0][1] == ID_CODES[0], f"{mode}：会话绑定名单账号", rows[0][1])
        self._record_wrong_credentials()

    def _record_wrong_credentials(self):
        info = self.releases["password"]
        body = {"operation_id": str(uuid.uuid4()), "proof": secrets.token_hex(32),
                "instance_id": self.instance.instance_id, "study_id": info["study_id"],
                "release_id": info["release_id"], "build_id": info["build_id"],
                "participant_code": ID_CODES[0], "password": "wrong-" + secrets.token_urlsafe(8)}
        status, payload, _ = self.http.post_json("/v1/participant/sessions", body)
        code = json.loads(payload).get("code") if payload[:1] == b"{" else None
        self.require(status == 403 and code == "admission_denied",
                     "错误口令被真实拒绝且不创建会话", {"status": status, "code": code})
        self.require(len(self.instance.session_rows(info["study_id"])) == 1,
                     "错误口令没有增加会话", {"sessions": len(self.instance.session_rows(info["study_id"]))})

    def _record_platform_negatives(self):
        """WN06 platform-side contracts against the real stored artifact."""
        info = self.releases["anonymous"]
        release_id = info["release_id"]
        record = self.instance.release_record(release_id)
        stored = self.instance.data / "artifacts" / record["path"]
        original = stored.read_bytes()
        try:
            tampered = bytearray(original)
            tampered[len(tampered) // 2] = (tampered[len(tampered) // 2] + 1) % 256
            stored.write_bytes(bytes(tampered))
            status, _, _ = self.http.get(f"/releases/{release_id}/artifact")
            self.require(status == 409, "WN06：存储完整包被篡改后授权下载失败关闭（409）", {"status": status})
            status, _, _ = self.http.get(f"/releases/{release_id}/artifact/artifact_manifest.json")
            self.require(status == 409, "WN06：篡改后 sidecar 下载失败关闭（409）", {"status": status})
            body = {"operation_id": str(uuid.uuid4()), "proof": secrets.token_hex(32),
                    "instance_id": self.instance.instance_id, "study_id": info["study_id"],
                    "release_id": release_id, "build_id": info["build_id"]}
            status, payload, _ = self.http.post_json("/v1/participant/sessions", body)
            code = json.loads(payload).get("code") if payload[:1] == b"{" else None
            self.require(status == 409 and code == "release_unavailable",
                         "WN06：篡改后准入在创建会话前失败关闭", {"status": status, "code": code})
            self.require(len(self.instance.session_rows(info["study_id"])) == 1,
                         "WN06：篡改后没有创建新会话")
        finally:
            stored.write_bytes(original)
        self.require(sha256_file(stored) == record["digest"], "WN06：恢复原始字节后摘要一致")
        status, payload, _ = self.http.get(f"/releases/{release_id}/artifact")
        self.require(status == 200 and sha256_file_bytes(payload) == record["digest"],
                     "WN06：恢复后同一发行可再次逐字节下载", {"status": status})
        self._record_upload_negatives()

    def _record_upload_negatives(self):
        info = self.releases["anonymous"]
        study_id = info["study_id"]
        descriptor = read_json(DESCRIPTOR)
        with zipfile.ZipFile(PROGRAM_ARCHIVE) as source:
            members = {item.filename: source.read(item.filename) for item in source.infolist()
                       if not item.filename.endswith("/")}
        dependency = descriptor["package"]["dependencies"][0]
        missing = {name: raw for name, raw in members.items()
                   if name != f'{descriptor["package"]["root"]}/{dependency}'}
        broken = self.evidence / "missing_dependency.zip"
        with zipfile.ZipFile(broken, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for name, raw in missing.items():
                out.writestr(name, raw)
        broken_descriptor = {**descriptor, "version": "synthetic-negative-missing",
                             "program_sha256": sha256_file(broken)}
        self.study_operation(study_id, [("op", "native"), ("descriptor", json.dumps(broken_descriptor))])
        broken_build = self.instance.build_record(study_id, "synthetic-negative-missing")
        status, payload, _ = self.http.post_multipart(
            f"/studies/{study_id}", [("op", "native_archive"), ("build_id", broken_build["id"])],
            "package", "missing_dependency.zip", broken.read_bytes())
        code = json.loads(payload).get("code") if payload[:1] == b"{" else None
        self.require(status == 422 and code == "missing_dependencies",
                     "WN06：缺少声明原生依赖的程序归档被真实拒绝", {"status": status, "code": code})
        stored = self.instance.build_record(study_id, "synthetic-negative-missing")
        self.require(not stored["package_path"], "WN06：被拒上传没有绑定程序包", stored["package_path"])

        wrong_archive = self.evidence / "wrong_platform.zip"
        with zipfile.ZipFile(wrong_archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
            for name, raw in missing.items():
                out.writestr(name, raw)
            out.writestr("GEP Synthetic Experiment/README.txt", b"synthetic wrong-platform probe")
        wrong = {**descriptor, "version": "synthetic-negative-platform", "platform": "macos_arm64",
                 "program_sha256": sha256_file(wrong_archive)}
        wrong.pop("package", None)
        self.study_operation(study_id, [("op", "native"), ("descriptor", json.dumps(wrong))])
        wrong_build = self.instance.build_record(study_id, "synthetic-negative-platform")
        status, payload, _ = self.http.post_multipart(
            f"/studies/{study_id}", [("op", "native_archive"), ("build_id", wrong_build["id"])],
            "package", "wrong_platform.zip", wrong_archive.read_bytes())
        code = json.loads(payload).get("code") if payload[:1] == b"{" else None
        self.require(status == 422 and code in ("reserved_path", "missing_binary", "unsafe_path",
                                                "missing_info_plist", "multiple_bundles", "invalid_archive"),
                     "WN06：错平台程序归档被真实拒绝", {"status": status, "code": code})

        grants = self.instance.db_rows(
            "select count(*) from core_grant grant join auth_user user on grant.user_id=user.id "
            "where user.username=? and grant.action='build.upload'", [MEMBER_USERNAME])[0][0]
        self.require(grants == 0, "WN06：受限成员没有下载权限（撤权前提）", {"build.upload grants": grants})
        status, _, _ = self.member.get(f"/releases/{info['release_id']}/artifact")
        self.require(status == 403, "WN06：缺少下载授权的成员被真实拒绝（403）", {"status": status})
        status, _, _ = self.http.get(f"/releases/{info['release_id']}/artifact")
        self.require(status == 200, "WN06：具备授权的账号仍可下载（对照）", {"status": status})

    def _write_kit_documents(self):
        lines = ["# GEP Windows x64 交付 kit（合成，真实平台发行）", "",
                 f"- kit 格式：{KIT_VERSION}",
                 "- 每个模式一个独立合成研究/发行，三者共用同一不可变 Windows x64 程序构建。",
                 "- 完整包与 sidecar 全部来自平台授权下载端点，未被本工具重新打包或改写。",
                 f"- 冻结 API：{self.runtime['api_url']}；Windows 侧经作用域隧道 "
                 f"{self.runtime['tunnel']['listen']} → 准备主机 {self.runtime['tunnel']['target']} 到达"
                 f"（方向：{self.runtime['tunnel']['direction']}，由准备主机建立；"
                 f"前置状态 {self.runtime['tunnel']['runtime_prerequisite']}）。",
                 "",
                 "## 交付内容",
                 ""]
        for mode, info in sorted(self.releases.items()):
            lines.append(f"- `{info['delivery']}`：{MODE_LABELS[mode]}（release {info['release_id']}，"
                         f"sha256 {info['artifact_sha256'][:16]}…）")
        lines += ["", "## 边界", "",
                  "- `integrity.json` 只是 kit 成员哈希清单（传输完整性），**不是签名**，也不代表代码签名。",
                  "- kit 内没有账号、口令、令牌或恢复证明；可用测试账号在私有产物中，不随参与者包分发。",
                  "- 平台侧完整性（发行外层摘要、成员清单、篡改失败关闭）由准备工具与严格门槛复核。",
                  "- 本地副本被篡改的 PCK 不会被程序运行时自动拒绝：运行时不做包完整性校验，这是已知限制。",
                  ""]
        (self.kit / "README.md").write_text("\n".join(lines), encoding="utf-8")
        self.runtime["kit_format"] = KIT_VERSION
        (self.kit / "releases.json").write_bytes(canonical_json(
            {"kit_format": KIT_VERSION, "instance_id": self.instance.instance_id,
             "api_url": self.runtime["api_url"], "modes": self.releases,
             "alternates": self.alternates,
             "program_sha256": self.runtime["expected"]["program_sha256"],
             "descriptor_sha256": self.runtime["expected"]["descriptor_sha256"],
             "note": "Recorded from the real platform lifecycle; the packages are the downloaded bytes."}))

    def _write_private_artifacts(self):
        self.private.mkdir(parents=True, exist_ok=True)
        accounts = self.private / "runtime_accounts.json"
        accounts.write_bytes(canonical_json(self.accounts))
        accounts.chmod(0o600)
        owner = self.private / "owner_credentials.json"
        owner.write_bytes(canonical_json({"format": OWNER_FORMAT, "username": OWNER_USERNAME,
                                          "password": self.instance.owner_password,
                                          "instance_id": self.instance.instance_id,
                                          "note": "Owner material for the isolated 03F instance; never copied to "
                                                  "the Windows test machine and never distributed."}))
        owner.chmod(0o600)
        self.runtime["private_accounts"] = "private/runtime_accounts.json"
        runtime = self.private / "runtime.json"
        runtime.write_bytes(canonical_json(self.runtime))
        runtime.chmod(0o600)
        self.require(stat.S_IMODE(accounts.stat().st_mode) == 0o600, "私有账号产物权限为 0600")
        self.require(self.kit not in accounts.parents and self.kit not in owner.parents,
                     "私有账号/口令产物不在可分发 kit 内")
        return accounts

    def _launcher_preamble(self):
        """The shared, relocatable launcher preamble: own paths, Python prerequisite."""
        return [
            "@echo off",
            "setlocal",
            "set HERE=%~dp0",
            "set KIT=%HERE%..",
            "set HARNESS=%HERE%harness\\windows_native_harness.py",
            "set RUNTIME=%HERE%runtime.json",
            "set ACCOUNTS=%HERE%..\\..\\private\\runtime_accounts.json",
            "if not exist \"%HARNESS%\" ( echo kit 内 harness 缺失：%HARNESS%，已中止 & exit /b 2 )",
            "if not exist \"%RUNTIME%\" ( echo kit 内 runtime.json 缺失，已中止 & exit /b 2 )",
            "if not exist \"%ACCOUNTS%\" ( echo 缺少私有账号产物 runtime_accounts.json，已中止 & exit /b 2 )",
            "rem Python 3.12 是 harness 的运行前置（纯标准库）；缺失时明确失败，不静默跳过。",
            "set \"PY=\"",
            "py -3.12 -c \"import sys;raise SystemExit(0 if sys.version_info[:2]>=(3,12) else 1)\" >nul 2>nul",
            "if not errorlevel 1 set \"PY=py -3.12\"",
            "if not defined PY (",
            "  python -c \"import sys;raise SystemExit(0 if sys.version_info[:2]>=(3,12) else 1)\" >nul 2>nul",
            "  if not errorlevel 1 set \"PY=python\"",
            ")",
            "if not defined PY ( echo 缺少 Python 3.12 运行时前置：请在本机安装 Python 3.12 后重试；不要下载未知来源依赖。 & exit /b 2 )",
        ]

    def _write_operator_material(self):
        operator = self.kit / "operator"
        (operator / "harness").mkdir(parents=True, exist_ok=True)
        shutil.copy2(HARNESS, self.kit / KIT_HARNESS)
        self.require((self.kit / KIT_HARNESS).is_file(), "kit 内自带可搬移 harness", KIT_HARNESS)
        (operator / "runtime.json").write_bytes(canonical_json(self.runtime))
        tunnel = self.runtime["tunnel"]
        listen = tunnel["listen_port"]
        (operator / "tunnel.cmd").write_text(
            "@echo off\r\n"
            "rem 作用域隧道自检（不是人工配置步骤）：准备主机在 --serve-runtime 时自动建立反向 SSH 连接，\r\n"
            f"rem 把 Windows 回环 127.0.0.1:{listen} 转发到准备主机的 127.0.0.1:{tunnel['target_port']}。\r\n"
            "rem 失败时不要修改防火墙、DNS 或证书信任；按提示在准备主机重新建立连接即可。\r\n"
            f"set LISTEN={listen}\r\n"
            "powershell -NoProfile -Command \"try{$c=New-Object Net.Sockets.TcpClient;"
            "$c.Connect('127.0.0.1',%LISTEN%);$c.Close();exit 0}catch{exit 2}\"\r\n"
            "if errorlevel 1 (\r\n"
            f"  echo 外部前置未满足：作用域隧道 127.0.0.1:%LISTEN% 不可达。\r\n"
            "  echo 准备主机需要先运行：tools/phase03_verify_windows_native.py --serve-runtime（自动建立反向隧道）。\r\n"
            "  echo 本机不需要输入命令，也不要改防火墙/证书信任。\r\n"
            "  exit /b 2\r\n"
            ")\r\n"
            f"echo 作用域隧道 127.0.0.1:%LISTEN% 可达。\r\n",
            encoding="utf-8")
        runner = self._launcher_preamble() + [
            "set RUN=%HERE%..\\..\\windows-run",
            "%PY% \"%HARNESS%\" --run --kit \"%KIT%\" --runtime \"%RUNTIME%\" --accounts \"%ACCOUNTS%\" "
            "--run-root \"%RUN%\" --json-out \"%RUN%\\run.json\"",
            "set CODE=%ERRORLEVEL%",
            "echo harness 退出码 %CODE%",
            "pause",
            "exit /b %CODE%",
            "",
        ]
        (operator / "run-windows-checks.cmd").write_text("\r\n".join(runner), encoding="utf-8")
        for mode in MODES:
            designer = self._launcher_preamble() + [
                "set RUN=%HERE%..\\..\\designer-run",
                f"%PY% \"%HARNESS%\" --designer-launch --mode {mode} --kit \"%KIT%\" --runtime \"%RUNTIME%\" "
                "--accounts \"%ACCOUNTS%\" --run-root \"%RUN%\"",
                "set CODE=%ERRORLEVEL%",
                "pause",
                "exit /b %CODE%",
                "",
            ]
            (operator / f"designer-mode-{mode}.cmd").write_text("\r\n".join(designer), encoding="utf-8")
        self.record(True, "kit 内提供可搬移的测试启动器与 Python 前置检测（无需人工输入命令）",
                    {"launchers": ["run-windows-checks.cmd", "designer-mode-*.cmd", "tunnel.cmd"],
                     "harness": KIT_HARNESS, "runtime_prerequisite": tunnel["runtime_prerequisite"]})

    def check_relocatable_launchers(self):
        """Copy only the declared output to a fresh directory and prove it works.

        A launcher that points at a sibling ``tools/`` directory would break on the
        Windows machine, so the complete operator tooling must travel inside the
        kit; the frozen endpoint contract is re-read from the copied runtime file.
        """
        fresh = self.root / "relocatable-check"
        if fresh.exists():
            shutil.rmtree(fresh)
        shutil.copytree(self.kit, fresh / "kit")
        (fresh / "private").mkdir(parents=True)
        shutil.copy2(self.private / "runtime_accounts.json", fresh / "private" / "runtime_accounts.json")
        targets = [fresh / "kit" / KIT_HARNESS, fresh / "kit" / "operator" / "runtime.json",
                   fresh / "private" / "runtime_accounts.json"]
        missing = [display_path(path) for path in targets if not path.is_file()]
        self.require(not missing, "复制 kit+私有账号到全新目录后启动器目标齐全", missing or None)
        runtime = read_json(fresh / "kit" / "operator" / "runtime.json")
        tunnel = runtime.get("tunnel") or {}
        listen, target = int(tunnel.get("listen_port") or 0), int(tunnel.get("target_port") or 0)
        self.require(listen == int(runtime.get("port") or 0) + 100 and target == int(runtime.get("port") or 0),
                     "隧道端点契约与冻结端口一致（Windows listen=port+100 → 准备机 port）",
                     {"listen": listen, "target": target, "port": runtime.get("port")})
        check = (fresh / "kit" / "operator" / "tunnel.cmd").read_text(encoding="utf-8")
        self.require(f"set LISTEN={listen}" in check and f"127.0.0.1:{target}" in check,
                     "tunnel.cmd 自检使用同一冻结端点契约", {"listen": listen, "target": target})
        runner = (fresh / "kit" / "operator" / "run-windows-checks.cmd").read_text(encoding="utf-8")
        self.require("%HERE%harness\\windows_native_harness.py" in runner and "..\\..\\tools" not in runner,
                     "启动器只引用 kit 内相对路径（没有兄弟 tools 目录）")
        self.require("Python 3.12" in runner, "启动器显式检测 Python 运行时前置")
        self.require("sys.version_info[:2]>=(3,12)" in runner,
                     "启动器 Python 前置校验真实版本（不是只 import sys）")
        return fresh

    def check_no_secrets(self, paths):
        findings = []
        for path in paths:
            candidates = [item for item in Path(path).rglob("*") if item.is_file()] if Path(path).is_dir() else [Path(path)]
            for candidate in candidates:
                if candidate.name in ("runtime_accounts.json", "owner_credentials.json"):
                    continue
                hits = scan_secrets(candidate)
                if hits:
                    findings.append({"path": display_path(candidate), "patterns": len(hits)})
        self.require(not findings, "kit 与可分发文件不含凭据/密钥样式内容", findings or None)

    def kit_members(self):
        members = {}
        for path in sorted(self.kit.rglob("*")):
            if not path.is_file() or path.name == "integrity.json":
                continue
            members[path.relative_to(self.kit).as_posix()] = {"sha256": sha256_file(path),
                                                             "size": path.stat().st_size}
        return members

    def write_integrity(self):
        sidecar = {"format": KIT_VERSION, "app": "GEP Synthetic Experiment",
                   "program_sha256": self.runtime["expected"]["program_sha256"],
                   "note": "Integrity list of the kit members (transport integrity). Not a signature.",
                   "members": self.kit_members()}
        (self.kit / "integrity.json").write_bytes(canonical_json(sidecar))
        return sidecar


def sha256_file_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def build_kit(root, record, require, quiet=False, log=print):
    """Run the real lifecycle once and return the preparation summary."""
    builder = KitBuilder(Path(root), record, require, quiet=quiet, log=log)
    summary = builder.prepare()
    sidecar = builder.write_integrity()
    builder.check_no_secrets([builder.kit])
    summary["integrity"] = sidecar
    summary["checks"] = {"kit_members": len(sidecar["members"])}
    return summary


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Build the 03F Windows readiness kit from the real lifecycle")
    parser.add_argument("--root", default=None, help="evidence root (defaults to a new stamp under p0307wr)")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else RUN_ROOT / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    root.mkdir(parents=True, exist_ok=True)

    def record(ok, label, detail=None):
        print(("ok   " if ok else "FAIL ") + label + (f" :: {detail}" if detail is not None else ""))
        return ok

    def require(ok, label, detail=None):
        if not record(ok, label, detail):
            raise KitError(label)
        return True

    try:
        summary = build_kit(root, record, require)
    except KitError as error:
        print(f"PHASE03_WINDOWS_KIT_FAILED {root} :: {error}")
        return 1
    print(f"PHASE03_WINDOWS_KIT_OK {root} :: modes={sorted(summary['releases'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
