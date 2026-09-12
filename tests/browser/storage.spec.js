import {test,expect} from '@playwright/test';
import fs from 'node:fs';
const config=JSON.parse(fs.readFileSync('build/native/connection.json','utf8'));
async function setup(page){
 await page.route('**/sdk.js',r=>r.fulfill({path:'packages/gec_web/sdk.js',contentType:'text/javascript'}));
 await page.goto('http://experiment.localhost:8000/');
 await page.evaluate(async config=>{const {GEC}=await import('/sdk.js');window.client=new GEC(config);await client.prepare();clearInterval(client.timer);await client.begin();},config);
}
const payload={trial_id:'t1',choice:'left',rt_ms:321.5,response_status:'responded'};
test('IndexedDB atomic abort, offline reload, lost ACK, active retention and cleanup',async({page,context})=>{
 await setup(page);
 const id=await page.evaluate(async payload=>{const e=client.record('exp.rt',payload,{id:'rt',version:'1'});await client.commit({version:1,strategy:'trial_boundary_v1',dependencies:[e.event_id],next_trial:1});return e.event_id;},payload);
 await context.setOffline(true);
 await expect(page.evaluate(()=>client.flush())).rejects.toThrow();
 expect(await page.evaluate(async()=>{const s=await client.get(client.id);return s.pending.length})).toBe(1);
 await context.setOffline(false);
 let dropped=false;
 await page.route('**/event-batches',async route=>{if(!dropped){dropped=true;await route.fetch();await route.abort();}else await route.continue();});
 await expect(page.evaluate(()=>client.flush())).rejects.toThrow();
 expect(await page.evaluate(async()=>{const s=await client.get(client.id);return s.pending.length})).toBe(1);
 await page.evaluate(()=>client.flush());
 expect(await page.evaluate(async()=>{const s=await client.get(client.id);return [s.pending.length,s.records.length,!!s.checkpoint]})).toEqual([0,1,true]);
 // Force a real aborted IndexedDB transaction; no memory-only substitute.
 const aborted=await page.evaluate(async()=>{try{await client.mutate((store,tx)=>{store.put({id:'aborted',kind:'bad'});tx.abort()});return false}catch{return !(await client.get('aborted'))}});
 expect(aborted).toBe(true);
 const session=await page.evaluate(()=>client.id);
 await page.evaluate(()=>client.finish());
 await page.reload();
 await page.evaluate(async config=>{const {GEC}=await import('/sdk.js');window.client=new GEC(config);await client.prepare();clearInterval(client.timer)},config);
 expect(await page.evaluate(async session=>{const s=await client.get(session);return s.records[0].event_id},session)).toBe(id);
 await page.evaluate(()=>client.flush());
 const tombstone=await page.evaluate(session=>client.get(session),session);
 expect(tombstone).toEqual({id:session,kind:'cleaned',state:'remote_acknowledged'});
 await page.evaluate(()=>client.close());
});
test('single writer and shared-device new participation locks old front recovery',async({page,context})=>{
 await setup(page);
 const old=await page.evaluate(()=>client.id);
 const other=await context.newPage();await other.goto('http://experiment.localhost:8000/');
 await other.route('**/sdk.js',r=>r.fulfill({path:'packages/gec_web/sdk.js',contentType:'text/javascript'}));
 const result=await other.evaluate(async config=>{const {GEC}=await import('/sdk.js');const c=new GEC(config);try{await c.prepare();return 'incorrect'}catch(e){return e.message}},config);
 expect(result).toBe('writer_busy');await other.close();
 await page.evaluate(()=>client.close());await page.reload();
 await page.evaluate(async config=>{const {GEC}=await import('/sdk.js');window.client=new GEC(config);await client.prepare();clearInterval(client.timer);await client.begin()},config);
 expect(await page.evaluate(async id=>(await client.get(id)).front_locked,old)).toBe(true);
 await page.evaluate(()=>client.close());
});
test('ACK persistence failure retains raw and interrupted cleanup resumes',async({page})=>{
 await setup(page);
 await page.evaluate(async payload=>{client.record('exp.rt',payload,{id:'rt',version:'1'});await client.commit({version:1,strategy:'trial_boundary_v1',dependencies:[],next_trial:1});client.originalMutate=client.mutate.bind(client);client.mutate=fn=>client.originalMutate((store,tx)=>{fn(store,tx);tx.abort()});},payload);
 await expect(page.evaluate(()=>client.flush())).rejects.toThrow();
 expect(await page.evaluate(async()=>{const s=await client.get(client.id);return [s.records.length,s.pending.length]})).toEqual([1,1]);
 await page.evaluate(async()=>{client.mutate=client.originalMutate;await client.flush();await client.finish();client.cleanup=async()=>{throw new Error('injected_cleanup_interruption')};});
 await expect(page.evaluate(()=>client.flush())).rejects.toThrow();
 const id=await page.evaluate(()=>client.id);
 expect(await page.evaluate(async()=>{const s=await client.get(client.id);return [!!s.complete_ack,s.records.length,s.pending.length]})).toEqual([true,1,0]);
 await page.evaluate(()=>client.close());await page.reload();
 await page.evaluate(async config=>{const {GEC}=await import('/sdk.js');window.client=new GEC(config);await client.prepare();clearInterval(client.timer);await client.flush()},config);
 expect(await page.evaluate(id=>client.get(id),id)).toEqual({id,kind:'cleaned',state:'remote_acknowledged'});
 await page.evaluate(()=>client.close());
});
test('configuration replacement cannot retarget pending records',async({page})=>{
 await setup(page);
 const id=await page.evaluate(async payload=>{client.record('exp.rt',payload,{id:'rt',version:'1'});await client.commit();return client.id},payload);
 await page.evaluate(()=>client.close());await page.reload();
 await page.evaluate(async config=>{const {GEC}=await import('/sdk.js');window.client=new GEC({...config,api_url:'http://127.0.0.1:9',study_id:crypto.randomUUID()});await client.prepare();clearInterval(client.timer);await client.flush()},config);
 const old=await page.evaluate(id=>client.get(id),id);
 expect(old.config.api_url).toBe(config.api_url);expect(old.config.study_id).toBe(config.study_id);expect(old.pending).toHaveLength(0);
 await page.evaluate(()=>client.close());
});
test('unknown storage version is preserved and rejected',async({page})=>{
 await page.route('**/sdk.js',r=>r.fulfill({path:'packages/gec_web/sdk.js',contentType:'text/javascript'}));await page.goto('http://experiment.localhost:8000/');
 await page.evaluate(()=>new Promise(resolve=>{const r=indexedDB.open('gec-1',2);r.onupgradeneeded=()=>r.result.createObjectStore('future');r.onsuccess=()=>{r.result.close();resolve()}}));
 const message=await page.evaluate(async config=>{const {GEC}=await import('/sdk.js');const c=new GEC(config);try{await c.prepare();return 'bad'}catch(e){c.close();return e.name}},config);
 expect(message).toBe('VersionError');
 expect(await page.evaluate(()=>new Promise(resolve=>{const r=indexedDB.open('gec-1');r.onsuccess=()=>{resolve(r.result.version);r.result.close()}}))).toBe(2);
});

test('successful completion clears the earlier offline error after durable cleanup',async({page,context})=>{
 await setup(page);
 await page.evaluate(async payload=>{client.record('exp.rt',payload,{id:'rt',version:'1'});await client.commit();await client.finish()},payload);
 await context.setOffline(true);
 await page.evaluate(async()=>{try{await client.flush()}catch(error){client.report(error)}});
 expect(await page.evaluate(()=>client.status().error)).toBeTruthy();
 expect(await page.evaluate(async()=>(await client.get(client.id)).pending.length)).toBe(1);
 await context.setOffline(false);
 await page.evaluate(()=>client.flush());
 expect(await page.evaluate(()=>client.status())).toEqual({state:'remote_acknowledged',error:null,buffered:0});
 expect(await page.evaluate(async()=>(await client.get(client.id)).kind)).toBe('cleaned');
 await page.evaluate(()=>client.close());
});

test('authorized recovery without task policy exports data without resuming trials or secrets',async({page,context})=>{
 await setup(page);
 const id=await page.evaluate(async payload=>{client.record('exp.rt',payload,{id:'rt',version:'1'});await client.commit();return client.id},payload);
 await page.evaluate(()=>client.mutate(store=>{const r=store.get(client.id);r.onsuccess=()=>{const s=r.result;s.paused=true;s.attempts=32;s.retryAt=Number.MAX_SAFE_INTEGER;store.put(s)}}));
 await page.evaluate(()=>client.close());await page.reload();
 await page.evaluate(async config=>{const {GEC}=await import('/sdk.js');window.client=new GEC(config);await client.prepare();clearInterval(client.timer)},config);
 await expect(page.evaluate(()=>client.recovery_export())).rejects.toThrow('recovery_export_unavailable');
 const admin=await context.newPage(),c=JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'));
 await admin.goto('http://admin.localhost:8000/login');await admin.locator('[name=username]').fill(c.username);await admin.locator('[name=password]').fill(c.password);await admin.getByRole('button',{name:'登录',exact:true}).click();
 await admin.goto('http://admin.localhost:8000/studies/'+config.study_id);await admin.locator('[name=session_id]').fill(id);await admin.getByRole('button',{name:'签发一次性恢复许可'}).click();
 const permit=(await admin.locator('.notice').textContent()).split('许可：')[1].trim();
 await expect(page.evaluate(id=>client.recover(id,'invalid-synthetic-permit'),id)).rejects.toThrow();
 expect(await page.evaluate(async id=>(await client.get(id)).paused,id)).toBe(true);
 const result=await page.evaluate(async({id,permit})=>{const recovered=await client.recover(id,permit);return {recovered,data:await client.recovery_export()}},{id,permit});
 expect(result.recovered).toEqual({state:'data_only',checkpoint:null});
 await page.evaluate(()=>client.flush());
 expect(await page.evaluate(async id=>{const s=await client.get(id);return [s.paused,s.attempts,s.retryAt,s.pending.length]},id)).toEqual([false,0,0,0]);
 expect(result.data.records).toHaveLength(1);expect(result.data.records[0].payload).toEqual(payload);
 expect(Object.keys(result.data).sort()).toEqual(['binding','checkpoint','completion','format_version','pending','records','session_id']);
 await expect(page.evaluate(payload=>client.record('exp.rt',payload,{id:'rt',version:'1'}),payload)).rejects.toThrow('not_recording');
 await page.evaluate(()=>client.close());
});
