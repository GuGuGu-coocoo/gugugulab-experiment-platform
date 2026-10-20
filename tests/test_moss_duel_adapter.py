"""Real Godot component fault checks for the optional Moss Duel adapter."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_adapter_commit_retry_finish_and_data_only(tmp_path):
    godot = shutil.which("godot")
    if not godot:
        pytest.skip("Godot 4.7.2 is required for the adapter harness")
    result = subprocess.run(
        [godot, "--headless", "--log-file", str(tmp_path / "adapter.log"), "--path", str(ROOT / "tests/fixtures/synthetic_experiment"),
         "--script", str(ROOT / "tests/godot/test_moss_duel_adapter.gd")],
        capture_output=True, text=True, timeout=40,
    )
    transcript = result.stdout + result.stderr
    assert result.returncode == 0, transcript
    assert "SCRIPT ERROR" not in transcript, transcript
    match = re.search(r"MOSS_DUEL_ADAPTER_RESULT (\{[^\n]+\})", transcript)
    assert match, transcript
    report = json.loads(match.group(1))
    assert report["checks"] == 18
    assert report["failures"] == 0


def test_native_setup_errors_are_english_without_changing_state_or_config(tmp_path):
    godot = shutil.which("godot")
    if not godot:
        pytest.skip("Godot 4.7.2 is required for the native language harness")
    result = subprocess.run(
        [godot, "--headless", "--log-file", str(tmp_path / "language.log"), "--path", str(ROOT / "tests/fixtures/synthetic_experiment"),
         "--script", str(ROOT / "tests/godot/test_moss_duel_native_language.gd")],
        capture_output=True, text=True, timeout=40,
    )
    transcript = result.stdout + result.stderr
    assert result.returncode == 0, transcript
    assert "SCRIPT ERROR" not in transcript, transcript
    match = re.search(r"MOSS_DUEL_LANGUAGE_RESULT (\{[^\n]+\})", transcript)
    assert match, transcript
    report = json.loads(match.group(1))
    assert report["checks"] == 24
    assert report["failures"] == 0
