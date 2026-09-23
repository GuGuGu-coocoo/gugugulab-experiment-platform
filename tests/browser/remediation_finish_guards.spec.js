import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
/* P03R09AR real-Chrome check for the shipped browser bridge and SDK.
 *
 * The pytest task driver serves the shipped packages/gec_web modules with a
 * local-preview application context and runs this spec with GEP_REMEDIATION_JOB
 * set. Without that environment the spec skips (tools/verify_browser.py may
 * collect this directory), but the Required Verification driver always sets it
 * and requires one passed, non-skipped test.
 *
 * Everything runs in real Chrome with real IndexedDB and the real bridge call
 * path the engine uses: a legal local-results download, then the refusals for a
 * front-locked session and for a session that is no longer a local one (the
 * data-only case), each asserting that no download is produced and that the
 * durable records are unchanged. It also drives the shipped participation panel
 * through the bridge and proves that a retired (data-only) entry cannot send an
 * action. No server is contacted at any point.
 */
const jobPath=process.env.GEP_REMEDIATION_JOB;
const MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.json':'application/json'};

test('real Chrome bridge: local results export guard and data-only retirement',async({browser})=>{
  test.skip(!jobPath,'set GEP_REMEDIATION_JOB to run the real bridge guard check');
  test.setTimeout(180000);
  const job=JSON.parse(fs.readFileSync(jobPath,'utf8'));
  const evidence=job.evidence_dir;
  fs.mkdirSync(evidence,{recursive:true});
  const checks=[];
  const failures=[];
  const apiRequests=[];
  const check=(ok,label,detail)=>{const entry={label,ok:!!ok,detail:detail??null};checks.push(entry);if(!ok)failures.push(entry);};

  const server=http.createServer((request,response)=>{
    const pathname=new URL(request.url,'http://127.0.0.1').pathname;
    const send=(file)=>{response.writeHead(200,{'Content-Type':MIME[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});response.end(fs.readFileSync(file));};
    if(pathname==='/index.html'){
      const context=JSON.stringify(job.context).replace(/</g,'\\u003c');
      response.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'});
      response.end(`<!doctype html><html><head><meta charset="utf-8"><title>GEC bridge guard</title><script>globalThis.GEP_CONTEXT=${context};</script><script type="module" src="/gec/bridge.js"></script></head><body><canvas id="canvas" tabindex="0"></canvas></body></html>`);
      return;
    }
    if(pathname.startsWith('/gec/')){
      const file=path.join(job.package_dir,pathname.slice('/gec/'.length));
      if(fs.existsSync(file)&&fs.statSync(file).isFile())send(file);
      else{response.writeHead(404);response.end();}
      return;
    }
    response.writeHead(404);response.end();
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;

  const context=await browser.newContext({locale:'zh-CN',acceptDownloads:true});
  const page=await context.newPage();
  const downloads=[];
  page.on('download',download=>downloads.push(download));
  page.on('request',request=>{if(request.url().startsWith(job.api_url))apiRequests.push(request.url());});
  page.on('pageerror',error=>check(false,'uncaught page error: '+error.message));

  const call=(op,args)=>page.evaluate(async({op,args})=>new Promise((resolve,reject)=>{
    const key='guard-'+Math.random().toString(36).slice(2);
    const deadline=Date.now()+60000;
    const attempt=()=>{
      if(!globalThis.GECBridge){if(Date.now()>deadline)reject(new Error('bridge timeout'));setTimeout(attempt,20);return;}
      globalThis.GECBridge.call(key,op,JSON.stringify(args));
      const poll=()=>{const value=globalThis.GECBridge.take(key);if(value!=null)resolve(JSON.parse(value));else if(Date.now()>deadline)reject(new Error('bridge timeout '+op));else setTimeout(poll,10);};
      poll();
    };
    attempt();
  }),{op,args});
  const readStore=()=>page.evaluate(()=>new Promise((resolve,reject)=>{
    const r=indexedDB.open('gec-1');
    r.onsuccess=()=>{const db=r.result;const tx=db.transaction('sessions');const q=tx.objectStore('sessions').getAll();tx.oncomplete=()=>{resolve(q.result);db.close();};tx.onerror=()=>reject(tx.error);};
    r.onerror=()=>reject(r.error);
  }));
  const mutateRow=(id,patch)=>page.evaluate(({id,patch})=>new Promise((resolve,reject)=>{
    const r=indexedDB.open('gec-1');
    r.onsuccess=()=>{
      const db=r.result;
      const tx=db.transaction('sessions','readwrite');
      const store=tx.objectStore('sessions');
      const q=store.get(id);
      q.onsuccess=()=>{const row=q.result;if(!row){reject(new Error('missing row '+id));return;}store.put({...row,...patch});};
      tx.oncomplete=()=>{db.close();resolve();};
      tx.onerror=()=>{db.close();reject(tx.error);};
    };
    r.onerror=()=>reject(r.error);
  }),{id,patch});
  const rowById=(rows,id)=>rows.find(entry=>entry.id===id);

  const evidenceDocument={checks,failures,api_requests:apiRequests,downloads:downloads.length,scenarios:{}};
  try{
    await page.goto(origin+'/index.html');
    await page.waitForFunction(()=>globalThis.GECBridge!==undefined,null,{timeout:60000});
    await page.waitForFunction(()=>globalThis.GECBridge.status().state==='ready',null,{timeout:60000});
    check(job.context.preview===true,'the application context is a local-only preview');

    // ---- a legal local session downloads its own records ------------------
    const started=await call('start',[{}]);
    check(started.state==='active','a local session starts: '+JSON.stringify(started));
    const initial=(await readStore()).find(entry=>entry.kind==='local');
    check(initial&&initial.records.length===0&&!initial.front_locked,'an unlocked local session is durable');
    const sessionId=initial.id;
    const first=await call('record',['exp.rt',{trial_id:'t1',choice:'left',rt_ms:321.5},{id:'rt',version:'1'},null]);
    const second=await call('record',['exp.interaction',{action:'revise',confidence:null},{id:'interaction',version:'1'},null]);
    await call('commit',[{version:1,strategy:'trial_boundary_v1',dependencies:[first.event_id,second.event_id],next_trial:1}]);
    const stored=rowById(await readStore(),sessionId);
    check(stored&&stored.kind==='local'&&stored.records.length===2&&!stored.front_locked,'two unlocked local records are durable');
    const downloadPromise=page.waitForEvent('download');
    const positive=await call('download_results',[]);
    check(positive.state==='download_requested'&&/^local-results-/.test(positive.filename),'the legal download is requested: '+JSON.stringify(positive));
    const download=await downloadPromise;
    const saved=path.join(evidence,'bridge-local-results.jsonl');
    await download.saveAs(saved);
    const lines=fs.readFileSync(saved,'utf8').split('\n').filter(Boolean).map(line=>JSON.parse(line));
    check(lines.length===2&&lines[0].payload.trial_id==='t1','the downloaded JSONL keeps the record values');
    expect(lines).toEqual(stored.records);
    evidenceDocument.scenarios.success={session_id:sessionId,records:stored.records.length,filename:positive.filename,downloads:downloads.length};

    // ---- a front-locked local session is refused, no download, no change --
    await mutateRow(sessionId,{front_locked:true});
    const beforeLocked=rowById(await readStore(),sessionId);
    const locked=await call('download_results',[]);
    check(locked.error==='results_export_unavailable','the front-locked session is refused: '+JSON.stringify(locked));
    await page.waitForTimeout(400);
    check(downloads.length===1,'the refused locked export produced no download: '+downloads.length);
    const afterLocked=rowById(await readStore(),sessionId);
    check(JSON.stringify(afterLocked.records)===JSON.stringify(beforeLocked.records),'the refused locked export left the records unchanged');
    check(afterLocked.front_locked===true,'the refused locked export left the lock in place');
    evidenceDocument.scenarios.locked={error:locked.error,downloads:downloads.length};

    // ---- a session that is no longer a local one is refused as well -------
    await mutateRow(sessionId,{kind:'session',front_locked:false});
    const beforeRemote=rowById(await readStore(),sessionId);
    const remote=await call('download_results',[]);
    check(remote.error==='results_export_unavailable','a non-local session is refused: '+JSON.stringify(remote));
    await page.waitForTimeout(400);
    check(downloads.length===1,'the refused non-local export produced no download: '+downloads.length);
    const afterRemote=rowById(await readStore(),sessionId);
    check(JSON.stringify(afterRemote.records)===JSON.stringify(beforeRemote.records),'the refused non-local export left the records unchanged');
    evidenceDocument.scenarios.remote_kind={error:remote.error,downloads:downloads.length};

    // ---- data-only recovery: no trial, results export refused, recovery
    //      export still available for the legal failure-data boundary ------
    await mutateRow(sessionId,{context:{token:''}});
    const confirmed=await call('confirm_recovery',[sessionId,'synthetic-token',false]);
    check(confirmed.state==='data_only','the explicit confirmation is data only: '+JSON.stringify(confirmed));
    check(await page.evaluate(()=>globalThis.GECBridge.status().state)==='data_only','the SDK state is data only');
    const dataOnlyDownload=await call('download_results',[]);
    check(dataOnlyDownload.error==='results_export_unavailable','the data-only session cannot download results: '+JSON.stringify(dataOnlyDownload));
    await page.waitForTimeout(400);
    check(downloads.length===1,'the refused data-only export produced no download: '+downloads.length);
    const afterDataOnly=rowById(await readStore(),sessionId);
    check(JSON.stringify(afterDataOnly.records)===JSON.stringify(afterRemote.records),'the data-only recovery left the records unchanged');
    const recovery=await call('recovery_export',[]);
    check(recovery.format_version===1&&recovery.records.length===2,'the legal recovery export keeps the records: '+JSON.stringify(recovery).slice(0,120));
    evidenceDocument.scenarios.data_only={state:confirmed.state,records:afterDataOnly.records.length,download_error:dataOnlyDownload.error,downloads:downloads.length};

    // ---- the shipped panel: a retired (data-only) entry sends no action ---
    const spec={title:'Synthetic',status:'',mode:'anonymous',show_code:false,local_only:true,
      messages:{anonymous:'This study does not require an ID.',code:'Roster ID',password:'Roster password',recovery_legend:'Same-device recovery',
        short_code:'Six-digit recovery code',recover:'Recover',advanced:'Advanced',session:'Session UUID',permit:'Permit',recover_permit:'Recover with permit',
        start:'Start participation',export:'Export current recovery data',download_results:'Download results JSONL',continue_:'Continue previous session',
        start_new:'Start a new session',cancel:'Cancel'}};
    await page.evaluate(value=>globalThis.GECBridge.mount_shell(JSON.stringify(value)),spec);
    await page.locator('#gec-start').waitFor({state:'visible',timeout:30000});
    await page.evaluate(()=>{globalThis.__guardActions=[];globalThis.GECBridge.on_shell_action(name=>globalThis.__guardActions.push(name));});
    await page.click('#gec-start');
    check((await page.evaluate(()=>globalThis.__guardActions)).join(',')==='start','a live panel action reaches the bridge');
    // The engine retires the panel for a data-only recovery: lock, then announce.
    await page.evaluate(()=>globalThis.GECBridge.shell_state({entry_hidden:true,busy:false}));
    await page.evaluate(()=>globalThis.GECBridge.shell_state({status:'Data recovery only: no trials will resume.'}));
    check((await page.locator('#gec-shell-status').textContent()).includes('Data recovery only'),'the data-only status reaches the panel');
    check(await page.locator('#gec-start').isHidden(),'the retired panel hides start');
    check(await page.locator('#gec-start').isDisabled(),'the retired panel disables start');
    check(await page.locator('#gec-input-short-code').isHidden(),'the retired panel hides the recovery fields');
    const actionsBefore=await page.evaluate(()=>globalThis.__guardActions.length);
    await page.evaluate(()=>{document.getElementById('gec-start').click();});
    await page.waitForTimeout(200);
    check(await page.evaluate(()=>globalThis.__guardActions.length)===actionsBefore,'a click on the retired start produces no action');
    evidenceDocument.scenarios.panel={actions_before:actionsBefore,actions_after:await page.evaluate(()=>globalThis.__guardActions.length)};

    check(apiRequests.length===0,'zero requests were made to the API origin: '+JSON.stringify(apiRequests.slice(0,3)));
    check(failures.length===0,'every bridge guard check passed');
  }finally{
    evidenceDocument.api_requests=apiRequests;
    evidenceDocument.checks=checks;
    evidenceDocument.failures=failures;
    evidenceDocument.downloads=downloads.length;
    fs.writeFileSync(path.join(evidence,'summary.json'),JSON.stringify(evidenceDocument,null,2));
    await context.close().catch(()=>{});
    await new Promise(resolve=>server.close(resolve));
  }
  if(failures.length)throw new Error('bridge guard checks failed: '+JSON.stringify(failures.map(entry=>entry.label)));
});
