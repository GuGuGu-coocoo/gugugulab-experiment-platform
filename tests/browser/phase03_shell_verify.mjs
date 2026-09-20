#!/usr/bin/env node
/* Phase 03 shell verification browser driver.
 *
 * Owned and invoked by tools/phase03_verify_shell.py. It drives the real
 * researcher GUI and the real exported Godot Web build through the reusable GEC
 * shell, using a temporary instance that the Python verifier owns. Results are
 * printed as one JSON document on stdout; evidence files stay in the run root.
 */
import {chromium} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const [command, jobPath] = process.argv.slice(2);
const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
const HOST_RULES = '--host-resolver-rules=MAP admin.localhost 127.0.0.1, MAP experiment.localhost 127.0.0.1';

const results = {checks: [], failures: [], evidence: {}};
function check(condition, label, detail) {
  const entry = {label, ok: !!condition, detail: detail ?? null};
  results.checks.push(entry);
  if (!condition) results.failures.push(entry);
  return !!condition;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function poll(fn, timeout = 20000, interval = 250) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    last = await fn();
    if (last) return last;
    await sleep(interval);
  }
  throw new Error('poll timeout, last=' + JSON.stringify(last));
}
const store = page => page.evaluate(() => new Promise((resolve, reject) => {
  const r = indexedDB.open('gec-1');
  r.onsuccess = () => {
    const db = r.result;
    const tx = db.transaction('sessions');
    const q = tx.objectStore('sessions').getAll();
    tx.oncomplete = () => { resolve(q.result); db.close(); };
    tx.onerror = () => reject(tx.error);
  };
  r.onerror = () => reject(r.error);
}));

async function launch() {
  const browser = await chromium.launch({channel: 'chrome', headless: true, args: [HOST_RULES]});
  const context = await browser.newContext({viewport: {width: 1280, height: 720}});
  return {browser, context};
}

async function login(page) {
  await page.goto(job.admin_url + '/login');
  await page.locator('[name=username]').fill(job.credentials.username);
  await page.locator('[name=password]').fill(job.credentials.password);
  await page.getByRole('button', {name: '登录', exact: true}).click();
  await page.waitForURL(url => !url.pathname.startsWith('/login'));
}

async function releaseMap(page) {
  const map = {};
  for (const radio of await page.locator('[data-release-option]').all()) {
    const value = await radio.getAttribute('data-release-option');
    if (!value) continue;
    const label = await radio.locator('xpath=..').innerText();
    if (label.includes('godot_web')) map.web = value;
    if (label.includes('macos_arm64')) map.native = value;
  }
  return map;
}

async function createStudy(page, spec) {
  await page.goto(job.admin_url + '/');
  await page.locator('[name=title]').fill(spec.title);
  await page.getByRole('button', {name: '创建', exact: true}).click();
  // New GUI contract (P0307 module pages): policy, roster, builds, recruitment
  // and current release live on separate module pages of the same study.
  const study_url = page.url();
  const study_id = study_url.split('/').pop();
  await page.goto(study_url + '/participation');
  await page.locator('[name=mode]').selectOption(spec.mode);
  await page.locator('[name=max_sessions]').fill(String(spec.max_sessions));
  await page.getByRole('button', {name: '保存政策'}).click();
  if (spec.roster) {
    await page.locator('[name=roster]').fill(spec.roster);
    await page.getByRole('button', {name: '导入名单'}).click();
    await page.locator('.notice').waitFor({timeout: 15000});
  }
  await page.goto(study_url + '/builds');
  if (spec.upload_web) {
    // The builds page carries a native archive form and a web form; both inputs
    // are named "package", so the web form is addressed by its own id.
    await page.locator('#package-upload input[name=package]').setInputFiles(spec.upload_web);
    await page.locator('#package-upload').getByRole('button', {name: '上传并验证'}).click();
    await page.getByRole('button', {name: '隔离预览（仅本地保存）'}).waitFor({timeout: 120000});
  }
  if (spec.native_descriptor) {
    await page.locator('[name=descriptor]').fill(fs.readFileSync(spec.native_descriptor, 'utf8'));
    await page.getByRole('button', {name: '登记不可变构建'}).click();
  }
  for (const platform of spec.approve) {
    await page.locator('article', {hasText: platform}).getByRole('button', {name: '批准合成发行'}).click();
    await page.waitForLoadState('load');
  }
  await page.goto(study_url + '/recruitment');
  const releases = await releaseMap(page);
  if (spec.current === 'web') {
    await page.locator(`[data-release-option="${releases.web}"]`).check();
    await page.getByRole('button', {name: '设为当前发行'}).click();
    await page.waitForLoadState('load');
  }
  await Promise.all([
    page.waitForResponse(response => response.url().includes('/studies/') && response.request().method() === 'POST'),
    page.locator('[name=state]').selectOption('open'),
  ]);
  await page.waitForLoadState('load');
  const config_response = await page.context().request.get(`${job.admin_url}/releases/${releases.native || releases.web}/config`);
  const connection = await config_response.json();
  const config_path = path.join(job.run_dir, `connection_${spec.key}.json`);
  fs.writeFileSync(config_path, JSON.stringify(connection));
  return {study_id, study_url, web_release_id: releases.web, native_release_id: releases.native, config_path, connection};
}

async function waitReady(page, url) {
  page.on('console', message => { if (message.type() === 'error') results.evidence.console = [...(results.evidence.console || []), message.text()].slice(-20); });
  await page.goto(url);
  await page.waitForFunction(() => globalThis.GECBridge !== undefined, null, {timeout: 30000});
  await page.locator('#gec-shell').waitFor({state: 'visible', timeout: 30000});
  await poll(async () => (await page.evaluate(() => globalThis.GECBridge.status().state)) === 'ready', 20000);
}

async function waitReason(page, reasons, timeout = 15000) {
  const wanted = Array.isArray(reasons) ? reasons : [reasons];
  return poll(async () => wanted.includes(await page.evaluate(() => document.getElementById('gec-shell-status')?.dataset.reason ?? '')), timeout);
}
async function waitTrial(page, trial, timeout = 20000) {
  try {
    await poll(async () => {
      const label = await page.locator('#canvas').getAttribute('aria-label');
      return label && (label.includes(`Trial ${trial}`) || label.includes(`第 ${trial} 次`));
    }, timeout);
  } catch (error) {
    const label = await page.locator('#canvas').getAttribute('aria-label').catch(() => null);
    throw new Error(`trial ${trial} was not announced, last label=${JSON.stringify(label)}: ${error.message}`);
  }
}
async function sessionRecord(page) {
  return (await store(page)).find(s => s.kind === 'session') ?? null;
}
async function liveSessions(page) {
  return (await store(page)).filter(s => s.kind === 'session');
}
async function sessionById(page, id) {
  return (await store(page)).find(s => s.id === id) ?? null;
}
async function waitRecords(page, id, count, timeout = 25000) {
  return poll(async () => {
    const s = (await store(page)).find(x => x.id === id);
    return s && s.kind === 'session' && s.records.length === count ? {records: s.records, segments: s.segments, pending: s.pending} : null;
  }, timeout);
}
async function waitCleaned(page, id, timeout = 25000) {
  return poll(async () => (await store(page)).find(s => s.id === id && s.kind === 'cleaned'), timeout);
}
async function waitFinished(page, timeout = 25000) {
  await poll(async () => (await page.evaluate(() => globalThis.GECBridge.status().state)) === 'finished', timeout);
}
async function holdCompletion(page) {
  // Hold the completion ACK until the local store snapshot is taken, so the
  // pre-cleanup records are observable deterministically.
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let held = false;
  await page.route('**/completion', async route => {
    const response = await route.fetch();
    if (!held) { held = true; await gate; }
    await route.fulfill({response});
  });
  return release;
}
async function dropFirstAck(page) {
  let dropped = false;
  await page.route('**/event-batches', async route => {
    if (!dropped) { dropped = true; await route.fetch(); await route.abort(); }
    else await route.continue();
  });
  return () => dropped;
}
async function blockUploads(page) {
  // Keep one declared completion unacknowledged while the page is open, so a
  // completed-but-unreceived candidate survives a real close/reopen and can only
  // be continued through the explicit named recovery below.
  const abort = route => route.abort();
  await page.route('**/event-batches', abort);
  await page.route('**/completion', abort);
  return async () => { await page.unroute('**/event-batches', abort); await page.unroute('**/completion', abort); };
}
async function holdAdmission(page) {
  let release; const gate = new Promise(resolve => { release = resolve; });
  let held = false;
  await page.route(/\/v1\/participant\/sessions$/, async route => {
    if (!held) { held = true; await gate; }
    await route.continue();
  });
  return () => release();
}
async function issueTicket(page, study_url, session_id, button) {
  // New GUI contract (P0307 module pages): the recovery code and the one-time
  // permit are issued from the dedicated UUID form on the sessions module.
  await page.goto(study_url + '/sessions');
  const form = page.locator('form[data-recover-uuid-form="1"]');
  await form.locator('[name=session_id]').fill(session_id);
  await form.getByRole('button', {name: button}).click();
  const notice = await page.locator('.notice').textContent();
  const match = notice.match(/([A-Za-z0-9_-]{6,})\s*$/);
  if (!match) throw new Error(`ticket not found in notice for ${button}`);
  return match[1];
}

async function runSetup(page) {
  const studies = {};
  for (const spec of job.study_specs) studies[spec.key] = await createStudy(page, spec);
  return studies;
}

async function webAnonymous(page, context, study) {
  const url = `${job.experiment_url}/run/${study.web_release_id}/web/index.html`;
  await waitReady(page, url);
  check((await page.locator('#gec-input-code').count()) === 0, 'anonymous mode hides the ID field');
  check((await page.locator('#gec-input-password').count()) === 0, 'anonymous mode hides the password field');
  check((await page.locator('#gec-input-short-code').count()) === 1, 'anonymous mode keeps the six-digit panel');
  const ackDropped = await dropFirstAck(page);
  await page.click('#gec-start');
  await waitTrial(page, 1);
  await context.setOffline(true);
  await page.locator('#canvas').press('ArrowLeft');
  await waitTrial(page, 2);
  const offlineSession = await sessionRecord(page);
  check(offlineSession.pending.length > 0, 'offline local commit stays pending', offlineSession.pending.length);
  check(offlineSession.checkpoint.next_trial === 1, 'offline local commit keeps the first trial boundary');
  await context.setOffline(false);
  const releaseCompletion = await holdCompletion(page);
  await page.locator('#canvas').press('ArrowRight');
  const saved = await waitRecords(page, offlineSession.id, 4);
  releaseCompletion();
  await waitFinished(page);
  check(saved.records.length === 4, 'anonymous session committed four records', saved.records.length);
  await poll(() => ackDropped(), 20000);
  const cleaned = await waitCleaned(page, offlineSession.id);
  check(cleaned.kind === 'cleaned', 'anonymous session reached the cleaned tombstone');
  await page.screenshot({path: path.join(job.run_dir, 'web_anonymous.png')});
  return {session_id: offlineSession.id, records: saved.records, segments: saved.segments};
}

async function webIdError(page, study) {
  const url = `${job.experiment_url}/run/${study.web_release_id}/web/index.html`;
  await waitReady(page, url);
  check((await page.locator('#gec-input-code').count()) === 1, 'id mode shows the ID field');
  check((await page.locator('#gec-input-password').count()) === 0, 'id mode hides the password field');
  // The device already holds a cleaned tombstone of another study (anonymous).
  // A name with no matching candidate in this study must not be reported as an
  // already-uploaded session: hold the admission and read the untampered status.
  const initial = await page.locator('#gec-shell-status').textContent();
  const releaseAdmission = await holdAdmission(page);
  await page.locator('#gec-input-code').fill('unknown-' + Date.now());
  await page.click('#gec-start');
  await sleep(500);
  const held = await page.locator('#gec-shell-status').textContent();
  check(held === initial, 'unrelated tombstone never turns a missing candidate into an uploaded claim', held);
  releaseAdmission();
  await waitReason(page, ['admission_denied', 'http_403']);
  const after = (await store(page)).filter(s => s.kind === 'session');
  check(after.length === 0, 'rejected admission creates no local session', after.length);
  return {rejected: true};
}

async function webIdFull(page, study) {
  const url = `${job.experiment_url}/run/${study.web_release_id}/web/index.html`;
  await waitReady(page, url);
  await page.locator('#gec-input-code').fill(job.roster_id);
  await page.click('#gec-start');
  await waitTrial(page, 1);
  const writer = await page.context().newPage();
  await writer.goto(url);
  await writer.waitForFunction(() => globalThis.GECBridge && globalThis.GECBridge.prepare_error !== undefined, null, {timeout: 30000});
  check((await writer.evaluate(() => globalThis.GECBridge.prepare_error)) === 'writer_busy', 'second Web writer is refused while the first holds the lock');
  await writer.close();
  await page.bringToFront();
  await page.locator('#canvas').press('ArrowLeft');
  await waitTrial(page, 2);
  const saved = await sessionRecord(page);
  check(saved.checkpoint.next_trial === 1, 'id-mode session keeps the first trial boundary');
  const releaseCompletion = await holdCompletion(page);
  await page.locator('#canvas').press('ArrowRight');
  const committed = await waitRecords(page, saved.id, 4);
  releaseCompletion();
  await waitFinished(page);
  await waitCleaned(page, saved.id);
  check(committed.records.length === 4, 'id-mode session committed four records', committed.records.length);
  return {session_id: saved.id, records: committed.records, segments: committed.segments};
}

async function webPasswordFlows(page, context, admin, study) {
  const url = `${job.experiment_url}/run/${study.web_release_id}/web/index.html`;
  // 1. Partial session A: one committed trial boundary.
  await waitReady(page, url);
  await page.locator('#gec-input-code').fill(job.roster_id);
  await page.locator('#gec-input-password').fill(job.roster_password);
  await page.click('#gec-start');
  await waitTrial(page, 1);
  await page.locator('#canvas').press('ArrowLeft');
  await waitTrial(page, 2);
  const sessionA = await sessionRecord(page);
  check(sessionA.records.length === 2 && sessionA.checkpoint.next_trial === 1, 'partial session A committed one trial boundary', sessionA.records.length);
  // 2. Named continuation is offered, never automatic; choose a new session.
  await page.reload();
  await waitReady(page, url);
  await page.locator('#gec-input-code').fill(job.roster_id);
  await page.locator('#gec-input-password').fill(job.roster_password);
  await page.click('#gec-start');
  await page.locator('#gec-shell-confirm').waitFor({state: 'visible', timeout: 20000});
  const beforeConfirm = await page.evaluate(id => new Promise(resolve => {
    const r = indexedDB.open('gec-1');
    r.onsuccess = () => {
      const db = r.result; const tx = db.transaction('sessions'); const q = tx.objectStore('sessions').get(id);
      tx.oncomplete = () => { resolve(q.result); db.close(); };
    };
  }), sessionA.id);
  check(beforeConfirm.checkpoint.next_trial === 1 && beforeConfirm.segments.length === 1, 'named continuation does not resume before confirmation');
  await page.click('#gec-confirm-new');
  await waitTrial(page, 1);
  const sessionB = (await liveSessions(page)).find(s => s.id !== sessionA.id);
  check(!!sessionB && sessionB.id !== sessionA.id, 'starting a new session creates a different session');
  await page.locator('#canvas').press('ArrowLeft');
  await waitTrial(page, 2);
  const releaseB = await holdCompletion(page);
  await page.locator('#canvas').press('ArrowRight');
  await waitRecords(page, sessionB.id, 4);
  releaseB();
  await waitFinished(page);
  await waitCleaned(page, sessionB.id);
  // 3. The abandoned session is now front-locked; named recovery refuses it.
  await page.reload();
  await waitReady(page, url);
  await page.locator('#gec-input-code').fill(job.roster_id);
  await page.locator('#gec-input-password').fill(job.roster_password);
  await page.click('#gec-start');
  await waitReason(page, 'front_locked');
  const locked = await page.evaluate(id => new Promise(resolve => {
    const r = indexedDB.open('gec-1');
    r.onsuccess = () => {
      const db = r.result; const tx = db.transaction('sessions'); const q = tx.objectStore('sessions').get(id);
      tx.oncomplete = () => { resolve(q.result); db.close(); };
    };
  }), sessionA.id);
  check(locked.kind === 'session' && locked.front_locked === true, 'front-locked candidate stays locked after refusal');
  // 4. The researcher-issued six-digit code recovers the locked session explicitly.
  const code = await issueTicket(admin, study.study_url, sessionA.id, '签发六位恢复码');
  await page.reload();
  await waitReady(page, url);
  await page.locator('#gec-input-short-code').fill(code);
  await page.click('#gec-recover-code');
  await page.locator('#gec-shell-confirm').waitFor({state: 'visible', timeout: 20000});
  const beforeCode = await page.evaluate(id => new Promise(resolve => {
    const r = indexedDB.open('gec-1');
    r.onsuccess = () => {
      const db = r.result; const tx = db.transaction('sessions'); const q = tx.objectStore('sessions').get(id);
      tx.oncomplete = () => { resolve(q.result); db.close(); };
    };
  }), sessionA.id);
  check(beforeCode.checkpoint.next_trial === 1 && beforeCode.segments.length === 1, 'code redemption does not resume before confirmation');
  await page.click('#gec-confirm-continue');
  await waitTrial(page, 2);
  check((await sessionById(page, sessionA.id)).segments.length === 2, 'continued session opened a second segment');
  const releaseA = await holdCompletion(page);
  await page.locator('#canvas').press('ArrowRight');
  const resumed = await waitRecords(page, sessionA.id, 4);
  releaseA();
  await waitFinished(page);
  await waitCleaned(page, sessionA.id);
  check(resumed.records.length === 4 && resumed.segments.length === 2, 'code recovery keeps the original session with a second segment', {records: resumed.records.length, segments: resumed.segments.length});
  check(resumed.records.filter(e => e.payload?.trial_id === 't1').length === 1, 'recovered session never replays trial 1');
  // 5. A device whose candidates are all cleaned reports that no recovery is needed.
  await page.reload();
  await waitReady(page, url);
  await page.locator('#gec-input-code').fill(job.roster_id);
  await page.locator('#gec-input-password').fill(job.roster_password);
  await page.click('#gec-start');
  await waitTrial(page, 1);
  const sessionC = (await liveSessions(page)).find(s => s.id !== sessionA.id && s.id !== sessionB.id);
  check(!!sessionC, 'cleaned device state starts a fresh session');
  await page.locator('#canvas').press('ArrowLeft');
  await waitTrial(page, 2);
  const releaseC = await holdCompletion(page);
  await page.locator('#canvas').press('ArrowRight');
  await waitRecords(page, sessionC.id, 4);
  releaseC();
  await waitFinished(page);
  await waitCleaned(page, sessionC.id);
  await page.screenshot({path: path.join(job.run_dir, 'web_password.png')});
  const data_only = await webNamedDataOnly(context, page, url);
  return {session_a: sessionA.id, session_b: sessionB.id, session_c: sessionC.id, code: true,
          recovered: {session_id: sessionA.id, records: resumed.records, segments: resumed.segments},
          data_only};
}

async function webNamedDataOnly(context, page, url) {
  // A declared completion with no receipt: both upload paths keep failing, the
  // tab is really closed (releasing the single-writer lock) and a new one reopens
  // the same IndexedDB. Only the explicit named recovery may adopt that candidate,
  // and it recovers data only.
  await page.evaluate(() => globalThis.GECBridge.call('release-writer', 'close', []));
  await sleep(300);
  await page.close();
  let worker = await context.newPage();
  let unblock = await blockUploads(worker);
  await waitReady(worker, url);
  await worker.locator('#gec-input-code').fill(job.roster_id);
  await worker.locator('#gec-input-password').fill(job.roster_password);
  await worker.click('#gec-start');
  await waitTrial(worker, 1);
  await worker.locator('#canvas').press('ArrowLeft');
  await waitTrial(worker, 2);
  await worker.locator('#canvas').press('ArrowRight');
  const declared = await poll(async () => {
    const s = (await store(worker)).find(x => x.kind === 'session' && x.completion && x.pending.length === 4);
    return s && s.records.length === 4 ? s : null;
  }, 30000);
  check(declared.complete_ack == null, 'declared completion is not a received acknowledgement', declared.complete_ack);
  check(declared.segments.length === 1, 'completed candidate keeps its single segment', declared.segments.length);
  await worker.close();
  unblock = null;

  worker = await context.newPage();
  unblock = await blockUploads(worker);
  await waitReady(worker, url);
  const reopened = await sessionById(worker, declared.id);
  check(reopened && reopened.kind === 'session' && reopened.pending.length === 4 && reopened.complete_ack == null,
    'reopened IndexedDB still holds the unreceived completion', reopened && {pending: reopened.pending.length, ack: reopened.complete_ack});
  await worker.locator('#gec-input-code').fill(job.roster_id);
  await worker.locator('#gec-input-password').fill(job.roster_password);
  await worker.click('#gec-start');
  await worker.locator('#gec-shell-confirm').waitFor({state: 'visible', timeout: 20000});
  const confirmText = await worker.locator('#gec-shell-confirm p').textContent();
  check(/data|数据/i.test(confirmText), 'finished candidate asks for an explicit data-only confirmation', confirmText);
  const beforeConfirm = await sessionById(worker, declared.id);
  check(beforeConfirm.records.length === 4 && beforeConfirm.segments.length === 1, 'no candidate is adopted before the explicit confirmation');
  await unblock();
  await worker.click('#gec-confirm-continue');
  await sleep(1000);
  const label = await worker.locator('#canvas').getAttribute('aria-label');
  check(!/Trial [0-9]|第 [0-9]+ 次/.test(label || ''), 'data-only recovery never announces a trial', label);
  const cleaned = await waitCleaned(worker, declared.id, 40000);
  check(cleaned && cleaned.kind === 'cleaned' && cleaned.records === undefined, 'cleaned tombstone keeps no raw records', Object.keys(cleaned || {}));
  await poll(async () => (await worker.evaluate(() => globalThis.GECBridge.status().state)) === 'remote_acknowledged', 20000);
  const status = await worker.locator('#gec-shell-status').textContent();
  check(/uploaded|已上传/i.test(status), 'uploaded confirmation is announced after the data-only recovery', status);
  await worker.screenshot({path: path.join(job.run_dir, 'web_named_data_only.png')});
  await worker.close();
  return {session_id: declared.id, records: beforeConfirm.records, segments: beforeConfirm.segments};
}

async function exportJsonl(page, study, out) {
  // New GUI contract (P0307 module pages): exports live on the /exports module.
  await page.goto(study.study_url + '/exports');
  await page.locator('#export-form').getByRole('button', {name: '创建 JSONL 固定快照'}).click();
  const link = page.locator('#export-result').getByRole('link', {name: '下载 JSONL'});
  await link.waitFor({timeout: 30000});
  const [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
  await download.saveAs(out);
  return out;
}

async function main() {
  const {browser, context} = await launch();
  const page = await context.newPage();
  try {
    await login(page);
    if (command === 'all') {
      results.evidence.studies = await runSetup(page);
      const studies = results.evidence.studies;
      if (job.web_checks !== false) {
        results.evidence.anonymous = await webAnonymous(page, context, studies.anonymous);
        results.evidence.id_error = await webIdError(page, studies.id);
        results.evidence.id = await webIdFull(page, studies.id);
        const admin = await context.newPage();
        results.evidence.password = await webPasswordFlows(page, context, admin, studies.password);
        await admin.close();
      }
    } else if (command === 'issue-code') {
      results.evidence.code = await issueTicket(page, job.study_url, job.session_id, job.button || '签发六位恢复码');
    } else if (command === 'export') {
      results.evidence.export = await exportJsonl(page, job, job.out);
    } else {
      throw new Error('unknown command ' + command);
    }
  } catch (error) {
    results.error = String(error && error.stack || error);
    try { results.evidence.page_html = (await page.content()).slice(0, 30000); } catch {}
    try { results.evidence.canvas_label = await page.locator('#canvas').getAttribute('aria-label'); } catch {}
    try { results.evidence.bridge_status = await page.evaluate(() => globalThis.GECBridge.status()); } catch {}
    try { results.evidence.store = await store(page); } catch {}
    try { results.evidence.confirm_dump = await page.evaluate(() => { const el = document.getElementById('gec-shell-confirm'); return el ? {style: el.getAttribute('style'), display: getComputedStyle(el).display, rect: el.getBoundingClientRect().toJSON(), status: document.getElementById('gec-shell-status')?.textContent, reason: document.getElementById('gec-shell-status')?.dataset.reason} : null; }); } catch {}
    try { await page.screenshot({path: path.join(job.run_dir, 'error.png')}); } catch {}
  } finally {
    await context.close();
    await browser.close();
  }
  process.stdout.write(JSON.stringify(results));
  process.exit(results.error || results.failures.length ? 1 : 0);
}
await main();
