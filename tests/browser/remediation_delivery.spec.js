import {chromium,test} from '@playwright/test';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
/* P03R09B real-Chrome check for the shipped browser bridge and SDK.
 *
 * The pytest task driver starts the synthetic fault server
 * (tests/remediation/delivery_fault_app.py) and runs this spec with
 * GEP_REMEDIATION_JOB set. Without that environment the spec skips
 * (tools/verify_browser.py may collect this directory), but the Required
 * Verification driver always sets it and requires one passed, non-skipped test.
 *
 * Everything runs in real Chrome (a persistent profile, so the final scenario
 * can terminate the browser process and reopen the same real IndexedDB), the
 * real bridge call path the engine uses and real HTTP to the fault server:
 * persistent per-session delivery counting, restart, completion-only failures,
 * invalid/partial/lost/malformed ACKs, an aborted IndexedDB ACK write, a real
 * SQLite-free IndexedDB-only queue, multi-session isolation, Retry-After, the
 * independent 32-attempt budget, safe pause/authorized recovery, study_deleted
 * (during the experiment, on completion-only and after a same-round ACK),
 * offline-unsent, old-queue and partially shaped version-2 compatibility, and a
 * process kill after the ACK commit but before the completion response.
 *
 * The SDK's 1s automatic flush timer is neutralised in this harness so every
 * round below is explicit; the unexpired manual/automatic wait behaviour itself
 * is checked directly. The tests advance the persisted retryAt to "now" only
 * where a real retry round must be sent, and record every such manipulation.
 */
const jobPath=process.env.GEP_REMEDIATION_JOB;
const MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.json':'application/json'};

test('real Chrome delivery rounds: persistent counts, ACK progress and safe pause',async()=>{
  test.skip(!jobPath,'set GEP_REMEDIATION_JOB to run the real delivery check');
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

  const server=http.createServer((request,response)=>{
    const pathname=new URL(request.url,'http://127.0.0.1').pathname;
    const send=(file)=>{response.writeHead(200,{'Content-Type':MIME[path.extname(file)]||'application/octet-stream','Cache-Control':'no-store'});response.end(fs.readFileSync(file));};
    if(pathname==='/index.html'){
      const context=JSON.stringify(job.context).replace(/</g,'\\u003c');
      response.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'});
      response.end(`<!doctype html><html><head><meta charset="utf-8"><title>GEC delivery</title><script>globalThis.GEP_CONTEXT=${context};</script><script type="module" src="/gec/bridge.js"></script></head><body><canvas id="canvas" tabindex="0"></canvas></body></html>`);
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

  // One persistent real Chrome profile for the whole spec; the kill scenario
  // closes the browser process and reopens this exact profile and storage.
  const profileDir=path.join(evidence,'chrome-profile');
  let context=await chromium.launchPersistentContext(profileDir,{channel:'chrome',headless:true});
  await context.addInitScript(()=>{const original=window.setInterval;window.setInterval=(fn,ms)=>ms===1000?0:original(fn,ms);});
  let page=await context.newPage();
  const preparePage=async()=>{
    page.on('pageerror',error=>check(false,'uncaught page error: '+error.message));
    page.on('request',request=>{if(request.url().startsWith(fault))apiRequests.push(request.url());});
    await page.goto(origin+'/index.html');
    await waitReady();
  };

  const call=(op,args)=>page.evaluate(async({op,args})=>new Promise((resolve,reject)=>{
    const key='delivery-'+Math.random().toString(36).slice(2);
    const deadline=Date.now()+120000;
    const attempt=()=>{
      if(!globalThis.GECBridge){if(Date.now()>deadline)reject(new Error('bridge timeout'));setTimeout(attempt,20);return;}
      globalThis.GECBridge.call(key,op,JSON.stringify(args));
      const poll=()=>{const value=globalThis.GECBridge.take(key);if(value!=null)resolve(JSON.parse(value));else if(Date.now()>deadline)reject(new Error('bridge timeout '+op));else setTimeout(poll,10);};
      poll();
    };
    attempt();
  }),{op,args});
  const flush=manual=>call('flush',[manual]);
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
  const putRow=row=>page.evaluate(row=>new Promise((resolve,reject)=>{
    const r=indexedDB.open('gec-1');
    r.onsuccess=()=>{const db=r.result;const tx=db.transaction('sessions','readwrite');tx.objectStore('sessions').put(row);tx.oncomplete=()=>{db.close();resolve();};tx.onerror=()=>{db.close();reject(tx.error);};};
    r.onerror=()=>reject(r.error);
  }),row);
  const waitReady=async()=>{
    await page.waitForFunction(()=>globalThis.GECBridge!==undefined,null,{timeout:60000});
    await page.waitForFunction(()=>globalThis.GECBridge.status().state==='ready',null,{timeout:60000});
    // The bridge reply channel serializes results; a successful flush resolves
    // undefined, which JSON.stringify turns into undefined and take() reports as
    // "no reply". Wrap the shipped class method so a success is observable.
    await page.evaluate(async()=>{
      const {GEC}=await import('/gec/sdk.js');
      if(GEC.prototype.__deliveryFlushWrapped)return;
      const original=GEC.prototype.flush;
      GEC.prototype.flush=async function(manual){const result=await original.call(this,manual);return result===undefined?{ok:true}:result;};
      GEC.prototype.__deliveryFlushWrapped=true;
    });
  };
  const admission=async()=>{
    let started=await call('start',[{}]);
    if(started.error==='not_ready'){
      // A finished session leaves the client in its acknowledged state; a fresh
      // document gives the next admission a clean ready client.
      await page.reload();
      await waitReady();
      started=await call('start',[{}]);
    }
    check(!!started.session_id,'admission returned a session id: '+JSON.stringify(started));
    return started.session_id;
  };
  const recordCommitted=async()=>{
    const first=await call('record',['exp.rt',{trial_id:'t1',choice:'left',rt_ms:321.5},{id:'rt',version:'1'},null]);
    const second=await call('record',['exp.interaction',{action:'revise',confidence:null},{id:'interaction',version:'1'},null]);
    check(!!first.event_id&&!!second.event_id,'two records buffered: '+JSON.stringify([first.event_id,second.event_id]));
    const committed=await call('commit',[{version:1,strategy:'trial_boundary_v1',dependencies:[first.event_id,second.event_id],next_trial:1}]);
    check(committed.state==='local_committed','trial commit saved: '+JSON.stringify(committed));
    return [first.event_id,second.event_id];
  };
  const recordTwo=async()=>{
    const ids=await recordCommitted();
    const finished=await call('finish',[]);
    check(finished.state==='local_committed','finish declared the completion set: '+JSON.stringify(finished));
    return ids;
  };
  const deliveryOf=row=>row?.delivery??{};
  const expectDelivery=(row,expected,label)=>{
    const actual=deliveryOf(row);
    for(const key of Object.keys(expected))check(actual[key]===expected[key],label+' delivery.'+key+': '+actual[key]+' != '+expected[key]);
  };
  // The persisted retry schedule is real; a scenario that wants the next real
  // retry round advances the stored retryAt to now and records the manipulation.
  const flushNow=async id=>{
    manipulations.push({session_id:id,change:'retryAt=now',reason:'send the next real retry round'});
    await mutateRow(id,{retryAt:0});
    return flush(true);
  };

  const evidenceDocument={checks,failures,api_requests:apiRequests,manipulations,scenarios:{}};
  try{
    await preparePage();
    check(await page.evaluate(()=>globalThis.GECBridge.prepare_error===undefined),'the bridge prepared without an error');

    // ---- basic: counting, restart, completion-only, invalid, cleanup -------
    const basic=await admission();
    const basicEvents=await recordTwo();
    await script({[basic]:{events:[{kind:'fail',status:503,code:'unavailable',retryable:true}],
                           completion:[{kind:'fail',status:503,code:'unavailable',retryable:true}]}});
    await flush(true);
    let row=rowById(await readStore(),basic);
    expectDelivery(row,{initial_failed:true,retry_failures:0,ack_progress:0,round_id:1,inflight:false},'basic first failure');
    check(row.attempts===1&&row.pending.length===2&&deliveryOf(row).last_error==='http_503'&&deliveryOf(row).last_error_kind==='unavailable',
      'the first normal send failure kept pending/attempts and the parsed status/code: '+JSON.stringify([row.attempts,row.pending.length,deliveryOf(row).last_error,deliveryOf(row).last_error_kind]));
    for(const expected of [1,2,3]){
      await script({[basic]:{events:[{kind:'fail',status:503,code:'unavailable',retryable:true}],
                             completion:[{kind:'fail',status:503,code:'unavailable',retryable:true}]}});
      await flushNow(basic);
      row=rowById(await readStore(),basic);
      expectDelivery(row,{initial_failed:true,retry_failures:expected,ack_progress:0,round_id:expected+1,inflight:false},'basic retry '+expected);
    }
    check(row.paused!==true&&row.attempts===4,'three delivery failures do not pause and use the legacy attempts separately: '+JSON.stringify([row.paused,row.attempts]));
    const summary=await call('summary',[]);
    check(deliveryOf(summary).retry_failures===3&&summary.pending===2,'summary reports the persisted failures and pending: '+JSON.stringify({failures:deliveryOf(summary).retry_failures,pending:summary.pending}));
    evidenceDocument.scenarios.basic={session_id:basic,events:basicEvents,retry_failures:deliveryOf(row).retry_failures,attempts:row.attempts};

    // A real restart (page reload + fresh bridge prepare) keeps every count.
    const proofBefore=row.proof;
    await page.reload();
    await waitReady();
    row=rowById(await readStore(),basic);
    expectDelivery(row,{initial_failed:true,retry_failures:3,ack_progress:0,round_id:4,inflight:false},'basic restart');
    check(row.attempts===4&&row.pending.length===2&&row.records.length===2&&row.proof===proofBefore&&row.config.study_id===job.context.study_id,
      'the restart kept attempts/pending/records/proof/binding: '+JSON.stringify([row.attempts,row.pending.length,row.records.length]));

    // Completion-only failures use the same retry logic.
    await script({[basic]:{events:[{kind:'ack'}],
                           completion:[{kind:'fail',status:503,code:'unavailable',retryable:true},
                                       {kind:'fail',status:503,code:'unavailable',retryable:true},
                                       {kind:'fail',status:503,code:'unavailable',retryable:true}]}});
    await flushNow(basic);
    row=rowById(await readStore(),basic);
    expectDelivery(row,{initial_failed:true,retry_failures:0,ack_progress:1,round_id:5,inflight:false},'basic batch progress');
    check(row.pending.length===0&&deliveryOf(row).last_error==='http_503','the validated batch ACK reduced pending and the failed completion kept its error: '+JSON.stringify([row.pending.length,deliveryOf(row).last_error]));
    for(const expected of [1,2]){
      await flushNow(basic);
      row=rowById(await readStore(),basic);
      expectDelivery(row,{retry_failures:expected,ack_progress:1,inflight:false},'basic completion-only '+expected);
    }
    await script({[basic]:{completion:[{kind:'invalid'}]}});
    await flushNow(basic);
    row=rowById(await readStore(),basic);
    expectDelivery(row,{retry_failures:3,ack_progress:1,inflight:false},'basic invalid completion');
    check(row.complete_ack==null&&deliveryOf(row).last_error==='invalid_completion_ack','an invalid completion ACK never became a complete_ack: '+JSON.stringify([row.complete_ack,deliveryOf(row).last_error]));
    await script({[basic]:{completion:[{kind:'complete'}]}});
    await flushNow(basic);
    row=rowById(await readStore(),basic);
    check(row&&row.kind==='cleaned'&&row.state==='remote_acknowledged','the validated completion cleaned the session: '+JSON.stringify(row));
    check(row.delivery===undefined&&row.proof===undefined&&row.records===undefined,'the tombstone keeps no delivery/proof/records');
    evidenceDocument.scenarios.basic_cleanup={tombstone:row};

    // ---- partial and lost ACKs ---------------------------------------------
    const acks=await admission();
    await recordTwo();
    await script({[acks]:{events:[{kind:'partial'}]}});
    await flush(true);
    row=rowById(await readStore(),acks);
    expectDelivery(row,{initial_failed:true,retry_failures:0,ack_progress:0,round_id:1},'partial ACK');
    check(row.pending.length===2&&deliveryOf(row).last_error==='invalid_ack','a partial ACK is no progress and keeps pending: '+JSON.stringify([row.pending.length,deliveryOf(row).last_error]));
    await script({[acks]:{events:[{kind:'lost'}]}});
    await flushNow(acks);
    row=rowById(await readStore(),acks);
    check(!!row,'the lost-ACK row is still in the durable store: '+JSON.stringify((await readStore()).map(entry=>entry.id)));
    expectDelivery(row,{retry_failures:1,ack_progress:0,round_id:2},'lost ACK');
    check(row.pending.length===2,'a lost ACK keeps the pending records: '+row.pending.length);
    await script({[acks]:{events:[{kind:'ack'}],completion:[{kind:'complete'}]}});
    await flushNow(acks);
    check(rowById(await readStore(),acks).kind==='cleaned','the lost ACK was retried to completion');
    const acksState=await sessionState(acks);
    check(acksState.batch_requests===3&&acksState.events.length===2,'the retried batches deduplicated to exactly two server events: '+JSON.stringify([acksState.batch_requests,acksState.events.length]));
    evidenceDocument.scenarios.acks={session_id:acks,batch_requests:acksState.batch_requests,events:acksState.events.length};

    // ---- a real aborted IndexedDB ACK write is no progress ------------------
    const storage=await admission();
    await recordTwo();
    await script({[storage]:{events:[{kind:'ack'}],completion:[{kind:'complete'}]}});
    await page.evaluate(async()=>{
      const {GEC}=await import('/gec/sdk.js');
      const original=GEC.prototype.mutate;
      GEC.prototype.__deliveryOriginalMutate=original;
      let calls=0;
      GEC.prototype.mutate=function(fn){calls+=1;if(calls===2)return original.call(this,(store,tx)=>{fn(store,tx);tx.abort();});return original.call(this,fn);};
    });
    const injected=await flush(true);
    row=rowById(await readStore(),storage);
    check(!!injected.error,'the aborted local ACK write surfaced as a failure: '+JSON.stringify(injected));
    check(row.pending.length===2&&row.complete_ack==null,'an interrupted local ACK write produced no false progress: '+JSON.stringify([row.pending.length,row.complete_ack]));
    expectDelivery(row,{ack_progress:0,retry_failures:0,inflight:false,last_error_kind:'local_storage_error',terminal_reason:''},'interrupted ACK write');
    check(row.paused!==true,'a local ACK write failure does not permanently pause the session');
    await page.evaluate(async()=>{const {GEC}=await import('/gec/sdk.js');GEC.prototype.mutate=GEC.prototype.__deliveryOriginalMutate;});
    await flushNow(storage);
    check(rowById(await readStore(),storage).kind==='cleaned','the retried session completed after the interrupted write');
    evidenceDocument.scenarios.storage={session_id:storage,injected};

    // ---- multi-session isolation -------------------------------------------
    const first=await admission();
    await recordTwo();
    const second=await admission();
    check(second!==first,'a second admission created another session');
    await recordTwo();
    await script({[first]:{events:[{kind:'fail',status:503,code:'unavailable',retryable:true},{kind:'fail',status:503,code:'unavailable',retryable:true}],
                            completion:[{kind:'fail',status:503,code:'unavailable',retryable:true}]},
                  [second]:{events:[{kind:'fail',status:429,code:'throttled',retryable:true},{kind:'fail',status:429,code:'throttled',retryable:true}]}});
    await flush(true);
    const firstRow=rowById(await readStore(),first);
    const secondRow=rowById(await readStore(),second);
    expectDelivery(firstRow,{initial_failed:true,retry_failures:0,ack_progress:0,round_id:1},'multi first failure');
    expectDelivery(secondRow,{initial_failed:true,retry_failures:0,ack_progress:0,round_id:1,last_error_kind:'throttled'},'multi second failure');
    await mutateRow(first,{retryAt:0});
    await mutateRow(second,{retryAt:0});
    manipulations.push({session_id:[first,second],change:'retryAt=now',reason:'send the next real retry round for both sessions'});
    await flush(true);
    expectDelivery(rowById(await readStore(),first),{retry_failures:1,round_id:2,last_error_kind:'unavailable'},'multi first retry');
    expectDelivery(rowById(await readStore(),second),{retry_failures:1,round_id:2,last_error_kind:'throttled'},'the second session keeps its own count and code');
    const multiSummary=await call('summary',[]);
    check(deliveryOf(multiSummary).last_error_kind==='throttled'&&deliveryOf(multiSummary).retry_failures===1&&multiSummary.pending===2,
      'the current session summary shows its own delivery state: '+JSON.stringify(multiSummary.delivery));
    evidenceDocument.scenarios.multi={first,second,summary:multiSummary.delivery};

    // ---- Retry-After, the unexpired wait and the 32-attempt budget ----------
    const schedule=await admission();
    await recordTwo();
    await script({[schedule]:{events:[{kind:'fail',status:429,code:'throttled',retryable:true,retry_after:5},
                                     {kind:'fail',status:503,code:'unavailable',retryable:true},
                                     {kind:'fail',status:503,code:'unavailable',retryable:true}]}});
    const before=Date.now();
    await flush(true);
    row=rowById(await readStore(),schedule);
    check(row.retryAt>=before+5000,'Retry-After set the retry schedule: '+(row.retryAt-before));
    expectDelivery(row,{initial_failed:true,retry_failures:0,round_id:1,last_error_kind:'throttled'},'schedule failure');
    const waiting=await sessionState(schedule);
    await flush(false);
    check((await sessionState(schedule)).batch_requests===waiting.batch_requests,'the automatic wait sent no request');
    // The manual round respects the same durable schedule: no request, no round
    // and no counter change while retryAt is still in the future.
    await flush(true);
    check((await sessionState(schedule)).batch_requests===waiting.batch_requests,'the unexpired manual retry sent no request');
    row=rowById(await readStore(),schedule);
    expectDelivery(row,{retry_failures:0,round_id:1},'an unexpired manual retry is not a round');
    check(row.attempts===1,'an unexpired manual retry advanced no legacy attempt: '+row.attempts);
    await flushNow(schedule);
    row=rowById(await readStore(),schedule);
    expectDelivery(row,{retry_failures:1,round_id:2},'the manual retry after the wait');
    await mutateRow(schedule,{attempts:31,retryAt:0});
    manipulations.push({session_id:schedule,change:'attempts=31,retryAt=now',reason:'reach the independent 32-attempt boundary'});
    await flush(true);
    row=rowById(await readStore(),schedule);
    check(row.attempts===32&&row.paused===true,'the 32nd legacy attempt pauses the session: '+JSON.stringify([row.attempts,row.paused]));
    expectDelivery(row,{retry_failures:2,round_id:3},'the 32 budget and the delivery counter are independent');
    const paused=await sessionState(schedule);
    await flush(true);
    check((await sessionState(schedule)).batch_requests===paused.batch_requests,'a paused session sends no request even for a manual flush');
    evidenceDocument.scenarios.schedule={session_id:schedule,retry_at_delta:row.retryAt-before,attempts:row.attempts};

    // ---- permanent authorization, manual no-unpause, authorized recovery ---
    const policy=await admission();
    await recordTwo();
    await script({[policy]:{events:[{kind:'fail',status:403,code:'session_unavailable',retryable:false}]}});
    await flush(true);
    row=rowById(await readStore(),policy);
    check(row.paused===true,'a permanent authorization rejection pauses safely');
    expectDelivery(row,{initial_failed:false,retry_failures:0,round_id:1,last_error_kind:'session_unavailable'},'policy rejection');
    const policyPaused=await sessionState(policy);
    await flush(true);
    check((await sessionState(policy)).batch_requests===policyPaused.batch_requests,'a manual flush cannot unpause a safe pause');
    // A same-device continuation runs from a fresh page, exactly like a real
    // restart; the previous finish left the old client in its finished state.
    await page.reload();
    await waitReady();
    const recovered=await call('recover',[policy,'synthetic-permit']);
    check(recovered.state==='data_only','the authorized recovery is data only: '+JSON.stringify(recovered));
    row=rowById(await readStore(),policy);
    check(row.paused!==true&&row.attempts===0,'the authorized recovery unpaused the legacy budget: '+JSON.stringify([row.paused,row.attempts]));
    expectDelivery(row,{retry_failures:0,initial_failed:false,round_id:1},'recovery keeps the delivery counters');
    await script({[policy]:{events:[{kind:'ack'}],completion:[{kind:'complete'}]}});
    await flush(true);
    check(rowById(await readStore(),policy).kind==='cleaned','the recovered session completes after the authorized retry');
    evidenceDocument.scenarios.policy={session_id:policy,recovered:recovered.state};

    // ---- a retryable:false refusal with a non-authorization status pauses ---
    const refusal=await admission();
    await recordTwo();
    await script({[refusal]:{events:[{kind:'fail',status:400,code:'synthetic_refusal',retryable:false}]}});
    await flush(true);
    row=rowById(await readStore(),refusal);
    check(row.paused===true,'a retryable:false 400 pauses safely');
    expectDelivery(row,{initial_failed:false,retry_failures:0,ack_progress:0,round_id:1,last_error_kind:'synthetic_refusal',terminal_reason:''},'retryable:false refusal');
    const refused=await sessionState(refusal);
    await flush(true);
    check((await sessionState(refusal)).batch_requests===refused.batch_requests,'a manual flush cannot lift the refusal pause');
    await page.reload();
    await waitReady();
    const recoveredRefusal=await call('recover',[refusal,'synthetic-permit']);
    check(recoveredRefusal.state==='data_only','the authorized recovery of a non-authorization refusal is data only: '+JSON.stringify(recoveredRefusal));
    await script({[refusal]:{events:[{kind:'ack'}],completion:[{kind:'complete'}]}});
    await flush(true);
    check(rowById(await readStore(),refusal).kind==='cleaned','the recovered refusal completed after the authorized retry');
    evidenceDocument.scenarios.refusal={session_id:refusal,status:400};

    // ---- study_deleted terminates immediately with a legal local export -----
    const terminal=await admission();
    await recordTwo();
    await script({[terminal]:{events:[{kind:'fail',status:403,code:'study_deleted',retryable:false}]}});
    await flush(true);
    row=rowById(await readStore(),terminal);
    check(row.paused===true,'study_deleted pauses sending immediately');
    expectDelivery(row,{retry_failures:0,initial_failed:false,terminal_reason:'study_deleted',round_id:1},'terminal refusal');
    const terminalPaused=await sessionState(terminal);
    await flush(true);
    check((await sessionState(terminal)).batch_requests===terminalPaused.batch_requests,'study_deleted sends nothing more');
    const exported=await call('recovery_export',[]);
    const exportedText=JSON.stringify(exported);
    check(exported.records.length===2&&!exportedText.includes('proof')&&!exportedText.includes('token')&&!exportedText.includes('password'),
      'the legal unlocked local export still works without secrets: '+JSON.stringify({records:exported.records.length}));
    evidenceDocument.scenarios.terminal={session_id:terminal,export_records:exported.records.length};

    // ---- study_deleted during the experiment is terminal too ---------------
    const activeDeleted=await admission();
    await recordCommitted();
    await script({[activeDeleted]:{events:[{kind:'fail',status:403,code:'study_deleted',retryable:false}]}});
    await flush(true);
    row=rowById(await readStore(),activeDeleted);
    check(row.paused===true,'study_deleted during the experiment pauses immediately');
    expectDelivery(row,{retry_failures:0,initial_failed:false,ack_progress:0,round_id:0,terminal_reason:'study_deleted'},'experiment-time study_deleted');
    check(row.records.length===2,'the experiment-time study_deleted kept the raw records: '+row.records.length);
    const activeDeletedState=await sessionState(activeDeleted);
    await flush(true);
    check((await sessionState(activeDeleted)).batch_requests===activeDeletedState.batch_requests,'the experiment-time study_deleted sends nothing more');
    const activeExport=await call('recovery_export',[]);
    check(activeExport.records.length===2,'the experiment-time study_deleted still allows the legal export');
    evidenceDocument.scenarios.active_deleted={session_id:activeDeleted,records:activeExport.records.length};

    // ---- a same-round batch ACK does not hide the deletion terminal ---------
    const progressDeleted=await admission();
    await recordTwo();
    await script({[progressDeleted]:{events:[{kind:'ack'}],completion:[{kind:'fail',status:403,code:'study_deleted',retryable:false}]}});
    await flush(true);
    row=rowById(await readStore(),progressDeleted);
    check(row.paused===true&&deliveryOf(row).terminal_reason==='study_deleted','a same-round batch ACK still ends in the deletion terminal: '+JSON.stringify(deliveryOf(row)));
    expectDelivery(row,{ack_progress:1,retry_failures:0,round_id:1},'same-round ACK then study_deleted');
    check(row.pending.length===0&&row.kind==='session','the legal ACK reduced pending and no cleanup happened: '+JSON.stringify([row.pending.length,row.kind]));
    const progressExport=await call('recovery_export',[]);
    check(progressExport.records.length===2,'the deletion after an ACK still allows the legal export');
    evidenceDocument.scenarios.progress_deleted={session_id:progressDeleted,ack_progress:deliveryOf(row).ack_progress};

    // ---- malformed ACK shapes over real HTTP are never accepted -------------
    const malformed=await admission();
    await recordTwo();
    await script({[malformed]:{events:[{kind:'accepted_null'},{kind:'duplicate_null'},{kind:'accepted_absent'},{kind:'accepted_string'}]}});
    for(const shape of ['accepted_null','duplicate_null','accepted_absent','accepted_string']){
      await flushNow(malformed);
      row=rowById(await readStore(),malformed);
      check(row.pending.length===2&&row.complete_ack==null&&deliveryOf(row).last_error==='invalid_ack',
        'malformed event ACK ('+shape+') is no progress: '+JSON.stringify([row.pending.length,row.complete_ack,deliveryOf(row).last_error]));
      check(row.checkpoint!==null&&row.records.length===2,'malformed event ACK ('+shape+') kept the checkpoint dependency and raw records');
    }
    await script({[malformed]:{events:[{kind:'ack'}],completion:[{kind:'fail',status:503,code:'unavailable',retryable:true}]}});
    await flushNow(malformed);
    row=rowById(await readStore(),malformed);
    check(row&&row.pending.length===0&&deliveryOf(row).ack_progress===1,'the valid batch ACK after the malformed shapes still made progress: '+JSON.stringify([row?.pending?.length,deliveryOf(row).ack_progress]));
    for(const shape of ['missing_absent','missing_null','missing_string','declaration_absent','declaration_events_string','missing_nonempty']){
      await script({[malformed]:{completion:[{kind:shape}]}});
      await flushNow(malformed);
      row=rowById(await readStore(),malformed);
      check(row.complete_ack==null&&row.kind==='session'&&deliveryOf(row).last_error==='invalid_completion_ack',
        'malformed completion ACK ('+shape+') saved no receipt and no cleanup: '+JSON.stringify([row.complete_ack,row.kind,deliveryOf(row).last_error]));
      check(row.records.length===2&&row.checkpoint!==null,'malformed completion ACK ('+shape+') kept the records and checkpoint');
    }
    await script({[malformed]:{completion:[{kind:'complete'}]}});
    await flushNow(malformed);
    check(rowById(await readStore(),malformed).kind==='cleaned','the valid completion ACK after the malformed shapes still cleaned the session');
    evidenceDocument.scenarios.malformed={session_id:malformed,shapes:10};

    // ---- offline: an unsent automatic round is not counted ------------------
    const offline=await admission();
    await recordTwo();
    await script({[offline]:{events:[{kind:'fail',status:503,code:'unavailable',retryable:true},{kind:'fail',status:503,code:'unavailable',retryable:true}]}});
    await flush(true);
    row=rowById(await readStore(),offline);
    expectDelivery(row,{initial_failed:true,retry_failures:0,round_id:1},'offline first failure');
    await mutateRow(offline,{retryAt:0});
    manipulations.push({session_id:offline,change:'retryAt=now',reason:'make the offline automatic round due'});
    await context.setOffline(true);
    check(await page.evaluate(()=>navigator.onLine===false),'the browser really reports offline');
    const offlineState=await sessionState(offline);
    await flush(false);
    check((await sessionState(offline)).batch_requests===offlineState.batch_requests,'an offline automatic round sends no request');
    row=rowById(await readStore(),offline);
    expectDelivery(row,{retry_failures:0,round_id:1},'an offline unsent round is not counted');
    check(row.attempts===1,'an offline unsent round advanced no legacy attempt: '+row.attempts);
    await context.setOffline(false);
    await flush(false);
    row=rowById(await readStore(),offline);
    expectDelivery(row,{retry_failures:1,round_id:2},'the online automatic retry really counts');
    evidenceDocument.scenarios.offline={session_id:offline,attempts:row.attempts};

    // ---- an old queue record only gains the missing defaults ----------------
    const oldId=crypto.randomUUID();
    const oldToken=crypto.randomUUID().replaceAll('-','');
    await control('/control/session',{session_id:oldId,token:oldToken,instance_id:job.context.instance_id,study_id:job.context.study_id,
                                      release_id:job.context.release_id,build_id:job.context.build_id,proof:'old-proof'});
    const oldEvent={protocol_version:'gep/1',event_id:crypto.randomUUID(),session_id:oldId,segment_id:crypto.randomUUID(),sequence:1,
                    event_type:'exp.rt',schema_id:'rt',schema_version:'1',payload:{trial_id:'old',choice:'left',rt_ms:111}};
    await putRow({id:oldId,kind:'session',config:job.context,context:{token:oldToken},proof:'old-proof',records:[oldEvent],
                  pending:[oldEvent.event_id],segments:[oldEvent.segment_id],checkpoint:null,
                  completion:{event_ids:[oldEvent.event_id],segment_ids:[oldEvent.segment_id]},
                  complete_ack:null,front_locked:false,attempts:7,retryAt:0,paused:false});
    await page.reload();
    await waitReady();
    row=rowById(await readStore(),oldId);
    check(row.delivery_version===2&&typeof row.delivery==='object','the old record gained the delivery version and defaults');
    expectDelivery(row,{initial_failed:false,retry_failures:0,round_id:0,inflight:false},'old queue defaults');
    check(row.attempts===7&&row.retryAt===0&&row.paused===false&&row.records.length===1&&row.pending.length===1&&row.proof==='old-proof',
      'the old attempts/records/pending/proof were not zeroed: '+JSON.stringify([row.attempts,row.retryAt,row.paused,row.records.length,row.pending.length]));
    check(row.config.study_id===job.context.study_id&&row.config.release_id===job.context.release_id,'the old binding was kept');
    await script({[oldId]:{events:[{kind:'ack'}],completion:[{kind:'fail',status:503,code:'unavailable',retryable:true}]}});
    await flush(true);
    row=rowById(await readStore(),oldId);
    expectDelivery(row,{initial_failed:false,retry_failures:0,ack_progress:1,round_id:1,inflight:false},'old queue first counted round');
    check(row.pending.length===0&&row.attempts===8&&deliveryOf(row).last_error==='http_503','the old record delivered and used one legacy attempt: '+JSON.stringify([row.pending.length,row.attempts,deliveryOf(row).last_error]));
    await script({[oldId]:{completion:[{kind:'complete'}]}});
    await flushNow(oldId);
    check(rowById(await readStore(),oldId).kind==='cleaned','the old queue session completed after the real ACK');
    evidenceDocument.scenarios.oldqueue={session_id:oldId,attempts:8};

    // ---- a partially shaped version-2 record persists the missing defaults --
    const partialId=crypto.randomUUID();
    const partialToken=crypto.randomUUID().replaceAll('-','');
    await control('/control/session',{session_id:partialId,token:partialToken,instance_id:job.context.instance_id,study_id:job.context.study_id,
                                      release_id:job.context.release_id,build_id:job.context.build_id,proof:'partial-proof'});
    const partialEvent={protocol_version:'gep/1',event_id:crypto.randomUUID(),session_id:partialId,segment_id:crypto.randomUUID(),sequence:1,
                        event_type:'exp.rt',schema_id:'rt',schema_version:'1',payload:{trial_id:'partial',choice:'left',rt_ms:222}};
    await putRow({id:partialId,kind:'session',config:job.context,context:{token:partialToken},proof:'partial-proof',records:[partialEvent],
                  pending:[partialEvent.event_id],segments:[partialEvent.segment_id],checkpoint:null,
                  completion:null,complete_ack:null,front_locked:false,attempts:7,retryAt:0,paused:false,
                  delivery_version:2,delivery:{retry_failures:2}});
    await page.reload();
    await waitReady();
    row=rowById(await readStore(),partialId);
    const deliveryKeys=['initial_failed','retry_failures','round_id','last_error','last_error_kind','ack_progress','terminal_reason','inflight'];
    check(deliveryKeys.every(key=>Object.prototype.hasOwnProperty.call(row.delivery,key)),'the partial version-2 record persisted every missing default: '+JSON.stringify(row.delivery));
    expectDelivery(row,{initial_failed:false,retry_failures:2,round_id:0,inflight:false},'partial version-2 defaults');
    check(row.attempts===7&&row.retryAt===0&&row.paused===false&&row.records.length===1&&row.pending.length===1&&row.proof==='partial-proof',
      'the partial version-2 record kept attempts/records/pending/proof: '+JSON.stringify([row.attempts,row.records.length,row.pending.length]));
    check(row.config.study_id===job.context.study_id,'the partial version-2 binding was kept');
    evidenceDocument.scenarios.partialqueue={session_id:partialId,delivery:row.delivery};

    // ---- terminate Chrome after the ACK commit, before the completion ------
    const killAck=await admission();
    await recordTwo();
    await script({[killAck]:{events:[{kind:'ack'}],completion:[{kind:'delay',delay_ms:30000}]}});
    flush(true).catch(()=>{});
    const killDeadline=Date.now()+60000;
    while(Date.now()<killDeadline&&(await sessionState(killAck)).completion_requests<1)await new Promise(resolve=>setTimeout(resolve,100));
    check((await sessionState(killAck)).completion_requests>=1,'the completion request was outstanding when Chrome was terminated');
    const beforeKill=rowById(await readStore(),killAck);
    check(beforeKill.pending.length===0&&deliveryOf(beforeKill).ack_progress===1,'the ACK transaction was committed before the kill: '+JSON.stringify([beforeKill.pending.length,deliveryOf(beforeKill).ack_progress]));
    await context.close(); // terminates the real Chrome process holding this profile
    context=await chromium.launchPersistentContext(profileDir,{channel:'chrome',headless:true});
    await context.addInitScript(()=>{const original=window.setInterval;window.setInterval=(fn,ms)=>ms===1000?0:original(fn,ms);});
    page=await context.newPage();
    await preparePage();
    row=rowById(await readStore(),killAck);
    check(row.pending.length===0&&deliveryOf(row).ack_progress===1&&deliveryOf(row).retry_failures===0,
      'the reopened real storage kept the committed ACK progress and pending: '+JSON.stringify([row.pending.length,deliveryOf(row).ack_progress,deliveryOf(row).retry_failures]));
    expectDelivery(row,{last_error_kind:'outcome_unknown',inflight:false},'the interrupted completion on reopen');
    check(row.records.length===2&&row.proof===beforeKill.proof&&row.config.study_id===job.context.study_id,
      'the reopened storage kept the original record identity and binding: '+JSON.stringify([row.records.length,row.proof===beforeKill.proof]));
    await flushNow(killAck);
    check(rowById(await readStore(),killAck).kind==='cleaned','the interrupted completion finished after the real retry');
    const killState=await sessionState(killAck);
    check(killState.batch_requests===1&&killState.events.length===2,
      'the committed batch was never resent and no event duplicated: '+JSON.stringify([killState.batch_requests,killState.events.length]));
    evidenceDocument.scenarios.kill_ack={session_id:killAck,profile:profileDir,batch_requests:killState.batch_requests,events:killState.events.length};

    check(failures.length===0,'every delivery check passed');
  }finally{
    evidenceDocument.api_requests=apiRequests.slice(-40);
    evidenceDocument.checks=checks;
    evidenceDocument.failures=failures;
    evidenceDocument.manipulations=manipulations;
    fs.writeFileSync(path.join(evidence,'summary.json'),JSON.stringify(evidenceDocument,null,2));
    await context.close().catch(()=>{});
    await new Promise(resolve=>server.close(resolve));
  }
  if(failures.length)throw new Error('delivery checks failed: '+JSON.stringify(failures.map(entry=>entry.label)));
});
