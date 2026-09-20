import {test,expect} from '@playwright/test';import fs from 'node:fs';
async function read(page){return page.evaluate(()=>new Promise(resolve=>{const r=indexedDB.open('gec-1');r.onsuccess=()=>{const db=r.result;const tx=db.transaction('sessions');const q=tx.objectStore('sessions').getAll();tx.oncomplete=()=>{resolve(q.result);db.close()}}}));}
// Isolated-instance target; all variables are required so this spec never
// silently connects to the local dev instance (GEP_DEV_INSTANCE=1 opts in).
function target(){
 if(process.env.GEP_WEB_E2E_URL&&process.env.GEP_WEB_E2E_STUDY_URL&&process.env.GEP_WEB_E2E_ADMIN_URL&&process.env.GEP_WEB_E2E_USERNAME&&process.env.GEP_WEB_E2E_PASSWORD)
  return {run:process.env.GEP_WEB_E2E_URL,study:process.env.GEP_WEB_E2E_STUDY_URL,admin:process.env.GEP_WEB_E2E_ADMIN_URL,credentials:{username:process.env.GEP_WEB_E2E_USERNAME,password:process.env.GEP_WEB_E2E_PASSWORD}};
 if(process.env.GEP_DEV_INSTANCE==='1')
  return {run:fs.readFileSync('build/web/run_url.txt','utf8'),study:fs.readFileSync('build/web/study_url.txt','utf8'),admin:'http://admin.localhost:8000',credentials:JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'))};
 throw new Error('set GEP_WEB_E2E_URL/STUDY_URL/ADMIN_URL/USERNAME/PASSWORD for an isolated instance, or GEP_DEV_INSTANCE=1 to use the local dev instance explicitly');
}
test('real Godot Web resumes original session at complete trial with new epoch',async({page,context})=>{
 const target_config=target();
 const url=target_config.run;await page.goto(url);await expect(page.locator('#status')).toBeHidden({timeout:30000});await page.locator('#gec-start').click();await expect(page.locator('#canvas')).toHaveAttribute('aria-label',/Trial [12]|第 [12] 次/);await page.keyboard.press('ArrowLeft');
 await expect.poll(async()=>{const s=(await read(page)).find(s=>s.kind==='session');return s?.checkpoint?.next_trial}).toBe(1);
 const saved=(await read(page)).find(s=>s.kind==='session');await page.reload();await expect(page.locator('#status')).toBeHidden({timeout:30000});
 const admin=await context.newPage();const c=target_config.credentials;await admin.goto(target_config.admin+'/login');await admin.locator('[name=username]').fill(c.username);await admin.locator('[name=password]').fill(c.password);await admin.getByRole('button',{name:'登录',exact:true}).click();
 // New GUI contract (P0307 module pages): sessions and the UUID permit form live
 // on the study's /sessions module, not on the study overview page.
 await admin.goto(target_config.study+'/sessions');
 const permit_form=admin.locator('form[data-recover-uuid-form="1"]');
 await permit_form.locator('[name=session_id]').fill(saved.id);await permit_form.getByRole('button',{name:'签发一次性恢复许可'}).click();const permit=(await admin.locator('.notice').textContent()).split('许可：')[1].trim();
 await page.bringToFront();
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 await page.locator('#gec-shell summary').click();
 for(const [selector,value] of [['#gec-input-recovery',saved.id],['#gec-input-permit',permit]]){
  await page.evaluate(value=>navigator.clipboard.writeText(value),value);
  await page.locator(selector).click();await page.keyboard.press('ControlOrMeta+V');
  await expect(page.locator(selector)).toHaveValue(value);
 }
 await page.locator('#gec-recover-permit').click();
 await expect(page.locator('#gec-input-permit')).toHaveValue('');await expect(page.locator('#canvas')).toHaveAttribute('aria-label',/Trial 2|第 2 次/).catch(async e=>{console.log('Begin recovery status',await page.evaluate(()=>GECBridge.status()));await page.screenshot({path:'build/recovery_failure.png'});throw e});
 await context.setOffline(true);await page.keyboard.press('ArrowRight');await page.waitForFunction(()=>GECBridge.status().state==='finished',null,{timeout:10000}).catch(async e=>{console.log('Recovery status',await page.evaluate(()=>GECBridge.status()));console.log('Checkpoint counts',(await read(page)).map(s=>({kind:s.kind,count:s.records?.length,next:s.checkpoint?.next_trial,segments:s.segments?.length})));await page.screenshot({path:'build/recovery_failure.png'});throw e});
 const resumed=(await read(page)).find(s=>s.id===saved.id);expect(resumed.records).toHaveLength(4);expect(resumed.segments).toHaveLength(2);expect(resumed.records.slice(0,2)).toEqual(saved.records);expect(resumed.records[2].observed_time.epoch).not.toBe(saved.records[0].observed_time.epoch);
 await context.setOffline(false);await expect.poll(async()=>{return (await read(page)).find(s=>s.id===saved.id)?.kind},{timeout:15000}).toBe('cleaned');
});
