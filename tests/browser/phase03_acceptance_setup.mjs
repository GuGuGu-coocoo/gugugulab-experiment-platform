#!/usr/bin/env node
/* 03F acceptance setup driver (owned by tools/phase03_acceptance.py).
 *
 * Creates one real isolated-instance study with a real uploaded/approved Godot
 * Web release through the researcher GUI, opens recruitment and writes the frozen
 * connection config. It never touches the local dev instance or the protected
 * acceptance database; the Python orchestrator owns the temporary instance.
 *
 * Usage: node phase03_acceptance_setup.mjs <job.json>
 * Prints one JSON document on stdout.
 */
import {chromium} from '@playwright/test';
import fs from 'node:fs';

const HOST_RULES = '--host-resolver-rules=MAP admin.localhost 127.0.0.1, MAP experiment.localhost 127.0.0.1';
const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

async function main() {
  const browser = await chromium.launch({channel: 'chrome', headless: true, args: [HOST_RULES]});
  const context = await browser.newContext({viewport: {width: 1280, height: 720}});
  const page = await context.newPage();
  const evidence = {};
  try {
    await page.goto(job.admin_url + '/login');
    await page.locator('[name=username]').fill(job.credentials.username);
    await page.locator('[name=password]').fill(job.credentials.password);
    await page.getByRole('button', {name: '登录', exact: true}).click();
    await page.waitForURL(url => !url.pathname.startsWith('/login'));

    await page.goto(job.admin_url + '/');
    await page.locator('[name=title]').fill('03F 隔离 Web 回归 ' + Date.now());
    await page.getByRole('button', {name: '创建', exact: true}).click();
    const study_url = page.url();
    const study_id = study_url.split('/').pop();

    await page.goto(study_url + '/participation');
    await page.locator('[name=mode]').selectOption('anonymous');
    await page.locator('[name=max_sessions]').fill('64');
    await page.getByRole('button', {name: '保存政策'}).click();

    await page.goto(study_url + '/builds');
    await page.locator('#package-upload input[name=package]').setInputFiles(job.web_zip);
    await page.locator('#package-upload').getByRole('button', {name: '上传并验证'}).click();
    await page.getByRole('button', {name: '隔离预览（仅本地保存）'}).waitFor({timeout: 180000});
    await page.getByRole('button', {name: '批准合成发行'}).click();
    const run_link = await page.getByRole('link', {name: '打开参与入口'}).getAttribute('href');

    const config_href = await page.getByRole('link', {name: '导出连接配置'}).getAttribute('href');
    const response = await page.request.get(job.admin_url + config_href);
    if (response.status() !== 200) throw new Error('connection config export returned ' + response.status());
    const connection = await response.json();
    connection.api_url = job.experiment_url;
    fs.writeFileSync(job.connection_out, JSON.stringify(connection));

    await page.goto(study_url + '/recruitment');
    await Promise.all([
      page.waitForResponse(r => r.url().includes('/studies/') && r.request().method() === 'POST'),
      page.locator('[name=state]').selectOption('open'),
    ]);
    await page.waitForLoadState('load');

    evidence.study_id = study_id;
    evidence.study_url = study_url;
    evidence.release_id = connection.release_id;
    evidence.build_id = connection.build_id;
    evidence.instance_id = connection.instance_id;
    evidence.run_url = run_link || `${job.experiment_url}/run/${connection.release_id}/web/index.html`;
    evidence.connection_out = job.connection_out;
    evidence.study_url_file = job.study_url_file || null;
    if (job.study_url_file) fs.writeFileSync(job.study_url_file, study_url);
    if (job.run_url_file) fs.writeFileSync(job.run_url_file, evidence.run_url);
    process.stdout.write(JSON.stringify({ok: true, evidence}) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ok: false, error: String(error && error.stack || error), evidence}) + '\n');
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
