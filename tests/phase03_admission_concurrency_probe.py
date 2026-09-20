"""Isolated threaded probes: first admission crossing a switch or a close (P0303).

Not collected by the default suite (filename does not match ``test_*.py``);
``tests/test_phase03_releases.py`` runs this module explicitly in a subprocess
with ``GEP_TEST_DB_FILE`` pointing at a temporary file so concurrent writers use
a real file database with the configured busy timeout and immediate transactions.

Two probe shapes per researcher write:

* a barrier race, where either the admission or the researcher write may commit
  first; the test asserts the *serializable* outcome set, not one interleaving;
* a held-write probe, where the researcher's transaction is still open while the
  admission call starts. SQLite admits one writer, so the admission can only
  observe the committed write and must fail closed.
"""
import threading
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.db import connections, transaction

from core import publication
from core.models import Audit, Build, Grant, Instance, Participant, Release, Session, Study
from core.services import admit_request

OWNER_PASSWORD = 'synthetic-test-password'


def study_world():
    """Owner, instance, one open study and two approved package releases with R1 current."""
    owner = get_user_model().objects.create_user('concurrent_admission_owner', password=OWNER_PASSWORD)
    Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='Synthetic concurrent admission', recruitment='open')
    releases = []
    for version in ('v1', 'v2'):
        build = Build.objects.create(study=study, descriptor={'platform': 'godot_web', 'version': version},
                                     digest=uuid.uuid4().hex + uuid.uuid4().hex, package_path=version + '.zip')
        releases.append(Release.objects.create(study=study, build=build, config={'purpose': 'synthetic'}, approved=True))
    for action in ('study.view', 'study.configure', 'recruitment.manage'):
        Grant.objects.create(user=owner, study=study, action=action, delegable=True)
    publication.select_current_release(owner, study, str(study.revision), str(releases[0].id))
    return owner, study, releases


def admission_request(study, release, revision):
    return {'operation_id': str(uuid.uuid4()), 'proof': 'p' * 48,
            'instance_id': str(Instance.objects.get(pk=1).instance_id), 'study_id': str(study.id),
            'expected_release_id': str(release.id), 'expected_revision': revision}


def run_pair(first, second):
    """Run both callables after one barrier on their own connections."""
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(fn):
        connections.close_all()
        barrier.wait()
        try:
            outcomes.append(('ok', fn()))
        except Exception as error:  # noqa: BLE001 - recorded as evidence, asserted below
            outcomes.append(('error', repr(error)))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(first,)), threading.Thread(target=worker, args=(second,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    print('race outcomes:', [status for status, _ in outcomes], [detail for status, detail in outcomes if status == 'error'])
    return outcomes


def run_while_writing(researcher_write, admission):
    """Start the admission call while the researcher write transaction is open.

    The researcher transaction takes the SQLite write lock before the admission
    is invoked, so the admission can only read the committed write. Returns
    ``{'researcher': (status, detail), 'admission': (status, detail)}``.
    """
    started = threading.Event()
    invoked = threading.Event()
    outcomes = {}

    def researcher():
        connections.close_all()
        try:
            with transaction.atomic():
                outcomes['researcher'] = ('ok', researcher_write())
                started.set()
                invoked.wait(timeout=10)
        except Exception as error:  # noqa: BLE001 - recorded as evidence, asserted below
            outcomes['researcher'] = ('error', repr(error))
            started.set()
        finally:
            connections.close_all()

    def admission_worker():
        connections.close_all()
        started.wait(timeout=10)
        invoked.set()
        try:
            outcomes['admission'] = ('ok', admission())
        except Exception as error:  # noqa: BLE001 - recorded as evidence, asserted below
            outcomes['admission'] = ('error', repr(error))
        finally:
            connections.close_all()

    threads = [threading.Thread(target=researcher), threading.Thread(target=admission_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    print('held-write outcomes:', outcomes)
    return outcomes


def errors_of(outcomes):
    return [detail for status, detail in outcomes if status == 'error']


@pytest.mark.django_db(transaction=True)
def test_first_admission_intersecting_current_release_switch_is_serializable():
    """A racing switch and first stable-entry admission cannot interleave: either
    the session is created against the observed R1 before the switch, or the
    switch wins and the admission is refused as stale with no session."""
    owner, study, releases = study_world()
    study.refresh_from_db()
    request = admission_request(study, releases[0], study.revision)

    def switch():
        actor = get_user_model().objects.get(pk=owner.pk)
        target = Study.objects.get(pk=study.pk)
        return publication.select_current_release(actor, target, str(target.revision), str(releases[1].id))

    outcomes = run_pair(switch, lambda: admit_request(dict(request)))

    stored = Study.objects.get(pk=study.pk)
    admitted = Session.objects.filter(operation=request['operation_id']).first()
    assert stored.current_release_id == releases[1].id and stored.revision == 2
    assert Audit.objects.filter(action=publication.RELEASE_CHANGED).count() == 2
    assert Session.objects.filter(release__study=study).count() in (0, 1)
    if admitted is None:
        assert len(errors_of(outcomes)) == 1 and 'stale_entry' in errors_of(outcomes)[0]
    else:
        assert errors_of(outcomes) == [] and admitted.release_id == releases[0].id


@pytest.mark.django_db(transaction=True)
def test_first_admission_intersecting_recruitment_close_is_serializable():
    """Racing close and first admission serialize: either the session is created
    while the study is still open, or the close wins and the admission is refused
    with no session and no created participant."""
    owner, study, releases = study_world()
    study.refresh_from_db()
    request = admission_request(study, releases[0], study.revision)

    def close():
        with transaction.atomic():
            target = Study.objects.select_for_update().get(pk=study.pk)
            target.recruitment = 'closed'
            target.save(update_fields=['recruitment'])
        return 'closed'

    outcomes = run_pair(close, lambda: admit_request(dict(request)))

    stored = Study.objects.get(pk=study.pk)
    admitted = Session.objects.filter(operation=request['operation_id']).first()
    assert stored.recruitment == 'closed'
    assert Session.objects.filter(release__study=study).count() in (0, 1)
    if admitted is None:
        assert len(errors_of(outcomes)) == 1 and 'admission_closed' in errors_of(outcomes)[0]
    else:
        assert errors_of(outcomes) == [] and admitted.release_id == releases[0].id


@pytest.mark.django_db(transaction=True)
def test_admission_during_open_switch_transaction_reads_committed_state():
    """The admission is invoked while the researcher's switch transaction is not
    yet committed; it must observe the committed switch and refuse as stale."""
    owner, study, releases = study_world()
    study.refresh_from_db()
    request = admission_request(study, releases[0], study.revision)

    def switch():
        actor = get_user_model().objects.get(pk=owner.pk)
        target = Study.objects.get(pk=study.pk)
        return publication.select_current_release(actor, target, str(target.revision), str(releases[1].id))

    outcomes = run_while_writing(switch, lambda: admit_request(dict(request)))

    stored = Study.objects.get(pk=study.pk)
    status, detail = outcomes['admission']
    assert outcomes['researcher'] == ('ok', {'revision': 2, 'before': {'current_release': str(releases[0].id)},
                                             'after': {'current_release': str(releases[1].id)}})
    assert stored.current_release_id == releases[1].id and stored.revision == 2
    assert status == 'error' and 'stale_entry' in detail
    assert not Session.objects.filter(operation=request['operation_id']).exists()
    assert Session.objects.filter(release__study=study).count() == 0


@pytest.mark.django_db(transaction=True)
def test_admission_during_open_close_transaction_reads_committed_state():
    """The admission is invoked while the researcher's recruitment close is not
    yet committed; it must observe the closed study and admit nothing."""
    owner, study, releases = study_world()
    study.refresh_from_db()
    request = admission_request(study, releases[0], study.revision)

    def close():
        target = Study.objects.select_for_update().get(pk=study.pk)
        target.recruitment = 'closed'
        target.save(update_fields=['recruitment'])
        return 'closed'

    outcomes = run_while_writing(close, lambda: admit_request(dict(request)))

    stored = Study.objects.get(pk=study.pk)
    status, detail = outcomes['admission']
    assert outcomes['researcher'] == ('ok', 'closed')
    assert stored.recruitment == 'closed'
    assert status == 'error' and 'admission_closed' in detail
    assert not Session.objects.filter(operation=request['operation_id']).exists()
    assert Participant.objects.filter(study=study).count() == 0
