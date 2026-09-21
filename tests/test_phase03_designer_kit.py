"""03F designer kit contract tests (P0308).

These tests prove the designer kit's security and reference contracts with real
files and real child processes:

* ``service.env`` values with spaces/apostrophes survive quoting and are parsed
  literally (never executed), and credential-bearing job files are created 0600
  and removed after use;
* reserved/protected/existing environment roots are refused before any write;
* frozen package member scans catch credential-like members and session
  store/export members, and accept a clean package;
* generated documents reference files that really exist, and a missing target is
  reported;
* the Windows entry is generated for the frozen port, and the protected content
  digest detects a same-length mutation.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import phase03_designer_kit as designer  # noqa: E402


# ------------------------------------------------------------------ env safety
def test_service_env_quoting_round_trips_spaces_and_apostrophes():
    for value in ("plain", "/tmp/with spaces/dir", "it's a 'quoted' value", "a'b'c",
                  "$(touch /tmp/never-run) `id`"):
        quoted = designer.sh_quote(value)
        parsed = designer.parse_env_text(f"KEY={quoted}\n")
        assert parsed["KEY"] == value, value


def test_service_env_parser_never_executes_untrusted_text(tmp_path):
    marker = tmp_path / "executed"
    text = "\n".join([
        f"GEP_DATA_DIR={designer.sh_quote(str(tmp_path))}",
        "GEP_SECRET_KEY='$(touch %s)'" % marker,
        "GEP_DESIGNER_PORT='8041'",
        "GEP_DESIGNER_ADMIN=\"http://admin.localhost:8041\"",
    ])
    parsed = designer.parse_env_text(text)
    assert parsed["GEP_SECRET_KEY"] == f"$(touch {marker})"
    assert parsed["GEP_DESIGNER_PORT"] == "8041"
    assert parsed["GEP_DESIGNER_ADMIN"] == "http://admin.localhost:8041"
    assert not marker.exists(), "parsing service.env must never execute its text"


def test_atomic_write_private_is_0600_and_replaces_content(tmp_path):
    path = tmp_path / "secret.json"
    designer.atomic_write_private(path, '{"password": "first"}')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    designer.atomic_write_private(path, '{"password": "second"}')
    assert path.read_text(encoding="utf-8") == '{"password": "second"}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp*")), "no temporary credential copy may survive"


def test_run_node_driver_keeps_the_job_0600_and_removes_it(tmp_path):
    driver = tmp_path / "driver.cjs"
    driver.write_text(
        "const fs=require('fs');\n"
        "const job=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));\n"
        "const mode=(fs.statSync(process.argv[2]).mode & 0o777).toString(8);\n"
        "process.stdout.write(JSON.stringify({ok:mode==='600',mode,password:job.password})+'\\n');\n",
        encoding="utf-8")
    job_path = tmp_path / "evidence" / "check_job.json"
    code, payload = designer.run_node_driver(
        driver, {"path": job_path, "document": {"password": "synthetic-secret-value"}},
        tmp_path / "driver.log", 60)
    assert code == 0 and payload and payload["ok"] is True
    assert payload["password"] == "synthetic-secret-value"
    assert not job_path.exists(), "the credential-bearing job copy is removed after use"


# ------------------------------------------------------------------- guards
def test_guard_environment_root_refuses_reserved_and_existing_roots(tmp_path):
    for reserved in (designer.ROOT, designer.PROTECTED_VOLUME, designer.PHASE_ROOT, designer.FREEZE_ROOT):
        with pytest.raises(designer.DesignerKitError, match="reserved root"):
            designer.guard_environment_root(reserved)
    existing = designer.PHASE_ROOT / "designer_synthetic_exists"
    existing.mkdir(parents=True, exist_ok=True)
    try:
        with pytest.raises(designer.DesignerKitError, match="refusing to overwrite"):
            designer.guard_environment_root(existing)
    finally:
        existing.rmdir()
    with pytest.raises(designer.DesignerKitError, match="must live under"):
        designer.guard_environment_root(tmp_path / "elsewhere")
    assert not (tmp_path / "elsewhere").exists(), "an unsafe root must be rejected before any write"


# ------------------------------------------------------------- member scans
def test_archive_findings_detects_secret_and_session_members(tmp_path):
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("artifact_manifest.json", json.dumps({"platform": "macos_arm64"}))
        out.writestr("connection.json", json.dumps({"mode": "anonymous"}))
        out.writestr("state/queue.sqlite", b"SQLite format 3\x00")
        out.writestr("notes/credentials.json", 'password = "hunter2hunter2"')
        out.writestr("exports/records.jsonl", '{"session_id": "x"}\n')
    findings = designer.archive_findings(archive)
    joined = " | ".join(findings)
    assert "queue.sqlite" in joined
    assert "credentials.json" in joined
    assert "records.jsonl" in joined
    assert all("session store/export" in item or "credential-like" in item for item in findings)


def test_archive_findings_detects_a_private_account_value_inside_a_member(tmp_path):
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("artifact_manifest.json", json.dumps({"platform": "macos_arm64"}))
        out.writestr("connection.json", "the secret value synthetic-owner-password appears here")
    findings = designer.archive_findings(archive, secrets_values=["synthetic-owner-password"])
    assert any("private account value" in item for item in findings)


def test_archive_findings_does_not_flag_program_source_identifiers(tmp_path):
    """A DOM element id like ``password:'gec-input-password'`` is not a credential."""
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("web/gec/shell.js", "const FIELD_IDS = {code:'gec-input-code',password:'gec-input-password'};")
    assert designer.archive_findings(archive) == []
    findings = designer.archive_findings(archive, secrets_values=["gec-input-password"])
    assert any("private account value" in item for item in findings)


def test_archive_findings_enforces_the_bounded_budget(tmp_path):
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("manifest.json", "x" * 4096)
    assert any("uncompressed size" in item for item in designer.archive_findings(archive, byte_limit=16))
    assert any("member count" in item for item in designer.archive_findings(archive, member_limit=0))


def test_archive_findings_accepts_a_clean_package(tmp_path):
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("manifest.json", json.dumps({"platform": "godot_web"}))
        out.writestr("web/index.html", "<html></html>")
        out.writestr("artifact_manifest.json", json.dumps({"platform": "macos_arm64"}))
        out.writestr("connection.json", json.dumps({"mode": "id", "instance_id": "i"}))
        out.writestr("LICENSE", "GEP license")
        out.writestr("THIRD_PARTY_NOTICES.txt", "notices")
    assert designer.archive_findings(archive) == []
    with zipfile.ZipFile(archive) as handle:
        names = {info.filename for info in handle.infolist()}
    assert all(member in names for member in designer.required_members("macos-arm64"))
    assert all(member in names for member in designer.required_members("web"))


# --------------------------------------------------------------- references
def _synthetic_freeze(tmp_path):
    freeze = tmp_path / "freeze"
    root = tmp_path / "designer_synthetic"
    (freeze / "packages" / "sidecars" / "macos-arm64").mkdir(parents=True)
    (root / "private").mkdir(parents=True)
    (root / "instance" / "data").mkdir(parents=True)
    files = [freeze / "CHECKLIST.zh-CN.md", freeze / "RESULTS_TEMPLATE.md", freeze / "README.md",
             freeze / "SHA256SUMS.txt", freeze / "manifest.json", freeze / "OPEN-ADMIN-WINDOWS.cmd",
             freeze / "packages" / "macos-arm64-s.zip",
             root / "service.py",
             root / "private" / "service.env", root / "private" / "owner_credentials.json",
             root / "private" / "owner_password.txt", root / "private" / "designer_seed_accounts.json",
             root / "instance" / "data" / "instance", root / "instance" / "data" / "gep.sqlite3"]
    for path in files:
        path.write_text("synthetic", encoding="utf-8")
    entry_text = "\n".join(["#!/bin/sh", "set -e", f"cd {designer.sh_quote(designer.ROOT)}",
                            'HERE="$(cd "$(dirname "$0")" && pwd)"',
                            f'exec {designer.sh_quote(designer.VENV_PYTHON)} "$HERE/service.py" open-browser',
                            ""])
    (root / "START-HERE.command").write_text(entry_text, encoding="utf-8")
    (root / "open-browser.command").write_text(entry_text, encoding="utf-8")
    (root / "HANDOFF.md").write_text(
        "local_data/phase03_20260920/designer_synthetic/START-HERE.command packages/macos-arm64-s.zip",
        encoding="utf-8")
    for name in ("CHECKLIST.zh-CN.md", "RESULTS_TEMPLATE.md", "README.md"):
        (freeze / name).write_text("packages/macos-arm64-s.zip OPEN-ADMIN-WINDOWS.cmd", encoding="utf-8")
    manifest = {"packages": {"packages/macos-arm64-s.zip": {"platform": "macos-arm64"}}}
    return root, freeze, manifest


def test_check_generated_references_accepts_a_complete_tree_and_reports_a_missing_one(tmp_path):
    root, freeze, manifest = _synthetic_freeze(tmp_path)
    checks = []
    designer.check_generated_references(root, freeze, manifest, lambda ok, label, detail=None: checks.append(ok) or ok)
    assert all(checks), checks
    (freeze / "README.md").unlink()
    checks = []
    with pytest.raises(designer.DesignerKitError, match="missing files"):
        designer.check_generated_references(root, freeze, manifest,
                                            lambda ok, label, detail=None: checks.append(ok) or ok)
    assert not all(checks), "a missing generated reference must be reported and refuse the preparation"


def test_check_generated_references_refuses_a_wrong_document_target(tmp_path):
    """A parent-relative or non-existent target inside a document is refused."""
    root, freeze, manifest = _synthetic_freeze(tmp_path)
    (freeze / "README.md").write_text(
        "见 local_data/phase03_20260920/designer_synthetic/../elsewhere/START-HERE.command",
        encoding="utf-8")
    checks = []
    with pytest.raises(designer.DesignerKitError, match="missing targets"):
        designer.check_generated_references(root, freeze, manifest,
                                            lambda ok, label, detail=None: checks.append((ok, label)) or ok)
    assert any(not ok for ok, _label in checks)


def test_check_generated_references_refuses_a_broken_entry_interpreter(tmp_path):
    """A double-click entry that points at a missing interpreter is refused."""
    root, freeze, manifest = _synthetic_freeze(tmp_path)
    (root / "START-HERE.command").write_text("\n".join([
        "#!/bin/sh", "set -e", f"cd {designer.sh_quote(designer.ROOT)}",
        'HERE="$(cd "$(dirname "$0")" && pwd)"',
        'exec \'/no/such/python\' "$HERE/service.py" open-browser', ""]), encoding="utf-8")
    checks = []
    with pytest.raises(designer.DesignerKitError, match="missing targets"):
        designer.check_generated_references(root, freeze, manifest,
                                            lambda ok, label, detail=None: checks.append((ok, label, detail)) or ok)
    assert any(not ok for ok, _label, _detail in checks)


def test_windows_entry_and_manifest_bind_the_frozen_port(tmp_path):
    target = tmp_path / "freeze"
    target.mkdir()
    members = {}
    manifest = designer.write_freeze_manifest(
        target, members, lambda *args, **kwargs: True, "testsynthetic",
        {"macos-arm64": {"study_id": "s"}},
        {"macos-arm64_study": {"study_id": "s"}},
        {"path": "local_data/phase03_20260920/designer_testsynthetic", "port": 8041,
         "instance_id": "11111111-1111-1111-1111-111111111111",
         "tunnel": {"listen_port": 8041, "target_port": 8041}})
    entry = (target / "OPEN-ADMIN-WINDOWS.cmd").read_text(encoding="utf-8")
    assert "http://localhost:8041/login" in entry
    assert "start \"\"" in entry
    assert manifest["instance_id"] == "11111111-1111-1111-1111-111111111111"
    assert manifest["human_status"] == designer.HUMAN_STATUS
    assert "OPEN-ADMIN-WINDOWS.cmd" in members
    assert (target / "SHA256SUMS.txt").is_file()


def test_freeze_hash_list_refuses_a_replaced_digest_or_a_missing_member(tmp_path):
    freeze = tmp_path / "freeze"
    freeze.mkdir()
    package = freeze / "packages" / "macos-arm64-s.zip"
    package.parent.mkdir()
    package.write_bytes(b"frozen bytes")
    digest = designer.sha256_file(package)
    (freeze / "SHA256SUMS.txt").write_text(f"{digest}  packages/macos-arm64-s.zip\n", encoding="utf-8")
    assert designer.freeze_hash_mismatches(freeze) == []
    (freeze / "SHA256SUMS.txt").write_text(f"{'0' * 64}  packages/macos-arm64-s.zip\n", encoding="utf-8")
    assert designer.freeze_hash_mismatches(freeze) == ["packages/macos-arm64-s.zip"]
    (freeze / "SHA256SUMS.txt").write_text(f"{digest}  packages/other.zip\n", encoding="utf-8")
    assert designer.freeze_hash_mismatches(freeze) == ["packages/other.zip"]
    (freeze / "SHA256SUMS.txt").unlink()
    assert designer.freeze_hash_mismatches(freeze) == ["SHA256SUMS.txt"]


def test_freeze_hash_list_refuses_empty_malformed_duplicate_and_coverage_gaps(tmp_path):
    freeze = tmp_path / "freeze"
    (freeze / "packages").mkdir(parents=True)
    package = freeze / "packages" / "web-s.zip"
    package.write_bytes(b"web bytes")
    digest = designer.sha256_file(package)
    member = {"packages/web-s.zip": {"platform": "web"}}
    (freeze / "SHA256SUMS.txt").write_text("", encoding="utf-8")
    problems = designer.freeze_hash_mismatches(freeze, members=member)
    assert any("empty hash list" in item for item in problems)
    (freeze / "SHA256SUMS.txt").write_text("not-a-hash  packages/web-s.zip\n", encoding="utf-8")
    problems = designer.freeze_hash_mismatches(freeze, members=member)
    assert any("malformed" in item for item in problems)
    (freeze / "SHA256SUMS.txt").write_text(f"{digest}  packages/web-s.zip\n{digest}  packages/web-s.zip\n",
                                           encoding="utf-8")
    problems = designer.freeze_hash_mismatches(freeze, members=member)
    assert any("duplicate" in item for item in problems)
    (freeze / "SHA256SUMS.txt").write_text(f"{digest}  packages/other.zip\n", encoding="utf-8")
    problems = designer.freeze_hash_mismatches(freeze, members=member)
    assert "missing hash line for manifest member packages/web-s.zip" in problems
    assert "hash line has no manifest member packages/other.zip" in problems
    (freeze / "SHA256SUMS.txt").write_text(f"{digest}  packages/web-s.zip\n", encoding="utf-8")
    assert designer.freeze_hash_mismatches(freeze, members=member) == []


# ------------------------------------------------------- freeze/build binding
def test_freeze_binding_refuses_a_stale_program_and_old_source(tmp_path):
    freeze = tmp_path / "freeze"
    (freeze / "packages").mkdir(parents=True)
    package = freeze / "packages" / "macos-arm64-s.zip"

    def write_package(program_sha256):
        with zipfile.ZipFile(package, "w") as out:
            out.writestr("artifact_manifest.json", json.dumps(
                {"platform": "macos_arm64", "config_member": "connection.json",
                 "program_sha256": program_sha256}))
            out.writestr("connection.json", json.dumps({"mode": "id"}))
        return designer.sha256_file(package)

    package_sha = write_package("0" * 64)
    live = tmp_path / "live.zip"
    live.write_bytes(b"live build bytes")
    live_digest = designer.sha256_file(live)
    current = {"program_source_digest": "new-source-digest",
               "archives": {"macos-arm64": {"sha256": live_digest}},
               "descriptors": {"macos-arm64": {"sha256": "1" * 64, "program_sha256": live_digest}}}
    manifest = {"packages": {"packages/macos-arm64-s.zip": {"platform": "macos-arm64",
                                                           "sha256": package_sha}},
                "build": {"program_source_digest": "old-source-digest",
                          "archives": {"macos-arm64": {"sha256": "0" * 64}},
                          "descriptors": {"macos-arm64": {"sha256": "0" * 64,
                                                          "program_sha256": "0" * 64}}}}
    problems = designer.freeze_binding_problems(manifest, current=current, freeze=freeze)
    joined = " | ".join(problems)
    assert "旧源码" in joined
    assert "归档摘要与当前构建不一致" in joined
    assert "陈旧构建" in joined
    assert "描述" in joined
    # A package that matches its own manifest and hash but the *current* build passes.
    manifest["build"] = {"program_source_digest": current["program_source_digest"],
                         "archives": {"macos-arm64": dict(current["archives"]["macos-arm64"])},
                         "descriptors": {"macos-arm64": dict(current["descriptors"]["macos-arm64"])}}
    manifest["packages"]["packages/macos-arm64-s.zip"]["sha256"] = write_package(live_digest)
    assert designer.freeze_binding_problems(manifest, current=current, freeze=freeze) == []


def test_member_scan_derives_platform_required_members_from_the_package(tmp_path):
    """Deleting required_members from the manifest cannot bypass the platform list."""
    freeze = tmp_path / "freeze"
    (freeze / "packages").mkdir(parents=True)
    package = freeze / "packages" / "macos-arm64-s.zip"
    with zipfile.ZipFile(package, "w") as out:
        out.writestr("artifact_manifest.json", json.dumps(
            {"platform": "macos_arm64", "config_member": "connection.json"}))
        out.writestr("connection.json", json.dumps({"mode": "id"}))
    manifest = {"packages": {"packages/macos-arm64-s.zip": {"platform": "macos-arm64"}}}
    problems = " ".join(designer.member_scan_problems(freeze, manifest, []))
    assert "missing required member LICENSE" in problems
    assert "missing required member THIRD_PARTY_NOTICES.txt" in problems
    with zipfile.ZipFile(package, "w") as out:
        out.writestr("artifact_manifest.json", json.dumps(
            {"platform": "macos_arm64", "config_member": "connection.json"}))
        out.writestr("connection.json", json.dumps({"mode": "id"}))
        out.writestr("LICENSE", "license text")
        out.writestr("THIRD_PARTY_NOTICES.txt", "notices text")
    assert designer.member_scan_problems(freeze, manifest, []) == []


def test_member_scan_refuses_a_package_without_a_config_member(tmp_path):
    freeze = tmp_path / "freeze"
    (freeze / "packages").mkdir(parents=True)
    package = freeze / "packages" / "windows-x64-s.zip"
    with zipfile.ZipFile(package, "w") as out:
        out.writestr("artifact_manifest.json", json.dumps({"platform": "windows_x64"}))
        out.writestr("connection.json", "{}")
        out.writestr("LICENSE", "l")
        out.writestr("THIRD_PARTY_NOTICES.txt", "n")
    manifest = {"packages": {"packages/windows-x64-s.zip": {"platform": "windows-x64"}}}
    problems = " ".join(designer.member_scan_problems(freeze, manifest, []))
    assert "缺少 config_member" in problems


# ------------------------------------------------------------- entry points
def test_entry_ready_never_trusts_a_broken_or_forged_entry(tmp_path):
    url = "http://admin.localhost:8041/login"
    payload = '{"opened": "%s", "status": {"ready": true}}' % url

    def script(body, exit_code):
        path = tmp_path / f"entry_{exit_code}_{abs(hash(body))}.command"
        path.write_text(f"#!/bin/sh\n{body}\nexit {exit_code}\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    broken = script("printf '%s\\n' '{}'", 0)
    (tmp_path / "missing.command").write_text("#!/bin/sh\nexec /no/such/python x\n", encoding="utf-8")
    (tmp_path / "missing.command").chmod(0o755)
    assert not designer.entry_ready(designer.run_generated_entry(tmp_path / "missing.command", timeout=30), url)
    forged = script(f"printf '%s\\n' '{payload}'", 3)
    result = designer.run_generated_entry(forged, timeout=30)
    assert result["exit"] == 3 and not designer.entry_ready(result, url)
    refused = script("printf '%s\\n' '{\"opened\": null, \"refused\": true}'", 3)
    assert not designer.entry_ready(designer.run_generated_entry(refused, timeout=30), url)
    wrong = script("printf '%s\\n' '{\"opened\": \"http://admin.localhost:9999/login\","
                   " \"status\": {\"ready\": true}}'", 0)
    assert not designer.entry_ready(designer.run_generated_entry(wrong, timeout=30), url)
    good = script(f"printf '%s\\n' '{payload}'", 0)
    assert designer.entry_ready(designer.run_generated_entry(good, timeout=30), url)
    assert not designer.entry_ready(designer.run_generated_entry(broken, timeout=30), url)


def _live_self_command():
    result = subprocess.run(["ps", "-o", "command=", "-p", str(os.getpid())], capture_output=True, text=True)
    return (result.stdout or "").strip()


def test_generated_service_never_opens_a_recorded_service_that_is_not_ready(tmp_path):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env_root = _service_environment(tmp_path, port)
    live = _live_self_command()
    assert live
    (env_root / "service.pid").write_text(
        json.dumps({"pid": os.getpid(), "command": live}), encoding="utf-8")
    code, payload = _helper(env_root, "start")
    assert code == 3 and payload["refused"] is True
    assert "not the frozen ready instance" in payload["reason"]
    assert payload["status"]["ready"] is False
    code, payload = _helper(env_root, "status")
    assert payload["pid_verified"] is True and payload["ready"] is False
    assert os.getpid() > 0, "the test process must never be signalled"


def test_generated_service_refuses_a_malformed_pid_record(tmp_path):
    env_root = _service_environment(tmp_path, 8126)
    pid_file = env_root / "service.pid"
    pid_file.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    code, payload = _helper(env_root, "start")
    assert code == 3 and "no recorded command line" in payload["reason"]
    code, payload = _helper(env_root, "stop")
    assert code == 3 and payload["refused"] is True
    assert pid_file.is_file(), "a refused record must stay for inspection"
    code, payload = _helper(env_root, "status")
    assert payload["pid_verified"] is False
    assert os.getpid() > 0


# ------------------------------------------------------------ protected data
def _service_environment(tmp_path, port):
    env_root = tmp_path / "local_data" / "phase03_20260920" / "designer_testsynthetic"
    (env_root / "private").mkdir(parents=True)
    (env_root / "evidence").mkdir()
    (env_root / "instance" / "data").mkdir(parents=True)
    (env_root / "private" / "service.env").write_text("\n".join([
        f"GEP_DATA_DIR={designer.sh_quote(str(env_root / 'instance' / 'data'))}",
        f"GEP_SECRET_KEY={designer.sh_quote('synthetic-secret')}",
        f"GEP_EXPECTED_INSTANCE={designer.sh_quote('11111111-1111-1111-1111-111111111111')}",
        f"GEP_PUBLIC_API={designer.sh_quote(f'http://experiment.localhost:{port}')}",
        f"GEP_DESIGNER_PORT={designer.sh_quote(port)}",
        f"GEP_DESIGNER_ADMIN={designer.sh_quote(f'http://admin.localhost:{port}')}",
        ""]), encoding="utf-8")
    (env_root / "service.py").write_text(designer.SERVICE_HELPER, encoding="utf-8")
    return env_root


def _helper(env_root, command):
    result = subprocess.run([sys.executable, str(env_root / "service.py"), command],
                            capture_output=True, text=True, timeout=60)
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    return result.returncode, payload


def test_generated_service_refuses_an_occupied_port(tmp_path):
    """An occupied port must never be reported as our ready instance."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(16)
        port = probe.getsockname()[1]
        env_root = _service_environment(tmp_path, port)
        code, payload = _helper(env_root, "start")
        assert code == 3 and payload["refused"] is True
        assert "another process" in payload["reason"]
        code, payload = _helper(env_root, "status")
        assert payload["port_answers"] is True and payload["ready"] is False


def test_generated_service_refuses_a_reused_pid_and_never_kills_it(tmp_path):
    env_root = _service_environment(tmp_path, 8123)
    pid_file = env_root / "service.pid"
    pid_file.write_text(json.dumps({"pid": os.getpid(), "command": "gunicorn gep.wsgi:application"}), encoding="utf-8")
    code, payload = _helper(env_root, "start")
    assert code == 3 and "refusing to reuse" in payload["reason"]
    code, payload = _helper(env_root, "stop")
    assert code == 3 and payload["refused"] is True
    assert pid_file.is_file(), "a refused PID record must stay for inspection"
    assert os.getpid() > 0, "this test process is still alive (no foreign kill happened)"


def test_generated_service_reports_identity_mismatch_as_not_ready(tmp_path):
    env_root = _service_environment(tmp_path, 8124)
    code, payload = _helper(env_root, "status")
    assert payload["ready"] is False
    assert payload["identity"]["matches"] is False
    assert payload["identity"]["expected"] == "11111111-1111-1111-1111-111111111111"


# ------------------------------------------- serve foreground shutdown signals
def _serve_wait_process(ignore_sigint):
    """A real child that runs the kit's foreground wait, with a bounded READY gate.

    ``--serve`` used to end in a bare ``time.sleep(3600)``: SIGINT/SIGTERM then fell
    back to the inherited/default disposition, and a caller whose stop path relied
    on the signal had to SIGKILL, which orphans the scoped ssh child. The child
    announces READY only after the handlers are installed, so the test signals a
    known-good state.
    """
    lines = [
        "import signal, sys, threading, time",
        f"sys.path.insert(0, {str(TOOLS)!r})",
        "import phase03_designer_kit as kit",
    ]
    if ignore_sigint:
        lines.append("signal.signal(signal.SIGINT, signal.SIG_IGN)")
    lines += [
        "handler = signal.getsignal(signal.SIGINT)",
        "label = ('ignored' if handler is signal.SIG_IGN else 'sig_dfl' if handler is signal.SIG_DFL",
        "         else 'default' if handler is signal.default_int_handler else repr(handler))",
        "print('DISPOSITION %s' % label, flush=True)",
        "def announce():",
        "    time.sleep(0.5)",
        "    print('READY', flush=True)",
        "threading.Thread(target=announce, daemon=True).start()",
        "kit.wait_for_shutdown(poll=0.05)",
        "print('CLEAN', flush=True)",
    ]
    return subprocess.Popen([sys.executable, "-c", "\n".join(lines)], cwd=str(designer.ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _line_within(process, timeout=30):
    import threading

    box = {}
    reader = threading.Thread(target=lambda: box.setdefault("line", process.stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    return box.get("line", "")


def _assert_serve_wait_stops(process, signal_number, expected_disposition, timeout=30):
    try:
        first = _line_within(process)
        assert "DISPOSITION" in first, first
        assert first.split()[-1] == expected_disposition, first
        ready = _line_within(process)
        assert "READY" in ready, ready
        process.send_signal(signal_number)
        output, _ = process.communicate(timeout=timeout)
        assert process.returncode == 0, f"exit={process.returncode} output={output!r}"
        assert "CLEAN" in output, output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_serve_foreground_wait_stops_cleanly_on_sigterm():
    """SIGTERM must be a graceful stop path for the foreground wait (never SIGKILL-only)."""
    import signal

    _assert_serve_wait_stops(_serve_wait_process(ignore_sigint=False), signal.SIGTERM, "default")


def test_serve_foreground_wait_rearms_sigint_ignored_by_the_parent():
    """A non-interactive launch inherits SIGINT ignored: the wait must re-arm it and stop cleanly."""
    import signal

    _assert_serve_wait_stops(_serve_wait_process(ignore_sigint=True), signal.SIGINT, "ignored")


def test_protected_digest_detects_a_same_length_mutation(tmp_path, monkeypatch):
    volume = tmp_path / "protected"
    volume.mkdir()
    (volume / "record.bin").write_bytes(b"same-length-payload-A")
    monkeypatch.setattr(designer, "PROTECTED_VOLUME", volume)
    monkeypatch.setattr(designer, "PROTECTED_DB", tmp_path / "missing.sqlite3")
    before = designer.protected_digest()
    (volume / "record.bin").write_bytes(b"same-length-payload-B")
    assert designer.protected_digest() != before


# --------------------------------------------------- fail-closed preflight gate
INSTANCE_ID = "11111111-1111-1111-1111-111111111111"
STUDY_ID = "22222222-2222-2222-2222-222222222222"
RELEASE_ID = "33333333-3333-3333-3333-333333333333"


def _verifiable_tree(tmp_path, monkeypatch):
    """A complete synthetic environment + freeze that passes every preflight check.

    The Web package is a byte copy of the real exported build so the build/source
    binding check is exercised against the actual workspace, and the native
    packages are real zips whose recorded program digest is the real archive
    digest. The negative tests below only inject one defect on top of this tree.
    """
    monkeypatch.setattr(designer, "FREEZE_ROOT", tmp_path / "build" / "phase03_20260920")
    root = tmp_path / "local_data" / "phase03_20260920" / "designer_synthetic"
    freeze = designer.FREEZE_ROOT / "synthetic"
    (freeze / "packages").mkdir(parents=True)
    (root / "private").mkdir(parents=True)
    (root / "evidence").mkdir()
    (root / "instance" / "data").mkdir(parents=True)
    (root / "private" / "owner_password.txt").write_text("synthetic-owner-password\n", encoding="utf-8")
    (root / "private" / "owner_credentials.json").write_text(
        '{"username": "synthetic_owner", "password": "synthetic-owner-password"}', encoding="utf-8")
    (root / "private" / "designer_seed_accounts.json").write_text(
        json.dumps({"accounts": {}}), encoding="utf-8")
    (root / "private" / "service.env").write_text("\n".join([
        f"GEP_DATA_DIR={designer.sh_quote(str(root / 'instance' / 'data'))}",
        f"GEP_SECRET_KEY={designer.sh_quote('synthetic-instance-secret')}",
        f"GEP_EXPECTED_INSTANCE={designer.sh_quote(INSTANCE_ID)}",
        f"GEP_PUBLIC_API={designer.sh_quote('http://experiment.localhost:8041')}",
        f"GEP_DESIGNER_PORT={designer.sh_quote('8041')}",
        f"GEP_DESIGNER_ADMIN={designer.sh_quote('http://admin.localhost:8041')}",
        ""]), encoding="utf-8")
    (root / "service.py").write_text(designer.SERVICE_HELPER, encoding="utf-8")
    (root / "instance" / "data" / "instance").write_text(INSTANCE_ID, encoding="utf-8")
    (root / "instance" / "data" / "secret").write_text("synthetic-instance-secret", encoding="utf-8")
    (root / "instance" / "data" / "gep.sqlite3").write_bytes(b"")
    entry_text = "\n".join(["#!/bin/sh", "set -e", f"cd {designer.sh_quote(designer.ROOT)}",
                            'HERE="$(cd "$(dirname "$0")" && pwd)"',
                            f'exec {designer.sh_quote(designer.VENV_PYTHON)} "$HERE/service.py" open-browser',
                            ""])
    (root / "START-HERE.command").write_text(entry_text, encoding="utf-8")
    (root / "open-browser.command").write_text(entry_text, encoding="utf-8")
    (root / "HANDOFF.md").write_text(
        "local_data/phase03_20260920/designer_synthetic/START-HERE.command\npackages/web-study.zip\n",
        encoding="utf-8")

    binding = designer.current_build_binding()
    packages = {}
    web_name = "packages/web-study.zip"
    shutil.copy2(designer.ROOT / "build" / "synthetic_web.zip", freeze / web_name)
    packages[web_name] = {"platform": "web", "mode": "anonymous", "study_id": STUDY_ID,
                          "release_id": RELEASE_ID, "config_member": "connection.json",
                          "sha256": designer.sha256_file(freeze / web_name),
                          "required_members": list(designer.required_members("web"))}
    sidecar_name = "packages/web-study.connection.json"
    (freeze / sidecar_name).write_text(json.dumps(
        {"instance_id": INSTANCE_ID, "study_id": STUDY_ID, "release_id": RELEASE_ID, "mode": "anonymous"}),
        encoding="utf-8")
    packages[sidecar_name] = {"platform": "web", "source": "sidecar",
                              "sha256": designer.sha256_file(freeze / sidecar_name)}
    for platform, manifest_platform, mode in (("macos-arm64", "macos_arm64", "id"),
                                              ("windows-x64", "windows_x64", "password")):
        name = f"packages/{platform}-study.zip"
        with zipfile.ZipFile(freeze / name, "w") as out:
            out.writestr("artifact_manifest.json", json.dumps(
                {"platform": manifest_platform, "config_member": "connection.json",
                 "program_sha256": binding["archives"][platform]["sha256"]}))
            out.writestr("connection.json", json.dumps(
                {"instance_id": INSTANCE_ID, "study_id": STUDY_ID, "release_id": RELEASE_ID, "mode": mode}))
            out.writestr("LICENSE", "GEP license")
            out.writestr("THIRD_PARTY_NOTICES.txt", "notices")
        packages[name] = {"platform": platform, "mode": mode, "study_id": STUDY_ID,
                          "release_id": RELEASE_ID, "config_member": "connection.json",
                          "sha256": designer.sha256_file(freeze / name),
                          "required_members": list(designer.required_members(platform))}
    (freeze / "CHECKLIST.zh-CN.md").write_text("状态 NOT_RUN；见 RESULTS_TEMPLATE.md", encoding="utf-8")
    (freeze / "RESULTS_TEMPLATE.md").write_text("状态 NOT_RUN", encoding="utf-8")
    (freeze / "README.md").write_text("哈希清单不是签名", encoding="utf-8")
    (freeze / "OPEN-ADMIN-WINDOWS.cmd").write_text(
        "@echo off\r\nstart \"\" http://localhost:8041/login\r\n", encoding="utf-8")
    manifest = {"format": "gep-phase03-designer-kit/v2", "stamp": "synthetic",
                "environment": "local_data/phase03_20260920/designer_synthetic",
                "port": 8041, "instance_id": INSTANCE_ID, "packages": packages,
                "bindings": {}, "seed_objects": {}, "build": binding, "tunnel": {},
                "human_status": designer.HUMAN_STATUS, "notes": []}
    (freeze / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _rewrite_sums(freeze, packages)
    return root, freeze, manifest


def _rewrite_sums(freeze, packages):
    (freeze / "SHA256SUMS.txt").write_text(
        "".join(f"{entry['sha256']}  {name}\n" for name, entry in sorted(packages.items())), encoding="utf-8")


def _rewrite_package(freeze, manifest, name, members):
    package = freeze / name
    with zipfile.ZipFile(package, "w") as out:
        for member, payload in members.items():
            out.writestr(member, payload)
    manifest["packages"][name]["sha256"] = designer.sha256_file(package)
    _rewrite_sums(freeze, manifest["packages"])
    (freeze / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _launch_sentinels(monkeypatch):
    """Any external launch path is a hard failure while the gate is red."""
    calls = []

    def sentinel(name):
        def boom(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"a failed preflight must not launch anything: {name}")
        return boom

    monkeypatch.setattr(designer, "run_generated_entry", sentinel("run_generated_entry"))
    monkeypatch.setattr(designer, "run_node_driver", sentinel("run_node_driver"))
    monkeypatch.setattr(designer, "run_frozen_macos_package", sentinel("run_frozen_macos_package"))
    monkeypatch.setattr(designer.subprocess, "run", sentinel("subprocess.run"))
    return calls


def _quiet_record(*args, **kwargs):
    return False


def _failed_labels(readiness):
    return {entry["label"] for entry in readiness["checks"] if not entry["ok"]}


def test_verify_preflight_is_green_only_for_a_complete_kit(tmp_path, monkeypatch):
    """Control: the complete tree reaches the launch phase; nothing launches early."""
    root, freeze, manifest = _verifiable_tree(tmp_path, monkeypatch)
    launched = []

    def fake_entry(*args, **kwargs):
        launched.append("entry")
        return {"path": "sentinel", "exit": None, "error": "sentinel", "payload": None}

    def fake_node(*args, **kwargs):
        launched.append("node")
        return 0, {"ok": False, "error": "sentinel"}

    def fake_run(command, **kwargs):
        launched.append("helper")
        return subprocess.CompletedProcess(list(command), 0, stdout='{"stopped": false}\n', stderr="")

    monkeypatch.setattr(designer, "run_generated_entry", fake_entry)
    monkeypatch.setattr(designer, "run_node_driver", fake_node)
    monkeypatch.setattr(designer, "run_frozen_macos_package", lambda *args, **kwargs: launched.append("macos"))
    monkeypatch.setattr(designer.subprocess, "run", fake_run)
    readiness = designer.verify(root, _quiet_record, quiet=True)
    assert readiness["launch_performed"] is True, _failed_labels(readiness)
    failed = _failed_labels(readiness)
    for label in ("冻结 manifest 存在且可解析", "冻结包哈希清单格式/覆盖/逐字节一致",
                  "冻结包成员扫描（秘密/会话数据/平台必需成员）通过",
                  "冻结配置绑定本实例/研究/发行/模式",
                  "冻结包与当前构建/源码记录一致（先核对绑定再启动）",
                  "生成的每个引用都指向真实文件",
                  "生成的双击入口引用真实解释器与脚本（可含空格/单引号路径）",
                  "文档内的每个路径都解析到真实目标"):
        assert label not in failed, (label, failed)
    assert "真实执行生成的 START-HERE.command：启动本实例并打开正确地址" in failed, \
        "the launch phase must be reached (with the sentinel refusing), not skipped"
    assert launched, "a green preflight must really reach the launch phase"


def test_verify_refuses_a_tampered_hash_before_any_launch(tmp_path, monkeypatch):
    root, freeze, manifest = _verifiable_tree(tmp_path, monkeypatch)
    calls = _launch_sentinels(monkeypatch)
    lines = (freeze / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    lines[0] = "0" * 64 + "  " + lines[0].split("  ", 1)[1]
    (freeze / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    readiness = designer.verify(root, _quiet_record, quiet=True)
    assert readiness["status"] == "NOT_READY" and readiness["checks_failed"] > 0
    assert any("逐字节一致" in label for label in _failed_labels(readiness))
    assert readiness["launch_performed"] is False and calls == []
    assert json.loads((root / "evidence" / "readiness.json").read_text(encoding="utf-8"))["status"] == "NOT_READY"


def test_verify_refuses_a_missing_manifest_before_any_launch(tmp_path, monkeypatch):
    root, freeze, manifest = _verifiable_tree(tmp_path, monkeypatch)
    calls = _launch_sentinels(monkeypatch)
    (freeze / "manifest.json").unlink()
    readiness = designer.verify(root, _quiet_record, quiet=True)
    assert readiness["status"] == "NOT_READY"
    assert "冻结 manifest 存在且可解析" in _failed_labels(readiness)
    assert readiness["launch_performed"] is False and calls == []


def test_verify_refuses_a_package_without_its_config_member(tmp_path, monkeypatch):
    root, freeze, manifest = _verifiable_tree(tmp_path, monkeypatch)
    calls = _launch_sentinels(monkeypatch)
    name = "packages/windows-x64-study.zip"
    _rewrite_package(freeze, manifest, name, {
        "artifact_manifest.json": json.dumps({"platform": "windows_x64", "config_member": "connection.json",
                                              "program_sha256": manifest["build"]["archives"]["windows-x64"]["sha256"]}),
        "LICENSE": "GEP license",
        "THIRD_PARTY_NOTICES.txt": "notices"})
    readiness = designer.verify(root, _quiet_record, quiet=True)
    assert readiness["status"] == "NOT_READY"
    failed = _failed_labels(readiness)
    assert "冻结配置绑定本实例/研究/发行/模式" in failed
    assert "冻结包成员扫描（秘密/会话数据/平台必需成员）通过" in failed
    assert readiness["launch_performed"] is False and calls == []


def test_verify_refuses_a_stale_source_binding_before_any_launch(tmp_path, monkeypatch):
    root, freeze, manifest = _verifiable_tree(tmp_path, monkeypatch)
    calls = _launch_sentinels(monkeypatch)
    manifest["build"] = {**manifest["build"], "program_source_digest": "0" * 64}
    (freeze / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    readiness = designer.verify(root, _quiet_record, quiet=True)
    assert readiness["status"] == "NOT_READY"
    assert "冻结包与当前构建/源码记录一致（先核对绑定再启动）" in _failed_labels(readiness)
    assert readiness["launch_performed"] is False and calls == []


def test_verify_reports_corrupted_json_as_not_ready_without_launching(tmp_path, monkeypatch):
    root, freeze, manifest = _verifiable_tree(tmp_path, monkeypatch)
    calls = _launch_sentinels(monkeypatch)
    (freeze / "manifest.json").write_text("{not valid json", encoding="utf-8")
    readiness = designer.verify(root, _quiet_record, quiet=True)
    assert readiness["status"] == "NOT_READY"
    assert "冻结 manifest 存在且可解析" in _failed_labels(readiness)
    assert readiness["launch_performed"] is False and calls == []


def test_verify_catches_a_package_parse_error_before_any_launch(tmp_path, monkeypatch):
    """A corrupted package member is a NOT_READY gate failure, never a crash/launch."""
    root, freeze, manifest = _verifiable_tree(tmp_path, monkeypatch)
    calls = _launch_sentinels(monkeypatch)
    name = "packages/macos-arm64-study.zip"
    _rewrite_package(freeze, manifest, name, {
        "artifact_manifest.json": json.dumps({"platform": "macos_arm64", "config_member": "connection.json",
                                              "program_sha256": manifest["build"]["archives"]["macos-arm64"]["sha256"]}),
        "connection.json": "{corrupted json",
        "LICENSE": "GEP license",
        "THIRD_PARTY_NOTICES.txt": "notices"})
    readiness = designer.verify(root, _quiet_record, quiet=True)
    assert readiness["status"] == "NOT_READY"
    assert "启动前预检在完成前失败（不启动任何服务/浏览器/程序）" in _failed_labels(readiness)
    assert readiness["launch_performed"] is False and calls == []
