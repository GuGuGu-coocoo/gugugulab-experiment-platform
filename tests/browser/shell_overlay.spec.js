import {expect, test} from '@playwright/test';
import fs from 'node:fs';
import http from 'node:http';

// Load the shipped module unchanged in real Chrome. The short-lived loopback
// server serves only this synthetic canvas document and the actual shell file;
// it is not an admission/API/database substitute.
const shellSource = fs.readFileSync(new URL('../../packages/gec_web/shell.js', import.meta.url), 'utf8');
const specification = {
  title: 'Synthetic overlay test', status: 'Ready', mode: 'password',
  local_only: false,
  messages: {
    code: 'Issued ID', password: 'Issued password',
    recovery_legend: 'Same-device recovery', short_code: 'Six-digit recovery code',
    recover: 'Recover', advanced: 'Advanced recovery', session: 'Session UUID',
    permit: 'One-time permit', recover_permit: 'Recover with permit',
    start: 'Start participation', export: 'Export recovery',
    continue_: 'Continue', start_new: 'Start new', cancel: 'Cancel',
  },
};
const html = `<!doctype html><html><head><meta charset="utf-8"></head>
<body><canvas id="canvas" tabindex="0" width="1000" height="700" style="width:1000px;height:700px"></canvas>
<script type="module">
import {installShell} from '/shell.js';
const spec = ${JSON.stringify(specification)};
spec.panel_hidden = new URLSearchParams(location.search).has('hidden');
globalThis.actions = [];
globalThis.canvasKeys = [];
document.getElementById('canvas').addEventListener('keydown', event => canvasKeys.push(event.key));
globalThis.shell = installShell(JSON.stringify(spec), action => actions.push(action));
</script></body></html>`;

let server;
let origin;
test.beforeAll(async () => {
  server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    if (pathname === '/') {
      response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'});
      response.end(html);
    } else if (pathname === '/shell.js') {
      response.writeHead(200, {'Content-Type': 'text/javascript; charset=utf-8', 'Cache-Control': 'no-store'});
      response.end(shellSource);
    } else {
      response.writeHead(404);
      response.end();
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(async () => {
  if (server) await new Promise(resolve => server.close(resolve));
});

test('hidden shell starts inert and returns entry focus to the game canvas', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(origin + '/?hidden=1');
  await page.waitForFunction(() => !!globalThis.shell);
  await expect(page.locator('#gec-shell')).toBeHidden();
  await expect(page.locator('#gec-shell')).toHaveAttribute('aria-hidden', 'true');
  expect(await page.locator('#gec-shell').evaluate(panel => panel.inert)).toBe(true);
  await expect(page.locator('#canvas')).toBeFocused();
  await page.evaluate(() => shell.setState({status: 'Still locally saved', focus: 'code'}));
  await expect(page.locator('#gec-shell')).toBeHidden();
  await expect(page.locator('#gec-shell-status')).toHaveText('Still locally saved');
  await expect(page.locator('#canvas')).toBeFocused();
  await page.evaluate(() => shell.setState({panel_hidden: false, focus: 'code'}));
  await expect(page.locator('#gec-shell')).toBeVisible();
  await expect(page.locator('#gec-shell')).toHaveAttribute('aria-hidden', 'false');
  expect(await page.locator('#gec-shell').evaluate(panel => panel.inert)).toBe(false);
  await page.getByLabel('Issued ID', {exact: true}).fill('synthetic-001');
  await expect(page.locator('#gec-input-code')).toBeFocused();
  await page.evaluate(() => shell.setState({panel_hidden: true}));
  await expect(page.locator('#canvas')).toBeFocused();
  await expect(page.locator('#gec-shell')).toBeHidden();
  expect(await page.locator('#gec-shell').evaluate(panel => panel.inert)).toBe(true);
  await page.keyboard.press('j');
  expect(await page.evaluate(() => canvasKeys)).toContain('j');
  await page.evaluate(() => shell.setState({busy: false, status: 'Uploading', focus: 'password'}));
  await expect(page.locator('#canvas')).toBeFocused();
  await expect(page.locator('#gec-shell')).toBeHidden();
  expect(await page.evaluate(() => actions)).toEqual([]);
  expect(errors).toEqual([]);
});

test('finish can reveal status without restoring retired admission controls', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(origin + '/');
  await page.waitForFunction(() => !!globalThis.shell);
  await page.getByLabel('Issued ID', {exact: true}).fill('synthetic-002');
  await page.getByLabel('Issued password', {exact: true}).fill('synthetic-test-password');
  await page.getByRole('button', {name: 'Start participation', exact: true}).click();
  expect(await page.evaluate(() => actions)).toEqual(['start']);
  // The host announces successful admission through the actual shell API.
  await page.evaluate(() => shell.setState({entry_hidden: true, panel_hidden: true, busy: false}));
  await expect(page.locator('#gec-shell')).toHaveAttribute('data-entry-hidden', '1');
  await expect(page.locator('#gec-shell')).toBeHidden();
  await expect(page.locator('#canvas')).toBeFocused();
  await page.evaluate(() => shell.setState({status: 'Locally saved, uploading', busy: false}));
  await expect(page.locator('#gec-shell')).toBeHidden();
  // The finish screen explicitly reveals the status and the legal export action.
  await page.evaluate(() => shell.setState({panel_hidden: false, status: 'All records received', busy: false, visible: ['export'], focus: 'code'}));
  await expect(page.locator('#gec-shell')).toBeVisible();
  await expect(page.locator('#gec-shell-status')).toHaveText('All records received');
  await expect(page.locator('#gec-start')).toBeHidden();
  await expect(page.locator('#gec-start')).toBeDisabled();
  for (const id of ['code', 'password', 'short-code', 'recovery', 'permit']) {
    await expect(page.locator('#gec-input-' + id)).toBeDisabled();
    await expect(page.locator('#gec-input-' + id)).toBeHidden();
  }
  expect(await page.locator('#gec-shell').evaluate(panel => panel.contains(document.activeElement))).toBe(false);
  // A later busy refresh never re-enables Start/Recover; display cannot undo retirement.
  await page.evaluate(() => shell.setState({busy: true}));
  await page.evaluate(() => shell.setState({busy: false}));
  for (const name of ['start', 'recover-code', 'recover-permit']) {
    await expect(page.locator('#gec-' + name)).toBeDisabled();
  }
  await page.locator('#gec-export').click();
  expect(await page.evaluate(() => actions)).toEqual(['start', 'export']);
  expect(errors).toEqual([]);
});
