import os
import pytest
from gep.runtime import configure

def test_missing_wrong_volume_fail_closed(tmp_path,monkeypatch):
    monkeypatch.setenv('GEP_DATA_DIR',str(tmp_path));monkeypatch.setenv('GEP_EXPECTED_INSTANCE','expected')
    with pytest.raises(RuntimeError,match='initialized volume'):configure()
    for name,value in [('instance','wrong'),('secret','synthetic-secret'),('gep.sqlite3','')]:
        (tmp_path/name).write_text(value)
    with pytest.raises(RuntimeError,match='Wrong volume'):configure()
