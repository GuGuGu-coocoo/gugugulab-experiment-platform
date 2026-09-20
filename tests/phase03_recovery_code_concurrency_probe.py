"""Threaded single-consume probe for six-digit recovery codes (P0304).

Not collected by the default suite (the filename does not match ``test_*.py``);
``tests/test_phase03_recovery_codes.py`` runs this module in a subprocess with
``GEP_TEST_DB_FILE`` pointing at a temporary file, so concurrent SQLite writers
honor the configured busy timeout instead of the in-memory shared-cache table
lock used by the rest of the suite.
"""
import threading

import pytest
from django.db import connections

from core.models import Audit, Grant, RecoveryCode
from core.services import RECOVERY_CODE_CAPABILITY, issue_recovery_code, redeem_recovery_code


@pytest.mark.django_db(transaction=True)
def test_concurrent_redemption_consumes_the_code_once(setup):
    """Two devices redeem the same six digits at once: the row lock and the
    checked consume admit exactly one caller and never a replayed credential."""
    session = setup['session']
    for action in ('study.view', 'session.recover'):
        Grant.objects.create(user=setup['owner'], study=setup['study'], action=action, delegable=True)
    issued = issue_recovery_code(setup['owner'], session.id)
    data = {'capability': RECOVERY_CODE_CAPABILITY, 'code': issued['code'], 'proof': setup['request']['proof'],
            'instance_id': str(setup['instance'].instance_id), 'study_id': str(setup['study'].id),
            'release_id': str(setup['release'].id), 'build_id': str(setup['release'].build_id)}
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(client_key):
        connections.close_all()
        barrier.wait()
        try:
            outcomes.append(('ok', redeem_recovery_code(dict(data), client_key)))
        except Exception as error:  # noqa: BLE001 - recorded as evidence, asserted below
            outcomes.append(('error', repr(error)))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(f'synthetic-client-{index}',)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    print('consume outcomes:', outcomes)
    assert sorted(status for status, _ in outcomes) == ['error', 'ok'], outcomes
    denied = [detail for status, detail in outcomes if status == 'error']
    assert len(denied) == 1 and 'recovery_denied' in denied[0], outcomes
    assert RecoveryCode.objects.get(session=session).consumed is True
    assert Audit.objects.filter(action='recovery.code_redeemed').count() == 1
