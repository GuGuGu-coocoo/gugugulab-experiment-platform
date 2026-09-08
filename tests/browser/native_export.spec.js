import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';import path from 'node:path';import {execFile,execFileSync} from 'node:child_process';import {promisify} from 'node:util';
test('native records match protected JSONL export',async({page})=>{
 const storage=fs.mkdtempSync(path.join(os.tmpdir(),'gep-export-'));
 for(let i=0;i<2;i++)await promisify(execFile)(path.resolve('build/native/GEP Synthetic Experiment.app/Contents/MacOS/GEP Synthetic Experiment'),['--headless','--','--synthetic-auto'],{env:{...process.env,GEP_SYNTHETIC_STORAGE:storage}});
 const expectedIds=JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys; print(json.dumps([json.loads(r[0])["id"] for r in sqlite3.connect(sys.argv[1]).execute("select value from sessions")]))',path.join(storage,'queue.sqlite')],{encoding:'utf8'}));
 const c=JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'));
 await page.goto('http://admin.localhost:8000/login');await page.locator('[name=username]').fill(c.username);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.goto(fs.readFileSync('build/native/study_url.txt','utf8'));
 await page.getByRole('button',{name:'创建 JSONL 固定快照'}).click();
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'下载 JSONL'}).click()]);await download.saveAs('build/native/export.jsonl');
 const rows=fs.readFileSync('build/native/export.jsonl','utf8').trim().split('\n').map(JSON.parse).filter(r=>expectedIds.includes(r.record.session_id));
 expect(rows).toHaveLength(8);expect(new Set(rows.map(r=>r.record.event_id)).size).toBe(8);
 const rt=rows.filter(r=>r.record.event_type==='exp.rt').map(r=>r.record.payload.rt_ms).sort();expect(rt).toEqual([217.25,217.25,321.5,321.5]);
 for(const r of rows.filter(r=>r.record.event_type==='exp.interaction'))expect(r.record.payload).toEqual({action:'revise',selection:['shape_a','shape_c'],confidence:null,nested:{changes:[{from:null,to:'001'}],confirmed:false}});
});
