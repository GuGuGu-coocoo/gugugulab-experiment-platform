import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {spawn,execFileSync} from 'node:child_process';
const binary=path.resolve('build/native/GEP Synthetic Experiment.app/Contents/MacOS/GEP Synthetic Experiment');
function launch(storage,extra=[]){return spawn(binary,['--headless','--','--synthetic-auto',...extra],{env:{...process.env,GEP_SYNTHETIC_STORAGE:storage},stdio:['ignore','pipe','pipe']});}
function until(proc,match){return new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>{proc.kill('SIGKILL');reject(new Error('native timeout'))},45000);proc.stdout.on('data',b=>{out+=b;if(out.includes(match)){clearTimeout(timer);resolve(out)}});proc.on('exit',code=>{if(!out.includes(match)){clearTimeout(timer);reject(new Error('native exit '+code+' '+out))}})});}
function snapshot(storage){return JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys; c=sqlite3.connect(sys.argv[1]); print(json.dumps([json.loads(r[0]) for r in c.execute("select value from sessions")]))',path.join(storage,'queue.sqlite')],{encoding:'utf8',stdio:['ignore','pipe','ignore']}));}
test('exported native process kill and authorized checkpoint recovery',async({page})=>{
 const storage=fs.mkdtempSync(path.join(os.tmpdir(),'gep-recovery-'));
 const first=launch(storage,['--stop-after-trial']);
 await expect.poll(()=>{try{return snapshot(storage).find(s=>s.kind==='session')?.checkpoint?.next_trial}catch{return null}},{timeout:30000}).toBe(1);first.kill('SIGKILL');await new Promise(r=>first.once('exit',r));
 const saved=snapshot(storage).find(s=>s.kind==='session');
 expect(saved.records).toHaveLength(2);expect(saved.checkpoint.next_trial).toBe(1);
 const oldIds=saved.records.map(e=>e.event_id),oldSegment=saved.segments[0];
 const c=JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'));
 await page.goto('http://admin.localhost:8000/login');await page.locator('[name=username]').fill(c.username);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.goto(fs.readFileSync('build/native/study_url.txt','utf8'));
 await page.locator('[name=session_id]').fill(saved.id);await page.getByRole('button',{name:'签发一次性恢复许可'}).click();
 const notice=await page.locator('.notice').textContent();const permit=notice.split('许可：')[1].trim();
 const resumed=launch(storage,['--recover='+saved.id,'--permit='+permit]);await until(resumed,'SYNTHETIC_DONE');await new Promise(r=>resumed.once('exit',r));
 expect(snapshot(storage)).toEqual([{id:saved.id,kind:'cleaned',state:'remote_acknowledged'}]);
 await page.goto(fs.readFileSync('build/native/study_url.txt','utf8'));await page.getByRole('button',{name:'创建 JSONL 固定快照'}).click();
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'下载 JSONL'}).click()]);
 const stream=await download.createReadStream();let text='';for await(const chunk of stream)text+=chunk;
 const records=text.trim().split('\n').map(JSON.parse).map(r=>r.record).filter(e=>e.session_id===saved.id);
 expect(records).toHaveLength(4);expect(new Set(records.map(e=>e.segment_id)).size).toBe(2);
 expect(records.filter(e=>e.segment_id===oldSegment).map(e=>e.event_id).sort()).toEqual(oldIds.sort());
 expect(records.filter(e=>e.event_type==='exp.rt').map(e=>e.payload.trial_id).sort()).toEqual(['t1','t2']);
});
