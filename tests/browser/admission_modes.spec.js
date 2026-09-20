import {test,expect} from '@playwright/test';import fs from 'node:fs';import crypto from 'node:crypto';
import {isolated} from './isolated_target.mjs';
// Isolated-instance target: never the local dev instance or the protected
// acceptance database (GEP_DEV_INSTANCE=1 is the explicit dev opt-in).
const target=isolated();
test('GUI roster policies and real API enforce all three admission modes',async({page,request})=>{
 const c=target.credentials;await page.goto(target.admin+'/login');await page.locator('[name=username]').fill(c.username);await page.locator('[name=password]').fill(c.password);await page.getByRole('button',{name:'登录',exact:true}).click();
 const identities=[];
 for(const mode of ['anonymous','id','password']){
  await page.goto(target.admin+'/');await page.locator('[name=title]').fill(mode+' synthetic '+Date.now());await page.getByRole('button',{name:'创建',exact:true}).click();
  const studyUrl=page.url();
  await page.goto(studyUrl+'/participation');await page.locator('[name=mode]').selectOption(mode);await page.locator('[name=max_sessions]').fill('2');await page.getByRole('button',{name:'保存政策'}).click();
  // New GUI contract (P0307 CSV roster): the participation form imports comma CSV
  // (quoting supported); the old tab row is only accepted through the explicit
  // legacy_tab format and would be rejected as roster_columns here.
  if(mode!=='anonymous'){await page.locator('[name=roster]').fill(mode==='id'?'001':'001,synthetic-password');await page.getByRole('button',{name:'导入名单'}).click();}
  await page.goto(studyUrl+'/builds');await page.locator('[name=descriptor]').fill(fs.readFileSync(target.descriptor,'utf8'));await page.getByRole('button',{name:'登记不可变构建'}).click();await page.getByRole('button',{name:'批准合成发行'}).click();
  const configURL=await page.getByRole('link',{name:'导出连接配置'}).getAttribute('href');const config=await (await page.request.get(target.admin+configURL)).json();
  await page.goto(studyUrl+'/recruitment');
  await Promise.all([page.waitForResponse(r=>r.url().includes('/studies/')&&r.request().method()==='POST'),page.locator('[name=state]').selectOption('open')]);await page.waitForLoadState('load');
  const data={instance_id:config.instance_id,study_id:config.study_id,build_id:config.build_id,release_id:config.release_id,operation_id:crypto.randomUUID(),proof:crypto.randomBytes(32).toString('hex')};
  const endpoint=target.experiment+'/v1/participant/sessions';
  if(mode!=='anonymous'){expect((await request.post(endpoint,{data:{...data,participant_code:'unknown',password:'wrong'}})).status()).toBe(403);data.participant_code='001';}
  if(mode==='password'){expect((await request.post(endpoint,{data:{...data,password:'wrong'}})).status()).toBe(403);data.password='synthetic-password';}
  const result=await request.post(endpoint,{data});expect(result.status()).toBe(200);const first=await result.json();expect(first.participant_uuid).toMatch(/^[a-f0-9-]{36}$/);expect(first.participant_code).toBe(mode==='anonymous'?null:'001');identities.push(first.participant_uuid);
  const repeated=await (await request.post(endpoint,{data})).json();expect(repeated.session_id).toBe(first.session_id);
  expect((await request.post(endpoint,{data:{...data,proof:'q'.repeat(64)}})).status()).toBe(409);
  const second=await (await request.post(endpoint,{data:{...data,operation_id:crypto.randomUUID()}})).json();expect(second.session_id).not.toBe(first.session_id);if(mode!=='anonymous')expect(second.participant_uuid).toBe(first.participant_uuid);
 }
 expect(new Set(identities).size).toBe(3);
});
