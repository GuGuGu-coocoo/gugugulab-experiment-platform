import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
/* P03R09A real-Chrome check for the local-only Web preview.
 *
 * The pytest task driver exports the real Godot Web build, points the job at the
 * shipped packages/gec_web modules and the preview application context, and runs
 * this spec with GEP_REMEDIATION_JOB set. Without that environment the spec skips
 * (tools/verify_browser.py may collect this directory), but the Required
 * Verification command always sets it and the driver requires one passed test.
 *
 * Everything runs in real Chrome against the real Web client modules, real
 * IndexedDB and the real exported engine: a local-only finish, an explicit JSONL
 * download, a cancelled download, and a real IndexedDB write failure that must
 * never be reported as a saved finish. No server is contacted at any point; each
 * scenario uses its own browser context so the device writer lock of one page
 * cannot leak into the next.
 */
const jobPath=process.env.GEP_REMEDIATION_JOB;
const MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.wasm':'application/wasm','.pck':'application/octet-stream','.png':'image/png','.json':'application/json'};
const PAYLOADS=[
  ['exp.rt',{trial_id:'t1',choice:'left',rt_ms:321.5,response_status:'responded'},{id:'rt',version:'1'}],
  ['exp.interaction',{action:'revise',selection:['shape_a','shape_c'],confidence:null,nested:{changes:[{from:null,to:'001'}],confirmed:false}},{id:'interaction',version:'1'}],
  ['exp.rt',{trial_id:'t2',choice:'right',rt_ms:217.25,response_status:'responded'},{id:'rt',version:'1'}],
  ['exp.interaction',{action:'revise',selection:['shape_a','shape_c'],confidence:null,nested:{changes:[{from:null,to:'001'}],confirmed:false}},{id:'interaction',version:'1'}],
];

test('real Web local-only preview: finish, JSONL download, cancel and IndexedDB failure',async({browser})=>{
  test.skip(!jobPath,'set GEP_REMEDIATION_JOB to run the real local-preview check');
  test.setTimeout(300000);
  const job=JSON.parse(fs.readFileSync(jobPath,'utf8'));
  const evidence=job.evidence_dir;
  fs.mkdirSync(evidence,{recursive:true});
  const checks=[];
  const failures=[];
  const check=(ok,label,detail)=>{const entry={label,ok:!!ok,detail:detail??null};checks.push(entry);if(!ok)failures.push(entry);};
  const apiRequests=[];

  const server=http.createServer((request,response)=>{
    const pathname=new URL(request.url,'http://127.0.0.1').pathname;
    const send=(file)=>{const type=MIME[path.extname(file)]||'application/octet-stream';response.writeHead(200,{'Content-Type':type,'Cache-Control':'no-store'});response.end(fs.readFileSync(file));};
    if(pathname==='/index.html'){
      let html=fs.readFileSync(path.join(job.export_dir,'index.html'),'utf8');
      const context=JSON.stringify(job.context).replace(/</g,'\\u003c');
      html=html.replace('<head>',`<head><script>globalThis.GEP_CONTEXT=${context};</script>`);
      response.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'});
      response.end(html);
      return;
    }
    if(pathname.startsWith('/gec/')){
      const file=path.join(job.package_dir,pathname.slice('/gec/'.length));
      if(fs.existsSync(file)&&fs.statSync(file).isFile())send(file);
      else{response.writeHead(404);response.end();}
      return;
    }
    const file=path.join(job.export_dir,pathname.replace(/^\//,''));
    if(fs.existsSync(file)&&fs.statSync(file).isFile())send(file);
    else{response.writeHead(404);response.end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const contexts=[];
  const injectedErrors=[];
  const watch=(page)=>{page.on('request',request=>{if(request.url().startsWith(job.api_url))apiRequests.push(request.url());});
                       page.on('pageerror',error=>{if(/injected IndexedDB write failure|Uncaught exception in event handler/.test(error.message)){injectedErrors.push(error.message);return;}check(false,'uncaught page error: '+error.message);});};
  const readStore=(page)=>page.evaluate(()=>new Promise((resolve,reject)=>{const r=indexedDB.open('gec-1');r.onsuccess=()=>{const db=r.result;const tx=db.transaction('sessions');const q=tx.objectStore('sessions').getAll();tx.oncomplete=()=>{resolve(q.result);db.close();};tx.onerror=()=>reject(tx.error);};r.onerror=()=>reject(r.error);}));
  const openContext=async()=>{const context=await browser.newContext({locale:'zh-CN'});contexts.push(context);return context;};
  const ready=async(page)=>{await page.goto(origin+'/index.html');await page.waitForFunction(()=>globalThis.GECBridge!==undefined,null,{timeout:120000});await page.waitForFunction(()=>globalThis.GECBridge.status().state==='ready',null,{timeout:120000});await page.locator('#gec-shell').waitFor({state:'visible',timeout:60000});};
  const trial=async(page,n)=>page.waitForFunction(number=>{const label=document.getElementById('canvas')?.getAttribute('aria-label')||'';return label.includes(`第 ${number} 次试次`)||label.includes(`Trial ${number}`);},n,{timeout:60000});
  const injectPutFailure=(page)=>page.evaluate(()=>{window.__originalPut=window.__originalPut||IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(){throw new DOMException('injected IndexedDB write failure','QuotaExceededError');};});
  const restorePut=(page)=>page.evaluate(()=>{if(window.__originalPut)IDBObjectStore.prototype.put=window.__originalPut;});

  const evidenceDocument={checks,failures,api_requests:apiRequests,scenarios:{}};
  try{
    // ---- A. the real exported engine: local finish, download, cancel ------
    const contextA=await openContext();
    const page=await contextA.newPage();
    watch(page);
    await ready(page);
    check(await page.locator('#gec-start').isVisible(),'the entry start is visible before admission');
    check(await page.locator('#gec-download-results').count()===1,'the local-only panel offers the results download');
    check(await page.locator('#gec-shell').getAttribute('data-local-only')==='1','the panel is marked as a local-only preview');
    await page.click('#gec-start');
    await trial(page,1);
    check(!(await page.locator('#gec-start').isVisible()),'start is hidden after admission');
    check(await page.locator('#gec-start').isDisabled(),'start stays disabled after admission');
    check(!(await page.locator('#gec-input-code').isVisible()),'entry fields are hidden after admission');
    check(await page.evaluate(()=>document.getElementById('gec-shell').dataset.entryHidden==='1'),'the panel is marked as retired');
    check(await page.evaluate(()=>document.activeElement===document.getElementById('canvas')),'the canvas owns the keyboard after admission');
    await page.locator('#canvas').press('ArrowLeft');
    await trial(page,2);
    await page.locator('#canvas').press('ArrowRight');
    await page.waitForFunction(()=>{const status=document.getElementById('gec-shell-status')?.textContent||'';return status.includes('本地测试完成')||status.includes('Local test complete');},null,{timeout:60000});
    check(await page.evaluate(()=>globalThis.GECBridge.status().state)==='finished_saved','the SDK reports finished_saved');
    const stored=await readStore(page);
    const session=stored.find(entry=>entry.kind==='local');
    check(!!session,'the local-only session is durable in real IndexedDB');
    check(!!session&&session.records.length===4&&!!session.completion&&session.completion.event_ids.length===4,'four records and a completion set are durable');
    const [download]=await Promise.all([page.waitForEvent('download'),page.click('#gec-download-results')]);
    const suggested=download.suggestedFilename();
    const saved=path.join(evidence,'web-local-results.jsonl');
    await download.saveAs(saved);
    const lines=fs.readFileSync(saved,'utf8').split('\n').filter(Boolean).map(line=>JSON.parse(line));
    expect(lines).toEqual(session.records);
    check(lines.length===4&&lines[0].payload.trial_id==='t1'&&lines[0].payload.choice==='left'&&lines[2].payload.trial_id==='t2'&&lines[2].payload.choice==='right','the JSONL keeps the original trial payload structure');
    check(lines[0].observed_time?.value===lines[0].payload.rt_ms&&lines[2].observed_time?.value===lines[2].payload.rt_ms,'the observed time matches the recorded RT value for value');
    check(lines[0].segment_id===lines[2].segment_id,'the JSONL keeps the original segment');
    check(/^local-results-/.test(suggested)&&suggested.endsWith('.jsonl'),'the download is named as a local results JSONL: '+suggested);
    await page.waitForFunction(()=>{const status=document.getElementById('gec-shell-status')?.textContent||'';return status.includes('已请求下载结果 JSONL');},null,{timeout:30000});
    check((await page.locator('#gec-shell-status').textContent()).includes('已请求下载结果 JSONL'),'the download request is announced');
    const beforeCancel=await readStore(page);
    const [cancelledDownload]=await Promise.all([page.waitForEvent('download'),page.click('#gec-download-results')]);
    let cancelled=false;
    try{await cancelledDownload.cancel();cancelled=true;}catch{/* the download may already be complete */}
    await page.waitForTimeout(300);
    const afterCancel=await readStore(page);
    expect(afterCancel.find(entry=>entry.kind==='local').records).toEqual(beforeCancel.find(entry=>entry.kind==='local').records);
    check(await page.locator('#gec-download-results').isVisible(),'the download stays available after a cancelled download');
    evidenceDocument.scenarios.success={session_id:session.id,records:session.records.length,declared:session.completion.event_ids.length,download:{file:saved,lines:lines.length,suggested},cancelled};
    await contextA.close();

    // ---- B. a failed finish: error, no saved state, records still exportable
    const contextB=await openContext();
    const pageB=await contextB.newPage();
    watch(pageB);
    await pageB.addInitScript(()=>{window.gecCall=(op,args)=>new Promise((resolve,reject)=>{const key='probe-'+Math.random().toString(36).slice(2);const deadline=Date.now()+60000;const attempt=()=>{if(!globalThis.GECBridge){if(Date.now()>deadline)reject(new Error('bridge timeout'));setTimeout(attempt,20);return;}globalThis.GECBridge.call(key,op,JSON.stringify(args));const poll=()=>{const value=globalThis.GECBridge.take(key);if(value!=null)resolve(JSON.parse(value));else if(Date.now()>deadline)reject(new Error('bridge timeout '+op));else setTimeout(poll,10);};poll();};attempt();});});
    await ready(pageB);
    const started=await pageB.evaluate(()=>window.gecCall('start',[{}]));
    check(started.state==='active','a direct local session starts');
    await pageB.evaluate(async payloads=>{const ids=[];for(const [kind,payload,schema] of payloads)ids.push((await window.gecCall('record',[kind,payload,schema,null])).event_id);await window.gecCall('commit',[{version:1,strategy:'trial_boundary_v1',dependencies:ids.slice(0,2),next_trial:1}]);await window.gecCall('commit',[{version:1,strategy:'trial_boundary_v1',dependencies:ids,next_trial:2}]);},PAYLOADS);
    const durableBefore=(await readStore(pageB)).find(entry=>entry.kind==='local');
    check(durableBefore.records.length===4&&!durableBefore.completion,'the direct session commits four records before the failure');
    await injectPutFailure(pageB);
    const failed=await pageB.evaluate(()=>window.gecCall('finish',[]));
    check(!!failed.error,'a failed finish reports an error: '+JSON.stringify(failed));
    check(failed.state!=='finished_saved'&&failed.state!=='local_committed','a failed finish never reports a saved state');
    check(await pageB.evaluate(()=>globalThis.GECBridge.status().state)!=='finished_saved','the SDK state is not finished after the failure');
    const partial=await pageB.evaluate(()=>window.gecCall('results_jsonl',[]));
    check(typeof partial==='string'&&partial.split('\n').filter(Boolean).length===4,'the durable records stay exportable during the failure');
    await restorePut(pageB);
    const retried=await pageB.evaluate(()=>window.gecCall('finish',[]));
    check(retried.state==='finished_saved','the retry finishes once the store is writable again: '+JSON.stringify(retried));
    const durableAfter=(await readStore(pageB)).find(entry=>entry.kind==='local');
    check(durableAfter.records.length===4&&!!durableAfter.completion,'the failed-then-retried session keeps four records and a completion');
    evidenceDocument.scenarios.failed_finish={error:failed.error,exportable_lines:partial.split('\n').filter(Boolean).length,retry:retried.state};
    await contextB.close();

    // ---- C. the real engine UI: a failed local write never says complete ---
    const contextC=await openContext();
    const pageC=await contextC.newPage();
    watch(pageC);
    await ready(pageC);
    await pageC.click('#gec-start');
    await trial(pageC,1);
    await pageC.locator('#canvas').press('ArrowLeft');
    await trial(pageC,2);
    await injectPutFailure(pageC);
    await pageC.locator('#canvas').press('ArrowRight');
    await pageC.waitForFunction(()=>{const status=document.getElementById('gec-shell-status')?.textContent||'';return status.includes('保存失败')||status.includes('Save failed');},null,{timeout:60000});
    const failedStatus=await pageC.locator('#gec-shell-status').textContent();
    check(!failedStatus.includes('本地测试完成')&&!failedStatus.includes('Local test complete'),'a failed local write never claims the local test complete: '+failedStatus);
    const storedC=(await readStore(pageC)).filter(entry=>entry.kind==='local').find(entry=>entry.records.length===2&&!entry.completion);
    check(!!storedC,'the committed first-trial records survive the failed write');
    evidenceDocument.scenarios.ui_failure={status:failedStatus,records:storedC?storedC.records.length:null};

    check(apiRequests.length===0,'zero requests were made to the API origin: '+JSON.stringify(apiRequests.slice(0,3)));
    check(failures.length===0,'every local-preview check passed');
  }finally{
    evidenceDocument.api_requests=apiRequests;
    evidenceDocument.checks=checks;
    evidenceDocument.failures=failures;
    evidenceDocument.injected_fault_page_errors=injectedErrors;
    fs.writeFileSync(path.join(evidence,'summary.json'),JSON.stringify(evidenceDocument,null,2));
    for(const context of contexts)await context.close().catch(()=>{});
    await new Promise(resolve=>server.close(resolve));
  }
  if(failures.length)throw new Error('local-preview checks failed: '+JSON.stringify(failures.map(entry=>entry.label)));
});
