import {test,expect} from '@playwright/test';
import fs from 'node:fs';
async function rows(page){return page.evaluate(()=>new Promise((resolve,reject)=>{const r=indexedDB.open('gec-1',1);r.onsuccess=()=>{const db=r.result;const tx=db.transaction('sessions');const q=tx.objectStore('sessions').getAll();tx.oncomplete=()=>{resolve(q.result);db.close()};tx.onerror=()=>reject(tx.error)}}));}
test('real Godot Web input through IndexedDB API database and authorized export',async({page,context})=>{
 await page.goto(fs.readFileSync('build/web/run_url.txt','utf8'));await expect(page.locator('#status')).toBeHidden({timeout:30000});
 await page.mouse.click(490,278);await expect(page.locator('#canvas')).toHaveAttribute('aria-label',/Trial [12]:/);
 await page.keyboard.press('ArrowLeft');await expect.poll(async()=>{const s=(await rows(page)).find(x=>x.kind==='session');return s?.checkpoint?.next_trial}).toBe(1);
 await context.setOffline(true);await page.keyboard.press('ArrowRight');await page.waitForFunction(()=>GECBridge.status().state==='finished');
 const saved=(await rows(page)).find(x=>x.kind==='session');expect(saved.records).toHaveLength(4);expect(saved.completion.event_ids).toHaveLength(4);
 const expected=saved.records;
 await context.setOffline(false);await expect.poll(async()=>{const s=(await rows(page)).find(x=>x.id===saved.id);return s?.kind},{timeout:15000}).toBe('cleaned');
 const c=JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'));await page.goto('http://admin.localhost:8000/login');await page.locator('[name=username]').fill(c.username);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.goto(fs.readFileSync('build/web/study_url.txt','utf8'));await page.getByRole('button',{name:'创建 JSONL 固定快照'}).click();
 const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'下载 JSONL'}).click()]);const stream=await download.createReadStream();let raw='';for await(const chunk of stream)raw+=chunk;
 const actual=raw.trim().split('\n').map(JSON.parse).map(r=>r.record).filter(e=>e.session_id===saved.id);
 expect(actual.sort((a,b)=>a.sequence-b.sequence)).toEqual(expected.sort((a,b)=>a.sequence-b.sequence));
 expect(actual.filter(e=>e.event_type==='exp.rt').map(e=>e.payload.choice)).toEqual(['left','right']);
 expect(actual.filter(e=>e.event_type==='exp.interaction')[0].payload.nested).toEqual({changes:[{from:null,to:'001'}],confirmed:false});
});
