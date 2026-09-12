import {test,expect} from '@playwright/test';import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import http from 'node:http';import {spawn,execFileSync} from 'node:child_process';
test('native records and checkpoints continue while real upload ACK is held',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'gep-nonblocking-'));const config=JSON.parse(fs.readFileSync('build/native/connection.json','utf8'));
 let release,held=false;const gate=new Promise(r=>release=r);
 const proxy=http.createServer((request,response)=>{const upstream=http.request({hostname:'127.0.0.1',port:8000,path:request.url,method:request.method,headers:request.headers},res=>{const chunks=[];res.on('data',b=>chunks.push(b));res.on('end',async()=>{if(request.url.endsWith('/event-batches')&&!held){held=true;await gate;}response.writeHead(res.statusCode,res.headers);response.end(Buffer.concat(chunks));})});upstream.on('error',()=>response.destroy());request.pipe(upstream);});
 await new Promise(r=>proxy.listen(8015,'127.0.0.1',r));const port=proxy.address().port;const configPath=path.join(dir,'connection.json');fs.writeFileSync(configPath,JSON.stringify({...config,api_url:'http://127.0.0.1:'+port}));
 const proc=spawn('godot',['--headless','--path','examples/synthetic_experiment','--script',path.resolve('tests/native/nonblocking_harness.gd')],{env:{...process.env,GEP_SYNTHETIC_STORAGE:dir,GEP_TEST_CONFIG:configPath},stdio:['ignore','pipe','pipe']});
 let output='';proc.stdout.on('data',b=>output+=b);proc.stderr.on('data',b=>output+=b);const exited=new Promise(r=>proc.once('exit',r));
 try{
  await expect.poll(()=>fs.existsSync(path.join(dir,'saved_while_upload_pending')),{timeout:10000}).toBe(true);expect(held).toBe(true);
  const saved=JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,sys;print(sqlite3.connect(sys.argv[1]).execute("select value from sessions").fetchone()[0])',path.join(dir,'queue.sqlite')],{encoding:'utf8'}));
  expect(saved.records).toHaveLength(4);expect(saved.pending).toHaveLength(4);expect(saved.checkpoint.next_trial).toBe(2);
  const count=Number(execFileSync('.venv/bin/python',['-c','import sqlite3,sys;print(sqlite3.connect("local_data/gep.sqlite3").execute("select count(*) from core_event where session_id=?",[sys.argv[1].replace("-","")]).fetchone()[0])',saved.id],{encoding:'utf8'}));expect(count).toBe(2);
  release();expect(await exited,output).toBe(0);expect(output).toContain('NATIVE_RECORDING_CONTINUED_WITH_ACK_HELD');
  const timing=JSON.parse(fs.readFileSync(path.join(dir,'saved_while_upload_pending'),'utf8'));console.log('Observed synthetic response+finish ms (not a physical timing bound):',timing.response_and_finish_ms);
 }finally{release();proc.kill('SIGTERM');await new Promise(r=>proxy.close(r));}
});
