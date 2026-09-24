import {test,expect} from '@playwright/test';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import {spawn,execFileSync} from 'node:child_process';
import {isolated,connectionConfig} from './isolated_target.mjs';
// Isolated instance only: the packaged program, the server database and the
// researcher GUI all come from the orchestrator (GEP_ISO_*), never from the dev
// instance or the protected acceptance database.
const target=isolated();
const config=connectionConfig(target);
const CONNECTION=path.resolve(target.connection);
// The packaged program must be the orchestrator's bound this-round artifact,
// never the historical build/native default.
if(!target.native_app)throw new Error('GEP_ISO_NATIVE_APP is required for the packaged run');
const NATIVE_APP=path.resolve(target.native_app);
for(const finished of [false,true]) test('native authorized data-only recovery unpauses uploads without replaying trials; finished='+finished,async({page})=>{
 test.setTimeout(60000);
 const dir=fs.mkdtempSync(path.join(target.scratch||os.tmpdir(),'gep-policy-'));
 const snapshot=()=>JSON.parse(execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;print(sqlite3.connect(sys.argv[1]).execute("select value from sessions").fetchone()[0])',path.join(dir,'queue.sqlite')],{encoding:'utf8',stdio:['ignore','pipe','ignore']}));
 const first=spawn(NATIVE_APP,['--headless','--',`--config=${CONNECTION}`,'--synthetic-auto','--stop-after-trial'],{env:{...process.env,GEP_SYNTHETIC_STORAGE:dir},stdio:'ignore'});
 const exit=new Promise(r=>first.once('exit',r));
 try{await expect.poll(()=>{try{return snapshot().checkpoint?.next_trial}catch{return null}},{timeout:15000}).toBe(1);}finally{first.kill('SIGTERM');await exit;}
 const saved=snapshot();
 execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;c=sqlite3.connect(sys.argv[1]);s=json.loads(c.execute("select value from sessions").fetchone()[0]);s["checkpoint"].pop("strategy") if sys.argv[2]=="false" else None;s.update(paused=True,attempts=32,retry_at=9999999999,pending=[e["event_id"] for e in s["records"]]);c.execute("update sessions set value=?",[json.dumps(s)]);c.commit()',path.join(dir,'queue.sqlite'),String(finished)]);
 if(finished){
  const declaration={event_ids:saved.records.map(e=>e.event_id),segment_ids:saved.segments};
  const response=await page.request.post(saved.config.api_url+'/v1/participant/sessions/'+saved.id+'/completion',{data:declaration,headers:{Authorization:'Bearer '+saved.context.token}});expect(response.status()).toBe(200);
  execFileSync('.venv/bin/python',['-c','import sqlite3,json,sys;c=sqlite3.connect(sys.argv[1]);s=json.loads(c.execute("select value from sessions").fetchone()[0]);s["completion"]=json.loads(sys.argv[2]);c.execute("update sessions set value=?",[json.dumps(s)]);c.commit();d=sqlite3.connect(sys.argv[3]);d.execute("update core_session set expires_at=? where id=?",["2000-01-01 00:00:00",s["id"].replace("-","")]);d.commit()',path.join(dir,'queue.sqlite'),JSON.stringify(declaration),target.db]);
 }
 const c=target.credentials;await page.goto(target.admin+'/login');await page.locator('[name=username]').fill(c.username);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 // New GUI contract (P0307 module pages): the one-time permit is issued from the
 // dedicated UUID form on the sessions module.
 await page.goto(target.admin+'/studies/'+config.study_id+'/sessions');
 const permit_form=page.locator('form[data-recover-uuid-form="1"]');
 await permit_form.locator('[name=session_id]').fill(saved.id);await permit_form.getByRole('button',{name:'签发一次性恢复许可'}).click();
 const permit=(await page.locator('.notice').textContent()).split('许可：')[1].trim(),fixture=path.join(dir,'recovery.json');fs.writeFileSync(fixture,JSON.stringify({recovery_session:saved.id,permit}),{mode:0o600});
 const proc=spawn('godot',['--headless','--path','examples/synthetic_experiment','--script',path.resolve('tests/native/recovery_policy_harness.gd')],{env:{...process.env,GEP_SYNTHETIC_STORAGE:dir,GEP_TEST_CONFIG:CONNECTION,GEP_TEST_RECOVERY:fixture},stdio:['ignore','pipe','pipe']});
 let out='';proc.stdout.on('data',b=>out+=b);proc.stderr.on('data',b=>out+=b);
 try{await expect.poll(()=>out,{timeout:30000}).toContain('NATIVE_DATA_ONLY_RECOVERY_VERIFIED');}finally{proc.kill('SIGTERM');}
 const after=snapshot();expect(after.id).toBe(saved.id);if(finished) expect(after.kind).toBe('cleaned');else{expect(after.records).toEqual(saved.records);expect(after.segments).toEqual(saved.segments);expect(after.pending).toEqual([]);}
});
