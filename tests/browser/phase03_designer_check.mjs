#!/usr/bin/env node
/* Real Chrome login check for the designer environment.
 *
 * Owned by tools/phase03_designer_kit.py --verify. It proves that the prepared
 * isolated instance answers a real Chrome login on its frozen loopback port
 * (>= 8040) and never claims human acceptance.
 */
import {chromium} from '@playwright/test';
import fs from 'node:fs';

const HOST_RULES = '--host-resolver-rules=MAP admin.localhost 127.0.0.1, MAP experiment.localhost 127.0.0.1';
const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

async function main() {
  const browser = await chromium.launch({channel: 'chrome', headless: true, args: [HOST_RULES]});
  try {
    const page = await browser.newPage();
    await page.goto(job.admin_url + '/login');
    await page.locator('[name=username]').fill(job.username);
    await page.locator('[name=password]').fill(job.password);
    await page.getByRole('button', {name: '登录', exact: true}).click();
    await page.waitForURL(url => !url.pathname.startsWith('/login'));
    process.stdout.write(JSON.stringify({ok: true, url: page.url(), title: await page.title()}) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ok: false, error: String(error && error.stack || error)}) + '\n');
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
