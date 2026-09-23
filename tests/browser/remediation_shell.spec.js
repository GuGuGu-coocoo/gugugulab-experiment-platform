import {chromium,test} from '@playwright/test';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
/* P03R09C real-Chrome check of the shipped Web shell.
 *
 * The pytest task driver exports the real Godot Web build (with the shipped
 * packages/gec_web bridge/shell/sdk copied next to it) and starts the synthetic
 * fault server; this spec serves the export with the same context injection the
 * real host uses (server/core/hosting.py) and drives the real participation
 * panel in real Chrome:
 *
 *   - the real, unpatched bridge answers a successful flush and a flush that is
 *     still inside the persisted backoff wait, so the shell never stays busy;
 *   - the entry form retires after admission (no front controls, no focus
 *     during the stimulus);
 *   - the persisted summary drives the failure surface: the generic first
 *     failure and the exact 1st/2nd retry counts, retry + export at the 3rd,
 *     hidden after the session is received;
 *   - the failure export is a real download with the original records and no
 *     credentials, and it keeps the local queue;
 *   - a real IndexedDB write abort on the completion save shows the local save
 *     failure and never claims a saved finish.
 *
 * Without GEP_REMEDIATION_JOB the spec skips (tools/verify_browser.py may
 * collect this directory); the Required Verification driver always sets it and
 * requires exactly one passed, non-skipped test.
 */
const jobPath=process.env.GEP_REMEDIATION_JOB;
const MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.json':'application/json','.wasm':'application/wasm','.pck':'application/octet-stream','.png':'image/png'};

test('real Chrome shell: persisted failure surface, legal export and the real bridge',async()=>{
  test.skip(!jobPath,'set GEP_REMEDIATION_JOB to run the real shell check');
  test.setTimeout(900000);
  const job=JSON.parse(fs.readFileSync(jobPath,'utf8'));
  const evidence=job.evidence_dir;
  fs.mkdirSync(evidence,{recursive:true});
  const checks=[];
  const failures=[];
  const apiRequests=[];
  const manipulations=[];
  const check=(ok,label,detail)=>{const entry={label,ok:!!ok,detail:detail??null};checks.push(entry);if(!ok)failures.push(entry);};
  const fault=job.api_url;

  // The real Godot Web build served with the host's own context injection.
  const server=http.createServer((request,response)=>{
    const pathname=new URL(request.url,'http://127.0.0.1').pathname;
    if(pathname==='/'||pathname==='/index.html'){
      const html=fs.readFileSync(path.join(job.package_dir,'index.html'),'utf8');
      const context=JSON.stringify(job.context).replace(/</g,'\\u003c');
      response.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'});
      response.end(html.replace('<head>',`<head><script>globalThis.GEP_CONTEXT=${context};</script>`));
      return;
    }
    const file=path.join(job.package_dir,pathname.slice(1));
    if(fs.existsSync(file)&&fs.statSync(file).isFile()){
      response.writeHead(200,{'Content-Type':MIME[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});
      response.end(fs.readFileSync(file));
      return;
    }
    response.writeHead(404);response.end();
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;

  const control=async(pathname,body)=>{
    const response=await fetch(fault+pathname,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body??{})});
    return response.json();
  };
  const script=sessions=>control('/control/script',{sessions});
  const faultState=async()=>{
    const response=await fetch(fault+'/control/state');
    return response.json();
  };
  const sessionState=async sid=>(await faultState()).sessions[sid]??{};

  const profileDir=path.join(evidence,'chrome-profile');
  let context=await chromium.launchPersistentContext(profileDir,{channel:'chrome',headless:true});
  // The SDK's 1s automatic flush timer is neutralised so every round below is
  // explicit through the real bridge or the real panel (a recorded
  // manipulation); the persisted backoff itself is still exercised.
  await context.addInitScript(()=>{const original=window.setInterval;window.setInterval=(fn,ms)=>ms===1000?0:original(fn,ms);});
  let page=await context.newPage();
  page.on('pageerror',error=>check(false,'uncaught page error: '+error.message));
  page.on('request',request=>{if(request.url().startsWith(fault))apiRequests.push(request.url());});

  const statusText=()=>page.locator('#gec-shell-status').textContent();
  const readStore=()=>page.evaluate(()=>new Promise((resolve,reject)=>{
    const r=indexedDB.open('gec-1');
    r.onsuccess=()=>{const db=r.result;const tx=db.transaction('sessions');const q=tx.objectStore('sessions').getAll();tx.oncomplete=()=>{resolve(q.result);db.close();};tx.onerror=()=>reject(tx.error);};
    r.onerror=()=>reject(r.error);
  }));
  const rowById=(rows,id)=>rows.find(entry=>entry.id===id);
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
  // The real bridge call path the Godot Web shell uses; a missing reply is a
  // hard timeout, so a successful flush or an unexpired wait must really answer.
  const call=(op,args)=>page.evaluate(async({op,args})=>new Promise((resolve,reject)=>{
    const key='shell-'+Math.random().toString(36).slice(2);
    const deadline=Date.now()+120000;
    const attempt=()=>{
      if(!globalThis.GECBridge){if(Date.now()>deadline)reject(new Error('bridge timeout'));setTimeout(attempt,20);return;}
      globalThis.GECBridge.call(key,op,JSON.stringify(args));
      const poll=()=>{const value=globalThis.GECBridge.take(key);if(value!=null)resolve(JSON.parse(value));else if(Date.now()>deadline)reject(new Error('bridge timeout '+op));else setTimeout(poll,10);};
      poll();
    };
    attempt();
  }),{op,args});
  const waitPanel=async()=>{
    await page.waitForSelector('#gec-shell',{timeout:180000});
    await page.waitForFunction(()=>{const button=document.getElementById('gec-start');return button&&!button.disabled;},null,{timeout:180000});
  };
  const deliveryOf=row=>row?.delivery??{};
  const advanceRetryAt=async id=>{
    manipulations.push({session_id:id,change:'retryAt=now',reason:'send the next real retry round through the shell'});
    await mutateRow(id,{retryAt:0});
  };
  const failureScript=()=>({events:[{kind:'fail',status:503,code:'unavailable',retryable:true},{kind:'fail',status:503,code:'unavailable',retryable:true},{kind:'fail',status:503,code:'unavailable',retryable:true},{kind:'fail',status:503,code:'unavailable',retryable:true}],
                            completion:[{kind:'fail',status:503,code:'unavailable',retryable:true}]});
  const panelFailureVisible=()=>page.evaluate(()=>{const box=document.getElementById('gec-shell-failure');return !!box&&box.style.display!=='none'&&!box.hidden;});
  const buttonVisible=id=>page.evaluate(id=>{const node=document.getElementById(id);return !!node&&node.style.display!=='none';},id);
  // Explicit bounded polling: an async predicate must never be handed to
  // waitForFunction, which would treat the pending Promise itself as truthy.
  const waitForRow=async(id,predicate,timeout=120000)=>{
    const end=Date.now()+timeout;
    let row=null;
    while(Date.now()<end){
      row=rowById(await readStore(),id);
      if(row&&predicate(row))return row;
      await page.waitForTimeout(150);
    }
    throw new Error('row condition timeout for '+id+': '+JSON.stringify(row));
  };
  const waitForStatus=async(fragment,timeout=60000)=>{
    const end=Date.now()+timeout;
    while(Date.now()<end){
      const text=await statusText();
      if(text.includes(fragment))return text;
      await page.waitForTimeout(200);
    }
    throw new Error('status timeout for '+fragment+': '+await statusText());
  };

  const evidenceDocument={checks,failures,api_requests:apiRequests,manipulations,scenarios:{}};
  try{
    // ---- the real Web build boots the shipped bridge and panel ------------
    await page.goto(origin+'/index.html');
    await waitPanel();
    check(await page.evaluate(()=>globalThis.GECBridge!==undefined),'the Godot Web build mounted the real bridge');
    check(await page.locator('#gec-input-code').count()===0,'anonymous mode renders no roster field');
    check(await page.locator('#gec-input-password').count()===0,'anonymous mode renders no password field');
    check(!(await panelFailureVisible()),'the failure surface starts hidden');
    check(await page.evaluate(()=>document.activeElement===null||document.activeElement.tagName!=='INPUT'),'no front input holds the focus before admission');

    // ---- admission retires the front form --------------------------------
    await page.click('#gec-start');
    await page.waitForFunction(()=>{const button=document.getElementById('gec-start');return button&&button.style.display==='none'&&button.disabled;},null,{timeout:120000});
    check(await page.evaluate(()=>[...document.querySelectorAll('#gec-shell input')].every(input=>input.disabled&&input.closest('label')?.style.display==='none')),'every entry field is hidden and disabled after admission');
    check(await page.evaluate(()=>!['gec-input-code','gec-input-password','gec-input-short-code','gec-input-recovery','gec-input-permit'].some(id=>{const node=document.getElementById(id);return node&&!node.disabled;})),'no front control stays usable during the stimulus');
    check(await page.evaluate(()=>{const active=document.activeElement;return !active||active.tagName!=='INPUT';}),'the entry focus was released for the stimulus');
    const sessionId=(await readStore()).find(row=>row.kind==='session')?.id??null;
    check(!!sessionId,'the real Web admission created a durable session: '+JSON.stringify(sessionId));

    // ---- the science trials finish and the failures start -----------------
    await script({'*':failureScript()});
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(400);
    await page.keyboard.press('ArrowRight');
    const completed=await waitForRow(sessionId,row=>!!row.completion);
    check(!!completed.completion&&completed.records.length===4,'the two real trials declared their four-record completion set: '+JSON.stringify([completed.records.length,completed.completion?.event_ids?.length]));
    await waitForStatus('等待上传确认');

    // ---- the first failure and the three real retry failures -------------
    await call('flush',[true]);
    let row=await waitForRow(sessionId,entry=>!!entry.delivery&&entry.delivery.round_id===1);
    check(deliveryOf(row).initial_failed===true&&deliveryOf(row).retry_failures===0,'the first normal failure only set initial_failed: '+JSON.stringify(deliveryOf(row)));
    check((await waitForStatus('服务器暂时不可用')).includes('服务器暂时不可用'),'the first failure announces the generic retry state without a retry count: '+await statusText());
    check(!(await panelFailureVisible()),'the failure surface is not shown before three retry failures');
    let previous=await statusText();
    for(const expected of [1,2,3]){
      await advanceRetryAt(sessionId);
      await call('flush',[true]);
      row=await waitForRow(sessionId,entry=>!!entry.delivery&&entry.delivery.retry_failures===expected);
      check(deliveryOf(row).retry_failures===expected,'retry failure '+expected+' is persisted: '+JSON.stringify(deliveryOf(row)));
      if(expected<3){
        check(!(await panelFailureVisible()),'retry '+expected+' keeps the retry-in-progress surface');
        const text=await waitForStatus('已连续 '+String(expected)+' 次发送失败');
        check(text!==previous,'the same-state refresh announced the changed count at retry '+expected+': '+text);
        previous=text;
      }else{
        await waitForStatus('可重试上传');
        check(await panelFailureVisible(),'the third retry failure shows the failure surface');
        check(await buttonVisible('gec-retry')&&await buttonVisible('gec-failure-export'),'both failure actions are visible at the third failure');
        previous=await statusText();
      }
    }
    check(row.pending.length===row.records.length&&row.pending.length>0&&row.complete_ack==null&&row.kind==='session','no fake receipt: pending and raw records stay local: '+JSON.stringify([row.pending.length,row.records.length,row.complete_ack,row.kind]));
    evidenceDocument.scenarios.failure_surface={session_id:sessionId,retry_failures:deliveryOf(row).retry_failures,status:previous};

    // ---- the failure export is a real download without secrets ------------
    const downloadPromise=page.waitForEvent('download',{timeout:60000});
    await page.click('#gec-failure-export');
    const download=await downloadPromise;
    const downloadPath=await download.path();
    const exported=fs.readFileSync(downloadPath,'utf8');
    check(exported.includes('exp.rt')&&exported.includes('exp.interaction'),'the failure download contains the original records');
    check(!exported.includes('proof')&&!exported.includes('token')&&!exported.includes('password'),'the failure download contains no credentials');
    check(exported.includes(sessionId),'the failure download is bound to the real session');
    check(rowById(await readStore(),sessionId).pending.length===rowById(await readStore(),sessionId).records.length,'the download never deletes or rewrites the local queue');
    evidenceDocument.scenarios.export={session_id:sessionId,filename:download.suggestedFilename()};

    // ---- the real bridge answers an unexpired wait without sending --------
    const waiting=await sessionState(sessionId);
    const bridgeReply=await call('flush',[true]);
    check(bridgeReply&&typeof bridgeReply==='object','the unpatched real bridge answered the flush inside the backoff wait: '+JSON.stringify(bridgeReply));
    check((await sessionState(sessionId)).batch_requests===waiting.batch_requests,'the unexpired wait sent no request');
    row=rowById(await readStore(),sessionId);
    check(deliveryOf(row).retry_failures===3&&row.attempts===4,'the unexpired wait is not a round and changes no counter: '+JSON.stringify([deliveryOf(row).retry_failures,row.attempts]));
    check(!(await page.evaluate(()=>document.getElementById('gec-retry').disabled)),'the shell is not left busy after the wait');

    // ---- a shell retry that succeeds hides the surface --------------------
    await script({[sessionId]:{events:[{kind:'ack'}],completion:[{kind:'complete'}]}});
    await advanceRetryAt(sessionId);
    await page.click('#gec-retry');
    await waitForStatus('已上传');
    const hidden=async()=>{const end=Date.now()+60000;while(Date.now()<end){if(!(await panelFailureVisible()))return true;await page.waitForTimeout(200);}return false;};
    check(await hidden(),'the received tombstone hides the failure surface');
    row=await waitForRow(sessionId,entry=>entry.kind==='cleaned');
    check(row&&row.kind==='cleaned'&&row.state==='remote_acknowledged','the received session is a cleaned tombstone: '+JSON.stringify(row));
    check(await page.evaluate(()=>{const retry=document.getElementById('gec-retry');const failureExport=document.getElementById('gec-failure-export');return (!retry||retry.offsetParent===null)&&(!failureExport||failureExport.offsetParent===null);}),'the failure actions are hidden after receipt');
    evidenceDocument.scenarios.received={session_id:sessionId,status:await statusText()};

    // ---- a real IndexedDB completion-save abort never claims a finish -----
    await page.reload();
    await waitPanel();
    await page.evaluate(()=>{
      const original=IDBObjectStore.prototype.put;
      globalThis.__gecOriginalPut=original;
      IDBObjectStore.prototype.put=function(value,...rest){
        if(globalThis.__gecBlockFinish&&value&&typeof value==='object'&&value.completion){this.transaction.abort();return;}
        return original.call(this,value,...rest);
      };
    });
    await page.evaluate(()=>{globalThis.__gecBlockFinish=true;});
    await page.click('#gec-start');
    await page.waitForFunction(()=>{const button=document.getElementById('gec-start');return button&&button.style.display==='none';},null,{timeout:120000});
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(400);
    await page.keyboard.press('ArrowRight');
    const failedStatus=await waitForStatus('本地保存失败');
    check(failedStatus.includes('本地保存失败'),'the real IndexedDB save failure is announced as a local save failure: '+failedStatus);
    check(!failedStatus.includes('等待上传确认'),'a failed finish never claims the records are saved and waiting');
    const failedRow=(await readStore()).find(entry=>entry.kind==='session');
    check(!!failedRow&&failedRow.kind==='session'&&failedRow.completion==null,'the failed finish kept the session local and undeclared: '+JSON.stringify([failedRow?.kind,failedRow?.completion]));
    check((failedRow?.records?.length??0)>=2,'the failed finish kept the raw records: '+JSON.stringify(failedRow?.records?.length));
    evidenceDocument.scenarios.local_save_failure={status:failedStatus,session_id:failedRow?.id??null};

    check(failures.length===0,'every shell check passed');
  }finally{
    evidenceDocument.api_requests=apiRequests.slice(-40);
    evidenceDocument.checks=checks;
    evidenceDocument.failures=failures;
    evidenceDocument.manipulations=manipulations;
    fs.writeFileSync(path.join(evidence,'summary.json'),JSON.stringify(evidenceDocument,null,2));
    await context.close().catch(()=>{});
    await new Promise(resolve=>server.close(resolve));
  }
  if(failures.length)throw new Error('shell checks failed: '+JSON.stringify(failures.map(entry=>entry.label)));
});
