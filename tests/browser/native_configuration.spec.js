import {test,expect} from '@playwright/test';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import http from 'node:http';import crypto from 'node:crypto';import {spawn,execFileSync} from 'node:child_process';
const binary=path.resolve('build/native/GEP Synthetic Experiment.app/Contents/MacOS/GEP Synthetic Experiment');
function snapshot(dir){return JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;print(json.dumps([json.loads(r[0]) for r in sqlite3.connect(sys.argv[1]).execute("select value from sessions")]))',path.join(dir,'queue.sqlite')],{encoding:'utf8',stdio:['ignore','pipe','ignore']}));}
function launch(dir,file,args=[]){const proc=spawn(binary,['--headless','--','--config='+file,...args],{env:{...process.env,GEP_SYNTHETIC_STORAGE:dir},stdio:['ignore','pipe','pipe']});let text='';proc.stdout.on('data',b=>text+=b);proc.stderr.on('data',b=>text+=b);return {proc,done:new Promise(r=>proc.once('exit',r)),text:()=>text};}
for(const [name,patch,error] of [['version',{config_version:'unsupported'},'invalid_configuration'],['study',{study_id:crypto.randomUUID()},'http_422']])test(`exported native rejects wrong external ${name}`,async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'gep-config-')),file=path.join(dir,'connection.json');fs.writeFileSync(file,JSON.stringify({...JSON.parse(fs.readFileSync('build/native/connection.json','utf8')),...patch}));const run=launch(dir,file,['--synthetic-auto']);
 try{expect(await run.done,run.text()).toBe(2);expect(run.text()).toContain(error);expect(snapshot(dir).filter(s=>s.kind==='session')).toHaveLength(0);}finally{run.proc.kill('SIGTERM');}
});
test('replacing native external configuration never retargets old pending records',async()=>{
 test.setTimeout(40000);const dir=fs.mkdtempSync(path.join(os.tmpdir(),'gep-target-')),file=path.join(dir,'connection.json');const original=JSON.parse(fs.readFileSync('build/native/connection.json','utf8'));
 let online=false,failures=0,newTargetRequests=0;
 const old=http.createServer((req,res)=>{if(req.url.endsWith('/event-batches')&&!online){failures++;res.writeHead(503);res.end();return;}const upstream=http.request({hostname:'127.0.0.1',port:8000,path:req.url,method:req.method,headers:req.headers},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res)});upstream.on('error',()=>res.destroy());req.pipe(upstream)});
 const other=http.createServer((req,res)=>{newTargetRequests++;res.writeHead(400);res.end()});await new Promise(r=>old.listen(8016,'127.0.0.1',r));await new Promise(r=>other.listen(8017,'127.0.0.1',r));
 fs.writeFileSync(file,JSON.stringify({...original,api_url:'http://127.0.0.1:8016'}));let first=launch(dir,file,['--synthetic-auto','--stop-after-trial']),second;
 try{
  await expect.poll(()=>failures,{timeout:15000}).toBeGreaterThan(0);first.proc.kill('SIGKILL');await first.done;const saved=snapshot(dir).find(s=>s.kind==='session');expect(saved.records).toHaveLength(2);expect(saved.pending).toHaveLength(2);
  fs.writeFileSync(file,JSON.stringify({...original,study_id:crypto.randomUUID(),api_url:'http://127.0.0.1:8017'}));online=true;second=launch(dir,file);
  await expect.poll(()=>snapshot(dir).find(s=>s.id===saved.id)?.pending.length,{timeout:15000}).toBe(0);
  const restored=snapshot(dir).find(s=>s.id===saved.id);expect(restored.config).toEqual(saved.config);expect(restored.records).toEqual(saved.records);expect(restored.checkpoint).toEqual(saved.checkpoint);expect(newTargetRequests).toBe(0);
  const remote=JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;print(json.dumps([json.loads(r[0]) for r in sqlite3.connect("local_data/gep.sqlite3").execute("select envelope from core_event where session_id=?",[sys.argv[1].replace("-","")])]))',saved.id],{encoding:'utf8'}));expect(remote.sort((a,b)=>a.sequence-b.sequence)).toEqual(saved.records);
 }finally{first.proc.kill('SIGTERM');second?.proc.kill('SIGTERM');if(second)await second.done;await new Promise(r=>old.close(r));await new Promise(r=>other.close(r));}
});
