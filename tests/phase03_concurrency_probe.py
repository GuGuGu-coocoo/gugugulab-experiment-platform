"""Isolated threaded-commit probe for the preview-bound permission matrix.

This module is not collected by the default suite (its filename does not match
``test_*.py``); ``tests/test_phase03_permissions.py`` runs it explicitly in a
subprocess with ``GEP_TEST_DB_FILE`` pointing at a temporary file, so concurrent
SQLite writers honor the configured busy timeout instead of failing fast on the
in-memory shared-cache table lock used by the rest of the suite.
"""
import threading
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connections

from core import permissions
from core.models import Audit, Grant, Instance, PermissionPreview, Study

OWNER_PASSWORD = 'synthetic-test-password'
NEW_PASSWORD = 'synthetic-new-password-2026'


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmations_apply_exactly_once():
    """Two threads confirm the same preview at once: the locked, single-use
    identity applies exactly one mutation and the other request replays it."""
    owner = get_user_model().objects.create_user('synthetic_concurrent_owner', password=OWNER_PASSWORD)
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='Synthetic concurrent study')
    target = get_user_model().objects.create_user('synthetic_concurrent_target', password=NEW_PASSWORD)
    Grant.objects.create(user=target, study=study, action='study.view', delegable=False)
    preview = permissions.preview_matrix(owner, {'user_id': str(target.pk), 'study_id': str(study.pk),
                                                 'visibility': '1', 'action:data.export_raw': '1'})
    barrier = threading.Barrier(2)
    outcomes = []

    def worker():
        connections.close_all()
        actor = get_user_model().objects.get(pk=owner.pk)
        barrier.wait()
        try:
            outcomes.append(('ok', permissions.commit_matrix(actor, OWNER_PASSWORD, preview.id)))
        except Exception as error:  # noqa: BLE001 - recorded as evidence, asserted below
            outcomes.append(('error', repr(error)))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(status for status, _ in outcomes) == ['ok', 'ok'], outcomes
    assert Audit.objects.filter(action='permission.matrix_changed').count() == 1
    assert PermissionPreview.objects.get(pk=preview.id).consumed is True
    assert sorted(Grant.objects.filter(user=target, study=study).values_list('action', flat=True)) == ['data.export_raw', 'study.view']
