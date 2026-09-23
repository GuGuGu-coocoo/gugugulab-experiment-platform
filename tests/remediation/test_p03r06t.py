"""P03R06T evidence: the actual roster callers import real CSV over HTTP.

The R06R review found a source-level gap the shared-helper test could not
cover: ``designer_kit.PlatformClient.roster`` still built ``ID\\tPASSWORD`` for
the password mode, so the CSV-only study entry read the batch as one column and
refused it. This module executes the *actual callers* - never the helper alone,
never a mock network and never a direct database import - against a new
isolated synthetic instance with its own file database and real HTTP service:

- the anonymous/id/password designer callers pass the real preview, the actor's
  own password confirmation and the file-database check, and the previous TSV
  shape is refused by that same real entry with zero writes;
- the Windows roster step (``KitBuilder._import_roster``, extracted from
  ``_prepare_mode`` and shared by production and this test) imports synthetic
  commas, quotes, newlines, Unicode and surrounding whitespace exactly;
- a wrong actor password and a duplicate batch write nothing, and the imported
  participants pass the real participant admission (a wrong password is
  refused).

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r06t/<UTC>-<random>/`` root.
"""
import json
import secrets
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest
from django.contrib.auth.hashers import check_password

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import roster_import_client  # noqa: E402  (tools/ path above)
import phase03_designer_kit as designer_kit  # noqa: E402
import phase03_windows_kit as windows_kit  # noqa: E402

OWNER = windows_kit.OWNER_USERNAME
WEB_ARCHIVE = ROOT / 'build' / 'synthetic_web.zip'
SPECIAL_ROWS = (
    ('P03R06T,comma', 'pass,comma'),
    ('P03R06T"quote"', 'pass"quote"'),
    ('P03R06T\nnewline', 'pass\nnewline'),
    ('被试-α-001', '暗号！'),
    ('  padded-id  ', '  padded-pass  '),
)


# --- the real isolated instance: own file database and HTTP service ----------

@pytest.fixture(scope='module')
def isolated_instance(evidence_root_for):
    """A new isolated synthetic instance (file DB + real gunicorn), stopped after."""
    root = evidence_root_for('p03r06t') / 'isolated_instance'
    instance = windows_kit.Instance(root)
    instance.initialize()
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture(scope='module')
def owner_api(isolated_instance):
    """The designer kit's real client, logged in as the synthetic owner."""
    client = designer_kit.PlatformClient(isolated_instance.port, isolated_instance.db)
    client.login(OWNER, isolated_instance.owner_password)
    return client


@pytest.fixture
def windows_builder(isolated_instance, owner_api, evidence_root):
    """The real Windows KitBuilder wired to the running isolated instance.

    Only its instance binding is redirected to the instance started above; the
    roster step, the HTTP client, the operator password and the file-database
    reads are the production ones.
    """
    def require(condition, message, detail=None):
        if not condition:
            raise AssertionError(f'{message}: {detail!r}')

    def record(ok, message, detail=None):
        return None

    builder = windows_kit.KitBuilder(evidence_root / 'windows_builder', record, require, quiet=True)
    builder.instance = isolated_instance
    builder.http = owner_api.http
    return builder


# --- helpers -----------------------------------------------------------------

def participants(instance, study_id):
    """``(code, password_hash)`` rows read from the real instance file database."""
    connection = sqlite3.connect(f'file:{instance.db}?mode=ro', uri=True, timeout=20)
    try:
        return connection.execute(
            'select code, password_hash from core_participant where study_id=? order by code',
            [str(study_id).replace('-', '')]).fetchall()
    finally:
        connection.close()


def session_rows(instance, study_id):
    """``(session_id, participant_code)`` rows of this study from the file DB."""
    connection = sqlite3.connect(f'file:{instance.db}?mode=ro', uri=True, timeout=20)
    try:
        return connection.execute(
            'select session.id, participant.code from core_session session '
            'join core_release release on session.release_id=release.id '
            'left join core_participant participant on session.participant_id=participant.id '
            'where release.study_id=? order by session.created_at',
            [str(study_id).replace('-', '')]).fetchall()
    finally:
        connection.close()


def dashed(value):
    """Django stores UUIDs as 32 hex characters on SQLite; compare dashed."""
    text = str(value)
    if len(text) == 32 and '-' not in text:
        return f'{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}'
    return text


def make_study(api, title, mode):
    study_id = api.create_study(title)
    api.configure(study_id, mode)
    return study_id


def publish_web(api, study_id):
    """One real web build -> approved release -> open recruitment."""
    build = api.upload_web(study_id, WEB_ARCHIVE)
    release = api.approve(study_id, build['id'], native=False)
    api.open_recruitment(study_id)
    return build, release


def admit(api, instance, study_id, build, release, code=None, password=None):
    body = {'operation_id': str(uuid.uuid4()), 'proof': secrets.token_hex(32),
            'instance_id': instance.instance_id, 'study_id': study_id,
            'release_id': release['release_id'], 'build_id': build['id']}
    if code is not None:
        body['participant_code'] = code
    if password is not None:
        body['password'] = password
    return api.http.post_json('/v1/participant/sessions', body)


# --- designer caller: real HTTP preview/commit + file database ---------------

def test_designer_caller_imports_csv_and_the_old_tsv_is_refused(
        isolated_instance, owner_api, evidence):
    anonymous = make_study(owner_api, 'P03R06T 设计者匿名模式', 'anonymous')
    assert owner_api.roster(anonymous, 'anonymous') is None
    assert participants(isolated_instance, anonymous) == []

    ids = make_study(owner_api, 'P03R06T 设计者 ID 模式', 'id')
    owner_api.roster(ids, 'id')
    id_rows = participants(isolated_instance, ids)
    assert id_rows == [(designer_kit.ROSTER_ID, '')]

    passwords = make_study(owner_api, 'P03R06T 设计者密码模式', 'password')
    owner_api.roster(passwords, 'password')
    password_rows = participants(isolated_instance, passwords)
    assert [row[0] for row in password_rows] == [designer_kit.ROSTER_ID]
    assert check_password(designer_kit.ROSTER_PASSWORD, password_rows[0][1])
    assert not check_password(designer_kit.ROSTER_PASSWORD[:-1], password_rows[0][1])

    # The previous caller's shape (ID<TAB>PASSWORD) is refused by the very same
    # real entry with zero writes: the CSV encoding is what makes the password
    # mode importable, and the entry still refuses the tab batch itself.
    status, payload, _ = owner_api.http.post_form(
        f'/studies/{passwords}/roster-import',
        [('op', 'import_roster_preview'),
         ('roster', f'{designer_kit.ROSTER_ID}\t{designer_kit.ROSTER_PASSWORD}')],
        expect_redirect=False)
    body = payload.decode('utf-8', 'replace')
    assert status == 200
    assert 'data-preview-error="roster_columns"' in body
    assert 'data-roster-confirm' not in body
    assert participants(isolated_instance, passwords) == password_rows

    # A wrong actor password is refused at the real commit with zero writes.
    refused_study = make_study(owner_api, 'P03R06T 设计者错误口令', 'password')
    current = owner_api.password
    owner_api.password = 'wrong-actor-password'
    try:
        with pytest.raises(roster_import_client.RosterImportError) as refused:
            owner_api.roster(refused_study, 'password')
    finally:
        owner_api.password = current
    assert '403' in str(refused.value)
    assert participants(isolated_instance, refused_study) == []

    evidence('designer_caller.json', {
        'anonymous_participants': 0,
        'id_codes': [row[0] for row in id_rows],
        'password_code': password_rows[0][0],
        'password_hash_verifies_exact_value': True,
        'tsv_preview_status': status,
        'tsv_preview_error': 'roster_columns',
        'wrong_actor_password_status': 403,
    })


def test_designer_caller_participants_are_admitted_over_real_http(
        isolated_instance, owner_api, evidence):
    study_id = make_study(owner_api, 'P03R06T 设计者准入', 'password')
    owner_api.roster(study_id, 'password')
    build, release = publish_web(owner_api, study_id)

    status, payload, _ = admit(owner_api, isolated_instance, study_id, build, release,
                               code=designer_kit.ROSTER_ID, password=designer_kit.ROSTER_PASSWORD)
    assert status == 200
    admission = json.loads(payload)
    assert admission['release_id'] == release['release_id']
    rows = session_rows(isolated_instance, study_id)
    assert [(dashed(row[0]), row[1]) for row in rows] == [(admission['session_id'], designer_kit.ROSTER_ID)]

    refused_status, refused_payload, _ = admit(owner_api, isolated_instance, study_id, build, release,
                                               code=designer_kit.ROSTER_ID,
                                               password='not-the-password')
    assert refused_status == 403
    assert json.loads(refused_payload)['code'] == 'admission_denied'
    assert len(session_rows(isolated_instance, study_id)) == 1

    evidence('designer_admission.json', {
        'session_id': admission['session_id'],
        'session_code': designer_kit.ROSTER_ID,
        'wrong_password_status': refused_status,
        'sessions_after_wrong_password': 1,
    })


# --- Windows roster step: exact special values + admission -------------------

def test_windows_roster_step_imports_special_values_exactly(
        isolated_instance, owner_api, windows_builder, evidence):
    anonymous = make_study(owner_api, 'P03R06T Windows 匿名模式', 'anonymous')
    assert windows_builder._import_roster(anonymous, 'anonymous', []) == 0
    assert participants(isolated_instance, anonymous) == []

    ids = make_study(owner_api, 'P03R06T Windows ID 模式', 'id')
    id_rows = [(code,) for code in windows_kit.ID_CODES]
    assert windows_builder._import_roster(ids, 'id', id_rows) == len(id_rows)
    assert participants(isolated_instance, ids) == [(code, '') for code in sorted(windows_kit.ID_CODES)]

    study_id = make_study(owner_api, 'P03R06T Windows 特殊字符', 'password')
    added = windows_builder._import_roster(study_id, 'password', SPECIAL_ROWS)
    assert added == len(SPECIAL_ROWS)
    rows = participants(isolated_instance, study_id)
    stored = dict(rows)
    assert sorted(stored) == sorted(code for code, _ in SPECIAL_ROWS)
    for code, password in SPECIAL_ROWS:
        assert check_password(password, stored[code])
    # The stored hash verifies the exact value only: the trimmed form fails.
    assert check_password('  padded-pass  ', stored['  padded-id  '])
    assert not check_password('padded-pass', stored['  padded-id  '])
    assert not check_password('comma,pass', stored['P03R06T,comma'])

    build, release = publish_web(owner_api, study_id)
    status, payload, _ = admit(owner_api, isolated_instance, study_id, build, release,
                               code='P03R06T,comma', password='pass,comma')
    assert status == 200
    admission = json.loads(payload)
    rows = session_rows(isolated_instance, study_id)
    assert [(dashed(row[0]), row[1]) for row in rows] == [(admission['session_id'], 'P03R06T,comma')]

    # The trimmed password is refused at admission exactly as at the hash check.
    refused_status, refused_payload, _ = admit(owner_api, isolated_instance, study_id, build, release,
                                               code='  padded-id  ', password='padded-pass')
    assert refused_status == 403
    assert json.loads(refused_payload)['code'] == 'admission_denied'
    assert len(session_rows(isolated_instance, study_id)) == 1

    evidence('windows_roster_caller.json', {
        'anonymous_added': 0,
        'id_codes': sorted(windows_kit.ID_CODES),
        'special_codes': [code for code, _ in SPECIAL_ROWS],
        'admitted_code': 'P03R06T,comma',
        'trimmed_password_status': refused_status,
        'sessions_after_refusal': 1,
    })


def test_windows_roster_step_duplicate_and_wrong_password_write_nothing(
        isolated_instance, owner_api, windows_builder, evidence):
    study_id = make_study(owner_api, 'P03R06T Windows 重复批', 'password')
    rows = [('dup-1', 'dup-pass-1')]
    assert windows_builder._import_roster(study_id, 'password', rows) == 1
    before = participants(isolated_instance, study_id)

    # A duplicate batch is refused by the real preview (the helper never
    # invents a confirm form) and writes nothing.
    with pytest.raises(roster_import_client.RosterImportError):
        windows_builder._import_roster(study_id, 'password', [('dup-1', 'other-password')])
    assert participants(isolated_instance, study_id) == before

    # A wrong operator password is refused at the real commit and writes nothing.
    wrong_study = make_study(owner_api, 'P03R06T Windows 错误口令', 'password')
    real = isolated_instance.owner_password
    isolated_instance.owner_password = 'wrong-actor-password'
    try:
        with pytest.raises(roster_import_client.RosterImportError) as refused:
            windows_builder._import_roster(wrong_study, 'password', [('wrong-actor-1', 'x')])
    finally:
        isolated_instance.owner_password = real
    assert '403' in str(refused.value)
    assert participants(isolated_instance, wrong_study) == []

    evidence('windows_roster_negatives.json', {
        'duplicate_batch_codes': [row[0] for row in before],
        'wrong_actor_password_status': 403,
        'wrong_actor_password_participants': 0,
    })
