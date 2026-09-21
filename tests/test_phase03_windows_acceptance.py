"""03F Windows native acceptance gates (P0307WR).

These tests prove the *preparation*, *harness contract* and *strict gate* halves of
the Windows native acceptance with real files and injected failures, never with a
mock pass:

* the preparation tool independently proves the real cross-built program bytes
  against ``build/windows/descriptor.json`` (PE32+ x86-64 entry and dependency,
  standalone PCK 4.7.2, Windows ZIP path rules) and refuses a defective archive;
* the readiness kit is produced by the real platform lifecycle (isolated
  instance, frozen releases, authenticated downloads) - the lifecycle itself is
  executed by ``tools/phase03_verify_windows_native.py --verify-preparation`` and
  asserted here through its recorded contract, not re-mocked;
* the harness carries the real engineering contract (owned-PID launch, explicit
  ``queue.sqlite``/``writer.sqlite`` handling, prepared launchers, no name-based
  process kill, no default execution-policy bypass);
* the strict gate loads an explicitly selected run directory, recomputes the
  current source/build/descriptor/harness digests, re-reads the bound kit and the
  isolated instance database, and refuses a bare PASS, a doctor-only report, a
  stale kit, a skipped case, a wrong host/arch/build, a mismatched raw value and
  a corrupted evidence file;
* the device probe reports an unreachable or non-Windows host as a precise
  ``BLOCKED`` condition and never as runtime success.

The real lifecycle run and the real device probe are the tool commands
``.venv/bin/python tools/phase03_verify_windows_native.py --verify-preparation
--probe-device``; these tests never pretend to have executed Windows.
"""
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import types
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import phase03_verify_windows_native as native  # noqa: E402
import phase03_windows_kit as kit  # noqa: E402
import windows_native_harness as native_harness  # noqa: E402

PCK_HEADER = b"GDPC" + struct.pack("<IIII", 4, 4, 7, 2)
ENTRY = "GEP Synthetic Experiment.exe"
PCK = "GEP Synthetic Experiment.pck"
DEPENDENCY = "libgdsqlite.windows.template_release.x86_64.dll"
EXTENSION = "addons/godot-sqlite/gdsqlite.gdextension"
ROOT_NAME = "GEP Synthetic Experiment"
EXTENSION_TEXT = ("[configuration]\nentry_symbol = \"sqlite_library_init\"\ncompatibility_minimum = \"4.5\"\n"
                  "[libraries]\nwindows.release.x86_64 = "
                  "\"res://addons/godot-sqlite/bin/libgdsqlite.windows.template_release.x86_64.dll\"\n")
GUIDE = ROOT / "examples" / "synthetic_experiment" / "windows_native" / "WINDOWS_NATIVE_GUIDE.zh-CN.md"
TEMPLATE = ROOT / "examples" / "synthetic_experiment" / "windows_native" / "WINDOWS_NATIVE_RESULT_TEMPLATE.md"
HARNESS = ROOT / "tools" / "windows_native_harness.py"


def pe_image(machine=0x8664, magic=0x20B, dll=False, marker=b"synthetic"):
    optional = struct.pack("<H", magic) + b"\x00" * 62
    characteristics = 0x2000 | 0x0002 if dll else 0x0002
    header = b"MZ" + b"\x00" * 0x3A + struct.pack("<I", 0x40)
    image = header + b"PE\x00\x00" + struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, len(optional), characteristics)
    return image + optional + marker


def write_program(tmp_path):
    program = tmp_path / "program"
    program.mkdir()
    (program / ENTRY).write_bytes(pe_image(marker=b"entry"))
    (program / PCK).write_bytes(PCK_HEADER + b"payload" * 32)
    (program / DEPENDENCY).write_bytes(pe_image(dll=True, marker=b"sqlite"))
    (program / EXTENSION).parent.mkdir(parents=True)
    (program / EXTENSION).write_text(EXTENSION_TEXT)
    descriptor = {
        "version": "synthetic-1", "platform": "windows_x64", "host_version": "4.7.2",
        "sdk_version": "0.1.0", "protocol_version": "gep/1",
        "program_sha256": "0" * 64,
        "package": {"root": ROOT_NAME, "entry": ENTRY, "dependencies": [DEPENDENCY], "extension": EXTENSION},
        "schemas": {"exp.rt": {"id": "rt", "version": "1",
                               "schema": {"type": "object", "properties": {"rt_ms": {"type": "number"}},
                                          "required": ["rt_ms"]}}},
        "codebook": {"rt_ms": {"unit": "ms"}},
    }
    archive = tmp_path / "synthetic_windows.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name in (ENTRY, PCK, DEPENDENCY, EXTENSION):
            out.write(program / name, f"{ROOT_NAME}/{name}")
    descriptor["program_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    (tmp_path / "descriptor.json").write_text(json.dumps(descriptor))
    return archive, program, descriptor


class FakeTemplate:
    name = "windows_release_x86_64.exe"
    size = 109268480

    def stat(self):
        class _Stat:
            st_size = self.size
        return _Stat()


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    archive, program, descriptor = write_program(tmp_path)
    instance = native.Preparation(stamp="testsynthetic", root=tmp_path / "evidence")
    monkeypatch.setattr(native, "PROGRAM_ARCHIVE", archive)
    monkeypatch.setattr(native, "PROGRAM_ROOT", program)
    monkeypatch.setattr(native, "DESCRIPTOR", tmp_path / "descriptor.json")
    monkeypatch.setattr(instance, "godot_binary", lambda: "/fake/godot")
    monkeypatch.setattr(instance, "check_toolchain", lambda: ("/fake/godot", FakeTemplate()))
    return instance


# ---------------------------------------------------------------- descriptor
def test_descriptor_without_explicit_package_metadata_fails(preparation):
    descriptor = json.loads(native.DESCRIPTOR.read_text())
    descriptor.pop("package")
    with pytest.raises(native.PreparationError, match="显式声明入口"):
        preparation.check_descriptor_contract(descriptor)


@pytest.mark.parametrize("field,value", [
    ("platform", "macos_arm64"),
    ("host_version", "4.6.1"),
])
def test_descriptor_with_wrong_platform_or_version_fails(preparation, field, value):
    descriptor = json.loads(native.DESCRIPTOR.read_text())
    descriptor[field] = value
    with pytest.raises(native.PreparationError):
        preparation.check_descriptor_contract(descriptor)


def test_descriptor_checks_pass_for_the_declared_contract(preparation):
    descriptor = json.loads(native.DESCRIPTOR.read_text())
    package = preparation.check_descriptor_contract(descriptor)
    assert package["entry"] == ENTRY
    assert descriptor["program_sha256"] == hashlib.sha256(native.PROGRAM_ARCHIVE.read_bytes()).hexdigest()


# ------------------------------------------------------------ program archive
def test_program_archive_is_proven_to_be_real_x86_64_with_pck_version(preparation):
    descriptor = json.loads(native.DESCRIPTOR.read_text())
    program = preparation.check_program_archive(descriptor)
    assert program["entry"].endswith(ENTRY)
    assert program["pck"].endswith(PCK)
    assert any(check["label"] == "入口是真实 PE32+ x86-64 可执行映像" for check in preparation.checks)


def _rewrite_archive(archive, members):
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, raw in members.items():
            out.writestr(name, raw)


def _members(archive):
    with zipfile.ZipFile(archive) as source:
        return {item.filename: source.read(item.filename) for item in source.infolist()
                if not item.filename.endswith("/")}


def test_archive_with_a_tampered_entry_is_rejected(preparation):
    original = json.loads(native.DESCRIPTOR.read_text())
    archive = native.PROGRAM_ARCHIVE
    members = _members(archive)
    members[f"{ROOT_NAME}/{ENTRY}"] = b"not a PE image"
    _rewrite_archive(archive, members)
    descriptor = {**original, "program_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    with pytest.raises(native.PreparationError, match="PE32\\+ x86-64"):
        preparation.check_program_archive(descriptor)


def test_archive_with_a_path_escape_is_rejected(preparation):
    original = json.loads(native.DESCRIPTOR.read_text())
    archive = native.PROGRAM_ARCHIVE
    members = _members(archive)
    members[f"{ROOT_NAME}/../escape.exe"] = pe_image()
    _rewrite_archive(archive, members)
    descriptor = {**original, "program_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    with pytest.raises(native.PreparationError, match="Windows ZIP 路径规则"):
        preparation.check_program_archive(descriptor)


def test_archive_with_a_wrong_pck_version_is_rejected(preparation):
    original = json.loads(native.DESCRIPTOR.read_text())
    archive = native.PROGRAM_ARCHIVE
    members = _members(archive)
    members[f"{ROOT_NAME}/{PCK}"] = b"GDPC" + struct.pack("<IIII", 4, 4, 6, 1) + b"payload"
    _rewrite_archive(archive, members)
    descriptor = {**original, "program_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    with pytest.raises(native.PreparationError, match="4\\.7\\.2 引擎版本"):
        preparation.check_program_archive(descriptor)


def test_program_is_reproducible_only_with_matching_digest(preparation):
    descriptor = json.loads(native.DESCRIPTOR.read_text())
    assert preparation.program_is_reproducible(descriptor)
    descriptor["program_sha256"] = "0" * 64
    assert not preparation.program_is_reproducible(descriptor)


# ---------------------------------------------------------------- kit contract
def test_kit_format_is_the_real_lifecycle_contract():
    assert kit.KIT_VERSION == "gep-windows-kit/v2"
    assert kit.MODES == ("anonymous", "id", "password")
    text = (TOOLS / "phase03_windows_kit.py").read_text(encoding="utf-8")
    assert "publish_complete_artifact" not in text or "op=native_archive" in text
    assert "/releases/" in text and "/artifact" in text, "kit packages must come from the release endpoints"
    assert "REPLACED_AT_RELEASE_TIME" not in text, "the placeholder publication must be gone"
    assert "freeze_offline" not in text, "no DIY freeze may remain"


def _kit_require(ok, label, detail=None):
    if not ok:
        raise kit.KitError(label)
    return True


def test_kit_scans_for_credential_style_content(tmp_path):
    builder = kit.KitBuilder(tmp_path, lambda *args, **kwargs: True, _kit_require, quiet=True)
    clean = tmp_path / "clean.txt"
    clean.write_text("public synthetic material, no credentials")
    builder.check_no_secrets([clean])
    leaked = tmp_path / "leaked.txt"
    leaked.write_text("token = 'abcdef0123456789abcdef'")
    with pytest.raises(kit.KitError, match="凭据"):
        builder.check_no_secrets([leaked])


def test_private_accounts_artifact_is_0600_and_outside_the_kit(tmp_path):
    builder = kit.KitBuilder(tmp_path, lambda *args, **kwargs: True, _kit_require, quiet=True)
    builder.instance.instance_id = "synthetic-instance"
    builder.accounts["member"] = {"username": "member", "password": "x"}
    builder.runtime["expected"] = {"program_sha256": "0" * 64}
    path = builder._write_private_artifacts()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert builder.kit not in path.parents
    assert path.name == "runtime_accounts.json"
    owner = tmp_path / "private" / "owner_credentials.json"
    assert (owner.stat().st_mode & 0o777) == 0o600
    assert builder.kit not in owner.parents


def _kit_tunnel(port=8123):
    return {"listen": f"127.0.0.1:{port + 100}", "listen_port": port + 100,
            "target": f"127.0.0.1:{port}", "target_port": port, "tunnel_port": port + 100,
            "direction": "reverse", "preparation_address": "192.0.2.7", "windows_alias": "synthetic-win",
            "runtime_prerequisite": "READY", "pending_reason": None}


def test_kit_operator_launchers_need_no_manual_commands(tmp_path):
    """Old expectation: a sibling ``tools/`` path and a ``mac_host`` a human edits.

    New contract (P0308): the harness travels inside the kit, the frozen tunnel
    endpoint is Windows ``listen=port+100`` -> preparation host ``port``, and the
    tunnel file is a self-check of the automatic reverse route (no host field to
    fill in, no manual command).
    """
    builder = kit.KitBuilder(tmp_path, lambda *args, **kwargs: True, _kit_require, quiet=True)
    builder.runtime["port"] = 8123
    builder.runtime["tunnel"] = _kit_tunnel()
    builder.kit.mkdir(parents=True, exist_ok=True)
    builder._write_operator_material()
    for name in ("run-windows-checks.cmd", "tunnel.cmd", "designer-mode-anonymous.cmd",
                 "designer-mode-id.cmd", "designer-mode-password.cmd", "runtime.json"):
        assert (builder.kit / "operator" / name).is_file(), name
    assert (builder.kit / kit.KIT_HARNESS).is_file()
    runner = (builder.kit / "operator" / "run-windows-checks.cmd").read_text(encoding="utf-8")
    assert "--run" in runner and "--accounts" in runner and "pause" in runner
    assert "%HERE%harness\\windows_native_harness.py" in runner
    assert "..\\..\\tools" not in runner
    assert "Python 3.12" in runner
    check = (builder.kit / "operator" / "tunnel.cmd").read_text(encoding="utf-8")
    assert "set LISTEN=8223" in check and "127.0.0.1:8123" in check
    assert "MAC_HOST" not in check and "--serve-runtime" in check


def test_kit_relocatable_operator_tooling_carries_every_target(tmp_path):
    builder = kit.KitBuilder(tmp_path, lambda *args, **kwargs: True, _kit_require, quiet=True)
    builder.runtime["port"] = 8123
    builder.runtime["tunnel"] = _kit_tunnel()
    builder.kit.mkdir(parents=True, exist_ok=True)
    builder.private.mkdir(parents=True, exist_ok=True)
    (builder.private / "runtime_accounts.json").write_text("{}", encoding="utf-8")
    builder._write_operator_material()
    fresh = builder.check_relocatable_launchers()
    assert (fresh / "kit" / kit.KIT_HARNESS).is_file()
    assert (fresh / "private" / "runtime_accounts.json").is_file()
    for mode in kit.MODES:
        assert (fresh / "kit" / "operator" / f"designer-mode-{mode}.cmd").is_file()


def test_kit_relocatable_check_refuses_a_sibling_tools_reference(tmp_path):
    builder = kit.KitBuilder(tmp_path, lambda *args, **kwargs: True, _kit_require, quiet=True)
    builder.runtime["port"] = 8123
    builder.runtime["tunnel"] = _kit_tunnel()
    builder.kit.mkdir(parents=True, exist_ok=True)
    builder.private.mkdir(parents=True, exist_ok=True)
    (builder.private / "runtime_accounts.json").write_text("{}", encoding="utf-8")
    builder._write_operator_material()
    (builder.kit / "operator" / "run-windows-checks.cmd").write_text(
        "py -3.12 \"%KIT%\\..\\tools\\windows_native_harness.py\" --run", encoding="utf-8")
    with pytest.raises(kit.KitError, match="兄弟 tools"):
        builder.check_relocatable_launchers()


def test_kit_python_prerequisite_checks_the_real_version(tmp_path):
    """Old behavior: ``python -c "import sys"`` accepted any interpreter.

    The launcher must fail closed on a Python older than the pinned 3.12, so the
    probe itself has to compare ``sys.version_info`` and the relocatable check
    must refuse a probe that only imports ``sys``.
    """
    builder = kit.KitBuilder(tmp_path, lambda *args, **kwargs: True, _kit_require, quiet=True)
    builder.runtime["port"] = 8123
    builder.runtime["tunnel"] = _kit_tunnel()
    builder.kit.mkdir(parents=True, exist_ok=True)
    builder.private.mkdir(parents=True, exist_ok=True)
    (builder.private / "runtime_accounts.json").write_text("{}", encoding="utf-8")
    builder._write_operator_material()
    runner = (builder.kit / "operator" / "run-windows-checks.cmd").read_text(encoding="utf-8")
    assert "sys.version_info[:2]>=(3,12)" in runner
    assert '-c "import sys" >nul' not in runner, "the version-less probe must be gone"
    builder.check_relocatable_launchers()
    (builder.kit / "operator" / "run-windows-checks.cmd").write_text(
        "py -3.12 -c \"import sys\" >nul 2>nul\r\n"
        "rem Python 3.12 前置\r\n"
        "%PY% \"%HERE%harness\\windows_native_harness.py\" --run\r\n", encoding="utf-8")
    with pytest.raises(kit.KitError, match="真实版本"):
        builder.check_relocatable_launchers()


def test_kit_tunnel_contract_is_the_frozen_endpoint(tmp_path):
    builder = kit.KitBuilder(tmp_path, lambda *args, **kwargs: True, _kit_require, quiet=True)
    builder.runtime["port"] = 8123
    builder.runtime["tunnel"] = {"listen": "127.0.0.1:8223", "listen_port": 8223,
                                 "target": "127.0.0.1:8123", "target_port": 8123,
                                 "tunnel_port": 8223, "direction": "reverse",
                                 "runtime_prerequisite": "READY"}
    builder.kit.mkdir(parents=True, exist_ok=True)
    builder.private.mkdir(parents=True, exist_ok=True)
    (builder.private / "runtime_accounts.json").write_text("{}", encoding="utf-8")
    builder._write_operator_material()
    (builder.kit / "operator" / "tunnel.cmd").write_text("set LISTEN=8123", encoding="utf-8")
    with pytest.raises(kit.KitError, match="端点契约"):
        builder.check_relocatable_launchers()


def test_kit_ssh_resolution_uses_the_task_local_binary(tmp_path, monkeypatch):
    """The preparation address must resolve through the same scoped route override."""
    marker = tmp_path / "kit-ssh-used.txt"
    fake = tmp_path / "ssh-kit.sh"
    fake.write_text("#!/bin/sh\n"
                    f"printf '%s\\n' \"$*\" >> {marker}\n"
                    "printf 'user synthetic\\nhostname 100.64.0.7\\nport 22\\n'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("GEP_SSH_BIN", str(fake))
    assert kit.ssh_resolved_hostname("example-win") == "100.64.0.7"
    recorded = marker.read_text() if marker.is_file() else ""
    assert "-G example-win" in recorded


# ------------------------------------------------------------------- harness
def test_harness_carries_the_owned_pid_contract():
    text = HARNESS.read_text(encoding="utf-8")
    for token in ("--doctor", "--run", "--designer-launch", "--kit", "--runtime", "--accounts",
                  "--run-root", "--json-out", "queue.sqlite", "writer.sqlite", "icacls"):
        assert token in text, token
    for name in native.WN_ITEMS:
        assert name in text, name
    assert "taskkill" not in text and "IMAGENAME" not in text
    assert "ExecutionPolicy" not in text
    assert "Stop-Process -Id" in text
    assert "runtime prerequisite missing" in text
    for token in ("def spawn_launcher", "def close_launch", "def abort_launch", "def verify_identity"):
        assert token in text, token
    assert "subprocess.run([\"powershell\", \"-NoProfile\", \"-File\"" not in text, \
        "the interactive branch must not run the launcher to completion"


def test_harness_doctor_never_emits_a_wn01_pass(tmp_path):
    result = __import__("subprocess").run(
        [sys.executable, str(HARNESS), "--doctor", "--kit", str(tmp_path / "missing"),
         "--runtime", str(tmp_path / "missing.json"), "--run-root", str(tmp_path / "run")],
        capture_output=True, text=True)
    assert result.returncode != 0
    report = json.loads((tmp_path / "run" / "run.json").read_text())
    assert report["runtime_acceptance"] != "PASS"
    assert not any(case["id"] == "WN01" and case["status"] == "PASS" for case in report["cases"])


def test_harness_requires_the_scoped_tunnel_before_any_runtime_case(tmp_path):
    """A Windows-less unit check of the preflight contract: no tunnel -> BLOCKED."""
    text = HARNESS.read_text(encoding="utf-8")
    assert "def preflight_tunnel" in text
    assert "case.block(" in text
    assert "BLOCKED" in text


def test_harness_never_records_credentials_in_its_report(tmp_path):
    text = HARNESS.read_text(encoding="utf-8")
    assert "SECRET_PATTERNS" in text
    assert "secret-free" not in text.lower() or "凭据" in text


def test_run_keeps_a_prerequisite_failure_and_skips_runtime_cases(tmp_path, monkeypatch):
    """Old behavior cleared the WN01 preflight checks; a real failure must survive."""
    harness = native_harness.Harness(kit=tmp_path / "kit", runtime=tmp_path / "runtime.json",
                                     run_root=tmp_path / "run")
    harness.api_port = 8123
    harness.tunnel_port = 8223
    monkeypatch.setattr(native_harness.sys, "platform", "win32")
    monkeypatch.setattr(harness, "preflight_tunnel", lambda case: True)
    monkeypatch.setattr(harness, "set_proxy", lambda mode: None)
    monkeypatch.setattr(harness, "check_kit_integrity",
                        lambda case: case.check(False, "注入的 kit 完整性失败"))
    monkeypatch.setattr(harness, "check_delivery", lambda case: None)
    monkeypatch.setattr(harness, "check_accounts_acl", lambda case: None)
    monkeypatch.setattr(harness, "authenticate_member",
                        lambda case: pytest.fail("runtime cases must not start"))
    monkeypatch.setattr(harness, "case_wn01", lambda: pytest.fail("WN01 must not run"))
    harness.run()
    wn01 = harness.cases["WN01"]
    assert any(not check["ok"] and "kit 完整性" in check["label"] for check in wn01.checks)
    assert wn01.status == native_harness.STATUS_FAIL
    assert harness.cases["WN02"].status == native_harness.STATUS_BLOCKED


def test_designer_launch_owns_the_connection_lifecycle(tmp_path, monkeypatch):
    """The proxy spans the program use and is released with our own launch."""
    harness = native_harness.Harness(kit=tmp_path / "kit", runtime=tmp_path / "runtime.json",
                                     run_root=tmp_path / "run", mode="anonymous")
    harness.releases = {"modes": {"anonymous": {"release_id": "r", "study_id": "s", "build_id": "b",
                                                "delivery": "delivery/anonymous.zip",
                                                "_manifest": {"entry": "app.exe"}}}}
    monkeypatch.setattr(native_harness.sys, "platform", "win32")
    events = []

    class FakeProxy:
        def stop(self):
            events.append("proxy-stopped")

    class FakeLauncher:
        def wait(self, timeout=None):
            events.append("program-closed")
            return 0

    launch = native_harness.Launch("designer-anonymous", "C:/app.exe", "C:/storage", [],
                                   tmp_path / "record.json", tmp_path / "exit.json",
                                   tmp_path / "out.txt", tmp_path / "err.txt", tmp_path / "s.ps1", True)
    launch.record = {"pid": 99, "path": "C:/app.exe", "session_id": 1, "start_time": "t"}
    launch.launcher = FakeLauncher()
    monkeypatch.setattr(harness, "preflight_tunnel", lambda case: case.check(True, "隧道可达") and True)
    monkeypatch.setattr(harness, "set_proxy",
                        lambda mode: (events.append(f"proxy:{mode}"),
                                      setattr(harness, "proxy", FakeProxy())))
    monkeypatch.setattr(harness, "extract", lambda package, mode: tmp_path)
    monkeypatch.setattr(harness, "start_program",
                        lambda *args, **kwargs: (events.append("program-started"), launch)[1])
    monkeypatch.setattr(harness, "close_launch", lambda item: (events.append("launch-closed"), item)[1])
    result = harness.designer_launch()
    assert result["designer"] is True
    assert events == ["proxy:pass", "program-started", "program-closed", "launch-closed", "proxy-stopped"]
    assert harness.proxy is None


def test_wait_owned_program_uses_the_owned_exit_record_without_a_launcher(tmp_path, monkeypatch):
    """Scheduled launches own no launcher handle; the script's exit record is the signal.

    Old behavior: ``designer_launch`` only waited for ``launch.launcher``, so a
    scheduled launch (no handle) released the task and the fault proxy while the
    program was still running. The owned exit record is the only acceptable
    signal, and no foreign process is probed or stopped.
    """
    harness = native_harness.Harness(run_root=tmp_path / "run")
    exit_path = tmp_path / "exit.json"
    launch = native_harness.Launch("designer-anonymous", "C:/app.exe", "C:/storage", [],
                                   tmp_path / "record.json", exit_path, tmp_path / "out.txt",
                                   tmp_path / "err.txt", tmp_path / "s.ps1", False)
    launch.record = {"pid": 4242, "path": "C:/app.exe"}
    launch.task_name = "GEP-Native-WN-test"
    commands = []
    monkeypatch.setattr(native_harness, "run_powershell_command",
                        lambda command, timeout=120: commands.append(command) or command_result())

    def exit_later():
        time.sleep(0.2)
        exit_path.write_text(json.dumps({"pid": 4242, "exit": 0}), encoding="utf-8")

    thread = threading.Thread(target=exit_later, daemon=True)
    thread.start()
    assert harness.wait_owned_program(launch, timeout=30, poll=0.05) is True
    assert launch.launch_exit == 0
    assert commands == [], "waiting for our own exit record must never probe or stop a foreign process"
    assert harness.wait_owned_program(launch, timeout=0.1, poll=0.02) is True


def test_designer_launch_scheduled_keeps_the_proxy_until_the_owned_program_exits(tmp_path, monkeypatch):
    """A delayed scheduled program exit keeps the fault proxy and own task alive."""
    harness = native_harness.Harness(kit=tmp_path / "kit", runtime=tmp_path / "runtime.json",
                                     run_root=tmp_path / "run", mode="anonymous")
    harness.releases = {"modes": {"anonymous": {"release_id": "r", "study_id": "s", "build_id": "b",
                                                "delivery": "delivery/anonymous.zip",
                                                "_manifest": {"entry": "app.exe"}}}}
    monkeypatch.setattr(native_harness.sys, "platform", "win32")
    events = []
    exit_path = tmp_path / "exit.json"

    class FakeProxy:
        def stop(self):
            events.append("proxy-stopped")

    launch = native_harness.Launch("designer-anonymous", "C:/app.exe", "C:/storage", [],
                                   tmp_path / "record.json", exit_path, tmp_path / "out.txt",
                                   tmp_path / "err.txt", tmp_path / "s.ps1", False)
    launch.record = {"pid": 77, "path": "C:/app.exe", "session_id": 1, "start_time": "t"}
    launch.launcher = None
    launch.task_name = "GEP-Native-WN-scheduled"
    commands = []
    monkeypatch.setattr(harness, "run_os",
                        lambda command, timeout=60: commands.append(list(command)) or command_result())
    monkeypatch.setattr(harness, "preflight_tunnel", lambda case: case.check(True, "隧道可达") and True)
    monkeypatch.setattr(harness, "set_proxy",
                        lambda mode: (events.append(f"proxy:{mode}"),
                                      setattr(harness, "proxy", FakeProxy())))
    monkeypatch.setattr(harness, "extract", lambda package, mode: tmp_path)
    monkeypatch.setattr(harness, "start_program",
                        lambda *args, **kwargs: (events.append("program-started"), launch)[1])
    real_close = harness.close_launch
    monkeypatch.setattr(harness, "close_launch",
                        lambda item: (events.append("launch-closed"), real_close(item))[1])
    real_wait = harness.wait_owned_program
    monkeypatch.setattr(harness, "wait_owned_program",
                        lambda item, **kwargs: real_wait(item, timeout=30, poll=0.05))

    def exit_later():
        time.sleep(0.3)
        exit_path.write_text(json.dumps({"pid": 77, "exit": 0}), encoding="utf-8")

    threading.Thread(target=exit_later, daemon=True).start()
    result = harness.designer_launch()
    assert result["designer"] is True
    assert events == ["proxy:pass", "program-started", "launch-closed", "proxy-stopped"], events
    assert harness.proxy is None
    assert launch.launch_exit == 0
    assert launch.task_name is None
    ended = [command[3] for command in commands if "/end" in command]
    deleted = [command[3] for command in commands if "/delete" in command]
    assert ended == ["GEP-Native-WN-scheduled"] and deleted == ["GEP-Native-WN-scheduled"]
    assert all("Stop-Process" not in " ".join(command) for command in commands)


def test_designer_launch_refuses_without_the_tunnel(tmp_path, monkeypatch):
    """An unprepared external prerequisite is reported, never a ready-to-click launch."""
    harness = native_harness.Harness(kit=tmp_path / "kit", runtime=tmp_path / "runtime.json",
                                     run_root=tmp_path / "run", mode="anonymous")
    monkeypatch.setattr(native_harness.sys, "platform", "win32")

    def blocked(case):
        case.block("runtime prerequisite missing: TCP 127.0.0.1:8223 unreachable")
        case.check(False, "作用域隧道端口可达（运行前置条件）")
        return False

    monkeypatch.setattr(harness, "preflight_tunnel", blocked)
    monkeypatch.setattr(harness, "set_proxy", lambda mode: pytest.fail("no proxy without the tunnel"))
    monkeypatch.setattr(harness, "start_program", lambda *args, **kwargs: pytest.fail("no program without the tunnel"))
    result = harness.designer_launch()
    assert result["designer"] is False
    assert result["runtime_prerequisite"] == "PENDING"
    assert harness.cases["WN01"].status == native_harness.STATUS_BLOCKED


def test_close_owned_launches_releases_only_this_harness(tmp_path, monkeypatch):
    harness = native_harness.Harness(run_root=tmp_path)
    events = []

    def make(label, record):
        launch = native_harness.Launch(label, "e", "s", [], tmp_path / "r.json", tmp_path / "e.json",
                                       tmp_path / "o.txt", tmp_path / "er.txt", tmp_path / "s.ps1", True)
        launch.record = record
        harness.launches.append(launch)

    make("with-record", {"pid": 1})
    make("without-record", {})
    monkeypatch.setattr(harness, "close_launch", lambda item: (events.append(("closed", item.label)), item)[1])
    monkeypatch.setattr(harness, "abort_launch", lambda item: (events.append(("aborted", item.label)), item)[1])
    harness.close_owned_launches()
    assert events == [("closed", "with-record"), ("aborted", "without-record")]


# ------------------------------------------------------------------ documents
def test_guide_and_template_cover_every_wn_item_without_human_commands():
    guide = GUIDE.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    for name in native.WN_ITEMS:
        assert name in guide and name in template
    assert "不需要输入任何命令" in guide
    assert "不是签名" in guide
    assert "不会自动拒绝" in guide or "运行时不做" in guide
    assert "未运行" in template
    assert "package 摘要" in template
    assert "实际结果" in template


def test_preparation_checks_the_documents_and_harness_contract(preparation):
    preparation.check_documents()
    preparation.check_harness()
    assert all(check["ok"] for check in preparation.checks), preparation.checks


# --------------------------------------------------------------- CLI wiring
def test_main_requires_an_explicit_action():
    result = __import__("subprocess").run([sys.executable, str(TOOLS / "phase03_verify_windows_native.py")],
                                          capture_output=True, text=True)
    assert result.returncode != 0
    assert "nothing to do" in (result.stderr + result.stdout)


def test_preparation_paths_default_into_the_ignored_evidence_root():
    # Old expectation: the P0307WR evidence root. P0308 owns its own task root.
    assert native.RUN_ROOT.name == "p0308"
    assert kit.RUN_ROOT.name == "p0308"
    assert "phase03_20260920" in str(native.RUN_ROOT)
    assert "local_data" in str(native.RUN_ROOT)


# ------------------------------------------------------------------- device
def test_unreachable_device_is_recorded_as_blocked_never_pass(preparation, monkeypatch):
    monkeypatch.setattr(preparation, "ssh_alias", lambda: (["example-win"], None))

    def fake_run(command, **kwargs):
        if "-G" in command:
            return __import__("subprocess").CompletedProcess(command, 0, "hostname 127.0.0.1\nport 65000\n")
        return __import__("subprocess").CompletedProcess(command, 255, "", "connection refused")

    monkeypatch.setattr("subprocess.run", fake_run)
    probe = preparation.probe_device()
    assert probe["alias"] == "example-win"
    assert probe["missing_condition"]
    assert probe["machine"] is None


def test_missing_alias_is_a_precise_blocker(preparation, monkeypatch):
    monkeypatch.setattr(preparation, "ssh_alias", lambda: (None, "no ~/.ssh/config"))
    probe = preparation.probe_device()
    assert probe["missing_condition"] == "documented Windows SSH alias not found"
    assert probe["machine"] is None


def test_non_windows_host_is_blocked(preparation, monkeypatch):
    monkeypatch.setattr(preparation, "ssh_alias", lambda: (["example-win"], None))
    monkeypatch.setattr(preparation, "ssh_options", lambda: [])

    def fake_run(command, **kwargs):
        payload = json.dumps({"Caption": "Ubuntu 24.04", "Arch": "64-bit", "Desktop": True})
        return __import__("subprocess").CompletedProcess(command, 0, payload + "\n", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    probe = preparation.probe_device()
    assert "not Windows" in probe["missing_condition"]


def test_windows_desktop_host_is_recorded_ready(preparation, monkeypatch):
    monkeypatch.setattr(preparation, "ssh_alias", lambda: (["example-win"], None))
    monkeypatch.setattr(preparation, "ssh_options", lambda: [])

    def fake_run(command, **kwargs):
        if "-G" in command:
            return __import__("subprocess").CompletedProcess(command, 0, "hostname 127.0.0.1\nport 65000\n")
        payload = json.dumps({"Caption": "Microsoft Windows 11 Pro", "Version": "10.0.26200",
                              "Arch": "64-bit", "System": "x64-based PC", "Desktop": True,
                              "User": "synthetic", "Session": "services"})
        return __import__("subprocess").CompletedProcess(command, 0, payload + "\n", "")

    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    original = fake_run

    def with_open_port(command, **kwargs):
        if "-G" in command:
            return __import__("subprocess").CompletedProcess(command, 0, f"hostname 127.0.0.1\nport {port}\n")
        return original(command, **kwargs)

    monkeypatch.setattr("subprocess.run", with_open_port)
    try:
        probe = preparation.probe_device()
    finally:
        listener.close()
    assert probe["missing_condition"] is None
    assert probe["machine"]["Caption"].startswith("Microsoft Windows")


def test_device_probe_uses_the_task_local_ssh_binary(preparation, tmp_path, monkeypatch):
    """The device probe honours GEP_SSH_BIN (scoped route override), like the tunnel."""
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    marker = tmp_path / "ssh-used.txt"
    fake = tmp_path / "ssh-local.sh"
    payload = json.dumps({"Caption": "Microsoft Windows 11 Pro", "Version": "10.0.26200",
                          "Arch": "64-bit", "System": "x64-based PC", "Desktop": True,
                          "User": "synthetic", "Session": "services"})
    fake.write_text("#!/bin/sh\n"
                    f"printf '%s\\n' \"$*\" >> {marker}\n"
                    "case \"$*\" in\n"
                    f"  *-G*) printf 'hostname 127.0.0.1\\nport {port}\\n' ;;\n"
                    f"  *) printf '%s\\n' '{payload}' ;;\n"
                    "esac\n")
    fake.chmod(0o755)
    monkeypatch.setenv("GEP_SSH_BIN", str(fake))
    monkeypatch.setattr(preparation, "ssh_alias", lambda: (["example-win"], None))
    monkeypatch.setattr(preparation, "ssh_options", lambda: [])
    try:
        probe = preparation.probe_device()
    finally:
        listener.close()
    assert probe["missing_condition"] is None
    assert probe["machine"]["Caption"].startswith("Microsoft Windows")
    recorded = marker.read_text() if marker.is_file() else ""
    assert "-G example-win" in recorded and "example-win" in recorded


def test_report_separates_preparation_from_runtime_status(preparation):
    report = preparation.finish(True, native.RUNTIME_BLOCKED,
                                {"alias": "example-win", "missing_condition": "TCP unreachable"})
    assert report["preparation_status"] == native.PREP_PREPARED
    assert report["runtime_status"] == native.RUNTIME_BLOCKED
    assert report["designer_autonomous_qa"] == "NOT_RUN"
    assert report["independent_t17"] == "NOT_RUN"
    assert "runtime_status" in (preparation.root / "readiness.json").read_text()


def test_failed_preparation_is_never_prepared(preparation):
    preparation.record(False, "synthetic injected failure")
    report = preparation.finish(False, native.RUNTIME_NOT_RUN, None)
    assert report["preparation_status"] == native.PREP_FAILED
    assert report["checks_failed"] == 1


# ------------------------------------------------------- strict final gate
def _store_copy(path, sessions):
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,value TEXT NOT NULL)")
        for document in sessions:
            connection.execute("INSERT OR REPLACE INTO sessions VALUES(?,?)",
                               [document["id"], json.dumps(document)])
        connection.commit()
    finally:
        connection.close()


def _snapshot(harness, label, tmp_path, sessions):
    """One store snapshot exactly as the real harness writes it.

    The live store is built in a storage directory, then the harness preserves
    its raw bytes and derives the self-contained snapshot the gate reads, so the
    fixture cannot drift from the real evidence shape.
    """
    storage = tmp_path / "stores" / label
    storage.mkdir(parents=True, exist_ok=True)
    _store_copy(storage / "queue.sqlite", sessions)
    return harness.snapshot_store(label, storage)


def _authorized_export(harness, label, tmp_path, session_id, rows):
    """One study-scoped download preserved, then derived for one session."""
    target = harness.evidence_dir(label) / "export.jsonl"
    payload = ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode("utf-8")
    selected, info = harness.scoped_export(payload, session_id, target)
    return selected, info, target


def _write_data_only_output(harness, session_id):
    """The real program output of one data-only recovery, exactly as captured."""
    path = harness.evidence_dir("WN04-data-only") / "stdout.txt"
    path.write_text("Godot Engine v4.7.2.stable.official.ed1daf0bf\n"
                    "SYNTHETIC_NAMED none\nSYNTHETIC_DATA_ONLY\n"
                    f"Closed database (.../wn04-data-only/queue.sqlite)\n", encoding="utf-8")
    return path


def _event(event_id, value, session_id=None):
    return {"event_id": event_id, "session_id": session_id, "segment_id": "seg-1", "sequence": 1,
            "event_type": "exp.rt", "payload": {"trial_id": "t1", "choice": "left",
                                                "rt_ms": value, "response_status": "responded"},
            "observed_time": {"value": value, "unit": "ms", "clock_id": "host_monotonic",
                              "epoch": "t0", "source": "Godot Time.get_ticks_usec"}}


def _session_document(session_id, events, pending=None, segments=None, checkpoint=None):
    return {"id": session_id, "kind": "session", "records": events, "pending": pending or [],
            "segments": segments or ["seg-1"], "checkpoint": checkpoint,
            "completion": {"event_ids": [event["event_id"] for event in events]}}


def _db_session(connection, session_id, release_id, events):
    """The bound server side of one session: session row plus raw event envelopes."""
    connection.execute("INSERT INTO core_session VALUES(?,?)",
                       [session_id.replace("-", ""), release_id.replace("-", "")])
    for event in events:
        connection.execute("INSERT INTO core_event VALUES(?,?)",
                           [session_id.replace("-", ""), json.dumps(event, ensure_ascii=False)])


def _fake_world(tmp_path, monkeypatch):
    """One bound preparation + one valid device run, built from real files."""
    prep = tmp_path / "prep"
    kit_dir = prep / "kit"
    (kit_dir / "delivery" / "anonymous").mkdir(parents=True)
    (kit_dir / "delivery" / "id").mkdir(parents=True)
    (kit_dir / "delivery" / "password").mkdir(parents=True)
    archive, program, descriptor = write_program(tmp_path)
    monkeypatch.setattr(native, "PROGRAM_ARCHIVE", archive)
    monkeypatch.setattr(native, "PROGRAM_ROOT", program)
    monkeypatch.setattr(native, "DESCRIPTOR", tmp_path / "descriptor.json")
    monkeypatch.setattr(native, "HARNESS", HARNESS)
    instance_dir = prep / "instance-root" / "instance"
    instance_dir.mkdir(parents=True)
    db = instance_dir / "gep.sqlite3"
    connection = sqlite3.connect(db)
    connection.executescript(
        "CREATE TABLE core_instance(instance_id TEXT);"
        "CREATE TABLE core_study(id TEXT, current_release_id TEXT);"
        "CREATE TABLE core_session(id TEXT, release_id TEXT);"
        "CREATE TABLE core_event(session_id TEXT, envelope TEXT);"
        "CREATE TABLE core_release(id TEXT, study_id TEXT, build_id TEXT, artifact_digest TEXT, approved INT, config TEXT);")
    instance_id = "11111111-2222-3333-4444-555555555555"
    connection.execute("INSERT INTO core_instance VALUES(?)", [instance_id])
    modes = {}
    for index, mode in enumerate(native.MODES):
        release_id = f"{index}{'0' * 7}-0000-0000-0000-000000000000"
        study_id = f"{index}{'1' * 7}-0000-0000-0000-000000000000"
        build_id = f"{index}{'2' * 7}-0000-0000-0000-000000000000"
        package = kit_dir / "delivery" / mode / f"gep-{release_id}.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as out:
            out.writestr("artifact_manifest.json", json.dumps(
                {"artifact_format_version": "gep-artifact/v1", "platform": "windows_x64",
                 "program_sha256": descriptor["program_sha256"]}))
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        connection.execute("INSERT INTO core_release VALUES(?,?,?,?,?,?)",
                           [release_id.replace("-", ""), study_id.replace("-", ""), build_id.replace("-", ""),
                            digest, 1, json.dumps({"mode": mode, "artifact_format_version": "gep-artifact/v1"})])
        modes[mode] = {"mode": mode, "study_id": study_id, "release_id": release_id, "build_id": build_id,
                       "artifact_sha256": digest, "artifact_size": package.stat().st_size,
                       "package_sha256": digest, "package_size": package.stat().st_size,
                       "delivery": f"delivery/{mode}/gep-{release_id}.zip"}
    alternate_id = "99999999-0000-0000-0000-000000000000"
    connection.execute("INSERT INTO core_release VALUES(?,?,?,?,?,?)",
                       [alternate_id.replace("-", ""), modes["password"]["study_id"].replace("-", ""),
                        modes["password"]["build_id"].replace("-", ""), "f" * 64, 1,
                        json.dumps({"mode": "password", "artifact_format_version": "gep-artifact/v1"})])
    connection.execute("INSERT INTO core_study VALUES(?,?)", [modes["password"]["study_id"].replace("-", ""),
                                                             alternate_id.replace("-", "")])
    (kit_dir / "releases.json").write_text(json.dumps({"kit_format": kit.KIT_VERSION, "instance_id": instance_id,
                                                       "modes": modes}))
    (kit_dir / "integrity.json").write_text(json.dumps({"format": kit.KIT_VERSION, "members": {}}))
    (prep / "readiness.json").write_text(json.dumps({
        "preparation_status": "PREPARED", "kit": {"path": str(kit_dir)}, "runtime_status": "NOT_RUN"}))

    run_dir = tmp_path / "windows-run"
    evidence = run_dir / "evidence"
    evidence.mkdir(parents=True)
    harness = native_harness.Harness(run_root=run_dir)
    cases = []
    for case_id in native.WN_ITEMS:
        cases.append({"id": case_id, "title": case_id, "status": "PASS", "reason": "",
                      "checks": [{"ok": True, "label": "synthetic", "detail": None}], "evidence": {}})
    cases[0]["evidence"]["launch"] = {"pid": 4242, "window_title": "GEP Synthetic Experiment",
                                      "executable_path": "C:/run/GEP Synthetic Experiment.exe",
                                      "console_session": 1, "stopped_owned_pid": True}
    for index, mode in enumerate(native.MODES):
        label = f"WN02-{mode}"
        session_id = modes[mode]["release_id"]
        events = [_event(f"{index}0000000-0000-0000-0000-00000000000{position}",
                         321.5 if position == 0 else 217.25, session_id=session_id)
                  for position in range(4)]
        store_copy = _snapshot(harness, label, tmp_path, [_session_document(session_id, events)])
        _db_session(connection, session_id, modes[mode]["release_id"], events)
        rows = [{"study_id": modes[mode]["study_id"], "release_id": modes[mode]["release_id"],
                 "build_id": modes[mode]["build_id"], "record": event} for event in events]
        _selected, export_full, export = _authorized_export(harness, label, tmp_path, session_id, rows)
        cases[1]["evidence"].setdefault("modes", {})[mode] = {
            "session_id": session_id, "boundary_event_ids": [event["event_id"] for event in events[:2]],
            "boundary_raw_values": [321.5], "export_ids": [event["event_id"] for event in events],
            "export_values": [217.25, 321.5], "export_path": export_full["subset_path"],
            "export_sha256": export_full["subset_sha256"], "export_full": export_full,
            "store_copy": store_copy}
        cases[4]["evidence"].setdefault("modes", {})[mode] = {
            "local_event_ids": [event["event_id"] for event in events[:2]], "remote_event_ids": [event["event_id"] for event in events],
            "local_values": [321.5], "remote_values": [217.25, 321.5],
            "export_path": export_full["subset_path"],
            "export_sha256": export_full["subset_sha256"]}
    # WN03
    offline_session = "30000000-0000-0000-0000-0000000000ff"
    offline_events = [_event("30000000-0000-0000-0000-000000000000", 321.5, session_id=offline_session),
                      _event("30000000-0000-0000-0000-000000000001", 217.25, session_id=offline_session)]
    offline_copy = _snapshot(harness, "WN03-offline", tmp_path,
                            [_session_document(offline_session, offline_events, pending=["a"])])
    reconnect_copy = _snapshot(harness, "WN03-reconnect", tmp_path,
                               [_session_document(offline_session, offline_events)])
    _db_session(connection, offline_session, modes["password"]["release_id"], offline_events)
    _selected, wn03_full, wn03_export = _authorized_export(
        harness, "WN03", tmp_path, offline_session,
        [{"study_id": modes["password"]["study_id"], "release_id": modes["password"]["release_id"],
          "build_id": modes["password"]["build_id"], "record": event} for event in offline_events])
    cases[2]["evidence"].update({
        "offline": {"session_id": offline_session, "event_ids": [event["event_id"] for event in offline_events],
                    "pending": ["a"], "raw_values": [321.5],
                    "store_copy": offline_copy},
        "reconnect": {"session_id": offline_session, "event_ids": [event["event_id"] for event in offline_events],
                      "store_copy": reconnect_copy},
        "export": {"event_ids": [event["event_id"] for event in offline_events], "values": [217.25, 321.5],
                   "path": wn03_full["subset_path"], "sha256": wn03_full["subset_sha256"],
                   "session_id": offline_session, "export_full": wn03_full},
        "lost_ack": {"events": 4, "unique": 4}})
    # WN04
    short_session = "40000000-0000-0000-0000-0000000000ff"
    short_events = [_event(f"40000000-0000-0000-0000-00000000000{position}", 321.5, session_id=short_session)
                    for position in range(4)]
    short_copy = _snapshot(harness, "WN04-short-code", tmp_path,
                          [_session_document(short_session, short_events)])
    _db_session(connection, short_session, modes["password"]["release_id"], short_events)
    recovery = evidence / "WN04-recovery-export.json"
    recovery.write_text(json.dumps({"format_version": 1, "session_id": "40000000-0000-0000-0000-0000000000ff",
                                    "binding": {"instance_id": instance_id, "study_id": modes["password"]["study_id"],
                                                "release_id": modes["password"]["release_id"],
                                                "build_id": modes["password"]["build_id"]},
                                    "records": short_events, "pending": [], "completion": None}))
    cases[3]["evidence"].update({
        "short_code": {"session_id": short_session,
                       "segments": ["a", "b"], "event_ids": [event["event_id"] for event in short_events],
                       "replay_exit": 2, "store_copy": short_copy},
        "expiry": {"waited_seconds": 300, "exit": 2},
        "shared_writer": {"holder_pid": 77, "second_exit": 2},
        "data_only": {"exit": 2, "stdout_tail": "SYNTHETIC_DATA_ONLY",
                      "stdout_path": harness.rel(_write_data_only_output(harness, short_session)),
                      "store_copy": _snapshot(harness, "WN04-data-only", tmp_path,
                                              [_session_document(short_session, short_events)])},
        "recovery_export": {"path": "evidence/WN04-recovery-export.json", "binding": {"instance_id": instance_id},
                            "records": 4, "sha256": hashlib.sha256(recovery.read_bytes()).hexdigest()}})
    old_session = "60000000-0000-0000-0000-0000000000ff"
    old_events = [_event(f"60000000-0000-0000-0000-00000000000{position}", 321.5, session_id=old_session)
                  for position in range(4)]
    old_copy = _snapshot(harness, "WN06-old-release", tmp_path,
                        [_session_document(old_session, old_events)])
    _db_session(connection, old_session, modes["password"]["release_id"], old_events)
    _selected, old_full, old_export = _authorized_export(
        harness, "WN06", tmp_path, old_session,
        [{"study_id": modes["password"]["study_id"], "release_id": modes["password"]["release_id"],
          "build_id": modes["password"]["build_id"], "record": event} for event in old_events])
    cases[5]["evidence"]["old_release"] = {
        "release_id": modes["password"]["release_id"], "study_id": modes["password"]["study_id"],
        "session_id": old_session,
        "event_ids": [event["event_id"] for event in old_events],
        "export_path": old_full["subset_path"],
        "export_sha256": old_full["subset_sha256"],
        "export_event_ids": [event["event_id"] for event in old_events],
        "export_full": old_full,
        "store_copy": old_copy}
    run = {"format": native.RUN_FORMAT, "task": "P0307WR", "mode": "run", "run_id": "synthetic",
           "host": {"platform": "win32", "processor_architecture": "AMD64",
                    "machine": {"Caption": "Microsoft Windows 11 Pro", "Arch": "64-bit", "System": "x64-based PC"},
                    "console_session": 1},
           "binding": {"instance_id": instance_id, "kit_format": kit.KIT_VERSION, "studies": modes},
           "expected": {"program_sha256": descriptor["program_sha256"],
                        "descriptor_sha256": hashlib.sha256(native.DESCRIPTOR.read_bytes()).hexdigest(),
                        "program_source_digest": kit.program_source_digest()},
           "kit_integrity_sha256": hashlib.sha256((kit_dir / "integrity.json").read_bytes()).hexdigest(),
           "harness_sha256": hashlib.sha256(HARNESS.read_bytes()).hexdigest(),
           "cases": cases, "runtime_acceptance": "PASS"}
    connection.commit()
    connection.close()
    (run_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2))
    return prep, run_dir, modes, descriptor


def test_gate_accepts_a_bound_complete_device_run(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_PASS, result["problems"]


def test_gate_requires_an_explicit_run_selection():
    verdict, detail, path = native.gate_verdict(None, None)
    assert verdict == native.GATE_VERDICT_SELECTION_REQUIRED
    assert path is None
    assert "never trusted" in detail


def test_gate_refuses_a_bare_runtime_pass_without_cases(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    report["cases"] = [{"id": "WN01", "checks": [], "evidence": {}}]
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("missing" in problem for problem in result["problems"])


def test_gate_refuses_doctor_only_evidence(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    report["mode"] = "doctor"
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("doctor" in problem for problem in result["problems"])


def test_gate_refuses_wrong_host_or_architecture(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    report["host"]["processor_architecture"] = "ARM64"
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("x64" in problem for problem in result["problems"])


def test_gate_refuses_a_stale_build_or_source_tree(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    report["expected"]["program_source_digest"] = "0" * 64
    report["expected"]["program_sha256"] = "1" * 64
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("stale" in problem for problem in result["problems"])


def test_gate_refuses_a_tampered_bound_package(tmp_path, monkeypatch):
    prep, run_dir, modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    package = prep / "kit" / modes["id"]["delivery"]
    package.write_bytes(package.read_bytes() + b"tamper")
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("package bytes" in problem for problem in result["problems"])


def test_gate_refuses_a_corrupted_evidence_file(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    export = run_dir / "evidence" / "WN02-id" / "export.jsonl"
    export.write_text("{not json")
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("WN02 id" in problem for problem in result["problems"])


def _refresh_export_hash(report, relative, path):
    """Keep the superficial summary/hash consistent with a mutated export file.

    The strict gate must still refuse because the real bytes disagree across
    sources; updating the summary and the recorded hash only removes the easy
    tamper signal.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    for case in report["cases"]:
        for entry in (case["evidence"].get("modes") or {}).values():
            if entry.get("export_path") == relative:
                entry["export_sha256"] = digest
        old = case["evidence"].get("old_release")
        if old and old.get("export_path") == relative:
            old["export_sha256"] = digest
    return digest


def _export_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _case_entry(run_dir, case_id, mode=None):
    report = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    case = next(item for item in report["cases"] if item["id"] == case_id)
    if mode is None:
        return case["evidence"]
    return case["evidence"]["modes"][mode]


def _gate_problems(run_dir, prep):
    return native.validate_run(run_dir, prep)["problems"]


def _mutate_store_bytes(run_dir, report, label, mutate):
    """Change one stored session document in the derived snapshot *and* in the
    preserved raw copy, refreshing both digests: the record then looks perfectly
    consistent with itself and only the bound instance can refute it."""
    entry = None
    for case in report["cases"]:
        for value in (case["evidence"].get("modes") or {}).values():
            if (value.get("store_copy") or {}).get("snapshot", {}).get("path", "").startswith(f"evidence/{label}/"):
                entry = value["store_copy"]
        for key in ("short_code", "old_release", "offline", "reconnect"):
            value = case["evidence"].get(key)
            if (value or {}).get("store_copy", {}).get("snapshot", {}).get("path", "").startswith(f"evidence/{label}/"):
                entry = value["store_copy"]
    assert entry is not None, label
    for relative in (entry["queue.sqlite"], entry["originals"]["queue.sqlite"]["path"]):
        path = run_dir / relative
        connection = sqlite3.connect(path)
        row_id, raw = connection.execute("SELECT id,value FROM sessions").fetchone()
        document = json.loads(raw)
        mutate(document)
        connection.execute("UPDATE sessions SET value=? WHERE id=?", [json.dumps(document), row_id])
        connection.commit()
        connection.close()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if relative == entry["queue.sqlite"]:
            entry["snapshot"]["sha256"] = digest
        else:
            entry["originals"]["queue.sqlite"]["sha256"] = digest
    return entry


def test_gate_reconciles_the_native_store_copy_against_the_bound_instance(tmp_path, monkeypatch):
    """The summary and the real SQLite bytes change together; the gate still refuses."""
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())

    def bump(document):
        document["records"][1]["payload"]["rt_ms"] = 999.0

    _mutate_store_bytes(run_dir, report, "WN02-id", bump)
    report["cases"][1]["evidence"]["modes"]["id"]["boundary_raw_values"] = [321.5, 999.0]
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("native store copy" in problem and "differs" in problem for problem in result["problems"]), result["problems"]


def test_gate_refuses_changed_raw_store_bytes_even_with_a_fresh_digest(tmp_path, monkeypatch):
    """The preserved raw copy is evidence: changing it (or its digest) is refused."""
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    entry = report["cases"][1]["evidence"]["modes"]["anonymous"]["store_copy"]
    original = run_dir / entry["originals"]["queue.sqlite"]["path"]
    connection = sqlite3.connect(original)
    connection.execute("UPDATE sessions SET value=?", ['{"kind":"session","records":[]}'])
    connection.commit()
    connection.close()
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("preserved native store copy" in problem for problem in result["problems"]), result["problems"]


def test_gate_refuses_a_derived_snapshot_that_dropped_the_wal_rows(tmp_path, monkeypatch):
    """A snapshot that no longer reproduces the preserved raw bytes (the classic
    'WAL deleted after a failed checkpoint') is refused."""
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    entry = report["cases"][1]["evidence"]["modes"]["anonymous"]["store_copy"]
    snapshot = run_dir / entry["queue.sqlite"]
    connection = sqlite3.connect(snapshot)
    connection.execute("DELETE FROM sessions")
    connection.commit()
    connection.close()
    entry["snapshot"]["sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("derived store snapshot does not reproduce the preserved store bytes" in problem
               for problem in result["problems"]), result["problems"]


def test_gate_refuses_a_missing_export_row_even_with_a_fresh_hash(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    relative = "evidence/WN02-id/export.jsonl"
    path = run_dir / relative
    rows = _export_rows(path)
    rows.pop()
    path.write_text("\n".join(json.dumps(row) for row in rows))
    _refresh_export_hash(report, relative, path)
    entry = report["cases"][1]["evidence"]["modes"]["id"]
    entry["export_ids"] = [row["record"]["event_id"] for row in rows]
    entry["export_values"] = sorted(float(row["record"]["payload"]["rt_ms"]) for row in rows)
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("disagree on the session event set" in problem for problem in result["problems"])


def test_gate_refuses_an_extra_export_row_even_with_a_fresh_hash(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    relative = "evidence/WN02-id/export.jsonl"
    path = run_dir / relative
    rows = _export_rows(path)
    extra = json.loads(json.dumps(rows[0]))
    extra["record"]["event_id"] = "70000000-0000-0000-0000-000000000099"
    rows.append(extra)
    path.write_text("\n".join(json.dumps(row) for row in rows))
    _refresh_export_hash(report, relative, path)
    entry = report["cases"][1]["evidence"]["modes"]["id"]
    entry["export_ids"] = [row["record"]["event_id"] for row in rows]
    entry["export_values"] = sorted(float(row["record"]["payload"]["rt_ms"]) for row in rows)
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("disagree on the session event set" in problem for problem in result["problems"])


def test_gate_refuses_duplicate_export_event_ids(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    relative = "evidence/WN02-id/export.jsonl"
    path = run_dir / relative
    rows = _export_rows(path)
    rows.append(json.loads(json.dumps(rows[0])))
    path.write_text("\n".join(json.dumps(row) for row in rows))
    _refresh_export_hash(report, relative, path)
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("duplicate event ids" in problem for problem in result["problems"])


def test_gate_refuses_a_wrong_release_binding_in_the_export(tmp_path, monkeypatch):
    prep, run_dir, modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    relative = "evidence/WN02-id/export.jsonl"
    path = run_dir / relative
    rows = _export_rows(path)
    for row in rows:
        row["release_id"] = modes["password"]["release_id"]
    path.write_text("\n".join(json.dumps(row) for row in rows))
    _refresh_export_hash(report, relative, path)
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("bound session/release" in problem for problem in result["problems"])


def test_gate_refuses_a_wrong_session_binding_in_the_export(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    relative = "evidence/WN02-id/export.jsonl"
    path = run_dir / relative
    rows = _export_rows(path)
    for row in rows:
        row["record"]["session_id"] = "50000000-0000-0000-0000-0000000000ff"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    _refresh_export_hash(report, relative, path)
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("bound session/release" in problem for problem in result["problems"])


def test_gate_refuses_a_nested_unit_change_in_the_export(tmp_path, monkeypatch):
    """A nested payload/unit/source change is caught even when ids and counts match."""
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    relative = "evidence/WN02-id/export.jsonl"
    path = run_dir / relative
    rows = _export_rows(path)
    rows[0]["record"]["observed_time"]["unit"] = "us"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    _refresh_export_hash(report, relative, path)
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("differs between the authorized export" in problem for problem in result["problems"])


def test_gate_refuses_a_malformed_native_store_precisely(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    (run_dir / "evidence" / "WN02-id" / "queue.sqlite").write_bytes(b"not a sqlite database")
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("native store copy is unreadable" in problem for problem in result["problems"])


def test_gate_refuses_a_malformed_export_line_precisely(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    relative = "evidence/WN02-id/export.jsonl"
    path = run_dir / relative
    path.write_text("{not json\n")
    _refresh_export_hash(report, relative, path)
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("malformed JSON" in problem for problem in result["problems"])


def test_gate_reconciles_the_old_release_store_copy(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())

    def flip(document):
        document["records"][0]["payload"]["choice"] = "right"

    _mutate_store_bytes(run_dir, report, "WN06-old-release", flip)
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("WN06 old release" in problem and "differs" in problem for problem in result["problems"]), result["problems"]


def test_gate_refuses_a_session_absent_from_the_bound_instance(tmp_path, monkeypatch):
    """A summary-only run cannot claim a session the isolated database never saw."""
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    connection = sqlite3.connect(prep / "instance-root" / "instance" / "gep.sqlite3")
    connection.execute("DELETE FROM core_session WHERE id=?",
                       ["00000000-0000-0000-0000-000000000000".replace("-", "")])
    connection.commit()
    connection.close()
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("session is missing from the bound isolated instance" in problem
               for problem in result["problems"])


def test_gate_accepts_a_whole_number_written_as_float_in_the_server_envelope(tmp_path, monkeypatch):
    """The native store writes an integer-valued field as ``1`` and the server
    envelope can hold ``1.0`` (the client re-reads its own store). The gate
    compares the value, not the token, while a real value change still refuses."""
    prep, run_dir, modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    db = prep / "instance-root" / "instance" / "gep.sqlite3"
    session = modes["anonymous"]["release_id"].replace("-", "")
    connection = sqlite3.connect(db)
    rows = [(envelope, json.loads(envelope)) for (envelope,) in connection.execute(
        "SELECT envelope FROM core_event WHERE session_id=?", [session])]
    assert rows
    raw, envelope = rows[0]
    envelope["sequence"] = float(envelope["sequence"])
    envelope["payload"]["rt_ms"] = float(envelope["payload"]["rt_ms"])
    connection.execute("UPDATE core_event SET envelope=? WHERE session_id=? AND envelope=?",
                       [json.dumps(envelope, ensure_ascii=False), session, raw])
    connection.commit()
    connection.close()
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_PASS, result["problems"]
    # A genuinely different number in the same field is still refused.
    connection = sqlite3.connect(db)
    envelope["payload"]["rt_ms"] = 321.499999
    connection.execute("UPDATE core_event SET envelope=? WHERE session_id=? AND envelope=?",
                       [json.dumps(envelope, ensure_ascii=False), session,
                        json.dumps({**envelope, "payload": {**envelope["payload"], "rt_ms": 321.5}},
                                   ensure_ascii=False)])
    connection.commit()
    connection.close()
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("differs between the native store copy and the bound instance" in problem
               for problem in result["problems"]), result["problems"]


def test_gate_uses_the_verified_process_title_when_the_launch_record_lacks_one(tmp_path, monkeypatch):
    """The launch record carries the title the interactive launcher observed; when
    a record predates that field the verified process observation is the same real
    reading. No title anywhere is still refused."""
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    launch = report["cases"][0]["evidence"]["launch"]
    launch["window_title"] = ""
    report["cases"][0]["evidence"]["processes"] = [{"pid": launch["pid"],
                                                    "window_title": "GEP Synthetic Experiment"}]
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_PASS, result["problems"]
    report["cases"][0]["evidence"]["processes"] = [{"pid": launch["pid"], "window_title": ""}]
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("real window title" in problem for problem in result["problems"])


def test_gate_reads_the_data_only_marker_from_the_real_program_output(tmp_path, monkeypatch):
    """The marker is checked in the captured program stdout, not in a truncated
    tail: a run whose real output lacks it is refused."""
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    data_only = report["cases"][3]["evidence"]["data_only"]
    output = run_dir / data_only["stdout_path"]
    assert "SYNTHETIC_DATA_ONLY" in output.read_text(encoding="utf-8")
    output.write_text("Godot Engine v4.7.2\nClosed database\n", encoding="utf-8")
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("data-only recovery evidence is incomplete" in problem for problem in result["problems"])
    # A missing output file is a refusal too, never a silent pass.
    output.unlink()
    result = native.validate_run(run_dir, prep)
    assert any("data-only recovery evidence is incomplete" in problem for problem in result["problems"])
    data_only.pop("stdout_path")
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert any("data-only recovery evidence is incomplete" in problem for problem in result["problems"])


def test_gate_refuses_mismatched_raw_values(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    report["cases"][1]["evidence"]["modes"]["anonymous"]["export_values"] = [999.0, 321.5]
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("raw values" in problem for problem in result["problems"])


def test_gate_refuses_a_skipped_case(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    report["cases"] = [case for case in report["cases"] if case["id"] != "WN04"]
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("WN04" in problem for problem in result["problems"])


def test_gate_refuses_a_failed_case_as_incomplete(tmp_path, monkeypatch):
    prep, run_dir, _modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    report = json.loads((run_dir / "run.json").read_text())
    report["cases"][2]["checks"] = [{"ok": False, "label": "injected", "detail": None}]
    (run_dir / "run.json").write_text(json.dumps(report))
    result = native.validate_run(run_dir, prep)
    assert result["verdict"] == native.GATE_VERDICT_INVALID
    assert any("WN03" in problem for problem in result["problems"])


def test_gate_cli_exits_nonzero_without_a_selected_run(tmp_path):
    result = __import__("subprocess").run(
        [sys.executable, str(TOOLS / "phase03_verify_windows_native.py"), "--gate",
         "--gate-run", str(tmp_path / "missing")], capture_output=True, text=True)
    assert result.returncode != 0
    assert "PHASE03_WINDOWS_NATIVE_GATE" in result.stdout


def test_serve_runtime_refuses_a_foreign_run_directory(tmp_path):
    result = __import__("subprocess").run(
        [sys.executable, str(TOOLS / "phase03_verify_windows_native.py"), "--serve-runtime",
         "--run", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "PHASE03_WINDOWS_RUNTIME_FAILED" in result.stdout


def _fake_ssh(tmp_path, name, script):
    path = tmp_path / name
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_tunnel_command_is_the_scoped_reverse_forward():
    """The frozen contract: reverse 127.0.0.1:listen -> preparation-host port."""
    tunnel = native.Tunnel({"listen_port": 8174, "target_port": 8074, "windows_alias": "example-win"},
                           log=lambda message: None)
    command = tunnel.command()
    assert "-R" in command and "8174:127.0.0.1:8074" in command
    assert "ExitOnForwardFailure=yes" in command
    assert "StrictHostKeyChecking=yes" in command
    assert "BatchMode=yes" in command and "PreferredAuthentications=publickey" in command
    assert command[-1] == "example-win"
    assert "example-win" in command and "-N" in command
    assert "ProxyCommand" not in " ".join(command) and "UserKnownHostsFile" not in " ".join(command)


def test_tunnel_probe_is_a_strictly_read_only_remote_check():
    """Readiness evidence is a remote read-only probe, not just a live ssh."""
    tunnel = native.Tunnel({"listen_port": 8174, "target_port": 8074, "windows_alias": "example-win"},
                           log=lambda message: None)
    command = tunnel.probe_command()
    assert "-N" not in command and "-R" not in command
    assert "BatchMode=yes" in command and "StrictHostKeyChecking=yes" in command
    assert "PreferredAuthentications=publickey" in command
    assert command[-2] == "-EncodedCommand" and command[-1]
    assert command[-3] == "-NonInteractive"
    assert tunnel.alias == command[command.index("example-win")]
    assert "ProxyCommand" not in " ".join(command)


def test_login_body_normalization_removes_only_the_csrf_token():
    body = b'<input type="hidden" name="csrfmiddlewaretoken" value="AbC123">'
    normalized = native.normalize_login_body(body)
    assert b"AbC123" not in normalized and b"<token>" in normalized
    other = b'<input type="hidden" name="csrfmiddlewaretoken" value="ZzZ999">'
    assert native.normalize_login_body(other) == normalized


def test_local_login_fingerprint_reads_a_real_local_page():
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'<form><input name="username">' + b'<input name="csrfmiddlewaretoken" value="' \
                + str(id(self)).encode() + b'"></form>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        fingerprint = native.local_login_fingerprint(server.server_address[1])
        assert fingerprint["status"] == 200
        assert b'name="username"' in fingerprint["body"]
        assert b"<token>" in fingerprint["body"]
    finally:
        server.shutdown()
        server.server_close()


def test_tunnel_refuses_without_the_authorized_alias():
    tunnel = native.Tunnel({"listen_port": 8174, "target_port": 8074}, log=lambda message: None)
    with pytest.raises(native.PreparationError, match="Windows 主机别名"):
        tunnel.command()
    tunnel = native.Tunnel({"listen_port": 0, "target_port": 8074, "windows_alias": "example-win"},
                           log=lambda message: None)
    with pytest.raises(native.PreparationError, match="端点无效"):
        tunnel.command()


def test_tunnel_ready_requires_a_live_ssh_a_reachable_target_and_a_working_forward(tmp_path):
    import threading
    import http.server

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = server.server_address[1]
    fake = _fake_ssh(tmp_path, "ssh_ok.sh", "#!/bin/sh\nsleep 30\n")
    tunnel = native.Tunnel({"listen_port": 8174, "target_port": target, "windows_alias": "example-win"},
                           ssh_binary=str(fake), log=lambda message: None)
    probes = []

    def probe():
        probes.append(True)
        return True, "injected read-only remote probe ok"

    try:
        tunnel.start()
        assert tunnel.wait_ready(timeout=10, grace=0.2, probe=probe) is True
        assert probes, "readiness must include the remote probe, not only a live ssh"
        assert "injected read-only remote probe ok" in tunnel.detail
        assert tunnel.stop() is True
        assert tunnel.process is None
    finally:
        server.shutdown()
        server.server_close()


def test_tunnel_never_reports_ready_when_the_remote_probe_fails(tmp_path):
    """A live ssh plus a reachable local port is not a working Windows forward."""
    import threading
    import http.server

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = server.server_address[1]
    fake = _fake_ssh(tmp_path, "ssh_ok.sh", "#!/bin/sh\nsleep 30\n")
    tunnel = native.Tunnel({"listen_port": 8174, "target_port": target, "windows_alias": "example-win"},
                           ssh_binary=str(fake), log=lambda message: None)
    try:
        tunnel.start()
        assert tunnel.wait_ready(timeout=3, grace=0.2, probe=lambda: (False, "windows 侧端点不可达")) is False
        assert tunnel.ready is False
        assert "windows 侧端点不可达" in tunnel.detail
    finally:
        tunnel.stop()
        server.shutdown()
        server.server_close()


def test_tunnel_probe_exception_is_never_ready(tmp_path):
    import threading
    import http.server

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = server.server_address[1]
    fake = _fake_ssh(tmp_path, "ssh_ok.sh", "#!/bin/sh\nsleep 30\n")

    def broken_probe():
        raise RuntimeError("probe blew up")

    tunnel = native.Tunnel({"listen_port": 8174, "target_port": target, "windows_alias": "example-win"},
                           ssh_binary=str(fake), log=lambda message: None)
    try:
        tunnel.start()
        assert tunnel.wait_ready(timeout=3, grace=0.2, probe=broken_probe) is False
        assert "探针异常" in tunnel.detail
    finally:
        tunnel.stop()
        server.shutdown()
        server.server_close()


def test_tunnel_failure_is_reported_and_never_ready(tmp_path):
    fake = _fake_ssh(tmp_path, "ssh_fail.sh", "#!/bin/sh\necho 'bind: Address already in use' >&2\nexit 255\n")
    tunnel = native.Tunnel({"listen_port": 8174, "target_port": 8074, "windows_alias": "example-win"},
                           ssh_binary=str(fake), log=lambda message: None)
    tunnel.start()
    assert tunnel.wait_ready(timeout=10, grace=0.2) is False
    assert "退出" in tunnel.detail
    assert tunnel.ready is False


def test_serve_runtime_stops_its_own_instance_when_the_tunnel_fails(tmp_path, monkeypatch):
    """A tunnel failure must not leave a ready-looking instance behind."""
    prep = tmp_path / "prep"
    data = prep / "instance-root" / "instance"
    data.mkdir(parents=True)
    (data / "gep.sqlite3").write_bytes(b"")
    (data / "instance").write_text("11111111-1111-1111-1111-111111111111")
    (data / "secret").write_text("synthetic-secret")
    private = prep / "private"
    private.mkdir()
    (private / "runtime.json").write_text(json.dumps({
        "format": native.RUNTIME_FORMAT, "instance_id": "11111111-1111-1111-1111-111111111111",
        "port": 8074, "tunnel": {"listen_port": 8174, "target_port": 8074, "windows_alias": "example-win"}}))
    fake_ssh = _fake_ssh(tmp_path, "ssh_fail.sh", "#!/bin/sh\nexit 255\n")
    monkeypatch.setenv("GEP_SSH_BIN", str(fake_ssh))
    events = []

    class FakeInstance:
        def __init__(self, root):
            self.root = root
            self.port = None
            self.instance_id = None
            self.secret_key = None
            self.server = types.SimpleNamespace(pid=os.getpid(),
                                                args=["/bin/sh", "-c", "sleep 30"])

        def start(self):
            events.append("instance-start")
            return self

        def stop(self):
            events.append("instance-stop")

    monkeypatch.setattr(native, "Instance", FakeInstance)
    with pytest.raises(native.PreparationError, match="隧道未就绪"):
        native.serve_runtime(prep, prep, foreground=False)
    assert events == ["instance-start", "instance-stop"], events
    assert not (prep / "runtime.pid").exists()


def test_stop_recorded_runtime_refuses_a_changed_command_line(tmp_path):
    """A reused PID must never be killed by the stop command."""
    record = tmp_path / "tunnel.pid"
    record.write_text(json.dumps({"pid": os.getpid(), "command": "ssh -N -R 8174:127.0.0.1:8074 example-win"}))
    stopped = native._stop_recorded_runtime(tmp_path, tmp_path)
    assert stopped and stopped[0]["outcome"] == "refused"
    assert record.is_file(), "a refused record must stay for inspection"


def test_command_tokens_match_accepts_an_exec_replaced_wrapper(tmp_path):
    """A task-local GEP_SSH_BIN wrapper execs ssh, so argv[0] is replaced while
    the argument vector is still the recorded one; the transformation is recorded
    explicitly and a changed command line stays refused."""
    wrapper = tmp_path / "ssh-quant-win.sh"
    argv = [str(wrapper), "-N", "-o", "ExitOnForwardFailure=yes",
            "-R", "8160:127.0.0.1:8060", "example-win"]
    assert native._expected_live_argv(argv) == (None, None), "a missing wrapper file is never guessed at"
    wrapper.write_text("#!/bin/sh\nexec /usr/bin/ssh \\\n  -o HostName=10.0.0.1 \\\n"
                       "  -o HostKeyAlias=example-win \\\n  \"$@\"\n", encoding="utf-8")
    transformation = {"kind": "exec", "script": str(wrapper), "program": "/usr/bin/ssh",
                      "fixed_arguments": ["-o", "HostName=10.0.0.1", "-o", "HostKeyAlias=example-win"]}
    assert native._expected_live_argv(argv) == (
        transformation, ["/usr/bin/ssh", "-o", "HostName=10.0.0.1", "-o", "HostKeyAlias=example-win", *argv[1:]])
    resolved = transformation and native._expected_live_argv(argv)[1]
    live = ("/usr/bin/ssh -o HostName=10.0.0.1 -o HostKeyAlias=example-win -N -o ExitOnForwardFailure=yes "
            "-R 8160:127.0.0.1:8060 example-win")
    assert native._command_tokens_match(resolved, live) is True
    for changed in ("/usr/bin/ssh -o HostName=10.0.0.1 -o HostKeyAlias=example-win -N -o ExitOnForwardFailure=yes "
                    "-R 9999:127.0.0.1:8060 example-win",
                    "/usr/bin/ssh -o HostName=10.0.0.1 -o HostKeyAlias=example-win -N -o ExitOnForwardFailure=yes "
                    "-R 8160:127.0.0.1:8060 example-win --extra",
                    "/usr/bin/scp -o HostName=10.0.0.1 -o HostKeyAlias=example-win -N -o ExitOnForwardFailure=yes "
                    "-R 8160:127.0.0.1:8060 example-win",
                    "sh /task/ssh-quant-win.sh -N -o ExitOnForwardFailure=yes -R 8160:127.0.0.1:8060 example-win",
                    ""):
        assert native._command_tokens_match(resolved, changed) is False, changed
    # The recorded argv without the wrapper transformation must not match the
    # exec-replaced command line either: no generic "ignore the first token" rule.
    assert native._command_tokens_match(argv, live) is False
    # A script whose last command is not the single explicit exec shape is never
    # exec-transformed.
    wrapper.write_text("#!/bin/sh\n/usr/bin/ssh -N \"$@\"\n", encoding="utf-8")
    transformation, resolved = native._expected_live_argv(argv)
    assert resolved == ["/bin/sh", str(wrapper), *argv[1:]], "a plain shebang script keeps its interpreter"
    # A real interpreter binary keeps the recorded argv as it is (no transform).
    assert native._expected_live_argv([sys.executable, "-c", "print(1)"]) == (None, None)


def test_command_matching_records_the_shebang_interpreter_of_a_console_script(tmp_path):
    """The virtualenv console script runs through its shebang interpreter, so the
    live command line starts with that interpreter; the recorded argv alone is
    refused while the recorded transformation matches exactly."""
    script = tmp_path / "gunicorn"
    script.write_text(f"#!{sys.executable}\nprint('server')\n", encoding="utf-8")
    argv = [str(script), "gep.wsgi:application", "--bind", "127.0.0.1:8060"]
    transformation, resolved = native._expected_live_argv(argv)
    assert transformation["kind"] == "shebang" and transformation["program"] == sys.executable
    assert resolved == [sys.executable, str(script), *argv[1:]]
    live = f"{sys.executable} {script} gep.wsgi:application --bind 127.0.0.1:8060"
    assert native._command_tokens_match(resolved, live) is True
    assert native._command_tokens_match(argv, live) is False
    assert native._command_tokens_match(resolved, live.replace("8060", "8061")) is False
    # A shebang with extra arguments is not transformed (fail closed).
    script.write_text(f"#!{sys.executable} -u\nprint('server')\n", encoding="utf-8")
    assert native._expected_live_argv(argv) == (None, None)


def test_recorded_pid_records_the_resolved_wrapper_argv_and_stop_requires_it(tmp_path):
    """The stop command compares the argv the OS really shows after the wrapper
    exec, and a different program with the same arguments is refused."""
    wrapper = tmp_path / "ssh-quant-win.sh"
    wrapper.write_text("#!/bin/sh\nexec /usr/bin/ssh -o HostKeyAlias=example-win \"$@\"\n", encoding="utf-8")
    recorded = ["-N", "-R", "8160:127.0.0.1:8060", "example-win"]
    process = types.SimpleNamespace(pid=os.getpid(), args=[str(wrapper), *recorded])
    record = tmp_path / "tunnel.pid"
    native._record_pid(record, process)
    document = json.loads(record.read_text(encoding="utf-8"))
    assert document["argv"] == [str(wrapper), *recorded]
    assert document["wrapper_transform"]["program"] == "/usr/bin/ssh"
    assert document["resolved_argv"] == ["/usr/bin/ssh", "-o", "HostKeyAlias=example-win", *recorded]
    assert native._recorded_argv(document) == document["resolved_argv"]


def test_stop_recorded_runtime_refuses_a_legacy_record_of_a_different_program(tmp_path):
    """A record written before the resolved argv existed is stopped only when the
    live command line is exactly the recorded argv through its explicit script
    shape; the same arguments under a different program are refused."""
    script = tmp_path / "runtime"
    script.write_text(f"#!{sys.executable}\nprint('x')\n", encoding="utf-8")
    record = tmp_path / "runtime.pid"
    record.write_text(json.dumps({"pid": os.getpid(), "argv": [str(script), "--serve"],
                                  "started": native._process_start_marker(os.getpid())}),
                      encoding="utf-8")
    # The live process is this test runner, not the recorded script.
    stopped = native._stop_recorded_runtime(tmp_path, tmp_path)
    assert stopped and stopped[0]["outcome"] == "refused", stopped
    assert "strict argv comparison" in stopped[0]["reason"]
    assert record.is_file(), "a refused record stays for inspection"


def test_recorded_runtime_local_start_record_stop_releases_the_port(tmp_path):
    """A real local start is recorded and stopped from a NEW process; the port is released."""
    import socket as socket_module

    run_dir = tmp_path / "run"
    prep_dir = tmp_path / "prep"
    run_dir.mkdir()
    prep_dir.mkdir()
    port = _free_port()
    process = subprocess.Popen([
        sys.executable, "-c",
        "import http.server,threading;"
        f"s=http.server.ThreadingHTTPServer(('127.0.0.1',{port}),http.server.BaseHTTPRequestHandler);"
        "s.serve_forever()"])
    try:
        record_path = prep_dir / "runtime.pid"
        native._record_pid(record_path, process)
        document = json.loads(record_path.read_text(encoding="utf-8"))
        assert document["pid"] == process.pid
        assert document["argv"] and document["argv"][0] == sys.executable
        assert document["started"], "a real start marker is recorded"
        result = subprocess.run([sys.executable, str(TOOLS / "phase03_verify_windows_native.py"),
                                 "--stop-runtime", "--run", str(run_dir), "--prep", str(prep_dir)],
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout.strip().splitlines()[-1].split(" ", 1)[1])
        assert payload and payload[0]["outcome"] == "stopped"
        assert not record_path.exists()
        deadline = time.time() + 10
        released = False
        while time.time() < deadline:
            try:
                with socket_module.create_connection(("127.0.0.1", port), timeout=0.5):
                    time.sleep(0.2)
            except OSError:
                released = True
                break
        assert released, "the recorded process must be gone and its port released"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_stop_recorded_runtime_refuses_a_malformed_record(tmp_path):
    record = tmp_path / "tunnel.pid"
    record.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    stopped = native._stop_recorded_runtime(tmp_path, tmp_path)
    assert stopped and stopped[0]["outcome"] == "refused"
    assert "malformed" in stopped[0]["reason"]
    assert record.is_file()
    record.write_text("not json", encoding="utf-8")
    stopped = native._stop_recorded_runtime(tmp_path, tmp_path)
    assert stopped and stopped[0]["outcome"] == "refused"
    assert record.is_file()
    assert os.getpid() > 0


def test_stop_recorded_runtime_refuses_a_reused_pid_with_a_changed_start_time(tmp_path):
    """A matching command line with a different start marker is a reused PID."""
    live = subprocess.run(["ps", "-o", "command=", "-p", str(os.getpid())],
                          capture_output=True, text=True).stdout.strip()
    import shlex
    record = tmp_path / "tunnel.pid"
    record.write_text(json.dumps({"pid": os.getpid(), "argv": shlex.split(live),
                                  "started": "Mon Jan  1 00:00:00 2001"}), encoding="utf-8")
    stopped = native._stop_recorded_runtime(tmp_path, tmp_path)
    assert stopped and stopped[0]["outcome"] == "refused"
    assert "reused" in stopped[0]["reason"]
    assert record.is_file()
    assert os.getpid() > 0, "this test process is still alive (no foreign kill happened)"


def test_record_pid_refuses_to_write_a_malformed_record(tmp_path):
    process = types.SimpleNamespace(pid=os.getpid())
    with pytest.raises(native.PreparationError, match="畸形 PID 记录"):
        native._record_pid(tmp_path / "runtime.pid", process)
    assert not (tmp_path / "runtime.pid").exists()

# ------------------------------------------------- native store + fault proxy
def test_native_store_uses_the_explicit_sqlite_paths(tmp_path):
    """The real store files are queue.sqlite/writer.sqlite, not *.sqlite3/*.db."""
    import phase03_verify_windows_native  # noqa: F401  (tool import order)
    harness_path = TOOLS / "windows_native_harness.py"
    namespace = {"__name__": "harness_under_test", "__file__": str(harness_path)}
    exec(compile(harness_path.read_text(encoding="utf-8"), str(harness_path), "exec"), namespace)
    storage = tmp_path / "store"
    storage.mkdir()
    for name in ("queue.sqlite", "writer.sqlite"):
        connection = sqlite3.connect(storage / name)
        connection.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.commit()
        connection.close()
    connection = sqlite3.connect(storage / "queue.sqlite")
    connection.execute("INSERT INTO sessions VALUES(?,?)",
                       ["session-1", json.dumps({"id": "session-1", "kind": "session",
                                                 "records": [_event("e1", 321.5)], "pending": ["e1"]})])
    connection.commit()
    connection.close()
    decoy = storage / "unrelated.sqlite3"
    sqlite3.connect(decoy).close()
    store = namespace["read_store"](storage)
    assert store["exists"] and store["writer_exists"]
    assert [document["id"] for document in store["sessions"]] == ["session-1"]
    assert namespace["store_event_ids"](store["sessions"][0]) == ["e1"]
    assert namespace["raw_values"](store["sessions"][0]) == [321.5]


def test_fault_proxy_injects_offline_lost_ack_and_pass(tmp_path):
    """The scoped loopback proxy really drops, loses one ACK, and relays."""
    import http.server
    import threading
    import socket as socket_module
    import urllib.request

    harness_path = TOOLS / "windows_native_harness.py"
    namespace = {"__name__": "harness_under_test", "__file__": str(harness_path)}
    exec(compile(harness_path.read_text(encoding="utf-8"), str(harness_path), "exec"), namespace)
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            seen.append(self.path)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    listen_port = _free_port()
    proxy = namespace["FaultProxy"](listen_port, upstream.server_address[1], mode="pass",
                                    log_path=tmp_path / "proxy.json")
    proxy.start()
    try:
        def post(path):
            request = urllib.request.Request(f"http://127.0.0.1:{listen_port}{path}", data=b"{}",
                                             headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
            except OSError:
                return 0

        assert post("/v1/participant/sessions/x/event-batches") == 200
        proxy.stop()
        proxy = namespace["FaultProxy"](listen_port, upstream.server_address[1], mode="drop-events")
        proxy.start()
        assert post("/v1/participant/sessions/x/event-batches") == 0
        assert post("/v1/participant/sessions/x/status") == 200
        proxy.stop()
        proxy = namespace["FaultProxy"](listen_port, upstream.server_address[1], mode="drop-completion")
        proxy.start()
        assert post("/v1/participant/sessions/x/completion") == 0
        assert post("/v1/participant/sessions/x/event-batches") == 200
        proxy.stop()
        proxy = namespace["FaultProxy"](listen_port, upstream.server_address[1], mode="lose-first-ack")
        proxy.start()
        assert post("/v1/participant/sessions/x/event-batches") == 0
        assert post("/v1/participant/sessions/x/event-batches") == 200
        proxy.stop()
        proxy = namespace["FaultProxy"](listen_port, upstream.server_address[1], mode="offline")
        proxy.start()
        assert post("/v1/participant/sessions/x/status") == 0
    finally:
        proxy.stop()
        upstream.shutdown()
        upstream.server_close()


def _free_port():
    import socket as socket_module
    with socket_module.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_harness_redacts_credentials_from_recorded_arguments():
    harness_path = TOOLS / "windows_native_harness.py"
    namespace = {"__name__": "harness_under_test", "__file__": str(harness_path)}
    exec(compile(harness_path.read_text(encoding="utf-8"), str(harness_path), "exec"), namespace)
    redacted = namespace["redact"](["--synthetic-auto", "--participant-code=001",
                                    "--password=secret-value", "--short-code=123456",
                                    "--permit=token-value", "--export-recovery=C:/run/out.json"])
    assert "--participant-code=001" in redacted
    assert "--export-recovery=C:/run/out.json" in redacted
    assert all("secret-value" not in value and "123456" not in value and "token-value" not in value
               for value in redacted)


# ------------------------------------------------ launch lifecycle (owned PID)
LAUNCH_RECORD = {
    "pid": 4242,
    "path": "C:/run/GEP Synthetic Experiment/GEP Synthetic Experiment.exe",
    "session_id": 1,
    "start_time": "2026-09-20T23:00:00.1234567+08:00",
    "window_title": "GEP Synthetic Experiment",
    "session_name": "Console",
}


def launch_record(**overrides):
    record = dict(LAUNCH_RECORD)
    record.update(overrides)
    return record


class FakeLauncher:
    """A launcher process that keeps running until the test ends it."""

    def __init__(self):
        self.returncode = None
        self.waits = []
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("launcher", timeout)
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = -15

    def kill(self):
        self.killed += 1
        self.returncode = -9


def command_result(stdout="", returncode=0):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_interactive_launch_waits_only_for_readiness_never_the_program(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONNAME", "Console")
    harness = native_harness.Harness(run_root=tmp_path / "run dir")
    launcher = FakeLauncher()
    spawned = []
    monkeypatch.setattr(harness, "spawn_launcher",
                        lambda script, out=None, err=None: spawned.append(Path(script)) or launcher)
    monkeypatch.setattr(harness, "wait_for", lambda path, timeout: dict(LAUNCH_RECORD))
    launch = harness.start_program(LAUNCH_RECORD["path"], "C:/run", tmp_path / "store",
                                   ["--synthetic-auto"], "WN01", 60)
    assert launch.record["pid"] == 4242 and launch.record["interactive"] is True
    assert launch.launch_exit is None and launcher.returncode is None
    assert launcher.waits == [], "start_program must not wait for the running program"
    assert spawned == [launch.script_path] and launch.script_path.is_file()
    launcher.returncode = 0
    harness.close_launch(launch)
    assert launch.record["launch_exit"] == 0 and launch.record["launcher_closed"] is True
    assert launcher.waits == [30]
    assert not launch.script_path.exists() and launch.closed
    assert harness.launches == []


def test_interactive_failure_releases_only_the_owned_launcher(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONNAME", "Console")
    harness = native_harness.Harness(run_root=tmp_path / "run")
    launcher = FakeLauncher()
    monkeypatch.setattr(harness, "spawn_launcher", lambda script, out=None, err=None: launcher)
    monkeypatch.setattr(harness, "wait_for", lambda path, timeout: None)
    commands = []
    monkeypatch.setattr(native_harness, "run_powershell_command",
                        lambda command, timeout=120: commands.append(command) or command_result())
    with pytest.raises(native_harness.HarnessError, match="launch record missing"):
        harness.start_program(LAUNCH_RECORD["path"], "C:/run", tmp_path / "store", [], "WN01", 1)
    assert launcher.terminated == 1 and launcher.killed == 0
    assert commands == [], "no PID-based stop may run without a verified identity record"
    assert not list((tmp_path / "run").glob("launch_*.ps1"))


def test_scheduled_task_and_script_survive_until_the_launch_is_closed(tmp_path, monkeypatch):
    """The scheduled-task branch must not delete its own task while it may still run."""
    monkeypatch.setenv("SESSIONNAME", "Services")
    harness = native_harness.Harness(run_root=tmp_path / "run")
    commands = []
    monkeypatch.setattr(harness, "run_os",
                        lambda command, timeout=60: commands.append(list(command)) or command_result())
    monkeypatch.setattr(native_harness, "active_console_session", lambda: 1)
    monkeypatch.setattr(harness, "wait_for", lambda path, timeout: dict(launch_record(session_name="Services")))
    launch = harness.start_program(LAUNCH_RECORD["path"], "C:/run", tmp_path / "store",
                                   ["--synthetic-auto"], "WN02", 60)
    task = launch.task_name
    assert task and task.startswith("GEP-Native-WN-")
    assert launch.launcher is None and launch.record["interactive"] is False
    assert launch.script_path.is_file()
    assert launch.wrapper_path is not None and launch.wrapper_path.is_file()
    created = [command for command in commands if command[:2] == ["schtasks", "/create"]]
    assert len(created) == 1 and "-File" not in " ".join(created[0]), \
        "the task action must not need a PowerShell script file"
    assert f'cmd /c "{launch.wrapper_path}"' in created[0]
    assert not any("/end" in command or "/delete" in command for command in commands), \
        "the own task may only be released after the launch is closed"
    wrapper = launch.wrapper_path.read_text(encoding="utf-8")
    assert "> " in wrapper and " 2> " in wrapper, \
        "the launcher's own streams are captured next to the program evidence"
    assert str(harness.run_root / "evidence" / "WN02" / "launcher.out.txt") in wrapper
    harness.close_launch(launch)
    assert [command[3] for command in commands if "/end" in command] == [task]
    assert [command[3] for command in commands if "/delete" in command] == [task]
    assert launch.task_name is None and not launch.script_path.exists() \
        and not launch.wrapper_path.exists() and launch.closed


def test_scheduled_launch_timeout_cleans_only_the_owned_task(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONNAME", "Services")
    harness = native_harness.Harness(run_root=tmp_path / "run")
    commands = []
    monkeypatch.setattr(harness, "run_os",
                        lambda command, timeout=60: commands.append(list(command)) or command_result())
    monkeypatch.setattr(native_harness, "active_console_session", lambda: 1)
    monkeypatch.setattr(harness, "wait_for", lambda path, timeout: None)
    with pytest.raises(native_harness.HarnessError, match="launch record missing"):
        harness.start_program(LAUNCH_RECORD["path"], "C:/run", tmp_path / "store", [], "WN02", 1)
    created = [command for command in commands if command[:2] == ["schtasks", "/create"]]
    assert len(created) == 1
    task = created[0][3]
    for command in commands:
        if "/tn" in command:
            assert command[3] == task, f"another task was touched: {command}"
    assert any("/end" in command for command in commands)
    assert any("/delete" in command for command in commands)
    assert not list((tmp_path / "run").glob("launch_*.ps1"))
    assert not list((tmp_path / "run").glob("launch_*.cmd"))
    assert all("Stop-Process" not in " ".join(command) for command in commands)


def test_stop_owned_refuses_a_reused_pid_with_a_changed_identity(tmp_path, monkeypatch):
    harness = native_harness.Harness(run_root=tmp_path / "run")
    record = launch_record()
    monkeypatch.setattr(harness, "process_identity", lambda pid: {
        "pid": pid, "path": "C:/Windows/System32/notepad.exe", "session_id": 1,
        "start_time": "2026-09-21T00:00:00.0000000+08:00", "window_title": "Notepad"})
    commands = []
    monkeypatch.setattr(native_harness, "run_powershell_command",
                        lambda command, timeout=120: commands.append(command) or command_result(stdout="stopped"))
    result = harness.stop_owned(record)
    assert result["outcome"] == "refused"
    assert result["problems"] == ["executable path", "start time"]
    assert commands == [], "a changed identity must never reach Stop-Process"
    assert record["stopped_owned_pid"] is False


def test_stop_owned_rechecks_the_full_identity_inside_the_stop_command(tmp_path, monkeypatch):
    harness = native_harness.Harness(run_root=tmp_path / "run")
    record = launch_record()
    monkeypatch.setattr(harness, "process_identity", lambda pid: dict(record, pid=pid))
    commands = []
    monkeypatch.setattr(native_harness, "run_powershell_command",
                        lambda command, timeout=120: commands.append(command) or command_result(stdout="stopped"))
    result = harness.stop_owned(record)
    assert result["outcome"] == "stopped" and record["stopped_owned_pid"] is True
    command = commands[0]
    assert "Stop-Process -Id 4242" in command
    assert record["path"] in command and record["start_time"] in command
    assert "$p.SessionId -eq 1" in command


def test_stop_owned_reports_a_gone_process_without_killing(tmp_path, monkeypatch):
    harness = native_harness.Harness(run_root=tmp_path / "run")
    record = launch_record()
    monkeypatch.setattr(harness, "process_identity", lambda pid: None)
    commands = []
    monkeypatch.setattr(native_harness, "run_powershell_command",
                        lambda command, timeout=120: commands.append(command) or command_result())
    result = harness.stop_owned(record)
    assert result["outcome"] == "gone" and commands == []
    assert record["stopped_owned_pid"] is False


def test_run_program_timeout_never_kills_a_changed_pid(tmp_path, monkeypatch):
    harness = native_harness.Harness(run_root=tmp_path / "run")
    target = tmp_path / "program"
    target.mkdir()
    (target / ENTRY).write_bytes(pe_image())
    harness.releases = {"modes": {"anonymous": {"_target": str(target), "_manifest": {"entry": ENTRY}}}}
    launch = native_harness.Launch("WN02-wrong-binding", str(target / ENTRY), str(tmp_path / "store"),
                                   [], tmp_path / "run" / "launch.json", tmp_path / "run" / "exit.json",
                                   tmp_path / "run" / "out.txt", tmp_path / "run" / "err.txt",
                                   tmp_path / "run" / "launch.ps1", True)
    launch.record = launch_record(label="WN02-wrong-binding")
    monkeypatch.setattr(harness, "start_program", lambda *args, **kwargs: launch)
    monkeypatch.setattr(native_harness, "active_console_session", lambda: 1)
    monkeypatch.setattr(harness, "process_identity", lambda pid: {
        "pid": pid, "path": "C:/Windows/System32/notepad.exe", "session_id": 1,
        "start_time": "2026-09-21T00:00:00.0000000+08:00", "window_title": "Notepad"})
    monkeypatch.setattr(harness, "wait_for", lambda path, timeout=None: None)
    commands = []
    monkeypatch.setattr(native_harness, "run_powershell_command",
                        lambda command, timeout=120: commands.append(command) or command_result())
    case = native_harness.Case("WN02", "lifecycle fault injection")
    result = harness.run_program(case, "anonymous", tmp_path / "store", [], "WN02-wrong-binding",
                                 1, expect_exit=2)
    assert result["timed_out"] is True
    assert commands == [], "the timeout path must stay constrained by the identity check"
    assert any("refused" in json.dumps(check) for check in case.checks), \
        "the refusal must be recorded in the case evidence"


def test_launch_script_quotes_paths_with_spaces_and_apostrophes(tmp_path, monkeypatch):
    assert native_harness.ps_quote("C:/a'b c") == "'C:/a''b c'"
    monkeypatch.setenv("SESSIONNAME", "Console")
    run_root = tmp_path / "run dir 'quoted'"
    run_root.mkdir(parents=True)
    harness = native_harness.Harness(run_root=run_root)
    entry = "C:/Program Files/GEP 'synthetic'/GEP Synthetic Experiment.exe"
    workdir = "C:/Program Files/GEP 'synthetic'"
    script = harness.launch_script(entry, workdir, tmp_path / "store 'x'",
                                   ["--participant-code=001"], run_root / "out.txt", run_root / "err.txt",
                                   run_root / "launch.json", run_root / "exit.json")
    text = script.read_text(encoding="utf-8-sig")
    assert native_harness.ps_quote(entry) in text and native_harness.ps_quote(workdir) in text
    assert "'" + entry + "'" not in text, "an unescaped apostrophe must not break the quoted path"
    assert "$p.WaitForExit()" in text, "the launcher keeps the program running while writing the exit record"
    assert " " in str(script)
    launcher = FakeLauncher()
    spawned = []
    monkeypatch.setattr(harness, "spawn_launcher",
                        lambda path, out=None, err=None: spawned.append(Path(path)) or launcher)
    monkeypatch.setattr(harness, "wait_for", lambda path, timeout: dict(LAUNCH_RECORD))
    launch = harness.start_program(entry, workdir, tmp_path / "store", [], "WN01", 1)
    assert spawned and spawned[0] == launch.script_path and " " in str(spawned[0])


def test_launch_script_always_writes_the_exit_record_for_a_fast_program(tmp_path):
    """A refused program can exit before the launcher reads its identity; the
    generated body must still write the exit record (with an honest unknown code)
    instead of leaving the harness without a record. This is the real defect found
    on the Windows device: ``Start-Process -PassThru`` refuses
    ``EnableRaisingEvents`` once the process has exited and then reports no exit
    code, so the setter runs first and every later step is failure-tolerant."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    harness = native_harness.Harness(run_root=run_root)
    script = harness.launch_script("C:/run/GEP.exe", "C:/run", run_root / "store",
                                   ["--synthetic-auto"], run_root / "out.txt", run_root / "err.txt",
                                   run_root / "launch.json", run_root / "exit.json")
    body = script.read_text(encoding="utf-8-sig")
    position = body.index("try{$p.EnableRaisingEvents=$true}catch{}")
    assert position < body.index("Get-Process -Id $p.Id"), \
        "the event raising must be enabled before anything can delay the launcher"
    assert "$code=$null" in body and body.index("$code=$null") < body.index("exit.json"), \
        "an unavailable exit code is recorded as unknown, never guessed"
    assert body.rstrip().endswith("exit.json'"), "the exit record is the last statement"
    assert "path=''" in body and "start_time=''" in body, \
        "a gone process leaves an honest empty identity instead of aborting the record"
    assert native_harness.cmd_path_quote("C:/a b/out.txt") == '"C:/a b/out.txt"'
    assert native_harness.cmd_path_quote("C:/plain/out.txt") == "C:/plain/out.txt"


def test_launcher_invocation_never_needs_a_powershell_script_policy(tmp_path):
    """A default Windows client blocks ``powershell -File``; the owned launcher
    must run the same generated body as an encoded command instead, without an
    execution-policy override and without changing the machine."""
    run_root = tmp_path / "run"
    run_root.mkdir()
    harness = native_harness.Harness(run_root=run_root)
    script = harness.launch_script("C:/run/GEP.exe", "C:/run", tmp_path / "store",
                                   ["--synthetic-auto"], run_root / "out.txt", run_root / "err.txt",
                                   run_root / "launch.json", run_root / "exit.json")
    command = harness.launcher_command(script)
    assert command[0] == "powershell" and "-EncodedCommand" in command
    assert "-File" not in command and not any("ExecutionPolicy" in part for part in command)
    decoded = base64.b64decode(command[-1]).decode("utf-16-le")
    assert "$p.WaitForExit()" in decoded and "Start-Process" in decoded
    assert "try{$p.EnableRaisingEvents=$true}catch{}" in decoded and \
        "try{$p.EnableRaisingEvents=$true}catch{}" in decoded.split("$proc=", 1)[0], \
        "raising events must be enabled before the program can exit, or no exit code is readable"
    assert "$proc.MainWindowTitle" in decoded and "$proc.HasExited" in decoded, \
        "the interactive launcher observes the real window title for the record"
    assert "$identity=[ordered]@{" in decoded and "$identity.window_title" in decoded, \
        "the identity is captured before a fast-exiting program can disappear"
    assert "if(-not $proc.HasExited){$identity.window_title=$proc.MainWindowTitle}" in decoded
    assert "$code=$null" in decoded and "$code=(Get-Process -Id $p.Id" in decoded, \
        "the exit record is written even when no exit code can be read"
    assert decoded.count("Set-Content -Encoding UTF8") == 2, \
        "the launcher always writes both the launch record and the exit record"
    wrapper = harness.task_command_path(script)
    text = wrapper.read_text(encoding="utf-8")
    assert text.startswith("@echo off") and "-EncodedCommand" in text and "-File" not in text
    assert len(f'cmd /c "{wrapper}"') <= 261, "the scheduled-task action stays within the schtasks limit"


def test_powershell_output_is_read_as_utf8(monkeypatch):
    """Non-ASCII paths/titles must survive the PowerShell round trip exactly."""
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(native_harness.subprocess, "run", fake_run)
    native_harness.run_powershell_command("Get-Process -Id 1")
    assert "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" in captured["command"][3]
    assert captured["encoding"] == "utf-8" and captured["errors"] == "replace"


def test_wn01_accepts_a_scheduled_task_without_sessionname(tmp_path, monkeypatch):
    """The interactive scheduled task has no SESSIONNAME; the active console
    session decides and a null value must not crash the case."""
    harness = native_harness.Harness(run_root=tmp_path / "run")
    target = tmp_path / "program"
    target.mkdir()
    (target / ENTRY).write_bytes(pe_image())
    harness.releases = {"modes": {"anonymous": {"_target": str(target), "_manifest": {"entry": ENTRY}}}}
    record = launch_record(session_name=None)
    launch = types.SimpleNamespace(record=record)
    seen = {}

    def fake_start(entry, workdir, storage, arguments, label, timeout):
        seen["arguments"] = list(arguments)
        return launch

    monkeypatch.setattr(harness, "start_program", fake_start)
    monkeypatch.setattr(harness, "verify_owned",
                        lambda case, record, entry, require_window, label: record)
    monkeypatch.setattr(harness, "stop_owned", lambda record: {"outcome": "stopped", "problems": []})
    monkeypatch.setattr(harness, "close_launch", lambda launch: launch)
    monkeypatch.setattr(native_harness, "active_console_session", lambda: 2)
    assert harness.case_wn01() == native_harness.STATUS_PASS
    assert seen["arguments"] == ["--synthetic-auto", "--stop-after-trial"], \
        "WN01 must keep the program alive until the owned PID is really stopped"


def test_wn03_keeps_reconnect_evidence_before_the_completion_is_confirmed(tmp_path, monkeypatch):
    """The real program writes the cleaned tombstone as soon as the completion is
    acknowledged, so the reconnect evidence must be captured while the retained
    session is still present (records kept, pending cleared, completion declared)
    and the confirmed retry then writes the tombstone."""
    harness = native_harness.Harness(run_root=tmp_path / "run")
    old = {"study_id": "11111111-1111-1111-1111-111111111111", "release_id": "r",
           "participant_codes": ["001", "002"], "_target": str(tmp_path / "target"),
           "_manifest": {"entry": ENTRY}}
    harness.releases = {"modes": {"password": old}}
    session_id = "44444444-4444-4444-4444-444444444444"

    def interaction(event_id, trial):
        event = _event(event_id, 0.0, session_id=session_id)
        event["event_type"] = "exp.interaction"
        event["payload"] = {"trial_id": trial, "choice": "left"}
        return event

    boundary = [interaction("a" + "0" * 35, "t1"), _event("b" + "0" * 35, 321.5, session_id=session_id)]
    events = boundary + [interaction("c" + "0" * 35, "t2"), _event("d" + "0" * 35, 217.25, session_id=session_id)]
    offline = {"kind": "session", "id": session_id, "records": boundary,
               "pending": [event["event_id"] for event in boundary],
               "checkpoint": {"next_trial": 1}, "completion": None}
    retained = {"kind": "session", "id": session_id, "records": list(events), "pending": [],
                "segments": ["a", "b"], "checkpoint": {"next_trial": 1},
                "completion": {"event_ids": [event["event_id"] for event in events]}}
    stores = iter([{"sessions": [offline]}, {"sessions": [retained]},
                   {"sessions": [{"kind": "cleaned", "id": session_id}]}])
    monkeypatch.setattr(native_harness, "read_store", lambda storage: next(stores))
    calls = []
    proxies = []

    def fake_set_proxy(mode):
        calls.append(mode)
        proxy = types.SimpleNamespace(mode=mode, events=[])
        if mode == "lose-first-ack":
            proxy.events.append({"message": "losing acknowledgement of x"})
        harness.proxy = proxy
        proxies.append(proxy)

    monkeypatch.setattr(harness, "set_proxy", fake_set_proxy)
    monkeypatch.setattr(harness, "mode_arguments",
                        lambda case, mode, code=None: ["--synthetic-auto", f"--participant-code={code or '001'}"])
    monkeypatch.setattr(harness, "kill_program",
                        lambda *args, **kwargs: {"record": {"pid": 1}, "stdout": "SYNTHETIC_BOUNDARY_SAVED"})
    runs = []

    def fake_run_program(case, mode, storage, arguments, label, timeout, expect_exit=0, **kwargs):
        runs.append({"label": label, "arguments": list(arguments), "expect_exit": expect_exit})
        if label == "WN03-reconnect":
            return {"stdout": "SYNTHETIC_TIMEOUT {}", "exit": 3}
        if label == "WN03-reconnect-complete":
            return {"stdout": "SYNTHETIC_DATA_ONLY_DONE {}", "exit": 0}
        return {"stdout": "SYNTHETIC_DONE {}", "exit": 0}

    monkeypatch.setattr(harness, "run_program", fake_run_program)
    monkeypatch.setattr(harness, "snapshot_store",
                        lambda label, storage: {"queue.sqlite": f"evidence/{label}/queue.sqlite"})
    export_rows = [{"study_id": old["study_id"], "release_id": "r", "build_id": "b", "record": event}
                   for event in events]

    class FakeApi:
        records = []

        def export_jsonl(self, study):
            return "export-1", "\n".join(json.dumps(row) for row in export_rows).encode()

    harness.api = FakeApi()
    assert harness.case_wn03() == native_harness.STATUS_PASS
    assert calls[:6] == ["drop-events", "pass", "drop-completion", "pass", "lose-first-ack", "pass"]
    assert [run["label"] for run in runs] == ["WN03-reconnect", "WN03-reconnect-complete", "WN03-lost-ack"]
    assert runs[0]["expect_exit"] == 3 and runs[1]["arguments"][-1] == "--await-upload"
    evidence = harness.cases["WN03"].evidence["reconnect"]
    assert evidence["session_id"] == session_id and len(evidence["event_ids"]) == 4
    assert harness.cases["WN03"].evidence["export"]["values"] == [217.25, 321.5]


def test_snapshot_store_preserves_the_raw_bytes_and_derives_a_consistent_snapshot(tmp_path):
    """A killed program never checkpoints: the raw store bytes are preserved and
    the derived snapshot still reads back the committed rows of the ``-wal``."""
    storage = tmp_path / "store"
    storage.mkdir()
    connection = sqlite3.connect(str(storage / "queue.sqlite"))
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO sessions VALUES(?,?)", ["s1", '{"kind":"session"}'])
    connection.commit()
    # A second committed row that is still in the WAL when the process is killed.
    connection.execute("INSERT INTO sessions VALUES(?,?)", ["s2", '{"kind":"session","second":true}'])
    connection.commit()
    before = {name: hashlib.sha256((storage / name).read_bytes()).hexdigest()
              for name in ("queue.sqlite", "queue.sqlite-wal") if (storage / name).is_file()}
    assert "queue.sqlite-wal" in before, "the fixture must have an uncheckpointed WAL"
    harness = native_harness.Harness(run_root=tmp_path / "run")
    copies = harness.snapshot_store("snap", storage)
    after = {name: hashlib.sha256((storage / name).read_bytes()).hexdigest()
             for name in ("queue.sqlite", "queue.sqlite-wal")}
    assert after == before, "the live store is read only, never written or checkpointed in place"
    assert copies["derivation"] == "sqlite-backup"
    originals = copies["originals"]
    assert {"queue.sqlite", "queue.sqlite-wal"} <= set(originals)
    for name, entry in originals.items():
        path = harness.run_root / entry["path"]
        assert path.name == name and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        assert entry["size"] == path.stat().st_size
        assert path.read_bytes() == (storage / name).read_bytes(), "raw bytes are preserved unchanged"
    snapshot = harness.run_root / copies["snapshot"]["path"]
    assert snapshot.name == "queue.snapshot.sqlite"
    assert not (snapshot.parent / f"{snapshot.name}-wal").exists(), "the derived snapshot is self-contained"
    assert copies["snapshot"]["integrity_check"] == "ok"
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == copies["snapshot"]["sha256"]
    read = sqlite3.connect(f"file:{snapshot}?mode=ro&immutable=1", uri=True)
    try:
        rows = [row[0] for row in read.execute("SELECT value FROM sessions ORDER BY id").fetchall()]
        assert read.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
    finally:
        read.close()
    connection.close()
    assert rows == ['{"kind":"session"}', '{"kind":"session","second":true}'], \
        "the committed row that only lived in the WAL must be in the snapshot"


def test_snapshot_store_carries_every_table_of_the_store(tmp_path):
    """The snapshot is a complete database copy, not only the sessions table."""
    storage = tmp_path / "store"
    storage.mkdir()
    connection = sqlite3.connect(str(storage / "queue.sqlite"))
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("CREATE TABLE audit(id INTEGER PRIMARY KEY, note TEXT NOT NULL)")
    connection.execute("INSERT INTO sessions VALUES(?,?)", ["s1", '{"kind":"session"}'])
    connection.execute("INSERT INTO audit VALUES(?,?)", [1, "first"])
    connection.execute("INSERT INTO audit VALUES(?,?)", [2, "second"])
    connection.commit()
    connection.close()
    harness = native_harness.Harness(run_root=tmp_path / "run")
    copies = harness.snapshot_store("snap", storage)
    snapshot = harness.run_root / copies["snapshot"]["path"]
    read = sqlite3.connect(f"file:{snapshot}?mode=ro&immutable=1", uri=True)
    try:
        tables = sorted(row[0] for row in read.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        audit = read.execute("SELECT id, note FROM audit ORDER BY id").fetchall()
    finally:
        read.close()
    assert tables == ["audit", "sessions"] and audit == [(1, "first"), (2, "second")]


def test_snapshot_store_fails_closed_on_a_corrupt_store_and_keeps_the_raw_copy(tmp_path):
    """A store that cannot be copied consistently must raise, never yield a
    snapshot that silently lost committed rows; the raw bytes stay for
    inspection and no WAL file is deleted from the evidence."""
    storage = tmp_path / "store"
    storage.mkdir()
    (storage / "queue.sqlite").write_bytes(b"this is not a sqlite database")
    (storage / "queue.sqlite-wal").write_bytes(b"stale wal bytes")
    harness = native_harness.Harness(run_root=tmp_path / "run")
    with pytest.raises(native_harness.HarnessError, match="consistent snapshot derivation failed"):
        harness.snapshot_store("snap", storage)
    evidence = harness.run_root / "evidence" / "snap"
    assert (evidence / "queue.sqlite").read_bytes() == b"this is not a sqlite database"
    assert (evidence / "queue.sqlite-wal").read_bytes() == b"stale wal bytes"
    assert not (evidence / "queue.snapshot.sqlite").exists()


def test_snapshot_store_fails_closed_when_the_derivation_is_locked(tmp_path, monkeypatch):
    """A busy/locked store is an error with the scene preserved, never a green
    snapshot derived from a partial copy."""
    storage = tmp_path / "store"
    storage.mkdir()
    connection = sqlite3.connect(str(storage / "queue.sqlite"))
    connection.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.commit()
    connection.close()
    harness = native_harness.Harness(run_root=tmp_path / "run")

    import sqlite3 as sqlite_module
    real_connect = sqlite_module.connect

    def locked_connect(path, *args, **kwargs):
        if str(path).endswith("queue.sqlite"):
            raise sqlite_module.OperationalError("database is locked")
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(native_harness.sqlite3, "connect", locked_connect)
    with pytest.raises(native_harness.HarnessError, match="database is locked"):
        harness.snapshot_store("snap", storage)
    evidence = harness.run_root / "evidence" / "snap"
    assert (evidence / "queue.sqlite").is_file(), "the raw copy is preserved for inspection"
    assert (evidence / "working" / "queue.sqlite").is_file(), "the failed scratch copy stays"


def test_wait_for_marker_or_exit_stops_at_the_real_program_exit(tmp_path):
    """A refused program ends the boundary wait at once: the real exit code and
    its own output are reported instead of an anonymous marker timeout."""
    import threading

    harness = native_harness.Harness(run_root=tmp_path / "run")
    out = tmp_path / "program.out.txt"
    exit_record = tmp_path / "exit.json"
    out.write_text("SYNTHETIC_NAMED none\nSYNTHETIC_ERROR http_403 participation_limit\n", encoding="utf-8")

    def finish():
        time.sleep(1.0)
        out.write_text(out.read_text() + "Closed database\n", encoding="utf-8")
        exit_record.write_text(json.dumps({"pid": 7, "exit": 2, "exited": True}), encoding="utf-8")

    thread = threading.Thread(target=finish, daemon=True)
    started = time.time()
    thread.start()
    marker, text, code = harness.wait_for_marker_or_exit(out, exit_record, ("SYNTHETIC_BOUNDARY_SAVED",), 60)
    assert marker is None and code == 2
    assert "participation_limit" in text
    assert time.time() - started < 30, "the wait must end as soon as the exit record exists"
    # A marker printed before the exit still wins, so a normal boundary is unchanged.
    out.write_text("SYNTHETIC_BOUNDARY_SAVED\n", encoding="utf-8")
    marker, text, code = harness.wait_for_marker_or_exit(out, exit_record, ("SYNTHETIC_BOUNDARY_SAVED",), 60)
    assert marker == "SYNTHETIC_BOUNDARY_SAVED" and code is None
    thread.join(timeout=5)


def test_kill_program_reports_the_program_output_when_the_boundary_is_missed(tmp_path, monkeypatch):
    """The boundary failure detail carries the program's real stdout and exit
    code, so a refused admission is visible instead of an empty marker timeout."""
    harness = native_harness.Harness(kit=tmp_path, run_root=tmp_path / "run")
    harness.releases = {"modes": {"password": {"_target": str(tmp_path),
                                               "_manifest": {"entry": ENTRY}}}}
    (tmp_path / ENTRY).write_bytes(pe_image())

    def fake_start(entry, workdir, storage, arguments, label, timeout):
        out = tmp_path / "out.txt"
        out.write_text("SYNTHETIC_ERROR http_403 participation_limit\n", encoding="utf-8")
        exit_path = tmp_path / "exit.json"
        exit_path.write_text(json.dumps({"pid": 11, "exit": 2, "exited": True}), encoding="utf-8")
        return types.SimpleNamespace(record={"pid": 11}, out_path=out, exit_path=exit_path)

    monkeypatch.setattr(harness, "start_program", fake_start)
    monkeypatch.setattr(harness, "verify_owned", lambda *args, **kwargs: {})
    monkeypatch.setattr(harness, "stop_owned",
                        lambda record, case=None: {"outcome": "gone", "pid": 11, "problems": []})
    monkeypatch.setattr(harness, "close_launch", lambda launch: launch)
    case = harness.case("WN04")
    harness.kill_program(case, "password", tmp_path / "store", ["--synthetic-auto"],
                         "WN04-boundary", 30, "SYNTHETIC_BOUNDARY_SAVED")
    failure = [check for check in case.checks if not check["ok"]][0]
    assert "participation_limit" in failure["detail"]["stdout_tail"]
    assert failure["detail"]["exit"] == 2
    assert case.evidence["kills"][0]["exit"] == 2
    assert (harness.run_root / "evidence" / "WN04-boundary" / "program.stdout.txt").is_file()


def test_prerequisite_lost_blocks_a_case_when_the_tunnel_stops_serving(tmp_path):
    """A tunnel that is gone mid-run blocks the case precisely instead of
    producing misleading per-case network failures."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html>instance</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the test output clean
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    harness = native_harness.Harness(run_root=tmp_path / "run")
    harness.tunnel_port = server.server_address[1]
    harness.case("WN05")
    try:
        assert harness.prerequisite_lost("WN05") is False
        assert harness.cases["WN05"].status == native_harness.STATUS_NOT_RUN
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert harness.prerequisite_lost("WN05") is True
    case = harness.cases["WN05"]
    assert case.status == native_harness.STATUS_BLOCKED
    assert "runtime prerequisite lost" in case.reason
    assert case.evidence["runtime_prerequisite"]["port"] == harness.tunnel_port


def test_api_sends_the_csrf_token_for_session_posts(monkeypatch):
    """The authorized export POST is not csrf_exempt; the client must carry the
    same token the browser form would send."""
    api = native_harness.Api(8060)
    api.csrf = "synthetic-token"
    api.cookies = {"gep_admin": "session-value"}
    captured = {}

    class FakeResponse:
        status = 201

        def read(self):
            return b"{}"

        def getheaders(self):
            return []

    class FakeConnection:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host

        def request(self, method, path, body=None, headers=None):
            captured["headers"] = dict(headers)

        def getresponse(self):
            return FakeResponse()

        def close(self):
            pass

    monkeypatch.setattr(native_harness.http.client, "HTTPConnection", FakeConnection)
    status, _payload, _headers = api.request("POST", "/v1/admin/exports", body=b"{}")
    assert status == 201
    assert captured["headers"]["X-CSRFToken"] == "synthetic-token"
    assert captured["headers"]["Cookie"] == "gep_admin=session-value"


def test_mode_arguments_supports_the_anonymous_mode(tmp_path):
    """The anonymous release has no roster codes; building its arguments must not
    index an empty participant list."""
    harness = native_harness.Harness(run_root=tmp_path / "run")
    harness.releases = {"modes": {"anonymous": {"participant_codes": []}}}
    case = native_harness.Case("WN02", "synthetic")
    assert harness.mode_arguments(case, "anonymous") == ["--synthetic-auto"]
    assert not [check for check in case.checks if not check["ok"]]


def test_wn04_short_code_keeps_the_recovered_session_as_evidence(tmp_path, monkeypatch):
    """The recovered session is tombstoned as soon as its completion is ACKed, so
    the evidence (same session, new segment, four records) is captured while the
    completion is still unacknowledged; the confirmed retry then cleans it."""
    harness = native_harness.Harness(run_root=tmp_path / "run")
    info = {"study_id": "11111111-1111-1111-1111-111111111111", "release_id": "r",
            "participant_codes": ["001"], "_target": str(tmp_path / "target"),
            "_manifest": {"entry": ENTRY}}
    harness.releases = {"modes": {"password": info}}
    session_id = "44444444-4444-4444-4444-444444444444"
    events = [_event(f"{index}" + "0" * 35, 321.5 if index == 0 else 217.25, session_id=session_id)
              for index in range(4)]
    boundary = {"kind": "session", "id": session_id, "records": events[:2],
                "checkpoint": {"next_trial": 1}}
    retained = {"kind": "session", "id": session_id, "records": list(events), "segments": ["a", "b"],
                "checkpoint": {"next_trial": 1},
                "completion": {"event_ids": [event["event_id"] for event in events]}}
    stores = iter([{"sessions": [boundary]}, {"sessions": [retained]},
                   {"sessions": [{"kind": "cleaned", "id": session_id}]}])
    monkeypatch.setattr(native_harness, "read_store", lambda storage: next(stores))
    calls = []
    monkeypatch.setattr(harness, "set_proxy", lambda mode: calls.append(mode))
    monkeypatch.setattr(harness, "kill_program",
                        lambda *args, **kwargs: {"record": {"pid": 1}, "stdout": "SYNTHETIC_BOUNDARY_SAVED"})
    monkeypatch.setattr(harness, "issue_short_code", lambda study, session: "123456")
    runs = []

    def fake_run_program(case, mode, storage, arguments, label, timeout, expect_exit=0, **kwargs):
        runs.append({"label": label, "arguments": list(arguments), "expect_exit": expect_exit})
        if label == "WN04-short-code":
            return {"stdout": "SYNTHETIC_TIMEOUT {}", "exit": 3}
        if label == "WN04-replay":
            return {"stdout": "SYNTHETIC_ERROR recovery_denied", "exit": 2}
        if label == "WN04-short-code-complete":
            return {"stdout": "SYNTHETIC_DATA_ONLY_DONE {}", "exit": 0}
        return {"stdout": "", "exit": 0}

    monkeypatch.setattr(harness, "run_program", fake_run_program)
    monkeypatch.setattr(harness, "snapshot_store",
                        lambda label, storage: {"queue.sqlite": f"evidence/{label}/queue.sqlite"})
    case = native_harness.Case("WN04", "synthetic")
    harness._wn04_short_code(case, "password", tmp_path / "store", ["--synthetic-auto"])
    assert all(check["ok"] for check in case.checks), case.checks
    assert calls == ["drop-completion", "pass"]
    assert [run["label"] for run in runs] == ["WN04-short-code", "WN04-replay", "WN04-short-code-complete"]
    assert runs[0]["expect_exit"] == 3 and runs[2]["arguments"][-1] == "--await-upload"
    evidence = case.evidence["short_code"]
    assert evidence["session_id"] == session_id and len(evidence["event_ids"]) == 4
    assert len(evidence["segments"]) >= 2


def test_scoped_export_preserves_the_full_download_before_the_subset(tmp_path):
    """The study-scoped authorized download is preserved byte for byte, and the
    derived case file keeps exactly its session's downloaded lines."""
    session_id = "44444444-4444-4444-4444-444444444444"
    other = "99999999-9999-9999-9999-999999999999"
    payload = ("\n".join(json.dumps({"record": _event(f"{index}" + "0" * 35, 1.0, session_id=session_id)})
                         for index in range(2))
               + "\n" + json.dumps({"record": _event("5" + "0" * 35, 2.0, session_id=other)}) + "\n").encode()
    harness = native_harness.Harness(run_root=tmp_path / "run")
    target = harness.evidence_dir("export") / "export.jsonl"
    selected, info = harness.scoped_export(payload, session_id, target)
    assert len(selected) == 2 and all(row["record"]["session_id"] == session_id for row in selected)
    kept = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(kept) == 2 and all(row["record"]["session_id"] == session_id for row in kept)
    assert json.dumps(kept[0]) == json.dumps(selected[0])
    full = harness.run_root / info["path"]
    assert full.read_bytes() == payload, "the whole download is preserved without rewriting"
    assert full.name == "export-full.jsonl"
    assert info["sha256"] == hashlib.sha256(payload).hexdigest()
    assert info["rows_total"] == 3 and info["rows_kept"] == 2
    assert info["subset_path"] == harness.rel(target)
    assert info["subset_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_gate_refuses_a_lost_preserved_export_download(tmp_path, monkeypatch):
    """The derivation is re-checked from the preserved download: deleting it, or
    rewriting the derived file, must be refused even with fresh hashes."""
    prep, run_dir, modes, _descriptor = _fake_world(tmp_path, monkeypatch)
    entry = _case_entry(run_dir, "WN02", "password")
    full = run_dir / entry["export_full"]["path"]
    full.unlink()
    problems = _gate_problems(run_dir, prep)
    assert any("preserved full authorized download" in problem for problem in problems), problems
    assert any("has no preserved full authorized download" not in problem for problem in problems)
    # And a derived file that is not the download's own session rows is refused.
    prep, run_dir, modes, _descriptor = _fake_world(tmp_path / "second", monkeypatch)
    report = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    entry = next(item for item in report["cases"] if item["id"] == "WN02")["evidence"]["modes"]["password"]
    other = "99999999-9999-9999-9999-999999999999"
    derived = run_dir / entry["export_path"]
    derived.write_text(json.dumps({"study_id": "x", "release_id": modes["password"]["release_id"],
                                   "build_id": "y", "record": _event("9" * 36, 1.0, session_id=other)}) + "\n",
                       encoding="utf-8")
    entry["export_sha256"] = hashlib.sha256(derived.read_bytes()).hexdigest()
    entry["export_full"]["subset_sha256"] = entry["export_sha256"]
    (run_dir / "run.json").write_text(json.dumps(report))
    problems = _gate_problems(run_dir, prep)
    assert any("derived export is not the preserved download's session rows" in problem
               for problem in problems), problems


def test_kill_program_uses_the_explicit_old_release_target(tmp_path, monkeypatch):
    """The old-release case must run the superseded package's own entry, not the
    current release's extracted directory."""
    harness = native_harness.Harness(run_root=tmp_path / "run")
    harness.releases = {"modes": {"password": {"_target": str(tmp_path / "current"),
                                               "_manifest": {"entry": ENTRY}}}}
    old_target = tmp_path / "old"
    (old_target / ENTRY).parent.mkdir(parents=True, exist_ok=True)
    (old_target / ENTRY).write_bytes(pe_image())
    captured = {}

    def fake_start(entry, workdir, storage, arguments, label, timeout):
        captured["entry"] = entry
        return types.SimpleNamespace(record={"pid": 1}, out_path=tmp_path / "out.txt",
                                     exit_path=tmp_path / "exit.json")

    monkeypatch.setattr(harness, "start_program", fake_start)
    monkeypatch.setattr(harness, "verify_owned", lambda *args, **kwargs: {})
    monkeypatch.setattr(harness, "wait_for_marker", lambda *args, **kwargs: ("SYNTHETIC_BOUNDARY_SAVED", ""))
    monkeypatch.setattr(harness, "stop_owned", lambda record: {"outcome": "stopped", "problems": []})
    monkeypatch.setattr(harness, "wait_for", lambda *args, **kwargs: {"exit": -1})
    monkeypatch.setattr(harness, "close_launch", lambda launch: launch)
    case = native_harness.Case("WN06", "synthetic")
    harness.kill_program(case, "password", tmp_path / "store", [], "WN06", 10,
                         "SYNTHETIC_BOUNDARY_SAVED", target=old_target,
                         manifest={"entry": ENTRY})
    assert captured["entry"] == str(old_target / ENTRY)


def test_wn06_runs_the_superseded_release_with_scoped_export(tmp_path, monkeypatch):
    """The gate requires WN06 evidence on a release that stopped being current and
    exactly one session's rows in its export; the harness must run the earlier
    release, never the alternate/current one."""
    harness = native_harness.Harness(run_root=tmp_path / "run")
    old_release = "11111111-1111-1111-1111-111111111111"
    current_release = "22222222-2222-2222-2222-222222222222"
    study_id = "33333333-3333-3333-3333-333333333333"
    session_id = "44444444-4444-4444-4444-444444444444"
    old = {"study_id": study_id, "release_id": old_release, "delivery": "delivery/password/gep-old.zip",
           "participant_codes": ["001"],
           "_manifest": {"entry": ENTRY, "config_member": "GEP Synthetic Experiment/connection.json"}}
    harness.releases = {"modes": {"password": old}}
    harness.runtime = {"alternates": {"password": {"release_id": current_release}}}
    harness.kit = tmp_path / "kit"
    package = harness.kit / old["delivery"]
    package.parent.mkdir(parents=True)
    package.write_bytes(b"old-package")
    extracted = []

    def fake_extract(package_path, mode):
        extracted.append(Path(package_path))
        target = tmp_path / "extracted"
        (target / "GEP Synthetic Experiment").mkdir(parents=True)
        (target / "artifact_manifest.json").write_text(json.dumps(
            {"entry": ENTRY, "config_member": "GEP Synthetic Experiment/connection.json"}))
        (target / "GEP Synthetic Experiment" / "connection.json").write_text(
            json.dumps({"release_id": old_release}))
        return target

    monkeypatch.setattr(harness, "extract", fake_extract)
    monkeypatch.setattr(harness, "mode_arguments", lambda case, mode: ["--synthetic-auto"])
    monkeypatch.setattr(harness, "kill_program",
                        lambda *args, **kwargs: {"record": {"pid": 1}, "stdout": "SYNTHETIC_BOUNDARY_SAVED"})
    monkeypatch.setattr(harness, "run_program", lambda *args, **kwargs: {"stdout": "SYNTHETIC_DONE", "exit": 0})
    monkeypatch.setattr(harness, "snapshot_store",
                        lambda label, storage: {"queue.sqlite": f"evidence/{label}/queue.sqlite"})
    stores = iter([
        {"sessions": [{"kind": "session", "id": session_id,
                       "records": [_event("a" + "0" * 35, 321.5, session_id=session_id),
                                   _event("b" + "0" * 35, 217.25, session_id=session_id)]}]},
        {"sessions": [{"kind": "cleaned", "id": session_id}]},
    ])
    monkeypatch.setattr(native_harness, "read_store", lambda storage: next(stores))
    other = _event("5" + "0" * 35, 999.0, session_id="99999999-9999-9999-9999-999999999999")
    mine = [_event("a" + "0" * 35, 321.5, session_id=session_id),
            _event("b" + "0" * 35, 217.25, session_id=session_id),
            _event("c" + "0" * 35, 321.5, session_id=session_id),
            _event("d" + "0" * 35, 217.25, session_id=session_id)]
    payload = "\n".join(json.dumps({"study_id": study_id, "release_id": old_release, "build_id": "b",
                                    "record": event}) for event in [other, *mine]).encode()

    class FakeApi:
        records = []

        def export_jsonl(self, study):
            return "export-1", payload

    harness.api = FakeApi()
    assert harness.case_wn06() == native_harness.STATUS_PASS
    assert extracted == [package], "WN06 must run the superseded release package"
    evidence = harness.cases["WN06"].evidence["old_release"]
    assert evidence["release_id"] == old_release and evidence["session_id"] == session_id
    export_path = harness.run_root / evidence["export_path"]
    rows = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["record"]["event_id"] for row in rows] == [event["event_id"] for event in mine]


def test_verify_owned_uses_the_interactive_window_title(tmp_path, monkeypatch):
    """Over SSH the live read cannot enumerate windows on the interactive desktop;
    the title observed by the launcher in the owning session is the evidence, and
    an empty title still fails closed."""
    harness = native_harness.Harness(run_root=tmp_path / "run")
    record = launch_record(window_title="GEP Synthetic Experiment")
    monkeypatch.setattr(harness, "process_identity",
                        lambda pid: dict(record, pid=pid, window_title=""))
    monkeypatch.setattr(native_harness, "active_console_session", lambda: 1)
    case = native_harness.Case("WN01", "synthetic")
    live = harness.verify_owned(case, record, record["path"], require_window=True, label="t ")
    assert live["window_title"] == ""
    assert all(check["ok"] for check in case.checks), case.checks
    assert case.evidence["processes"][0]["window_title"] == "GEP Synthetic Experiment"
    empty = native_harness.Case("WN01", "synthetic")
    harness.verify_owned(empty, launch_record(window_title=""), record["path"],
                         require_window=True, label="t ")
    assert not all(check["ok"] for check in empty.checks)
