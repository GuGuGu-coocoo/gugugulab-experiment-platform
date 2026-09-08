import {test,expect} from '@playwright/test';import fs from 'node:fs';
async function read(page){return page.evaluate(()=>new Promise(resolve=>{const r=indexedDB.open('gec-1');r.onsuccess=()=>{const db=r.result;const tx=db.transaction('sessions');const q=tx.objectStore('sessions').getAll();tx.oncomplete=()=>{resolve(q.result);db.close()}}}));}
test('real Godot Web resumes original session at complete trial with new epoch',async({page,context})=>{
 const url=fs.readFileSync('build/web/run_url.txt','utf8');await page.goto(url);await expect(page.locator('#status')).toBeHidden({timeout:30000});await page.mouse.click(490,278);await expect(page.locator('#canvas')).toHaveAttribute('aria-label',/Trial [12]:/);await page.keyboard.press('ArrowLeft');
 await expect.poll(async()=>{const s=(await read(page)).find(s=>s.kind==='session');return s?.checkpoint?.next_trial}).toBe(1);
 const saved=(await read(page)).find(s=>s.kind==='session');await page.reload();await expect(page.locator('#status')).toBeHidden({timeout:30000});
 const admin=await context.newPage();const c=JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'));await admin.goto('http://admin.localhost:8000/login');await admin.locator('[name=username]').fill(c.username);await admin.locator('[name=password]').fill(c.password);await admin.getByRole('button',{name:'登录',exact:true}).click();await admin.goto(fs.readFileSync('build/web/study_url.txt','utf8'));await admin.locator('[name=session_id]').fill(saved.id);await admin.getByRole('button',{name:'签发一次性恢复许可'}).click();const permit=(await admin.locator('.notice').textContent()).split('许可：')[1].trim();
 await page.bringToFront();
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 for(const [selector,value] of [['#gec-input-recovery',saved.id],['#gec-input-permit',permit]]){
  await page.evaluate(value=>navigator.clipboard.writeText(value),value);
  await page.locator(selector).click();await page.keyboard.press('ControlOrMeta+V');
  await expect(page.locator(selector)).toHaveValue(value);
 }
 await page.mouse.click(490,278);
 await expect(page.locator('#gec-input-permit')).toHaveValue('');await expect(page.locator('#canvas')).toHaveAttribute('aria-label',/Trial 2:/).catch(async e=>{console.log('Begin recovery status',await page.evaluate(()=>GECBridge.status()));await page.screenshot({path:'build/recovery_failure.png'});throw e});
 await context.setOffline(true);await page.keyboard.press('ArrowRight');await page.waitForFunction(()=>GECBridge.status().state==='finished',null,{timeout:10000}).catch(async e=>{console.log('Recovery status',await page.evaluate(()=>GECBridge.status()));console.log('Checkpoint counts',(await read(page)).map(s=>({kind:s.kind,count:s.records?.length,next:s.checkpoint?.next_trial,segments:s.segments?.length})));await page.screenshot({path:'build/recovery_failure.png'});throw e});
 const resumed=(await read(page)).find(s=>s.id===saved.id);expect(resumed.records).toHaveLength(4);expect(resumed.segments).toHaveLength(2);expect(resumed.records.slice(0,2)).toEqual(saved.records);expect(resumed.records[2].observed_time.epoch).not.toBe(saved.records[0].observed_time.epoch);
 await context.setOffline(false);await expect.poll(async()=>{return (await read(page)).find(s=>s.id===saved.id)?.kind},{timeout:15000}).toBe('cleaned');
});
