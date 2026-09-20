"""03F acceptance orchestrator contract tests (P0308).

These tests prove the orchestrator's own behavior with real subprocesses and
injected failures, never with a mock pass:

* a missing or skipped subcase is ``NOT_RUN`` and a failed subcase is ``FAIL``;
* the overall verdict preserves ``FAIL`` (a real failing subcommand, a timeout,
  a step that throws) and only uses ``BLOCKED`` when the *only* missing evidence
  is the external Windows device gate;
* a command that prints a PASS summary but exits non-zero is never trusted;
* the Windows runtime gate uses exactly the explicitly selected run+prep pair,
  refuses a partial or mismatched pair, and never substitutes the fresh local
  preparation;
* an existing/protected/foreign/symlinked evidence root is refused before any
  write and the previous attempt keeps every file's content and path;
* the protected-data digest detects a same-length content mutation;
* unchanged valid evidence still evaluates to PASS.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import phase03_acceptance as acceptance  # noqa: E402


def passing_evidence(include_windows=False):
    """Synthesize an evidence index with every declared selector resolved."""
    evidence = {"pytest": {"tests": {}}, "browser": {"tests": {}}, "shell": {"checks": {}},
                "package": {"checks": {}}, "windows_preparation": {"checks": {}},
                "windows_runtime": {"items": {}}}
    for _name, (_description, subcases) in acceptance.SUBREQUIREMENTS.items():
        for _subcase, selectors in subcases:
            for selector in selectors:
                source, _, key = selector.partition(":")
                if source == "pytest":
                    evidence["pytest"]["tests"][key] = "passed"
                elif source == "browser":
                    evidence["browser"]["tests"][key] = "passed"
                elif source == "shell":
                    evidence["shell"]["checks"][key] = acceptance.STATUS_PASS
                elif source == "package":
                    evidence["package"]["checks"][key] = acceptance.STATUS_PASS
                elif source == "windows_preparation":
                    evidence["windows_preparation"]["checks"][key] = acceptance.STATUS_PASS
                elif source == "windows_runtime":
                    evidence["windows_runtime"]["items"][key] = (
                        acceptance.STATUS_PASS if include_windows else acceptance.STATUS_NOT_RUN)
    return evidence


def all_steps(status=acceptance.STATUS_PASS, windows=acceptance.STATUS_NOT_RUN):
    return {"integrity": status, "pytest": status, "shell": status, "package": status,
            "browser": status, "windows_preparation": status, "windows_runtime": windows}


# ------------------------------------------------------------------- matrix
def test_matrix_marks_a_missing_selector_as_not_run():
    evidence = passing_evidence()
    evidence["shell"]["checks"].pop("native second writer is refused")
    matrix = acceptance.requirement_matrix(evidence)
    assert matrix["T24"]["status"] == acceptance.STATUS_NOT_RUN
    subcase = next(entry for entry in matrix["T24"]["subcases"]
                   if entry["name"] == "cleanup retention and shared-device lock")
    missing = [item for item in subcase["evidence"]
               if item["selector"] == "shell:native second writer is refused"]
    assert missing and missing[0]["status"] == acceptance.STATUS_NOT_RUN
    assert matrix["T23"]["status"] == acceptance.STATUS_PASS


def test_matrix_marks_a_failed_selector_as_fail_not_not_run():
    evidence = passing_evidence()
    evidence["pytest"]["tests"]["tests/test_phase03_accounts.py::test_admin_cannot_manage_owner_or_change_roles"] = "failed"
    matrix = acceptance.requirement_matrix(evidence)
    assert matrix["T13"]["status"] == acceptance.STATUS_FAIL
    assert matrix["T26"]["status"] == acceptance.STATUS_PASS


def test_matrix_marks_a_skipped_pytest_node_as_not_run_never_pass():
    evidence = passing_evidence()
    node = "tests/test_phase03_ui_browser.py::test_chrome_modules_languages_themes_keyboard_and_narrow_layout"
    evidence["pytest"]["tests"][node] = "skipped"
    matrix = acceptance.requirement_matrix(evidence)
    assert matrix["T30"]["status"] == acceptance.STATUS_NOT_RUN


def test_matrix_matches_a_parametrized_pytest_selector_by_all_its_nodes():
    evidence = passing_evidence()
    selector = "pytest:tests/test_phase03_packages.py::test_native_program_rejects_hostile_archive"
    source, _, key = selector.partition(":")
    evidence["pytest"]["tests"][key + "[path_traversal-hostile_archive]"] = "passed"
    evidence["pytest"]["tests"][key + "[absolute_path-hostile_archive]"] = "failed"
    status, _detail = acceptance._selector_status("pytest", key, evidence)
    assert status == acceptance.STATUS_FAIL
    assert acceptance.requirement_matrix(evidence)["T14"]["status"] == acceptance.STATUS_FAIL


# ------------------------------------------------------------------ overall
def test_overall_passes_only_when_every_subcase_and_step_passes():
    matrix = acceptance.requirement_matrix(passing_evidence(include_windows=True))
    overall, _reason = acceptance.overall_status(matrix, all_steps(windows=acceptance.STATUS_PASS))
    assert overall == acceptance.STATUS_PASS


def test_overall_never_passes_when_a_step_is_not_run_even_with_a_full_matrix():
    """An all-PASS matrix with a NOT_RUN required step must never be PASS."""
    matrix = acceptance.requirement_matrix(passing_evidence(include_windows=True))
    steps = all_steps(windows=acceptance.STATUS_PASS)
    steps["package"] = acceptance.STATUS_NOT_RUN
    overall, reason = acceptance.overall_status(matrix, steps)
    assert overall != acceptance.STATUS_PASS
    assert overall == acceptance.STATUS_NOT_RUN
    assert "package" in reason


def test_overall_fails_on_a_step_that_raised_and_an_unknown_step_status():
    matrix = acceptance.requirement_matrix(passing_evidence(include_windows=True))
    steps = all_steps(windows=acceptance.STATUS_PASS)
    overall, reason = acceptance.overall_status(matrix, steps, {"browser": "RuntimeError('boom')"})
    assert overall == acceptance.STATUS_FAIL
    assert "browser" in reason
    steps = all_steps(windows=acceptance.STATUS_PASS)
    steps["shell"] = "SOMETHING_ELSE"
    overall, reason = acceptance.overall_status(matrix, steps)
    assert overall == acceptance.STATUS_FAIL
    assert "shell" in reason


def test_overall_is_not_run_when_t20_also_misses_a_local_subcase():
    """A requirement missing the local subcase too is never 'external only'."""
    evidence = passing_evidence()
    selectors = dict(acceptance.SUBREQUIREMENTS["T20"][1])[
        "one real OS native package without a runner (macOS, fresh run)"]
    for selector in selectors:
        source, _, key = selector.partition(":")
        if source == "shell":
            evidence["shell"]["checks"].pop(key, None)
        elif source == "package":
            evidence["package"]["checks"].pop(key, None)
    matrix = acceptance.requirement_matrix(evidence)
    t20 = {subcase["name"]: subcase["status"] for subcase in matrix["T20"]["subcases"]}
    assert t20["one real OS native package without a runner (macOS, fresh run)"] == acceptance.STATUS_NOT_RUN
    assert t20["real Windows x64 native runtime (external device required)"] == acceptance.STATUS_NOT_RUN
    overall, reason = acceptance.overall_status(matrix, all_steps(windows=acceptance.STATUS_NOT_RUN))
    assert overall == acceptance.STATUS_NOT_RUN
    assert "T20" in reason


def test_unknown_selector_and_check_statuses_never_pass():
    evidence = passing_evidence()
    node = "tests/test_phase03_queries.py::test_dashboard_cards_stable_navigation_and_module_pages"
    evidence["pytest"]["tests"][node] = "unexpected-outcome"
    status, detail = acceptance._selector_status("pytest", node, evidence)
    assert status == acceptance.STATUS_NOT_RUN and "unknown" in detail
    evidence = passing_evidence()
    spec = "web_e2e.spec.js::real Godot Web input through IndexedDB API database and authorized export"
    evidence["browser"]["tests"][spec] = "mystery"
    status, detail = acceptance._selector_status("browser", spec, evidence)
    assert status == acceptance.STATUS_NOT_RUN and "unknown" in detail
    status, _detail = acceptance._check_status({"some label": "WEIRD"}, "some label")
    assert status == acceptance.STATUS_NOT_RUN
    status, _detail = acceptance._check_status({"label": acceptance.STATUS_PASS}, "label")
    assert status == acceptance.STATUS_PASS
    status, _detail = acceptance._check_status({"label": acceptance.STATUS_FAIL}, "label")
    assert status == acceptance.STATUS_FAIL
    evidence = passing_evidence()
    evidence["windows_runtime"]["items"]["WN01"] = "maybe"
    status, _detail = acceptance._selector_status("windows_runtime", "WN01", evidence)
    assert status == acceptance.STATUS_NOT_RUN


def test_overall_is_blocked_when_only_the_external_windows_gate_is_missing():
    matrix = acceptance.requirement_matrix(passing_evidence(include_windows=False))
    overall, reason = acceptance.overall_status(matrix, all_steps(windows=acceptance.STATUS_NOT_RUN))
    assert overall == acceptance.STATUS_BLOCKED
    assert "Windows" in reason


def test_overall_is_not_run_when_other_required_evidence_is_missing():
    evidence = passing_evidence()
    evidence["shell"]["checks"].pop("native second writer is refused")
    matrix = acceptance.requirement_matrix(evidence)
    overall, reason = acceptance.overall_status(matrix, all_steps(windows=acceptance.STATUS_NOT_RUN))
    assert overall == acceptance.STATUS_NOT_RUN
    assert "T24" in reason


def test_overall_fails_when_a_step_failed_even_with_all_subcases_present():
    matrix = acceptance.requirement_matrix(passing_evidence(include_windows=True))
    overall, reason = acceptance.overall_status(matrix, all_steps(status=acceptance.STATUS_FAIL))
    assert overall == acceptance.STATUS_FAIL
    assert "steps failed" in reason


def test_overall_fails_when_the_windows_runtime_step_is_selected_but_invalid():
    matrix = acceptance.requirement_matrix(passing_evidence(include_windows=False))
    steps = all_steps(windows=acceptance.STATUS_FAIL)
    overall, _reason = acceptance.overall_status(matrix, steps)
    assert overall == acceptance.STATUS_FAIL


# --------------------------------------------------------------- runner seam
def test_run_step_records_a_real_failing_command_and_a_real_timeout(tmp_path):
    runner = acceptance.Runner(tmp_path, quiet=True)
    record = runner.run_step("failing", [sys.executable, "-c", "import sys; sys.exit(3)"], timeout=30)
    assert record["exit"] == 3 and record["timed_out"] is False
    assert runner.status_of("failing") == acceptance.STATUS_FAIL
    record = runner.run_step("slow", [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert record["timed_out"] is True and record["exit"] is None
    assert runner.status_of("slow") == acceptance.STATUS_FAIL
    record = runner.run_step("missing", [str(tmp_path / "no-such-binary")], timeout=10)
    assert record["exit"] is None and record["error"]
    assert runner.status_of("missing") == acceptance.STATUS_FAIL


def test_forged_pass_summary_is_never_trusted(tmp_path, monkeypatch):
    """A step that prints a PASS line but exits non-zero must stay FAIL."""
    runner = acceptance.Runner(tmp_path, quiet=True)
    evidence_root = tmp_path / "shell" / "run"
    evidence_root.mkdir(parents=True)
    (evidence_root / "evidence.json").write_text(json.dumps(
        {"checks": [{"ok": True, "label": "native second writer is refused"}]}))

    class FakeRunner(acceptance.Runner):
        def run_step(self, name, command, timeout, env=None, cwd=acceptance.ROOT):
            record = super().run_step(name, command, timeout, env=env, cwd=cwd)
            record["tail"] = "PHASE03_SHELL_VERIFY_OK forged summary\n"
            return record

    forged = FakeRunner(tmp_path, quiet=True)
    monkeypatch.setattr(acceptance, "TOOLS", tmp_path / "tools")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "phase03_verify_shell.py").write_text(
        "import sys; print('PHASE03_SHELL_VERIFY_OK'); sys.exit(1)\n", encoding="utf-8")
    result = acceptance.verifier_step(forged, "shell", "phase03_verify_shell.py")
    assert result["status"] == acceptance.STATUS_FAIL
    assert result["record"]["exit"] == 1
    assert "PHASE03_SHELL_VERIFY_OK" in result["record"]["tail"]


# ------------------------------------------------------------------ windows
def test_windows_selection_never_discovers_a_directory():
    class Args:
        windows_run = None
        windows_prep = None

    assert acceptance.windows_selection(Args(), environ={}) == (None, None)
    assert acceptance.windows_selection(Args(), environ={"GEP_WINDOWS_RUN_DIR": "r"}) == ("r", None)
    args = Args()
    args.windows_run, args.windows_prep = "run", "prep"
    assert acceptance.windows_selection(args, environ={"GEP_WINDOWS_RUN_DIR": "other"}) == ("run", "prep")


def test_windows_runtime_step_refuses_a_partial_pair(tmp_path):
    result = acceptance.windows_runtime_step(None, str(tmp_path / "run"), None,
                                             validator=lambda *a: pytest.fail("validator must not run"))
    assert result["status"] == acceptance.STATUS_FAIL
    assert "both" in result["detail"]
    assert set(result["items"].values()) == {acceptance.STATUS_NOT_RUN}


def test_windows_runtime_step_uses_exactly_the_selected_pair(tmp_path):
    run_dir, prep_dir = tmp_path / "old-run", tmp_path / "old-prep"
    run_dir.mkdir(), prep_dir.mkdir()
    seen = {}

    def validator(run, prep):
        seen["pair"] = (Path(run), Path(prep))
        return {"verdict": "RUNTIME_PASS", "detail": "ok",
                "cases": {name: acceptance.STATUS_PASS for name in acceptance.WINDOWS_ITEMS}}

    result = acceptance.windows_runtime_step(None, str(run_dir), str(prep_dir), validator=validator)
    assert seen["pair"] == (run_dir.resolve(), prep_dir.resolve())
    assert result["status"] == acceptance.STATUS_PASS
    assert set(result["items"].values()) == {acceptance.STATUS_PASS}


def test_windows_runtime_step_invalid_evidence_is_fail(tmp_path):
    run_dir, prep_dir = tmp_path / "run", tmp_path / "prep"
    run_dir.mkdir(), prep_dir.mkdir()
    result = acceptance.windows_runtime_step(
        None, str(run_dir), str(prep_dir),
        validator=lambda run, prep: {"verdict": "EVIDENCE_INVALID", "detail": "stale",
                                     "problems": ["stale evidence"], "cases": {}})
    assert result["status"] == acceptance.STATUS_FAIL
    assert result["verdict"] == "EVIDENCE_INVALID"


def test_orchestrate_never_substitutes_the_fresh_preparation(tmp_path, monkeypatch):
    """The local preparation step and the selected runtime pair stay separate."""
    fresh = tmp_path / "fresh-prep"
    fresh.mkdir()
    selected = tmp_path / "selected-prep"
    selected.mkdir()
    recorded = {}
    monkeypatch.setattr(acceptance, "RUN_ROOT", tmp_path)

    monkeypatch.setattr(acceptance, "integrity_step", lambda runner, baseline: {"checks": [], "failures": []})
    monkeypatch.setattr(acceptance, "pytest_step", lambda runner: {"status": acceptance.STATUS_PASS, "tests": {}})
    monkeypatch.setattr(acceptance, "verifier_step",
                        lambda runner, name, script: {"status": acceptance.STATUS_PASS, "checks": {}})
    monkeypatch.setattr(acceptance, "browser_step",
                        lambda runner: {"status": acceptance.STATUS_PASS, "tests": {}, "specs": []})
    monkeypatch.setattr(acceptance, "windows_preparation_step",
                        lambda runner: {"status": acceptance.STATUS_PASS, "checks": {}})

    def fake_runtime(runner, run_dir, prep_dir, validator=None):
        recorded["pair"] = (run_dir, prep_dir)
        return {"status": acceptance.STATUS_NOT_RUN, "items": {}, "verdict": None, "detail": None}

    monkeypatch.setattr(acceptance, "windows_runtime_step", fake_runtime)
    code = acceptance.orchestrate(tmp_path / "evidence", quiet=True,
                                  windows_run=str(tmp_path / "selected-run"), windows_prep=str(selected))
    assert code != 0
    assert recorded["pair"] == (str(tmp_path / "selected-run"), str(selected))
    assert str(fresh) not in json.dumps(recorded)


# ------------------------------------------------------ refusal of old roots
def _tree_digest(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(root).rglob("*")) if path.is_file()}


def test_guard_refuses_protected_and_foreign_roots(tmp_path, monkeypatch):
    """Protected roots, foreign paths and symlinked components are refused."""
    monkeypatch.setattr(acceptance, "RUN_ROOT", tmp_path / "runs")
    (tmp_path / "runs").mkdir()
    for reserved in (acceptance.ROOT, acceptance.PROTECTED_DB, acceptance.PROTECTED_VOLUME,
                     acceptance.PHASE_ROOT, acceptance.RUN_ROOT):
        with pytest.raises(acceptance.EvidenceRootError, match="reserved root"):
            acceptance.guard_evidence_root(reserved)
    with pytest.raises(acceptance.EvidenceRootError, match="dedicated run root"):
        acceptance.guard_evidence_root(tmp_path / "elsewhere")
    (tmp_path / "runs" / "real").mkdir()
    (tmp_path / "runs" / "link").symlink_to(tmp_path / "runs" / "real")
    with pytest.raises(acceptance.EvidenceRootError, match="symlinked"):
        acceptance.guard_evidence_root(tmp_path / "runs" / "link")
    with pytest.raises(acceptance.EvidenceRootError, match="symlinked path component"):
        acceptance.guard_evidence_root(tmp_path / "runs" / "link" / "child")
    assert not (tmp_path / "elsewhere").exists(), "a refused root must never be created"


def test_guard_refuses_a_windows_preparation_or_evidence_directory(tmp_path, monkeypatch):
    """A preparation/evidence directory is never accepted as an acceptance root."""
    monkeypatch.setattr(acceptance, "RUN_ROOT", tmp_path)
    prep = tmp_path / "selected-prep"
    prep.mkdir()
    (prep / "readiness.json").write_text("{}", encoding="utf-8")
    (prep / "kit").mkdir()
    with pytest.raises(acceptance.EvidenceRootError, match="non-dedicated"):
        acceptance.guard_evidence_root(prep)
    (prep / "readiness.json").unlink()
    (prep / "kit").rmdir()
    (prep / "evidence.json").write_text("{}", encoding="utf-8")
    with pytest.raises(acceptance.EvidenceRootError, match="non-dedicated"):
        acceptance.guard_evidence_root(prep)


def test_existing_evidence_root_is_refused_and_kept_byte_identical(tmp_path, monkeypatch):
    """A rerun after failure keeps every file and path; it must use a new root."""
    monkeypatch.setattr(acceptance, "RUN_ROOT", tmp_path)
    root = tmp_path / "acceptance_attempt_1"
    (root / "shell" / "run" / "data").mkdir(parents=True)
    database = root / "shell" / "run" / "data" / "gep.sqlite3"
    database.write_bytes(b"attempt-1-database")
    Path(str(database) + "-wal").write_bytes(b"attempt-1-wal")
    Path(str(database) + "-shm").write_bytes(b"attempt-1-shm")
    (root / "shell" / "run" / "queue.jsonl").write_text('{"pending": 1}\n', encoding="utf-8")
    log = root / "windows_preparation" / "kit" / "prepare.log"
    log.parent.mkdir(parents=True)
    log.write_text("attempt 1 preparation log\n", encoding="utf-8")
    before = _tree_digest(root)
    with pytest.raises(acceptance.EvidenceRootError, match="new unique root"):
        acceptance.guard_evidence_root(root)
    with pytest.raises(acceptance.EvidenceRootError):
        acceptance.orchestrate(root, quiet=True)
    assert acceptance.main(["--verify", "--root", str(root)]) != 0
    assert _tree_digest(root) == before, "every previous file must keep its path and content"


def test_orchestrate_uses_a_new_root_and_never_touches_the_previous_attempt(tmp_path, monkeypatch):
    """A retry writes a fresh tree; the failed attempt stays untouched."""
    monkeypatch.setattr(acceptance, "RUN_ROOT", tmp_path)
    previous = tmp_path / "acceptance_attempt_1"
    (previous / "shell" / "run").mkdir(parents=True)
    (previous / "shell" / "run" / "gep.sqlite3").write_bytes(b"failed-attempt-db")
    before = _tree_digest(previous)
    for name in ("integrity_step", "pytest_step", "verifier_step", "browser_step",
                 "windows_preparation_step", "windows_runtime_step"):
        monkeypatch.setattr(acceptance, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(acceptance, "integrity_step", lambda runner, baseline: {"checks": [], "failures": []})
    retry = tmp_path / "acceptance_attempt_2"
    code = acceptance.orchestrate(retry, quiet=True)
    assert code != 0, "a stubbed matrix can never be PASS"
    assert (retry / "acceptance.json").is_file()
    assert _tree_digest(previous) == before
    assert not (previous / "acceptance.json").exists()


def test_orchestrate_writes_a_report_even_when_a_step_throws(tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(acceptance, "integrity_step", lambda runner, baseline: {"checks": [], "failures": []})

    def boom(*args, **kwargs):
        raise RuntimeError("injected step failure")

    for name in ("pytest_step", "verifier_step", "browser_step", "windows_preparation_step",
                 "windows_runtime_step"):
        monkeypatch.setattr(acceptance, name, boom)
    code = acceptance.orchestrate(tmp_path / "evidence", quiet=True)
    report = json.loads((tmp_path / "evidence" / "acceptance.json").read_text())
    assert code != 0
    assert report["overall"] == acceptance.STATUS_FAIL
    assert set(report["steps"].values()) >= {acceptance.STEP_ERROR}
    assert report["steps"]["pytest"] == acceptance.STEP_ERROR
    assert report["steps"]["windows_runtime"] == acceptance.STEP_ERROR, \
        "a raised windows runtime step must be an internal error, not NOT_RUN"
    assert "pytest" in report["step_errors"] and "windows_runtime" in report["step_errors"]
    assert (tmp_path / "evidence" / "ACCEPTANCE.md").is_file()


# ------------------------------------------------------------ evidence reuse
def test_unchanged_valid_evidence_stays_pass_and_changed_evidence_fails(tmp_path, monkeypatch):
    volume = tmp_path / "protected"
    volume.mkdir()
    (volume / "record.bin").write_bytes(b"same-length-payload-A")
    monkeypatch.setattr(acceptance, "PROTECTED_VOLUME", volume)
    monkeypatch.setattr(acceptance, "PROTECTED_DB", tmp_path / "missing.sqlite3")
    first = acceptance.protected_digest()
    assert acceptance.protected_digest() == first
    (volume / "record.bin").write_bytes(b"same-length-payload-B")
    assert acceptance.protected_digest() != first


def test_protected_digest_includes_wal_sidecars(tmp_path, monkeypatch):
    database = tmp_path / "gep.sqlite3"
    database.write_bytes(b"database")
    monkeypatch.setattr(acceptance, "PROTECTED_DB", database)
    monkeypatch.setattr(acceptance, "PROTECTED_VOLUME", tmp_path / "no-volume")
    without_wal = acceptance.protected_digest()
    Path(str(database) + "-wal").write_bytes(b"wal-content")
    with_wal = acceptance.protected_digest()
    assert without_wal != with_wal
