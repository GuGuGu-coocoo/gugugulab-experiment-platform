import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
/* P03R10/P03R10R real-Chrome checks for the Web isolated preview golden.
 *
 * The pytest task drivers export the real Godot Web builds (with the R10
 * experiment-data wrapper compiled in), point the job at the shipped
 * packages/gec_web modules and a preview application context, and run this spec
 * with GEP_REMEDIATION_JOB set. Without that environment the default test
 * skips (tools/verify_browser.py may collect this directory), but the Required
 * Verification driver always sets it and requires one passed test.
 *
 * The job shape selects exactly one registered test so the statistics stay
 * exact: without ``demo`` the scientific-task preview test runs; with
 * ``demo: true`` the P03R10R defaults-demo test runs against the test-only
 * project copy that replaced only its bootstrap script.
 *
 * Science scenario: the real exported engine runs the real scientific task
 * through the real wrapper, the real bridge and the real SDK against real
 * IndexedDB: both trial structures are reconciled value for value with the
 * fixed golden below, the envelope keys are checked exactly (no extra
 * collection), the observed time is the explicit recorded RT (never a guessed
 * clock), the JSONL download keeps the original envelopes, and no request
 * reaches the API origin.
 *
 * Demo scenario: the same real engine/bridge/SDK path executes the
 * ``configure_defaults`` fields/provider short records, the explicit
 * four-parameter override and the source mutations through the real GDScript
 * wrapper; the same golden is reconciled against IndexedDB and the downloaded
 * JSONL, and the direct cycle/over-deep inputs the demo attempted were rejected
 * without adding any record.
 */
const jobPath=process.env.GEP_REMEDIATION_JOB;
const job=jobPath?JSON.parse(fs.readFileSync(jobPath,'utf8')):null;
const MIME={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.wasm':'application/wasm','.pck':'application/octet-stream','.png':'image/png','.json':'application/json'};
const GOLDEN=[
  {event_type:'exp.rt',schema_id:'rt',schema_version:'1',observed:true,
   payload:{trial_id:'t1',choice:'left',response_status:'responded'}},
  {event_type:'exp.interaction',schema_id:'interaction',schema_version:'1',observed:false,
   payload:{action:'revise',selection:['shape_a','shape_c'],confidence:null,nested:{changes:[{from:null,to:'001'}],confirmed:false}}},
  {event_type:'exp.rt',schema_id:'rt',schema_version:'1',observed:true,
   payload:{trial_id:'t2',choice:'right',response_status:'responded'}},
  {event_type:'exp.interaction',schema_id:'interaction',schema_version:'1',observed:false,
   payload:{action:'revise',selection:['shape_a','shape_c'],confidence:null,nested:{changes:[{from:null,to:'001'}],confirmed:false}}},
];
const DEMO_GOLDEN=[
  {event_type:'exp.rt',schema_id:'rt',schema_version:'1',observed:true,
   payload:{trial_id:'t1',choice:'left',rt_ms:321.5,response_status:'responded'}},
  GOLDEN[1],
  {event_type:'exp.rt',schema_id:'rt',schema_version:'1',observed:true,
   payload:{trial_id:'t2',choice:'right',rt_ms:217.25,response_status:'responded'}},
  GOLDEN[3],
];
const RT_KEYS=['choice','response_status','rt_ms','trial_id'];
const ENVELOPE_KEYS=['protocol_version','event_id','session_id','segment_id','sequence','event_type','schema_id','schema_version','payload'];
const deepEqual=(a,b)=>{
  if(a===b)return true;
  if(a===null||b===null||typeof a!==typeof b)return false;
  if(Array.isArray(a))return Array.isArray(b)&&a.length===b.length&&a.every((value,index)=>deepEqual(value,b[index]));
  if(typeof a==='object'){const ka=Object.keys(a),kb=Object.keys(b);
    return ka.length===kb.length&&ka.every(key=>Object.prototype.hasOwnProperty.call(b,key)&&deepEqual(a[key],b[key]));}
  return false;
};

const startServer=async()=>{
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
  return {server,origin:`http://127.0.0.1:${server.address().port}`};
};

const readStore=page=>page.evaluate(()=>new Promise((resolve,reject)=>{
  const r=indexedDB.open('gec-1');
  r.onsuccess=()=>{const db=r.result;const tx=db.transaction('sessions');const q=tx.objectStore('sessions').getAll();
    tx.oncomplete=()=>{resolve(q.result);db.close();};tx.onerror=()=>reject(tx.error);};
  r.onerror=()=>reject(r.error);
}));

const waitForDemoSummary=async(page,lines)=>{
  await page.waitForFunction(()=>{
    const status=document.getElementById('gec-shell-status')?.textContent||'';
    return status.includes('本地测试完成')||status.includes('Local test complete');
  },null,{timeout:120000});
  for(let attempt=0;attempt<40;attempt++){
    for(const line of lines){
      const at=line.indexOf('DATA_DEFAULTS_WEB ');
      if(at>=0){try{return JSON.parse(line.slice(at+'DATA_DEFAULTS_WEB '.length));}catch{return null;}}
    }
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  return null;
};

const runScience=async({browser})=>{
  test.setTimeout(300000);
  const evidence=job.evidence_dir;
  fs.mkdirSync(evidence,{recursive:true});
  const checks=[];
  const failures=[];
  const apiRequests=[];
  const check=(ok,label,detail)=>{const entry={label,ok:!!ok,detail:detail??null};checks.push(entry);if(!ok)failures.push(entry);};

  const {server,origin}=await startServer();

  const context=await browser.newContext({locale:'zh-CN'});
  const page=await context.newPage();
  page.on('request',request=>{if(request.url().startsWith(job.api_url))apiRequests.push(request.url());});
  page.on('pageerror',error=>check(false,'uncaught page error: '+error.message));
  const trial=async number=>page.waitForFunction(n=>{
    const label=document.getElementById('canvas')?.getAttribute('aria-label')||'';
    return label.includes(`第 ${n} 次试次`)||label.includes(`Trial ${n}`);
  },number,{timeout:60000});

  const evidenceDocument={checks,failures,api_requests:apiRequests,scenarios:{}};
  try{
    await page.goto(origin+'/index.html');
    await page.waitForFunction(()=>globalThis.GECBridge!==undefined,null,{timeout:120000});
    await page.waitForFunction(()=>globalThis.GECBridge.status().state==='ready',null,{timeout:120000});
    await page.locator('#gec-shell').waitFor({state:'visible',timeout:60000});
    check(await page.locator('#gec-shell').getAttribute('data-local-only')==='1','the panel is a local-only preview');
    check(await page.locator('#gec-download-results').count()===1,'the local preview offers the results download');

    // The real engine runs the real task through the real wrapper and bridge.
    await page.click('#gec-start');
    await trial(1);
    await page.locator('#canvas').press('ArrowLeft');
    await trial(2);
    await page.locator('#canvas').press('ArrowRight');
    await page.waitForFunction(()=>{const status=document.getElementById('gec-shell-status')?.textContent||'';
      return status.includes('本地测试完成')||status.includes('Local test complete');},null,{timeout:60000});
    check(await page.evaluate(()=>globalThis.GECBridge.status().state)==='finished_saved','the SDK reports finished_saved');

    const stored=await readStore(page);
    const session=stored.find(entry=>entry.kind==='local');
    check(!!session,'the local preview session is durable in real IndexedDB');
    const records=session?.records??[];
    check(records.length===4,'exactly four records, no extra collection: '+records.length);
    check(session?.completion?.event_ids?.length===4,'the completion set covers the four records');
    const expectedKeys=[...ENVELOPE_KEYS].sort();
    for(let index=0;index<GOLDEN.length;index++){
      const golden=GOLDEN[index];
      const record=records[index]??{};
      const keys=Object.keys(record).filter(key=>key!=='observed_time').sort();
      check(deepEqual(keys,expectedKeys),'record '+index+' has exactly the documented envelope keys: '+JSON.stringify(Object.keys(record)));
      check(record.event_type===golden.event_type&&record.schema_id===golden.schema_id&&record.schema_version===golden.schema_version,
            'record '+index+' type/schema: '+JSON.stringify([record.event_type,record.schema_id,record.schema_version]));
      check(record.sequence===index+1,'record '+index+' keeps the scientific sequence: '+record.sequence);
      if(golden.observed){
        // The RT is a real measurement of the actual key press: the golden is
        // the deterministic structure plus observed === payload RT.
        check(record.payload?.trial_id===golden.payload.trial_id&&record.payload?.choice===golden.payload.choice
              &&record.payload?.response_status===golden.payload.response_status
              &&typeof record.payload?.rt_ms==='number'&&isFinite(record.payload.rt_ms)&&record.payload.rt_ms>=0
              &&deepEqual(Object.keys(record.payload).sort(),RT_KEYS),
              'record '+index+' keeps the deterministic RT payload: '+JSON.stringify(record.payload));
        check(record.observed_time?.value===record.payload?.rt_ms&&record.observed_time?.unit==='ms'
              &&record.observed_time?.clock_id==='host_monotonic'&&record.observed_time?.source==='Godot Time.get_ticks_usec',
              'record '+index+' observed time equals the measured RT: '+JSON.stringify(record.observed_time));
      }else{
        check(deepEqual(record.payload,golden.payload),'record '+index+' payload value for value: '+JSON.stringify(record.payload));
        check(!Object.prototype.hasOwnProperty.call(record,'observed_time'),'record '+index+' has no implicit clock');
      }
    }
    check(records[0]?.segment_id===records[3]?.segment_id,'one segment across the four records');

    const [download]=await Promise.all([page.waitForEvent('download'),page.click('#gec-download-results')]);
    const saved=path.join(evidence,'web-defaults-results.jsonl');
    await download.saveAs(saved);
    const lines=fs.readFileSync(saved,'utf8').split('\n').filter(Boolean).map(line=>JSON.parse(line));
    check(deepEqual(lines,records),'the JSONL download keeps the original envelopes');
    check(lines.length===4&&lines.every((line,index)=>line.event_type===GOLDEN[index].event_type
          &&(GOLDEN[index].observed
            ? deepEqual(Object.keys(line.payload).sort(),RT_KEYS)&&line.observed_time?.value===line.payload?.rt_ms
            : deepEqual(line.payload,GOLDEN[index].payload))),'the JSONL reconciles value for value with the golden');
    check(apiRequests.length===0,'zero requests were made to the API origin: '+JSON.stringify(apiRequests.slice(0,3)));
    check(failures.length===0,'every Web golden check passed');
    evidenceDocument.scenarios.web_preview={session_id:session?.id??null,records:records.length,
      observed:records.map(record=>record.observed_time?.value??null),download:{file:saved,lines:lines.length}};
  }finally{
    evidenceDocument.api_requests=apiRequests;
    evidenceDocument.checks=checks;
    evidenceDocument.failures=failures;
    fs.writeFileSync(path.join(evidence,'summary.json'),JSON.stringify(evidenceDocument,null,2));
    await context.close().catch(()=>{});
    await new Promise(resolve=>server.close(resolve));
  }
  if(failures.length)throw new Error('Web golden checks failed: '+JSON.stringify(failures.map(entry=>entry.label)));
};

const runDemo=async({browser})=>{
  test.setTimeout(300000);
  const evidence=job.evidence_dir;
  fs.mkdirSync(evidence,{recursive:true});
  const checks=[];
  const failures=[];
  const apiRequests=[];
  const consoleLines=[];
  const check=(ok,label,detail)=>{const entry={label,ok:!!ok,detail:detail??null};checks.push(entry);if(!ok)failures.push(entry);};

  const {server,origin}=await startServer();

  const context=await browser.newContext({locale:'zh-CN'});
  const page=await context.newPage();
  page.on('request',request=>{if(request.url().startsWith(job.api_url))apiRequests.push(request.url());});
  page.on('console',message=>consoleLines.push(message.text()));
  page.on('pageerror',error=>check(false,'uncaught page error: '+error.message));

  const evidenceDocument={checks,failures,api_requests:apiRequests,console:consoleLines.slice(0,200),scenarios:{}};
  try{
    await page.goto(origin+'/index.html');
    await page.waitForFunction(()=>globalThis.GECBridge!==undefined,null,{timeout:120000});
    await page.waitForFunction(()=>globalThis.GECBridge.status().state==='ready',null,{timeout:120000});
    await page.locator('#gec-shell').waitFor({state:'visible',timeout:60000});
    check(await page.locator('#gec-shell').getAttribute('data-local-only')==='1','the panel is a local-only preview');
    check(await page.locator('#gec-download-results').count()===1,'the local preview offers the results download');

    // The real engine runs the demo through the real wrapper and bridge.
    await page.click('#gec-start');
    const demo=await waitForDemoSummary(page,consoleLines);
    const scriptErrors=consoleLines.filter(line=>line.includes('SCRIPT ERROR')||line.includes('Parse Error'));
    check(scriptErrors.length===0,'the exported engine reported no script or parse error: '+JSON.stringify(scriptErrors.slice(0,3)));
    check(!!demo,'the exported engine printed the demo summary');
    check(deepEqual(demo?.failures,[]),'the in-engine demo reported no failures: '+JSON.stringify(demo?.failures));
    check(demo?.cycle?.error==='invalid_value','the demo direct cycle was rejected in the exported engine: '+JSON.stringify(demo?.cycle));
    check(demo?.deep?.error==='invalid_value','the demo over-deep value was rejected in the exported engine: '+JSON.stringify(demo?.deep));
    check(demo?.state==='finished_saved','the demo finished the local test: '+JSON.stringify(demo?.state));
    check(await page.evaluate(()=>globalThis.GECBridge.status().state)==='finished_saved','the SDK reports finished_saved');

    const stored=await readStore(page);
    const session=stored.find(entry=>entry.kind==='local');
    check(!!session,'the demo session is durable in real IndexedDB');
    const records=session?.records??[];
    check(records.length===4,'exactly four records and no pseudo event: '+records.length);
    check(session?.completion?.event_ids?.length===4,'the completion set covers the four records');
    const expectedKeys=[...ENVELOPE_KEYS].sort();
    for(let index=0;index<DEMO_GOLDEN.length;index++){
      const golden=DEMO_GOLDEN[index];
      const record=records[index]??{};
      const keys=Object.keys(record).filter(key=>key!=='observed_time').sort();
      check(deepEqual(keys,expectedKeys),'record '+index+' has exactly the documented envelope keys: '+JSON.stringify(Object.keys(record)));
      check(record.event_type===golden.event_type&&record.schema_id===golden.schema_id&&record.schema_version===golden.schema_version,
            'record '+index+' type/schema: '+JSON.stringify([record.event_type,record.schema_id,record.schema_version]));
      check(record.sequence===index+1,'record '+index+' keeps the scientific sequence: '+record.sequence);
      check(deepEqual(record.payload,golden.payload),
            'record '+index+' payload value for value after the source mutations: '+JSON.stringify(record.payload));
      if(golden.observed){
        check(record.observed_time?.value===golden.payload.rt_ms&&record.observed_time?.unit==='ms'
              &&record.observed_time?.clock_id==='host_monotonic'&&record.observed_time?.source==='synthetic fixture'
              &&record.observed_time?.epoch==='p03r10r',
              'record '+index+' observed time equals the explicit synthetic fixture RT: '+JSON.stringify(record.observed_time));
      }else{
        check(!Object.prototype.hasOwnProperty.call(record,'observed_time'),'record '+index+' has no implicit clock');
      }
    }
    check(records[0]?.segment_id===records[3]?.segment_id,'one segment across the four records');

    const [download]=await Promise.all([page.waitForEvent('download'),page.click('#gec-download-results')]);
    const saved=path.join(evidence,'web-defaults-demo-results.jsonl');
    await download.saveAs(saved);
    const lines=fs.readFileSync(saved,'utf8').split('\n').filter(Boolean).map(line=>JSON.parse(line));
    check(deepEqual(lines,records),'the JSONL download keeps the original envelopes');
    check(deepEqual(lines.map(line=>line.payload),DEMO_GOLDEN.map(golden=>golden.payload)),
          'the JSONL reconciles value for value with the defaults golden');
    check(apiRequests.length===0,'zero requests were made to the API origin: '+JSON.stringify(apiRequests.slice(0,3)));
    check(demo?.events?.length===4&&demo.events.every(entry=>entry.event_id),'four event identities were produced');
    check(failures.length===0,'every Web defaults check passed');
    evidenceDocument.scenarios.web_defaults_demo={session_id:session?.id??null,records:records.length,
      observed:records.map(record=>record.observed_time?.value??null),demo:demo??null,
      download:{file:saved,lines:lines.length}};
  }finally{
    evidenceDocument.api_requests=apiRequests;
    evidenceDocument.checks=checks;
    evidenceDocument.failures=failures;
    fs.writeFileSync(path.join(evidence,'summary.json'),JSON.stringify(evidenceDocument,null,2));
    await context.close().catch(()=>{});
    await new Promise(resolve=>server.close(resolve));
  }
  if(failures.length)throw new Error('Web defaults checks failed: '+JSON.stringify(failures.map(entry=>entry.label)));
};

if(jobPath&&job.demo){
  test('real Web defaults demo: the exported wrapper routes the defaults golden into IndexedDB and JSONL',runDemo);
}else{
  test('real Web local-only preview: the two scientific structures reconcile value for value',async({browser})=>{
    test.skip(!jobPath,'set GEP_REMEDIATION_JOB to run the real Web golden check');
    await runScience({browser});
  });
}
