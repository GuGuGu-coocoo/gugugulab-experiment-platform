import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_initialize_empty_volume_and_refuse_overwrite(tmp_path):
    root = tmp_path / 'synthetic-volume'
    env = {**os.environ, 'PYTHONPATH': str(Path('server').resolve())}
    command = [sys.executable, '-m', 'gep.initialize', '--data-dir', str(root)]
    first = subprocess.run(command, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    credentials = json.loads((root / 'dev_credentials.json').read_text())
    assert credentials['password'] not in first.stdout + first.stderr
    assert (root / 'secret').stat().st_mode & 0o777 == 0o600
    assert not (root / 'initializing').exists()
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    second = subprocess.run(command, env=env, capture_output=True, text=True)
    assert second.returncode != 0 and 'nonempty volume' in second.stderr
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    with sqlite3.connect(root / 'gep.sqlite3') as db:
        assert db.execute('select count(*) from core_instance').fetchone()[0] == 1
        assert db.execute('select count(*) from auth_user').fetchone()[0] == 1


def test_incomplete_initialization_cannot_serve(tmp_path, monkeypatch):
    from gep.runtime import configure
    import pytest
    for name, value in [('instance', 'expected'), ('secret', 'synthetic-secret'), ('gep.sqlite3', ''), ('initializing', '')]:
        (tmp_path / name).write_text(value)
    monkeypatch.setenv('GEP_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('GEP_EXPECTED_INSTANCE', 'expected')
    with pytest.raises(RuntimeError, match='initialized volume'):
        configure()
