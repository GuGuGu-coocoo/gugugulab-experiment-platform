"""Godot component checks for the generic, optional session convenience layer."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_generic_session_queue_retry_finish_and_data_recovery(tmp_path):
    godot = shutil.which("godot")
    if not godot:
        pytest.skip("Godot 4.7.2 is required")
    project = tmp_path / "ordinary_task"
    addons = project / "addons/gec"
    addons.mkdir(parents=True)
    for name in ("shell.gd", "native_backend.gd", "web_backend.gd"):
        shutil.copy2(ROOT / "examples/synthetic_experiment/addons/gec" / name, addons / name)
    shutil.copy2(ROOT / "examples/synthetic_experiment/data/experiment_data.gd", addons / "experiment_data.gd")
    shutil.copy2(ROOT / "packages/gec_godot/session.gd", addons / "session.gd")
    (project / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Generic session harness"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
    result = subprocess.run(
        [godot, "--headless", "--log-file", str(tmp_path / "generic_session.log"), "--path", str(project),
         "--script", str(ROOT / "tests/godot/test_gec_session.gd")],
        capture_output=True, text=True, timeout=40,
    )
    transcript = result.stdout + result.stderr
    assert result.returncode == 0, transcript
    assert "SCRIPT ERROR" not in transcript, transcript
    match = re.search(r"GEC_SESSION_RESULT (\{[^\n]+\})", transcript)
    assert match, transcript
    report = json.loads(match.group(1))
    assert report["checks"] == 54
    assert report["failures"] == 0


def test_session_source_has_no_experiment_binding():
    source = (ROOT / "packages/gec_godot/session.gd").read_text()
    for binding in ("Moss", "moss", "duel", "Game", "synthetic-1", "telemetry_event", "run_finished"):
        assert binding not in source
