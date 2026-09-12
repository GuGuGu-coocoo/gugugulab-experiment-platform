import pytest
from django.db import connection
from core.protocol import Rejected
from core.throttle import check
from core.models import Throttle


def test_attempt_limit_survives_reconnection_without_storing_credentials(db,monkeypatch):
    monkeypatch.setattr('core.throttle.time.time',lambda:1800000000)
    label='synthetic-study:001'
    check(label,limit=2);check(label,limit=2)
    connection.close()
    with pytest.raises(Rejected,match='rate_limited') as rejected:check(label,limit=2)
    assert rejected.value.status==429
    row=Throttle.objects.get();assert row.count==3 and len(row.key)==64 and row.key!=label
    with pytest.raises(Rejected,match='rate_limited'):check(label,limit=2)
    row.refresh_from_db();assert row.count==4
