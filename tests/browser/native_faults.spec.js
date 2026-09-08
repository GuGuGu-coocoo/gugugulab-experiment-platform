import {test,expect} from '@playwright/test';
import fs from 'node:fs';import path from 'node:path';import os from 'node:os';import http from 'node:http';import {spawn,execFileSync} from 'node:child_process';
test('native lost ACK survives receiving process restart without duplicates',async({page})=>{
 test.setTimeout(60000);
 const config=JSON.parse(fs.readFileSync('build/native/connection.json','utf8'));
 let server;
 const start=()=>{server=spawn('.venv/bin/gunicorn',['gep.wsgi:application','--bind','127.0.0.1:8012','--workers','1','--access-logfile','/dev/null'],{env:{...process.env,PYTHONPATH:path.resolve('server'),GEP_DATA_DIR:path.resolve('local_data'),GEP_EXPECTED_INSTANCE:config.instance_id},stdio:'ignore'});};
 start();let dropped=false,restarted=false;
 const proxy=http.createServer((req,res)=>{const upstream=http.request({hostname:'127.0.0.1',port:8012,path:req.url,method:req.method,headers:req.headers},response=>{let chunks=[];response.on('data',c=>chunks.push(c));response.on('end',()=>{if(req.url.endsWith('/event-batches')&&!dropped&&response.statusCode===200){dropped=true;res.destroy();server.kill('SIGTERM');server.once('exit',()=>{start();restarted=true});}else{res.writeHead(response.statusCode,response.headers);res.end(Buffer.concat(chunks));}})});upstream.on('error',()=>res.destroy());req.pipe(upstream)});
 await new Promise(r=>proxy.listen(8013,'127.0.0.1',r));
 const storage=fs.mkdtempSync(path.join(os.tmpdir(),'gep-native-ack-'));const configPath=path.join(storage,'connection.json');fs.writeFileSync(configPath,JSON.stringify({...config,api_url:'http://127.0.0.1:8013'}));
 let native;
 try{
  await expect.poll(()=>new Promise(resolve=>{http.get({hostname:'127.0.0.1',port:8012,path:'/login',headers:{Host:'admin.localhost'}},r=>{r.resume();resolve(r.statusCode)}).on('error',()=>resolve(0))})).toBe(200);
  native=spawn(path.resolve('build/native/GEP Synthetic Experiment.app/Contents/MacOS/GEP Synthetic Experiment'),['--headless','--','--synthetic-auto','--config='+configPath],{env:{...process.env,GEP_SYNTHETIC_STORAGE:storage},stdio:['ignore','pipe','pipe']});
  let output='';native.stdout.on('data',c=>output+=c);const exit=await new Promise(r=>native.once('exit',r));
  expect(exit).toBe(0);expect(output).toContain('remote_acknowledged');expect(dropped&&restarted).toBe(true);
  const local=JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;print(json.dumps([json.loads(r[0]) for r in sqlite3.connect(sys.argv[1]).execute("select value from sessions")]))',path.join(storage,'queue.sqlite')],{encoding:'utf8'}));
  expect(local).toHaveLength(1);expect(local[0].kind).toBe('cleaned');
  const result=JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys; c=sqlite3.connect("local_data/gep.sqlite3"); rows=c.execute("select envelope from core_event where session_id=?",[sys.argv[1].replace("-","")]).fetchall();print(json.dumps([json.loads(r[0]) for r in rows]))',local[0].id],{encoding:'utf8'}));
  expect(result).toHaveLength(4);expect(new Set(result.map(e=>e.event_id)).size).toBe(4);
  expect(result.filter(e=>e.event_type==='exp.rt').map(e=>e.payload.rt_ms).sort()).toEqual([217.25,321.5]);
 }finally{native?.kill('SIGTERM');server?.kill('SIGTERM');await new Promise(r=>proxy.close(r));}
});
