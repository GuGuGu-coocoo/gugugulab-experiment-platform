"""Isolated threaded probe for concurrent current-release switches (P0303).

This module is not collected by the default suite (its filename does not match
``test_*.py``); ``tests/test_phase03_releases.py`` runs it explicitly in a
subprocess with ``GEP_TEST_DB_FILE`` pointing at a temporary file so concurrent
SQLite writers honor the configured busy timeout.
"""
import threading
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connections

from core import publication
from core.models import Audit, Build, Grant, Instance, Release, Study

OWNER_PASSWORD = 'synthetic-test-password'


@pytest.mark.django_db(transaction=True)
def test_concurrent_current_release_switches_compare_revision():
    """Two switches submitted for the same publication revision: exactly one
    applies; the other is refused as stale and leaves the winner untouched."""
    owner = get_user_model().objects.create_user('concurrent_release_owner', password=OWNER_PASSWORD)
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='Synthetic concurrent release')
    releases = []
    for version in ('v1', 'v2'):
        build = Build.objects.create(study=study, descriptor={'platform': 'godot_web', 'version': version},
                                     digest=uuid.uuid4().hex + uuid.uuid4().hex, package_path=version + '.zip')
        releases.append(Release.objects.create(study=study, build=build, config={'purpose': 'synthetic'}, approved=True))
    for action in ('study.view', 'study.configure', 'recruitment.manage'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)

    revision = str(study.revision)
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(release_id):
        connections.close_all()
        actor = get_user_model().objects.get(pk=owner.pk)
        target = Study.objects.get(pk=study.pk)
        barrier.wait()
        try:
            outcomes.append(('ok', publication.select_current_release(actor, target, revision, str(release_id))))
        except Exception as error:  # noqa: BLE001 - recorded as evidence, asserted below
            outcomes.append(('error', repr(error)))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(release.id,)) for release in releases]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(status for status, _ in outcomes) == ['error', 'ok'], outcomes
    refused = [detail for status, detail in outcomes if status == 'error'][0]
    assert 'revision_conflict' in refused
    winner = [result['after']['current_release'] for status, result in outcomes if status == 'ok'][0]
    stored = Study.objects.get(pk=study.pk)
    assert str(stored.current_release_id) == winner
    assert stored.revision == 1
    assert Audit.objects.filter(action=publication.RELEASE_CHANGED).count() == 1
