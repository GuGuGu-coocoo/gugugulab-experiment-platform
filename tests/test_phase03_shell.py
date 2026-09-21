"""Phase 03 reusable GEC shell contract tests.

These tests cover the server-side frozen public configuration, the versioned
same-device recovery API the shell consumes, the real headless Godot shell
contract and the packaging boundary that keeps the scientific task unchanged.
They are non-interactive and fail (never skip) when the pinned Godot toolchain is
missing.
"""
import hashlib
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import Client

from core.gui import connection_config
from core.models import Build, Grant, Instance, Participant, Release, Session, Study
from core.access import ACTIONS
from core.protocol import Rejected
from core.services import (RECOVERY_CODE_CAPABILITY, RECOVERY_NAMED_CAPABILITY, SHELL_CAPABILITY,
                           issue_recovery_code)

ROOT = Path(__file__).resolve().parents[1]
GODOT = shutil.which('godot')


def _godot() -> str:
    if GODOT is None:
        pytest.fail('godot executable not on PATH; the pinned Godot 4.7.2 toolchain is required')
    return GODOT


def frozen_config(release, mode):
    release.config = {'purpose': 'synthetic', 'mode': mode, 'max_sessions': 3,
                      'offline_policy': 'continue_local', 'recovery': 'trial_boundary_v1'}
    release.save(update_fields=['config'])
    return connection_config(release)


def test_connection_config_freezes_mode_without_credentials(setup):
    for mode in ('anonymous', 'id', 'password'):
        config = frozen_config(setup['release'], mode)
        assert config['mode'] == mode
        assert config['shell_capability'] == SHELL_CAPABILITY
        assert config['purpose'] == 'synthetic'
        assert set(config) <= {'config_version', 'protocol_version', 'sdk_version', 'api_url', 'instance_id', 'study_id',
                               'release_id', 'build_id', 'purpose', 'mode', 'shell_capability'}
        serialized = json.dumps(config)
        assert 'roster' not in serialized and 'participant' not in serialized and 'secret' not in serialized
    assert connection_config(setup['release'])['mode'] == 'password'


def test_legacy_release_keeps_pre_shell_contract(setup):
    """A release approved before the shell has no frozen mode: the shell must
    keep the legacy field set instead of guessing one."""
    config = connection_config(setup['release'])
    assert 'mode' not in config and 'shell_capability' not in config
    assert config['config_version'] == '1' and config['purpose'] == 'synthetic'


def test_config_endpoint_serves_frozen_mode(setup, db):
    owner = setup['owner']
    for action in ACTIONS:
        Grant.objects.get_or_create(user=owner, study=setup['study'], action=action, defaults={'delegable': True})
    frozen_config(setup['release'], 'id')
    client = Client()
    client.force_login(owner)
    response = client.get(f'/releases/{setup["release"].id}/config', HTTP_HOST='admin.localhost')
    assert response.status_code == 200
    config = json.loads(response.content)
    assert config['mode'] == 'id' and config['shell_capability'] == SHELL_CAPABILITY
    assert config['release_id'] == str(setup['release'].id) and config['build_id'] == str(setup['release'].build_id)


def test_recovery_api_requires_versioned_capability(setup, client):
    response = client.post('/v1/participant/recovery', data=json.dumps({'capability': 'recovery_code/v2'}),
                           content_type='application/json', HTTP_HOST='experiment.localhost')
    assert response.status_code == 409
    assert json.loads(response.content)['code'] == 'unsupported_capability'


def test_recovery_code_needs_device_proof_and_binding(setup, db):
    session = setup['session']
    for action in ('study.view', 'session.recover'):
        Grant.objects.get_or_create(user=setup['owner'], study=setup['study'], action=action, defaults={'delegable': True})
    binding = {'instance_id': str(setup['instance'].instance_id), 'study_id': str(setup['study'].id),
               'release_id': str(setup['release'].id), 'build_id': str(setup['release'].build_id)}
    client = Client()
    issued = issue_recovery_code(setup['owner'], session.id)
    assert re.fullmatch(r'\d{6}', issued['code'])
    # Wrong proof, wrong code and unknown binding all deny without disclosing a session.
    for body in ({'capability': RECOVERY_CODE_CAPABILITY, 'code': issued['code'], 'proof': 'q' * 48, **binding},
                 {'capability': RECOVERY_CODE_CAPABILITY, 'code': '000000', 'proof': 'p' * 48, **binding},
                 {'capability': RECOVERY_CODE_CAPABILITY, 'code': issued['code'], 'proof': 'p' * 48,
                  **{**binding, 'study_id': str(uuid.uuid4())}}):
        response = client.post('/v1/participant/recovery', data=json.dumps(body),
                               content_type='application/json', HTTP_HOST='experiment.localhost')
        assert response.status_code == 403
        payload = json.loads(response.content)
        assert payload['code'] == 'recovery_denied' and 'token' not in payload and 'session_id' not in payload
    # The original device proof redeems the code without any public UUID.
    redeemed = client.post('/v1/participant/recovery',
                           data=json.dumps({'capability': RECOVERY_CODE_CAPABILITY, 'code': issued['code'],
                                            'proof': 'p' * 48, **binding}),
                           content_type='application/json', HTTP_HOST='experiment.localhost')
    assert redeemed.status_code == 200
    payload = json.loads(redeemed.content)
    assert payload['session_id'] == str(session.id) and payload['token'] and payload['task_finished'] is False
    assert 'participant_code' not in payload and 'participant_uuid' not in payload
    # A consumed code cannot be replayed.
    replay = client.post('/v1/participant/recovery',
                         data=json.dumps({'capability': RECOVERY_CODE_CAPABILITY, 'code': issued['code'],
                                          'proof': 'p' * 48, **binding}),
                         content_type='application/json', HTTP_HOST='experiment.localhost')
    assert replay.status_code == 403


@pytest.fixture
def password_release(db):
    owner = get_user_model().objects.create_user('shell_owner', password='shell-owner-password')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title='Shell password study', mode='password', recruitment='open', max_sessions=2)
    participant = Participant.objects.create(study=study, code='001', password_hash=make_password('synthetic-password'))
    build = Build.objects.create(study=study, descriptor={'platform': 'godot_web', 'version': 'synthetic-1'}, digest='b' * 64)
    release = Release.objects.create(study=study, build=build, approved=True,
                                     config={'purpose': 'synthetic', 'mode': 'password', 'max_sessions': 2})
    session = Session.objects.create(participant=participant, release=release, operation=uuid.uuid4(),
                                     proof_hash=hashlib.sha256(('p' * 48).encode()).hexdigest(),
                                     request={'operation_id': str(uuid.uuid4())},
                                     token_hash=hashlib.sha256(b'token').hexdigest(),
                                     expires_at=timezone.now() + timedelta(days=1))
    return {'owner': owner, 'instance': instance, 'study': study, 'release': release, 'build': build,
            'session': session, 'participant': participant}


def test_named_recovery_refuses_front_locked_and_wrong_password(password_release, client):
    binding = {'instance_id': str(password_release['instance'].instance_id), 'study_id': str(password_release['study'].id),
               'release_id': str(password_release['release'].id), 'build_id': str(password_release['build'].id)}
    base = {'capability': RECOVERY_NAMED_CAPABILITY, 'participant_code': '001', 'proof': 'p' * 48, **binding}
    locked = client.post('/v1/participant/recovery',
                         data=json.dumps({**base, 'password': 'synthetic-password', 'front_locked': True}),
                         content_type='application/json', HTTP_HOST='experiment.localhost')
    assert locked.status_code == 403 and json.loads(locked.content)['code'] == 'front_locked'
    wrong = client.post('/v1/participant/recovery',
                        data=json.dumps({**base, 'password': 'not-the-password', 'front_locked': False}),
                        content_type='application/json', HTTP_HOST='experiment.localhost')
    assert wrong.status_code == 403 and 'token' not in json.loads(wrong.content)
    matched = client.post('/v1/participant/recovery',
                          data=json.dumps({**base, 'password': 'synthetic-password', 'front_locked': False}),
                          content_type='application/json', HTTP_HOST='experiment.localhost')
    assert matched.status_code == 200
    payload = json.loads(matched.content)
    assert payload['session_id'] == str(password_release['session'].id) and payload['token']


# ---------------------------------------------------------------------------
# Frozen release mode vs. later study policy changes (real HTTP admission)
# ---------------------------------------------------------------------------

def admission_world(study_mode='anonymous', frozen=None, roster=True):
    """Owner + one approved release; ``frozen`` is the mode frozen into it.

    The study policy is written first and can be edited afterwards, exactly like
    a researcher policy change after the release was approved.
    """
    owner = get_user_model().objects.create_user(f'shell_admission_{uuid.uuid4().hex[:10]}',
                                                 password='synthetic-test-password')
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner)
    study = Study.objects.create(title=f'Shell frozen {frozen}', mode=study_mode, recruitment='open', max_sessions=4)
    build = Build.objects.create(study=study, descriptor={'platform': 'godot_web', 'version': 'synthetic-1'}, digest='c' * 64)
    config = {'purpose': 'synthetic'}
    if frozen is not None:
        config['mode'] = frozen
    release = Release.objects.create(study=study, build=build, approved=True, config=config)
    if roster:
        Participant.objects.create(study=study, code='001', password_hash=make_password('synthetic-password'))
    return {'owner': owner, 'instance': instance, 'study': study, 'release': release, 'build': build}


def admit_post(world, **extra):
    body = {'operation_id': str(uuid.uuid4()), 'proof': 'a' * 48,
            'instance_id': str(world['instance'].instance_id), 'study_id': str(world['study'].id),
            'release_id': str(world['release'].id), 'build_id': str(world['build'].id)}
    body.update(extra)
    return Client().post('/v1/participant/sessions', body, content_type='application/json',
                         HTTP_HOST='experiment.localhost')


def admitted(response):
    assert response.status_code == 200, response.content
    payload = json.loads(response.content)
    return payload, Session.objects.get(pk=payload['session_id'])


@pytest.mark.django_db
def test_frozen_password_release_keeps_admission_after_study_policy_change():
    """A release approved as password keeps requiring the password even when the
    study is later switched to anonymous; the public config keeps the same mode."""
    world = admission_world(study_mode='password', frozen='password')
    world['study'].mode = 'anonymous'
    world['study'].save(update_fields=['mode'])
    assert connection_config(world['release'])['mode'] == 'password'

    payload, session = admitted(admit_post(world, participant_code='001', password='synthetic-password'))
    assert session.release_id == world['release'].id
    assert Session.objects.get(pk=payload['session_id']).participant.code == '001'

    assert admit_post(world, participant_code='001', password='wrong-password').status_code == 403
    assert admit_post(world, participant_code='001').status_code == 403
    assert admit_post(world).status_code == 403
    # The anonymous-looking request never created a second participant.
    assert Participant.objects.filter(study=world['study'], code__isnull=True).count() == 0


@pytest.mark.django_db
def test_frozen_anonymous_release_ignores_study_roster_policy():
    world = admission_world(study_mode='password', frozen='anonymous')
    world['study'].mode = 'password'
    world['study'].save(update_fields=['mode'])
    assert connection_config(world['release'])['mode'] == 'anonymous'

    refused = admit_post(world, participant_code='001', password='synthetic-password')
    assert refused.status_code == 422 and json.loads(refused.content)['code'] == 'unexpected_code'
    response = admit_post(world)
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert Session.objects.get(pk=payload['session_id']).participant.code is None


@pytest.mark.django_db
def test_frozen_id_release_requires_roster_id_after_study_policy_change():
    world = admission_world(study_mode='anonymous', frozen='id')
    world['study'].mode = 'anonymous'
    world['study'].save(update_fields=['mode'])
    assert connection_config(world['release'])['mode'] == 'id'

    assert admit_post(world).status_code == 403
    assert admit_post(world, participant_code='unknown').status_code == 403
    payload, _session = admitted(admit_post(world, participant_code='001'))
    assert Session.objects.get(pk=payload['session_id']).participant.code == '001'


@pytest.mark.django_db
def test_legacy_release_without_frozen_mode_follows_the_study_policy():
    """Legacy releases keep the pre-shell rule: the study policy decides."""
    world = admission_world(study_mode='password', frozen=None)
    assert 'mode' not in connection_config(world['release'])
    assert admit_post(world, participant_code='001', password='wrong-password').status_code == 403
    admitted(admit_post(world, participant_code='001', password='synthetic-password'))


@pytest.mark.django_db
def test_legacy_anonymous_release_still_refuses_a_roster_id():
    world = admission_world(study_mode='anonymous', frozen=None)
    assert 'mode' not in connection_config(world['release'])
    refused = admit_post(world, participant_code='001')
    assert refused.status_code == 422 and json.loads(refused.content)['code'] == 'unexpected_code'
    response = admit_post(world)
    assert response.status_code == 200
    assert Session.objects.get(pk=json.loads(response.content)['session_id']).participant.code is None


@pytest.mark.django_db
def test_unknown_frozen_mode_fails_closed_in_config_and_admission():
    world = admission_world(study_mode='anonymous', frozen='invite')
    with pytest.raises(Rejected) as rejected:
        connection_config(world['release'])
    assert rejected.value.code == 'unsupported_capability' and rejected.value.status == 409
    response = admit_post(world)
    assert response.status_code == 409 and json.loads(response.content)['code'] == 'unsupported_capability'
    assert Session.objects.filter(release=world['release']).count() == 0

    owner = world['owner']
    for action in ACTIONS:
        Grant.objects.get_or_create(user=owner, study=world['study'], action=action, defaults={'delegable': True})
    client = Client()
    client.force_login(owner)
    endpoint = client.get(f'/releases/{world["release"].id}/config', HTTP_HOST='admin.localhost')
    assert endpoint.status_code == 409 and json.loads(endpoint.content)['code'] == 'unsupported_capability'


@pytest.mark.django_db
def test_public_config_endpoint_keeps_frozen_mode_after_policy_change():
    world = admission_world(study_mode='id', frozen='password')
    world['study'].mode = 'anonymous'
    world['study'].save(update_fields=['mode'])
    owner = world['owner']
    for action in ACTIONS:
        Grant.objects.get_or_create(user=owner, study=world['study'], action=action, defaults={'delegable': True})
    client = Client()
    client.force_login(owner)
    response = client.get(f'/releases/{world["release"].id}/config', HTTP_HOST='admin.localhost')
    assert response.status_code == 200
    config = json.loads(response.content)
    assert config['mode'] == 'password' and config['shell_capability'] == SHELL_CAPABILITY
    serialized = json.dumps(config)
    assert 'password_hash' not in serialized and 'roster' not in serialized and 'participant' not in serialized


def test_verify_init_derives_every_path_from_the_resolved_root(tmp_path, monkeypatch):
    """A relative evidence root must not leak into the child-facing paths.

    Old behavior: ``Verify.__init__`` resolved ``self.root`` but built ``data`` /
    ``evidence`` / ``native`` from the original argument, so a relative root made
    the exported program's ``--config`` path unreadable (the P0308 shell
    regression). Every derived path now comes from the resolved root.
    """
    import sys as _sys

    if str(ROOT / "tools") not in _sys.path:
        _sys.path.insert(0, str(ROOT / "tools"))
    import phase03_verify_shell as shell_verify

    monkeypatch.chdir(tmp_path)
    verify = shell_verify.Verify(Path("relative-root"))
    assert verify.root == (tmp_path / "relative-root").resolve()
    assert verify.data == verify.root / "data"
    assert verify.evidence == verify.root / "evidence"
    assert verify.native_root == verify.root / "native"
    assert verify.db_path == verify.root / "data" / "gep.sqlite3"
    for path in (verify.data, verify.evidence, verify.native_root):
        assert path.is_absolute()


def test_headless_shell_contract_harness():
    """The real Control-based shell is driven through its own fields and buttons."""
    result = subprocess.run([_godot(), '--headless', '--path', str(ROOT / 'examples' / 'synthetic_experiment'),
                             '--script', str(ROOT / 'tests' / 'native' / 'shell_harness.gd')],
                            cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert 'SHELL_UNIT_VERIFIED' in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


def test_bootstrap_is_assembly_only_and_scientific_task_unchanged():
    bootstrap = (ROOT / 'examples' / 'synthetic_experiment' / 'bootstrap.gd').read_text()
    assert 'shell.gd' in bootstrap and 'Shell.new()' in bootstrap
    assert 'LineEdit.new' not in bootstrap and 'placeholder_text' not in bootstrap
    assert 'permit.text' not in bootstrap and 'password.text' not in bootstrap and 'short_code.text' not in bootstrap
    assert 'data.prepare' not in bootstrap and 'backend.prepare' not in bootstrap
    assert 'task.response' in bootstrap and 'shell.auto_submit' in bootstrap
    task = (ROOT / 'examples' / 'synthetic_experiment' / 'task.gd').read_bytes()
    recorded = (ROOT / 'tests' / 'fixtures' / 'scientific_task.sha256').read_text().strip()
    assert hashlib.sha256(task).hexdigest() == recorded, 'the scientific task source changed; that requires an explicit task decision'


def test_shell_module_and_web_companion_contract():
    shell = (ROOT / 'examples' / 'synthetic_experiment' / 'addons' / 'gec' / 'shell.gd').read_text()
    assert 'gec-shell/v1' in shell and 'SHELL_CAPABILITY' in shell
    assert 'confirm_session' in shell and 'confirm_new_session' in shell
    assert 'announce_finished' in shell
    companion = (ROOT / 'packages' / 'gec_web' / 'shell.js').read_text()
    for field in ('gec-input-code', 'gec-input-password', 'gec-input-short-code', 'gec-input-recovery', 'gec-input-permit'):
        assert field in companion
    assert 'ResizeObserver' in companion, 'the Web panel must reflow on resize instead of keeping stale coordinates'
    bridge = (ROOT / 'packages' / 'gec_web' / 'bridge.js').read_text()
    assert 'mount_shell' in bridge and 'on_shell_action' in bridge
    assert 'mount_inputs' in bridge, 'the legacy fallback panel must stay available'
    packaging = (ROOT / 'tools' / 'package_build.py').read_text()
    assert 'gec/shell.js' in packaging
    builder = (ROOT / 'tools' / 'build.py').read_text()
    assert "'shell.js'" in builder


def test_native_recovery_export_entry_writes_the_gui_document_without_secrets(tmp_path):
    """The new --export-recovery automation entry, verified on real Godot.

    This is not a shell unit test with a scripted backend: it runs the pinned
    Godot 4.7.2 against the real native SQLite backend and the real shell in an
    isolated synthetic store, and proves the automation entry writes the same
    recovery document as the GUI save dialog, keeps records/pending untouched,
    contains no session token/proof and reports an error for an unopenable path.
    """
    storage = tmp_path / 'store with spaces'
    storage.mkdir()
    environment = {**__import__('os').environ, 'GEP_SYNTHETIC_STORAGE': str(storage)}
    result = subprocess.run(
        [_godot(), '--headless', '--path', str(ROOT / 'examples' / 'synthetic_experiment'),
         '--script', str(ROOT / 'tests' / 'native' / 'recovery_export_harness.gd')],
        cwd=ROOT, capture_output=True, text=True, timeout=180, env=environment)
    assert 'NATIVE_RECOVERY_EXPORT_VERIFIED' in result.stdout, result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Fast/delayed completion-receipt regression (real Chrome + shipped Web client)
# ---------------------------------------------------------------------------

PROBE_PAGE = b'''<!doctype html>
<html><head><meta charset="utf-8"><title>GEC completion probe</title></head><body>
<script type="module">
import "./gec/bridge.js";
const call = async (op, args) => {
  const key = "probe-" + Math.random().toString(36).slice(2);
  await globalThis.GECBridge.call(key, op, JSON.stringify(args));
  return JSON.parse(globalThis.GECBridge.take(key));
};
const states = [];
let watching = false;
let last = null;
const sample = () => {
  const state = globalThis.GECBridge.status().state;
  if (state !== last) { states.push([Math.round(performance.now()), state]); last = state; }
};
setInterval(() => { if (watching) sample(); }, 5);
globalThis.GECProbe = {
  ready: false, error: null,
  async run(spec = {}) {
    if (!globalThis.GECProbe.ready) throw new Error("probe_not_ready");
    const total = spec.records ?? 4;
    const started = await call("start", [{}]);
    if (started.error) throw new Error("start:" + started.error);
    for (let index = 0; index < total; index++) {
      const recorded = await call("record", ["exp.rt", {rt_ms: 100 + index, choice: "left"}, {id: "rt", version: "1"}, null]);
      if (recorded.error) throw new Error("record:" + recorded.error);
    }
    const committed = await call("commit", [null]);
    if (committed.error) throw new Error("commit:" + committed.error);
    const finished = await call("finish", []);
    if (finished.error) throw new Error("finish:" + finished.error);
    return {session_id: started.session_id, records: total};
  },
  watch() { states.length = 0; last = null; watching = true; sample(); },
  mark(label) { states.push([Math.round(performance.now()), "mark:" + label]); },
  timeline() { watching = false; return states.slice(); },
};
(async () => {
  for (let attempt = 0; attempt < 400; attempt++) {
    if (globalThis.GECBridge.status().state === "ready") { globalThis.GECProbe.ready = true; return; }
    if (globalThis.GECBridge.prepare_error) { globalThis.GECProbe.error = globalThis.GECBridge.prepare_error; return; }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  globalThis.GECProbe.error = "probe_timeout";
})();
</script></body></html>'''


def completion_probe_package() -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w') as archive:
        archive.writestr('web/index.html', PROBE_PAGE)
        for name in ('sdk.js', 'bridge.js', 'inputs.js', 'shell.js'):
            archive.writestr('web/gec/' + name, (ROOT / 'packages' / 'gec_web' / name).read_bytes())
    return target.getvalue()


def probe_release_code() -> str:
    descriptor = {'platform': 'godot_web', 'version': 'completion-probe',
                  'schemas': {'exp.rt': {'id': 'rt', 'version': '1', 'schema': {
                      'type': 'object', 'properties': {'rt_ms': {'type': 'number'}, 'choice': {'type': 'string'}},
                      'required': ['rt_ms', 'choice'], 'additionalProperties': False}}}}
    return (
        "import django\n"
        "django.setup()\n"
        "from core.models import Build, Release, Study\n"
        f"descriptor = {descriptor!r}\n"
        "study = Study.objects.create(title='Shell completion probe', mode='anonymous', recruitment='open', max_sessions=8)\n"
        "build = Build.objects.create(study=study, descriptor=descriptor, digest='e' * 64, package_path='completion-probe.zip')\n"
        "release = Release.objects.create(study=study, build=build, approved=True, "
        "config={'purpose': 'synthetic', 'mode': 'anonymous', 'max_sessions': 8})\n"
        "print('PROBE_RELEASE', release.id)\n"
    )


def test_completion_receipt_fast_delayed_truncated_and_refused(tmp_path):
    """The receipt observation must not race a transient UI state.

    Real Chrome runs the shipped Web client against the verifier's own isolated
    gunicorn instance (its own data directory, database and fresh port), so the
    test needs no live-server database sharing. While the completion receipt is
    withheld, the durable declaration and the finished state are observable;
    after release the session cleans. A refused receipt must stay local,
    finished and unacknowledged, a two-record declaration must never satisfy the
    four-record wait, and the fast path must reach the same cleaned receipt
    without depending on the transient.
    """
    if str(ROOT / 'tools') not in sys.path:
        sys.path.insert(0, str(ROOT / 'tools'))
    import phase03_verify_shell as shell_verify

    verify = shell_verify.Verify(tmp_path / 'instance')
    verify.init_instance()
    verify.start_server()
    try:
        packages = verify.data / 'packages'
        packages.mkdir(parents=True, exist_ok=True)
        (packages / 'completion-probe.zip').write_bytes(completion_probe_package())
        created = verify.run_python(probe_release_code())
        match = re.search(r'PROBE_RELEASE ([0-9a-f-]{36})', created.stdout)
        assert match, created.stdout + created.stderr
        release_id = match.group(1)
        job_path = tmp_path / 'completion_probe_job.json'
        job_path.write_text(json.dumps({
            'probe_url': f'http://experiment.localhost:{verify.port}/run/{release_id}/web/index.html',
            'run_dir': str(tmp_path)}))
        result = subprocess.run(['node', str(ROOT / 'tests' / 'browser' / 'phase03_shell_verify.mjs'),
                                 'completion-probe', str(job_path)],
                                cwd=ROOT, capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload['failures'] == [], payload['failures']
        labels = [entry['label'] for entry in payload['checks']]
        for label in ('probe: delayed receipt observes finished with the full declaration while the ACK is withheld',
                      'probe: delayed receipt reaches the cleaned remote-acknowledged tombstone',
                      'probe: fast receipt reaches the cleaned remote-acknowledged tombstone without the transient',
                      'probe: a two-record session is never accepted as a full four-record finish',
                      'probe: a refused receipt stays local, finished and unacknowledged, with no tombstone',
                      'probe: the retained declaration retransmits after the refusal and cleans'):
            assert label in labels, labels
        timeline = payload['evidence']['probe']['scenarios']['delayed']['timeline']
        values = [value for _, value in timeline]
        release_at = values.index('mark:release')
        assert 'finished' in values[:release_at], timeline
        assert 'remote_acknowledged' in values[release_at:], timeline
        scenarios = payload['evidence']['probe']['scenarios']
        sessions = {}
        connection = sqlite3.connect(f'file:{verify.db_path}?mode=ro', uri=True)
        try:
            for session_id, completion in connection.execute(
                    'select id, completion from core_session where release_id=?', [release_id.replace('-', '')]):
                events = connection.execute('select count(*) from core_event where session_id=?',
                                            [session_id]).fetchone()[0]
                sessions[session_id] = (events, len(json.loads(completion)['event_ids']) if completion else None)
        finally:
            connection.close()
        assert len(sessions) == 4
        assert sessions[scenarios['short']['session_id'].replace('-', '')] == (2, 2)
        assert sorted(sessions.values()) == [(2, 2), (4, 4), (4, 4), (4, 4)]
    finally:
        verify.stop_server()
