import {test,expect} from '@playwright/test';
import fs from 'node:fs';
async function rows(page){return page.evaluate(()=>new Promise((resolve,reject)=>{const r=indexedDB.open('gec-1',1);r.onsuccess=()=>{const db=r.result;const tx=db.transaction('sessions');const q=tx.objectStore('sessions').getAll();tx.oncomplete=()=>{resolve(q.result);db.close()};tx.onerror=()=>reject(tx.error)}}));}
// Isolated-instance target: all five variables are required so this spec can never
// silently connect to the local dev instance. GEP_DEV_INSTANCE=1 is an explicit
// opt-in for the maintainer's own dev instance only.
function target(){
 if(process.env.GEP_WEB_E2E_URL&&process.env.GEP_WEB_E2E_STUDY_URL&&process.env.GEP_WEB_E2E_ADMIN_URL&&process.env.GEP_WEB_E2E_USERNAME&&process.env.GEP_WEB_E2E_PASSWORD)
  return {run:process.env.GEP_WEB_E2E_URL,study:process.env.GEP_WEB_E2E_STUDY_URL,admin:process.env.GEP_WEB_E2E_ADMIN_URL,credentials:{username:process.env.GEP_WEB_E2E_USERNAME,password:process.env.GEP_WEB_E2E_PASSWORD}};
 if(process.env.GEP_DEV_INSTANCE==='1')
  return {run:fs.readFileSync('build/web/run_url.txt','utf8'),study:fs.readFileSync('build/web/study_url.txt','utf8'),admin:'http://admin.localhost:8000',credentials:JSON.parse(fs.readFileSync('local_data/dev_credentials.json','utf8'))};
 throw new Error('set GEP_WEB_E2E_URL/STUDY_URL/ADMIN_URL/USERNAME/PASSWORD for an isolated instance, or GEP_DEV_INSTANCE=1 to use the local dev instance explicitly');
}
test('real Godot Web input through IndexedDB API database and authorized export',async({page,context})=>{
 const target_config=target();
 await page.goto(target_config.run);await expect(page.locator('#status')).toBeHidden({timeout:30000});
 await page.locator('#gec-start').click();await expect(page.locator('#canvas')).toHaveAttribute('aria-label',/Trial [12]|第 [12] 次/);
 await page.keyboard.press('ArrowLeft');await expect.poll(async()=>{const s=(await rows(page)).find(x=>x.kind==='session');return s?.checkpoint?.next_trial}).toBe(1);
 await context.setOffline(true);await page.keyboard.press('ArrowRight');await page.waitForFunction(()=>GECBridge.status().state==='finished');
 const saved=(await rows(page)).find(x=>x.kind==='session');expect(saved.records).toHaveLength(4);expect(saved.completion.event_ids).toHaveLength(4);
 const expected=saved.records;
 await context.setOffline(false);await expect.poll(async()=>{const s=(await rows(page)).find(x=>x.id===saved.id);return s?.kind},{timeout:15000}).toBe('cleaned');
 const c=target_config.credentials;await page.goto(target_config.admin+'/login');await page.locator('[name=username]').fill(c.username);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 // New GUI contract (P0307 module pages): exports live on the study's /exports
 // module and the snapshot form creates the JSONL download link in-place.
 await page.goto(target_config.study+'/exports');await page.locator('#export-form').getByRole('button',{name:'创建 JSONL 固定快照'}).click();
 const download_link=page.locator('#export-result').getByRole('link',{name:'下载 JSONL'});await download_link.waitFor({timeout:30000});
 const [download]=await Promise.all([page.waitForEvent('download'),download_link.click()]);const stream=await download.createReadStream();let raw='';for await(const chunk of stream)raw+=chunk;
 const actual=raw.trim().split('\n').map(JSON.parse).map(r=>r.record).filter(e=>e.session_id===saved.id);
 expect(actual.sort((a,b)=>a.sequence-b.sequence)).toEqual(expected.sort((a,b)=>a.sequence-b.sequence));
 expect(actual.filter(e=>e.event_type==='exp.rt').map(e=>e.payload.choice)).toEqual(['left','right']);
 expect(actual.filter(e=>e.event_type==='exp.interaction')[0].payload.nested).toEqual({changes:[{from:null,to:'001'}],confirmed:false});
});
