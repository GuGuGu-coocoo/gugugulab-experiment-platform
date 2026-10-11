import {expect, test} from '@playwright/test';
import fs from 'node:fs';
import http from 'node:http';

// Real Chrome and the shipped DOM module verify that local-save retry can
// actually be clicked above the canvas. The synthetic host models commit
// faults only; these checks make no API/database/ACK acceptance claim.
const shellSource = fs.readFileSync(new URL('../../packages/gec_web/shell.js', import.meta.url), 'utf8');
const html = `<!doctype html><html><head><meta charset="utf-8"></head>
<body><canvas id="canvas" tabindex="0" width="1000" height="700" style="width:1000px;height:700px"></canvas>
<script type="module">
import {installShell} from '/shell.js';
const query = new URLSearchParams(location.search);
globalThis.actions = [];
globalThis.attempts = 0;
globalThis.canvasKeys = [];
document.getElementById('canvas').addEventListener('keydown', event => canvasKeys.push(event.key));
globalThis.shell = installShell(JSON.stringify({
  title:'Generic local save retry',status:'Ready',mode:'password',
  local_save_retry:query.has('early'),
  messages:{code:'Issued ID',password:'Issued password',recovery_legend:'Same-device recovery',
    short_code:'Six-digit recovery code',recover:'Recover',advanced:'Advanced recovery',session:'Session UUID',
    permit:'One-time permit',recover_permit:'Recover with permit',start:'Start participation',export:'Export recovery',
    retry_local_save:'Retry local save',retry_upload:'Retry upload',export_failure:'Export failure data',
    continue_:'Continue',start_new:'Start new',cancel:'Cancel'}
}), action => {
  actions.push(action);
  if(action==='start')shell.setState({entry_hidden:true,panel_hidden:true,busy:false});
  if(action==='local-retry'){
    attempts++;
    shell.setState({local_save_retry:false,busy:true});
    if(query.has('finish'))shell.setState({local_save_retry:false,busy:false,panel_hidden:false,
      visible:['export'],status:'Locally saved finish; uploading'});
    else if(attempts===1)shell.setState({local_save_retry:true,busy:false,panel_hidden:false,
      visible:['export'],status:'Local save failed again: original buffered record retained'});
    else shell.setState({local_save_retry:false,busy:false,panel_hidden:true,status:'Local save complete'});
  }
});
</script></body></html>`;

let server;
let origin;
test.beforeAll(async () => {
  server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    const content = pathname === '/' ? html : pathname === '/shell.js' ? shellSource : null;
    response.writeHead(content === null ? 404 : 200, {'Content-Type': pathname === '/' ? 'text/html; charset=utf-8' : 'text/javascript; charset=utf-8', 'Cache-Control':'no-store'});
    response.end(content ?? '');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(async () => {if(server)await new Promise(resolve => server.close(resolve));});

async function admitAndFail(page, query='') {
  await page.goto(origin + '/' + query);
  await page.waitForFunction(() => !!globalThis.shell);
  await page.getByRole('button', {name:'Start participation',exact:true}).click();
  await expect(page.locator('#gec-shell')).toBeHidden();
  await expect(page.locator('#canvas')).toBeFocused();
  await page.evaluate(() => shell.setState({panel_hidden:false,local_save_retry:true,
    visible:['export'],status:'Local save failed: uncommitted in-process records may be lost on closing'}));
}

test('local-save retry is clickable above the canvas and stays separate from upload retry', async ({page}, testInfo) => {
  const errors=[];
  page.on('pageerror', error => errors.push(error.message));
  await admitAndFail(page);
  const retry=page.locator('#gec-local-retry');
  await expect(retry).toBeVisible();
  await expect(retry).toBeEnabled();
  await expect(page.locator('#gec-retry')).toBeHidden();
  await expect(page.locator('#gec-start')).toBeHidden();
  await expect(page.locator('#gec-start')).toBeDisabled();
  await page.screenshot({path:testInfo.outputPath('local-save-failed.png')});
  // No force: Chrome must be able to hit the real DOM control.
  await retry.click();
  await expect(page.locator('#gec-shell-status')).toHaveText('Local save failed again: original buffered record retained');
  await expect(retry).toBeEnabled();
  await retry.click();
  expect(await page.evaluate(() => actions)).toEqual(['start','local-retry','local-retry']);
  expect(await page.evaluate(() => attempts)).toBe(2);
  await expect(page.locator('#gec-shell')).toBeHidden();
  expect(await page.locator('#gec-shell').evaluate(panel=>panel.inert)).toBe(true);
  await expect(page.locator('#canvas')).toBeFocused();
  await expect(retry).toBeDisabled();
  await page.keyboard.press('j');
  expect(await page.evaluate(()=>canvasKeys)).toContain('j');
  // Upload failure may independently show its own retry after the task retires.
  await page.evaluate(()=>shell.setState({panel_hidden:false,failure:{visible:true,retry:true,export:true}}));
  await expect(retry).toBeHidden();
  await page.locator('#gec-retry').click();
  expect(await page.evaluate(()=>actions)).toEqual(['start','local-retry','local-retry','retry']);
  expect(errors).toEqual([]);
});

test('a successful finish retry leaves status visible without reopening task admission', async ({page}, testInfo) => {
  const errors=[];
  page.on('pageerror', error=>errors.push(error.message));
  await admitAndFail(page,'?finish=1');
  await page.locator('#gec-local-retry').click();
  await expect(page.locator('#gec-shell')).toBeVisible();
  await expect(page.locator('#gec-shell-status')).toHaveText('Locally saved finish; uploading');
  await expect(page.locator('#gec-local-retry')).toBeHidden();
  await expect(page.locator('#gec-local-retry')).toBeDisabled();
  await page.evaluate(()=>shell.setState({busy:true}));
  await page.evaluate(()=>shell.setState({busy:false,focus:'code'}));
  for(const name of ['start','recover-code','recover-permit']){
    await expect(page.locator('#gec-'+name)).toBeHidden();
    await expect(page.locator('#gec-'+name)).toBeDisabled();
  }
  for(const id of ['code','password','short-code','recovery','permit']){
    await expect(page.locator('#gec-input-'+id)).toBeHidden();
    await expect(page.locator('#gec-input-'+id)).toBeDisabled();
  }
  await page.screenshot({path:testInfo.outputPath('finished-entry-retired.png')});
  await page.locator('#gec-export').click();
  expect(await page.evaluate(()=>actions)).toEqual(['start','local-retry','export']);
  expect(await page.evaluate(()=>attempts)).toBe(1);
  expect(errors).toEqual([]);
});

test('an already failed save survives mounting and busy refreshes cannot enable its hidden control', async ({page}) => {
  await page.goto(origin+'/?early=1');
  await page.waitForFunction(()=>!!globalThis.shell);
  const retry=page.locator('#gec-local-retry');
  await expect(retry).toBeVisible();
  await expect(retry).toBeEnabled();
  await page.evaluate(()=>shell.setState({busy:true}));
  await expect(retry).toBeDisabled();
  await page.evaluate(()=>shell.setState({busy:false,local_save_retry:false}));
  await expect(retry).toBeHidden();
  await expect(retry).toBeDisabled();
  await page.evaluate(()=>shell.setState({busy:true}));
  await page.evaluate(()=>shell.setState({busy:false}));
  await expect(retry).toBeDisabled();
  expect(await page.evaluate(()=>actions)).toEqual([]);
});
