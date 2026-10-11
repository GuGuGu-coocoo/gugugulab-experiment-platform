// Real GUI / Godot Web / IndexedDB / authorized export engineering check.
// Always a fresh Chrome process and ephemeral contexts; no user's browser profile.
import {chromium} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const [command, jobFile] = process.argv.slice(2);
const job = JSON.parse(fs.readFileSync(jobFile, 'utf8'));
const report = {checks: [], failures: [], page_errors: [], evidence: {}};
const check = (ok, name, detail = null) => {
  const row = {name, ok: !!ok, detail}; report.checks.push(row);
  if (!ok) report.failures.push(row);
};
const save = (name, object) => fs.writeFileSync(path.join(job.evidence_dir, name), JSON.stringify(object, null, 2));
const browser = await chromium.launch({channel: 'chrome', headless: true,
  args: ['--host-resolver-rules=MAP admin.localhost 127.0.0.1']});
const adminContext = await browser.newContext({viewport: {width: 1440, height: 1000}, acceptDownloads: true});
const admin = await adminContext.newPage();
admin.on('pageerror', error => report.page_errors.push({title: 'admin', message: error.message}));

async function login() {
  await admin.goto(job.admin + '/login');
  await admin.locator('[name=username]').fill(job.credentials.username);
  await admin.locator('[name=password]').fill(job.credentials.password);
  await admin.getByRole('button', {name: '登录', exact: true}).click();
  await admin.waitForURL(url => !url.pathname.startsWith('/login'));
}
async function createStudy(title) {
  await admin.goto(job.admin + '/');
  await admin.locator('[name=title]').fill(title);
  await admin.getByRole('button', {name: '创建', exact: true}).click();
  const url = admin.url();
  if (!/\/studies\/[0-9a-f-]{36}$/.test(url)) throw new Error('Study GUI did not create a study');
  return url;
}
async function recruit(study, release) {
  await admin.goto(study + '/recruitment');
  await admin.locator(`[data-release-option="${release}"]`).check();
  await admin.getByRole('button', {name: '设为当前发行', exact: true}).click();
  await Promise.all([
    admin.waitForResponse(r => r.request().method() === 'POST' && r.url().includes('/studies/')),
    admin.locator('[name=state]').selectOption('open'),
  ]);
  await admin.waitForLoadState('load');
}
async function safeRows(page) {
  return page.evaluate(() => new Promise((resolve, reject) => {
    const request = indexedDB.open('gec-1');
    request.onerror = () => reject(new Error('IndexedDB open failed'));
    request.onsuccess = () => {
      const db = request.result, tx = db.transaction('sessions');
      const all = tx.objectStore('sessions').getAll();
      tx.oncomplete = () => {
        resolve(all.result.map(s => ({id: s.id, kind: s.kind, records: s.records ?? [],
          pending: s.pending ?? [], completion: s.completion ?? null,
          checkpoint: s.checkpoint ?? null, complete_ack: !!s.complete_ack,
          state: s.state ?? null, front_locked: !!s.front_locked})));
        db.close();
      };
      tx.onerror = () => reject(new Error('IndexedDB transaction failed'));
    };
  }));
}
async function waitForGame(page, title) {
  page.on('pageerror', error => report.page_errors.push({title, message: error.message}));
  // Godot renders from requestAnimationFrame. An inactive preview tab can be
  // suspended by Chrome, so make the actual participant surface foreground.
  await page.bringToFront();
  await page.evaluate(() => {
    globalThis.__mossFrameProbe = 0;
    const tick = () => { globalThis.__mossFrameProbe += 1; requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
  });
  await page.waitForFunction(() => globalThis.GECBridge !== undefined, null, {timeout: 90000});
  await page.locator('#gec-start').waitFor({state: 'visible', timeout: 90000});
  await page.screenshot({path: path.join(job.evidence_dir, title + '_entry.png')});
  await page.locator('#gec-start').click();
  await page.waitForFunction(() => document.getElementById('gec-shell')?.style.display === 'none', null, {timeout: 30000});
  // Real keyboard input verifies that retiring the shell returns focus to the
  // game's canvas. Do not call gameplay functions or inject telemetry.
  await page.keyboard.down('j'); await page.waitForTimeout(120); await page.keyboard.up('j');
  await page.waitForTimeout(450);
  await page.keyboard.down('k'); await page.waitForTimeout(1100); await page.keyboard.up('k');
  await page.waitForTimeout(700);
  await page.keyboard.down('l'); await page.waitForTimeout(120); await page.keyboard.up('l');
  await page.waitForTimeout(200);
  await page.screenshot({path: path.join(job.evidence_dir, title + '_battle.png')});
  // Inspect the actual cookie jar, including HttpOnly cookies; document.cookie
  // alone cannot establish that a management session was not shared.
  const cookies = await page.context().cookies(page.url());
  check(!cookies.some(cookie => cookie.name === 'gep_admin'), title + ': no management cookie on experiment origin');
}
async function waitForLocalFinish(page) {
  // Do not hand an IndexedDB Promise to waitForFunction's expression poller:
  // it can treat the Promise object as truthy before the transaction finishes.
  // Await the actual read on the Node side, then inspect the durable boundary.
  const deadline = Date.now() + 180000;
  let nextEvidence = Date.now() + 20000;
  while (Date.now() < deadline) {
    const rows = await safeRows(page);
    if (rows.some(s => s.completion && s.records.some(e => e.payload?.kind === 'round_summary'))) return;
    const localFault = await page.evaluate(() => {
      const panel = document.getElementById('gec-shell');
      return panel?.style.display !== 'none' && /本地保存失败|Local save failed/.test(panel?.innerText ?? '') ? panel.innerText : '';
    });
    if (localFault) {
      save('battle_save_failure.json', {message: localFault, rows});
      await page.screenshot({path: path.join(job.evidence_dir, 'battle_save_failure.png')});
      throw new Error('Actual game paused for local save failure: ' + localFault.split('\n')[0]);
    }
    if (Date.now() >= nextEvidence) {
      save('battle_progress.json', {rows, browser: await page.evaluate(() => ({
        frames: globalThis.__mossFrameProbe, visibility: document.visibilityState,
        bridge: globalThis.GECBridge.status(), shell: document.getElementById('gec-shell')?.innerText,
      }))});
      await page.screenshot({path: path.join(job.evidence_dir, 'battle_progress.png')});
      nextEvidence += 20000;
    }
    await page.waitForTimeout(250);
  }
  await page.screenshot({path: path.join(job.evidence_dir, 'battle_timeout.png')});
  save('battle_timeout_indexeddb.json', await safeRows(page));
  throw new Error('Real battle did not durably finish within 180 seconds');
}
async function exportStudy(study, filename) {
  await admin.goto(study + '/exports');
  await admin.locator('[data-export-submit]').click();
  const link = admin.locator('#export-result [data-created-format="jsonl"]');
  await link.waitFor({timeout: 45000});
  const response = await adminContext.request.get(new URL(await link.getAttribute('href'), admin.url()).href);
  if (response.status() !== 200) throw new Error('Authorized JSONL download refused: ' + response.status());
  const bytes = await response.body();
  fs.writeFileSync(path.join(job.evidence_dir, filename), bytes);
  return bytes.toString('utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
}
async function prepareNative() {
    const nativeStudy = await createStudy('Moss Duel macOS engineering ' + Date.now());
    await admin.goto(nativeStudy + '/builds');
    await admin.locator('[name=descriptor]').fill(fs.readFileSync(job.native_descriptor, 'utf8'));
    await admin.getByRole('button', {name: '登记不可变构建', exact: true}).click();
    await admin.locator('#native-package-upload [name=package]').setInputFiles(job.native_zip);
    await admin.locator('#native-package-upload').getByRole('button', {name: '上传并验证', exact: true}).click();
    await admin.locator('article p', {hasText: '已绑定程序包'}).first().waitFor({timeout: 90000});
    await admin.getByRole('button', {name: '批准合成发行', exact: true}).click();
    const artifact = admin.locator('[data-artifact]').first();
    await artifact.waitFor({timeout: 180000});
    const artifactURL = await artifact.getAttribute('href');
    const nativeRelease = artifactURL.split('/')[2];
    const [download] = await Promise.all([admin.waitForEvent('download'), artifact.click()]);
    await download.saveAs(path.join(job.evidence_dir, 'native_complete.zip'));
    await recruit(nativeStudy, nativeRelease);
    report.evidence.native = {study_url: nativeStudy, release_id: nativeRelease};
    check(true, 'new real macOS program registered, uploaded, frozen and downloaded through GEP GUI');
}

try {
  await login();
  if (command === 'export') {
    report.evidence.native_export = await exportStudy(job.native_study, 'native_authorized.jsonl');
  } else if (command === 'native-prepare') {
    await prepareNative();
  } else {
    const webStudy = await createStudy('Moss Duel Web engineering ' + Date.now());
    await admin.goto(webStudy + '/builds');
    await admin.locator('#package-upload [name=package]').setInputFiles(job.web_zip);
    await admin.locator('#package-upload').getByRole('button', {name: '上传并验证', exact: true}).click();
    await admin.getByRole('button', {name: '隔离预览（仅本地保存）', exact: true}).waitFor({timeout: 90000});
    check(true, 'real GEP GUI validated newly exported Godot Web program');
    const [preview] = await Promise.all([adminContext.waitForEvent('page'),
      admin.getByRole('button', {name: '隔离预览（仅本地保存）', exact: true}).click()]);
    let previewParticipantRequests = 0;
    preview.on('request', r => {if (r.url().includes('/v1/participant/')) previewParticipantRequests += 1;});
    await waitForGame(preview, 'preview');
    await waitForLocalFinish(preview);
    const previewRows = await safeRows(preview);
    save('preview_indexeddb.json', previewRows);
    const previewLocal = previewRows.find(s => s.kind === 'local' && s.completion);
    check(!!previewLocal && previewLocal.records.length > 2, 'preview real game records persist in IndexedDB');
    check(['light_attack', 'heavy_charged_attack', 'parry_attempt'].every(kind => previewLocal?.records.some(e => e.payload.kind === kind)),
      'actual keyboard light/heavy/parry work after GEC returns canvas focus');
    check(previewParticipantRequests === 0 && !previewLocal?.complete_ack, 'preview has no participant HTTP and no fake server ACK');
    await preview.screenshot({path: path.join(job.evidence_dir, 'preview_finish.png')});
    await preview.close();

    await admin.getByRole('button', {name: '批准合成发行', exact: true}).click();
    const runLink = await admin.getByRole('link', {name: '打开参与入口', exact: true}).getAttribute('href');
    const release = runLink.match(/\/run\/([0-9a-f-]{36})\//)?.[1];
    if (!release) throw new Error('No concrete release resource URL after approval');
    await recruit(webStudy, release);
    const participant = await browser.newContext({viewport: {width: 1360, height: 850}});
    const run = await participant.newPage();
    await run.goto(runLink);
    await waitForGame(run, 'published');
    // Actual browser network outage after online admission, while the unchanged
    // game continues. All held records are read before restoring network/ACK.
    await participant.setOffline(true);
    await waitForLocalFinish(run);
    const beforeRows = await safeRows(run);
    const local = beforeRows.find(s => s.kind === 'session' && s.completion);
    if (!local) throw new Error('No durable completed Web session during outage');
    save('web_before_upload.json', local);
    check(local.pending.length > 0 && !local.complete_ack, 'offline battle finished with genuine local pending records');
    check(local.checkpoint === null, 'no undeclared battle checkpoint was fabricated');
    check(local.records.filter(e => e.payload.kind === 'round_summary').length === 1, 'exactly one round summary before upload');
    check(['light_attack', 'heavy_charged_attack', 'parry_attempt'].every(kind => local.records.some(e => e.payload.kind === kind)),
      'published canvas receives real keyboard actions');
    await run.screenshot({path: path.join(job.evidence_dir, 'published_waiting.png')});
    await participant.setOffline(false);
    await run.waitForFunction(() => globalThis.GECBridge.status().state === 'remote_acknowledged', null, {timeout: 120000});
    const afterRows = await safeRows(run);
    save('web_after_ack.json', afterRows);
    const cleaned = afterRows.find(s => s.id === local.id);
    check(cleaned?.kind === 'cleaned' && cleaned.records.length === 0 && cleaned.pending.length === 0, 'ACK persisted and completed original records cleaned to tombstone');
    await run.waitForFunction(() => /uploaded and confirmed|上传并确认/.test(document.getElementById('gec-shell')?.innerText ?? ''), null, {timeout: 10000});
    await run.screenshot({path: path.join(job.evidence_dir, 'published_received.png')});
    const webExport = await exportStudy(webStudy, 'web_authorized.jsonl');
    report.evidence.web = {study_url: webStudy, release_id: release, session_id: local.id, local_records: local.records.length,
      export_records: webExport.length};
    await participant.close();

    await prepareNative();
  }
  check(report.page_errors.length === 0, command === 'native-prepare' ? 'native preparation GUI has no unhandled page errors' : 'no unhandled Godot Web page errors', report.page_errors);
} catch (error) {
  check(false, 'driver exception', error.message);
  await admin.screenshot({path: path.join(job.evidence_dir, 'driver_failure.png')}).catch(() => {});
} finally {
  save(command === 'export' ? 'native_export_driver.json' : 'browser_report.json', report);
  await browser.close();
}
console.log(JSON.stringify({checks: report.checks.length, failures: report.failures, evidence: report.evidence}));
if (report.failures.length) process.exitCode = 1;
