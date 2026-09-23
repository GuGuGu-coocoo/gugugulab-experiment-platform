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
    # The container/Compose initializer generates its Owner password through the
    # one researcher-password module: at least 24 characters with all four
    # classes present (U07), never a bare token_urlsafe() sample.
    from core import researcher_passwords
    generated = credentials['password']
    assert len(generated) >= researcher_passwords.TEMPORARY_LENGTH
    assert researcher_passwords.password_problem(generated) is None
    for characters in researcher_passwords.CLASSES:
        assert any(character in characters for character in generated)
    assert (root / 'secret').stat().st_mode & 0o777 == 0o600
    assert not (root / 'initializing').exists()
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    second = subprocess.run(command, env=env, capture_output=True, text=True)
    assert second.returncode != 0 and 'nonempty volume' in second.stderr
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    with sqlite3.connect(root / 'gep.sqlite3') as db:
        assert db.execute('select count(*) from core_instance').fetchone()[0] == 1
        assert db.execute('select count(*) from auth_user').fetchone()[0] == 1
        # A new isolated volume is explicitly v2 with the Owner's stable
        # principal and a v2 policy profile (never a silent v1 default).
        assert db.execute('select authorization_version from core_instance').fetchone()[0] == 2
        assert db.execute('select count(*) from core_principal').fetchone()[0] == 1
        assert db.execute('select policy_version, role from core_accountprofile').fetchone() == (2, 'user')


def test_incomplete_initialization_cannot_serve(tmp_path, monkeypatch):
    from gep.runtime import configure
    import pytest
    for name, value in [('instance', 'expected'), ('secret', 'synthetic-secret'), ('gep.sqlite3', ''), ('initializing', '')]:
        (tmp_path / name).write_text(value)
    monkeypatch.setenv('GEP_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('GEP_EXPECTED_INSTANCE', 'expected')
    with pytest.raises(RuntimeError, match='initialized volume'):
        configure()
