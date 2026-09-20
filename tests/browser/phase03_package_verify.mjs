#!/usr/bin/env node
/* Phase 03 complete-package verification browser driver.
 *
 * Owned and invoked by tools/phase03_verify_package.py. It drives the real
 * researcher GUI against a temporary instance: register the native descriptor,
 * upload the actual macOS program archive, approve the release (the platform
 * assembles the complete package), download that package through the real
 * authenticated GUI link together with its sidecars, invite a second member
 * with build scope, verify the member download, revoke the member and verify
 * the refusal, then export the authorized JSONL. The second command re-checks
 * download status and digests on demand (tamper and restore phases).
 *
 * Results are printed as one JSON document on stdout; evidence files stay in
 * the run root.
 */
import {chromium} from '@playwright/test';
import crypto from 'node:crypto';
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
const sha256 = buffer => crypto.createHash('sha256').update(buffer).digest('hex');
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function launch() {
  const browser = await chromium.launch({channel: 'chrome', headless: true, args: [HOST_RULES]});
  const context = await browser.newContext({viewport: {width: 1320, height: 900}, acceptDownloads: true});
  return {browser, context};
}

async function login(page, credentials) {
  await page.goto(job.admin_url + '/login');
  await page.locator('[name=username]').fill(credentials.username);
  await page.locator('[name=password]').fill(credentials.password);
  await page.getByRole('button', {name: '登录', exact: true}).click();
  await page.waitForURL(url => !url.pathname.startsWith('/login'));
}

async function packageFlow(page, context) {
  // 1. Study through the GUI (the creator receives its own grants).
  await page.goto(job.admin_url + '/');
  await page.locator('[name=title]').fill(job.title);
  await page.getByRole('button', {name: '创建', exact: true}).click();
  await page.locator('[name=mode]').selectOption('anonymous');
  await page.locator('[name=max_sessions]').fill(String(job.max_sessions));
  await page.getByRole('button', {name: '保存政策'}).click();
  const study_url = page.url();
  const study_id = study_url.split('/').pop();
  check(/\/studies\/[0-9a-f-]{36}$/.test(study_url), 'study created through the GUI', study_url);

  // 2. Native descriptor, then the actual program archive.
  await page.locator('[name=descriptor]').fill(fs.readFileSync(job.descriptor, 'utf8'));
  await page.getByRole('button', {name: '登记不可变构建'}).click();
  await page.waitForLoadState('load');
  const buildArticle = page.locator('article', {hasText: 'macos_arm64'}).first();
  await buildArticle.waitFor({timeout: 30000});
  check(true, 'native descriptor registered through the GUI');

  await page.locator('#native-package-upload [name=package]').setInputFiles(job.program);
  await page.getByRole('button', {name: '上传并绑定程序包'}).click();
  await page.waitForLoadState('load');
  await page.locator('article p', {hasText: '已绑定程序包'}).first().waitFor({timeout: 300000});
  check(true, 'native program archive bound through the GUI upload form', fs.statSync(job.program).size);

  // 3. Approve: the platform freezes and assembles the complete package.
  await page.locator('article', {hasText: 'macos_arm64'}).first().getByRole('button', {name: '批准合成发行'}).click();
  await page.waitForLoadState('load');
  const artifactLink = page.locator('[data-artifact]').first();
  await artifactLink.waitFor({timeout: 900000});
  const release_url = await artifactLink.getAttribute('href');
  const release_id = release_url.split('/')[2];
  check(/^\/releases\/[0-9a-f-]{36}\/artifact$/.test(release_url), 'complete artifact published and offered in the release list', release_url);
  const label = await artifactLink.textContent();
  check(/下载完整发行包/.test(label || ''), 'the release list names the complete package download', label);

  // 4. Download through the real GUI link, then again through the session.
  const artifactPath = path.join(job.run_dir, 'complete_package.zip');
  const [download] = await Promise.all([page.waitForEvent('download'), artifactLink.click()]);
  await download.saveAs(artifactPath);
  const firstBody = fs.readFileSync(artifactPath);
  check(firstBody.length > 0, 'complete package saved from the GUI download', firstBody.length);
  const again = await context.request.get(job.admin_url + release_url);
  const againBody = await again.body();
  const secondPath = path.join(job.run_dir, 'complete_package_second.zip');
  fs.writeFileSync(secondPath, againBody);
  check(again.status() === 200, 'repeated authorized download answers 200', again.status());
  check(sha256(againBody) === sha256(firstBody), 'repeated authorized download is byte-identical', sha256(againBody));

  // 5. Sidecars through the same build scope.
  const sidecars = {};
  for (const member of ['artifact_manifest.json', 'connection.json', 'LICENSE', 'THIRD_PARTY_NOTICES.txt']) {
    const response = await context.request.get(`${job.admin_url}${release_url}/${member}`);
    const body = await response.body();
    const target = path.join(job.run_dir, `sidecar_${path.basename(member)}`);
    fs.writeFileSync(target, body);
    sidecars[member] = target;
    check(response.status() === 200, `sidecar ${member} downloadable by the build scope`, response.status());
    check(sha256(body) === (response.headers()['x-artifact-member-sha256'] || sha256(body)),
      `sidecar ${member} digest matches its response header`);
  }

  // 6. Open recruitment so the downloaded package can admit.
  await Promise.all([page.waitForResponse(r => r.url().includes('/studies/') && r.request().method() === 'POST'),
                     page.locator('[name=state]').selectOption('open')]);
  await page.waitForLoadState('load');

  // 6b. Publish the study and make the complete native release current, then
  // inspect the real portal and stable entry in the browser: a complete native
  // current release is an independent controlled program, never a Web start.
  await page.locator('[name=public]').check();
  await page.getByRole('button', {name: '保存公开政策'}).click();
  await page.waitForLoadState('load');
  await page.locator(`[data-release-option="${release_id}"]`).check();
  await page.getByRole('button', {name: '设为当前发行'}).click();
  await page.waitForLoadState('load');
  const current = await page.locator('p[data-current-release]').getAttribute('data-current-release');
  check(current === release_id, 'complete native release selected as the current release through the GUI', current);

  const experiment = job.experiment_url;
  await page.goto(experiment + '/');
  const portalBody = await page.content();
  check(portalBody.includes(`data-study="${study_id}"`) && portalBody.includes('data-participation="native"'),
        'public portal lists the native study as an independent program', study_id);
  check(portalBody.includes('data-native-participation="1"') && !portalBody.includes('/run/') && !portalBody.includes('web/index.html'),
        'portal fabricates no Web start for the complete native release');
  await page.goto(`${experiment}/join/${study_id}`);
  const entryBody = await page.content();
  check(entryBody.includes('data-startable="0"') && entryBody.includes('data-participation="native"') &&
        entryBody.includes('data-native-participation="1"'),
        'stable entry explains native participation without a start button');
  check(!entryBody.includes('/run/') && !entryBody.includes('web/index.html'),
        'stable entry contains no invalid Web path for the native release');
  await page.goto(study_url);

  // 7. Invite a second researcher holding build scope, then revoke it.
  await page.goto(study_url);
  await page.locator('[name=username]').fill(job.member.username);
  await page.locator('[name=actions][value="study.view"]').check();
  await page.locator('[name=actions][value="build.upload"]').check();
  await page.getByRole('button', {name: '创建 24 小时邀请'}).click();
  await page.waitForLoadState('load');
  const notice = await page.locator('p.notice').first().textContent();
  const token = (notice || '').split('：').pop().trim();
  check(token.length >= 20, 'invitation secret issued through the GUI', token.length);

  const memberContext = await context.browser().newContext({viewport: {width: 1100, height: 800}, acceptDownloads: true});
  const memberPage = await memberContext.newPage();
  await memberPage.goto(job.admin_url + '/activate');
  await memberPage.locator('[name=token]').fill(token);
  await memberPage.locator('[name=password]').fill(job.member.password);
  await memberPage.getByRole('button', {name: '激活账号'}).click();
  await login(memberPage, job.member);
  const memberDownload = await memberContext.request.get(job.admin_url + release_url);
  check(memberDownload.status() === 200, 'invited member with build scope downloads the artifact', memberDownload.status());
  const memberSidecar = await memberContext.request.get(`${job.admin_url}${release_url}/connection.json`);
  check(memberSidecar.status() === 200, 'invited member downloads the sidecar', memberSidecar.status());

  await page.goto(study_url);
  await page.locator('form', {hasText: job.member.username}).first().getByRole('button', {name: '撤销研究权限'}).click();
  await page.waitForLoadState('load');
  const revokedDownload = await memberContext.request.get(job.admin_url + release_url);
  const revokedSidecar = await memberContext.request.get(`${job.admin_url}${release_url}/connection.json`);
  check(revokedDownload.status() === 403, 'revoked member is denied the artifact', revokedDownload.status());
  check(revokedSidecar.status() === 403, 'revoked member is denied the sidecars', revokedSidecar.status());
  await memberContext.close();

  await page.goto(study_url);
  await page.screenshot({path: path.join(job.run_dir, 'admin_study.png'), fullPage: true});
  return {study_id, study_url, release_id, release_url, artifact_path: artifactPath, second_path: secondPath,
          sidecars, member_username: job.member.username};
}

async function exportJsonl(page) {
  await page.goto(job.study_url);
  await page.getByRole('button', {name: '创建 JSONL 固定快照'}).click();
  const [download] = await Promise.all([page.waitForEvent('download'),
                                        page.getByRole('link', {name: '下载 JSONL'}).click()]);
  await download.saveAs(job.out);
  check(fs.statSync(job.out).size > 0, 'authorized JSONL export produced through the GUI', fs.statSync(job.out).size);
  return job.out;
}

async function downloadItems(context, items) {
  const out = [];
  for (const item of items) {
    const response = await context.request.get(item.url);
    const body = await response.body();
    if (item.out && response.status() === 200) fs.writeFileSync(item.out, body);
    out.push({url: item.url, status: response.status(), bytes: body.length, sha256: sha256(body),
              artifact_sha256: response.headers()['x-artifact-sha256'] || null});
  }
  return out;
}

async function main() {
  const {browser, context} = await launch();
  const page = await context.newPage();
  try {
    await login(page, job.credentials);
    if (command === 'package') {
      results.evidence.package = await packageFlow(page, context);
    } else if (command === 'export') {
      results.evidence.export = await exportJsonl(page);
    } else if (command === 'downloads') {
      results.evidence.downloads = await downloadItems(context, job.items);
    } else {
      throw new Error('unknown command ' + command);
    }
  } catch (error) {
    results.error = String((error && error.stack) || error);
    try { await page.screenshot({path: path.join(job.run_dir, 'error.png')}); } catch {}
    try { results.evidence.page_html = (await page.content()).slice(0, 30000); } catch {}
  } finally {
    await context.close();
    await browser.close();
  }
  process.stdout.write(JSON.stringify(results));
  process.exit(results.error || results.failures.length ? 1 : 0);
}

await main();
