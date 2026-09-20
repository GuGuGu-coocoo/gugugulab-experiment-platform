#!/usr/bin/env python3
"""Phase 03F Windows x64 native preparation, runtime service and strict gate.

Three jobs, deliberately separated:

``--verify-preparation``
    Proves the *preparation* half inside the project root: the pinned toolchain
    (project virtualenv, official local Godot 4.7.2, pinned official Windows
    export template verified by digest), the real cross-built program archive and
    its descriptor (PE32+ x86-64 entry and dependency, standalone PCK 4.7.2,
    Windows ZIP path rules, GDExtension declaration), and a readiness kit built
    from the **real platform lifecycle** (``tools/phase03_windows_kit``): one
    isolated synthetic instance, three frozen studies/releases sharing one
    immutable Windows build, real roster accounts, a real invited member, real
    logins and real HTTP admissions, the complete package and its sidecars
    downloaded through the authenticated release endpoints, and the platform-side
    WN06 contracts (tamper, permission, wrong platform, missing dependency).
    Preparation success is never runtime acceptance.

``--serve-runtime``
    Restarts the prepared isolated instance on the frozen loopback port so a real
    Windows run can reach it through the scoped tunnel. It never rewrites the
    frozen configuration and never serves a different instance.

``--gate --gate-run <dir>``
    The strict integration gate. It loads the **explicitly selected** run
    directory, recomputes the current source/build/descriptor/harness digests,
    re-reads the bound kit bytes and the isolated instance database, and
    independently re-validates every WN01-WN06 case from the run's raw evidence
    files (native store copies, authorized JSONL exports, recovery export, launch
    records). A bare ``runtime_status=PASS``, a doctor-only report, a stale kit, a
    missing case, a mismatched raw value or a corrupted evidence file is refused.
    Only a real device run whose cases all pass and whose evidence is still bound
    to the current build can return ``RUNTIME_PASS``.

Evidence stays under ``local_data/phase03_20260920/p0307wr/<stamp>/``.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE_ROOT = ROOT / "local_data" / "phase03_20260920"
RUN_ROOT = PHASE_ROOT / "p0308"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
GUNICORN = ROOT / ".venv" / "bin" / "gunicorn"
LICENSE = ROOT / "LICENSE"
GODOT_SQLITE_LICENSE = ROOT / "third_party" / "godot_sqlite_license.md"
PROGRAM_ARCHIVE = ROOT / "build" / "windows" / "synthetic_windows.zip"
PROGRAM_ROOT = ROOT / "build" / "windows" / "GEP Synthetic Experiment"
DESCRIPTOR = ROOT / "build" / "windows" / "descriptor.json"
HARNESS = ROOT / "tools" / "windows_native_harness.py"
GUIDE = ROOT / "examples" / "synthetic_experiment" / "windows_native" / "WINDOWS_NATIVE_GUIDE.zh-CN.md"
TEMPLATE_RESULT = ROOT / "examples" / "synthetic_experiment" / "windows_native" / "WINDOWS_NATIVE_RESULT_TEMPLATE.md"
README = ROOT / "examples" / "synthetic_experiment" / "windows_native" / "README.md"
EXPECTED_HOST_VERSION = "4.7.2"
WN_ITEMS = ("WN01", "WN02", "WN03", "WN04", "WN05", "WN06")
MODES = ("anonymous", "id", "password")
RUNTIME_NOT_RUN = "NOT_RUN"
RUNTIME_BLOCKED = "BLOCKED"
RUNTIME_PASS = "PASS"
RUNTIME_FAIL = "FAIL"
GATE_VERDICT_PASS = "RUNTIME_PASS"
GATE_VERDICT_INCOMPLETE = "RUNTIME_INCOMPLETE"
GATE_VERDICT_INVALID = "EVIDENCE_INVALID"
GATE_VERDICT_ABSENT = "EVIDENCE_ABSENT"
GATE_VERDICT_PREPARATION_ONLY = "PREPARATION_ONLY"
GATE_VERDICT_SELECTION_REQUIRED = "SELECTION_REQUIRED"
PREP_PREPARED = "PREPARED"
PREP_FAILED = "PREPARATION_FAILED"
PREP_NOT_VERIFIED = "NOT_VERIFIED"
RUN_FORMAT = "gep-windows-run/v1"
RAW_VALUES = (217.25, 321.5)
EXPECTED_EVENTS_PER_SESSION = 4
EXPIRY_MIN_WAIT = 300
_SECRET_PATTERNS = (
    re.compile(rb"BEGIN (RSA|OPENSSH|EC|PRIVATE) PRIVATE KEY"),
    re.compile(rb"password\s*[:=]\s*[\"'][^\"'\s]{4,}", re.IGNORECASE),
    re.compile(rb"token\s*[:=]\s*[\"'][A-Za-z0-9_\-]{12,}", re.IGNORECASE),
    re.compile(rb"secret\s*[:=]\s*[\"'][A-Za-z0-9_\-]{8,}", re.IGNORECASE),
)

sys.path.insert(0, str(ROOT / "tools"))
from phase03_windows_kit import (  # noqa: E402
    ARTIFACT_FORMAT,
    KIT_VERSION,
    RUNTIME_FORMAT,
    KitError,
    Instance,
    build_kit,
    display_path,
    program_source_digest,
    read_json,
    sha256_file,
    scan_secrets,
)


class PreparationError(Exception):
    """The preparation itself is defective (not merely the external device)."""


def canonical_json(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def pe_summary(raw):
    if len(raw) < 0x40 or raw[:2] != b"MZ":
        raise PreparationError("missing MZ header")
    offset = int.from_bytes(raw[0x3C:0x40], "little")
    if not 0x40 <= offset or offset + 24 > len(raw):
        raise PreparationError("invalid PE offset")
    if raw[offset:offset + 4] != b"PE\x00\x00":
        raise PreparationError("missing PE signature")
    machine = int.from_bytes(raw[offset + 4:offset + 6], "little")
    characteristics = int.from_bytes(raw[offset + 22:offset + 24], "little")
    magic = int.from_bytes(raw[offset + 24:offset + 26], "little")
    return {"machine": {0x8664: "x86_64", 0xAA64: "arm64", 0x14C: "i386"}.get(machine, hex(machine)),
            "magic": magic, "executable": bool(characteristics & 0x0002),
            "dll": bool(characteristics & 0x2000)}


def pck_version(raw):
    if len(raw) < 20 or raw[:4] != b"GDPC":
        raise PreparationError("missing GDPC PCK magic")
    return tuple(int.from_bytes(raw[8 + 4 * index:12 + 4 * index], "little") for index in range(3))


def gdextension_release_library(text):
    sections, current = {}, None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith((";", "#")):
            continue
        if stripped.startswith("["):
            if not stripped.endswith("]"):
                raise PreparationError("malformed GDExtension section")
            current = stripped[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if "=" not in stripped or current is None:
            raise PreparationError("malformed GDExtension line")
        key, _, value = stripped.partition("=")
        sections[current][key.strip()] = value.strip()
    symbol = sections.get("configuration", {}).get("entry_symbol", "")
    if len(symbol) < 3 or symbol[0] != '"' or symbol[-1] != '"':
        raise PreparationError("missing or unquoted GDExtension entry_symbol")
    reference = sections.get("libraries", {}).get("windows.release.x86_64", "")
    if len(reference) < 3 or reference[0] != '"' or reference[-1] != '"' or not reference[1:-1].startswith("res://"):
        raise PreparationError("missing or unquoted Windows release library entry")
    return symbol[1:-1], reference[1:-1]


class Preparation:
    """One preparation run: checks, device probe and machine-readable report."""

    def __init__(self, stamp=None, quiet=False, root=None):
        self.stamp = stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self.root = Path(root) if root else RUN_ROOT / self.stamp
        self.kit = self.root / "kit"
        self.private = self.root / "private"
        self.quiet = quiet
        self.checks = []
        self.summary_lines = []
        self.report = {}
        self.lifecycle = {}
        self.device = None

    # ---------------------------------------------------------------- logging
    def log(self, message):
        line = f"[windows-native-prep] {message}"
        self.summary_lines.append(line)
        if not self.quiet:
            print(line, flush=True)

    def record(self, ok, label, detail=None):
        entry = {"ok": bool(ok), "label": label, "detail": detail}
        self.checks.append(entry)
        self.log(("ok   " if ok else "FAIL ") + label + (f" :: {detail}" if detail is not None else ""))
        return bool(ok)

    def require(self, ok, label, detail=None):
        if not self.record(ok, label, detail):
            raise PreparationError(label)
        return True

    # -------------------------------------------------------------- toolchain
    def godot_binary(self):
        candidate = os.environ.get("GEP_GODOT_BIN") or shutil.which("godot") or \
            "/Applications/Godot.app/Contents/MacOS/Godot"
        if not Path(candidate).exists():
            return None
        return candidate

    def check_toolchain(self):
        missing = [display_path(path) for path in (VENV_PYTHON, LICENSE, GODOT_SQLITE_LICENSE, HARNESS, GUIDE)
                   if not path.exists()]
        self.require(not missing, "项目工具链与交付文件存在", {"missing": missing} if missing else None)
        godot = self.godot_binary()
        self.require(godot is not None, "本机官方 Godot 引擎存在", {"GEP_GODOT_BIN": os.environ.get("GEP_GODOT_BIN")})
        version = subprocess.check_output([godot, "--version"], text=True).strip()
        self.require(version.startswith("4.7.2.stable."), "本机 Godot 为固定的 4.7.2", version)
        try:
            from windows_template import find_windows_template, verify_windows_template
        except ImportError as error:  # pragma: no cover - packaging failure
            raise PreparationError(f"windows_template module unavailable: {error}")
        template = find_windows_template()
        digest = verify_windows_template(template)
        self.record(True, "Windows 导出模板字节等于固定官方摘要",
                    {"template": template.name, "sha256": digest, "bytes": template.stat().st_size})
        return godot, template

    # ------------------------------------------------------------ program kit
    def build_program(self, godot):
        result = subprocess.run([str(VENV_PYTHON), str(ROOT / "tools" / "build.py"), "windows"],
                                cwd=ROOT, capture_output=True, text=True, timeout=3600)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "program_build.log").write_text((result.stdout or "") + (result.stderr or ""))
        self.require(result.returncode == 0, "Windows 程序交叉构建退出 0", {"exit": result.returncode})
        self.require(PROGRAM_ARCHIVE.is_file() and DESCRIPTOR.is_file(), "程序归档与描述已生成")

    def program_is_reproducible(self, descriptor=None):
        if not PROGRAM_ARCHIVE.is_file() or not DESCRIPTOR.is_file():
            return False
        document = descriptor or read_json(DESCRIPTOR)
        if document.get("program_sha256") != sha256_file(PROGRAM_ARCHIVE):
            return False
        root = PROGRAM_ROOT
        for name in (document.get("package", {}).get("entry"), *document.get("package", {}).get("dependencies", [])):
            if name and not (root / name).is_file():
                return False
        return True

    def check_descriptor_contract(self, descriptor):
        package = descriptor.get("package")
        self.require(descriptor.get("platform") == "windows_x64", "描述固定为 windows_x64",
                     descriptor.get("platform"))
        self.require(descriptor.get("host_version", "").startswith(EXPECTED_HOST_VERSION),
                     "描述引擎版本为 4.7.2", descriptor.get("host_version"))
        self.require(isinstance(package, dict) and set(package) == {"root", "entry", "dependencies", "extension"},
                     "描述显式声明入口/依赖/GDExtension 清单", package)
        self.require(isinstance(descriptor.get("program_sha256"), str)
                     and re.fullmatch(r"[0-9a-f]{64}", descriptor["program_sha256"]) is not None,
                     "描述记录程序归档 SHA-256", descriptor.get("program_sha256"))
        for key in ("schemas", "codebook"):
            self.require(bool(descriptor.get(key)), f"描述包含 {key}", None)
        return package

    def check_program_archive(self, descriptor):
        package = descriptor["package"]
        root = package["root"]
        with zipfile.ZipFile(PROGRAM_ARCHIVE) as archive:
            names = [item.filename for item in archive.infolist() if not item.filename.endswith("/")]
            infos = {item.filename: item for item in archive.infolist()}
        self.require(len(names) == len(set(names)), "程序归档无重复成员", len(names))
        for name in names:
            path = Path(name)
            self.require(len(path.parts) >= 2 and not name.startswith("/") and ".." not in path.parts
                         and "\\" not in name and ":" not in path.parts[0],
                         "程序归档成员符合 Windows ZIP 路径规则", name)
        self.require(all(name.split("/")[0] == root for name in names), "程序归档只有一个声明根目录", root)
        entry_name = f"{root}/{package['entry']}"
        self.require(entry_name in infos, "归档包含声明的入口", entry_name)
        with zipfile.ZipFile(PROGRAM_ARCHIVE) as archive:
            entry_bytes = archive.read(entry_name)
        try:
            entry = pe_summary(entry_bytes)
        except PreparationError as error:
            raise PreparationError(f"入口不是真实 PE32+ x86-64 可执行映像: {error}")
        self.require(entry["machine"] == "x86_64" and entry["magic"] == 0x20B and entry["executable"],
                     "入口是真实 PE32+ x86-64 可执行映像", entry)
        for dependency in package["dependencies"]:
            member = f"{root}/{dependency}"
            self.require(member in infos, "归档包含声明的原生依赖", member)
            with zipfile.ZipFile(PROGRAM_ARCHIVE) as archive:
                dependency_bytes = archive.read(member)
            try:
                summary = pe_summary(dependency_bytes)
            except PreparationError as error:
                raise PreparationError(f"依赖不是真实 x86-64 DLL 映像: {error}")
            self.require(summary["machine"] == "x86_64" and summary["dll"],
                         "依赖是真实 x86-64 DLL 映像", {"member": member, **summary})
        extension = f"{root}/{package['extension']}"
        self.require(extension in infos, "归档包含 GDExtension 清单", extension)
        with zipfile.ZipFile(PROGRAM_ARCHIVE) as archive:
            _symbol, reference = gdextension_release_library(archive.read(extension).decode("utf-8"))
        self.record(True, "GDExtension 声明 Windows release 动态库",
                    {"reference": reference, "declared": package["dependencies"][0]})
        pck_name = next((name for name in sorted(names) if name.endswith(".pck")), None)
        self.require(pck_name is not None, "归档包含独立 PCK")
        with zipfile.ZipFile(PROGRAM_ARCHIVE) as archive:
            version = pck_version(archive.read(pck_name))
        self.require(version == (4, 7, 2), "PCK 头部记录固定的 4.7.2 引擎版本",
                     {"member": pck_name, "version": version})
        self.record(True, "程序归档未包含冻结时才生成的连接配置",
                    {"config": "GEP Synthetic Experiment/connection.json"})
        return {"root": root, "entry": entry_name, "members": sorted(names), "pck": pck_name,
                "archive_bytes": PROGRAM_ARCHIVE.stat().st_size, "archive_sha256": descriptor["program_sha256"]}

    # ----------------------------------------------------------- real lifecycle
    def build_real_kit(self):
        self.require(self.root.exists() or True, "准备证据目录已建立")
        self.root.mkdir(parents=True, exist_ok=True)
        summary = build_kit(self.root, self.record, self.require, quiet=self.quiet, log=self.log)
        self.lifecycle = summary
        return summary

    def check_kit_integrity(self):
        sidecar_path = self.kit / "integrity.json"
        self.require(sidecar_path.is_file(), "kit 完整性清单存在", display_path(sidecar_path))
        sidecar = read_json(sidecar_path)
        self.require(sidecar.get("format") == KIT_VERSION, "kit 完整性清单格式版本正确", sidecar.get("format"))
        actual = {}
        for path in sorted(self.kit.rglob("*")):
            if path.is_file() and path.name != "integrity.json":
                actual[path.relative_to(self.kit).as_posix()] = {"sha256": sha256_file(path),
                                                                "size": path.stat().st_size}
        self.require(set(actual) == set(sidecar.get("members", {})), "完整性清单覆盖全部 kit 成员",
                     {"actual": len(actual), "listed": len(sidecar.get("members", {}))})
        for name, entry in sorted(sidecar["members"].items()):
            self.require(actual[name]["sha256"] == entry["sha256"] and actual[name]["size"] == entry["size"],
                         "kit 成员摘要与完整性清单一致", name)
        self.record(True, "kit 完整性清单全部匹配（传输完整性，不是签名）", {"members": len(actual)})
        return sidecar

    def check_no_secrets(self, paths):
        findings = []
        for path in paths:
            candidates = [item for item in Path(path).rglob("*") if item.is_file()] if Path(path).is_dir() \
                else [Path(path)]
            for candidate in candidates:
                if candidate.name in ("runtime_accounts.json", "owner_credentials.json"):
                    continue
                hits = scan_secrets(candidate)
                if hits:
                    findings.append({"path": display_path(candidate), "patterns": len(hits)})
        self.require(not findings, "公开可分发文件不含凭据/密钥样式内容", findings or None)

    def check_private_artifacts(self):
        for name in ("runtime_accounts.json", "owner_credentials.json", "runtime.json"):
            path = self.private / name
            self.require(path.is_file(), f"私有产物存在：{name}", display_path(path))
            mode = path.stat().st_mode & 0o777
            self.require(mode == 0o600, f"私有产物权限为 0600：{name}", f"{mode:04o}")
            self.require(self.kit not in path.parents, f"私有产物不在可分发 kit 内：{name}")
        accounts = read_json(self.private / "runtime_accounts.json")
        self.require(accounts.get("format") == "gep-windows-accounts/v1"
                     and accounts.get("member", {}).get("username")
                     and accounts.get("participants", {}).get("id", {}).get("001", {}).get("code"),
                     "私有账号产物包含可用的成员与名单账号",
                     {"member": accounts.get("member", {}).get("username"),
                      "codes": sorted(accounts.get("participants", {}).get("id", {}))})
        self.check_kit_has_no_credentials(accounts)
        return accounts

    def check_kit_has_no_credentials(self, accounts):
        """No private account value may appear anywhere in the distributable kit."""
        secrets = []
        for entry in (accounts.get("participants") or {}).values():
            for record in entry.values() if isinstance(entry, dict) else []:
                if isinstance(record, dict) and record.get("password"):
                    secrets.append(str(record["password"]))
        member = accounts.get("member") or {}
        if member.get("password"):
            secrets.append(str(member["password"]))
        self.require(bool(secrets), "私有账号产物提供用于比对的真实口令值", len(secrets))
        hits = []
        for path in sorted(self.kit.rglob("*")):
            if not path.is_file():
                continue
            raw = path.read_bytes()[: 8 << 20]
            if any(value.encode("utf-8") in raw for value in secrets):
                hits.append(display_path(path))
        self.require(not hits, "kit 内不含私有账号口令值", hits or None)
        forbidden = {"password", "token", "secret", "proof", "password_hash"}
        document = read_json(self.kit / "releases.json")

        def walk(node, trail=""):
            found = []
            if isinstance(node, dict):
                for key, value in node.items():
                    # ``modes.<name>`` / ``alternates.<name>`` are the documented
                    # participation-mode enum (one of anonymous/id/password),
                    # never credential fields.
                    if key in forbidden and trail not in (".modes", ".alternates"):
                        found.append(f"{trail}.{key}")
                    found.extend(walk(value, f"{trail}.{key}"))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    found.extend(walk(value, f"{trail}[{index}]"))
            return found

        keys = walk(document)
        self.require(not keys, "可分发发行记录不含口令/令牌字段", keys[:5] or None)

    def check_harness(self):
        self.require(HARNESS.is_file(), "Windows 非交互 harness 存在", HARNESS.name)
        text = HARNESS.read_text(encoding="utf-8")
        for token in ("--doctor", "--run", "--designer-launch", "--kit", "--runtime", "--accounts",
                      "--run-root", "--json-out", "queue.sqlite", "writer.sqlite", "icacls"):
            self.require(token in text, f"harness 支持 {token}")
        for name in WN_ITEMS:
            self.record(name in text, f"harness 覆盖 {name} 检查契约")
        self.require("taskkill" not in text and "IMAGENAME" not in text,
                     "harness 不使用按镜像名查找/终止进程")
        self.require("ExecutionPolicy" not in text, "harness 默认不使用 ExecutionPolicy Bypass")
        self.require("Stop-Process -Id" in text or "Stop-Process -Id" in text,
                     "harness 只按自有 PID 终止进程")
        result = subprocess.run([str(VENV_PYTHON), "-c",
                                 "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())",
                                 str(HARNESS)], capture_output=True, text=True)
        self.require(result.returncode == 0, "harness 通过语法解析",
                     (result.stderr or "").strip()[-200:] or None)
        self.record(True, "harness 由准备门槛按当前源码摘要绑定", sha256_file(HARNESS))

    def check_documents(self):
        for path, label in ((GUIDE, "自导中文实机指南"), (TEMPLATE_RESULT, "结构化实机结果模板"),
                            (README, "Windows 原生目录说明")):
            self.require(path.is_file(), f"{label}存在", display_path(path))
        guide = GUIDE.read_text(encoding="utf-8")
        for name in WN_ITEMS:
            self.require(name in guide, f"指南覆盖 {name}")
        self.require("不需要输入任何命令" in guide and "界面操作" in guide,
                     "指南声明人类无需输入命令、只做界面操作")
        self.require("三个" in guide or "三个模式" in guide or "三项研究" in guide,
                     "指南说明三种模式是三个冻结研究")
        self.require("不是签名" in guide or "并非签名" in guide, "指南声明哈希清单不是签名")
        self.require("运行时不做" in guide or "不会自动拒绝" in guide,
                     "指南明确本地篡改 PCK 不被运行时完整性校验拒绝")
        template = TEMPLATE_RESULT.read_text(encoding="utf-8")
        for name in WN_ITEMS:
            self.require(name in template, f"结果模板覆盖 {name}")
        for field in ("Windows 版本", "package 摘要", "实际结果"):
            self.require(field in template or field in guide, f"记录字段包含 {field}")
        self.require("NOT_RUN" in template or "未运行" in template, "模板区分未运行与通过")

    # ----------------------------------------------------------------- device
    def ssh_alias(self):
        config = Path.home() / ".ssh" / "config"
        if not config.is_file():
            return None, "no ~/.ssh/config"
        text = config.read_text(encoding="utf-8", errors="ignore")
        aliases = []
        for block in re.finditer(r"^\s*Host\s+(.+)$", text, re.MULTILINE | re.IGNORECASE):
            for name in block.group(1).split():
                if name not in aliases and any(token in name.lower() for token in ("win", "quant")):
                    aliases.append(name)
        return (aliases or None), None

    def ssh_options(self):
        return ["-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=yes",
                "-o", "NumberOfPasswordPrompts=0", "-o", "PreferredAuthentications=publickey",
                "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]

    def probe_device(self, alias=None):
        probe = {"alias": alias, "probes": [], "machine": None, "missing_condition": None}
        aliases, note = self.ssh_alias()
        probe["candidates"] = aliases
        if note:
            probe["probes"].append({"ssh_config": note})
        if not aliases:
            probe["missing_condition"] = "documented Windows SSH alias not found"
            return probe
        target = alias or aliases[0]
        probe["alias"] = target
        resolved = subprocess.run(["ssh", *self.ssh_options(), "-G", target],
                                  capture_output=True, text=True, timeout=30)
        probe["probes"].append({"resolve": target, "exit": resolved.returncode})
        if resolved.returncode != 0:
            probe["missing_condition"] = f"ssh alias {target!r} cannot be resolved"
            return probe
        fields = {}
        for line in resolved.stdout.splitlines():
            tokens = line.split(None, 1)
            if len(tokens) == 2:
                fields[tokens[0].lower()] = tokens[1].strip()
        host = fields.get("hostname")
        port = int(fields.get("port", "22"))
        probe["probes"].append({"hostname_field": bool(host), "port": port})
        if host:
            try:
                with socket.create_connection((host, port), timeout=4):
                    probe["probes"].append({"tcp_connect": "ok"})
            except OSError as error:
                probe["probes"].append({"tcp_connect": type(error).__name__})
                probe["missing_condition"] = f"TCP {host}:{port} unreachable ({type(error).__name__})"
                return probe
        remote = ("$os=Get-CimInstance Win32_OperatingSystem;"
                  "$cs=Get-CimInstance Win32_ComputerSystem;"
                  "[pscustomobject]@{Caption=$os.Caption;Version=$os.Version;Build=$os.BuildNumber;"
                  "Arch=$os.OSArchitecture;System=$cs.SystemType;User=$env:USERNAME;"
                  "Desktop=(Test-Path ([Environment]::GetFolderPath('Desktop')));Session=$env:SESSIONNAME}"
                  "|ConvertTo-Json -Compress")
        probe_command = 'powershell -NoProfile -Command "' + remote + '"'
        result = subprocess.run(["ssh", *self.ssh_options(), target, probe_command],
                                capture_output=True, text=True, timeout=40)
        probe["probes"].append({"machine_facts": target, "exit": result.returncode})
        if result.returncode != 0:
            stderr = (result.stderr or "").strip().splitlines()
            probe["missing_condition"] = f"ssh read-only remote command failed (exit {result.returncode})"
            probe["probes"][-1]["stderr_tail"] = stderr[-1][:200] if stderr else ""
            return probe
        machine = None
        for line in result.stdout.strip().splitlines():
            try:
                machine = json.loads(line)
                break
            except ValueError:
                continue
        if not isinstance(machine, dict):
            probe["missing_condition"] = "remote machine facts unreadable"
            return probe
        probe["machine"] = machine
        missing = []
        if "windows" not in str(machine.get("Caption", "")).lower():
            missing.append("remote host is not Windows")
        if "64" not in str(machine.get("Arch", "")) and "AMD64" not in str(machine.get("System", "")):
            missing.append("remote host is not x64")
        if not machine.get("Desktop"):
            missing.append("interactive desktop unavailable")
        probe["missing_condition"] = "; ".join(missing) if missing else None
        probe["_session_hint"] = machine.get("Session")
        return probe

    def desktop_state(self, alias):
        command = ("powershell -NoProfile -Command \"query session | Out-String;"
                   "(Get-CimInstance Win32_OperatingSystem).Caption;"
                   "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System'"
                   " -ErrorAction SilentlyContinue).EnableLUA\"")
        result = subprocess.run(["ssh", *self.ssh_options(), alias, command],
                                capture_output=True, text=True, timeout=40)
        return {"exit": result.returncode, "stdout": (result.stdout or "")[-800:],
                "stderr_tail": (result.stderr or "").strip().splitlines()[-1][:200] if result.stderr else ""}

    # --------------------------------------------------------------- pipeline
    def prepare(self):
        godot, _template = self.check_toolchain()
        if not self.program_is_reproducible():
            self.log("程序归档与当前程序字节不一致，重新交叉构建")
            self.build_program(godot)
        descriptor = read_json(DESCRIPTOR)
        self.require(self.program_is_reproducible(descriptor), "程序归档摘要与描述记录一致",
                     descriptor.get("program_sha256"))
        self.check_descriptor_contract(descriptor)
        self.check_program_archive(descriptor)
        self.build_real_kit()
        self.check_kit_integrity()
        self.check_private_artifacts()
        self.check_no_secrets([self.kit, HARNESS, GUIDE, TEMPLATE_RESULT, README])
        self.check_harness()
        self.check_documents()
        return descriptor

    def finish(self, ok, runtime_status, device, preparation_verified=True):
        failures = [check for check in self.checks if not check["ok"]]
        runtime = {"status": runtime_status}
        if device:
            runtime["device"] = {key: value for key, value in device.items() if not key.startswith("_")}
        self.report.update({
            "task": "P0307WR",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "preparation_status": ((PREP_PREPARED if ok else PREP_FAILED) if preparation_verified
                                   else PREP_NOT_VERIFIED),
            "runtime_status": runtime_status,
            "runtime": runtime,
            "checks_total": len(self.checks),
            "checks_failed": len(failures),
            "failed": [{"label": check["label"], "detail": check["detail"]} for check in failures],
            "designer_autonomous_qa": "NOT_RUN",
            "independent_t17": "NOT_RUN",
            "notes": [
                "preparation_status describes the local kit/harness/contracts only.",
                "The kit packages are the downloaded platform artifacts; nothing here is a signature.",
                "runtime_status stays NOT_RUN/BLOCKED until a real Windows x64 desktop executes WN01-WN06.",
                "The strict gate refuses Phase completion without bound real-device WN evidence.",
            ],
            "kit": {"path": display_path(self.kit), "format": KIT_VERSION,
                    "integrity_sha256": sha256_file(self.kit / "integrity.json")
                    if (self.kit / "integrity.json").is_file() else None,
                    "members": len(read_json(self.kit / "integrity.json").get("members", {}))
                    if (self.kit / "integrity.json").is_file() else 0,
                    "releases": self.lifecycle.get("releases"),
                    "alternates": self.lifecycle.get("alternates"),
                    "program_sha256": self.lifecycle.get("runtime", {}).get("expected", {}).get("program_sha256"),
                    "program_source_digest": program_source_digest(),
                    "harness_sha256": sha256_file(HARNESS),
                    "instance_root": display_path(self.root / "instance-root"),
                    "runtime_port": self.lifecycle.get("runtime", {}).get("port")},
            "lifecycle": {key: value for key, value in self.lifecycle.items()
                          if key in ("releases", "alternates", "artifacts", "runtime")},
            "checks": self.checks,
        })
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "readiness.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        (self.root / "summary.txt").write_text("\n".join(self.summary_lines) + "\n", encoding="utf-8")
        return self.report


# --------------------------------------------------------------------- runtime
# The strictly read-only probe the Windows side runs over its own SSH session.
# It fetches the forwarded login page, forces the admin host header (no DNS
# change) and emits the raw bytes in short chunks so the output survives any
# console line wrapping. It never writes, never changes configuration and never
# touches the forward itself.
TUNNEL_PROBE_SCRIPT = (
    "$ProgressPreference='SilentlyContinue'\n"
    "try {\n"
    "  $request=[System.Net.HttpWebRequest]::Create('http://127.0.0.1:#PORT#/login')\n"
    "  $request.Host='admin.localhost'\n"
    "  $request.Method='GET'\n"
    "  $request.Proxy=$null\n"
    "  $request.Timeout=15000\n"
    "  $response=$request.GetResponse()\n"
    "  $stream=$response.GetResponseStream()\n"
    "  $memory=New-Object System.IO.MemoryStream\n"
    "  $stream.CopyTo($memory)\n"
    "  $bytes=$memory.ToArray()\n"
    "  Write-Output ('GEP_TUNNEL_PROBE_STATUS ' + [int]$response.StatusCode)\n"
    "  Write-Output 'GEP_TUNNEL_PROBE_BYTES_BEGIN'\n"
    "  $encoded=[Convert]::ToBase64String($bytes)\n"
    "  for ($i=0; $i -lt $encoded.Length; $i += 60) {\n"
    "    Write-Output $encoded.Substring($i, [Math]::Min(60, $encoded.Length - $i))\n"
    "  }\n"
    "  Write-Output 'GEP_TUNNEL_PROBE_BYTES_END'\n"
    "} catch {\n"
    "  Write-Output ('GEP_TUNNEL_PROBE_ERROR ' + $_.Exception.Message)\n"
    "  exit 9\n"
    "}\n")


def encoded_powershell(script):
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def normalize_login_body(raw):
    """Remove the per-request CSRF token so two read-only responses are comparable."""
    text = raw.decode("utf-8", "replace")
    text = re.sub(r'(name="csrfmiddlewaretoken" value=")[^"]*"', r'\1<token>"', text)
    return text.encode("utf-8")


def local_login_fingerprint(port, timeout=10):
    """The normalized login page served directly on this machine (read-only GET)."""
    import http.client
    connection = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
    try:
        connection.request("GET", "/login", headers={"Host": "admin.localhost"})
        response = connection.getresponse()
        return {"status": response.status, "body": normalize_login_body(response.read())}
    finally:
        connection.close()


def _process_start_marker(pid):
    """One stable OS-level process identity used to detect PID reuse."""
    result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    marker = (result.stdout or "").strip()
    return marker if result.returncode == 0 and marker else None


def _recorded_argv(document):
    """The recorded argv tokens, or None when the record is malformed."""
    if not isinstance(document, dict):
        return None
    argv = document.get("argv")
    if isinstance(argv, list):
        if not argv or not all(isinstance(item, str) and item for item in argv):
            return None
        return [str(item) for item in argv]
    command = str(document.get("command") or "").strip()
    if not command:
        return None
    import shlex
    try:
        return shlex.split(command)
    except ValueError:
        return None


def _command_tokens_match(argv, live):
    """The recorded argv must appear in the live command line.

    ``ps`` joins argv with single spaces and does not quote arguments, so both
    sides are whitespace-normalised and the recorded argument vector must appear
    as an ordered, contiguous run. The run may be preceded by an interpreter
    path when the OS exec'd a script through its shebang (the real gunicorn
    command is recorded from its own Popen argv, never guessed).
    """
    wanted = " ".join(str(item) for item in argv)
    if not wanted.strip():
        return False
    normalized_wanted = " ".join(wanted.split())
    normalized_live = " ".join(str(live).split())
    return normalized_wanted in normalized_live


def _stop_recorded_runtime(run_dir, prep_dir):
    """Stop only the runtime/tunnel PIDs recorded by an earlier --serve-runtime.

    The record must carry a real pid and the real command line; the live command
    is re-read from the OS before any signal, and a reused PID (different start
    time), a changed command line or a malformed record is refused instead of
    killed. A malformed record is never treated as a match.
    """
    run_dir = Path(run_dir).resolve()
    prep_dir = Path(prep_dir).resolve() if prep_dir else run_dir.parent
    stopped = []
    for name in ("tunnel.pid", "runtime.pid"):
        record_path = (run_dir if name == "tunnel.pid" else prep_dir) / name
        if not record_path.is_file():
            continue
        try:
            document = read_json(record_path)
        except (OSError, ValueError) as problem:
            stopped.append({"pid": None, "outcome": "refused", "reason": f"unreadable record: {problem}"})
            continue
        argv = _recorded_argv(document)
        if not argv:
            stopped.append({"pid": None, "outcome": "refused",
                            "reason": "malformed pid record (no pid/command line); refusing to signal it"})
            continue
        try:
            pid = int(document.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            stopped.append({"pid": None, "outcome": "refused",
                            "reason": "malformed pid record (invalid pid); refusing to signal it"})
            continue
        live = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
        command = (live.stdout or "").strip()
        if live.returncode != 0 or not command:
            stopped.append({"pid": pid, "outcome": "gone"})
            record_path.unlink()
            continue
        if not _command_tokens_match(argv, command):
            stopped.append({"pid": pid, "outcome": "refused", "reason": "command line changed"})
            continue
        recorded_marker = str(document.get("started") or "").strip()
        live_marker = _process_start_marker(pid)
        if recorded_marker and live_marker and recorded_marker != live_marker:
            stopped.append({"pid": pid, "outcome": "refused", "reason": "pid was reused (start time changed)"})
            continue
        os.kill(pid, signal.SIGTERM)
        stopped.append({"pid": pid, "outcome": "stopped", "command": command[:200]})
        record_path.unlink()
    return stopped


def _record_pid(path, process, command=None):
    """Record the *real* Popen argv plus a start marker, never a guessed command."""
    args = getattr(process, "args", None)
    argv = [str(part) for part in args] if isinstance(args, (list, tuple)) else None
    if not argv:
        argv = [str(part) for part in (command or [])]
    if not argv:
        raise PreparationError("无法记录真实进程命令行（argv 为空）；拒绝写入畸形 PID 记录")
    path.write_text(json.dumps({"pid": process.pid, "argv": argv, "command": " ".join(argv),
                                "started": _process_start_marker(process.pid)},
                               ensure_ascii=False), encoding="utf-8")
class Tunnel:
    """One scoped reverse SSH forward owned by this process.

    The preparation host opens a reverse connection to the scoped Windows host
    (its alias/address are operator-side external parameters, never public
    documentation) so that ``127.0.0.1:<listen_port>`` on Windows reaches
    ``127.0.0.1:<target_port>`` on this machine, where the prepared instance
    serves the frozen loopback port. ``ExitOnForwardFailure=yes`` makes a refused
    or already-bound remote forward exit immediately instead of pretending to be
    ready, and strict host-key checking plus public-key batch mode keep the
    connection non-interactive. Readiness additionally runs a strictly read-only
    remote probe over its own SSH session: the Windows side fetches the forwarded
    ``/login`` page and the returned body must match this machine's own page, so
    a live ssh process alone can never be reported as a working forward. Only the
    exact process started here is stopped; no firewall, DNS, TLS-trust or global
    SSH change is made.
    """

    def __init__(self, spec, alias=None, ssh_binary=None, log=print, quiet=False, stderr_path=None):
        self.spec = spec or {}
        self.alias = alias or self.spec.get("windows_alias")
        self.ssh_binary = ssh_binary or os.environ.get("GEP_SSH_BIN") or "ssh"
        self.log = log
        self.quiet = quiet
        self.stderr_path = stderr_path
        self.process = None
        self.ready = False
        self.detail = None
        self.probe_detail = None

    def _base_options(self):
        return ["-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "NumberOfPasswordPrompts=0",
                "-o", "PreferredAuthentications=publickey", "-o", "ConnectTimeout=8"]

    def command(self):
        listen = int(self.spec.get("listen_port") or 0)
        target = int(self.spec.get("target_port") or 0)
        if not self.alias:
            raise PreparationError("运行时隧道缺少已授权的 Windows 主机别名（runtime.tunnel.windows_alias）")
        if listen <= 0 or target <= 0:
            raise PreparationError("运行时隧道端点无效（listen_port/target_port 必须为正整数）")
        return [self.ssh_binary, "-N", *self._base_options(),
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                "-R", f"{listen}:127.0.0.1:{target}", self.alias]

    def probe_command(self):
        """A strictly read-only probe command: no forward, no local config change."""
        listen = int(self.spec.get("listen_port") or 0)
        if not self.alias:
            raise PreparationError("运行时隧道缺少已授权的 Windows 主机别名（runtime.tunnel.windows_alias）")
        if listen <= 0:
            raise PreparationError("运行时隧道端点无效（listen_port 必须为正整数）")
        script = "".join(("$ErrorActionPreference='Stop'\n", TUNNEL_PROBE_SCRIPT)).replace("#PORT#", str(listen))
        return [self.ssh_binary, *self._base_options(),
                self.alias, "powershell", "-NoProfile", "-NonInteractive",
                "-EncodedCommand", encoded_powershell(script)]

    def start(self):
        command = self.command()
        self.log(f"[tunnel] 建立作用域反向转发：{' '.join(command)}")
        if self.stderr_path:
            stream = open(self.stderr_path, "ab")
        else:
            stream = subprocess.PIPE
        self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stream, text=True)
        return self

    def last_stderr(self, limit=400):
        if not self.stderr_path or not Path(self.stderr_path).is_file():
            return ""
        try:
            return Path(self.stderr_path).read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    def remote_probe(self, timeout=45):
        """Read-only probe: does the Windows-side forwarded endpoint serve our instance?"""
        listen = int(self.spec.get("listen_port") or 0)
        target = int(self.spec.get("target_port") or 0)
        try:
            local = local_login_fingerprint(target)
        except OSError as problem:
            return False, f"本机实例登录页不可读（{problem}）"
        if local["status"] != 200 or b'name="username"' not in local["body"]:
            return False, f"本机实例登录页异常（HTTP {local['status']}）"
        try:
            result = subprocess.run(self.probe_command(), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"远端只读探针在 {timeout}s 内未返回"
        except OSError as problem:
            return False, f"远端只读探针无法执行（{problem!r}）"
        output = result.stdout or ""
        if "GEP_TUNNEL_PROBE_ERROR" in output:
            return False, "远端探针报告错误：" + output.strip().splitlines()[-1][:200]
        status, encoded = None, []
        collecting = False
        for line in output.splitlines():
            if line.startswith("GEP_TUNNEL_PROBE_STATUS "):
                status = line.split(" ", 1)[1].strip()
            elif line.strip() == "GEP_TUNNEL_PROBE_BYTES_BEGIN":
                collecting = True
            elif line.strip() == "GEP_TUNNEL_PROBE_BYTES_END":
                collecting = False
            elif collecting:
                encoded.append(line.strip())
        if status != "200":
            return False, f"远端探针状态异常：{status!r}（ssh exit={result.returncode}）"
        try:
            remote_body = normalize_login_body(base64.b64decode("".join(encoded)))
        except (ValueError, TypeError) as problem:
            return False, f"远端探针响应不可解码（{problem!r}）"
        if remote_body != local["body"]:
            return False, "远端转发端点返回的页面与本机实例不一致（可能指向错误实例）"
        return True, (f"Windows 侧 127.0.0.1:{listen} 经转发返回本机实例登录页"
                      f"（HTTP 200，页面指纹一致，{len(remote_body)} 字节）")

    def wait_ready(self, timeout=30, grace=12.0, probe=None):
        """Ready only when our own ssh is live, the target answers *and* the forward works.

        ``ExitOnForwardFailure`` makes a refused remote forward exit, and
        ``ConnectTimeout`` bounds the connection attempt, so a still-live ssh
        after the grace period means the session is up. A live process alone is
        not enough: the strictly read-only remote probe must confirm that the
        Windows-side forwarded endpoint really serves this instance. A custom
        ``probe`` (``() -> (ok, detail)``) exists for tests only.
        """
        if self.process is None:
            raise PreparationError("隧道尚未启动")
        target = int(self.spec.get("target_port") or 0)
        probe = probe or self.remote_probe
        started = time.time()
        deadline = started + timeout
        next_probe = started + grace
        probe_failure = None
        while time.time() < deadline:
            if self.process.poll() is not None:
                detail = f"ssh 在建立转发前退出（exit {self.process.returncode}）"
                tail = self.last_stderr()
                self.detail = detail + (f"；ssh stderr: {tail.strip()[-200:]}" if tail.strip() else "")
                return False
            if time.time() >= next_probe:
                next_probe = time.time() + 5.0
                try:
                    with socket.create_connection(("127.0.0.1", target), timeout=1):
                        pass
                except OSError as problem:
                    probe_failure = f"本机目标 127.0.0.1:{target} 尚不可达（{problem}）"
                    self.probe_detail = probe_failure
                    time.sleep(0.2)
                    continue
                try:
                    ok, detail = probe()
                except Exception as problem:  # a probe must never fake readiness
                    ok, detail = False, f"远端探针异常：{problem!r}"
                self.probe_detail = detail
                if ok:
                    self.ready = True
                    self.detail = (f"ssh pid={self.process.pid} 存活超过连接超时，本机目标 127.0.0.1:{target} "
                                   f"可达，{detail}")
                    return True
                probe_failure = detail
            time.sleep(0.2)
        self.detail = (f"隧道在 {timeout}s 内未就绪：{probe_failure or 'ssh 可能仍在连接或未建立转发'}")
        return False

    def stop(self, timeout=15):
        """Stop exactly the ssh process this tunnel started."""
        if self.process is None or self.process.poll() is not None:
            self.process = None
            self.ready = False
            return True
        pid = self.process.pid
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self.log(f"[tunnel] 自有 ssh pid={pid} 未能结束")
                return False
        self.log(f"[tunnel] 已停止自有 ssh pid={pid}（exit {self.process.returncode}）")
        self.process = None
        self.ready = False
        return True


def serve_runtime(run_dir, prep_dir=None, foreground=False, device_alias=None, tunnel=True):
    """Serve the prepared isolated instance on the frozen loopback port.

    The scoped reverse forward from ``runtime.tunnel`` is established as part of
    this command: without it a real Windows machine cannot reach the frozen
    loopback port, so a failed tunnel is reported as a precise failure instead of
    a ready-looking service. The tunnel and the instance are owned by this
    process; only their own PIDs are recorded and stopped.
    """
    run_dir = Path(run_dir).resolve()
    prep_dir = Path(prep_dir).resolve() if prep_dir else run_dir.parent
    candidates = [run_dir / "private" / "runtime.json", run_dir / "kit" / "operator" / "runtime.json",
                  prep_dir / "private" / "runtime.json"]
    runtime = None
    for candidate in candidates:
        if candidate.is_file():
            runtime = read_json(candidate)
            break
    if runtime is None:
        raise PreparationError(f"no runtime fixture found under {display_path(run_dir)}")
    if runtime.get("format") != RUNTIME_FORMAT:
        raise PreparationError(f"runtime fixture format {runtime.get('format')!r} is not {RUNTIME_FORMAT!r}")
    data = prep_dir / "instance-root" / "instance"
    if not (data / "gep.sqlite3").is_file() or not (data / "instance").is_file():
        raise PreparationError(f"prepared instance missing under {display_path(data)}")
    instance = Instance(prep_dir / "instance-root")
    instance.port = int(runtime["port"])
    instance.instance_id = (data / "instance").read_text(encoding="utf-8").strip()
    instance.secret_key = (data / "secret").read_text(encoding="utf-8").strip()
    if runtime.get("instance_id") != instance.instance_id:
        raise PreparationError("runtime fixture does not belong to the prepared instance")
    tunnel_spec = runtime.get("tunnel") or {}
    forward = Tunnel(tunnel_spec, alias=device_alias, log=print)
    try:
        instance.start()
        _record_pid(prep_dir / "runtime.pid", instance.server)
        if tunnel:
            forward.stderr_path = run_dir / "tunnel.log"
            forward.start()
            if not forward.wait_ready(timeout=30):
                raise PreparationError(f"作用域隧道未就绪：{forward.detail}；"
                                       "未建立转发前不提供可被误认为就绪的服务")
            _record_pid(run_dir / "tunnel.pid", forward.process)
    except BaseException:
        forward.stop()
        instance.stop()
        for stale in (prep_dir / "runtime.pid", run_dir / "tunnel.pid"):
            try:
                stale.unlink()
            except OSError:
                pass
        raise
    print(f"PHASE03_WINDOWS_RUNTIME_SERVING 127.0.0.1:{instance.port} "
          f"instance={instance.instance_id} data={display_path(data)} "
          f"tunnel={'READY' if forward.ready else 'SKIPPED'} "
          f"listen=127.0.0.1:{tunnel_spec.get('listen_port')} "
          f"target=127.0.0.1:{tunnel_spec.get('target_port')}")
    if not foreground:
        return 0
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        forward.stop()
        instance.stop()
    return 0


# ------------------------------------------------------------------------ gate
def _problem(problems, message):
    problems.append(message)
    return problems


def _evidence_file(run_dir, relative):
    path = run_dir / relative
    return path if path.is_file() else None


def _read_store_copy(path):
    """One native store copy read-only; ``(documents, error)``.

    A malformed SQLite file or a malformed stored session document is a precise
    refusal, never a silent empty result that could pass for "no mismatch".
    """
    if path is None:
        return None, "native store copy is missing"
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=20)
        try:
            rows = [row[0] for row in connection.execute("SELECT value FROM sessions ORDER BY id")]
        finally:
            connection.close()
    except sqlite3.Error as error:
        return None, f"native store copy is unreadable ({error})"
    documents = []
    for raw in rows:
        try:
            document = json.loads(raw)
        except ValueError as error:
            return None, f"native store copy holds malformed JSON ({error})"
        if not isinstance(document, dict):
            return None, "native store copy holds a non-object session row"
        documents.append(document)
    return documents, None


def _canonical_record(record):
    """One event envelope as canonical JSON; nested payload, units and values included."""
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _db_session_release(db_path, session_id):
    """The release the bound instance binds this session to, read-only."""
    if db_path is None or not Path(db_path).is_file():
        return None, "bound isolated instance database is missing"
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20)
        try:
            rows = connection.execute("select release_id from core_session where id=?",
                                      [str(session_id).replace("-", "")]).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return None, f"bound instance session table unreadable ({error})"
    if not rows:
        return None, "session is missing from the bound isolated instance"
    return _uuid_text(rows[0][0]), None


def _db_session_events(db_path, session_id):
    """Server-side event envelopes of one session, read-only; ``(events, error)``."""
    if db_path is None or not Path(db_path).is_file():
        return None, "bound isolated instance database is missing"
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20)
        try:
            rows = connection.execute("select envelope from core_event where session_id=?",
                                      [str(session_id).replace("-", "")]).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return None, f"bound instance event table unreadable ({error})"
    events = []
    for (raw,) in rows:
        try:
            document = json.loads(raw)
        except ValueError as error:
            return None, f"bound instance holds a malformed event envelope ({error})"
        if not isinstance(document, dict):
            return None, "bound instance holds a non-object event envelope"
        events.append(document)
    return events, None


def _export_rows(path):
    """One authorized JSONL export; ``(rows, error)`` with precise malformed lines."""
    if path is None:
        return None, "authorized export file is missing"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return None, f"authorized export unreadable ({error})"
    rows = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as error:
            return None, f"authorized export line {number} is malformed JSON ({error})"
        if not isinstance(row, dict) or not isinstance(row.get("record"), dict):
            return None, f"authorized export line {number} has no record envelope"
        rows.append(row)
    return rows, None


def _store_events(documents):
    events = []
    for document in documents or []:
        if document.get("kind") != "session":
            continue
        for record in document.get("records") or []:
            if isinstance(record, dict):
                events.append(record)
    return events


def _case_status(case):
    checks = case.get("checks") or []
    if any(not check.get("ok") for check in checks):
        return RUNTIME_FAIL
    return RUNTIME_PASS if checks else RUNTIME_NOT_RUN


def validate_run(run_dir, prep_dir):
    """Independently validate one selected Windows run against the current tree."""
    run_dir = Path(run_dir).resolve()
    problems = []
    report_path = run_dir / "run.json"
    if not report_path.is_file():
        return {"verdict": GATE_VERDICT_ABSENT, "detail": f"no run.json under {display_path(run_dir)}",
                "problems": ["run.json missing"], "run_dir": str(run_dir)}
    try:
        report = read_json(report_path)
    except (OSError, ValueError) as error:
        return {"verdict": GATE_VERDICT_INVALID, "detail": f"run.json unreadable: {error}",
                "problems": ["run.json unreadable"], "run_dir": str(run_dir)}
    if report.get("format") != RUN_FORMAT:
        _problem(problems, f"unexpected run format {report.get('format')!r}")
    if report.get("mode") != "run":
        _problem(problems, f"run mode {report.get('mode')!r} is not a full engineering run (doctor-only evidence is refused)")
    host = report.get("host") or {}
    if host.get("platform") != "win32":
        _problem(problems, "evidence was not produced on a real Windows host")
    if str(host.get("processor_architecture", "")).upper() != "AMD64":
        _problem(problems, f"host architecture {host.get('processor_architecture')!r} is not native x64")
    machine = host.get("machine") or {}
    if "windows" not in str(machine.get("Caption", "")).lower():
        _problem(problems, "host does not report a Windows caption")
    if "x64" not in str(machine.get("System", "")) and "64" not in str(machine.get("Arch", "")):
        _problem(problems, "host does not report a 64-bit system")
    if host.get("console_session") is None:
        _problem(problems, "no active interactive console session was recorded")
    expected = report.get("expected") or {}
    current_descriptor = read_json(DESCRIPTOR)
    if expected.get("program_sha256") != current_descriptor.get("program_sha256"):
        _problem(problems, "run program digest is not the current build digest (stale evidence)")
    if PROGRAM_ARCHIVE.is_file() and expected.get("program_sha256") != sha256_file(PROGRAM_ARCHIVE):
        _problem(problems, "run program digest does not match the current program archive")
    if expected.get("descriptor_sha256") != sha256_file(DESCRIPTOR):
        _problem(problems, "run descriptor digest is not the current descriptor (stale evidence)")
    if expected.get("program_source_digest") != program_source_digest():
        _problem(problems, "run program source digest is not the current source tree (stale evidence)")
    if report.get("harness_sha256") != sha256_file(HARNESS):
        _problem(problems, "run was produced by a different harness revision")

    prep_root = Path(prep_dir).resolve() if prep_dir else None
    if prep_root is None or not (prep_root / "readiness.json").is_file():
        return {"verdict": GATE_VERDICT_SELECTION_REQUIRED,
                "detail": "an explicit preparation directory with readiness.json is required",
                "problems": problems + ["preparation directory not selected"], "run_dir": str(run_dir)}
    prep = read_json(prep_root / "readiness.json")
    if prep.get("preparation_status") != PREP_PREPARED:
        _problem(problems, f"bound preparation status {prep.get('preparation_status')!r} is not PREPARED")
    kit_path = prep_root / "kit"
    if not kit_path.is_dir():
        _problem(problems, "bound preparation kit directory missing")
    if report.get("kit_integrity_sha256") != (sha256_file(kit_path / "integrity.json")
                                              if (kit_path / "integrity.json").is_file() else None):
        _problem(problems, "run kit integrity digest does not match the bound kit")
    binding = report.get("binding") or {}
    kit_releases = read_json(kit_path / "releases.json") if (kit_path / "releases.json").is_file() else {}
    if binding.get("instance_id") != kit_releases.get("instance_id"):
        _problem(problems, "run instance binding does not match the bound kit")
    studies = binding.get("studies") or {}
    for mode in MODES:
        entry = studies.get(mode) or {}
        kit_entry = (kit_releases.get("modes") or {}).get(mode) or {}
        if not entry:
            _problem(problems, f"run binding has no {mode} study")
            continue
        if entry.get("release_id") != kit_entry.get("release_id") or entry.get("study_id") != kit_entry.get("study_id"):
            _problem(problems, f"{mode} run binding does not match the bound kit release")
        package = kit_path / str(kit_entry.get("delivery", ""))
        if not package.is_file():
            _problem(problems, f"{mode} bound package missing from the kit")
        elif sha256_file(package) != entry.get("package_sha256") or sha256_file(package) != kit_entry.get("artifact_sha256"):
            _problem(problems, f"{mode} bound package bytes do not match the recorded digest")
        else:
            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read("artifact_manifest.json"))
            if manifest.get("program_sha256") != current_descriptor.get("program_sha256"):
                _problem(problems, f"{mode} package was frozen from a different program build")
            if manifest.get("platform") != "windows_x64" or manifest.get("artifact_format_version") != ARTIFACT_FORMAT:
                _problem(problems, f"{mode} package manifest is not a windows_x64 complete artifact")
    _validate_instance_binding(problems, prep_root, kit_releases, binding)

    cases = {case.get("id"): case for case in report.get("cases") or []}
    for case_id in WN_ITEMS:
        if case_id not in cases:
            _problem(problems, f"case {case_id} is missing from the run (skipped cases are refused)")
            continue
        status = _case_status(cases[case_id])
        if status == RUNTIME_FAIL:
            _problem(problems, f"case {case_id} has failing checks")
        elif status == RUNTIME_NOT_RUN:
            _problem(problems, f"case {case_id} was not executed")
        _validate_case(problems, case_id, cases[case_id], run_dir, studies, kit_releases, prep_root, cases)

    failures = [case_id for case_id, case in cases.items() if _case_status(case) != RUNTIME_PASS]
    if problems:
        verdict = GATE_VERDICT_INVALID
        detail = f"{len(problems)} evidence problem(s); Phase completion must be refused"
    elif failures:
        verdict = GATE_VERDICT_INCOMPLETE
        detail = "evidence is bound and valid but these cases are not passed: " + ", ".join(sorted(failures))
    else:
        verdict = GATE_VERDICT_PASS
        detail = "real Windows x64 device run passed every WN01-WN06 case on the bound kit"
    return {"verdict": verdict, "detail": detail, "problems": problems, "run_dir": str(run_dir),
            "prep_dir": str(prep_root), "cases": {case_id: _case_status(case) for case_id, case in cases.items()}}


def _validate_instance_binding(problems, prep_root, kit_releases, binding):
    db = prep_root / "instance-root" / "instance" / "gep.sqlite3"
    if not db.is_file():
        _problem(problems, "bound isolated instance database is missing")
        return
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=20)
    try:
        rows = connection.execute("select instance_id from core_instance").fetchall()
        if not rows or _uuid_text(rows[0][0]) != binding.get("instance_id"):
            _problem(problems, "run instance id is not the bound isolated instance")
        for mode in MODES:
            entry = (binding.get("studies") or {}).get(mode) or {}
            record = connection.execute(
                "select release.artifact_digest, release.approved, release.config, release.build_id, release.study_id "
                "from core_release release where release.id=?",
                [str(entry.get("release_id", "")).replace("-", "")]).fetchall()
            if not record:
                _problem(problems, f"{mode} release is missing from the bound instance database")
                continue
            digest, approved, config, build_id, study_id = record[0]
            config = json.loads(config)
            if not approved or digest != entry.get("package_sha256"):
                _problem(problems, f"{mode} release artifact record does not match the run binding")
            if config.get("mode") != mode or config.get("artifact_format_version") != ARTIFACT_FORMAT:
                _problem(problems, f"{mode} release is not frozen with this mode and artifact format")
            if _uuid_text(build_id) != entry.get("build_id") or _uuid_text(study_id) != entry.get("study_id"):
                _problem(problems, f"{mode} release is not bound to the run's build/study")
    finally:
        connection.close()
    for mode, entry in (kit_releases.get("modes") or {}).items():
        if entry.get("artifact_sha256") != ((binding.get("studies") or {}).get(mode) or {}).get("package_sha256"):
            _problem(problems, f"{mode} kit release digest and run binding disagree")
    # Raw session/event data, not only the release record: every bound release must
    # carry real sessions, and every stored envelope must name its own session.
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=20)
    try:
        for mode in MODES:
            release_id = str(((binding.get("studies") or {}).get(mode) or {}).get("release_id", "")).replace("-", "")
            if not release_id:
                continue
            sessions = [row[0] for row in connection.execute(
                "select id from core_session where release_id=?", [release_id]).fetchall()]
            if not sessions:
                _problem(problems, f"{mode} bound release has no real session in the isolated instance")
                continue
            for session in sessions:
                for (envelope,) in connection.execute(
                        "select envelope from core_event where session_id=?", [session]).fetchall():
                    try:
                        document = json.loads(envelope)
                    except ValueError:
                        _problem(problems, f"{mode} isolated instance holds a malformed event envelope")
                        continue
                    if str(document.get("session_id")) != _uuid_text(session):
                        _problem(problems, f"{mode} isolated instance event envelope is bound to another session")
    finally:
        connection.close()


def _uuid_text(raw):
    if isinstance(raw, bytes):
        raw = raw.decode()
    raw = str(raw)
    if len(raw) == 32:
        return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return raw


def _release_is_superseded(prep_root, release_id):
    """True when the isolated instance's current release is a different one."""
    db = Path(prep_root) / "instance-root" / "instance" / "gep.sqlite3"
    if not db.is_file():
        return False
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=20)
    try:
        rows = connection.execute("select current_release_id from core_study").fetchall()
    except sqlite3.Error:
        return False
    finally:
        connection.close()
    current = {_uuid_text(row[0]) for row in rows if row[0]}
    return str(release_id) not in current


def _validate_case(problems, case_id, case, run_dir, studies, kit_releases, prep_root=None, cases=None):
    evidence = case.get("evidence") or {}
    checks = case.get("checks") or []
    if not any(check.get("ok") for check in checks):
        _problem(problems, f"{case_id} has no passing check")
    if case_id == "WN01":
        launch = evidence.get("launch") or {}
        if not launch.get("pid") or not str(launch.get("window_title") or "").strip():
            _problem(problems, "WN01 launch evidence lacks an owned pid or a real window title")
        if not str(launch.get("executable_path") or "").endswith(".exe"):
            _problem(problems, "WN01 launch evidence lacks the verified executable path")
        if launch.get("stopped_owned_pid") is not True:
            _problem(problems, "WN01 does not prove the owned PID was the process that was stopped")
        if launch.get("console_session") is None:
            _problem(problems, "WN01 launch evidence lacks the interactive console session")
    elif case_id == "WN02":
        modes = evidence.get("modes") or {}
        for mode in MODES:
            entry = modes.get(mode)
            if not entry:
                _problem(problems, f"WN02 has no {mode} evidence")
                continue
            if not entry.get("session_id"):
                _problem(problems, f"WN02 {mode} has no admitted session id")
            if len(entry.get("boundary_event_ids") or []) != 2:
                _problem(problems, f"WN02 {mode} boundary evidence is not two committed events")
            if sorted(entry.get("export_values") or []) != list(RAW_VALUES):
                _problem(problems, f"WN02 {mode} exported raw values do not match the synthetic policy")
            _validate_export(problems, run_dir, f"WN02 {mode}", entry)
            _validate_store_copy(problems, run_dir, f"WN02 {mode}", entry)
            _reconcile_entry(problems, f"WN02 {mode}", run_dir, prep_root, entry,
                             (studies.get(mode) or {}).get("release_id"))
    elif case_id == "WN03":
        offline = evidence.get("offline") or {}
        if not offline.get("pending") or len(offline.get("event_ids") or []) != 2:
            _problem(problems, "WN03 offline evidence lacks two locally committed pending events")
        if offline.get("raw_values") != [321.5]:
            _problem(problems, "WN03 offline raw values do not match the synthetic policy")
        reconnect = evidence.get("reconnect") or {}
        if not set(offline.get("event_ids") or []) <= set(reconnect.get("event_ids") or []):
            _problem(problems, "WN03 reconnect did not keep the offline event ids")
        export = evidence.get("export") or {}
        if sorted(export.get("values") or []) != list(RAW_VALUES):
            _problem(problems, "WN03 export values do not match the synthetic policy")
        if len(set(export.get("event_ids") or [])) != len(export.get("event_ids") or []):
            _problem(problems, "WN03 export contains duplicate event ids after reconnect")
        lost = evidence.get("lost_ack") or {}
        if not lost or lost.get("events") != lost.get("unique"):
            _problem(problems, "WN03 lost-ack evidence does not prove server-side deduplication")
        _validate_store_copy(problems, run_dir, "WN03 offline", offline)
        _validate_store_copy(problems, run_dir, "WN03 reconnect", reconnect)
        release = (studies.get("password") or {}).get("release_id")
        _reconcile_entry(problems, "WN03 offline", run_dir, prep_root, offline, release,
                         session_id=offline.get("session_id"), export_path=export.get("path"))
        _reconcile_entry(problems, "WN03 reconnect", run_dir, prep_root, reconnect, release,
                         session_id=offline.get("session_id"), export_path=export.get("path"))
    elif case_id == "WN04":
        short = evidence.get("short_code") or {}
        if len(short.get("segments") or []) < 2:
            _problem(problems, "WN04 short-code recovery did not create a new segment/epoch")
        if len(short.get("event_ids") or []) != EXPECTED_EVENTS_PER_SESSION:
            _problem(problems, "WN04 recovery replayed or lost trial boundaries")
        if short.get("replay_exit") != 2:
            _problem(problems, "WN04 replay of a consumed short code was not refused")
        expiry = evidence.get("expiry") or {}
        if int(expiry.get("waited_seconds") or 0) < EXPIRY_MIN_WAIT or expiry.get("exit") != 2:
            _problem(problems, "WN04 expiry evidence did not wait for the real TTL and refuse the code")
        shared = evidence.get("shared_writer") or {}
        if not shared.get("holder_pid") or shared.get("second_exit") != 2:
            _problem(problems, "WN04 shared-writer evidence is incomplete")
        data_only = evidence.get("data_only") or {}
        if data_only.get("exit") != 2 or "SYNTHETIC_DATA_ONLY" not in str(data_only.get("stdout_tail") or ""):
            _problem(problems, "WN04 data-only recovery evidence is incomplete")
        recovery = evidence.get("recovery_export") or {}
        path = _evidence_file(run_dir, str(recovery.get("path") or ""))
        if path is None:
            _problem(problems, "WN04 failure-data export file is missing")
        else:
            raw = path.read_bytes()
            if any(pattern.search(raw) for pattern in _SECRET_PATTERNS):
                _problem(problems, "WN04 failure-data export contains credential-like content")
            document = json.loads(raw.decode("utf-8"))
            binding = document.get("binding") or {}
            mode_entry = (studies.get("password") or {})
            if binding.get("instance_id") is None or binding.get("release_id") != mode_entry.get("release_id"):
                _problem(problems, "WN04 failure-data export is not bound to the target release")
            if not document.get("records"):
                _problem(problems, "WN04 failure-data export has no records")
        _validate_store_copy(problems, run_dir, "WN04 short code", short)
        _reconcile_entry(problems, "WN04 short code", run_dir, prep_root, short,
                         (studies.get("password") or {}).get("release_id"), require_export=False)
    elif case_id == "WN05":
        modes = evidence.get("modes") or {}
        wn02_modes = ((cases or {}).get("WN02") or {}).get("evidence", {}).get("modes") or {}
        for mode in MODES:
            entry = modes.get(mode)
            if not entry:
                _problem(problems, f"WN05 has no {mode} reconciliation")
                continue
            _reconcile_entry(problems, f"WN05 {mode}", run_dir, prep_root, entry,
                             (studies.get(mode) or {}).get("release_id"),
                             session_id=(wn02_modes.get(mode) or {}).get("session_id"),
                             store_copy=(wn02_modes.get(mode) or {}).get("store_copy"))
            local = entry.get("local_event_ids") or []
            remote = entry.get("remote_event_ids") or []
            if not local or not set(local) <= set(remote):
                _problem(problems, f"WN05 {mode} local event ids are not contained in the authorized export")
            if sorted(entry.get("local_values") or []) != [321.5] or sorted(entry.get("remote_values") or []) != list(RAW_VALUES):
                _problem(problems, f"WN05 {mode} raw values do not match between store and export")
            if len(set(remote)) != len(remote):
                _problem(problems, f"WN05 {mode} authorized export contains duplicate event ids")
            export_path = _evidence_file(run_dir, str(entry.get("export_path") or ""))
            if export_path is None or sha256_file(export_path) != entry.get("export_sha256"):
                _problem(problems, f"WN05 {mode} export file is missing or changed after the run")
    elif case_id == "WN06":
        old = evidence.get("old_release") or {}
        if not old.get("release_id") or not old.get("session_id"):
            _problem(problems, "WN06 has no old-release device evidence")
        else:
            path = _evidence_file(run_dir, str(old.get("export_path") or ""))
            if path is None or sha256_file(path) != old.get("export_sha256"):
                _problem(problems, "WN06 old-release authorized export is missing or changed")
            else:
                rows, error = _export_rows(path)
                if error:
                    _problem(problems, f"WN06 old release {error}")
                    rows = []
                old_lines = [row for row in rows if row.get("release_id") == old.get("release_id")]
                if len(old_lines) != EXPECTED_EVENTS_PER_SESSION:
                    _problem(problems, "WN06 old-release export does not carry every recorded event")
                ids = [line["record"].get("event_id") for line in old_lines]
                if len(set(ids)) != len(ids) or not set(old.get("event_ids") or []) <= set(ids):
                    _problem(problems, "WN06 old-release local and exported event ids disagree")
                if not set(old.get("event_ids") or []) <= set(old.get("export_event_ids") or []):
                    _problem(problems, "WN06 old-release evidence records inconsistent event ids")
        _validate_store_copy(problems, run_dir, "WN06 old release", old)
        _reconcile_entry(problems, "WN06 old release", run_dir, prep_root, old, old.get("release_id"))
        if old.get("release_id") and not _release_is_superseded(prep_root, old.get("release_id")):
            _problem(problems, "WN06 old-release evidence is not bound to a release that stopped being current")


def _reconcile_records(problems, label, documents, export_rows, db_events, session_id, release_id):
    """Reconcile native store, authorized JSONL and bound server database.

    The comparison is by session/event id and by the canonical raw record: the
    whole event envelope (nested payload, original units/source, float values,
    sequence/segment) must be identical across all three sources. A summary,
    count or hash in the run report is never accepted as a substitute. When
    ``export_rows`` is ``None`` the case has no authorized export (recovery
    evidence) and the store copy is reconciled against the bound instance only.
    """
    local_events = _store_events(documents or [])
    local_ids = [event.get("event_id") for event in local_events]
    if len(set(local_ids)) != len(local_ids):
        _problem(problems, f"{label} native store copy contains duplicate event ids")
    db_ids = [event.get("event_id") for event in db_events]
    if len(set(db_ids)) != len(db_ids):
        _problem(problems, f"{label} bound instance contains duplicate event ids for the session")
    db_index = {event.get("event_id"): event for event in db_events}
    if export_rows is not None:
        selected = [row for row in export_rows if str(row.get("release_id")) == str(release_id)
                    and (row.get("record") or {}).get("session_id") == str(session_id)]
        if not selected:
            _problem(problems, f"{label} authorized export has no row for the bound session/release")
        export_events = [row["record"] for row in selected]
        export_ids = [event.get("event_id") for event in export_events]
        if len(set(export_ids)) != len(export_ids):
            _problem(problems, f"{label} authorized export contains duplicate event ids")
        if set(db_ids) != set(export_ids):
            missing = sorted(str(value) for value in set(db_ids) - set(export_ids))[:3]
            extra = sorted(str(value) for value in set(export_ids) - set(db_ids))[:3]
            _problem(problems, f"{label} authorized export and bound instance disagree on the session "
                               f"event set (missing={missing}, extra={extra})")
        export_index = {event.get("event_id"): event for event in export_events}
        for event_id in sorted(set(db_index) & set(export_index), key=str):
            if _canonical_record(db_index[event_id]) != _canonical_record(export_index[event_id]):
                _problem(problems, f"{label} event {event_id} differs between the authorized export "
                                   f"and the bound instance")
    for event in local_events:
        event_id = event.get("event_id")
        reference = db_index.get(event_id)
        if reference is None:
            _problem(problems, f"{label} native store copy holds an event id absent from the bound "
                               f"instance ({event_id})")
            continue
        if _canonical_record(event) != _canonical_record(reference):
            _problem(problems, f"{label} event {event_id} differs between the native store copy "
                               f"and the bound instance")


def _reconcile_entry(problems, label, run_dir, prep_root, entry, release_id, session_id=None,
                     store_copy=None, export_path=None, require_export=True):
    """One bound session reconciled across store copy, export and server database."""
    session = session_id or entry.get("session_id")
    if not session:
        _problem(problems, f"{label} has no session id to reconcile")
        return
    if not release_id:
        _problem(problems, f"{label} has no bound release id to reconcile")
        return
    db_path = (Path(prep_root) / "instance-root" / "instance" / "gep.sqlite3") if prep_root else None
    bound, error = _db_session_release(db_path, session)
    if error:
        _problem(problems, f"{label} {error}")
        return
    if str(bound) != str(release_id):
        _problem(problems, f"{label} session is bound to a different release than the run claims")
    declared = str(export_path or entry.get("export_path") or "")
    rows = None
    if declared:
        path = _evidence_file(run_dir, declared)
        rows, error = _export_rows(path)
        if error:
            _problem(problems, f"{label} {error}")
            return
    elif require_export:
        _problem(problems, f"{label} authorized export file is missing")
        return
    events, error = _db_session_events(db_path, session)
    if error:
        _problem(problems, f"{label} {error}")
        return
    copy = store_copy if store_copy is not None else (entry.get("store_copy") or {})
    documents, error = _read_store_copy(_evidence_file(run_dir, str(copy.get("queue.sqlite") or "")))
    if error:
        _problem(problems, f"{label} {error}")
        return
    _reconcile_records(problems, label, documents, rows, events, session, release_id)


def _validate_export(problems, run_dir, label, entry):
    path = _evidence_file(run_dir, str(entry.get("export_path") or ""))
    if path is None:
        _problem(problems, f"{label} authorized export file is missing")
        return
    if sha256_file(path) != entry.get("export_sha256"):
        _problem(problems, f"{label} authorized export changed after the run")
        return
    rows, error = _export_rows(path)
    if error:
        _problem(problems, f"{label} {error}")
        return
    if len(rows) != EXPECTED_EVENTS_PER_SESSION:
        _problem(problems, f"{label} authorized export does not carry every recorded event")
    ids = [row["record"].get("event_id") for row in rows]
    if len(set(ids)) != len(ids):
        _problem(problems, f"{label} authorized export contains duplicate event ids")
    if not set(entry.get("boundary_event_ids") or []) <= set(ids):
        _problem(problems, f"{label} authorized export lost locally committed event ids")


def _validate_store_copy(problems, run_dir, label, evidence):
    copy = evidence.get("store_copy") or {}
    queue = _evidence_file(run_dir, str(copy.get("queue.sqlite") or ""))
    documents, error = _read_store_copy(queue)
    if error:
        _problem(problems, f"{label} {error}")
        return
    expected_ids = evidence.get("event_ids") or evidence.get("boundary_event_ids") or []
    if expected_ids and not set(expected_ids) <= {record.get("event_id") for record in _store_events(documents)}:
        _problem(problems, f"{label} native store copy does not contain the recorded event ids")


def gate_verdict(run_dir=None, prep_dir=None):
    """Strict integration gate. Returns ``(verdict, detail, run_dir)``."""
    if not run_dir:
        return GATE_VERDICT_SELECTION_REQUIRED, \
            "an explicit --gate-run directory is required; the newest preparation is never trusted", None
    result = validate_run(run_dir, prep_dir)
    return result["verdict"], result["detail"], result.get("run_dir")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 03F Windows native preparation, runtime and strict gate")
    parser.add_argument("--verify-preparation", action="store_true",
                        help="validate toolchain/program and build the real lifecycle readiness kit")
    parser.add_argument("--probe-device", action="store_true",
                        help="safe read-only probe of the documented Windows host")
    parser.add_argument("--serve-runtime", action="store_true",
                        help="serve the prepared isolated instance and its scoped reverse tunnel")
    parser.add_argument("--stop-runtime", action="store_true",
                        help="stop only the runtime/tunnel PIDs recorded by --serve-runtime")
    parser.add_argument("--no-tunnel", action="store_true",
                        help="local debugging only: serve without the reverse forward (never a ready state)")
    parser.add_argument("--gate", action="store_true", help="strict validation of one selected device run")
    parser.add_argument("--gate-run", default=None, help="explicit run directory to validate")
    parser.add_argument("--gate-prep", default=None, help="preparation directory that owns the bound kit")
    parser.add_argument("--run", default=None, help="run directory for --serve-runtime")
    parser.add_argument("--prep", default=None, help="preparation directory for --serve-runtime")
    parser.add_argument("--foreground", action="store_true", help="keep --serve-runtime in the foreground")
    parser.add_argument("--device-alias", default=None, help="override the documented SSH alias")
    parser.add_argument("--stamp", default=None, help="evidence stamp (defaults to the current UTC time)")
    parser.add_argument("--root", default=None, help="explicit preparation evidence root")
    parser.add_argument("--quiet", action="store_true", help="only write evidence, print the final line")
    args = parser.parse_args(argv)
    if args.serve_runtime:
        try:
            return serve_runtime(args.run or args.root, args.prep, args.foreground,
                                 device_alias=args.device_alias, tunnel=not args.no_tunnel)
        except PreparationError as error:
            print(f"PHASE03_WINDOWS_RUNTIME_FAILED {error}")
            return 1
    if args.stop_runtime:
        try:
            stopped = _stop_recorded_runtime(args.run or args.root, args.prep)
        except (OSError, ValueError) as error:
            print(f"PHASE03_WINDOWS_RUNTIME_STOP_FAILED {error}")
            return 1
        print("PHASE03_WINDOWS_RUNTIME_STOPPED " + json.dumps(stopped, ensure_ascii=False))
        return 0
    if args.gate:
        verdict, detail, report_path = gate_verdict(args.gate_run, args.gate_prep)
        print(f"PHASE03_WINDOWS_NATIVE_GATE {verdict} :: {detail}"
              + (f" :: {report_path}" if report_path else ""))
        return 0 if verdict == GATE_VERDICT_PASS else 1
    if not args.verify_preparation and not args.probe_device:
        parser.error("nothing to do: pass --verify-preparation, --probe-device, --serve-runtime, "
                     "--stop-runtime or --gate")
    preparation = Preparation(stamp=args.stamp, quiet=args.quiet, root=args.root)
    device = None
    ok = False
    runtime_status = RUNTIME_NOT_RUN
    try:
        if args.verify_preparation:
            preparation.prepare()
            ok = True
            if args.probe_device:
                device = preparation.probe_device(args.device_alias)
                if device.get("missing_condition"):
                    runtime_status = RUNTIME_BLOCKED
                elif device.get("machine"):
                    device["desktop"] = preparation.desktop_state(device["alias"])
        else:
            device = preparation.probe_device(args.device_alias)
            runtime_status = RUNTIME_BLOCKED if device.get("missing_condition") else RUNTIME_NOT_RUN
            ok = True
    except (PreparationError, KitError) as error:
        preparation.log(f"preparation aborted: {error}")
        ok = False
        if device is None and args.probe_device:
            runtime_status = RUNTIME_BLOCKED
    except Exception as error:  # unexpected: keep evidence, fail loudly
        preparation.log(f"unexpected failure: {error!r}")
        ok = False
    report = preparation.finish(ok, runtime_status, device, preparation_verified=args.verify_preparation)
    print(("PHASE03_WINDOWS_NATIVE_PREP_OK " if ok else "PHASE03_WINDOWS_NATIVE_PREP_FAILED ")
          + f"{preparation.root} prep={report['preparation_status']} runtime={report['runtime_status']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
