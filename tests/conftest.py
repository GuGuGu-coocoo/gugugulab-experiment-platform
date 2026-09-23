import os
import uuid
from pathlib import Path
import pytest
from django.contrib.auth import get_user_model
from core.models import Instance, Study, Build, Release
from core.services import admit

REPO_ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope='session')
def django_db_modify_db_settings():
    """In-memory by default (matching pytest-django); the isolated concurrency
    probe subprocess sets GEP_TEST_DB_FILE so concurrent writers use a real file
    database with the configured busy timeout instead of shared-cache table locks."""
    override = os.environ.get('GEP_TEST_DB_FILE')
    if not override:
        return
    from django.conf import settings
    settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = override


@pytest.fixture(scope='session')
def live_server(request):
    """pytest-django's live server without the shared in-memory connection.

    For an in-memory SQLite test database, Django's ``LiveServerThread`` hands
    the *same* connection object to every request thread (``connections_override``
    plus ``inc_thread_sharing``). Parallel requests for one page -- the GEC Web
    client's bridge/sdk/inputs/shell.js module graph is exactly that -- then run
    queries on one connection at the same time and can observe a row that is
    really there as missing: a real 400 ``Release matching query does not exist``
    was captured for a release that sibling requests served in the same page load.
    The in-memory database is created with ``cache=shared``, so each request
    thread's own connection still sees the same committed data; every test that
    uses ``live_server`` runs with ``transactional_db`` (pytest-django's helper
    requests it), so per-thread connections keep the same visibility. The fixture
    is otherwise identical to pytest-django's, including the modified
    ``ALLOWED_HOSTS`` handling the autouse helper enables.
    """
    from pytest_django.live_server_helper import LiveServer
    addr = (request.config.getvalue('liveserver')
            or os.getenv('DJANGO_LIVE_TEST_SERVER_ADDRESS') or 'localhost')
    # Django's live-server thread always installs its private static-file
    # wrapper, which needs a string STATIC_URL/STATIC_ROOT to initialize. The
    # wrapper is pointed at a path that does not exist, so every /static/...
    # request raises Http404 inside it and falls through to the application's
    # own whitelist route (core.assets): the asset is served by exactly the code
    # path the running application uses, with no test-only static serving.
    from django.conf import settings
    before = (settings.STATIC_URL, settings.STATIC_ROOT)
    if not settings.STATIC_URL:
        settings.STATIC_URL = '/static/'
    settings.STATIC_ROOT = str(REPO_ROOT / '.live-server-no-static-root')
    server = LiveServer(addr, start=False)
    # The shared connection is the race; the server thread and its request
    # threads open their own connections to the same shared-cache database.
    server.thread.connections_override = {}
    server.start()
    yield server
    server.stop()
    settings.STATIC_URL, settings.STATIC_ROOT = before

@pytest.fixture
def setup(db):
    owner = get_user_model().objects.create_user('synthetic_owner', password='synthetic-test-password')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='Synthetic A', recruitment='open', max_sessions=3)
    descriptor = {'schemas': {'exp.rt': {'id':'rt', 'version':'1', 'schema': {'type':'object','properties':{'rt_ms':{'type':'number'},'choice':{'type':'string'}},'required':['rt_ms','choice'],'additionalProperties':False}}}, 'codebook': {'rt_ms': {'unit':'ms','source':'host'}}}
    build = Build.objects.create(study=study, descriptor=descriptor, digest='a'*64)
    release = Release.objects.create(study=study, build=build, config={'purpose':'synthetic'}, approved=True)
    request = {'operation_id':str(uuid.uuid4()), 'proof':'p'*48, 'instance_id':str(instance.instance_id),'study_id':str(study.id),'release_id':str(release.id),'build_id':str(build.id)}
    session, token = admit(release, request)
    return {'owner':owner,'instance':instance,'study':study,'release':release,'request':request,'session':session,'token':token}

@pytest.fixture
def event(setup):
    return {'protocol_version':'gep/1','event_id':str(uuid.uuid4()),'session_id':str(setup['session'].id),'segment_id':str(uuid.uuid4()),'sequence':1,'event_type':'exp.rt','schema_id':'rt','schema_version':'1','payload':{'rt_ms':321.5,'choice':'left'}}
