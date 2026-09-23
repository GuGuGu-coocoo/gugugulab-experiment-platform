"""P03R06R evidence: the closed legacy roster write entry and the blank-row contract.

The expected side is the R00 contract (2026-09-23), the confirmed product
requirements (current_requirements §4, U07/U08) and explicit synthetic inputs,
never the implementation under test:

- the study-page legacy ``op=roster`` write entry is refused explicitly on every
  reachable path (root page, module page, alias URL, default ``legacy_tab`` and
  ``csv`` formats) with a 409 ``roster_import_moved``, zero writes and no
  redirect that could replay the POST; an actor without the study's configure
  scope is still refused first;
- every new write needs the preview and the actor's *current* own password: no
  preview, an unknown preview, a stale actor and a changed password all refuse
  with zero writes;
- CSV and XLSX distinguish a truly empty record (ignored) from non-empty
  whitespace text: an all-whitespace ID is a per-row ``id_whitespace`` error
  with its real source row number, a mixed batch writes nothing, and a
  whitespace-only password with a valid ID is kept exactly and admits exactly;
- the updated acceptance tools create the roster through the shared HTTP helper
  (preview + operator-password commit) over a real server and the synthetic
  database, and the legacy direct-write call is gone from both kits.

Evidence stays under this module's unique
``local_data/phase03_remediation_20260923/p03r06r/<UTC>-<random>/`` root.
"""
import io
import json
import os
import re
import sys
import uuid
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from openpyxl import Workbook

from core import excel, importers
from core.models import (AccountProfile, Build, Instance, Participant, Principal,
                         Release, Session, Study)

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import roster_import_client  # noqa: E402  (tools/ path above)
import phase03_designer_kit as designer_kit  # noqa: E402
import phase03_windows_kit as windows_kit  # noqa: E402

CREATOR_PASSWORD = 'synthetic-p03r06r-creator-password'
CREATOR_PASSWORD_2 = 'synthetic-p03r06r-creator-password-two'
VIEWER_PASSWORD = 'synthetic-p03r06r-viewer-password'
VIEW = 'study.view'
LEGACY_ID = 'p03r06r-legacy-id'
PREVIEW_ID_RE = re.compile(r'(?:data-preview-id="|name="preview_id" value=")([0-9a-fA-F-]{36})')

browser_only = pytest.mark.skipif(
    os.environ.get('GEP_T17_BROWSER') != '1',
    reason='Set GEP_T17_BROWSER=1 to run the isolated Chrome check')


# --- synthetic world ---------------------------------------------------------

def make_user(username, password, *, role='user', studies=None):
    user = get_user_model().objects.create_user(username, password=password)
    Principal.objects.create(user=user)
    AccountProfile.objects.create(user=user, role=role, policy_version=2,
                                  study_overrides=dict(studies or {}))
    return user


def signed_in(username, password):
    client = Client()
    assert client.post('/login', {'username': username, 'password': password}).status_code == 302
    return client


@pytest.fixture
def world(db):
    owner = make_user('p03r06r_owner', CREATOR_PASSWORD)
    instance = Instance.objects.create(instance_id=uuid.uuid4(), owner=owner,
                                       authorization_version=2)
    creator = make_user('p03r06r_creator', CREATOR_PASSWORD)
    client = signed_in(creator.username, CREATOR_PASSWORD)
    assert client.post('/', {'title': 'P03R06R study'}).status_code == 302
    study = Study.objects.get(title='P03R06R study')
    study.mode = 'password'
    study.recruitment = 'open'
    study.save(update_fields=['mode', 'recruitment'])
    build = Build.objects.create(study=study, descriptor={'platform': 'godot_web', 'version': 'v1'},
                                 digest='d' * 64)
    release = Release.objects.create(study=study, build=build, approved=True,
                                     config={'purpose': 'synthetic', 'mode': 'password'})
    return {'owner': owner, 'creator': creator, 'instance': instance, 'study': study,
            'build': build, 'release': release, 'client': client}


def codes_of(study):
    return set(Participant.objects.filter(study=study).values_list('code', flat=True))


def xlsx_bytes(rows, headers=excel.ROSTER_HEADERS):
    book = Workbook()
    sheet = book.active
    sheet.title = 'roster'
    sheet.append(list(headers))
    for row in rows:
        sheet.append(list(row))
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def roster_upload(client, study, payload):
    upload = SimpleUploadedFile('roster.xlsx', payload, content_type=(
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
    return client.post(f'/studies/{study.pk}/roster-import',
                       {'op': 'import_roster_preview', 'file': upload})


def roster_csv(client, study, text):
    return client.post(f'/studies/{study.pk}/roster-import',
                       {'op': 'import_roster_preview', 'roster': text})


def roster_commit(client, study, preview, password):
    return client.post(f'/studies/{study.pk}/roster-import',
                       {'op': 'import_roster_commit', 'preview_id': preview, 'password': password})


def preview_id(response):
    match = PREVIEW_ID_RE.search(response.content.decode())
    assert match, response.content[:400]
    return match.group(1)


def admit(client, world, code, password):
    body = {'operation_id': str(uuid.uuid4()), 'proof': 'p' * 48,
            'instance_id': str(world['instance'].instance_id), 'study_id': str(world['study'].pk),
            'release_id': str(world['release'].pk), 'build_id': str(world['build'].pk),
            'participant_code': code}
    if password is not None:
        body['password'] = password
    return client.post('/v1/participant/sessions', data=json.dumps(body),
                       content_type='application/json', HTTP_HOST='experiment.localhost')


# --- the legacy write entry is closed on every reachable path ----------------

def test_legacy_roster_write_is_refused_on_every_reachable_path(world):
    study, client = world['study'], world['client']
    before = codes_of(study)

    paths = (f'/studies/{study.pk}', f'/studies/{study.pk}/participation',
             f'/studies/{study.pk}/builds', f'/studies/{study.pk}/roster-import')
    formats = ({'roster_format': 'legacy_tab', 'roster': f'{LEGACY_ID}\tlegacy-password'},
               {'roster_format': 'csv', 'roster': f'{LEGACY_ID},legacy-password'},
               {'roster': LEGACY_ID})
    for path in paths:
        for fields in formats:
            refused = client.post(path, {'op': 'roster', **fields})
            assert refused.status_code == 409, (path, fields, refused.status_code)
            assert refused.json()['code'] == 'roster_import_moved'
            assert 'Location' not in refused
        html = client.post(path, {'op': 'roster', 'roster': LEGACY_ID}, HTTP_ACCEPT='text/html')
        assert html.status_code == 409
        assert html.context['error_code'] == 'roster_import_moved'
        assert 'Location' not in html
        body = html.content.decode()
        assert 'data-error="roster_import_moved"' in body
        assert '本次未写入任何更改' in body

    # Zero writes and no audit of a roster import.
    assert codes_of(study) == before
    assert not Participant.objects.filter(study=study, code=LEGACY_ID).exists()

    # The authorization negative is unchanged: an actor without this study's
    # configure scope is refused before any legacy branch.
    viewer = make_user('p03r06r_viewer', VIEWER_PASSWORD, studies={str(study.pk): [VIEW]})
    viewer_client = signed_in(viewer.username, VIEWER_PASSWORD)
    assert viewer_client.post(f'/studies/{study.pk}', {'op': 'roster', 'roster': 'late-1'},
                              HTTP_ACCEPT='text/html').status_code == 403
    assert codes_of(study) == before
    assert not Participant.objects.filter(study=study, code='late-1').exists()

    # The legacy GET entry stays authorized and redirects to the study page.
    redirect = client.get(f'/users/templates/roster?study={study.pk}')
    assert redirect.status_code == 302
    assert redirect.url == f'/studies/{study.pk}/roster-template'
    assert client.get(redirect.url).status_code == 200


# --- blank rows vs non-empty whitespace text ---------------------------------

def test_csv_whitespace_only_id_keeps_its_real_line_number(world):
    study = world['study']

    # A truly empty record is ignored; a whitespace-only record is an error with
    # its real source line number.
    preview = importers.preview_roster(world['creator'], study, text='ok-1,1\n\n   ,   \nok-2,2')
    assert [(error['row'], error['code']) for error in preview.errors] == [(3, 'id_whitespace')]
    assert [row['code'] for row in preview.staged['rows']] == ['ok-1', 'ok-2']

    # An empty ID with another valid field is refused instead of being ignored.
    empty = importers.preview_roster(world['creator'], study, text=',x\nok-3,3')
    assert [(error['row'], error['code']) for error in empty.errors] == [(1, 'id')]

    # A source with no text at all is refused (nothing to import), never a
    # silent success.
    blank = world['client'].post(f'/studies/{study.pk}/roster-import',
                                 {'op': 'import_roster_preview', 'roster': '\n\n'})
    assert blank.status_code == 400
    assert 'data-error="roster_empty"' in blank.content.decode()
    assert codes_of(study) == set()


def test_xlsx_whitespace_only_id_keeps_its_real_row_number(world):
    study = world['study']
    payload = xlsx_bytes([('ok-1', '1'), (None, None), ('   ', '   '), (None, 'x')])
    preview = importers.preview_roster(world['creator'], study,
                                       raw=payload)
    # Source rows: 2 ok, 3 empty (ignored), 4 whitespace ID, 5 empty ID with a
    # password.
    assert [(error['row'], error['code']) for error in preview.errors] == [(4, 'id_whitespace'),
                                                                           (5, 'id')]
    assert [row['code'] for row in preview.staged['rows']] == ['ok-1']


def test_mixed_batches_write_nothing_and_whitespace_passwords_are_kept(world):
    study, client = world['study'], world['client']

    # A mixed CSV batch refuses the whole commit with zero writes.
    mixed = roster_csv(client, study, 'mix-1,1\n   ,   \nmix-2,2')
    assert mixed.status_code == 200
    assert 'data-preview-error="id_whitespace"' in mixed.content.decode()
    assert 'data-roster-confirm' not in mixed.content.decode()
    assert roster_commit(client, study, preview_id(mixed), CREATOR_PASSWORD).status_code == 409
    assert codes_of(study) == set()

    # The same batch through XLSX, with the same zero-write outcome.
    book = roster_upload(client, study, xlsx_bytes([('mix-3', '3'), ('   ', '   ')]))
    assert 'data-preview-error="id_whitespace"' in book.content.decode()
    assert 'data-roster-confirm' not in book.content.decode()
    assert roster_commit(client, study, preview_id(book), CREATOR_PASSWORD).status_code == 409
    assert codes_of(study) == set()

    # A non-empty whitespace password with a valid ID is kept exactly as written
    # and admits exactly as written; the trimmed value never works.
    spaced = roster_csv(client, study, 'ws-pass,   ')
    assert spaced.status_code == 200 and '追加 1 个' in spaced.content.decode()
    assert roster_commit(client, study, preview_id(spaced), CREATOR_PASSWORD).status_code == 200
    assert check_password('   ', Participant.objects.get(study=study, code='ws-pass').password_hash)
    anonymous = Client()
    assert admit(anonymous, world, 'ws-pass', '   ').status_code == 200
    assert admit(anonymous, world, 'ws-pass', '').status_code == 403
    assert Session.objects.filter(participant__code='ws-pass').count() == 1
    assert codes_of(study) == {'ws-pass'}


# --- every new write needs the preview and the current own password ----------

def test_new_writes_require_preview_and_current_password(world):
    study, client, creator = world['study'], world['client'], world['creator']
    before = codes_of(study)

    # No preview and an unknown preview are both refused with zero writes.
    missing = client.post(f'/studies/{study.pk}/roster-import',
                          {'op': 'import_roster_commit', 'password': CREATOR_PASSWORD})
    assert missing.status_code == 400 and 'data-error="preview_required"' in missing.content.decode()
    unknown = roster_commit(client, study, str(uuid.uuid4()), CREATOR_PASSWORD)
    assert unknown.status_code == 404 and 'data-error="preview_invalid"' in unknown.content.decode()
    assert codes_of(study) == before

    # The preview alone never writes; the confirmation must use the actor's
    # current own password.
    preview = preview_id(roster_csv(client, study, 'reauth-1,x'))
    assert codes_of(study) == before
    wrong = roster_commit(client, study, preview, 'not-the-current-password')
    assert wrong.status_code == 403 and 'data-error="reauth_failed"' in wrong.content.decode()
    assert codes_of(study) == before

    # A password change invalidates the old password (Django also rotates the
    # session's auth hash); a fresh sign-in with the current password still
    # commits the very same preview.
    creator.set_password(CREATOR_PASSWORD_2)
    creator.save(update_fields=['password'])
    current_client = signed_in(creator.username, CREATOR_PASSWORD_2)
    stale = roster_commit(current_client, study, preview, CREATOR_PASSWORD)
    assert stale.status_code == 403 and codes_of(study) == before
    done = roster_commit(current_client, study, preview, CREATOR_PASSWORD_2)
    assert done.status_code == 200 and '名单导入完成' in done.content.decode()
    assert codes_of(study) == {'reauth-1'}


# --- the affected tools go through the real HTTP entry -----------------------

def test_shared_tool_helper_imports_over_real_http(live_server, world, evidence):
    study = world['study']
    port = int(live_server.url.rsplit(':', 1)[-1])
    http = windows_kit.HttpClient(port)
    http.login('p03r06r_creator', CREATOR_PASSWORD)

    added = roster_import_client.preview_and_commit(http, str(study.pk), 'http-1,http-pass',
                                                    CREATOR_PASSWORD)
    assert added == 1
    assert Participant.objects.filter(study=study, code='http-1').exists()
    assert check_password('http-pass', Participant.objects.get(study=study, code='http-1').password_hash)
    assert admit(Client(), world, 'http-1', 'http-pass').status_code == 200

    # A wrong confirmation password is refused by the real server with zero
    # writes, and the helper reports it instead of a fake success.
    with pytest.raises(roster_import_client.RosterImportError) as refused:
        roster_import_client.preview_and_commit(http, str(study.pk), 'http-2,http-pass', 'wrong-password')
    assert '403' in str(refused.value)
    assert not Participant.objects.filter(study=study, code='http-2').exists()

    # The legacy write entry is refused over the same real HTTP session.
    status, _payload, _ = http.post_form(f'/studies/{study.pk}',
                                         [('op', 'roster'), ('roster_format', 'legacy_tab'),
                                          ('roster', 'http-legacy\tx')], expect_redirect=False)
    assert status == 409
    assert not Participant.objects.filter(study=study, code='http-legacy').exists()

    evidence('tool_http_import.json', {
        'helper_added': added, 'admitted_http_1': True, 'wrong_password_refused': True,
        'legacy_http_status': status, 'codes': sorted(codes_of(study))})


def test_affected_kits_route_roster_creation_through_the_shared_helper():
    # Both kits delegate to the one shared HTTP helper...
    assert designer_kit.preview_and_commit is roster_import_client.preview_and_commit
    assert windows_kit.preview_and_commit is roster_import_client.preview_and_commit
    # ...and the legacy direct-write call is gone from their sources.
    for module in (designer_kit, windows_kit):
        source = Path(module.__file__).read_text(encoding='utf-8')
        assert '"op", "roster"' not in source
        assert "'op', 'roster'" not in source
        assert 'legacy_tab' not in source
    # The helper itself never writes the database or re-implements server rules:
    # it only speaks the public study-page HTTP entry.
    helper = Path(roster_import_client.__file__).read_text(encoding='utf-8')
    assert 'sqlite3' not in helper and 'Participant' not in helper
    assert roster_import_client.PREVIEW_OP == 'import_roster_preview'
    assert roster_import_client.COMMIT_OP == 'import_roster_commit'


# --- real Chrome: the closed legacy entry and the current entry together -----

@browser_only
def test_actual_chrome_legacy_write_refused_and_creator_flow(
        live_server, world, evidence, evidence_root, run_chrome_tokens):
    study, creator = world['study'], world['creator']
    env = dict(os.environ, GEP_TEST_BASE=live_server.url, GEP_CREATOR_PASSWORD=CREATOR_PASSWORD,
               GEP_STUDY=str(study.pk), GEP_EVIDENCE=str(evidence_root))
    result, reported = run_chrome_tokens(SCRIPT, env, timeout=300)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert reported == []
    observations = json.loads(re.search(r'P03R06R_CHROME (\{.*\})', result.stdout).group(1))
    assert observations['page_errors'] == []
    assert observations['preview_forms'] is True
    assert observations['legacy_form_gone'] is True
    assert observations['legacy_refused'] is True
    assert observations['no_legacy_notice'] is True
    assert observations['preview_new_ids'] is True
    assert observations['commit_notice'] is True
    evidence('chrome_legacy_and_creator_flow.json', observations)

    # Server truth: only the confirmed row exists, the legacy ID never did.
    assert codes_of(study) == {'browser-1'}
    assert check_password('1', Participant.objects.get(study=study, code='browser-1').password_hash)
    assert not Participant.objects.filter(study=study, code='legacy-browser-id').exists()


SCRIPT = r'''
import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.GEP_TEST_BASE, password=process.env.GEP_CREATOR_PASSWORD;
const study=process.env.GEP_STUDY, evidence=process.env.GEP_EVIDENCE;
const observations={},errors=[];
try {
  const ctx=await browser.newContext({viewport:{width:1366,height:900}});
  const page=await ctx.newPage();
  page.on('pageerror',e=>errors.push('legacy:'+e.message));
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill('p03r06r_creator');
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button',{name:'登录'}).click();
  await page.waitForURL(base+'/');
  await page.goto(base+'/studies/'+study+'/participation');
  await page.waitForLoadState('load');
  observations.preview_forms=(await page.locator('[data-roster-xlsx-form]').count())===1
    && (await page.locator('[data-roster-csv-form]').count())===1;
  observations.legacy_form_gone=(await page.locator('[data-roster-import]').count())===0;

  // An old bookmarked page still posts op=roster; the server must refuse it in
  // the real browser and never write.
  await Promise.all([
    page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/studies/')),
    page.evaluate((studyId)=>{
      const token=document.querySelector('[name=csrfmiddlewaretoken]').value;
      const form=document.createElement('form');
      form.method='post'; form.action='/studies/'+studyId;
      for(const [name,value] of [['csrfmiddlewaretoken',token],['op','roster'],
                                 ['roster_format','legacy_tab'],['roster','legacy-browser-id\tlegacy-browser-pass']]){
        const input=document.createElement('input'); input.type='hidden'; input.name=name; input.value=value;
        form.appendChild(input);
      }
      document.body.appendChild(form); form.submit();
    }, study)
  ]);
  await page.waitForLoadState('load');
  observations.legacy_refused = await page.locator('[data-error="roster_import_moved"]')
    .waitFor({state:'attached',timeout:15000}).then(()=>true,()=>false);
  observations.no_legacy_notice=(await page.locator('[data-notice="1"]').count())===0;
  await page.screenshot({path:evidence+'/legacy_refused_zh.png'});

  // The current entry still works in the same session.
  await page.goto(base+'/studies/'+study+'/participation');
  await page.locator('[data-roster-csv-form] textarea').fill('browser-1,1');
  await page.locator('[data-roster-csv-form] button').click();
  await page.waitForLoadState('load');
  await expect(page.locator('[data-preview="roster_import"]')).toBeVisible();
  observations.preview_new_ids=(await page.locator('[data-preview="roster_import"]').innerText()).includes('追加 1 个');
  await page.locator('[data-roster-confirm] [name=password]').fill(password);
  await page.locator('[data-roster-confirm] button').click();
  await page.waitForLoadState('load');
  observations.commit_notice=(await page.locator('[data-notice="1"]').innerText()).includes('名单导入完成');
} finally {await browser.close();}
observations.page_errors=errors;
console.log('P03R06R_CHROME '+JSON.stringify(observations));
'''
